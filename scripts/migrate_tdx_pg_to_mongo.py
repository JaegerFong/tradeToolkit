#!/usr/bin/env python3
"""
TDX PostgreSQL → MongoDB 数据迁移脚本

将 tdx2db 已同步到 PostgreSQL 的日K/5分钟K数据迁移到 MongoDB。

用法:
  1. 配置环境变量 (.env 或命令行):
     TDX_PG_HOST=localhost
     TDX_PG_PORT=5432
     TDX_PG_DB=tdx_data
     TDX_PG_USER=postgres
     TDX_PG_PASSWORD=postgres

  2. 运行:
     python scripts/migrate_tdx_pg_to_mongo.py

  3. 选项:
     --dry-run       仅检查数据量, 不实际写入
     --daily-only    仅迁移日K
     --minute5-only  仅迁移5分钟K
     --batch-size N  批量大小 (默认 5000)
"""
import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    logger.error("需要 psycopg2: pip install psycopg2-binary")
    sys.exit(1)


def get_pg_connection():
    return psycopg2.connect(
        host=os.getenv("TDX_PG_HOST", "localhost"),
        port=int(os.getenv("TDX_PG_PORT", "5432")),
        dbname=os.getenv("TDX_PG_DB", "tdx_data"),
        user=os.getenv("TDX_PG_USER", "postgres"),
        password=os.getenv("TDX_PG_PASSWORD", "postgres"),
    )


def pg_count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return cur.fetchone()[0]


def migrate_daily(conn, mongo_collection, batch_size: int, dry_run: bool):
    """迁移日K数据"""
    total = pg_count(conn, "daily_data")
    logger.info(f"日K数据: PG 共 {total:,} 条")

    if dry_run:
        return {"total_pg": total, "migrated": 0}

    with conn.cursor(name="daily_cursor") as cur:
        cur.itersize = batch_size
        cur.execute(
            "SELECT code, date, open, high, low, close, volume, amount FROM daily_data ORDER BY code, date"
        )

        migrated = 0
        batch = []
        for row in cur:
            code, date, open_, high, low, close, volume, amount = row
            doc = {
                "code": str(code).zfill(6),
                "symbol": str(code).zfill(6),
                "date": date,
                "open": float(open_ or 0),
                "high": float(high or 0),
                "low": float(low or 0),
                "close": float(close or 0),
                "volume": float(volume or 0),
                "amount": float(amount or 0),
                "data_source": "tdx",
                "period": "daily",
                "migrated_at": datetime.utcnow(),
            }
            batch.append(doc)

            if len(batch) >= batch_size:
                _flush_batch(mongo_collection, batch, "daily")
                migrated += len(batch)
                batch = []
                if migrated % 50000 == 0:
                    logger.info(f"日K迁移进度: {migrated:,}/{total:,}")

        if batch:
            _flush_batch(mongo_collection, batch, "daily")
            migrated += len(batch)

        logger.info(f"日K迁移完成: {migrated:,} 条")
        return {"total_pg": total, "migrated": migrated}


def migrate_minute5(conn, mongo_collection, batch_size: int, dry_run: bool):
    """迁移5分钟K数据"""
    total = pg_count(conn, "minute5_data")
    logger.info(f"5分钟K数据: PG 共 {total:,} 条")

    if dry_run:
        return {"total_pg": total, "migrated": 0}

    with conn.cursor(name="minute5_cursor") as cur:
        cur.itersize = batch_size
        cur.execute(
            "SELECT code, datetime, open, high, low, close, volume, amount FROM minute5_data ORDER BY code, datetime"
        )

        migrated = 0
        batch = []
        for row in cur:
            code, dt, open_, high, low, close, volume, amount = row
            doc = {
                "code": str(code).zfill(6),
                "symbol": str(code).zfill(6),
                "date": dt,
                "open": float(open_ or 0),
                "high": float(high or 0),
                "low": float(low or 0),
                "close": float(close or 0),
                "volume": float(volume or 0),
                "amount": float(amount or 0),
                "data_source": "tdx",
                "period": "5min",
                "migrated_at": datetime.utcnow(),
            }
            batch.append(doc)

            if len(batch) >= batch_size:
                _flush_batch(mongo_collection, batch, "5min")
                migrated += len(batch)
                batch = []
                if migrated % 50000 == 0:
                    logger.info(f"5分钟K迁移进度: {migrated:,}/{total:,}")

        if batch:
            _flush_batch(mongo_collection, batch, "5min")
            migrated += len(batch)

        logger.info(f"5分钟K迁移完成: {migrated:,} 条")
        return {"total_pg": total, "migrated": migrated}


def _flush_batch(collection, batch, period):
    """批量写入 MongoDB (upsert)"""
    from pymongo import UpdateOne

    operations = []
    for doc in batch:
        operations.append(
            UpdateOne(
                {"code": doc["code"], "date": doc["date"], "data_source": "tdx", "period": period},
                {"$set": doc},
                upsert=True,
            )
        )
    if operations:
        collection.bulk_write(operations, ordered=False)


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="TDX PG → MongoDB 数据迁移")
    parser.add_argument("--dry-run", action="store_true", help="仅检查数据量")
    parser.add_argument("--daily-only", action="store_true", help="仅迁移日K")
    parser.add_argument("--minute5-only", action="store_true", help="仅迁移5分钟K")
    parser.add_argument("--batch-size", type=int, default=5000, help="批量大小")
    args = parser.parse_args()

    # 初始化 MongoDB
    from app.core.database import init_db
    await init_db()
    from app.core.database import get_mongo_db
    db = get_mongo_db()
    collection = db.stock_daily_quotes

    # 连接 PostgreSQL
    logger.info("连接 PostgreSQL...")
    conn = get_pg_connection()
    logger.info("PostgreSQL 连接成功")

    try:
        results = {}

        if not args.minute5_only:
            results["daily"] = migrate_daily(conn, collection, args.batch_size, args.dry_run)

        if not args.daily_only:
            results["minute5"] = migrate_minute5(conn, collection, args.batch_size, args.dry_run)

        # 汇总
        logger.info("=" * 50)
        logger.info("迁移汇总:")
        for k, v in results.items():
            logger.info(f"  {k}: PG={v['total_pg']:,}, 迁移={v['migrated']:,}")
        logger.info("=" * 50)

    finally:
        conn.close()
        logger.info("PostgreSQL 连接已关闭")


if __name__ == "__main__":
    asyncio.run(main())
