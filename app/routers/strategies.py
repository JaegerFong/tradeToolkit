from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.models.strategy import (
    StrategyBacktestCreateRequest,
    StrategyBacktestResponse,
    StrategyCreateRequest,
    StrategyDetail,
    StrategyParseResult,
    StrategyPoolItem,
    StrategyRunEvent,
    StrategyRunResponse,
    StrategyRunResultList,
    StrategySummary,
    StrategyUpdateRequest,
)
from app.routers.auth_db import get_current_user
from app.services.strategy_task_service import get_strategy_task_service

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.post("", response_model=StrategyDetail)
async def create_strategy(req: StrategyCreateRequest, user: dict = Depends(get_current_user)):
    return await get_strategy_task_service().create_strategy(user["id"], req)


@router.get("", response_model=List[StrategySummary])
async def list_strategies(user: dict = Depends(get_current_user)):
    return await get_strategy_task_service().list_strategies(user["id"])


@router.get("/{strategy_id}", response_model=StrategyDetail)
async def get_strategy(strategy_id: str, user: dict = Depends(get_current_user)):
    item = await get_strategy_task_service().get_strategy(strategy_id, user["id"])
    if not item:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return item


@router.put("/{strategy_id}", response_model=StrategyDetail)
async def update_strategy(strategy_id: str, req: StrategyUpdateRequest, user: dict = Depends(get_current_user)):
    item = await get_strategy_task_service().update_strategy(strategy_id, user["id"], req)
    if not item:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return item


@router.post("/{strategy_id}/validate", response_model=StrategyParseResult)
async def validate_strategy(strategy_id: str, user: dict = Depends(get_current_user)):
    result = await get_strategy_task_service().validate_strategy(strategy_id, user["id"])
    if not result:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return result


@router.post("/{strategy_id}/runs", response_model=StrategyRunResponse)
async def create_strategy_run(
    strategy_id: str,
    background_tasks: BackgroundTasks,
    as_of_date: Optional[str] = Query(None, description="YYYY-MM-DD; empty means latest local data"),
    user: dict = Depends(get_current_user),
):
    svc = get_strategy_task_service()
    run = await svc.create_run(strategy_id, user["id"], run_type="manual", as_of_date=as_of_date)
    if not run:
        raise HTTPException(status_code=404, detail="Strategy not found")

    def _run_wrapper():
        asyncio.create_task(svc.run_task_background(strategy_id, run.run_id, user["id"]))

    background_tasks.add_task(_run_wrapper)
    return run


@router.get("/{strategy_id}/runs/{run_id}", response_model=StrategyRunResponse)
async def get_strategy_run(strategy_id: str, run_id: str, user: dict = Depends(get_current_user)):
    run = await get_strategy_task_service().get_run(strategy_id, run_id, user["id"])
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/{strategy_id}/runs/{run_id}/events", response_model=List[StrategyRunEvent])
async def list_strategy_run_events(
    strategy_id: str,
    run_id: str,
    limit: int = Query(200, ge=1, le=1000),
    user: dict = Depends(get_current_user),
):
    return await get_strategy_task_service().list_run_events(strategy_id, run_id, user["id"], limit)


@router.get("/{strategy_id}/runs/{run_id}/results", response_model=StrategyRunResultList)
async def list_strategy_run_results(
    strategy_id: str,
    run_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    total, items = await get_strategy_task_service().list_run_results(strategy_id, run_id, user["id"], limit, offset)
    return StrategyRunResultList(total=total, items=items)


@router.get("/{strategy_id}/pool", response_model=List[StrategyPoolItem])
async def list_strategy_pool(
    strategy_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    return await get_strategy_task_service().list_pool(strategy_id, user["id"], status, limit, offset)


@router.post("/{strategy_id}/backtests", response_model=StrategyBacktestResponse)
async def create_strategy_backtest(
    strategy_id: str,
    req: StrategyBacktestCreateRequest,
    background_tasks: BackgroundTasks,
    user: dict = Depends(get_current_user),
):
    svc = get_strategy_task_service()
    bt = await svc.create_backtest(strategy_id, user["id"], req)
    if not bt:
        raise HTTPException(status_code=404, detail="Strategy not found")

    def _run_wrapper():
        asyncio.create_task(svc.run_backtest_background(strategy_id, bt.backtest_id, user["id"]))

    background_tasks.add_task(_run_wrapper)
    return bt


@router.get("/{strategy_id}/backtests/{backtest_id}", response_model=StrategyBacktestResponse)
async def get_strategy_backtest(strategy_id: str, backtest_id: str, user: dict = Depends(get_current_user)):
    bt = await get_strategy_task_service().get_backtest(strategy_id, backtest_id, user["id"])
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return bt
