"""
Backup, import, and export routines for PostgreSQL.
"""
from __future__ import annotations

import json
import os
import gzip
import asyncio
import subprocess
import shutil
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

from sqlalchemy import select, delete, text, inspect

from app.core.database import async_session_factory
from app.core.config import settings
from app.core.pg_models import DatabaseBackup, Base

logger = logging.getLogger(__name__)


def _check_pg_dump_available() -> bool:
    """检查 pg_dump 命令是否可用"""
    return shutil.which("pg_dump") is not None


async def create_backup_native(name: str, backup_dir: str, collections: Optional[List[str]] = None, user_id: str | None = None) -> Dict[str, Any]:
    """
    使用 PostgreSQL pg_dump 创建备份（推荐，速度快）
    """
    if not _check_pg_dump_available():
        raise Exception("pg_dump 命令不可用，请安装 PostgreSQL 客户端工具")

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_dirname = f"backup_{name}_{timestamp}"
    backup_path = os.path.join(backup_dir, backup_dirname)
    os.makedirs(backup_dir, exist_ok=True)

    pg_host = getattr(settings, 'PG_HOST', 'localhost')
    pg_port = str(getattr(settings, 'PG_PORT', 5432))
    pg_db = getattr(settings, 'PG_DATABASE', 'tradetoolkit')
    pg_user = getattr(settings, 'PG_USER', 'postgres')

    cmd = [
        "pg_dump",
        "-h", pg_host,
        "-p", pg_port,
        "-U", pg_user,
        "-d", pg_db,
        "-F", "c",  # custom format
        "-f", os.path.join(backup_path, "dump.sqlc"),
        "--no-password",
    ]

    # 如果指定了表，只备份这些表
    if collections:
        for table_name in collections:
            cmd.extend(["-t", f"{settings.PG_APP_SCHEMA}.{table_name}"])

    env = os.environ.copy()
    env["PGPASSWORD"] = getattr(settings, 'PG_PASSWORD', '')

    logger.info(f"开始执行 pg_dump 备份: {name}")

    def _run_pg_dump():
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            env=env,
        )
        if result.returncode != 0:
            raise Exception(f"pg_dump 执行失败: {result.stderr}")
        return result

    try:
        await asyncio.to_thread(_run_pg_dump)
        logger.info(f"pg_dump 备份完成: {name}")
    except subprocess.TimeoutExpired:
        raise Exception("备份超时（超过1小时）")
    except Exception as e:
        logger.error(f"pg_dump 备份失败: {e}")
        if os.path.exists(backup_path):
            await asyncio.to_thread(shutil.rmtree, backup_path)
        raise

    def _get_dir_size(path):
        total = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                total += os.path.getsize(filepath)
        return total

    file_size = await asyncio.to_thread(_get_dir_size, backup_path)

    if not collections:
        collections = await _get_all_tables()

    async with async_session_factory() as session:
        backup_record = DatabaseBackup(
            filename=backup_dirname,
            size_bytes=file_size,
            status="completed",
            created_at=datetime.utcnow(),
        )
        session.add(backup_record)
        await session.commit()
        backup_id = backup_record.id

    return {
        "id": str(backup_id),
        "name": name,
        "filename": backup_dirname,
        "file_path": backup_path,
        "size": file_size,
        "collections": collections,
        "created_at": datetime.utcnow().isoformat(),
        "backup_type": "pg_dump",
    }


async def _get_all_tables() -> List[str]:
    """获取所有app schema的表名"""
    async with async_session_factory() as session:
        inspector = inspect(session.bind)
        return inspector.get_table_names(schema=settings.PG_APP_SCHEMA)


async def create_backup(name: str, backup_dir: str, collections: Optional[List[str]] = None, user_id: str | None = None) -> Dict[str, Any]:
    """
    创建数据库备份（Python SQL dump 实现）
    """
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{name}_{timestamp}.json.gz"
    backup_path = os.path.join(backup_dir, backup_filename)
    os.makedirs(backup_dir, exist_ok=True)

    if not collections:
        collections = await _get_all_tables()

    backup_data: Dict[str, Any] = {
        "backup_name": name,
        "created_at": datetime.utcnow().isoformat(),
        "collections": collections,
        "data": {},
    }

    async with async_session_factory() as session:
        for table_name in collections:
            try:
                result = await session.execute(text(f"SELECT row_to_json(t) FROM {settings.PG_APP_SCHEMA}.{table_name} t"))
                rows = [row[0] for row in result.all()]
                backup_data["data"][table_name] = rows
            except Exception as e:
                logger.warning(f"导出表 {table_name} 失败: {e}")
                backup_data["data"][table_name] = []

    def _write_backup():
        with gzip.open(backup_path, "wt", encoding="utf-8") as f:
            json.dump(backup_data, f, ensure_ascii=False, indent=2)
        return os.path.getsize(backup_path)

    file_size = await asyncio.to_thread(_write_backup)

    async with async_session_factory() as session:
        backup_record = DatabaseBackup(
            filename=backup_filename,
            size_bytes=file_size,
            status="completed",
            created_at=datetime.utcnow(),
        )
        session.add(backup_record)
        await session.commit()
        backup_id = backup_record.id

    return {
        "id": str(backup_id),
        "name": name,
        "filename": backup_filename,
        "file_path": backup_path,
        "size": file_size,
        "collections": collections,
        "created_at": datetime.utcnow().isoformat(),
    }


async def list_backups() -> List[Dict[str, Any]]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatabaseBackup).order_by(DatabaseBackup.created_at.desc())
        )
        backups = result.scalars().all()
        return [
            {
                "id": str(b.id),
                "name": b.filename,
                "filename": b.filename,
                "size": b.size_bytes,
                "collections": [],
                "created_at": b.created_at.isoformat() if b.created_at else None,
                "created_by": "",
            }
            for b in backups
        ]


async def delete_backup(backup_id: str) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(DatabaseBackup).where(DatabaseBackup.id == int(backup_id))
        )
        backup = result.scalar_one_or_none()
        if not backup:
            raise Exception("备份不存在")
        await session.delete(backup)
        await session.commit()


async def import_data(content: bytes, collection: str, *, format: str = "json", overwrite: bool = False, filename: str | None = None) -> Dict[str, Any]:
    """导入数据到数据库"""
    if format.lower() == "json":
        def _parse_json():
            return json.loads(content.decode("utf-8"))
        data = await asyncio.to_thread(_parse_json)
    else:
        raise Exception(f"不支持的格式: {format}")

    async with async_session_factory() as session:
        # 多集合模式
        if isinstance(data, dict) and "data" in data:
            actual_data = data.get("data", {})
            total_inserted = 0
            imported_tables = []

            for table_name, documents in actual_data.items():
                if not documents:
                    continue

                if overwrite:
                    await session.execute(text(f"DELETE FROM {settings.PG_APP_SCHEMA}.{table_name}"))

                for doc in documents:
                    if isinstance(doc, dict):
                        doc.pop("_id", None)
                        try:
                            columns = ", ".join(doc.keys())
                            placeholders = ", ".join([f":{k}" for k in doc.keys()])
                            await session.execute(
                                text(f"INSERT INTO {settings.PG_APP_SCHEMA}.{table_name} ({columns}) VALUES ({placeholders})"),
                                doc
                            )
                            total_inserted += 1
                        except Exception as e:
                            logger.warning(f"导入表 {table_name} 失败: {e}")

                imported_tables.append(table_name)

            await session.commit()

            return {
                "mode": "multi_collection",
                "collections": imported_tables,
                "total_collections": len(imported_tables),
                "total_inserted": total_inserted,
                "filename": filename,
                "format": format,
                "overwrite": overwrite,
            }
        else:
            # 单集合模式
            documents = data if isinstance(data, list) else [data]

            if overwrite:
                await session.execute(text(f"DELETE FROM {settings.PG_APP_SCHEMA}.{collection}"))

            inserted_count = 0
            for doc in documents:
                if isinstance(doc, dict):
                    doc.pop("_id", None)
                    try:
                        columns = ", ".join(doc.keys())
                        placeholders = ", ".join([f":{k}" for k in doc.keys()])
                        await session.execute(
                            text(f"INSERT INTO {settings.PG_APP_SCHEMA}.{collection} ({columns}) VALUES ({placeholders})"),
                            doc
                        )
                        inserted_count += 1
                    except Exception as e:
                        logger.warning(f"导入失败: {e}")

            await session.commit()

            return {
                "mode": "single_collection",
                "collection": collection,
                "inserted_count": inserted_count,
                "filename": filename,
                "format": format,
                "overwrite": overwrite,
            }


async def export_data(collections: Optional[List[str]] = None, *, export_dir: str, format: str = "json", sanitize: bool = False) -> str:
    import pandas as pd

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    os.makedirs(export_dir, exist_ok=True)

    if not collections:
        collections = await _get_all_tables()

    all_data: Dict[str, List[dict]] = {}
    async with async_session_factory() as session:
        for table_name in collections:
            if sanitize and table_name == "users":
                all_data[table_name] = []
                continue

            try:
                result = await session.execute(text(f"SELECT row_to_json(t) FROM {settings.PG_APP_SCHEMA}.{table_name} t"))
                rows = [row[0] for row in result.all()]
                all_data[table_name] = rows
            except Exception as e:
                logger.warning(f"导出表 {table_name} 失败: {e}")
                all_data[table_name] = []

    if format.lower() == "json":
        filename = f"export_{timestamp}.json"
        file_path = os.path.join(export_dir, filename)
        export_data_dict = {
            "export_info": {
                "created_at": datetime.utcnow().isoformat(),
                "collections": collections,
                "format": format,
            },
            "data": all_data,
        }

        def _write_json():
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(export_data_dict, f, ensure_ascii=False, indent=2)

        await asyncio.to_thread(_write_json)
        return file_path

    if format.lower() == "csv":
        filename = f"export_{timestamp}.csv"
        file_path = os.path.join(export_dir, filename)
        rows: List[dict] = []
        for tname, documents in all_data.items():
            for doc in documents:
                if isinstance(doc, dict):
                    row = {**doc}
                    row["_collection"] = tname
                    rows.append(row)

        def _write_csv():
            if rows:
                pd.DataFrame(rows).to_csv(file_path, index=False, encoding="utf-8-sig")
            else:
                pd.DataFrame().to_csv(file_path, index=False, encoding="utf-8-sig")

        await asyncio.to_thread(_write_csv)
        return file_path

    if format.lower() in ["xlsx", "excel"]:
        filename = f"export_{timestamp}.xlsx"
        file_path = os.path.join(export_dir, filename)

        def _write_excel():
            with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
                for tname, documents in all_data.items():
                    df = pd.DataFrame(documents) if documents else pd.DataFrame()
                    sheet = tname[:31]
                    df.to_excel(writer, sheet_name=sheet, index=False)

        await asyncio.to_thread(_write_excel)
        return file_path

    raise Exception(f"不支持的导出格式: {format}")
