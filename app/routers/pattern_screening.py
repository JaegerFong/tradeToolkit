from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.routers.auth_db import get_current_user
from app.models.pattern_screening import (
    PatternResultDetail,
    PatternResultListResponse,
    PatternScreeningCreateRequest,
    PatternScreeningCreateResponse,
    PatternScreeningEvent,
    PatternTaskResponse,
)
from app.services.pattern_screening_service import get_pattern_screening_service

logger = logging.getLogger("webapi")

router = APIRouter(prefix="/pattern-screening", tags=["pattern_screening"])


@router.post("/tasks", response_model=PatternScreeningCreateResponse)
async def create_pattern_screening_task(
    request: PatternScreeningCreateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    svc = get_pattern_screening_service()
    created = await svc.create_task(user["id"], request)
    task_id = created["task_id"]

    async def _run():
        await svc.run_task_background(task_id, user["id"])

    # FastAPI BackgroundTasks 不支持直接 await async，因此用 asyncio.create_task 包一层
    def _run_wrapper():
        asyncio.create_task(_run())

    background_tasks.add_task(_run_wrapper)
    return PatternScreeningCreateResponse(task_id=task_id, status=created["status"])


@router.get("/tasks/{task_id}", response_model=PatternTaskResponse)
async def get_pattern_screening_task(task_id: str, user: dict = Depends(get_current_user)):
    svc = get_pattern_screening_service()
    task = await svc.get_task(task_id, user["id"])
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.get("/tasks/{task_id}/events", response_model=List[PatternScreeningEvent])
async def list_pattern_screening_events(
    task_id: str,
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(get_current_user),
):
    svc = get_pattern_screening_service()
    return await svc.list_events(task_id, user["id"], limit=limit)


@router.get("/tasks/{task_id}/results", response_model=PatternResultListResponse)
async def list_pattern_screening_results(
    task_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    svc = get_pattern_screening_service()
    total, items = await svc.list_results(task_id, user["id"], limit=limit, offset=offset)
    return PatternResultListResponse(total=total, items=items)


@router.get("/tasks/{task_id}/results/{code}", response_model=PatternResultDetail)
async def get_pattern_screening_result_detail(task_id: str, code: str, user: dict = Depends(get_current_user)):
    svc = get_pattern_screening_service()
    detail = await svc.get_result_detail(task_id, code, user["id"])
    if not detail:
        raise HTTPException(status_code=404, detail="Result not found")
    return detail


@router.post("/tasks/{task_id}/cancel", response_model=Dict[str, Any])
async def cancel_pattern_screening_task(task_id: str, user: dict = Depends(get_current_user)):
    svc = get_pattern_screening_service()
    ok = await svc.cancel_task(task_id, user["id"])
    return {"success": ok}

