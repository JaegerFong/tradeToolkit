"""
Cleanup routines extracted from DatabaseService.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict

from sqlalchemy import select, delete

from app.core.database import async_session_factory
from app.core.pg_models import AnalysisTask, OperationLog


async def cleanup_old_data(days: int) -> Dict[str, Any]:
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    deleted_count = 0
    cleaned_collections = []

    async with async_session_factory() as session:
        res = await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.created_at < cutoff_date,
                AnalysisTask.status.in_(["completed", "failed"]),
            )
        )
        if res.rowcount:
            deleted_count += res.rowcount
            cleaned_collections.append(f"analysis_tasks: {res.rowcount}")

        await session.commit()

    return {
        "deleted_count": deleted_count,
        "cleaned_collections": cleaned_collections,
        "cutoff_date": cutoff_date.isoformat(),
    }


async def cleanup_analysis_results(days: int) -> Dict[str, Any]:
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    deleted_count = 0
    cleaned_collections = []

    async with async_session_factory() as session:
        res = await session.execute(
            delete(AnalysisTask).where(
                AnalysisTask.created_at < cutoff_date,
                AnalysisTask.status.in_(["completed", "failed"]),
            )
        )
        if res.rowcount:
            deleted_count += res.rowcount
            cleaned_collections.append(f"analysis_tasks: {res.rowcount}")

        await session.commit()

    return {
        "deleted_count": deleted_count,
        "cleaned_collections": cleaned_collections,
        "cutoff_date": cutoff_date.isoformat(),
    }


async def cleanup_operation_logs(days: int) -> Dict[str, Any]:
    cutoff_date = datetime.utcnow() - timedelta(days=days)

    deleted_count = 0
    cleaned_collections = []

    async with async_session_factory() as session:
        res = await session.execute(
            delete(OperationLog).where(OperationLog.created_at < cutoff_date)
        )
        if res.rowcount:
            deleted_count += res.rowcount
            cleaned_collections.append(f"operation_logs: {res.rowcount}")

        await session.commit()

    return {
        "deleted_count": deleted_count,
        "cleaned_collections": cleaned_collections,
        "cutoff_date": cutoff_date.isoformat(),
    }
