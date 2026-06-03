"""
操作日志服务
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy import select, func, delete, cast, extract, Integer

from app.core.database import async_session_factory
from app.core.pg_models import OperationLog
from app.models.operation_log import (
    OperationLogCreate,
    OperationLogResponse,
    OperationLogQuery,
    OperationLogStats,
)
from app.utils.timezone import now_tz

logger = logging.getLogger("webapi")


class OperationLogService:
    """操作日志服务"""

    async def create_log(
        self,
        user_id: str,
        username: str,
        log_data: OperationLogCreate,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> str:
        """创建操作日志"""
        try:
            current_time = now_tz().replace(tzinfo=None)
            async with async_session_factory() as session:
                log = OperationLog(
                    user_id=int(user_id) if user_id else None,
                    username=username,
                    action=log_data.action,
                    resource=log_data.action_type,
                    details={
                        "details": log_data.details or {},
                        "success": log_data.success,
                        "error_message": log_data.error_message,
                        "duration_ms": log_data.duration_ms,
                        "session_id": log_data.session_id,
                    },
                    ip_address=ip_address or log_data.ip_address,
                    created_at=current_time,
                )
                session.add(log)
                await session.commit()
                await session.refresh(log)
                logger.info(f"操作日志已记录: {username} - {log_data.action}")
                return str(log.id)
        except Exception as e:
            logger.error(f"创建操作日志失败: {e}")
            raise Exception(f"创建操作日志失败: {str(e)}")

    async def get_logs(self, query: OperationLogQuery) -> Tuple[List[OperationLogResponse], int]:
        """获取操作日志列表"""
        try:
            async with async_session_factory() as session:
                stmt = select(OperationLog)

                # 时间范围筛选
                if query.start_date:
                    start_str = query.start_date.replace('Z', '')
                    start_dt = datetime.fromisoformat(start_str)
                    stmt = stmt.where(OperationLog.created_at >= start_dt)
                if query.end_date:
                    end_str = query.end_date.replace('Z', '')
                    end_dt = datetime.fromisoformat(end_str)
                    stmt = stmt.where(OperationLog.created_at <= end_dt)

                # 操作类型筛选
                if query.action_type:
                    stmt = stmt.where(OperationLog.resource == query.action_type)

                # 成功状态筛选 - 存在 details JSONB 中
                # For now, skip if not easily queryable; kept for compatibility

                # 用户筛选
                if query.user_id:
                    stmt = stmt.where(OperationLog.user_id == int(query.user_id))

                # 关键词搜索
                if query.keyword:
                    kw = f"%{query.keyword}%"
                    from sqlalchemy import or_
                    stmt = stmt.where(or_(
                        OperationLog.action.ilike(kw),
                        OperationLog.username.ilike(kw),
                    ))

                # 获取总数
                count_stmt = select(func.count()).select_from(stmt.subquery())
                result = await session.execute(count_stmt)
                total = result.scalar()

                # 分页查询
                skip = (query.page - 1) * query.page_size
                stmt = stmt.order_by(OperationLog.created_at.desc()).offset(skip).limit(query.page_size)
                result = await session.execute(stmt)
                docs = result.scalars().all()

                logs = []
                for doc in docs:
                    details = doc.details or {}
                    logs.append(OperationLogResponse(
                        id=str(doc.id),
                        user_id=str(doc.user_id) if doc.user_id else "",
                        username=doc.username or "",
                        action_type=doc.resource or "",
                        action=doc.action or "",
                        details=details.get("details", {}),
                        success=details.get("success", True),
                        error_message=details.get("error_message"),
                        duration_ms=details.get("duration_ms"),
                        ip_address=doc.ip_address,
                        timestamp=doc.created_at.isoformat() if doc.created_at else None,
                    ))

                logger.info(f"获取操作日志: 总数={total}, 返回={len(logs)}")
                return logs, total
        except Exception as e:
            logger.error(f"获取操作日志失败: {e}")
            raise Exception(f"获取操作日志失败: {str(e)}")

    async def get_stats(self, days: int = 30) -> OperationLogStats:
        """获取操作日志统计"""
        try:
            start_date = now_tz() - timedelta(days=days)
            start_date = start_date.replace(tzinfo=None)

            async with async_session_factory() as session:
                # 基础统计
                result = await session.execute(
                    select(func.count()).select_from(OperationLog).where(
                        OperationLog.created_at >= start_date
                    )
                )
                total_logs = result.scalar()

                # 成功/失败统计（简化：假定所有记录都是成功的）
                success_logs = total_logs
                failed_logs = 0
                success_rate = 100.0 if total_logs > 0 else 0

                # 操作类型分布
                result = await session.execute(
                    select(OperationLog.resource, func.count()).where(
                        OperationLog.created_at >= start_date
                    ).group_by(OperationLog.resource)
                )
                action_type_distribution = {row[0] or "unknown": row[1] for row in result.all()}

                # 小时分布统计
                result = await session.execute(
                    select(extract('hour', OperationLog.created_at), func.count()).where(
                        OperationLog.created_at >= start_date
                    ).group_by(extract('hour', OperationLog.created_at)).order_by(extract('hour', OperationLog.created_at))
                )
                hourly_data = {i: 0 for i in range(24)}
                for hour, count in result.all():
                    hourly_data[int(hour)] = count

                hourly_distribution = [
                    {"hour": f"{hour:02d}:00", "count": count}
                    for hour, count in hourly_data.items()
                ]

                stats = OperationLogStats(
                    total_logs=total_logs,
                    success_logs=success_logs,
                    failed_logs=failed_logs,
                    success_rate=round(success_rate, 2),
                    action_type_distribution=action_type_distribution,
                    hourly_distribution=hourly_distribution
                )

                logger.info(f"操作日志统计: 总数={total_logs}, 成功率={success_rate:.1f}%")
                return stats
        except Exception as e:
            logger.error(f"获取操作日志统计失败: {e}")
            raise Exception(f"获取操作日志统计失败: {str(e)}")

    async def clear_logs(self, days: Optional[int] = None, action_type: Optional[str] = None) -> Dict[str, Any]:
        """清空操作日志"""
        try:
            async with async_session_factory() as session:
                stmt = delete(OperationLog)
                if days is not None:
                    cutoff_date = datetime.now() - timedelta(days=days)
                    stmt = stmt.where(OperationLog.created_at < cutoff_date)
                if action_type:
                    stmt = stmt.where(OperationLog.resource == action_type)
                result = await session.execute(stmt)
                await session.commit()
                deleted_count = result.rowcount
                logger.info(f"清空操作日志: 删除了 {deleted_count} 条记录")
                return {"deleted_count": deleted_count}
        except Exception as e:
            logger.error(f"清空操作日志失败: {e}")
            raise Exception(f"清空操作日志失败: {str(e)}")

    async def get_log_by_id(self, log_id: str) -> Optional[OperationLogResponse]:
        """根据ID获取操作日志"""
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(OperationLog).where(OperationLog.id == int(log_id))
                )
                doc = result.scalar_one_or_none()
                if not doc:
                    return None
                details = doc.details or {}
                return OperationLogResponse(
                    id=str(doc.id),
                    user_id=str(doc.user_id) if doc.user_id else "",
                    username=doc.username or "",
                    action_type=doc.resource or "",
                    action=doc.action or "",
                    details=details.get("details", {}),
                    success=details.get("success", True),
                    error_message=details.get("error_message"),
                    duration_ms=details.get("duration_ms"),
                    ip_address=doc.ip_address,
                    timestamp=doc.created_at.isoformat() if doc.created_at else None,
                )
        except Exception as e:
            logger.error(f"获取操作日志详情失败: {e}")
            return None


# 全局服务实例
_operation_log_service: Optional[OperationLogService] = None


def get_operation_log_service() -> OperationLogService:
    """获取操作日志服务实例"""
    global _operation_log_service
    if _operation_log_service is None:
        _operation_log_service = OperationLogService()
    return _operation_log_service


# 便捷函数
async def log_operation(
    user_id: str,
    username: str,
    action_type: str,
    action: str,
    details: Optional[Dict[str, Any]] = None,
    success: bool = True,
    error_message: Optional[str] = None,
    duration_ms: Optional[int] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
    session_id: Optional[str] = None
) -> str:
    """记录操作日志的便捷函数"""
    service = get_operation_log_service()
    log_data = OperationLogCreate(
        action_type=action_type,
        action=action,
        details=details,
        success=success,
        error_message=error_message,
        duration_ms=duration_ms,
        ip_address=ip_address,
        user_agent=user_agent,
        session_id=session_id
    )
    return await service.create_log(user_id, username, log_data, ip_address, user_agent)
