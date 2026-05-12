from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from app.core.database import get_mongo_db
from app.models.strategy import (
    StrategyBacktest,
    StrategyBacktestCreateRequest,
    StrategyBacktestMetrics,
    StrategyBacktestResponse,
    StrategyCreateRequest,
    StrategyDefinition,
    StrategyDetail,
    StrategyParseResult,
    StrategyPoolItem,
    StrategyPoolStatus,
    StrategyRun,
    StrategyRunEvent,
    StrategyRunResponse,
    StrategyRunResult,
    StrategyRunStats,
    StrategyRunStatus,
    StrategyStatus,
    StrategySummary,
    StrategyUpdateRequest,
)
from app.services.strategy_markdown_parser import get_strategy_markdown_parser
from app.services.strategy_narrative_service import get_strategy_narrative_service
from app.services.strong_trend_rule_engine import get_strong_trend_rule_engine
from app.utils.timezone import now_tz

logger = logging.getLogger("webapi")


class StrategyTaskService:
    STRATEGIES = "strategy_definitions"
    RUNS = "strategy_runs"
    EVENTS = "strategy_run_events"
    RESULTS = "strategy_run_results"
    POOL = "strategy_stock_pool"
    BACKTESTS = "strategy_backtests"
    BACKTEST_RESULTS = "strategy_backtest_results"

    def __init__(self) -> None:
        self.parser = get_strategy_markdown_parser()
        self.engine = get_strong_trend_rule_engine()
        self.narrative = get_strategy_narrative_service()
        self._indexes_ready = False

    async def ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        db = get_mongo_db()
        await db[self.STRATEGIES].create_index([("user_id", 1), ("strategy_id", 1)], unique=True)
        await db[self.RUNS].create_index([("user_id", 1), ("strategy_id", 1), ("run_id", 1)], unique=True)
        await db[self.EVENTS].create_index([("strategy_id", 1), ("run_id", 1), ("timestamp", 1)])
        await db[self.RESULTS].create_index([("strategy_id", 1), ("run_id", 1), ("total_score", -1)])
        await db[self.POOL].create_index([("user_id", 1), ("strategy_id", 1), ("code", 1)], unique=True)
        await db[self.BACKTESTS].create_index([("user_id", 1), ("strategy_id", 1), ("backtest_id", 1)], unique=True)
        await db[self.BACKTEST_RESULTS].create_index([("strategy_id", 1), ("backtest_id", 1), ("date", 1)])
        self._indexes_ready = True

    async def create_strategy(self, user_id: str, req: StrategyCreateRequest) -> StrategyDetail:
        await self.ensure_indexes()
        parse = self.parser.parse(req.markdown)
        strategy_id = f"st_{now_tz().strftime('%Y%m%d')}_{uuid.uuid4().hex[:10]}"
        schedule = req.schedule.model_copy()
        if req.enabled:
            schedule.enabled = True
        doc = StrategyDefinition(
            strategy_id=strategy_id,
            user_id=str(user_id),
            name=req.name,
            markdown=req.markdown,
            status=StrategyStatus.ENABLED if req.enabled else StrategyStatus.DRAFT,
            schedule=schedule,
            parse_result=parse,
        )
        db = get_mongo_db()
        await db[self.STRATEGIES].insert_one(doc.model_dump(by_alias=True))
        return self._to_detail(doc)

    async def list_strategies(self, user_id: str) -> List[StrategySummary]:
        await self.ensure_indexes()
        db = get_mongo_db()
        cursor = db[self.STRATEGIES].find({"user_id": str(user_id)}).sort("updated_at", -1)
        items: List[StrategySummary] = []
        async for doc in cursor:
            items.append(self._to_summary(StrategyDefinition.model_validate(doc)))
        return items

    async def get_strategy(self, strategy_id: str, user_id: str) -> Optional[StrategyDetail]:
        doc = await get_mongo_db()[self.STRATEGIES].find_one({"strategy_id": strategy_id, "user_id": str(user_id)})
        return self._to_detail(StrategyDefinition.model_validate(doc)) if doc else None

    async def update_strategy(self, strategy_id: str, user_id: str, req: StrategyUpdateRequest) -> Optional[StrategyDetail]:
        db = get_mongo_db()
        doc = await db[self.STRATEGIES].find_one({"strategy_id": strategy_id, "user_id": str(user_id)})
        if not doc:
            return None
        current = StrategyDefinition.model_validate(doc)
        markdown = req.markdown if req.markdown is not None else current.markdown
        parse = self.parser.parse(markdown)
        update: Dict[str, Any] = {
            "updated_at": now_tz(),
            "parse_result": parse.model_dump(),
        }
        if req.name is not None:
            update["name"] = req.name
        if req.markdown is not None:
            update["markdown"] = req.markdown
            update["version"] = current.version + 1
        if req.enabled is not None:
            update["status"] = StrategyStatus.ENABLED.value if req.enabled else StrategyStatus.DISABLED.value
            if req.schedule is None:
                schedule = current.schedule.model_copy()
                schedule.enabled = bool(req.enabled)
                update["schedule"] = schedule.model_dump()
        if req.schedule is not None:
            update["schedule"] = req.schedule.model_dump()
        await db[self.STRATEGIES].update_one({"strategy_id": strategy_id, "user_id": str(user_id)}, {"$set": update})
        return await self.get_strategy(strategy_id, user_id)

    async def validate_strategy(self, strategy_id: str, user_id: str) -> Optional[StrategyParseResult]:
        doc = await get_mongo_db()[self.STRATEGIES].find_one({"strategy_id": strategy_id, "user_id": str(user_id)})
        if not doc:
            return None
        return self.parser.parse(str(doc.get("markdown") or ""))

    async def create_run(self, strategy_id: str, user_id: str, run_type: str = "manual", as_of_date: Optional[str] = None) -> Optional[StrategyRunResponse]:
        await self.ensure_indexes()
        db = get_mongo_db()
        strategy = await self._load_strategy(strategy_id, user_id)
        if not strategy:
            return None
        running = await db[self.RUNS].find_one({"strategy_id": strategy_id, "status": {"$in": ["queued", "running"]}})
        if running:
            return self._to_run_response(StrategyRun.model_validate(running))
        run_id = f"sr_{now_tz().strftime('%Y%m%d')}_{uuid.uuid4().hex[:10]}"
        run = StrategyRun(
            run_id=run_id,
            strategy_id=strategy_id,
            user_id=str(user_id),
            strategy_version=strategy.version,
            run_type="scheduled" if run_type == "scheduled" else "manual",
            as_of_date=as_of_date,
        )
        await db[self.RUNS].insert_one(run.model_dump(by_alias=True))
        return self._to_run_response(run)

    async def get_run(self, strategy_id: str, run_id: str, user_id: str) -> Optional[StrategyRunResponse]:
        doc = await get_mongo_db()[self.RUNS].find_one({"strategy_id": strategy_id, "run_id": run_id, "user_id": str(user_id)})
        return self._to_run_response(StrategyRun.model_validate(doc)) if doc else None

    async def list_run_events(self, strategy_id: str, run_id: str, user_id: str, limit: int = 200) -> List[StrategyRunEvent]:
        db = get_mongo_db()
        run = await db[self.RUNS].find_one({"strategy_id": strategy_id, "run_id": run_id, "user_id": str(user_id)}, {"_id": 1})
        if not run:
            return []
        cursor = db[self.EVENTS].find({"strategy_id": strategy_id, "run_id": run_id}).sort("timestamp", 1).limit(limit)
        return [StrategyRunEvent.model_validate(doc) async for doc in cursor]

    async def list_run_results(self, strategy_id: str, run_id: str, user_id: str, limit: int = 50, offset: int = 0) -> Tuple[int, List[StrategyRunResult]]:
        db = get_mongo_db()
        run = await db[self.RUNS].find_one({"strategy_id": strategy_id, "run_id": run_id, "user_id": str(user_id)}, {"_id": 1})
        if not run:
            return 0, []
        total = await db[self.RESULTS].count_documents({"strategy_id": strategy_id, "run_id": run_id})
        cursor = db[self.RESULTS].find({"strategy_id": strategy_id, "run_id": run_id}, {"_id": 0, "strategy_id": 0, "run_id": 0}).sort("total_score", -1).skip(offset).limit(limit)
        return total, [StrategyRunResult.model_validate(doc) async for doc in cursor]

    async def list_pool(self, strategy_id: str, user_id: str, status: Optional[str] = None, limit: int = 200, offset: int = 0) -> List[StrategyPoolItem]:
        query: Dict[str, Any] = {"strategy_id": strategy_id, "user_id": str(user_id)}
        if status:
            query["status"] = status
        cursor = get_mongo_db()[self.POOL].find(query, {"_id": 0, "user_id": 0}).sort("last_score", -1).skip(offset).limit(limit)
        return [StrategyPoolItem.model_validate(doc) async for doc in cursor]

    async def run_task_background(self, strategy_id: str, run_id: str, user_id: str) -> None:
        db = get_mongo_db()
        strategy = await self._load_strategy(strategy_id, user_id)
        run_doc = await db[self.RUNS].find_one({"strategy_id": strategy_id, "run_id": run_id, "user_id": str(user_id)})
        if not strategy or not run_doc:
            return
        run = StrategyRun.model_validate(run_doc)
        stats = StrategyRunStats()
        try:
            await self._update_run(run_id, strategy_id, user_id, {"status": StrategyRunStatus.RUNNING.value, "started_at": now_tz()})
            await self._emit_event(run_id, strategy_id, "load_universe", "加载股票池", "正在读取本地A股基础信息", 5)
            symbols = await self._load_universe(strategy.parse_result.config)
            await db[self.RESULTS].delete_many({"strategy_id": strategy_id, "run_id": run_id})
            await self._emit_event(run_id, strategy_id, "scan", "执行规则", f"开始扫描{len(symbols)}只股票", 10)

            results: List[StrategyRunResult] = []
            for idx, basic in enumerate(symbols, start=1):
                code = str(basic.get("code") or "")
                rows = await self._load_daily_rows(code, run.as_of_date)
                ev = self.engine.evaluate(code, str(basic.get("name") or ""), rows, basic, strategy.parse_result.config, run.as_of_date)
                stats.total_scanned += 1
                if ev.passed_initial:
                    stats.initial_candidates += 1
                if ev.trend_confirmed:
                    stats.trend_confirmed += 1
                if ev.quality_passed:
                    stats.quality_candidates += 1
                if ev.result:
                    results.append(ev.result)
                    stats.selected_count += 1
                    await db[self.RESULTS].insert_one({"strategy_id": strategy_id, "run_id": run_id, **ev.result.model_dump()})
                    await self._upsert_pool(strategy_id, user_id, ev.result)
                if idx % 50 == 0 or idx == len(symbols):
                    pct = min(92, 10 + int(idx / max(len(symbols), 1) * 80))
                    await self._emit_event(run_id, strategy_id, "scan", "执行规则", f"已扫描{idx}/{len(symbols)}，入选{stats.selected_count}只", pct)
                    await self._update_run(run_id, strategy_id, user_id, {"progress": {"percent": pct, "step": "scan", "message": f"已扫描{idx}/{len(symbols)}"}, "stats": stats.model_dump()})

            daily_review = await self.narrative.build_daily_review(results, stats)
            next_day_plan = await self.narrative.build_next_day_plan(results)
            summary = f"扫描{stats.total_scanned}只，初筛{stats.initial_candidates}只，强趋势{stats.trend_confirmed}只，入池{stats.selected_count}只。"
            await self._emit_event(run_id, strategy_id, "finalize", "完成", summary, 100)
            await self._update_run(
                run_id,
                strategy_id,
                user_id,
                {
                    "status": StrategyRunStatus.COMPLETED.value,
                    "completed_at": now_tz(),
                    "progress": {"percent": 100, "step": "finalize", "message": "任务完成"},
                    "stats": stats.model_dump(),
                    "summary": summary,
                    "daily_review": daily_review,
                    "next_day_plan": next_day_plan,
                },
            )
        except Exception as e:
            logger.error("[strategy] run failed: %s", e, exc_info=True)
            await self._update_run(run_id, strategy_id, user_id, {"status": StrategyRunStatus.FAILED.value, "completed_at": now_tz(), "error": str(e)})

    async def create_backtest(self, strategy_id: str, user_id: str, req: StrategyBacktestCreateRequest) -> Optional[StrategyBacktestResponse]:
        await self.ensure_indexes()
        strategy = await self._load_strategy(strategy_id, user_id)
        if not strategy:
            return None
        bt_id = f"sbt_{now_tz().strftime('%Y%m%d')}_{uuid.uuid4().hex[:10]}"
        bt = StrategyBacktest(backtest_id=bt_id, strategy_id=strategy_id, user_id=str(user_id), strategy_version=strategy.version, request=req)
        await get_mongo_db()[self.BACKTESTS].insert_one(bt.model_dump(by_alias=True))
        return self._to_backtest_response(bt)

    async def get_backtest(self, strategy_id: str, backtest_id: str, user_id: str) -> Optional[StrategyBacktestResponse]:
        doc = await get_mongo_db()[self.BACKTESTS].find_one({"strategy_id": strategy_id, "backtest_id": backtest_id, "user_id": str(user_id)})
        return self._to_backtest_response(StrategyBacktest.model_validate(doc)) if doc else None

    async def run_backtest_background(self, strategy_id: str, backtest_id: str, user_id: str) -> None:
        db = get_mongo_db()
        strategy = await self._load_strategy(strategy_id, user_id)
        bt_doc = await db[self.BACKTESTS].find_one({"strategy_id": strategy_id, "backtest_id": backtest_id, "user_id": str(user_id)})
        if not strategy or not bt_doc:
            return
        bt = StrategyBacktest.model_validate(bt_doc)
        try:
            await db[self.BACKTESTS].update_one({"backtest_id": backtest_id}, {"$set": {"status": "running", "started_at": now_tz()}})
            symbols = await self._load_universe(strategy.parse_result.config, bt.request.max_symbols)
            dates = pd.date_range(bt.request.start_date, bt.request.end_date, freq="B")
            signals: List[Dict[str, Any]] = []
            by_type: Dict[str, List[float]] = {}

            for d_idx, current in enumerate(dates, start=1):
                as_of = current.date().isoformat()
                for basic in symbols:
                    code = str(basic.get("code") or "")
                    rows = await self._load_daily_rows(code, as_of, before_days=220, after_days=bt.request.holding_days + 1)
                    ev = self.engine.evaluate(code, str(basic.get("name") or ""), rows, basic, strategy.parse_result.config, as_of)
                    if not ev.result or not ev.result.buy_signals:
                        continue
                    ret = self._forward_return(rows, as_of, bt.request.holding_days)
                    if ret is None:
                        continue
                    signal_type = ev.result.buy_signals[0].signal_type
                    signals.append({"date": as_of, "code": ev.result.code, "name": ev.result.name, "signal_type": signal_type, "return": ret, "score": ev.result.total_score})
                    by_type.setdefault(signal_type, []).append(ret)
                if d_idx % 5 == 0 or d_idx == len(dates):
                    pct = min(95, int(d_idx / max(len(dates), 1) * 95))
                    await db[self.BACKTESTS].update_one({"backtest_id": backtest_id}, {"$set": {"progress": {"percent": pct, "step": "replay", "message": f"回放至{as_of}"}}})

            await db[self.BACKTEST_RESULTS].delete_many({"strategy_id": strategy_id, "backtest_id": backtest_id})
            if signals:
                await db[self.BACKTEST_RESULTS].insert_many([{"strategy_id": strategy_id, "backtest_id": backtest_id, **s} for s in signals])
            returns = [float(s["return"]) for s in signals]
            metrics = self._build_backtest_metrics(returns, by_type)
            summary = f"回测完成：共产生{metrics.total_signals}个买点信号，胜率{metrics.win_rate:.2%}，平均收益{metrics.avg_return:.2%}。"
            await db[self.BACKTESTS].update_one(
                {"backtest_id": backtest_id},
                {"$set": {"status": "completed", "completed_at": now_tz(), "progress": {"percent": 100, "step": "finalize", "message": "回测完成"}, "metrics": metrics.model_dump(), "summary": summary}},
            )
        except Exception as e:
            logger.error("[strategy] backtest failed: %s", e, exc_info=True)
            await db[self.BACKTESTS].update_one({"backtest_id": backtest_id}, {"$set": {"status": "failed", "completed_at": now_tz(), "error": str(e)}})

    async def _load_strategy(self, strategy_id: str, user_id: str) -> Optional[StrategyDefinition]:
        doc = await get_mongo_db()[self.STRATEGIES].find_one({"strategy_id": strategy_id, "user_id": str(user_id)})
        return StrategyDefinition.model_validate(doc) if doc else None

    async def _load_universe(self, config: Any, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        cursor = get_mongo_db()["stock_basic_info"].find({}, {"_id": 0, "code": 1, "name": 1, "list_date": 1, "listing_date": 1, "industry": 1, "total_mv": 1, "market_cap": 1}).sort("code", 1)
        items: List[Dict[str, Any]] = []
        async for doc in cursor:
            code = str(doc.get("code") or "")
            if len(code) == 6 and code.isdigit():
                items.append(doc)
                if limit and len(items) >= limit:
                    break
        return items

    async def _load_daily_rows(self, code: str, as_of_date: Optional[str], before_days: int = 260, after_days: int = 0) -> List[Dict[str, Any]]:
        end = pd.to_datetime(as_of_date).date() if as_of_date else now_tz().date()
        start = end - timedelta(days=before_days * 2)
        final = end + timedelta(days=after_days * 2)
        cursor = (
            get_mongo_db()["stock_daily_quotes"]
            .find({"symbol": code, "period": "daily", "trade_date": {"$gte": datetime.combine(start, datetime.min.time()), "$lte": datetime.combine(final, datetime.max.time())}}, {"_id": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "trade_date": 1})
            .sort("trade_date", 1)
        )
        return [doc async for doc in cursor]

    async def _upsert_pool(self, strategy_id: str, user_id: str, result: StrategyRunResult) -> None:
        db = get_mongo_db()
        now = now_tz()
        existing = await db[self.POOL].find_one({"strategy_id": strategy_id, "user_id": str(user_id), "code": result.code})
        update = {
            "name": result.name,
            "status": result.status.value,
            "last_signal_date": result.signal_date,
            "last_score": result.total_score,
            "entry_reason": result.entry_reason,
            "evidence": result.evidence,
        }
        if existing:
            update["tracking_days"] = int(existing.get("tracking_days") or 0) + 1
            await db[self.POOL].update_one({"_id": existing["_id"]}, {"$set": update})
        else:
            await db[self.POOL].insert_one({"strategy_id": strategy_id, "user_id": str(user_id), "code": result.code, "entered_at": now, "entry_date": result.signal_date, "tracking_days": 1, **update})

    async def _emit_event(self, run_id: str, strategy_id: str, step: str, title: str, message: str, progress: int, data: Optional[Dict[str, Any]] = None) -> None:
        ev = StrategyRunEvent(run_id=run_id, strategy_id=strategy_id, step=step, title=title, message=message, progress=progress, data=data or {})
        await get_mongo_db()[self.EVENTS].insert_one(ev.model_dump())

    async def _update_run(self, run_id: str, strategy_id: str, user_id: str, update: Dict[str, Any]) -> None:
        await get_mongo_db()[self.RUNS].update_one({"run_id": run_id, "strategy_id": strategy_id, "user_id": str(user_id)}, {"$set": update})

    def _forward_return(self, rows: List[Dict[str, Any]], as_of_date: str, holding_days: int) -> Optional[float]:
        df = pd.DataFrame(rows)
        if df.empty:
            return None
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df = df.sort_values("trade_date")
        current = df[df["trade_date"] <= pd.to_datetime(as_of_date)].tail(1)
        future = df[df["trade_date"] > pd.to_datetime(as_of_date)].head(holding_days)
        if current.empty or future.empty:
            return None
        return float(future["close"].iloc[-1] / current["close"].iloc[-1] - 1)

    def _build_backtest_metrics(self, returns: List[float], by_type: Dict[str, List[float]]) -> StrategyBacktestMetrics:
        if not returns:
            return StrategyBacktestMetrics()
        grouped = {
            key: {"count": len(vals), "win_rate": sum(1 for v in vals if v > 0.02) / len(vals), "avg_return": sum(vals) / len(vals)}
            for key, vals in by_type.items()
            if vals
        }
        return StrategyBacktestMetrics(
            total_signals=len(returns),
            wins=sum(1 for r in returns if r > 0.02),
            win_rate=sum(1 for r in returns if r > 0.02) / len(returns),
            avg_return=sum(returns) / len(returns),
            max_favorable_return=max(returns),
            max_adverse_return=min(returns),
            by_signal_type=grouped,
        )

    def _to_summary(self, doc: StrategyDefinition) -> StrategySummary:
        return StrategySummary(
            strategy_id=doc.strategy_id,
            name=doc.name,
            version=doc.version,
            status=doc.status,
            schedule=doc.schedule,
            validation_status=doc.parse_result.status,
            errors=doc.parse_result.errors,
            warnings=doc.parse_result.warnings,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )

    def _to_detail(self, doc: StrategyDefinition) -> StrategyDetail:
        base = self._to_summary(doc).model_dump()
        return StrategyDetail(**base, markdown=doc.markdown, config=doc.parse_result.config)

    def _to_run_response(self, run: StrategyRun) -> StrategyRunResponse:
        return StrategyRunResponse(**run.model_dump(exclude={"id", "cancel_requested"}))

    def _to_backtest_response(self, bt: StrategyBacktest) -> StrategyBacktestResponse:
        return StrategyBacktestResponse(**bt.model_dump(exclude={"id", "request"}))


_strategy_task_service: Optional[StrategyTaskService] = None


def get_strategy_task_service() -> StrategyTaskService:
    global _strategy_task_service
    if _strategy_task_service is None:
        _strategy_task_service = StrategyTaskService()
    return _strategy_task_service


async def run_enabled_strategy_jobs_once() -> None:
    """Hook for schedulers: create and run one job for each enabled strategy."""
    svc = get_strategy_task_service()
    await svc.ensure_indexes()
    db = get_mongo_db()
    cursor = db[svc.STRATEGIES].find({"status": StrategyStatus.ENABLED.value})
    async for doc in cursor:
        strategy = StrategyDefinition.model_validate(doc)
        created = await svc.create_run(strategy.strategy_id, strategy.user_id, run_type="scheduled")
        if created:
            asyncio.create_task(svc.run_task_background(strategy.strategy_id, created.run_id, strategy.user_id))


async def register_enabled_strategy_jobs(scheduler: Any, timezone: Any) -> None:
    """Register one APScheduler cron job per enabled strategy definition."""
    from apscheduler.triggers.cron import CronTrigger

    svc = get_strategy_task_service()
    await svc.ensure_indexes()
    db = get_mongo_db()

    for job in list(scheduler.get_jobs()):
        if job.id.startswith("strategy_daily_run_"):
            scheduler.remove_job(job.id)

    cursor = db[svc.STRATEGIES].find({"status": StrategyStatus.ENABLED.value, "schedule.enabled": True})
    async for doc in cursor:
        strategy = StrategyDefinition.model_validate(doc)
        job_id = f"strategy_daily_run_{strategy.strategy_id}"

        async def _run_one(strategy_id: str = strategy.strategy_id, user_id: str = strategy.user_id) -> None:
            created = await svc.create_run(strategy_id, user_id, run_type="scheduled")
            if created:
                await svc.run_task_background(strategy_id, created.run_id, user_id)

        scheduler.add_job(
            _run_one,
            CronTrigger.from_crontab(strategy.schedule.cron, timezone=timezone),
            id=job_id,
            name=f"策略每日筛选与复盘 - {strategy.name}",
            replace_existing=True,
        )
