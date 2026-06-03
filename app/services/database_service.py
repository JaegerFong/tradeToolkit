"""
数据库管理服务
"""

import json
import os
import csv
import gzip
import shutil
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from sqlalchemy import text, inspect

from app.core.database import async_session_factory, get_redis_client
from app.core.config import settings

from app.services.database import status_checks as _db_status
from app.services.database import cleanup as _db_cleanup
from app.services.database import backups as _db_backups
from app.services.database.serialization import serialize_document as _serialize_doc

logger = logging.getLogger(__name__)


class DatabaseService:
    """数据库管理服务"""

    def __init__(self):
        self.backup_dir = os.path.join(settings.TRADINGAGENTS_DATA_DIR, "backups")
        self.export_dir = os.path.join(settings.TRADINGAGENTS_DATA_DIR, "exports")

        os.makedirs(self.backup_dir, exist_ok=True)
        os.makedirs(self.export_dir, exist_ok=True)

    async def get_database_status(self) -> Dict[str, Any]:
        """获取数据库连接状态（委托子模块）"""
        return await _db_status.get_database_status()

    async def _get_mongodb_status(self) -> Dict[str, Any]:
        """MongoDB已迁移，返回离线状态"""
        return await _db_status.get_mongodb_status()

    async def _get_redis_status(self) -> Dict[str, Any]:
        """获取Redis状态（委托子模块）"""
        return await _db_status.get_redis_status()

    async def get_database_stats(self) -> Dict[str, Any]:
        """获取数据库统计信息"""
        try:
            async with async_session_factory() as session:
                inspector = inspect(session.bind)
                tables = inspector.get_table_names(schema=settings.PG_APP_SCHEMA)

                collections_info = []
                total_size = 0

                for table_name in tables:
                    try:
                        result = await session.execute(
                            text(f"SELECT reltuples::bigint AS estimate FROM pg_class WHERE relname = :tname"),
                            {"tname": table_name}
                        )
                        row = result.fetchone()
                        doc_count = row[0] if row and row[0] else 0

                        size_result = await session.execute(
                            text(f"SELECT pg_total_relation_size(:schema || '.' || :tname)"),
                            {"schema": settings.PG_APP_SCHEMA, "tname": table_name}
                        )
                        size = size_result.scalar() or 0
                        total_size += size

                        index_result = await session.execute(
                            text("SELECT count(*) FROM pg_indexes WHERE schemaname = :schema AND tablename = :tname"),
                            {"schema": settings.PG_APP_SCHEMA, "tname": table_name}
                        )
                        index_count = index_result.scalar() or 0

                        collections_info.append({
                            "name": table_name,
                            "documents": doc_count,
                            "size": size,
                            "storage_size": size,
                            "indexes": index_count,
                            "index_size": 0,
                        })
                    except Exception as e:
                        logger.error(f"获取表 {table_name} 统计失败: {e}")
                        collections_info.append({
                            "name": table_name,
                            "documents": 0,
                            "size": 0,
                            "storage_size": 0,
                            "indexes": 0,
                            "index_size": 0,
                        })

                total_documents = sum(c["documents"] for c in collections_info)

                return {
                    "total_collections": len(tables),
                    "total_documents": total_documents,
                    "total_size": total_size,
                    "collections": collections_info,
                }
        except Exception as e:
            raise Exception(f"获取数据库统计失败: {str(e)}")

    async def test_connections(self) -> Dict[str, Any]:
        """测试数据库连接（委托子模块）"""
        return await _db_status.test_connections()

    async def _test_mongodb_connection(self) -> Dict[str, Any]:
        """MongoDB已迁移"""
        return await _db_status.test_mongodb_connection()

    async def _test_redis_connection(self) -> Dict[str, Any]:
        """测试Redis连接（委托子模块）"""
        return await _db_status.test_redis_connection()

    async def create_backup(self, name: str, collections: List[str] = None, user_id: str = None) -> Dict[str, Any]:
        """创建数据库备份"""
        if _db_backups._check_pg_dump_available():
            logger.info("使用 pg_dump 原生备份（推荐）")
            return await _db_backups.create_backup_native(
                name=name,
                backup_dir=self.backup_dir,
                collections=collections,
                user_id=user_id
            )
        else:
            logger.warning("pg_dump 不可用，使用 Python 备份（较慢）")
            return await _db_backups.create_backup(
                name=name,
                backup_dir=self.backup_dir,
                collections=collections,
                user_id=user_id
            )

    async def list_backups(self) -> List[Dict[str, Any]]:
        """获取备份列表（委托子模块）"""
        return await _db_backups.list_backups()

    async def delete_backup(self, backup_id: str) -> None:
        """删除备份（委托子模块）"""
        await _db_backups.delete_backup(backup_id)

    async def cleanup_old_data(self, days: int) -> Dict[str, Any]:
        """清理旧数据（委托子模块）"""
        return await _db_cleanup.cleanup_old_data(days)

    async def cleanup_analysis_results(self, days: int) -> Dict[str, Any]:
        """清理过期分析结果（委托子模块）"""
        return await _db_cleanup.cleanup_analysis_results(days)

    async def cleanup_operation_logs(self, days: int) -> Dict[str, Any]:
        """清理操作日志（委托子模块）"""
        return await _db_cleanup.cleanup_operation_logs(days)

    async def import_data(self, content: bytes, collection: str, format: str = "json",
                         overwrite: bool = False, filename: str = None) -> Dict[str, Any]:
        """导入数据（委托子模块）"""
        return await _db_backups.import_data(content, collection, format=format, overwrite=overwrite, filename=filename)

    async def export_data(self, collections: List[str] = None, format: str = "json", sanitize: bool = False) -> str:
        """导出数据（委托子模块）"""
        return await _db_backups.export_data(collections, export_dir=self.export_dir, format=format, sanitize=sanitize)

    def _serialize_document(self, doc: dict) -> dict:
        """序列化文档，处理特殊类型（委托子模块）"""
        return _serialize_doc(doc)
