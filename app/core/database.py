"""
数据库连接管理模块
PostgreSQL + SQLAlchemy 2.0 async 实现
支持连接池、健康检查和 Redis 集成
"""

import logging
from typing import Optional, AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from redis.asyncio import Redis, ConnectionPool

from .config import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    """数据库连接管理器"""

    def __init__(self):
        self.async_engine: Optional[AsyncEngine] = None
        self.async_session_factory: Optional[async_sessionmaker] = None
        self.sync_engine: Optional[object] = None
        self.sync_session_factory: Optional[sessionmaker] = None
        self.redis_client: Optional[Redis] = None
        self.redis_pool: Optional[ConnectionPool] = None
        self._pg_healthy = False
        self._redis_healthy = False

    async def init_postgres(self):
        """初始化 PostgreSQL 异步连接"""
        try:
            logger.info("🔄 正在初始化 PostgreSQL 连接...")

            self.async_engine = create_async_engine(
                settings.PG_URI,
                pool_size=settings.PG_MAX_CONNECTIONS,
                max_overflow=10,
                pool_recycle=settings.PG_POOL_RECYCLE,
                pool_pre_ping=False,
                echo=False,
                connect_args={"timeout": 15},
            )
            self.async_session_factory = async_sessionmaker(
                self.async_engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            async with self.async_engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            self._pg_healthy = True

            logger.info("✅ PostgreSQL 异步连接成功建立")
            logger.info(f"📊 数据库: {settings.PG_DATABASE} (schema: {settings.PG_APP_SCHEMA})")
            logger.info(f"🔗 连接池: {settings.PG_MIN_CONNECTIONS}-{settings.PG_MAX_CONNECTIONS}")

        except Exception as e:
            logger.error(f"❌ PostgreSQL 连接失败: {e}")
            self._pg_healthy = False
            raise

    def init_postgres_sync(self):
        """初始化 PostgreSQL 同步连接"""
        try:
            self.sync_engine = create_engine(
                settings.PG_SYNC_URI,
                pool_size=settings.PG_MIN_CONNECTIONS,
                max_overflow=5,
                pool_recycle=settings.PG_POOL_RECYCLE,
                pool_pre_ping=False,
                echo=False,
            )
            self.sync_session_factory = sessionmaker(
                self.sync_engine,
                expire_on_commit=False,
            )

            with self.sync_engine.connect() as conn:
                conn.execute(text("SELECT 1"))

            logger.info("✅ PostgreSQL 同步连接成功建立")
        except Exception as e:
            logger.error(f"❌ PostgreSQL 同步连接失败: {e}")
            raise

    async def init_redis(self):
        """初始化 Redis 连接"""
        try:
            logger.info("🔄 正在初始化 Redis 连接...")

            self.redis_pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                retry_on_timeout=settings.REDIS_RETRY_ON_TIMEOUT,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=10,
            )
            self.redis_client = Redis(connection_pool=self.redis_pool)
            await self.redis_client.ping()
            self._redis_healthy = True

            logger.info("✅ Redis 连接成功建立")

        except Exception as e:
            logger.error(f"❌ Redis 连接失败: {e}")
            self._redis_healthy = False
            raise

    async def close_connections(self):
        """关闭所有数据库连接"""
        logger.info("🔄 正在关闭数据库连接...")
        if self.async_engine:
            await self.async_engine.dispose()
            self._pg_healthy = False
            logger.info("✅ PostgreSQL 异步连接已关闭")
        if self.sync_engine:
            self.sync_engine.dispose()
            logger.info("✅ PostgreSQL 同步连接已关闭")
        if self.redis_client:
            await self.redis_client.close()
            self._redis_healthy = False
            logger.info("✅ Redis 连接已关闭")
        if self.redis_pool:
            await self.redis_pool.disconnect()
            logger.info("✅ Redis 连接池已关闭")

    async def health_check(self) -> dict:
        health_status = {
            "postgresql": {"status": "unknown", "details": None},
            "redis": {"status": "unknown", "details": None},
        }
        try:
            if self.async_engine:
                async with self.async_engine.connect() as conn:
                    result = await conn.execute(text("SELECT version()"))
                    version = result.scalar()
                health_status["postgresql"] = {"status": "healthy", "details": {"version": version, "database": settings.PG_DATABASE}}
                self._pg_healthy = True
            else:
                health_status["postgresql"]["status"] = "disconnected"
        except Exception as e:
            health_status["postgresql"] = {"status": "unhealthy", "details": {"error": str(e)}}
            self._pg_healthy = False

        try:
            if self.redis_client:
                result = await self.redis_client.ping()
                health_status["redis"] = {"status": "healthy", "details": {"ping": result}}
                self._redis_healthy = True
            else:
                health_status["redis"]["status"] = "disconnected"
        except Exception as e:
            health_status["redis"] = {"status": "unhealthy", "details": {"error": str(e)}}
            self._redis_healthy = False

        return health_status

    @property
    def is_healthy(self) -> bool:
        return self._pg_healthy and self._redis_healthy


# 全局数据库管理器实例
db_manager = DatabaseManager()
redis_client: Optional[Redis] = None
redis_pool: Optional[ConnectionPool] = None


async def init_database():
    """初始化数据库连接"""
    try:
        await db_manager.init_postgres()
        db_manager.init_postgres_sync()

        try:
            await db_manager.init_redis()
            global redis_client, redis_pool
            redis_client = db_manager.redis_client
            redis_pool = db_manager.redis_pool
            logger.info("🎉 PostgreSQL + Redis 连接初始化完成")
        except Exception as e:
            logger.warning(f"⚠️ Redis 初始化失败，将以无 Redis 模式继续运行: {e}")
            logger.info("🎉 PostgreSQL 连接初始化完成（Redis 未启用）")

        # 初始化 app schema 和表
        await init_app_schema_and_tables()

    except Exception as e:
        logger.error(f"💥 数据库初始化失败: {e}")
        raise


async def init_app_schema_and_tables():
    """初始化 app schema 和数据库表/视图"""
    try:
        from app.core.pg_models import Base

        async with db_manager.async_engine.connect() as conn:
            await conn.execute(
                text(f"CREATE SCHEMA IF NOT EXISTS {settings.PG_APP_SCHEMA}")
            )
            await conn.commit()

        async with db_manager.async_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        logger.info(f"✅ App schema '{settings.PG_APP_SCHEMA}' 及表初始化完成")

    except Exception as e:
        logger.warning(f"⚠️ Schema/表初始化失败: {e}")


async def close_database():
    """关闭数据库连接"""
    global redis_client, redis_pool
    await db_manager.close_connections()
    redis_client = None
    redis_pool = None


def get_redis_client() -> Redis:
    """获取 Redis 客户端"""
    if db_manager.redis_client is None:
        raise RuntimeError("Redis 客户端未初始化")
    return db_manager.redis_client


async def get_database_health() -> dict:
    """获取数据库健康状态"""
    return await db_manager.health_check()


def get_database():
    """获取数据库管理器实例"""
    return db_manager


# 兼容性别名
init_db = init_database
close_db = close_database


class _LazySessionFactory:
    """延迟解析 Session Factory —— 解决 Python import 时序问题。

    当其他模块通过 `from app.core.database import async_session_factory` 导入时，
    Python 会在 import 时捕获此包装器实例。后续调用 `.f()` 时动态解析到实际 factory。
    """
    def __init__(self, manager: DatabaseManager, attr: str):
        self._manager = manager
        self._attr = attr

    def __call__(self, *args, **kwargs):
        factory = getattr(self._manager, self._attr, None)
        if factory is None:
            raise RuntimeError(
                f"PostgreSQL {self._attr} 未初始化，请先调用 init_db()")
        return factory(*args, **kwargs)

    @property
    def f(self):
        """显式获取底层 factory"""
        factory = getattr(self._manager, self._attr, None)
        if factory is None:
            raise RuntimeError(
                f"PostgreSQL {self._attr} 未初始化，请先调用 init_db()")
        return factory


# 模块级变量 —— 导入时捕获 LazySessionFactory 包装器，运行时动态解析
async_session_factory = _LazySessionFactory(db_manager, "async_session_factory")
sync_session_factory = _LazySessionFactory(db_manager, "sync_session_factory")
