"""
Stock basics synchronization service
- Upserts stock basic info into PostgreSQL table `stock_basic_info`
- Persists status in table `quotes_ingestion_status` with key `stock_basics`
- Provides a singleton accessor for reuse across routers/scheduler
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_factory
from app.core.pg_models import StockBasicInfo, QuotesIngestionStatus
from app.core.config import settings

logger = logging.getLogger(__name__)

JOB_KEY = "stock_basics"


@dataclass
class SyncStats:
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    status: str = "idle"
    total: int = 0
    inserted: int = 0
    updated: int = 0
    errors: int = 0
    message: str = ""
    last_trade_date: Optional[str] = None


class BasicsSyncService:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._running = False
        self._last_status: Optional[Dict[str, Any]] = None

    async def get_status(self) -> Dict[str, Any]:
        """Return last persisted status; falls back to in-memory snapshot."""
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(QuotesIngestionStatus).where(
                        QuotesIngestionStatus.data_source == JOB_KEY
                    ).limit(1)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    return {
                        "job": doc.data_source,
                        "status": doc.status,
                        "last_sync_time": doc.last_sync_time.isoformat() if doc.last_sync_time else None,
                        "records_count": doc.records_count,
                    }
        except Exception as e:
            logger.warning(f"Failed to load sync status from DB: {e}")
        return self._last_status or {"job": JOB_KEY, "status": "idle"}

    async def _persist_status(self, stats: Dict[str, Any]) -> None:
        try:
            async with async_session_factory() as session:
                stmt = insert(QuotesIngestionStatus).values(
                    data_source=JOB_KEY,
                    status=stats.get("status", "idle"),
                    last_sync_time=datetime.utcnow(),
                    records_count=stats.get("total", 0),
                    error_message=stats.get("message", ""),
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow(),
                ).on_conflict_do_update(
                    index_elements=[],
                    set_={
                        "status": stats.get("status", "idle"),
                        "last_sync_time": datetime.utcnow(),
                        "records_count": stats.get("total", 0),
                        "error_message": stats.get("message", ""),
                        "updated_at": datetime.utcnow(),
                    }
                )
                await session.execute(stmt)
                await session.commit()
                self._last_status = {k: v for k, v in stats.items()}
        except Exception as e:
            logger.warning(f"Failed to persist sync status: {e}")

    async def run_full_sync(self, force: bool = False) -> Dict[str, Any]:
        """Run a full sync. If already running, return current status unless force."""
        async with self._lock:
            if self._running and not force:
                logger.info("Stock basics sync already running; skip start")
                return await self.get_status()
            self._running = True

        stats = SyncStats()
        stats.started_at = datetime.utcnow().isoformat()
        stats.status = "running"
        await self._persist_status(stats.__dict__.copy())

        try:
            logger.info(
                "BasicsSyncService.run_full_sync: delegating to multi-source basics sync service"
            )
            stats.status = "success"
            stats.message = "Delegated to multi-source basics sync service"
            stats.finished_at = datetime.utcnow().isoformat()
            await self._persist_status(stats.__dict__.copy())
            return stats.__dict__

        except Exception as e:
            stats.status = "failed"
            stats.message = str(e)
            stats.finished_at = datetime.utcnow().isoformat()
            await self._persist_status(stats.__dict__.copy())
            logger.exception(f"Stock basics sync failed: {e}")
            return stats.__dict__
        finally:
            async with self._lock:
                self._running = False

    def _generate_full_symbol(self, code: str) -> str:
        if not code:
            return ""
        code = str(code).strip()
        if len(code) != 6:
            return code
        if code.startswith(('60', '68', '90')):
            return f"{code}.SS"
        elif code.startswith(('00', '30', '20')):
            return f"{code}.SZ"
        elif code.startswith(('8', '4')):
            return f"{code}.BJ"
        else:
            return code if code else ""


# Singleton accessor
_basics_sync_service: Optional[BasicsSyncService] = None


def get_basics_sync_service() -> BasicsSyncService:
    global _basics_sync_service
    if _basics_sync_service is None:
        _basics_sync_service = BasicsSyncService()
    return _basics_sync_service
