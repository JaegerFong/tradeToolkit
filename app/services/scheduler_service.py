#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
定时任务管理服务 (PostgreSQL)
提供定时任务的查询、暂停、恢复、手动触发等功能
"""

import asyncio
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.job import Job
from apscheduler.events import (
    EVENT_JOB_EXECUTED,
    EVENT_JOB_ERROR,
    EVENT_JOB_MISSED,
    JobExecutionEvent
)

from sqlalchemy import select, func, text

from app.core.database import async_session_factory, sync_session_factory
from tradingagents.utils.logging_manager import get_logger
from app.utils.timezone import now_tz

logger = get_logger(__name__)

# UTC+8 时区
UTC_8 = timezone(timedelta(hours=8))


def get_utc8_now():
    """获取 UTC+8 当前时间（naive datetime）"""
    return now_tz().replace(tzinfo=None)


class TaskCancelledException(Exception):
    """任务被取消异常"""
    pass


from app.core.config import settings

# ---- 确保 scheduler 辅助表存在 ----
async def _ensure_scheduler_tables():
    """确保 scheduler_executions 和 scheduler_metadata 表存在"""
    from app.core.pg_models import Base
    from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.orm import declarative_base

    # 检查表是否存在，不存在则创建
    async with async_session_factory() as session:
        # 检查 scheduler_executions 表
        result = await session.execute(text(
            f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = '{settings.PG_APP_SCHEMA}' AND table_name = 'scheduler_executions')"
        ))
        if not result.scalar():
            await session.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {settings.PG_APP_SCHEMA}.scheduler_executions (
                    id SERIAL PRIMARY KEY,
                    job_id VARCHAR(255),
                    job_name VARCHAR(255),
                    status VARCHAR(50),
                    scheduled_time TIMESTAMP,
                    execution_time DOUBLE PRECISION,
                    return_value TEXT,
                    error_message TEXT,
                    traceback TEXT,
                    progress INTEGER DEFAULT 0,
                    progress_message TEXT,
                    current_item TEXT,
                    total_items INTEGER,
                    processed_items INTEGER,
                    is_manual BOOLEAN DEFAULT FALSE,
                    cancel_requested BOOLEAN DEFAULT FALSE,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    extra JSONB DEFAULT '{{}}'
                )
            """))
            await session.commit()

        # 检查 scheduler_metadata 表
        result = await session.execute(text(
            f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = '{settings.PG_APP_SCHEMA}' AND table_name = 'scheduler_metadata')"
        ))
        if not result.scalar():
            await session.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {settings.PG_APP_SCHEMA}.scheduler_metadata (
                    id SERIAL PRIMARY KEY,
                    job_id VARCHAR(255) UNIQUE,
                    display_name VARCHAR(255),
                    description TEXT,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    extra JSONB DEFAULT '{{}}'
                )
            """))
            await session.commit()

        # 检查 scheduler_history 表
        result = await session.execute(text(
            f"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_schema = '{settings.PG_APP_SCHEMA}' AND table_name = 'scheduler_history')"
        ))
        if not result.scalar():
            await session.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {settings.PG_APP_SCHEMA}.scheduler_history (
                    id SERIAL PRIMARY KEY,
                    job_id VARCHAR(255),
                    action VARCHAR(50),
                    status VARCHAR(50),
                    error_message TEXT,
                    timestamp TIMESTAMP DEFAULT NOW(),
                    extra JSONB DEFAULT '{{}}'
                )
            """))
            await session.commit()


class SchedulerService:
    """定时任务管理服务"""

    def __init__(self, scheduler: AsyncIOScheduler):
        self.scheduler = scheduler
        self._tables_ensured = False

        # 添加事件监听器，监控任务执行
        self._setup_event_listeners()

    async def _ensure_tables(self):
        if not self._tables_ensured:
            await _ensure_scheduler_tables()
            self._tables_ensured = True

    async def list_jobs(self) -> List[Dict[str, Any]]:
        await self._ensure_tables()
        jobs = []
        for job in self.scheduler.get_jobs():
            job_dict = self._job_to_dict(job)
            metadata = await self._get_job_metadata(job.id)
            if metadata:
                job_dict["display_name"] = metadata.get("display_name")
                job_dict["description"] = metadata.get("description")
            jobs.append(job_dict)
        logger.info(f"获取到 {len(jobs)} 个定时任务")
        return jobs

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        job = self.scheduler.get_job(job_id)
        if job:
            job_dict = self._job_to_dict(job, include_details=True)
            metadata = await self._get_job_metadata(job_id)
            if metadata:
                job_dict["display_name"] = metadata.get("display_name")
                job_dict["description"] = metadata.get("description")
            return job_dict
        return None

    async def pause_job(self, job_id: str) -> bool:
        try:
            self.scheduler.pause_job(job_id)
            logger.info(f"任务 {job_id} 已暂停")
            await self._record_job_action(job_id, "pause", "success")
            return True
        except Exception as e:
            logger.error(f"暂停任务 {job_id} 失败: {e}")
            await self._record_job_action(job_id, "pause", "failed", str(e))
            return False

    async def resume_job(self, job_id: str) -> bool:
        try:
            self.scheduler.resume_job(job_id)
            logger.info(f"任务 {job_id} 已恢复")
            await self._record_job_action(job_id, "resume", "success")
            return True
        except Exception as e:
            logger.error(f"恢复任务 {job_id} 失败: {e}")
            await self._record_job_action(job_id, "resume", "failed", str(e))
            return False

    async def trigger_job(self, job_id: str, kwargs: Optional[Dict[str, Any]] = None) -> bool:
        try:
            job = self.scheduler.get_job(job_id)
            if not job:
                logger.error(f"任务 {job_id} 不存在")
                return False

            was_paused = job.next_run_time is None
            if was_paused:
                logger.warning(f"任务 {job_id} 处于暂停状态，临时恢复以执行一次")
                self.scheduler.resume_job(job_id)
                job = self.scheduler.get_job(job_id)

            if kwargs:
                original_kwargs = job.kwargs.copy() if job.kwargs else {}
                merged_kwargs = {**original_kwargs, **kwargs}
                job.modify(kwargs=merged_kwargs)

            now = datetime.now(timezone.utc)
            job.modify(next_run_time=now)
            logger.info(f"手动触发任务 {job_id}")

            await self._record_job_action(job_id, "trigger", "success")
            await self._record_job_execution(
                job_id=job_id,
                status="running",
                scheduled_time=get_utc8_now(),
                progress=0,
                is_manual=True
            )
            return True
        except Exception as e:
            logger.error(f"触发任务 {job_id} 失败: {e}")
            await self._record_job_action(job_id, "trigger", "failed", str(e))
            return False

    async def get_job_history(
        self,
        job_id: str,
        limit: int = 20,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        await self._ensure_tables()
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"SELECT * FROM {settings.PG_APP_SCHEMA}.scheduler_history WHERE job_id = :jid ORDER BY timestamp DESC LIMIT :lim OFFSET :off")
                    .bindparams(jid=job_id, lim=limit, off=offset)
                )
                rows = result.all()
                cols = result.keys()
                history = []
                for row in rows:
                    d = dict(zip(cols, row))
                    d.pop("id", None)
                    for time_field in ["timestamp"]:
                        if d.get(time_field) and hasattr(d[time_field], 'isoformat'):
                            d[time_field] = d[time_field].isoformat()
                    history.append(d)
                return history
        except Exception as e:
            logger.error(f"获取任务 {job_id} 执行历史失败: {e}")
            return []

    async def count_job_history(self, job_id: str) -> int:
        await self._ensure_tables()
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"SELECT COUNT(*) FROM {settings.PG_APP_SCHEMA}.scheduler_history WHERE job_id = :jid"),
                    {"jid": job_id}
                )
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"统计任务 {job_id} 执行历史失败: {e}")
            return 0

    async def get_all_history(
        self,
        limit: int = 50,
        offset: int = 0,
        job_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        await self._ensure_tables()
        try:
            where_clauses = []
            params = {"lim": limit, "off": offset}
            if job_id:
                where_clauses.append("job_id = :jid")
                params["jid"] = job_id
            if status:
                where_clauses.append("status = :st")
                params["st"] = status

            where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"SELECT * FROM {settings.PG_APP_SCHEMA}.scheduler_history {where} ORDER BY timestamp DESC LIMIT :lim OFFSET :off"),
                    params
                )
                rows = result.all()
                cols = result.keys()
                history = []
                for row in rows:
                    d = dict(zip(cols, row))
                    d.pop("id", None)
                    for time_field in ["timestamp"]:
                        if d.get(time_field) and hasattr(d[time_field], 'isoformat'):
                            d[time_field] = d[time_field].isoformat()
                    history.append(d)
                return history
        except Exception as e:
            logger.error(f"获取执行历史失败: {e}")
            return []

    async def count_all_history(
        self,
        job_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> int:
        await self._ensure_tables()
        try:
            where_clauses = []
            params = {}
            if job_id:
                where_clauses.append("job_id = :jid")
                params["jid"] = job_id
            if status:
                where_clauses.append("status = :st")
                params["st"] = status

            where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"SELECT COUNT(*) FROM {settings.PG_APP_SCHEMA}.scheduler_history {where}"),
                    params
                )
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"统计执行历史失败: {e}")
            return 0

    async def get_job_executions(
        self,
        job_id: Optional[str] = None,
        status: Optional[str] = None,
        is_manual: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        await self._ensure_tables()
        try:
            where_clauses = []
            params = {"lim": limit, "off": offset}
            if job_id:
                where_clauses.append("job_id = :jid")
                params["jid"] = job_id
            if status:
                where_clauses.append("status = :st")
                params["st"] = status
            if is_manual is not None:
                if is_manual:
                    where_clauses.append("is_manual = TRUE")
                else:
                    where_clauses.append("(is_manual = FALSE OR is_manual IS NULL)")

            where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"SELECT * FROM {settings.PG_APP_SCHEMA}.scheduler_executions {where} ORDER BY timestamp DESC LIMIT :lim OFFSET :off"),
                    params
                )
                rows = result.all()
                cols = result.keys()
                executions = []
                for row in rows:
                    d = dict(zip(cols, row))
                    d["_id"] = str(d.get("id", ""))
                    for time_field in ["scheduled_time", "timestamp", "updated_at"]:
                        if d.get(time_field) and hasattr(d[time_field], 'isoformat'):
                            d[time_field] = d[time_field].isoformat()
                    executions.append(d)
                return executions
        except Exception as e:
            logger.error(f"获取任务执行历史失败: {e}")
            return []

    async def count_job_executions(
        self,
        job_id: Optional[str] = None,
        status: Optional[str] = None,
        is_manual: Optional[bool] = None
    ) -> int:
        await self._ensure_tables()
        try:
            where_clauses = []
            params = {}
            if job_id:
                where_clauses.append("job_id = :jid")
                params["jid"] = job_id
            if status:
                where_clauses.append("status = :st")
                params["st"] = status
            if is_manual is not None:
                if is_manual:
                    where_clauses.append("is_manual = TRUE")
                else:
                    where_clauses.append("(is_manual = FALSE OR is_manual IS NULL)")

            where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"SELECT COUNT(*) FROM {settings.PG_APP_SCHEMA}.scheduler_executions {where}"),
                    params
                )
                return result.scalar() or 0
        except Exception as e:
            logger.error(f"统计任务执行历史失败: {e}")
            return 0

    async def cancel_job_execution(self, execution_id: str) -> bool:
        await self._ensure_tables()
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"UPDATE {settings.PG_APP_SCHEMA}.scheduler_executions SET cancel_requested = TRUE, updated_at = :now WHERE id = :eid AND status = 'running'"),
                    {"now": get_utc8_now(), "eid": int(execution_id)}
                )
                await session.commit()
                return result.rowcount > 0
        except (ValueError, TypeError):
            return False
        except Exception as e:
            logger.error(f"取消任务执行失败: {e}")
            return False

    async def mark_execution_as_failed(self, execution_id: str, reason: str = "用户手动标记为失败") -> bool:
        await self._ensure_tables()
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"UPDATE {settings.PG_APP_SCHEMA}.scheduler_executions SET status = 'failed', error_message = :reason, updated_at = :now WHERE id = :eid"),
                    {"reason": reason, "now": get_utc8_now(), "eid": int(execution_id)}
                )
                await session.commit()
                return result.rowcount > 0
        except (ValueError, TypeError):
            return False
        except Exception as e:
            logger.error(f"标记执行记录失败: {e}")
            return False

    async def delete_execution(self, execution_id: str) -> bool:
        await self._ensure_tables()
        try:
            async with async_session_factory() as session:
                # Check not running
                chk = await session.execute(
                    text(f"SELECT status FROM {settings.PG_APP_SCHEMA}.scheduler_executions WHERE id = :eid"),
                    {"eid": int(execution_id)}
                )
                row = chk.fetchone()
                if row and row[0] == "running":
                    return False

                result = await session.execute(
                    text(f"DELETE FROM {settings.PG_APP_SCHEMA}.scheduler_executions WHERE id = :eid"),
                    {"eid": int(execution_id)}
                )
                await session.commit()
                return result.rowcount > 0
        except (ValueError, TypeError):
            return False
        except Exception as e:
            logger.error(f"删除执行记录失败: {e}")
            return False

    async def get_job_execution_stats(self, job_id: str) -> Dict[str, Any]:
        await self._ensure_tables()
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"""
                        SELECT status, COUNT(*), AVG(execution_time)
                        FROM {settings.PG_APP_SCHEMA}.scheduler_executions
                        WHERE job_id = :jid
                        GROUP BY status
                    """),
                    {"jid": job_id}
                )
                rows = result.all()

                stats = {"total": 0, "success": 0, "failed": 0, "missed": 0, "avg_execution_time": 0}
                for row in rows:
                    status, count, avg_time = row
                    stats["total"] += count
                    stats[status] = count
                    if status == "success" and avg_time:
                        stats["avg_execution_time"] = round(avg_time, 2)

                # 最近一次执行
                last = await session.execute(
                    text(f"SELECT status, timestamp, execution_time FROM {settings.PG_APP_SCHEMA}.scheduler_executions WHERE job_id = :jid ORDER BY timestamp DESC LIMIT 1"),
                    {"jid": job_id}
                )
                lr = last.fetchone()
                if lr:
                    stats["last_execution"] = {
                        "status": lr[0],
                        "timestamp": lr[1].isoformat() if lr[1] and hasattr(lr[1], 'isoformat') else None,
                        "execution_time": lr[2],
                    }
                return stats
        except Exception as e:
            logger.error(f"获取任务执行统计失败: {e}")
            return {}

    async def get_stats(self) -> Dict[str, Any]:
        jobs = self.scheduler.get_jobs()
        total = len(jobs)
        running = sum(1 for job in jobs if job.next_run_time is not None)
        paused = total - running

        return {
            "total_jobs": total,
            "running_jobs": running,
            "paused_jobs": paused,
            "scheduler_running": self.scheduler.running,
            "scheduler_state": self.scheduler.state
        }

    async def health_check(self) -> Dict[str, Any]:
        return {
            "status": "healthy" if self.scheduler.running else "stopped",
            "running": self.scheduler.running,
            "state": self.scheduler.state,
            "timestamp": get_utc8_now().isoformat()
        }

    def _job_to_dict(self, job: Job, include_details: bool = False) -> Dict[str, Any]:
        result = {
            "id": job.id,
            "name": job.name or job.id,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "paused": job.next_run_time is None,
            "trigger": str(job.trigger),
        }
        if include_details:
            result.update({
                "func": f"{job.func.__module__}.{job.func.__name__}",
                "args": job.args,
                "kwargs": job.kwargs,
                "misfire_grace_time": job.misfire_grace_time,
                "max_instances": job.max_instances,
            })
        return result

    def _setup_event_listeners(self):
        self.scheduler.add_listener(self._on_job_executed, EVENT_JOB_EXECUTED)
        self.scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)
        self.scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)
        logger.info("APScheduler事件监听器已设置")

        self.scheduler.add_job(
            self._check_zombie_tasks,
            'interval',
            minutes=5,
            id='check_zombie_tasks',
            name='检测僵尸任务',
            replace_existing=True
        )
        logger.info("僵尸任务检测定时任务已添加")

    async def _check_zombie_tasks(self):
        await self._ensure_tables()
        try:
            threshold_time = get_utc8_now() - timedelta(minutes=30)

            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"SELECT * FROM {settings.PG_APP_SCHEMA}.scheduler_executions WHERE status = 'running' AND timestamp < :threshold"),
                    {"threshold": threshold_time}
                )
                zombie_tasks = result.all()
                cols = result.keys()

                for row in zombie_tasks:
                    d = dict(zip(cols, row))
                    eid = d.get("id")
                    await session.execute(
                        text(f"UPDATE {settings.PG_APP_SCHEMA}.scheduler_executions SET status = 'failed', error_message = '任务执行超时或进程异常终止', updated_at = :now WHERE id = :eid"),
                        {"now": get_utc8_now(), "eid": eid}
                    )

                if zombie_tasks:
                    logger.info(f"已标记 {len(zombie_tasks)} 个僵尸任务为失败状态")
                await session.commit()

        except Exception as e:
            logger.error(f"检测僵尸任务失败: {e}")

    def _on_job_executed(self, event: JobExecutionEvent):
        execution_time = None
        if event.scheduled_run_time:
            now = datetime.now(event.scheduled_run_time.tzinfo)
            execution_time = (now - event.scheduled_run_time).total_seconds()

        asyncio.create_task(self._record_job_execution(
            job_id=event.job_id,
            status="success",
            scheduled_time=event.scheduled_run_time,
            execution_time=execution_time,
            return_value=str(event.retval) if event.retval else None,
            progress=100
        ))

    def _on_job_error(self, event: JobExecutionEvent):
        execution_time = None
        if event.scheduled_run_time:
            now = datetime.now(event.scheduled_run_time.tzinfo)
            execution_time = (now - event.scheduled_run_time).total_seconds()

        asyncio.create_task(self._record_job_execution(
            job_id=event.job_id,
            status="failed",
            scheduled_time=event.scheduled_run_time,
            execution_time=execution_time,
            error_message=str(event.exception) if event.exception else None,
            traceback=event.traceback if hasattr(event, 'traceback') else None,
            progress=None
        ))

    def _on_job_missed(self, event: JobExecutionEvent):
        asyncio.create_task(self._record_job_execution(
            job_id=event.job_id,
            status="missed",
            scheduled_time=event.scheduled_run_time,
            progress=None
        ))

    async def _record_job_execution(
        self,
        job_id: str,
        status: str,
        scheduled_time: datetime = None,
        execution_time: float = None,
        return_value: str = None,
        error_message: str = None,
        traceback: str = None,
        progress: int = None,
        is_manual: bool = False
    ):
        await self._ensure_tables()
        try:
            job = self.scheduler.get_job(job_id)
            job_name = job.name if job else job_id

            scheduled_time_naive = None
            if scheduled_time:
                if scheduled_time.tzinfo is not None:
                    scheduled_time_naive = scheduled_time.astimezone(UTC_8).replace(tzinfo=None)
                else:
                    scheduled_time_naive = scheduled_time

            async with async_session_factory() as session:
                # 如果是完成状态，查找 running 记录更新
                if status in ["success", "failed"]:
                    five_minutes_ago = get_utc8_now() - timedelta(minutes=5)
                    result = await session.execute(
                        text(f"SELECT id FROM {settings.PG_APP_SCHEMA}.scheduler_executions WHERE job_id = :jid AND status = 'running' AND timestamp >= :threshold ORDER BY timestamp DESC LIMIT 1"),
                        {"jid": job_id, "threshold": five_minutes_ago}
                    )
                    row = result.fetchone()
                    if row:
                        updates = {
                            "status": status,
                            "execution_time": execution_time,
                            "updated_at": get_utc8_now(),
                        }
                        if return_value:
                            updates["return_value"] = return_value
                        if error_message:
                            updates["error_message"] = error_message
                        if traceback:
                            updates["traceback"] = traceback
                        if progress is not None:
                            updates["progress"] = progress

                        set_clauses = ", ".join(f"{k} = :{k}" for k in updates.keys())
                        updates["eid"] = row[0]
                        await session.execute(
                            text(f"UPDATE {settings.PG_APP_SCHEMA}.scheduler_executions SET {set_clauses} WHERE id = :eid"),
                            updates
                        )
                        await session.commit()

                        if status == "success":
                            logger.info(f"[任务执行] {job_name} 执行成功，耗时: {execution_time:.2f}秒")
                        else:
                            logger.error(f"[任务执行] {job_name} 执行失败: {error_message}")
                        return

                # 插入新记录
                insert_params = {
                    "job_id": job_id,
                    "job_name": job_name,
                    "status": status,
                    "scheduled_time": scheduled_time_naive,
                    "execution_time": execution_time,
                    "return_value": return_value,
                    "error_message": error_message,
                    "traceback": traceback,
                    "progress": progress or 0,
                    "is_manual": is_manual,
                    "timestamp": get_utc8_now(),
                    "updated_at": get_utc8_now(),
                }
                keys = [k for k, v in insert_params.items() if v is not None]
                values = {k: v for k, v in insert_params.items() if v is not None}
                placeholders = ", ".join(f":{k}" for k in keys)
                cols = ", ".join(keys)

                await session.execute(
                    text(f"INSERT INTO {settings.PG_APP_SCHEMA}.scheduler_executions ({cols}) VALUES ({placeholders})"),
                    values
                )
                await session.commit()

                if status == "success":
                    logger.info(f"[任务执行] {job_name} 执行成功")
                elif status == "failed":
                    logger.error(f"[任务执行] {job_name} 执行失败: {error_message}")

        except Exception as e:
            logger.error(f"记录任务执行历史失败: {e}")

    async def _record_job_action(
        self,
        job_id: str,
        action: str,
        status: str,
        error_message: str = None
    ):
        await self._ensure_tables()
        try:
            async with async_session_factory() as session:
                await session.execute(
                    text(f"INSERT INTO {settings.PG_APP_SCHEMA}.scheduler_history (job_id, action, status, error_message, timestamp) VALUES (:jid, :action, :status, :err, :ts)"),
                    {
                        "jid": job_id,
                        "action": action,
                        "status": status,
                        "err": error_message,
                        "ts": get_utc8_now(),
                    }
                )
                await session.commit()
        except Exception as e:
            logger.error(f"记录任务操作历史失败: {e}")

    async def _get_job_metadata(self, job_id: str) -> Optional[Dict[str, Any]]:
        await self._ensure_tables()
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    text(f"SELECT * FROM {settings.PG_APP_SCHEMA}.scheduler_metadata WHERE job_id = :jid"),
                    {"jid": job_id}
                )
                row = result.fetchone()
                if row:
                    cols = result.keys()
                    d = dict(zip(cols, row))
                    d.pop("id", None)
                    return d
                return None
        except Exception as e:
            logger.error(f"获取任务 {job_id} 元数据失败: {e}")
            return None

    async def update_job_metadata(
        self,
        job_id: str,
        display_name: Optional[str] = None,
        description: Optional[str] = None
    ) -> bool:
        await self._ensure_tables()
        try:
            job = self.scheduler.get_job(job_id)
            if not job:
                logger.error(f"任务 {job_id} 不存在")
                return False

            async with async_session_factory() as session:
                # Check existing
                result = await session.execute(
                    text(f"SELECT id FROM {settings.PG_APP_SCHEMA}.scheduler_metadata WHERE job_id = :jid"),
                    {"jid": job_id}
                )
                row = result.fetchone()
                now = get_utc8_now()

                if row:
                    set_parts = ["updated_at = :now"]
                    params = {"now": now, "jid": job_id}
                    if display_name is not None:
                        set_parts.append("display_name = :dn")
                        params["dn"] = display_name
                    if description is not None:
                        set_parts.append("description = :desc")
                        params["desc"] = description
                    await session.execute(
                        text(f"UPDATE {settings.PG_APP_SCHEMA}.scheduler_metadata SET {', '.join(set_parts)} WHERE job_id = :jid"),
                        params
                    )
                else:
                    await session.execute(
                        text(f"INSERT INTO {settings.PG_APP_SCHEMA}.scheduler_metadata (job_id, display_name, description, created_at, updated_at) VALUES (:jid, :dn, :desc, :now, :now)"),
                        {"jid": job_id, "dn": display_name, "desc": description, "now": now}
                    )
                await session.commit()

            logger.info(f"任务 {job_id} 元数据已更新")
            return True
        except Exception as e:
            logger.error(f"更新任务 {job_id} 元数据失败: {e}")
            return False


# 全局服务实例
_scheduler_service: Optional[SchedulerService] = None
_scheduler_instance: Optional[AsyncIOScheduler] = None


def set_scheduler_instance(scheduler: AsyncIOScheduler):
    global _scheduler_instance
    _scheduler_instance = scheduler
    logger.info("调度器实例已设置")


def get_scheduler_service() -> SchedulerService:
    global _scheduler_service, _scheduler_instance
    if _scheduler_instance is None:
        raise RuntimeError("调度器实例未设置，请先调用 set_scheduler_instance()")
    if _scheduler_service is None:
        _scheduler_service = SchedulerService(_scheduler_instance)
        logger.info("调度器服务实例已创建")
    return _scheduler_service


async def update_job_progress(
    job_id: str,
    progress: int,
    message: str = None,
    current_item: str = None,
    total_items: int = None,
    processed_items: int = None
):
    """更新任务执行进度（供定时任务内部调用）"""
    await _ensure_scheduler_tables()
    try:

        async with async_session_factory() as session:
            # 查找最近的 running 记录
            result = await session.execute(
                text(f"SELECT * FROM {settings.PG_APP_SCHEMA}.scheduler_executions WHERE job_id = :jid AND status = 'running' ORDER BY timestamp DESC LIMIT 1"),
                {"jid": job_id}
            )
            row = result.fetchone()
            cols = result.keys()

            if row:
                d = dict(zip(cols, row))
                if d.get("cancel_requested"):
                    raise TaskCancelledException(f"任务 {job_id} 已被用户取消")

                set_parts = ["progress = :prog", "status = 'running'", "updated_at = :now"]
                params = {"prog": progress, "now": get_utc8_now(), "eid": d["id"]}
                if message:
                    set_parts.append("progress_message = :msg")
                    params["msg"] = message
                if current_item:
                    set_parts.append("current_item = :ci")
                    params["ci"] = current_item
                if total_items is not None:
                    set_parts.append("total_items = :ti")
                    params["ti"] = total_items
                if processed_items is not None:
                    set_parts.append("processed_items = :pi")
                    params["pi"] = processed_items

                await session.execute(
                    text(f"UPDATE {settings.PG_APP_SCHEMA}.scheduler_executions SET {', '.join(set_parts)} WHERE id = :eid"),
                    params
                )
            else:
                # 创建新的执行记录
                job_name = job_id
                if _scheduler_instance:
                    j = _scheduler_instance.get_job(job_id)
                    if j:
                        job_name = j.name

                await session.execute(
                    text(f"INSERT INTO {settings.PG_APP_SCHEMA}.scheduler_executions (job_id, job_name, status, progress, progress_message, current_item, total_items, processed_items, scheduled_time, timestamp, updated_at) VALUES (:jid, :jname, 'running', :prog, :msg, :ci, :ti, :pi, :now, :now, :now)"),
                    {
                        "jid": job_id,
                        "jname": job_name,
                        "prog": progress,
                        "msg": message,
                        "ci": current_item,
                        "ti": total_items,
                        "pi": processed_items,
                        "now": get_utc8_now(),
                    }
                )
            await session.commit()

    except TaskCancelledException:
        raise
    except Exception as e:
        logger.error(f"更新任务进度失败: {e}")
