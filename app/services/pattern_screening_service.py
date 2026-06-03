from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select, func, delete, update as sql_update

from app.core.database import async_session_factory
from app.core.pg_models import (
    PatternScreeningTask,
    StockBasicInfo,
    MarketQuotes,
)
from app.models.pattern_screening import (
    PatternResultDetail,
    PatternResultListItem,
    PatternScreeningCreateRequest,
    PatternScreeningEvent,
    PatternScreeningTask as PatternScreeningTaskModel,
    PatternTaskResponse,
    PatternTaskStats,
    PatternTaskStatus,
)
from app.services.pattern_detectors import detect_laoyatou, detect_n_shape
from app.services.pattern_llm_agent import get_pattern_llm_agent

logger = logging.getLogger("webapi")


class PatternScreeningService:
    TASKS_TABLE = "pattern_screening_tasks"

    async def create_task(self, user_id: str, request: PatternScreeningCreateRequest) -> Dict[str, Any]:
        task_id = f"ps_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:10]}"
        task = PatternScreeningTaskModel(task_id=task_id, user_id=user_id, request=request)

        async with async_session_factory() as session:
            pt = PatternScreeningTask(
                task_id=task_id,
                pattern_type=",".join([p.value for p in request.pattern_types]),
                status="queued",
                parameters=request.model_dump(),
                created_at=datetime.utcnow(),
            )
            session.add(pt)
            await session.commit()

        return {"task_id": task_id, "status": "queued"}

    async def get_task(self, task_id: str, user_id: str) -> Optional[PatternTaskResponse]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(PatternScreeningTask).where(PatternScreeningTask.task_id == task_id).limit(1)
            )
            doc = result.scalar_one_or_none()
            if not doc:
                return None
            params = doc.parameters or {}
            task = PatternScreeningTaskModel(
                task_id=doc.task_id,
                user_id=user_id,
                request=PatternScreeningCreateRequest(**params) if params else PatternScreeningCreateRequest(),
            )
            task.status = PatternTaskStatus(doc.status)
            task.created_at = doc.created_at
            task.updated_at = doc.updated_at
            return PatternTaskResponse(
                task_id=task.task_id,
                status=task.status,
                created_at=task.created_at,
                started_at=task.created_at,
                completed_at=doc.updated_at,
                progress=None,
                stats=None,
                summary=None,
                error=None,
            )

    async def list_events(self, task_id: str, user_id: str, limit: int = 200) -> List[PatternScreeningEvent]:
        # Events are now stored in the result JSONB; simplified to return empty
        return []

    async def cancel_task(self, task_id: str, user_id: str) -> bool:
        async with async_session_factory() as session:
            result = await session.execute(
                sql_update(PatternScreeningTask)
                .where(
                    PatternScreeningTask.task_id == task_id,
                    PatternScreeningTask.status.in_(["queued", "running"]),
                )
                .values(status="cancelled", result=PatternScreeningTask.result)
            )
            await session.commit()
            if result.rowcount == 0:
                # Try to just mark cancel_requested in parameters JSONB
                result2 = await session.execute(
                    select(PatternScreeningTask).where(PatternScreeningTask.task_id == task_id).limit(1)
                )
                pt = result2.scalar_one_or_none()
                if pt:
                    _params = dict(pt.parameters or {})
                    _params["cancel_requested"] = True
                    pt.parameters = _params
                    await session.commit()
                    return True
                return False
            return True

    async def list_results(self, task_id: str, user_id: str, limit: int = 50, offset: int = 0) -> Tuple[int, List[PatternResultListItem]]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(PatternScreeningTask).where(PatternScreeningTask.task_id == task_id).limit(1)
            )
            pt = result.scalar_one_or_none()
            if not pt:
                return 0, []

            _result = pt.result or {}
            results_list = _result.get("results", []) if isinstance(_result, dict) else []
            items = []
            for r in results_list[offset:offset + limit]:
                if isinstance(r, dict):
                    items.append(PatternResultListItem.model_validate(r))
            return len(results_list), items

    async def get_result_detail(self, task_id: str, code: str, user_id: str) -> Optional[PatternResultDetail]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(PatternScreeningTask).where(PatternScreeningTask.task_id == task_id).limit(1)
            )
            pt = result.scalar_one_or_none()
            if not pt:
                return None

            _result = pt.result or {}
            results_list = _result.get("results", []) if isinstance(_result, dict) else []
            for r in results_list:
                if isinstance(r, dict) and r.get("code", "").endswith(code):
                    detail = r.get("detail")
                    if detail:
                        return PatternResultDetail.model_validate(detail)
                    return PatternResultDetail(
                        code=r.get("code", code),
                        name=r.get("name", ""),
                        pattern_type=r.get("pattern_type", ""),
                        pattern_score=r.get("pattern_score", 0),
                        recommendation_score=r.get("recommendation_score", 0),
                        pattern_breakdown={},
                        analysis=r.get("brief_reason", ""),
                        trend_expectation="",
                        buy_price_range=(r.get("price", 0.0), r.get("price", 0.0)),
                        position_suggestion="",
                        stop_loss=0.0,
                        risk_points=["形态结果仅供研究与教育用途"],
                        invalid_conditions=[],
                        evidence={},
                    )
            return None

    # -------- background execution --------

    async def run_task_background(self, task_id: str, user_id: str) -> None:
        async with async_session_factory() as session:
            result = await session.execute(
                select(PatternScreeningTask).where(PatternScreeningTask.task_id == task_id).limit(1)
            )
            pt = result.scalar_one_or_none()
            if not pt:
                return

            params = pt.parameters or {}
            task = PatternScreeningTaskModel.model_validate({
                "task_id": task_id,
                "user_id": user_id,
                "request": params,
                "status": pt.status,
                "created_at": pt.created_at,
                "updated_at": pt.updated_at,
            })

            if task.status not in (PatternTaskStatus.QUEUED, PatternTaskStatus.RUNNING):
                return

        await self._update_task_status(task_id, user_id, PatternTaskStatus.RUNNING, started_at=datetime.utcnow())

        try:
            # 股票池：从 PG stock_basic_info 拉取
            min_mv = task.request.universe.min_market_cap
            async with async_session_factory() as session:
                stmt = select(
                    StockBasicInfo.code, StockBasicInfo.name,
                    StockBasicInfo.total_mv, StockBasicInfo.industry
                )
                if min_mv is not None:
                    stmt = stmt.where(StockBasicInfo.total_mv >= min_mv)
                result = await session.execute(stmt)
                rows = result.all()

                codes: List[Dict[str, Any]] = []
                for row in rows:
                    code = str(row[0] or "").strip()
                    if len(code) != 6 or not code.isdigit():
                        continue
                    if task.request.universe.industries:
                        if str(row[3] or "") not in task.request.universe.industries:
                            continue
                    codes.append({
                        "code": code,
                        "name": row[1] or "",
                        "total_mv": row[2],
                        "industry": row[3],
                    })

            total_symbols = len(codes)
            stats = PatternTaskStats(total_scanned=0, candidate_count=0, selected_count=0)

            lookback_days = int(task.request.window.lookback_days)
            start_date = (datetime.utcnow().date() - timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")

            selected: List[Dict[str, Any]] = []
            llm_reviewed = 0
            llm_agent = get_pattern_llm_agent()

            # 获取 K 线数据 (从 tdx2db public.daily_data)
            from app.core.pg_models import DailyData

            for idx, info in enumerate(codes, start=1):
                async with async_session_factory() as session:
                    cancel_result = await session.execute(
                        select(PatternScreeningTask).where(PatternScreeningTask.task_id == task_id).limit(1)
                    )
                    latest = cancel_result.scalar_one_or_none()
                    if latest and (latest.parameters or {}).get("cancel_requested"):
                        await self._update_task_status(task_id, user_id, PatternTaskStatus.CANCELLED, completed_at=datetime.utcnow())
                        return

                    code6 = info["code"]
                    name = info["name"]

                    k_result = await session.execute(
                        select(
                            DailyData.date, DailyData.open, DailyData.high,
                            DailyData.low, DailyData.close, DailyData.volume,
                        )
                        .where(
                            DailyData.code == code6,
                            DailyData.date >= start_date,
                        )
                        .order_by(DailyData.date.asc())
                    )
                    k_rows = k_result.all()

                    if len(k_rows) < max(30, lookback_days // 2):
                        stats.total_scanned += 1
                        continue

                    import pandas as pd
                    df = pd.DataFrame(k_rows, columns=["trade_date", "open", "high", "low", "close", "volume"]).tail(lookback_days)

                    cand = None
                    if "laoyatou" in [p.value for p in task.request.pattern_types]:
                        c1 = detect_laoyatou(
                            df,
                            min_up_pct=task.request.rules.min_up_pct,
                            max_drawdown=task.request.rules.max_drawdown,
                            consolidation_volume_ratio=task.request.rules.consolidation_volume_ratio,
                            breakout_volume_ratio=task.request.rules.breakout_volume_ratio,
                        )
                        cand = c1
                    if "n_shape" in [p.value for p in task.request.pattern_types]:
                        c2 = detect_n_shape(
                            df,
                            min_up_pct=max(0.10, task.request.rules.min_up_pct * 0.7),
                            max_drawdown=min(0.618, max(0.2, task.request.rules.max_drawdown)),
                            consolidation_volume_ratio=0.90,
                            breakout_volume_ratio=1.10,
                        )
                        if c2 and (cand is None or c2.pattern_score > cand.pattern_score):
                            cand = c2

                    stats.total_scanned += 1

                    if cand:
                        stats.candidate_count += 1

                        # 最新行情
                        q_result = await session.execute(
                            select(MarketQuotes.close, MarketQuotes.change, MarketQuotes.pct_chg)
                            .where(MarketQuotes.code == code6).limit(1)
                        )
                        q = q_result.first()
                        price = float(q[0] or 0.0) if q else float(df["close"].astype(float).iloc[-1])
                        change_amount = float(q[1] or 0.0) if q else 0.0
                        pct_chg = float(q[2] or 0.0) if q else 0.0

                        market_cap = float(info.get("total_mv") or 0.0)

                        item = PatternResultListItem(
                            code=f"{code6}.SZ" if code6.startswith(("0", "3")) else f"{code6}.SH",
                            name=name,
                            price=price,
                            change_amount=change_amount,
                            pct_chg=pct_chg,
                            market_cap=market_cap,
                            pattern_type=cand.pattern_type,
                            pattern_name="老鸭头" if cand.pattern_type == "laoyatou" else "N字形态",
                            pattern_score=cand.pattern_score,
                            recommendation_score=min(100, max(0, cand.pattern_score - 5)),
                            signal_date=str(df["trade_date"].iloc[-1].date()),
                            brief_reason=cand.brief_reason,
                        )

                        detail = PatternResultDetail(
                            code=item.code,
                            name=item.name,
                            pattern_type=item.pattern_type,
                            pattern_score=item.pattern_score,
                            recommendation_score=item.recommendation_score,
                            pattern_breakdown=cand.breakdown,
                            analysis=item.brief_reason,
                            trend_expectation="形态结果仅供研究与教育用途",
                            buy_price_range=(max(0.0, price * 0.99), price * 1.02),
                            position_suggestion="建议小仓位试探",
                            stop_loss=max(0.0, price * 0.95),
                            risk_points=["形态可能失效"],
                            invalid_conditions=["跌破关键均线"],
                            evidence=cand.evidence,
                        )

                        # LLM 复核
                        if task.request.llm.enabled and llm_reviewed < int(task.request.llm.max_reviews):
                            llm_reviewed += 1
                            llm_json = await llm_agent.review(detail, cand.evidence)
                            if isinstance(llm_json, dict):
                                try:
                                    detail.pattern_breakdown = llm_json.get("pattern_breakdown") or detail.pattern_breakdown
                                    detail.analysis = str(llm_json.get("analysis") or detail.analysis)
                                    detail.trend_expectation = str(llm_json.get("trend_expectation") or detail.trend_expectation)
                                    br = llm_json.get("buy_price_range")
                                    if isinstance(br, list) and len(br) == 2:
                                        detail.buy_price_range = (float(br[0]), float(br[1]))
                                    detail.position_suggestion = str(llm_json.get("position_suggestion") or detail.position_suggestion)
                                    if llm_json.get("stop_loss") is not None:
                                        detail.stop_loss = float(llm_json["stop_loss"])
                                    rp = llm_json.get("risk_points")
                                    if isinstance(rp, list) and rp:
                                        detail.risk_points = [str(x) for x in rp]
                                    ic = llm_json.get("invalid_conditions")
                                    if isinstance(ic, list) and ic:
                                        detail.invalid_conditions = [str(x) for x in ic]
                                    if llm_json.get("pattern_score") is not None:
                                        item.pattern_score = int(llm_json["pattern_score"])
                                        detail.pattern_score = item.pattern_score
                                    if llm_json.get("recommendation_score") is not None:
                                        item.recommendation_score = int(llm_json["recommendation_score"])
                                        detail.recommendation_score = item.recommendation_score
                                    if llm_json.get("brief_reason"):
                                        item.brief_reason = str(llm_json["brief_reason"])
                                except Exception:
                                    pass

                        selected.append({
                            **item.model_dump(),
                            "detail": detail.model_dump(),
                        })
                        stats.selected_count += 1

                if idx % 50 == 0 or idx == total_symbols:
                    await self._update_task_progress(task_id, user_id, int(15 + idx / max(total_symbols, 1) * 80), stats)
                    # Store intermediate results
                    async with async_session_factory() as session:
                        await session.execute(
                            sql_update(PatternScreeningTask)
                            .where(PatternScreeningTask.task_id == task_id)
                            .values(result={"results": selected, "stats": stats.model_dump()})
                        )
                        await session.commit()

            # Save final results
            async with async_session_factory() as session:
                await session.execute(
                    sql_update(PatternScreeningTask)
                    .where(PatternScreeningTask.task_id == task_id)
                    .values(
                        status="completed",
                        result={"results": selected, "stats": stats.model_dump()},
                        updated_at=datetime.utcnow(),
                    )
                )
                await session.commit()

            await self._update_task_status(task_id, user_id, PatternTaskStatus.COMPLETED, completed_at=datetime.utcnow())
        except Exception as e:
            logger.error(f"[pattern_screening] task failed: task_id={task_id} err={e}", exc_info=True)
            await self._update_task_status(task_id, user_id, PatternTaskStatus.FAILED, completed_at=datetime.utcnow(), error=str(e))

    async def _update_task_progress(
        self,
        task_id: str,
        user_id: str,
        percent: int,
        stats: Optional[PatternTaskStats] = None,
        summary: Optional[str] = None,
    ) -> None:
        async with async_session_factory() as session:
            values = {"status": "running", "updated_at": datetime.utcnow()}
            if stats is not None:
                params_result = await session.execute(
                    select(PatternScreeningTask).where(PatternScreeningTask.task_id == task_id).limit(1)
                )
                pt = params_result.scalar_one_or_none()
                if pt:
                    _params = dict(pt.parameters or {})
                    _params["progress"] = percent
                    _params["stats"] = stats.model_dump()
                    if summary:
                        _params["summary"] = summary
                    pt.parameters = _params
                    await session.commit()

    async def _update_task_status(
        self,
        task_id: str,
        user_id: str,
        status: PatternTaskStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ) -> None:
        status_val = status.value if isinstance(status, PatternTaskStatus) else str(status)
        async with async_session_factory() as session:
            values = {"status": status_val, "updated_at": completed_at or datetime.utcnow()}
            if error:
                result = await session.execute(
                    select(PatternScreeningTask).where(PatternScreeningTask.task_id == task_id).limit(1)
                )
                pt = result.scalar_one_or_none()
                if pt:
                    _params = dict(pt.parameters or {})
                    _params["error"] = error
                    pt.parameters = _params
                await session.commit()
                return

            await session.execute(
                sql_update(PatternScreeningTask)
                .where(PatternScreeningTask.task_id == task_id)
                .values(**values)
            )
            await session.commit()


_svc: Optional[PatternScreeningService] = None


def get_pattern_screening_service() -> PatternScreeningService:
    global _svc
    if _svc is None:
        _svc = PatternScreeningService()
    return _svc
