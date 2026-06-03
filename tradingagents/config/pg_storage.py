#!/usr/bin/env python3
"""
PostgreSQL 存储适配器
用于将 token 使用记录存储到 PostgreSQL 数据库
（从 mongodb_storage 迁移而来）
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Optional, Any
from dataclasses import asdict

from sqlalchemy import select, func, and_, desc, delete

from .usage_models import UsageRecord
from tradingagents.utils.logging_manager import get_logger
from tradingagents.config.runtime_settings import get_timezone_name
logger = get_logger('agents')


class PgStorage:
    """PostgreSQL 存储适配器"""

    def __init__(self, connection_uri: str = None, database_name: str = "tradingagents"):
        self._session = None
        self._session_factory = None
        self._connected = False
        self.database_name = database_name
        self.collection_name = "token_usage"

        try:
            self._connect()
        except Exception as e:
            logger.warning(f"⚠️ PG 存储初始化失败: {e}")
            self._connected = False

    def _connect(self):
        """连接到 PostgreSQL"""
        try:
            from app.core.database import sync_session_factory
            from sqlalchemy import text

            self._session_factory = sync_session_factory
            session = self._session_factory()
            try:
                session.execute(text("SELECT 1"))
                self._connected = True
                logger.info(f"✅ PostgreSQL 存储连接成功: {self.database_name}.{self.collection_name}")
            finally:
                session.close()
        except Exception as e:
            logger.error(f"❌ PostgreSQL 存储连接失败: {e}")
            logger.info(f"将使用本地JSON文件存储")
            self._connected = False

    def _get_session(self):
        """获取一个新的数据库会话"""
        if self._session_factory is None:
            self._connect()
        if self._session_factory:
            return self._session_factory()
        return None

    def is_connected(self) -> bool:
        """检查是否连接到 PostgreSQL"""
        return self._connected

    def save_usage_record(self, record: UsageRecord) -> bool:
        """保存单个使用记录到 PostgreSQL"""
        if not self._connected:
            logger.warning(f"⚠️ [PG存储] 未连接，无法保存记录")
            return False

        session = self._get_session()
        if session is None:
            return False

        try:
            from app.core.pg_models import TokenUsage

            record_dict = asdict(record)

            token_record = TokenUsage(
                provider=record_dict.get("provider"),
                model=record_dict.get("model_name", record_dict.get("model")),
                tokens_input=record_dict.get("input_tokens", 0),
                tokens_output=record_dict.get("output_tokens", 0),
                cost=record_dict.get("cost", 0.0),
                request_type=record_dict.get("analysis_type", "stock_analysis"),
                metadata_=record_dict,
                created_at=datetime.now(ZoneInfo(get_timezone_name())),
            )

            session.add(token_record)
            session.commit()

            logger.info(f"✅ [PG存储] 记录已保存: ID={token_record.id}, {record.provider}/{record.model_name}, ¥{record.cost:.4f}")
            return True

        except Exception as e:
            logger.error(f"❌ [PG存储] 保存记录失败: {e}")
            if session:
                session.rollback()
            return False
        finally:
            if session:
                session.close()

    def load_usage_records(self, limit: int = 10000, days: int = None) -> List[UsageRecord]:
        """从 PostgreSQL 加载使用记录"""
        if not self._connected:
            return []

        session = self._get_session()
        if session is None:
            return []

        try:
            from app.core.pg_models import TokenUsage

            conditions = []
            if days:
                cutoff_date = datetime.now(ZoneInfo(get_timezone_name())) - timedelta(days=days)
                conditions.append(TokenUsage.created_at >= cutoff_date)

            stmt = (
                select(TokenUsage)
                .where(*conditions)
                .order_by(desc(TokenUsage.created_at))
                .limit(limit)
            )
            result = session.execute(stmt)
            rows = result.scalars().all()

            records = []
            for row in rows:
                try:
                    meta = row.metadata_ or {}
                    record = UsageRecord(
                        timestamp=meta.get("timestamp", row.created_at.isoformat() if row.created_at else ""),
                        provider=row.provider or meta.get("provider", ""),
                        model_name=row.model or meta.get("model_name", ""),
                        input_tokens=row.tokens_input or meta.get("input_tokens", 0),
                        output_tokens=row.tokens_output or meta.get("output_tokens", 0),
                        cost=row.cost or meta.get("cost", 0.0),
                        currency=meta.get("currency", "CNY"),
                        session_id=meta.get("session_id", ""),
                        analysis_type=row.request_type or meta.get("analysis_type", "stock_analysis"),
                    )
                    records.append(record)
                except Exception as e:
                    logger.error(f"解析记录失败: {e}")
                    continue

            return records

        except Exception as e:
            logger.error(f"从 PG 加载记录失败: {e}")
            return []
        finally:
            session.close()

    def get_usage_statistics(self, days: int = 30) -> Dict[str, Any]:
        """从 PostgreSQL 获取使用统计"""
        if not self._connected:
            return {}

        session = self._get_session()
        if session is None:
            return {}

        try:
            from app.core.pg_models import TokenUsage

            cutoff_date = datetime.now() - timedelta(days=days)

            stmt = (
                select(
                    func.sum(TokenUsage.cost).label("total_cost"),
                    func.sum(TokenUsage.tokens_input).label("total_input_tokens"),
                    func.sum(TokenUsage.tokens_output).label("total_output_tokens"),
                    func.count(TokenUsage.id).label("total_requests"),
                )
                .where(TokenUsage.created_at >= cutoff_date)
            )
            result = session.execute(stmt)
            row = result.one()

            return {
                'period_days': days,
                'total_cost': round(row.total_cost or 0, 4),
                'total_input_tokens': row.total_input_tokens or 0,
                'total_output_tokens': row.total_output_tokens or 0,
                'total_requests': row.total_requests or 0,
            }

        except Exception as e:
            logger.error(f"获取 PG 统计失败: {e}")
            return {}
        finally:
            session.close()

    def get_provider_statistics(self, days: int = 30) -> Dict[str, Dict[str, Any]]:
        """按供应商获取统计信息"""
        if not self._connected:
            return {}

        session = self._get_session()
        if session is None:
            return {}

        try:
            from app.core.pg_models import TokenUsage

            cutoff_date = datetime.now() - timedelta(days=days)

            stmt = (
                select(
                    TokenUsage.provider,
                    func.sum(TokenUsage.cost).label("cost"),
                    func.sum(TokenUsage.tokens_input).label("input_tokens"),
                    func.sum(TokenUsage.tokens_output).label("output_tokens"),
                    func.count(TokenUsage.id).label("requests"),
                )
                .where(TokenUsage.created_at >= cutoff_date)
                .group_by(TokenUsage.provider)
            )
            result = session.execute(stmt)
            rows = result.all()

            provider_stats = {}
            for row in rows:
                provider = row.provider or "unknown"
                provider_stats[provider] = {
                    'cost': round(row.cost or 0, 4),
                    'input_tokens': row.input_tokens or 0,
                    'output_tokens': row.output_tokens or 0,
                    'requests': row.requests or 0,
                }

            return provider_stats

        except Exception as e:
            logger.error(f"获取供应商统计失败: {e}")
            return {}
        finally:
            session.close()

    def cleanup_old_records(self, days: int = 90) -> int:
        """清理旧记录"""
        if not self._connected:
            return 0

        session = self._get_session()
        if session is None:
            return 0

        try:
            from app.core.pg_models import TokenUsage

            cutoff_date = datetime.now() - timedelta(days=days)

            stmt = delete(TokenUsage).where(TokenUsage.created_at < cutoff_date)
            result = session.execute(stmt)
            session.commit()

            deleted_count = result.rowcount
            if deleted_count > 0:
                logger.info(f"清理了 {deleted_count} 条超过 {days} 天的记录")

            return deleted_count

        except Exception as e:
            logger.error(f"清理旧记录失败: {e}")
            if session:
                session.rollback()
            return 0
        finally:
            session.close()

    def close(self):
        """关闭 PostgreSQL 连接（无具体操作，会话按需管理）"""
        self._connected = False
        logger.info(f"PostgreSQL 存储连接已关闭")
