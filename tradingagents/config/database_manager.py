#!/usr/bin/env python3
"""
智能数据库管理器
自动检测 PostgreSQL 和 Redis 可用性，提供降级方案
使用项目现有的 .env 配置
（从依赖 MongoDB 迁移到 PostgreSQL）
"""

import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple


class DatabaseManager:
    """智能数据库管理器"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

        # 加载.env配置
        self._load_env_config()

        # 数据库连接状态
        self.pg_available = False
        self.redis_available = False
        self._pg_session_factory = None
        self.redis_client = None

        # 检测数据库可用性
        self._detect_databases()

        # 初始化连接
        self._initialize_connections()

        self.logger.info(f"数据库管理器初始化完成 - PG: {self.pg_available}, Redis: {self.redis_available}")

    def _load_env_config(self):
        """从.env文件加载配置"""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            self.logger.info("python-dotenv未安装，直接读取环境变量")

        from .env_utils import parse_bool_env
        self.pg_enabled = parse_bool_env("PG_ENABLED", True)
        self.redis_enabled = parse_bool_env("REDIS_ENABLED", False)

        self.pg_config = {
            "enabled": self.pg_enabled,
            "uri": os.getenv("PG_URI", ""),
            "database": os.getenv("PG_DATABASE", "tradingagents"),
            "schema": os.getenv("PG_APP_SCHEMA", "tradetoolkit"),
        }

        self.redis_config = {
            "enabled": self.redis_enabled,
            "host": os.getenv("REDIS_HOST", "localhost"),
            "port": int(os.getenv("REDIS_PORT", "6379")),
            "password": os.getenv("REDIS_PASSWORD"),
            "db": int(os.getenv("REDIS_DB", "0")),
            "timeout": 2
        }

        self.logger.info(f"PG启用: {self.pg_enabled}")
        self.logger.info(f"Redis启用: {self.redis_enabled}")
        if self.pg_enabled:
            self.logger.info(f"PG配置: database={self.pg_config['database']}, schema={self.pg_config['schema']}")
        if self.redis_enabled:
            self.logger.info(f"Redis配置: {self.redis_config['host']}:{self.redis_config['port']}")

    def _detect_postgresql(self) -> Tuple[bool, str]:
        """检测 PostgreSQL 是否可用"""
        if not self.pg_enabled:
            return False, "PostgreSQL 未启用 (PG_ENABLED=false)"

        try:
            from app.core.database import sync_session_factory
            session = sync_session_factory()
            try:
                from sqlalchemy import text
                session.execute(text("SELECT 1"))
                return True, "PostgreSQL 连接成功"
            finally:
                session.close()
        except Exception as e:
            return False, f"PostgreSQL 连接失败: {str(e)}"

    def _detect_redis(self) -> Tuple[bool, str]:
        """检测Redis是否可用"""
        if not self.redis_enabled:
            return False, "Redis未启用 (REDIS_ENABLED=false)"

        try:
            import redis

            connect_kwargs = {
                "host": self.redis_config["host"],
                "port": self.redis_config["port"],
                "db": self.redis_config["db"],
                "socket_timeout": self.redis_config["timeout"],
                "socket_connect_timeout": self.redis_config["timeout"]
            }

            if self.redis_config["password"]:
                connect_kwargs["password"] = self.redis_config["password"]

            client = redis.Redis(**connect_kwargs)
            client.ping()

            return True, "Redis连接成功"

        except ImportError:
            return False, "redis未安装"
        except Exception as e:
            return False, f"Redis连接失败: {str(e)}"

    def _detect_databases(self):
        """检测所有数据库"""
        self.logger.info("开始检测数据库可用性...")

        pg_available, pg_msg = self._detect_postgresql()
        self.pg_available = pg_available

        if pg_available:
            self.logger.info(f"✅ PostgreSQL: {pg_msg}")
        else:
            self.logger.info(f"❌ PostgreSQL: {pg_msg}")

        redis_available, redis_msg = self._detect_redis()
        self.redis_available = redis_available

        if redis_available:
            self.logger.info(f"✅ Redis: {redis_msg}")
        else:
            self.logger.info(f"❌ Redis: {redis_msg}")

        self._update_config_based_on_detection()

    def _update_config_based_on_detection(self):
        """根据检测结果更新配置"""
        if self.redis_available:
            self.primary_backend = "redis"
        elif self.pg_available:
            self.primary_backend = "postgresql"
        else:
            self.primary_backend = "file"

        self.logger.info(f"主要缓存后端: {self.primary_backend}")

    def _initialize_connections(self):
        """初始化数据库连接"""
        if self.pg_available:
            try:
                from app.core.database import sync_session_factory
                self._pg_session_factory = sync_session_factory
                self.logger.info("PostgreSQL 会话工厂初始化成功")
            except Exception as e:
                self.logger.error(f"PostgreSQL 会话工厂初始化失败: {e}")
                self.pg_available = False

        if self.redis_available:
            try:
                import redis

                connect_kwargs = {
                    "host": self.redis_config["host"],
                    "port": self.redis_config["port"],
                    "db": self.redis_config["db"],
                    "socket_timeout": self.redis_config["timeout"]
                }

                if self.redis_config["password"]:
                    connect_kwargs["password"] = self.redis_config["password"]

                self.redis_client = redis.Redis(**connect_kwargs)
                self.logger.info("Redis客户端初始化成功")
            except Exception as e:
                self.logger.error(f"Redis客户端初始化失败: {e}")
                self.redis_available = False

    def get_pg_session(self):
        """获取 PG 同步会话"""
        if self.pg_available and self._pg_session_factory:
            return self._pg_session_factory()
        return None

    def get_redis_client(self):
        """获取Redis客户端"""
        if self.redis_available and self.redis_client:
            return self.redis_client
        return None

    def is_pg_available(self) -> bool:
        """检查 PostgreSQL 是否可用"""
        return self.pg_available

    # 向后兼容别名
    is_mongodb_available = is_pg_available
    mongodb_available = pg_available

    def get_mongodb_client(self):
        """获取 PG 会话（向后兼容，原 get_mongodb_client 的 PG 替代）"""
        return self.get_pg_session()

    @property
    def mongodb_db(self):
        """PG 兼容属性（向后兼容）- 返回自身用于直接访问 PG 模型"""
        return self

    def is_redis_available(self) -> bool:
        """检查Redis是否可用"""
        return self.redis_available

    def is_database_available(self) -> bool:
        """检查是否有任何数据库可用"""
        return self.pg_available or self.redis_available

    def get_cache_backend(self) -> str:
        """获取当前缓存后端"""
        return self.primary_backend

    def get_config(self) -> Dict[str, Any]:
        """获取配置信息"""
        return {
            "pg": self.pg_config,
            "redis": self.redis_config,
            "primary_backend": self.primary_backend,
            "pg_available": self.pg_available,
            "redis_available": self.redis_available,
            "mongodb_available": self.pg_available,  # 向后兼容
            "cache": {
                "primary_backend": self.primary_backend,
                "fallback_enabled": True,
                "ttl_settings": {
                    "us_stock_data": 7200,
                    "us_news": 21600,
                    "us_fundamentals": 86400,
                    "china_stock_data": 3600,
                    "china_news": 14400,
                    "china_fundamentals": 43200,
                }
            }
        }

    def get_status_report(self) -> Dict[str, Any]:
        """获取状态报告"""
        return {
            "database_available": self.is_database_available(),
            "postgresql": {
                "available": self.pg_available,
                "database": self.pg_config["database"],
                "schema": self.pg_config["schema"],
            },
            "mongodb": {
                "available": self.pg_available,  # 向后兼容
                "host": "N/A (migrated to PG)",
                "port": "N/A (migrated to PG)",
            },
            "redis": {
                "available": self.redis_available,
                "host": self.redis_config["host"],
                "port": self.redis_config["port"]
            },
            "cache_backend": self.get_cache_backend(),
            "fallback_enabled": True
        }

    def get_cache_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        stats = {
            "pg_available": self.pg_available,
            "mongodb_available": self.pg_available,  # 向后兼容
            "redis_available": self.redis_available,
            "redis_keys": 0,
            "redis_memory": "N/A"
        }

        if self.redis_available and self.redis_client:
            try:
                info = self.redis_client.info()
                stats["redis_keys"] = self.redis_client.dbsize()
                stats["redis_memory"] = info.get("used_memory_human", "N/A")
            except Exception as e:
                self.logger.error(f"获取Redis统计失败: {e}")

        return stats

    def cache_clear_pattern(self, pattern: str) -> int:
        """清理匹配模式的缓存"""
        cleared_count = 0

        if self.redis_available and self.redis_client:
            try:
                keys = self.redis_client.keys(pattern)
                if keys:
                    cleared_count += self.redis_client.delete(*keys)
            except Exception as e:
                self.logger.error(f"Redis缓存清理失败: {e}")

        return cleared_count


# 全局数据库管理器实例
_database_manager = None


def get_database_manager() -> DatabaseManager:
    """获取全局数据库管理器实例"""
    global _database_manager
    if _database_manager is None:
        _database_manager = DatabaseManager()
    return _database_manager


def is_pg_available() -> bool:
    """检查 PostgreSQL 是否可用"""
    return get_database_manager().is_pg_available()


# 向后兼容别名
def is_mongodb_available() -> bool:
    """检查数据库是否可用（向后兼容）"""
    return get_database_manager().is_pg_available()


def is_redis_available() -> bool:
    """检查Redis是否可用"""
    return get_database_manager().is_redis_available()


def get_cache_backend() -> str:
    """获取当前缓存后端"""
    return get_database_manager().get_cache_backend()


def get_mongodb_client():
    """获取 PG 会话（向后兼容，原 get_mongodb_client 的 PG 替代）"""
    return get_database_manager().get_pg_session()


def get_redis_client():
    """获取Redis客户端"""
    return get_database_manager().get_redis_client()
