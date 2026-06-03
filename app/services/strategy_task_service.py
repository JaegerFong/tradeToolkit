from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from sqlalchemy import select, func, delete, update as sql_update
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_factory
from app.core.pg_models import (
    StrategyDefinition,
    StrategyRun,
    StrategyPool,
    StrategyBacktest,
    StockBasicInfo,
)
from app.models.strategy import (
    StrategyBacktest as StrategyBacktestModel,
    StrategyBacktestCreateRequest,
    StrategyBacktestMetrics,
    StrategyBacktestResponse,
    StrategyCreateRequest,
    StrategyDefinition as StrategyDefinitionModel,
    StrategyDetail,
    StrategyParseResult,
    StrategyPoolItem,
    StrategyPoolStatus,
    StrategyRun as StrategyRunModel,
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
    def __init__(self) -> None:
        self.parser = get_strategy_markdown_parser()
        self.engine = get_strong_trend_rule_engine()
        self.narrative = get_strategy_narrative_service()

    async def create_strategy(self, user_id: str, req: StrategyCreateRequest) -> StrategyDetail:
        parse = self.parser.parse(req.markdown)
        strategy_id = f"st_{now_tz().strftime('%Y%m%d')}_{uuid.uuid4().hex[:10]}"
        schedule = req.schedule.model_copy()
        if req.enabled:
            schedule.enabled = True

        doc = StrategyDefinitionModel(
            strategy_id=strategy_id,
            user_id=str(user_id),
            name=req.name,
            markdown=req.markdown,
            status=StrategyStatus.ENABLED if req.enabled else StrategyStatus.DRAFT,
            schedule=schedule,
            parse_result=parse,
        )

        async with async_session_factory() as session:
            sd = StrategyDefinition(
                name=req.name,
                status=StrategyStatus.ENABLED.value if req.enabled else StrategyStatus.DRAFT.value,
                config={
                    "strategy_id": strategy_id,
                    "user_id": str(user_id),
                    "markdown": req.markdown,
                    "schedule": schedule.model_dump(),
                    "parse_result": parse.model_dump(),
                },
                created_at=now_tz().replace(tzinfo=None),
                updated_at=now_tz().replace(tzinfo=None),
            )
            session.add(sd)
            await session.commit()
            await session.refresh(sd)

        return self._to_detail(doc)

    async def list_strategies(self, user_id: str) -> List[StrategySummary]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyDefinition).order_by(StrategyDefinition.updated_at.desc())
            )
            docs = result.scalars().all()
            items: List[StrategySummary] = []
            for doc in docs:
                config = doc.config or {}
                items.append(StrategySummary(
                    strategy_id=config.get("strategy_id", str(doc.id)),
                    name=doc.name,
                    version=config.get("version", 1),
                    status=StrategyStatus(doc.status),
                    schedule=None,
                    validation_status="ok",
                    errors=[],
                    warnings=[],
                    created_at=doc.created_at,
                    updated_at=doc.updated_at,
                ))
            return items

    async def get_strategy(self, strategy_id: str, user_id: str) -> Optional[StrategyDetail]:
        async with async_session_factory() as session:
            # Search in config JSONB for matching strategy_id
            result = await session.execute(
                select(StrategyDefinition).where(
                    StrategyDefinition.config['strategy_id'].as_string() == strategy_id
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return None
            config = doc.config or {}
            strategy = StrategyDefinitionModel(
                strategy_id=config.get("strategy_id", str(doc.id)),
                user_id=str(user_id),
                name=doc.name,
                markdown=config.get("markdown", ""),
                status=StrategyStatus(doc.status),
                schedule=None,
                parse_result=StrategyParseResult.model_validate(config.get("parse_result", {})),
            )
            return self._to_detail(strategy)

    async def update_strategy(self, strategy_id: str, user_id: str, req: StrategyUpdateRequest) -> Optional[StrategyDetail]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyDefinition).where(
                    StrategyDefinition.config['strategy_id'].as_string() == strategy_id
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return None

            config = dict(doc.config or {})
            markdown = req.markdown if req.markdown is not None else config.get("markdown", "")
            parse = self.parser.parse(markdown)
            config["markdown"] = markdown
            config["parse_result"] = parse.model_dump()
            if req.name is not None:
                doc.name = req.name
            if req.markdown is not None:
                config["version"] = config.get("version", 1) + 1
            if req.enabled is not None:
                doc.status = StrategyStatus.ENABLED.value if req.enabled else StrategyStatus.DISABLED.value
            if req.schedule is not None:
                config["schedule"] = req.schedule.model_dump()

            doc.config = config
            doc.updated_at = now_tz().replace(tzinfo=None)
            await session.commit()

        return await self.get_strategy(strategy_id, user_id)

    async def validate_strategy(self, strategy_id: str, user_id: str) -> Optional[StrategyParseResult]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyDefinition).where(
                    StrategyDefinition.config['strategy_id'].as_string() == strategy_id
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return None
            config = doc.config or {}
            return self.parser.parse(str(config.get("markdown") or ""))

    async def create_run(self, strategy_id: str, user_id: str, run_type: str = "manual", as_of_date: Optional[str] = None) -> Optional[StrategyRunResponse]:
        strategy = await self._load_strategy_model(strategy_id, user_id)
        if not strategy:
            return None

        # Check for existing running run
        async with async_session_factory() as session:
            running = await session.execute(
                select(StrategyRun).where(
                    StrategyRun.result['strategy_id'].as_string() == strategy_id,
                    StrategyRun.status.in_(["queued", "running"]),
                ).limit(1)
            )
            if running.scalar_one_or_none():
                return None

        run_id = f"sr_{now_tz().strftime('%Y%m%d')}_{uuid.uuid4().hex[:10]}"
        run = StrategyRunModel(
            run_id=run_id,
            strategy_id=strategy_id,
            user_id=str(user_id),
            strategy_version=strategy.version,
            run_type="scheduled" if run_type == "scheduled" else "manual",
            as_of_date=as_of_date,
        )

        async with async_session_factory() as session:
            sr = StrategyRun(
                status="queued",
                result={
                    "strategy_id": strategy_id,
                    "run_id": run_id,
                    "user_id": str(user_id),
                    "run_type": run_type,
                    "as_of_date": as_of_date,
                },
                started_at=now_tz().replace(tzinfo=None),
                created_at=now_tz().replace(tzinfo=None),
            )
            session.add(sr)
            await session.commit()

        return self._to_run_response(run)

    async def get_run(self, strategy_id: str, run_id: str, user_id: str) -> Optional[StrategyRunResponse]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyRun).where(
                    StrategyRun.result['run_id'].as_string() == run_id,
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return None
            config = doc.result or {}
            run = StrategyRunModel(
                run_id=run_id,
                strategy_id=strategy_id,
                user_id=str(user_id),
                run_type=config.get("run_type", "manual"),
                status=StrategyRunStatus(doc.status),
                started_at=doc.started_at,
                progress=None,
                stats=None,
                summary=None,
            )
            return self._to_run_response(run)

    async def list_run_events(self, strategy_id: str, run_id: str, user_id: str, limit: int = 200) -> List[StrategyRunEvent]:
        return []

    async def list_run_results(self, strategy_id: str, run_id: str, user_id: str, limit: int = 50, offset: int = 0) -> Tuple[int, List[StrategyRunResult]]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyRun).where(
                    StrategyRun.result['run_id'].as_string() == run_id,
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return 0, []
            config = doc.result or {}
            signals = config.get("signals", [])
            return len(signals), [StrategyRunResult.model_validate(s) for s in signals[offset:offset + limit]]

    async def list_pool(self, strategy_id: str, user_id: str, status: Optional[str] = None, limit: int = 200, offset: int = 0) -> List[StrategyPoolItem]:
        async with async_session_factory() as session:
            stmt = select(StrategyPool).where(
                StrategyPool.details['strategy_id'].as_string() == strategy_id
            )
            if status:
                stmt = stmt.where(StrategyPool.status == status)
            stmt = stmt.order_by(StrategyPool.score.desc()).offset(offset).limit(limit)
            result = await session.execute(stmt)
            docs = result.scalars().all()
            return [
                StrategyPoolItem(
                    code=d.stock_code,
                    name=d.stock_name,
                    status=StrategyPoolStatus(d.status),
                    last_signal_date=d.updated_at,
                    last_score=d.score or 0,
                    entry_reason=d.reason or "",
                    evidence={},
                )
                for d in docs
            ]

    async def run_task_background(self, strategy_id: str, run_id: str, user_id: str) -> None:
        strategy = await self._load_strategy_model(strategy_id, user_id)
        if not strategy:
            return

        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyRun).where(
                    StrategyRun.result['run_id'].as_string() == run_id,
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return
            if doc.status != "queued":
                return

            # Update to running
            doc.status = "running"
            doc.started_at = now_tz().replace(tzinfo=None)
            await session.commit()

        stats = StrategyRunStats()
        try:
            symbols = await self._load_universe(strategy.parse_result.config)
            if not symbols:
                async with async_session_factory() as session:
                    result = await session.execute(
                        select(StrategyRun).where(StrategyRun.result['run_id'].as_string() == run_id).limit(1)
                    )
                    run_doc = result.scalar_one_or_none()
                    if run_doc:
                        config = dict(run_doc.result or {})
                        config["status"] = "data_incomplete"
                        config["error"] = "本地 stock_basic_info 为空"
                        run_doc.result = config
                        run_doc.status = "data_incomplete"
                        run_doc.completed_at = now_tz().replace(tzinfo=None)
                        await session.commit()
                return

            results: List[StrategyRunResult] = []
            for idx, basic in enumerate(symbols, start=1):
                code = str(basic.get("code") or "")
                rows = await self._load_daily_rows(code, run_id)
                ev = self.engine.evaluate(code, str(basic.get("name") or ""), rows, basic, strategy.parse_result.config, run_id)
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
                    await self._upsert_pool(strategy_id, user_id, ev.result)

                if idx % 50 == 0 or idx == len(symbols):
                    # Update run with intermediate signals
                    async with async_session_factory() as session:
                        result = await session.execute(
                            select(StrategyRun).where(StrategyRun.result['run_id'].as_string() == run_id).limit(1)
                        )
                        run_doc = result.scalar_one_or_none()
                        if run_doc:
                            config = dict(run_doc.result or {})
                            config["signals"] = [r.model_dump() for r in results]
                            config["stats"] = stats.model_dump()
                            run_doc.result = config
                            await session.commit()

            # Finalize
            daily_review = await self.narrative.build_daily_review(results, stats)
            next_day_plan = await self.narrative.build_next_day_plan(results)
            summary = f"扫描{stats.total_scanned}只，初筛{stats.initial_candidates}只，强趋势{stats.trend_confirmed}只，入池{stats.selected_count}只。"

            async with async_session_factory() as session:
                result = await session.execute(
                    select(StrategyRun).where(StrategyRun.result['run_id'].as_string() == run_id).limit(1)
                )
                run_doc = result.scalar_one_or_none()
                if run_doc:
                    config = {
                        "strategy_id": strategy_id,
                        "run_id": run_id,
                        "user_id": str(user_id),
                        "signals": [r.model_dump() for r in results],
                        "stats": stats.model_dump(),
                        "summary": summary,
                        "daily_review": daily_review,
                        "next_day_plan": next_day_plan,
                    }
                    run_doc.result = config
                    run_doc.status = "completed"
                    run_doc.completed_at = now_tz().replace(tzinfo=None)
                    await session.commit()

        except Exception as e:
            logger.error("[strategy] run failed: %s", e, exc_info=True)
            async with async_session_factory() as session:
                result = await session.execute(
                    select(StrategyRun).where(StrategyRun.result['run_id'].as_string() == run_id).limit(1)
                )
                run_doc = result.scalar_one_or_none()
                if run_doc:
                    config = dict(run_doc.result or {})
                    config["error"] = str(e)
                    run_doc.result = config
                    run_doc.status = "failed"
                    run_doc.completed_at = now_tz().replace(tzinfo=None)
                    await session.commit()

    async def create_backtest(self, strategy_id: str, user_id: str, req: StrategyBacktestCreateRequest) -> Optional[StrategyBacktestResponse]:
        bt_id = f"sbt_{now_tz().strftime('%Y%m%d')}_{uuid.uuid4().hex[:10]}"
        async with async_session_factory() as session:
            bt = StrategyBacktest(
                strategy_id=0,
                status="queued",
                parameters={
                    "backtest_id": bt_id,
                    "strategy_id": strategy_id,
                    "user_id": str(user_id),
                    "start_date": req.start_date,
                    "end_date": req.end_date,
                    "max_symbols": req.max_symbols,
                    "holding_days": req.holding_days,
                },
                started_at=now_tz().replace(tzinfo=None),
                created_at=now_tz().replace(tzinfo=None),
            )
            session.add(bt)
            await session.commit()
            await session.refresh(bt)

        bt_model = StrategyBacktestModel(
            backtest_id=bt_id,
            strategy_id=strategy_id,
            user_id=str(user_id),
            strategy_version=1,
            request=req,
        )
        bt_model.status = "queued"
        return self._to_backtest_response(bt_model)

    async def get_backtest(self, strategy_id: str, backtest_id: str, user_id: str) -> Optional[StrategyBacktestResponse]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyBacktest).where(
                    StrategyBacktest.parameters['backtest_id'].as_string() == backtest_id,
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return None
            params = doc.parameters or {}
            bt = StrategyBacktestModel(
                backtest_id=backtest_id,
                strategy_id=strategy_id,
                user_id=str(user_id),
                strategy_version=1,
                request=StrategyBacktestCreateRequest(**params),
            )
            bt.status = doc.status
            bt.started_at = doc.started_at
            bt.completed_at = doc.completed_at
            return self._to_backtest_response(bt)

    async def run_backtest_background(self, strategy_id: str, backtest_id: str, user_id: str) -> None:
        strategy = await self._load_strategy_model(strategy_id, user_id)
        if not strategy:
            return

        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyBacktest).where(
                    StrategyBacktest.parameters['backtest_id'].as_string() == backtest_id,
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return

            params = doc.parameters or {}
            doc.status = "running"
            doc.started_at = now_tz().replace(tzinfo=None)
            await session.commit()

        try:
            symbols = await self._load_universe(strategy.parse_result.config, params.get("max_symbols"))
            start_date = params.get("start_date", "2024-01-01")
            end_date = params.get("end_date", "2024-12-31")
            holding_days = params.get("holding_days", 5)
            dates = pd.date_range(start_date, end_date, freq="B")
            signals: List[Dict[str, Any]] = []
            by_type: Dict[str, List[float]] = {}

            for d_idx, current in enumerate(dates, start=1):
                as_of = current.date().isoformat()
                for basic in symbols:
                    code = str(basic.get("code") or "")
                    rows = await self._load_daily_rows(code, backtest_id, before_days=220, after_days=holding_days + 1)
                    ev = self.engine.evaluate(code, str(basic.get("name") or ""), rows, basic, strategy.parse_result.config, as_of)
                    if not ev.result or not ev.result.buy_signals:
                        continue
                    ret = self._forward_return(rows, as_of, holding_days)
                    if ret is None:
                        continue
                    signal_type = ev.result.buy_signals[0].signal_type
                    signals.append({"date": as_of, "code": ev.result.code, "name": ev.result.name, "signal_type": signal_type, "return": ret, "score": ev.result.total_score})
                    by_type.setdefault(signal_type, []).append(ret)

            returns = [float(s["return"]) for s in signals]
            metrics = self._build_backtest_metrics(returns, by_type)
            summary = f"回测完成：共产生{metrics.total_signals}个买点信号，胜率{metrics.win_rate:.2%}，平均收益{metrics.avg_return:.2%}。"

            async with async_session_factory() as session:
                result = await session.execute(
                    select(StrategyBacktest).where(
                        StrategyBacktest.parameters['backtest_id'].as_string() == backtest_id,
                    ).limit(1)
                )
                bt_doc = result.scalar_one_or_none()
                if bt_doc:
                    config = dict(bt_doc.parameters or {})
                    config["signals"] = signals
                    config["metrics"] = metrics.model_dump()
                    config["summary"] = summary
                    bt_doc.parameters = config
                    bt_doc.result = {"signals": signals, "metrics": metrics.model_dump()}
                    bt_doc.status = "completed"
                    bt_doc.completed_at = now_tz().replace(tzinfo=None)
                    await session.commit()

        except Exception as e:
            logger.error("[strategy] backtest failed: %s", e, exc_info=True)
            async with async_session_factory() as session:
                result = await session.execute(
                    select(StrategyBacktest).where(
                        StrategyBacktest.parameters['backtest_id'].as_string() == backtest_id,
                    ).limit(1)
                )
                bt_doc = result.scalar_one_or_none()
                if bt_doc:
                    config = dict(bt_doc.parameters or {})
                    config["error"] = str(e)
                    bt_doc.parameters = config
                    bt_doc.status = "failed"
                    bt_doc.completed_at = now_tz().replace(tzinfo=None)
                    await session.commit()

    async def _load_strategy_model(self, strategy_id: str, user_id: str) -> Optional[StrategyDefinitionModel]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StrategyDefinition).where(
                    StrategyDefinition.config['strategy_id'].as_string() == strategy_id
                ).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return None
            config = doc.config or {}
            return StrategyDefinitionModel(
                strategy_id=config.get("strategy_id", str(doc.id)),
                user_id=str(user_id),
                name=doc.name,
                markdown=config.get("markdown", ""),
                status=StrategyStatus(doc.status),
                schedule=None,
                parse_result=StrategyParseResult.model_validate(config.get("parse_result", {})),
            )

    async def _load_universe(self, config: Any, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        async with async_session_factory() as session:
            stmt = select(
                StockBasicInfo.code, StockBasicInfo.name,
                StockBasicInfo.total_mv
            ).order_by(StockBasicInfo.code)
            if limit:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            return [
                {"code": row[0], "name": row[1], "total_mv": row[2]}
                for row in result.all()
                if row[0] and len(str(row[0])) == 6 and str(row[0]).isdigit()
            ]

    async def _load_daily_rows(self, code: str, as_of_date: Optional[str], before_days: int = 260, after_days: int = 0) -> List[Dict[str, Any]]:
        from app.core.pg_models import DailyData
        end = pd.to_datetime(as_of_date).date() if as_of_date else datetime.utcnow().date()
        start = end - timedelta(days=before_days * 2)
        final = end + timedelta(days=after_days * 2)

        async with async_session_factory() as session:
            result = await session.execute(
                select(
                    DailyData.date, DailyData.open, DailyData.high,
                    DailyData.low, DailyData.close, DailyData.volume,
                )
                .where(
                    DailyData.code == code,
                    DailyData.date >= start,
                    DailyData.date <= final,
                )
                .order_by(DailyData.date.asc())
            )
            return [
                {
                    "trade_date": row[0], "open": row[1], "high": row[2],
                    "low": row[3], "close": row[4], "volume": row[5],
                }
                for row in result.all()
            ]

    async def _upsert_pool(self, strategy_id: str, user_id: str, result: StrategyRunResult) -> None:
        now = now_tz().replace(tzinfo=None)
        async with async_session_factory() as session:
            existing_result = await session.execute(
                select(StrategyPool).where(
                    StrategyPool.details['strategy_id'].as_string() == strategy_id,
                    StrategyPool.stock_code == result.code,
                ).limit(1)
            )
            existing = existing_result.scalar_one_or_none()

            if existing:
                existing.score = result.total_score
                existing.reason = result.entry_reason
                existing.updated_at = now
            else:
                sp = StrategyPool(
                    stock_code=result.code,
                    stock_name=result.name,
                    status=result.status.value,
                    score=result.total_score,
                    reason=result.entry_reason,
                    details={
                        "strategy_id": strategy_id,
                        "user_id": str(user_id),
                        "signal_date": result.signal_date,
                        "evidence": result.evidence,
                    },
                    created_at=now,
                    updated_at=now,
                )
                session.add(sp)
            await session.commit()

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

    def _to_summary(self, doc: StrategyDefinitionModel) -> StrategySummary:
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

    def _to_detail(self, doc: StrategyDefinitionModel) -> StrategyDetail:
        base = self._to_summary(doc).model_dump()
        return StrategyDetail(**base, markdown=doc.markdown, config=doc.parse_result.config)

    def _to_run_response(self, run: StrategyRunModel) -> StrategyRunResponse:
        return StrategyRunResponse(**run.model_dump(exclude={"id", "cancel_requested"}))

    def _to_backtest_response(self, bt: StrategyBacktestModel) -> StrategyBacktestResponse:
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
    async with async_session_factory() as session:
        result = await session.execute(
            select(StrategyDefinition).where(StrategyDefinition.status == StrategyStatus.ENABLED.value)
        )
        docs = result.scalars().all()
        for doc in docs:
            config = doc.config or {}
            sid = config.get("strategy_id", str(doc.id))
            uid = config.get("user_id", "admin")
            created = await svc.create_run(sid, uid, run_type="scheduled")
            if created:
                asyncio.create_task(svc.run_task_background(sid, created.run_id, uid))


async def register_enabled_strategy_jobs(scheduler: Any, timezone: Any) -> None:
    """Register one APScheduler cron job per enabled strategy definition."""
    from apscheduler.triggers.cron import CronTrigger

    svc = get_strategy_task_service()

    for job in list(scheduler.get_jobs()):
        if job.id.startswith("strategy_daily_run_"):
            scheduler.remove_job(job.id)

    async with async_session_factory() as session:
        result = await session.execute(
            select(StrategyDefinition).where(StrategyDefinition.status == StrategyStatus.ENABLED.value)
        )
        docs = result.scalars().all()
        for doc in docs:
            config = doc.config or {}
            sid = config.get("strategy_id", str(doc.id))
            uid = config.get("user_id", "admin")
            schedule_cfg = config.get("schedule", {})
            cron_expr = schedule_cfg.get("cron", "0 18 * * 1-5")
            enabled = schedule_cfg.get("enabled", True)

            if not enabled:
                continue

            job_id = f"strategy_daily_run_{sid}"

            async def _run_one(strategy_id: str = sid, user_id: str = uid) -> None:
                created = await svc.create_run(strategy_id, user_id, run_type="scheduled")
                if created:
                    await svc.run_task_background(strategy_id, created.run_id, user_id)

            scheduler.add_job(
                _run_one,
                CronTrigger.from_crontab(cron_expr, timezone=timezone),
                id=job_id,
                name=f"策略每日筛选与复盘 - {doc.name}",
                replace_existing=True,
            )
