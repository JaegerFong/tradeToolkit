"""
Database status and connection checks, extracted from DatabaseService.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict

from sqlalchemy import text

from app.core.database import async_session_factory, get_redis_client
from app.core.config import settings


async def get_mongodb_status() -> Dict[str, Any]:
    """MongoDB is no longer used; return placeholder."""
    return {
        "connected": False,
        "error": "MongoDB has been migrated to PostgreSQL",
        "host": "",
        "port": 0,
        "database": "",
        "database_identity": "",
    }


async def get_postgresql_status() -> Dict[str, Any]:
    """Check PostgreSQL connection status."""
    try:
        async with async_session_factory() as session:
            start = datetime.utcnow()
            await session.execute(text("SELECT 1"))
            took_ms = (datetime.utcnow() - start).total_seconds() * 1000
        return {
            "connected": True,
            "host": settings.PG_HOST if hasattr(settings, 'PG_HOST') else "localhost",
            "port": settings.PG_PORT if hasattr(settings, 'PG_PORT') else 5432,
            "database": settings.PG_DATABASE if hasattr(settings, 'PG_DATABASE') else "tradetoolkit",
            "database_identity": "PostgreSQL",
            "version": "N/A",
            "uptime": 0,
            "connections": {},
            "memory": {},
            "connected_at": datetime.utcnow().isoformat(),
            "response_time_ms": round(took_ms, 2),
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
            "host": "",
            "port": 0,
            "database": "",
            "database_identity": "PostgreSQL",
        }


async def get_redis_status() -> Dict[str, Any]:
    try:
        redis_client = get_redis_client()
        await redis_client.ping()
        info = await redis_client.info()
        return {
            "connected": True,
            "host": settings.REDIS_HOST,
            "port": settings.REDIS_PORT,
            "database": settings.REDIS_DB,
            "version": info.get("redis_version", "Unknown"),
            "uptime": info.get("uptime_in_seconds", 0),
            "memory_used": info.get("used_memory", 0),
            "memory_peak": info.get("used_memory_peak", 0),
            "connected_clients": info.get("connected_clients", 0),
            "total_commands": info.get("total_commands_processed", 0),
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
            "host": settings.REDIS_HOST,
            "port": settings.REDIS_PORT,
            "database": settings.REDIS_DB,
        }


async def get_database_status() -> Dict[str, Any]:
    postgresql_status = await get_postgresql_status()
    redis_status = await get_redis_status()
    return {"postgresql": postgresql_status, "redis": redis_status}


async def test_mongodb_connection() -> Dict[str, Any]:
    """MongoDB is no longer used."""
    return {"success": False, "error": "MongoDB has been migrated to PostgreSQL", "message": "MongoDB已迁移"}


async def test_postgresql_connection() -> Dict[str, Any]:
    try:
        async with async_session_factory() as session:
            start = datetime.utcnow()
            await session.execute(text("SELECT 1"))
            took_ms = (datetime.utcnow() - start).total_seconds() * 1000
        return {"success": True, "response_time_ms": round(took_ms, 2), "message": "PostgreSQL连接正常"}
    except Exception as e:
        return {"success": False, "error": str(e), "message": "PostgreSQL连接失败"}


async def test_redis_connection() -> Dict[str, Any]:
    try:
        redis_client = get_redis_client()
        start = datetime.utcnow()
        await redis_client.ping()
        took_ms = (datetime.utcnow() - start).total_seconds() * 1000
        return {"success": True, "response_time_ms": round(took_ms, 2), "message": "Redis连接正常"}
    except Exception as e:
        return {"success": False, "error": str(e), "message": "Redis连接失败"}


async def test_connections() -> Dict[str, Any]:
    postgresql = await test_postgresql_connection()
    redis = await test_redis_connection()
    return {"postgresql": postgresql, "redis": redis, "overall": postgresql["success"] and redis["success"]}
