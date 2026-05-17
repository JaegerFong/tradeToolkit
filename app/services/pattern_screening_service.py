from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.database import get_mongo_db
from app.models.pattern_screening import (
    PatternResultDetail,
    PatternResultListItem,
    PatternScreeningCreateRequest,
    PatternScreeningEvent,
    PatternScreeningTask,
    PatternTaskResponse,
    PatternTaskStats,
    PatternTaskStatus,
)
from app.utils.timezone import now_tz
from app.services.pattern_detectors import detect_laoyatou, detect_n_shape
from app.services.pattern_llm_agent import get_pattern_llm_agent

logger = logging.getLogger("webapi")


class PatternScreeningService:
    TASKS_COLLECTION = "pattern_screening_tasks"
    RESULTS_COLLECTION = "pattern_screening_results"
    EVENTS_COLLECTION = "pattern_screening_events"

    async def create_task(self, user_id: str, request: PatternScreeningCreateRequest) -> Dict[str, Any]:
        task_id = f"ps_{now_tz().strftime('%Y%m%d')}_{uuid.uuid4().hex[:10]}"
        task = PatternScreeningTask(task_id=task_id, user_id=user_id, request=request)

        db = get_mongo_db()
        await db[self.TASKS_COLLECTION].insert_one(task.model_dump(by_alias=True))
        return {"task_id": task_id, "status": task.status.value}

    async def get_task(self, task_id: str, user_id: str) -> Optional[PatternTaskResponse]:
        db = get_mongo_db()
        doc = await db[self.TASKS_COLLECTION].find_one({"task_id": task_id, "user_id": user_id})
        if not doc:
            return None
        task = PatternScreeningTask.model_validate(doc)
        return PatternTaskResponse(
            task_id=task.task_id,
            status=task.status,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
            progress=task.progress,
            stats=task.stats,
            summary=task.summary,
            error=task.error,
        )

    async def list_events(self, task_id: str, user_id: str, limit: int = 200) -> List[PatternScreeningEvent]:
        db = get_mongo_db()
        task = await db[self.TASKS_COLLECTION].find_one({"task_id": task_id, "user_id": user_id}, {"_id": 1})
        if not task:
            return []
        cursor = (
            db[self.EVENTS_COLLECTION]
            .find({"task_id": task_id})
            .sort("timestamp", 1)
            .limit(limit)
        )
        events: List[PatternScreeningEvent] = []
        async for doc in cursor:
            events.append(PatternScreeningEvent.model_validate(doc))
        return events

    async def cancel_task(self, task_id: str, user_id: str) -> bool:
        db = get_mongo_db()
        res = await db[self.TASKS_COLLECTION].update_one(
            {"task_id": task_id, "user_id": user_id, "status": {"$in": [PatternTaskStatus.QUEUED, PatternTaskStatus.RUNNING]}},
            {"$set": {"cancel_requested": True}},
        )
        return res.modified_count > 0

    async def list_results(self, task_id: str, user_id: str, limit: int = 50, offset: int = 0) -> Tuple[int, List[PatternResultListItem]]:
        db = get_mongo_db()
        task = await db[self.TASKS_COLLECTION].find_one({"task_id": task_id, "user_id": user_id}, {"_id": 1})
        if not task:
            return 0, []
        total = await db[self.RESULTS_COLLECTION].count_documents({"task_id": task_id})
        cursor = (
            db[self.RESULTS_COLLECTION]
            .find({"task_id": task_id}, {"_id": 0, "task_id": 0, "detail": 0})
            .sort([("recommendation_score", -1), ("pattern_score", -1)])
            .skip(offset)
            .limit(limit)
        )
        items: List[PatternResultListItem] = []
        async for doc in cursor:
            items.append(PatternResultListItem.model_validate(doc))
        return total, items

    async def get_result_detail(self, task_id: str, code: str, user_id: str) -> Optional[PatternResultDetail]:
        db = get_mongo_db()
        task = await db[self.TASKS_COLLECTION].find_one({"task_id": task_id, "user_id": user_id}, {"_id": 1})
        if not task:
            return None
        doc = await db[self.RESULTS_COLLECTION].find_one({"task_id": task_id, "code": code}, {"_id": 0, "task_id": 0})
        if not doc:
            return None
        detail = doc.get("detail")
        if detail:
            return PatternResultDetail.model_validate(detail)

        # 兼容：若未存 detail，则用列表字段拼一个最小详情
        return PatternResultDetail(
            code=doc["code"],
            name=doc["name"],
            pattern_type=doc["pattern_type"],
            pattern_score=doc.get("pattern_score", 0),
            recommendation_score=doc.get("recommendation_score", 0),
            pattern_breakdown={},
            analysis=doc.get("brief_reason", ""),
            trend_expectation="",
            buy_price_range=(doc.get("price", 0.0), doc.get("price", 0.0)),
            position_suggestion="",
            stop_loss=0.0,
            risk_points=["形态结果仅供研究与教育用途"],
            invalid_conditions=[],
            evidence={},
        )

    # -------- background execution --------

    async def run_task_background(self, task_id: str, user_id: str) -> None:
        db = get_mongo_db()
        task_doc = await db[self.TASKS_COLLECTION].find_one({"task_id": task_id, "user_id": user_id})
        if not task_doc:
            return

        task = PatternScreeningTask.model_validate(task_doc)
        if task.status not in (PatternTaskStatus.QUEUED, PatternTaskStatus.RUNNING):
            return

        await self._update_task_status(db, task_id, user_id, PatternTaskStatus.RUNNING, started_at=now_tz())
        start_ts = time.time()

        try:
            await self._emit_event(db, task_id, "init", "初始化", "开始执行技术形态选股任务", 1)
            await self._emit_event(db, task_id, "load_universe", "加载股票池", "正在加载本地股票池", 5)

            # 股票池：从 stock_basic_info 拉取 code/name/total_mv（字段兼容）
            min_mv = task.request.universe.min_market_cap
            query: Dict[str, Any] = {}
            if min_mv is not None:
                # 兼容 total_mv / market_cap 两种字段
                query["$or"] = [{"total_mv": {"$gte": min_mv}}, {"market_cap": {"$gte": min_mv}}]

            projection = {"_id": 0, "code": 1, "name": 1, "total_mv": 1, "market_cap": 1, "industry": 1}
            cursor = db["stock_basic_info"].find(query, projection)

            codes: List[Dict[str, Any]] = []
            async for doc in cursor:
                code = str(doc.get("code") or "").strip()
                if len(code) != 6 or not code.isdigit():
                    continue
                if task.request.universe.industries:
                    if str(doc.get("industry") or "") not in task.request.universe.industries:
                        continue
                codes.append(doc)

            total_symbols = len(codes)
            stats = PatternTaskStats(total_scanned=0, candidate_count=0, selected_count=0)
            await self._emit_event(db, task_id, "load_universe", "加载股票池", f"股票池加载完成，共 {total_symbols} 只", 10)

            # 清理旧结果（允许重复运行同一 task_id 时幂等）
            await db[self.RESULTS_COLLECTION].delete_many({"task_id": task_id})

            lookback_days = int(task.request.window.lookback_days)
            await self._emit_event(db, task_id, "load_kline", "读取K线", f"开始读取最近 {lookback_days} 日K线并扫描形态", 15)

            # 计算起始日期（简化：按自然日回退，最终以 trade_date 过滤）
            start_date = (now_tz().date() - timedelta(days=lookback_days * 2)).strftime("%Y-%m-%d")

            # 遍历股票：逐股读取最近 N 日 daily K 线（性能后续可优化为批量聚合）
            selected: List[Dict[str, Any]] = []
            llm_reviewed = 0
            llm_agent = get_pattern_llm_agent()
            for idx, info in enumerate(codes, start=1):
                # 取消检查
                latest_task = await db[self.TASKS_COLLECTION].find_one(
                    {"task_id": task_id, "user_id": user_id},
                    {"cancel_requested": 1, "_id": 0},
                )
                if latest_task and latest_task.get("cancel_requested"):
                    await self._emit_event(db, task_id, "cancel", "取消", "任务已被用户取消", 100)
                    await self._update_task_status(db, task_id, user_id, PatternTaskStatus.CANCELLED, completed_at=now_tz())
                    return

                code6 = str(info.get("code"))
                name = str(info.get("name") or "")

                # 取最近窗口 K 线
                k_cursor = (
                    db["stock_daily_quotes"]
                    .find(
                        {
                            "symbol": code6,
                            "period": "daily",
                            "trade_date": {"$gte": start_date},
                        },
                        {"_id": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1, "trade_date": 1},
                    )
                    .sort("trade_date", 1)
                )

                rows: List[Dict[str, Any]] = []
                async for r in k_cursor:
                    rows.append(r)
                if len(rows) < max(30, lookback_days // 2):
                    stats.total_scanned += 1
                    continue

                import pandas as pd

                df = pd.DataFrame(rows).tail(lookback_days)

                cand = None
                # 形态二选一（首期：只要任一命中就入候选；若多形态命中，以分高者为准）
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

                    # 最新行情 + 市值
                    q = await db["market_quotes"].find_one({"code": code6}, {"_id": 0, "close": 1, "change": 1, "pct_chg": 1})
                    price = float(q.get("close") or 0.0) if q else float(df["close"].astype(float).iloc[-1])
                    change_amount = float(q.get("change") or 0.0) if q else 0.0
                    pct_chg = float(q.get("pct_chg") or 0.0) if q else 0.0

                    market_cap = float(info.get("total_mv") or info.get("market_cap") or 0.0)

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
                        trend_expectation="形态结果仅供研究与教育用途；建议结合行业与基本面进一步验证。",
                        buy_price_range=(max(0.0, price * 0.99), price * 1.02),
                        position_suggestion="建议小仓位试探，突破确认后再考虑加仓；请自行评估风险承受能力。",
                        stop_loss=max(0.0, price * 0.95),
                        risk_points=["形态可能失效", "数据不完整会导致误判", "结果不构成投资建议"],
                        invalid_conditions=["跌破关键均线/平台下沿", "放量失败并回落"],
                        evidence=cand.evidence,
                    )

                    # LLM 复核（可选）：只复核前 max_reviews 只候选，失败则自动降级
                    if task.request.llm.enabled and llm_reviewed < int(task.request.llm.max_reviews):
                        llm_reviewed += 1
                        await self._emit_event(
                            db,
                            task_id,
                            "llm_review",
                            "LLM复核",
                            f"正在复核候选股 {code6}（{llm_reviewed}/{task.request.llm.max_reviews}）",
                            min(95, int(15 + (idx / max(total_symbols, 1)) * 80)),
                            data={"symbol": code6, "pattern_type": cand.pattern_type},
                        )
                        llm_json = await llm_agent.review(detail, cand.evidence)
                        if isinstance(llm_json, dict):
                            try:
                                # 允许 LLM 对分数、摘要与交易计划做更贴近用户的补充
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
                                # 忽略单条解析失败，继续用规则输出
                                pass

                    await db[self.RESULTS_COLLECTION].insert_one(
                        {
                            "task_id": task_id,
                            **item.model_dump(),
                            "detail": detail.model_dump(),
                        }
                    )
                    stats.selected_count += 1

                if idx % 50 == 0 or idx == total_symbols:
                    percent = int(15 + (idx / max(total_symbols, 1)) * 80)
                    await self._emit_event(
                        db,
                        task_id,
                        "detect_patterns",
                        "扫描形态",
                        f"已扫描 {idx}/{total_symbols}，命中候选 {stats.candidate_count}，入选 {stats.selected_count}",
                        percent,
                        data={"scanned": idx, "total": total_symbols},
                    )
                    await self._update_task_progress(
                        db,
                        task_id,
                        user_id,
                        percent=percent,
                        step="detect_patterns",
                        message=f"扫描中：{idx}/{total_symbols}",
                        stats=stats,
                    )

            await self._emit_event(db, task_id, "finalize", "完成", f"扫描完成，入选 {stats.selected_count} 只", 100)
            await self._update_task_progress(
                db,
                task_id,
                user_id,
                percent=100,
                step="finalize",
                message="任务完成",
                stats=stats,
                summary=f"扫描 {stats.total_scanned} 只，候选 {stats.candidate_count} 只，入选 {stats.selected_count} 只。",
            )
            await self._update_task_status(db, task_id, user_id, PatternTaskStatus.COMPLETED, completed_at=now_tz())
        except Exception as e:
            logger.error(f"[pattern_screening] task failed: task_id={task_id} err={e}", exc_info=True)
            await self._update_task_status(db, task_id, user_id, PatternTaskStatus.FAILED, completed_at=now_tz(), error=str(e))
        finally:
            elapsed = time.time() - start_ts
            logger.info(f"[pattern_screening] finished task_id={task_id} elapsed={elapsed:.2f}s")

    async def _emit_event(
        self,
        db: AsyncIOMotorDatabase,
        task_id: str,
        step: str,
        title: str,
        message: str,
        progress: int,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        ev = PatternScreeningEvent(
            task_id=task_id,
            step=step,
            title=title,
            message=message,
            progress=progress,
            data=data or {},
        )
        await db[self.EVENTS_COLLECTION].insert_one(ev.model_dump())

        # 同步更新任务快照（便于前端只查 tasks/{id} 也能拿到最新状态）
        await db[self.TASKS_COLLECTION].update_one(
            {"task_id": task_id},
            {
                "$set": {
                    "progress.percent": progress,
                    "progress.step": step,
                    "progress.message": message,
                }
            },
        )

    async def _update_task_progress(
        self,
        db: AsyncIOMotorDatabase,
        task_id: str,
        user_id: str,
        percent: int,
        step: str,
        message: str,
        stats: Optional[PatternTaskStats] = None,
        summary: Optional[str] = None,
    ) -> None:
        update: Dict[str, Any] = {
            "progress.percent": percent,
            "progress.step": step,
            "progress.message": message,
        }
        if stats is not None:
            update["stats"] = stats.model_dump()
        if summary is not None:
            update["summary"] = summary
        await db[self.TASKS_COLLECTION].update_one({"task_id": task_id, "user_id": user_id}, {"$set": update})

    async def _update_task_status(
        self,
        db: AsyncIOMotorDatabase,
        task_id: str,
        user_id: str,
        status: PatternTaskStatus,
        started_at: Optional[datetime] = None,
        completed_at: Optional[datetime] = None,
        error: Optional[str] = None,
    ) -> None:
        update: Dict[str, Any] = {"status": status.value if isinstance(status, PatternTaskStatus) else str(status)}
        if started_at is not None:
            update["started_at"] = started_at
        if completed_at is not None:
            update["completed_at"] = completed_at
        if error is not None:
            update["error"] = error
        await db[self.TASKS_COLLECTION].update_one({"task_id": task_id, "user_id": user_id}, {"$set": update})


_svc: Optional[PatternScreeningService] = None


def get_pattern_screening_service() -> PatternScreeningService:
    global _svc
    if _svc is None:
        _svc = PatternScreeningService()
    return _svc
