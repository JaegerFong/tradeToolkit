"""
统一数据同步编排器
按数据类型×数据源编排同步流程，支持进度追踪和取消
"""
import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from sqlalchemy import select

from app.core.database import async_session_factory
from app.core.pg_models import UserFavorite

logger = logging.getLogger(__name__)

# 支持的同步项
SYNC_ITEMS = {
    "basic_info": "基础信息",
    "quotes": "实时行情",
    "historical": "历史日线",
    "weekly": "历史周线",
    "monthly": "历史月线",
    "financial": "财务数据",
    "news": "新闻数据",
}

# 将 sync_items 映射到具体的执行步骤
ITEM_TO_PERIOD = {
    "historical": "daily",
    "weekly": "weekly",
    "monthly": "monthly",
}

CN_TZ = timezone(timedelta(hours=8))


class UnifiedSyncConfig:
    """统一同步配置"""

    def __init__(
        self,
        sync_items: List[str],
        data_sources: List[str],
        mode: str = "incremental",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        symbol_scope: str = "all",
        symbols: Optional[List[str]] = None,
        batch_size: int = 100,
        rate_limit_delay: float = 0.5,
        max_retries: int = 3,
    ):
        self.sync_items = sync_items
        self.data_sources = data_sources
        self.mode = mode
        self.start_date = start_date
        self.end_date = end_date or datetime.now(CN_TZ).strftime("%Y-%m-%d")
        self.symbol_scope = symbol_scope
        self.symbols = symbols
        self.batch_size = batch_size
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries

    @property
    def is_incremental(self) -> bool:
        return self.mode == "incremental"

    @property
    def is_full(self) -> bool:
        return self.mode == "full"

    @property
    def is_date_range(self) -> bool:
        return self.mode == "date_range"


class UnifiedSyncService:
    """统一数据同步编排器"""

    def __init__(self):
        self._running_jobs: Dict[str, Dict[str, Any]] = {}

    def _build_steps(self, config: UnifiedSyncConfig) -> List[Dict[str, str]]:
        """根据配置展开执行步骤：每个 (数据类型, 数据源) 为一个步骤"""
        steps = []
        for item in config.sync_items:
            for source in config.data_sources:
                steps.append({"item": item, "source": source})
        return steps

    async def run_sync(self, job_id: str, config: UnifiedSyncConfig) -> Dict[str, Any]:
        """执行统一同步"""
        steps = self._build_steps(config)
        total_steps = len(steps)
        results: List[Dict[str, Any]] = []

        self._running_jobs[job_id] = {
            "status": "running",
            "total_steps": total_steps,
            "completed_steps": 0,
            "current_step": "准备中...",
            "started_at": datetime.now(CN_TZ).isoformat(),
            "results": [],
        }

        try:
            for idx, step in enumerate(steps):
                if self._is_cancelled(job_id):
                    logger.info(f"同步任务 {job_id} 已被取消")
                    break

                item = step["item"]
                source = step["source"]
                step_name = f"{SYNC_ITEMS.get(item, item)} ({source.upper()})"

                # 标记步骤开始执行
                self._running_jobs[job_id]["current_step"] = step_name
                self._running_jobs[job_id]["completed_steps"] = idx
                self._update_progress(job_id, idx, total_steps, step_name)

                try:
                    result = await self._run_single_step(job_id, step, config)
                    results.append({"step": step_name, "success": True, "result": result})
                except Exception as e:
                    logger.error(f"步骤 {step_name} 失败: {e}")
                    results.append({"step": step_name, "success": False, "error": str(e)})

                self._running_jobs[job_id]["completed_steps"] = idx + 1
                self._running_jobs[job_id]["results"] = results

            self._running_jobs[job_id]["status"] = "completed"
            self._running_jobs[job_id]["finished_at"] = datetime.now(CN_TZ).isoformat()

        except Exception as e:
            logger.error(f"同步任务 {job_id} 异常: {e}")
            self._running_jobs[job_id]["status"] = "failed"
            self._running_jobs[job_id]["error"] = str(e)

        return {
            "job_id": job_id,
            "total_steps": total_steps,
            "completed_steps": self._running_jobs[job_id]["completed_steps"],
            "status": self._running_jobs[job_id]["status"],
            "results": results,
        }

    async def _run_single_step(
        self, job_id: str, step: Dict[str, str], config: UnifiedSyncConfig
    ) -> Dict[str, Any]:
        """执行单个同步步骤"""
        item = step["item"]
        source = step["source"]

        # 基础信息
        if item == "basic_info":
            if source == "akshare":
                from app.worker.akshare_sync_service import get_akshare_sync_service
                svc = await get_akshare_sync_service()
                return await svc.sync_stock_basic_info(force_update=not config.is_incremental)

        # 实时行情
        elif item == "quotes":
            symbols = await self._resolve_symbols(config)
            if source == "akshare":
                from app.worker.akshare_sync_service import get_akshare_sync_service
                svc = await get_akshare_sync_service()
                return await svc.sync_realtime_quotes(symbols=symbols, force=config.is_full)

        # 历史数据（日/周/月）
        elif item in ITEM_TO_PERIOD:
            period = ITEM_TO_PERIOD[item]
            symbols = await self._resolve_symbols(config)

            start_date = config.start_date
            end_date = config.end_date
            incremental = config.is_incremental

            if source == "akshare":
                from app.worker.akshare_sync_service import get_akshare_sync_service
                svc = await get_akshare_sync_service()
                return await svc.sync_historical_data(
                    start_date=start_date if config.is_date_range else None,
                    end_date=end_date,
                    symbols=symbols,
                    incremental=incremental,
                    period=period,
                )

        # 财务数据
        elif item == "financial":
            symbols = await self._resolve_symbols(config)
            if source == "akshare":
                from app.worker.akshare_sync_service import get_akshare_sync_service
                svc = await get_akshare_sync_service()
                return await svc.sync_financial_data(symbols=symbols)

        # 新闻数据
        elif item == "news":
            if source == "akshare":
                favorites_only = config.symbol_scope == "favorites"
                symbols = config.symbols if config.symbol_scope == "custom" else None
                from app.worker.akshare_sync_service import get_akshare_sync_service
                svc = await get_akshare_sync_service()
                return await svc.sync_news_data(
                    symbols=symbols,
                    favorites_only=favorites_only,
                    max_news_per_stock=20,
                )

        return {"message": f"未知步骤: {item}/{source}"}

    async def _resolve_symbols(self, config: UnifiedSyncConfig) -> Optional[List[str]]:
        """根据配置解析股票列表"""
        if config.symbol_scope == "custom" and config.symbols:
            return config.symbols
        if config.symbol_scope == "favorites":
            return await self._get_favorite_symbols()
        return None  # all

    async def _get_favorite_symbols(self) -> List[str]:
        """获取自选股列表"""
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(UserFavorite.stock_code).distinct()
                )
                return [row[0] for row in result.all() if row[0]]
        except Exception as e:
            logger.warning(f"获取自选股失败: {e}")
            return []

    def _update_progress(self, job_id: str, step_idx: int, total: int, step_name: str):
        """更新进度"""
        if job_id in self._running_jobs:
            self._running_jobs[job_id]["current_step"] = f"执行中: {step_name}"
        try:
            from app.services.scheduler_service import update_job_progress
            pct = int((step_idx / total) * 100) if total > 0 else 0
            update_job_progress(
                job_id=job_id,
                progress=pct,
                progress_message=f"步骤 {step_idx + 1}/{total}: {step_name}",
                current_item=step_name,
                total_items=total,
                processed_items=step_idx,
            )
        except Exception:
            pass

    def _is_cancelled(self, job_id: str) -> bool:
        """检查任务是否被取消"""
        if job_id not in self._running_jobs:
            return False
        return False

    def get_job_status(self, job_id: str) -> Optional[Dict[str, Any]]:
        """查询任务状态"""
        return self._running_jobs.get(job_id)

    def cancel_job(self, job_id: str) -> bool:
        """取消任务"""
        if job_id in self._running_jobs:
            self._running_jobs[job_id]["status"] = "cancelled"
            return True
        return False


# 全局单例
_unified_sync_service: Optional[UnifiedSyncService] = None


def get_unified_sync_service() -> UnifiedSyncService:
    global _unified_sync_service
    if _unified_sync_service is None:
        _unified_sync_service = UnifiedSyncService()
    return _unified_sync_service
