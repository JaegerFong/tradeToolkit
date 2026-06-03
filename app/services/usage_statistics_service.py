"""
使用统计服务
管理模型使用记录和成本统计
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from collections import defaultdict

from sqlalchemy import select, func, delete

from app.core.database import async_session_factory
from app.core.pg_models import TokenUsage
from app.models.config import UsageRecord, UsageStatistics

logger = logging.getLogger("app.services.usage_statistics_service")


class UsageStatisticsService:
    """使用统计服务"""

    def __init__(self):
        pass

    async def add_usage_record(self, record: UsageRecord) -> bool:
        """添加使用记录"""
        try:
            async with async_session_factory() as session:
                usage = TokenUsage(
                    user_id=record.user_id,
                    provider=record.provider,
                    model=record.model_name,
                    tokens_input=record.input_tokens or 0,
                    tokens_output=record.output_tokens or 0,
                    cost=record.cost or 0.0,
                    request_type=record.request_type,
                    metadata_=record.model_dump(exclude={"id", "user_id", "provider", "model_name", "input_tokens", "output_tokens", "cost", "request_type", "timestamp"}),
                    created_at=record.timestamp or datetime.utcnow(),
                )
                session.add(usage)
                await session.commit()
            logger.info(f"添加使用记录成功: {record.provider}/{record.model_name}")
            return True
        except Exception as e:
            logger.error(f"添加使用记录失败: {e}")
            return False

    async def get_usage_records(
        self,
        provider: Optional[str] = None,
        model_name: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100
    ) -> List[UsageRecord]:
        """获取使用记录"""
        try:
            async with async_session_factory() as session:
                stmt = select(TokenUsage)
                if provider:
                    stmt = stmt.where(TokenUsage.provider == provider)
                if model_name:
                    stmt = stmt.where(TokenUsage.model == model_name)
                if start_date:
                    stmt = stmt.where(TokenUsage.created_at >= start_date)
                if end_date:
                    stmt = stmt.where(TokenUsage.created_at <= end_date)
                stmt = stmt.order_by(TokenUsage.created_at.desc()).limit(limit)
                result = await session.execute(stmt)
                docs = result.scalars().all()

                records = []
                for doc in docs:
                    records.append(UsageRecord(
                        id=str(doc.id),
                        user_id=doc.user_id,
                        provider=doc.provider,
                        model_name=doc.model,
                        input_tokens=doc.tokens_input,
                        output_tokens=doc.tokens_output,
                        cost=doc.cost,
                        request_type=doc.request_type,
                        timestamp=doc.created_at.isoformat() if doc.created_at else None,
                    ))
                logger.info(f"获取使用记录成功: {len(records)} 条")
                return records
        except Exception as e:
            logger.error(f"获取使用记录失败: {e}")
            return []

    async def get_usage_statistics(
        self,
        days: int = 7,
        provider: Optional[str] = None,
        model_name: Optional[str] = None
    ) -> UsageStatistics:
        """获取使用统计"""
        try:
            async with async_session_factory() as session:
                end_date = datetime.utcnow()
                start_date = end_date - timedelta(days=days)

                stmt = select(TokenUsage).where(
                    TokenUsage.created_at >= start_date,
                    TokenUsage.created_at <= end_date
                )
                if provider:
                    stmt = stmt.where(TokenUsage.provider == provider)
                if model_name:
                    stmt = stmt.where(TokenUsage.model == model_name)

                result = await session.execute(stmt)
                records = result.scalars().all()

                stats = UsageStatistics()
                stats.total_requests = len(records)

                cost_by_currency = defaultdict(float)

                by_provider = defaultdict(lambda: {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0,
                    "cost_by_currency": defaultdict(float)
                })
                by_model = defaultdict(lambda: {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0,
                    "cost_by_currency": defaultdict(float)
                })
                by_date = defaultdict(lambda: {
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0,
                    "cost_by_currency": defaultdict(float)
                })

                for record in records:
                    cost = record.cost or 0.0
                    currency = "CNY"
                    meta = record.metadata_ or {}
                    if meta and isinstance(meta, dict):
                        currency = meta.get("currency", "CNY")

                    stats.total_input_tokens += record.tokens_input or 0
                    stats.total_output_tokens += record.tokens_output or 0
                    stats.total_cost += cost
                    cost_by_currency[currency] += cost

                    provider_key = record.provider or "unknown"
                    by_provider[provider_key]["requests"] += 1
                    by_provider[provider_key]["input_tokens"] += record.tokens_input or 0
                    by_provider[provider_key]["output_tokens"] += record.tokens_output or 0
                    by_provider[provider_key]["cost"] += cost
                    by_provider[provider_key]["cost_by_currency"][currency] += cost

                    model_key = f"{record.provider or 'unknown'}/{record.model or 'unknown'}"
                    by_model[model_key]["requests"] += 1
                    by_model[model_key]["input_tokens"] += record.tokens_input or 0
                    by_model[model_key]["output_tokens"] += record.tokens_output or 0
                    by_model[model_key]["cost"] += cost
                    by_model[model_key]["cost_by_currency"][currency] += cost

                    if record.created_at:
                        date_key = record.created_at.strftime("%Y-%m-%d")
                        by_date[date_key]["requests"] += 1
                        by_date[date_key]["input_tokens"] += record.tokens_input or 0
                        by_date[date_key]["output_tokens"] += record.tokens_output or 0
                        by_date[date_key]["cost"] += cost
                        by_date[date_key]["cost_by_currency"][currency] += cost

                stats.cost_by_currency = dict(cost_by_currency)
                stats.by_provider = {k: {**v, "cost_by_currency": dict(v["cost_by_currency"])} for k, v in by_provider.items()}
                stats.by_model = {k: {**v, "cost_by_currency": dict(v["cost_by_currency"])} for k, v in by_model.items()}
                stats.by_date = {k: {**v, "cost_by_currency": dict(v["cost_by_currency"])} for k, v in by_date.items()}

                logger.info(f"获取使用统计成功: {stats.total_requests} 条记录")
                return stats
        except Exception as e:
            logger.error(f"获取使用统计失败: {e}")
            return UsageStatistics()

    async def get_cost_by_provider(self, days: int = 7) -> Dict[str, float]:
        stats = await self.get_usage_statistics(days=days)
        return {
            provider: data["cost"]
            for provider, data in stats.by_provider.items()
        }

    async def get_cost_by_model(self, days: int = 7) -> Dict[str, float]:
        stats = await self.get_usage_statistics(days=days)
        return {
            model: data["cost"]
            for model, data in stats.by_model.items()
        }

    async def get_daily_cost(self, days: int = 7) -> Dict[str, float]:
        stats = await self.get_usage_statistics(days=days)
        return {
            date: data["cost"]
            for date, data in stats.by_date.items()
        }

    async def delete_old_records(self, days: int = 90) -> int:
        """删除旧记录"""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            async with async_session_factory() as session:
                stmt = delete(TokenUsage).where(TokenUsage.created_at < cutoff_date)
                result = await session.execute(stmt)
                await session.commit()
                deleted_count = result.rowcount
                logger.info(f"删除旧记录成功: {deleted_count} 条")
                return deleted_count
        except Exception as e:
            logger.error(f"删除旧记录失败: {e}")
            return 0


# 创建全局实例
usage_statistics_service = UsageStatisticsService()
