"""
行情入库服务 - PostgreSQL 版本
从 AKShare 获取实时行情，写入 PostgreSQL market_quotes 表
"""

import logging
from datetime import datetime, time as dtime, timedelta
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.pg_models import MarketQuotes, QuotesIngestionStatus
from app.services.data_sources.manager import DataSourceManager

logger = logging.getLogger(__name__)


class QuotesIngestionService:
    """定时从数据源获取全市场近实时行情，入库到 PostgreSQL market_quotes 表"""

    def __init__(self, collection_name: str = "market_quotes") -> None:
        self.table_name = "market_quotes"
        self.tz = ZoneInfo(settings.TIMEZONE)
        self._rotation_sources = ["akshare_eastmoney", "akshare_sina"]
        self._rotation_index = 0

    @staticmethod
    def _normalize_stock_code(code: str) -> str:
        if not code:
            return ""
        code_str = str(code).strip()
        if len(code_str) > 6:
            code_str = ''.join(filter(str.isdigit, code_str))
        if code_str.isdigit():
            code_clean = code_str.lstrip('0') or '0'
            return code_clean.zfill(6)
        code_digits = ''.join(filter(str.isdigit, code_str))
        if code_digits:
            return code_digits.zfill(6)
        return ""

    async def _record_sync_status(
        self,
        success: bool,
        source: Optional[str] = None,
        records_count: int = 0,
        error_msg: Optional[str] = None
    ) -> None:
        try:
            async with async_session_factory() as session:
                now = datetime.now(self.tz)
                stmt = insert(QuotesIngestionStatus).values(
                    last_sync_time=now,
                    last_sync_time_iso=now.isoformat(),
                    interval_seconds=settings.QUOTES_INGEST_INTERVAL_SECONDS,
                    status="success" if success else "failed",
                    data_source=source,
                    records_count=records_count,
                    error_message=error_msg,
                    updated_at=now,
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.warning(f"记录同步状态失败（忽略）: {e}")

    async def get_sync_status(self) -> Dict[str, any]:
        try:
            from sqlalchemy import select, desc
            async with async_session_factory() as session:
                result = await session.execute(
                    select(QuotesIngestionStatus)
                    .order_by(desc(QuotesIngestionStatus.updated_at))
                    .limit(1)
                )
                doc = result.scalar_one_or_none()

                if not doc:
                    return {
                        "last_sync_time": None,
                        "last_sync_time_iso": None,
                        "interval_seconds": settings.QUOTES_INGEST_INTERVAL_SECONDS,
                        "interval_minutes": settings.QUOTES_INGEST_INTERVAL_SECONDS / 60,
                        "data_source": None,
                        "success": None,
                        "records_count": 0,
                        "error_message": "尚未执行过同步",
                    }

                dt_local = doc.last_sync_time
                if dt_local and dt_local.tzinfo is None:
                    dt_local = dt_local.replace(tzinfo=ZoneInfo("UTC"))
                if dt_local:
                    dt_local = dt_local.astimezone(self.tz)

                return {
                    "last_sync_time": dt_local.strftime("%Y-%m-%d %H:%M:%S") if dt_local else None,
                    "last_sync_time_iso": doc.last_sync_time_iso,
                    "interval_seconds": doc.interval_seconds,
                    "interval_minutes": (doc.interval_seconds or 0) / 60,
                    "data_source": doc.data_source,
                    "success": doc.status == "success" if doc.status else None,
                    "records_count": doc.records_count,
                    "error_message": doc.error_message,
                }

        except Exception as e:
            logger.error(f"获取同步状态失败: {e}")
            return {
                "last_sync_time": None, "last_sync_time_iso": None,
                "interval_seconds": settings.QUOTES_INGEST_INTERVAL_SECONDS,
                "interval_minutes": settings.QUOTES_INGEST_INTERVAL_SECONDS / 60,
                "data_source": None, "success": None, "records_count": 0,
                "error_message": f"获取状态失败: {str(e)}",
            }

    def _get_next_source(self) -> Tuple[str, Optional[str]]:
        if not settings.QUOTES_ROTATION_ENABLED:
            return "akshare", "eastmoney"
        current_source = self._rotation_sources[self._rotation_index]
        self._rotation_index = (self._rotation_index + 1) % len(self._rotation_sources)
        if current_source == "akshare_eastmoney":
            return "akshare", "eastmoney"
        else:
            return "akshare", "sina"

    def _is_trading_time(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(self.tz)
        if now.weekday() > 4:
            return False
        t = now.time()
        morning = dtime(9, 30)
        noon = dtime(11, 30)
        afternoon_start = dtime(13, 0)
        buffer_end = dtime(15, 30)
        return (morning <= t <= noon) or (afternoon_start <= t <= buffer_end)

    async def _bulk_upsert(self, quotes_map: Dict[str, Dict], trade_date: str, source: Optional[str] = None) -> None:
        async with async_session_factory() as session:
            updated_at = datetime.now(self.tz)
            count = 0
            for code, q in quotes_map.items():
                if not code:
                    continue
                code6 = self._normalize_stock_code(code)
                if not code6:
                    continue

                stmt = insert(MarketQuotes).values(
                    code=code6,
                    symbol=code6,
                    close=q.get("close"),
                    pct_chg=q.get("pct_chg"),
                    amount=q.get("amount"),
                    volume=q.get("volume"),
                    open=q.get("open"),
                    high=q.get("high"),
                    low=q.get("low"),
                    pre_close=q.get("pre_close"),
                    trade_date=trade_date,
                    data_source=source,
                    updated_at=updated_at,
                ).on_conflict_do_update(
                    index_elements=["code"],
                    set_={
                        "symbol": code6,
                        "close": q.get("close"),
                        "pct_chg": q.get("pct_chg"),
                        "amount": q.get("amount"),
                        "volume": q.get("volume"),
                        "open": q.get("open"),
                        "high": q.get("high"),
                        "low": q.get("low"),
                        "pre_close": q.get("pre_close"),
                        "trade_date": trade_date,
                        "data_source": source,
                        "updated_at": updated_at,
                    },
                )
                await session.execute(stmt)
                count += 1

            await session.commit()
            logger.info(f"✅ 行情入库完成 source={source}, records={count}")

    def _fetch_quotes_from_source(self, source_type: str, akshare_api: Optional[str] = None) -> Tuple[Optional[Dict], Optional[str]]:
        try:
            if source_type == "akshare":
                from app.services.data_sources.akshare_adapter import AKShareAdapter
                adapter = AKShareAdapter()
                if not adapter.is_available():
                    logger.warning("AKShare 不可用")
                    return None, None
                api_name = akshare_api or "eastmoney"
                logger.info(f"📊 使用 AKShare {api_name} 接口获取实时行情")
                quotes_map = adapter.get_realtime_quotes(source=api_name)
                if quotes_map:
                    return quotes_map, f"akshare_{api_name}"
                else:
                    logger.warning(f"AKShare {api_name} 返回空数据")
                    return None, None
            else:
                logger.error(f"未知数据源类型: {source_type}")
                return None, None
        except Exception as e:
            logger.error(f"从 {source_type} 获取行情失败: {e}")
            return None, None

    async def run_once(self) -> None:
        if not self._is_trading_time():
            logger.info("⏭️ 非交易时段，跳过行情采集")
            return

        try:
            source_type, akshare_api = self._get_next_source()
            quotes_map, source_name = self._fetch_quotes_from_source(source_type, akshare_api)

            if not quotes_map:
                logger.warning(f"⚠️ {source_name or source_type} 未获取到行情数据")
                await self._record_sync_status(success=False, source=source_name or source_type, records_count=0, error_msg="未获取到行情数据")
                return

            try:
                manager = DataSourceManager()
                trade_date = manager.find_latest_trade_date_with_fallback() or datetime.now(self.tz).strftime("%Y%m%d")
            except Exception:
                trade_date = datetime.now(self.tz).strftime("%Y%m%d")

            await self._bulk_upsert(quotes_map, trade_date, source_name)
            await self._record_sync_status(success=True, source=source_name, records_count=len(quotes_map))

        except Exception as e:
            logger.error(f"❌ 行情入库失败: {e}")
            await self._record_sync_status(success=False, source=None, records_count=0, error_msg=str(e))
