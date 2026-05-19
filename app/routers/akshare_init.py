"""
AKShare数据初始化API路由
提供Web接口进行AKShare数据初始化和管理
"""
import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from app.core.database import get_mongo_db
from app.worker.akshare_init_service import get_akshare_init_service
from app.worker.akshare_sync_service import get_akshare_sync_service
from app.routers.auth_db import get_current_user
from app.utils.timezone import now_tz

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/akshare-init", tags=["AKShare初始化"])

# 全局任务状态存储
_initialization_status = {
    "is_running": False,
    "current_task": None,
    "start_time": None,
    "progress": None,
    "result": None
}


class InitializationRequest(BaseModel):
    """初始化请求模型"""
    historical_days: int = Field(default=365, ge=1, le=3650, description="历史数据天数")
    force: bool = Field(default=False, description="是否强制重新初始化")
    skip_if_exists: bool = Field(default=True, description="如果数据存在是否跳过")


class StrategyDataSyncRequest(BaseModel):
    """策略数据同步请求模型"""
    historical_days: int = Field(default=365, ge=1, le=3650, description="历史数据天数")
    force: bool = Field(default=True, description="是否强制同步，已有数据时也继续执行")


class SyncRequest(BaseModel):
    """同步请求模型"""
    force_update: bool = Field(default=False, description="是否强制更新")
    symbols: Optional[list] = Field(default=None, description="指定股票代码列表")


@router.get("/status")
async def get_database_status():
    """
    获取数据库状态
    
    Returns:
        数据库状态信息
    """
    try:
        db = get_mongo_db()
        
        # 检查基础信息
        basic_count = await db.stock_basic_info.count_documents({})
        extended_count = await db.stock_basic_info.count_documents({
            "full_symbol": {"$exists": True},
            "market_info": {"$exists": True}
        })
        
        # 获取最新更新时间
        latest_basic = await db.stock_basic_info.find_one(
            {}, sort=[("updated_at", -1)]
        )
        
        # 检查行情数据
        quotes_count = await db.market_quotes.count_documents({})
        latest_quotes = await db.market_quotes.find_one(
            {}, sort=[("updated_at", -1)]
        )

        # 检查日线历史数据（策略工具主要依赖）
        daily_match = {"period": "daily"}
        daily_count = await db.stock_daily_quotes.count_documents(daily_match)
        daily_summary = await db.stock_daily_quotes.aggregate([
            {"$match": daily_match},
            {
                "$group": {
                    "_id": None,
                    "total_count": {"$sum": 1},
                    "symbol_count": {"$addToSet": "$symbol"},
                    "earliest_trade_date": {"$min": "$trade_date"},
                    "latest_trade_date": {"$max": "$trade_date"},
                    "latest_update": {"$max": "$updated_at"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "total_count": 1,
                    "symbol_count": {"$size": "$symbol_count"},
                    "earliest_trade_date": 1,
                    "latest_trade_date": 1,
                    "latest_update": 1,
                }
            }
        ]).to_list(length=1)
        daily_data = daily_summary[0] if daily_summary else {
            "total_count": daily_count,
            "symbol_count": 0,
            "earliest_trade_date": None,
            "latest_trade_date": None,
            "latest_update": None,
        }

        period_rows = await db.stock_daily_quotes.aggregate([
            {
                "$group": {
                    "_id": "$period",
                    "total_count": {"$sum": 1},
                    "symbol_count": {"$addToSet": "$symbol"},
                    "earliest_trade_date": {"$min": "$trade_date"},
                    "latest_trade_date": {"$max": "$trade_date"},
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "period": "$_id",
                    "total_count": 1,
                    "symbol_count": {"$size": "$symbol_count"},
                    "earliest_trade_date": 1,
                    "latest_trade_date": 1,
                }
            },
            {"$sort": {"period": 1}}
        ]).to_list(length=None)
        
        # 数据质量评估
        data_quality = "excellent"
        if basic_count == 0:
            data_quality = "empty"
        elif extended_count / basic_count < 0.5:
            data_quality = "poor"
        elif extended_count / basic_count < 0.9:
            data_quality = "good"
        
        return {
            "success": True,
            "data": {
                "basic_info": {
                    "total_count": basic_count,
                    "extended_count": extended_count,
                    "coverage_rate": round(extended_count / basic_count * 100, 2) if basic_count > 0 else 0,
                    "latest_update": latest_basic.get("updated_at") if latest_basic else None
                },
                "market_quotes": {
                    "total_count": quotes_count,
                    "latest_update": latest_quotes.get("updated_at") if latest_quotes else None
                },
                "historical_daily": daily_data,
                "historical_by_period": period_rows,
                "data_quality": data_quality,
                "check_time": now_tz()
            },
            "message": "数据库状态检查完成"
        }
        
    except Exception as e:
        logger.error(f"获取数据库状态失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取数据库状态失败: {str(e)}")


@router.get("/connection-test")
async def test_akshare_connection():
    """
    测试AKShare连接状态
    
    Returns:
        连接测试结果
    """
    try:
        service = await get_akshare_sync_service()
        connected = await service.provider.test_connection()
        
        result = {
            "connected": connected,
            "test_time": now_tz()
        }
        
        if connected:
            # 测试获取股票列表
            try:
                stock_list = await service.provider.get_stock_list()
                result["stock_count"] = len(stock_list) if stock_list else 0
                result["sample_stocks"] = stock_list[:5] if stock_list else []
            except Exception as e:
                result["stock_list_error"] = str(e)
        
        return {
            "success": True,
            "data": result,
            "message": "AKShare连接测试完成"
        }
        
    except Exception as e:
        logger.error(f"AKShare连接测试失败: {e}")
        raise HTTPException(status_code=500, detail=f"连接测试失败: {str(e)}")


@router.post("/start-full")
async def start_full_initialization(
    request: InitializationRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    启动完整的数据初始化
    
    Args:
        request: 初始化请求参数
        background_tasks: 后台任务管理器
        current_user: 当前用户信息
        
    Returns:
        初始化启动结果
    """
    global _initialization_status
    
    if _initialization_status["is_running"]:
        raise HTTPException(status_code=400, detail="初始化任务正在运行中")
    
    try:
        # 设置任务状态
        _initialization_status.update({
            "is_running": True,
            "current_task": "full_initialization",
            "start_time": now_tz(),
            "progress": {"current_step": "准备中", "completed_steps": 0, "total_steps": 6},
            "result": None
        })
        
        # 启动后台任务
        background_tasks.add_task(
            _run_full_initialization_background,
            request.historical_days,
            not request.skip_if_exists
        )
        
        return {
            "success": True,
            "data": {
                "task_id": "full_initialization",
                "start_time": _initialization_status["start_time"],
                "parameters": {
                    "historical_days": request.historical_days,
                    "force": not request.skip_if_exists
                }
            },
            "message": "完整初始化任务已启动，请使用 /initialization-status 查看进度"
        }
        
    except Exception as e:
        _initialization_status["is_running"] = False
        logger.error(f"启动完整初始化失败: {e}")
        raise HTTPException(status_code=500, detail=f"启动初始化失败: {str(e)}")


@router.post("/start-strategy-sync")
async def start_strategy_data_sync(
    request: StrategyDataSyncRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    启动策略工具所需数据同步：A 股基础信息 + 指定周期日线。

    该接口用于页面手动触发近 1 年、近 2 年、近 3 年等周期同步。
    """
    global _initialization_status

    if _initialization_status["is_running"]:
        raise HTTPException(status_code=400, detail="同步任务正在运行中")

    try:
        _initialization_status.update({
            "is_running": True,
            "current_task": "strategy_data_sync",
            "start_time": now_tz(),
            "progress": {
                "current_step": f"准备同步策略数据({request.historical_days}天)",
                "completed_steps": 0,
                "total_steps": 4
            },
            "result": None
        })

        background_tasks.add_task(
            _run_strategy_data_sync_background,
            request.historical_days,
            request.force
        )

        return {
            "success": True,
            "data": {
                "task_id": "strategy_data_sync",
                "start_time": _initialization_status["start_time"],
                "parameters": {
                    "historical_days": request.historical_days,
                    "force": request.force,
                    "sync_items": ["basic_info", "historical"]
                }
            },
            "message": "策略数据同步任务已启动"
        }

    except Exception as e:
        _initialization_status["is_running"] = False
        logger.error(f"启动策略数据同步失败: {e}")
        raise HTTPException(status_code=500, detail=f"启动同步失败: {str(e)}")


@router.post("/start-basic-sync")
async def start_basic_sync(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    启动基础信息同步
    
    Args:
        request: 同步请求参数
        background_tasks: 后台任务管理器
        current_user: 当前用户信息
        
    Returns:
        同步启动结果
    """
    global _initialization_status
    
    if _initialization_status["is_running"]:
        raise HTTPException(status_code=400, detail="同步任务正在运行中")
    
    try:
        # 设置任务状态
        _initialization_status.update({
            "is_running": True,
            "current_task": "basic_sync",
            "start_time": now_tz(),
            "progress": {"current_step": "同步基础信息", "completed_steps": 0, "total_steps": 1},
            "result": None
        })
        
        # 启动后台任务
        background_tasks.add_task(
            _run_basic_sync_background,
            request.force_update
        )
        
        return {
            "success": True,
            "data": {
                "task_id": "basic_sync",
                "start_time": _initialization_status["start_time"],
                "parameters": {
                    "force_update": request.force_update
                }
            },
            "message": "基础信息同步任务已启动"
        }
        
    except Exception as e:
        _initialization_status["is_running"] = False
        logger.error(f"启动基础信息同步失败: {e}")
        raise HTTPException(status_code=500, detail=f"启动同步失败: {str(e)}")


@router.get("/initialization-status")
async def get_initialization_status():
    """
    获取初始化任务状态
    
    Returns:
        当前任务状态
    """
    global _initialization_status
    
    return {
        "success": True,
        "data": {
            "is_running": _initialization_status["is_running"],
            "current_task": _initialization_status["current_task"],
            "start_time": _initialization_status["start_time"],
            "progress": _initialization_status["progress"],
            "result": _initialization_status["result"],
            "duration": (
                (now_tz() - _initialization_status["start_time"]).total_seconds()
                if _initialization_status["start_time"] else 0
            )
        },
        "message": "任务状态获取成功"
    }


@router.post("/stop")
async def stop_initialization(current_user: dict = Depends(get_current_user)):
    """
    停止当前初始化任务
    
    Args:
        current_user: 当前用户信息
        
    Returns:
        停止结果
    """
    global _initialization_status
    
    if not _initialization_status["is_running"]:
        raise HTTPException(status_code=400, detail="没有正在运行的任务")
    
    try:
        # 重置任务状态
        _initialization_status.update({
            "is_running": False,
            "current_task": None,
            "start_time": None,
            "progress": None,
            "result": {"stopped": True, "stop_time": datetime.utcnow()}
        })
        
        return {
            "success": True,
            "data": {
                "stopped": True,
                "stop_time": datetime.utcnow()
            },
            "message": "初始化任务已停止"
        }
        
    except Exception as e:
        logger.error(f"停止初始化任务失败: {e}")
        raise HTTPException(status_code=500, detail=f"停止任务失败: {str(e)}")


async def _run_full_initialization_background(historical_days: int, force: bool):
    """后台运行完整初始化"""
    global _initialization_status
    
    try:
        service = await get_akshare_init_service()
        result = await service.run_full_initialization(
            historical_days=historical_days,
            skip_if_exists=not force
        )
        
        _initialization_status.update({
            "is_running": False,
            "result": result
        })
        
        logger.info(f"完整初始化后台任务完成: {result}")
        
    except Exception as e:
        _initialization_status.update({
            "is_running": False,
            "result": {"success": False, "error": str(e)}
        })
        logger.error(f"完整初始化后台任务失败: {e}")


async def _run_strategy_data_sync_background(historical_days: int, force: bool):
    """后台运行策略数据同步"""
    global _initialization_status

    def _on_progress(step_name: str, completed: int, total: int):
        _initialization_status["progress"] = {
            "current_step": step_name,
            "completed_steps": completed,
            "total_steps": total,
        }

    try:
        service = await get_akshare_init_service()
        result = await service.run_full_initialization(
            historical_days=historical_days,
            skip_if_exists=not force,
            sync_items=["basic_info", "historical"],
            progress_callback=_on_progress,
        )

        _initialization_status.update({
            "is_running": False,
            "result": result
        })

        logger.info(f"策略数据同步后台任务完成: {result}")

    except Exception as e:
        _initialization_status.update({
            "is_running": False,
            "result": {"success": False, "error": str(e)}
        })
        logger.error(f"策略数据同步后台任务失败: {e}")


async def _run_basic_sync_background(force_update: bool):
    """后台运行基础信息同步"""
    global _initialization_status
    
    try:
        service = await get_akshare_sync_service()
        result = await service.sync_stock_basic_info(force_update=force_update)
        
        _initialization_status.update({
            "is_running": False,
            "result": result
        })
        
        logger.info(f"基础信息同步后台任务完成: {result}")
        
    except Exception as e:
        _initialization_status.update({
            "is_running": False,
            "result": {"success": False, "error": str(e)}
        })
        logger.error(f"基础信息同步后台任务失败: {e}")


# ── AKShare 代理配置 API ──────────────────────────────────

class ProxyConfigRequest(BaseModel):
    """代理配置请求"""
    proxy_mode: Optional[str] = Field(None, description="off / basic / strong")
    proxy_api_url: Optional[str] = Field(None, description="动态代理 API 地址")
    static_proxies: Optional[str] = Field(None, description="静态代理列表 (逗号分隔)")
    proxy_cache_seconds: Optional[int] = Field(None, ge=10, le=3600)
    proxy_rounds: Optional[int] = Field(None, ge=1, le=10)
    include_direct: Optional[bool] = None
    request_timeout: Optional[float] = Field(None, ge=5, le=120)
    request_retries: Optional[int] = Field(None, ge=1, le=10)
    min_request_interval: Optional[float] = Field(None, ge=0.1, le=30)
    use_curl_cffi: Optional[bool] = None


@router.get("/proxy-config")
async def get_proxy_config():
    """获取当前 AKShare 代理配置"""
    import os
    from tradingagents.dataflows.providers.china.akshare_network import get_network_stats

    env_config = {
        "proxy_mode": os.getenv("AKSHARE_PROXY_MODE", "strong"),
        "proxy_api_url": os.getenv("AKSHARE_PROXY_API_URL", ""),
        "static_proxies": os.getenv("AKSHARE_PROXIES", ""),
        "proxy_cache_seconds": int(os.getenv("AKSHARE_PROXY_CACHE_SECONDS", "60")),
        "proxy_rounds": int(os.getenv("AKSHARE_PROXY_ROUNDS", "2")),
        "include_direct": os.getenv("AKSHARE_PROXY_INCLUDE_DIRECT", "true").lower() not in ("0", "false", "no"),
        "request_timeout": float(os.getenv("AKSHARE_REQUEST_TIMEOUT", "20")),
        "request_retries": int(os.getenv("AKSHARE_REQUEST_RETRIES", "4")),
        "min_request_interval": float(os.getenv("AKSHARE_MIN_REQUEST_INTERVAL",
            os.getenv("AKSHARE_RATE_LIMIT_DELAY", "0.8"))),
        "use_curl_cffi": os.getenv("AKSHARE_USE_CURL_CFFI", "true").lower() not in ("0", "false", "no"),
    }

    stats = {}
    try:
        stats = get_network_stats()
    except Exception:
        pass

    return {
        "success": True,
        "data": {
            "config": env_config,
            "stats": stats,
            "note": "配置修改后需重启后端服务生效",
        }
    }


@router.post("/proxy-config")
async def update_proxy_config(req: ProxyConfigRequest):
    """更新 AKShare 代理配置（写入 .env 文件，需重启生效）"""
    import os
    from pathlib import Path

    env_path = Path(".env")
    if not env_path.exists():
        raise HTTPException(status_code=500, detail=".env 文件不存在")

    # 读取当前 .env
    with open(env_path, "r") as f:
        lines = f.readlines()

    # 配置映射: 请求字段 → 环境变量名
    mapping = {
        "proxy_mode": "AKSHARE_PROXY_MODE",
        "proxy_api_url": "AKSHARE_PROXY_API_URL",
        "static_proxies": "AKSHARE_PROXIES",
        "proxy_cache_seconds": "AKSHARE_PROXY_CACHE_SECONDS",
        "proxy_rounds": "AKSHARE_PROXY_ROUNDS",
        "include_direct": "AKSHARE_PROXY_INCLUDE_DIRECT",
        "request_timeout": "AKSHARE_REQUEST_TIMEOUT",
        "request_retries": "AKSHARE_REQUEST_RETRIES",
        "min_request_interval": "AKSHARE_MIN_REQUEST_INTERVAL",
        "use_curl_cffi": "AKSHARE_USE_CURL_CFFI",
    }

    updates = {}
    for field, env_key in mapping.items():
        val = getattr(req, field, None)
        if val is not None:
            updates[env_key] = "true" if isinstance(val, bool) and val else \
                               "false" if isinstance(val, bool) else str(val)

    if not updates:
        raise HTTPException(status_code=400, detail="未提供任何配置项")

    # 更新或追加
    updated_keys = set()
    new_lines = []
    for line in lines:
        stripped = line.strip()
        matched = False
        for env_key, new_val in updates.items():
            if stripped.startswith(f"{env_key}=") or stripped.startswith(f"#{env_key}="):
                new_lines.append(f"{env_key}={new_val}\n")
                updated_keys.add(env_key)
                matched = True
                break
        if not matched:
            new_lines.append(line)

    # 追加未更新的新配置
    for env_key, new_val in updates.items():
        if env_key not in updated_keys:
            new_lines.append(f"\n{env_key}={new_val}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)

    return {
        "success": True,
        "message": "代理配置已更新，重启后端服务后生效",
        "data": {"updated": list(updates.keys())},
    }


@router.post("/proxy-config/test")
async def test_proxy_connection():
    """测试当前代理配置的连通性"""
    import time
    import requests as req

    proxy_mode = os.getenv("AKSHARE_PROXY_MODE", "strong")
    proxy_api_url = os.getenv("AKSHARE_PROXY_API_URL", "").strip()

    results = {"proxy_mode": proxy_mode, "tests": []}

    if proxy_mode == "off":
        results["tests"].append({"type": "direct", "status": "info", "message": "代理已关闭，使用直连模式"})
        return {"success": True, "data": results}

    # 测试动态代理 API
    if proxy_mode == "strong" and proxy_api_url:
        start = time.time()
        try:
            resp = req.get(proxy_api_url, timeout=10)
            elapsed = time.time() - start
            ips = [l.strip() for l in resp.text.replace("\r", "").split("\n") if l.strip()]
            results["tests"].append({
                "type": "proxy_api",
                "status": "success",
                "message": f"获取到 {len(ips)} 个代理 IP",
                "elapsed": round(elapsed, 2),
                "sample": ips[:3],
            })
        except Exception as e:
            results["tests"].append({
                "type": "proxy_api",
                "status": "error",
                "message": f"代理 API 请求失败: {str(e)}",
                "elapsed": round(time.time() - start, 2),
            })
    elif proxy_mode == "strong" and not proxy_api_url:
        results["tests"].append({"type": "proxy_api", "status": "warning", "message": "未配置代理 API 地址"})

    # 测试静态代理
    static_proxies = os.getenv("AKSHARE_PROXIES", "").strip()
    if static_proxies:
        proxies = [p.strip() for p in static_proxies.replace("\n", ",").split(",") if p.strip()]
        results["tests"].append({"type": "static", "status": "info", "message": f"已配置 {len(proxies)} 个静态代理"})
    else:
        results["tests"].append({"type": "static", "status": "info", "message": "未配置静态代理"})

    # 测试 eastmoney 连通性
    start = time.time()
    try:
        resp = req.get("https://push2.eastmoney.com/api/qt/stock/get", timeout=10)
        elapsed = time.time() - start
        results["tests"].append({
            "type": "eastmoney",
            "status": "success" if resp.status_code == 200 else "warning",
            "message": f"东方财富 API 可访问 (HTTP {resp.status_code})",
            "elapsed": round(elapsed, 2),
        })
    except Exception as e:
        results["tests"].append({
            "type": "eastmoney",
            "status": "error",
            "message": f"东方财富 API 不可达: {str(e)}",
            "elapsed": round(time.time() - start, 2),
        })

    return {"success": True, "data": results}
