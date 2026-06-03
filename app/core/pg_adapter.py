"""
PG 兼容适配层
提供 MongoDB 风格的数据库访问接口，底层使用 SQLAlchemy + PostgreSQL
用于快速迁移存量 MongoDB 代码
"""

import logging
from datetime import datetime, timezone
from sqlalchemy import select, func, update as sa_update, delete, and_, or_, desc
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.database import async_session_factory
from app.core.pg_models import (
    StockBasicInfo, MarketQuotes, StockFinancialData, StockNewsData,
    User as UserModel, UserFavorite,
    LLMProvider as LLMProviderModel, ModelCatalog as ModelCatalogModel,
    SystemConfig as SystemConfigModel, OperationLog,
    AnalysisTask, AnalysisBatch, PatternScreeningTask,
    StrategyDefinition, StrategyRun, StrategyPool, StrategyBacktest,
    TokenUsage, Notification, StockTag,
    QuotesIngestionStatus, SyncStatus, MarketCategory, DataSourceGrouping, DatabaseBackup,
    # tdx2db tables (read-only)
    DailyData, Minute5Data, Minute15Data, Minute30Data, Minute60Data, StockInfo,
)

logger = logging.getLogger(__name__)

COLLECTION_MODEL_MAP = {
    "stock_basic_info": StockBasicInfo,
    "market_quotes": MarketQuotes,
    "stock_financial_data": StockFinancialData,
    "stock_news_data": StockNewsData,
    "users": UserModel,
    "user_favorites": UserFavorite,
    "favorites": UserFavorite,
    "llm_providers": LLMProviderModel,
    "model_catalog": ModelCatalogModel,
    "system_configs": SystemConfigModel,
    "operation_logs": OperationLog,
    "analysis_tasks": AnalysisTask,
    "analysis_batches": AnalysisBatch,
    "analysis_reports": AnalysisBatch,  # 兼容旧字段名
    "pattern_screening_tasks": PatternScreeningTask,
    "strategy_definitions": StrategyDefinition,
    "strategy_runs": StrategyRun,
    "strategy_pool": StrategyPool,
    "strategy_backtests": StrategyBacktest,
    "token_usage": TokenUsage,
    "notifications": Notification,
    "stock_tags": StockTag,
    "quotes_ingestion_status": QuotesIngestionStatus,
    "sync_status": SyncStatus,
    "market_categories": MarketCategory,
    "data_source_groupings": DataSourceGrouping,
    "db_backups": DatabaseBackup,
    # tdx2db public tables (只读)
    "daily_data": DailyData,
    "minute5_data": Minute5Data,
    "minute15_data": Minute15Data,
    "minute30_data": Minute30Data,
    "minute60_data": Minute60Data,
    "stock_info": StockInfo,
    "stock_daily_quotes": DailyData,  # 兼容旧集合名
    "stock_daily_kline": DailyData,    # 兼容旧集合名
}


def _row_to_dict(obj):
    """ORM 对象转 dict"""
    if obj is None:
        return None
    if hasattr(obj, 'model_dump'):
        return obj.model_dump()
    if hasattr(obj, '__dict__'):
        d = {k: v for k, v in obj.__dict__.items()
             if not k.startswith('_sa_')}
        d.pop('_sa_instance_state', None)
        return d
    if isinstance(obj, dict):
        return obj
    return dict(obj)


# ---- Result wrappers ----
class _InsertOneResult:
    def __init__(self, inserted_id): self.inserted_id = inserted_id

class _InsertManyResult:
    def __init__(self, inserted_ids): self.inserted_ids = inserted_ids

class _UpdateResult:
    def __init__(self, modified_count=0, upserted_id=None):
        self.modified_count = modified_count
        self.upserted_id = upserted_id

class _DeleteResult:
    def __init__(self, deleted_count=0): self.deleted_count = deleted_count


class _AsyncCursor:
    """MongoDB Cursor 兼容包装"""
    def __init__(self, stmt, session_factory):
        self._stmt = stmt
        self._sf = session_factory
        self._sort_exprs = []
        self._skip_val = 0
        self._limit_val = None

    def sort(self, field, direction=1):
        self._sort_exprs.append((field, direction))
        return self

    def skip(self, n):
        self._skip_val = n
        return self

    def limit(self, n):
        self._limit_val = n
        return self

    async def to_list(self, length=None):
        limit = length if length is not None else self._limit_val
        async with self._sf() as session:
            stmt = self._stmt
            for field_spec, direction in self._sort_exprs:
                if isinstance(field_spec, str):
                    col = getattr(stmt.selected_columns, field_spec, field_spec)
                else:
                    col = field_spec
                try:
                    stmt = stmt.order_by(col.desc() if direction < 0 else col.asc()) if hasattr(col, 'desc') else stmt.order_by(
                        getattr(self._get_model_col(session), str(field_spec)).desc() if direction < 0
                        else getattr(self._get_model_col(session), str(field_spec)).asc()
                    )
                except Exception:
                    pass  # best effort sort
            if self._skip_val:
                stmt = stmt.offset(self._skip_val)
            if limit is not None:
                stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_row_to_dict(r) for r in rows]


class PGCollection:
    """MongoDB Collection 兼容包装"""
    def __init__(self, session_factory, model_class):
        self._sf = session_factory
        self._model = model_class

    def find(self, query=None, projection=None):
        stmt = select(self._model)
        if query:
            stmt = self._apply_query(stmt, query)
        return _AsyncCursor(stmt, self._sf)

    async def find_one(self, query=None, projection=None, sort=None):
        async with self._sf() as session:
            stmt = select(self._model)
            if query:
                stmt = self._apply_query(stmt, query)
            if sort:
                for spec in sort:
                    if isinstance(spec, (list, tuple)):
                        f, d = spec
                        col = getattr(self._model, f)
                        stmt = stmt.order_by(col.desc() if d < 0 else col.asc())
                    else:
                        stmt = stmt.order_by(getattr(self._model, spec))
            stmt = stmt.limit(1)
            result = await session.execute(stmt)
            return _row_to_dict(result.scalar_one_or_none())

    def _apply_query(self, stmt, query):
        for key, value in query.items():
            if key == "$or":
                conds = []
                for q in value:
                    c = and_(True)
                    for k, v in q.items():
                        c = self._build_cond(c, k, v)
                    conds.append(c)
                stmt = stmt.where(or_(*conds))
            elif key == "$and":
                for q in value:
                    for k, v in q.items():
                        stmt = self._build_cond(stmt, k, v)
            else:
                stmt = self._build_cond(stmt, key, value)
        return stmt

    def _build_cond(self, stmt, key, value):
        col = getattr(self._model, key, None)
        if col is None:
            return stmt.where(and_(False))
        if isinstance(value, dict):
            for op, val in value.items():
                if op == "$regex":
                    stmt = stmt.where(col.ilike(f"%{val}%"))
                elif op == "$ne":
                    stmt = stmt.where(col != val)
                elif op == "$in":
                    stmt = stmt.where(col.in_(val))
                elif op == "$gte":
                    stmt = stmt.where(col >= val)
                elif op == "$lte":
                    stmt = stmt.where(col <= val)
                elif op == "$gt":
                    stmt = stmt.where(col > val)
                elif op == "$lt":
                    stmt = stmt.where(col < val)
            return stmt
        elif isinstance(value, list):
            return stmt.where(col.in_(value))
        else:
            return stmt.where(col == value)

    async def insert_one(self, document):
        async with self._sf() as session:
            doc = {k: v for k, v in document.items() if k != '_id'}
            instance = self._model(**doc)
            session.add(instance)
            await session.commit()
            await session.refresh(instance)
            return _InsertOneResult(instance.id)

    async def insert_many(self, documents):
        ids = []
        async with self._sf() as session:
            for doc in documents:
                d = {k: v for k, v in doc.items() if k != '_id'}
                instance = self._model(**d)
                session.add(instance)
                await session.flush()
                ids.append(instance.id)
            await session.commit()
        return _InsertManyResult(ids)

    async def update_one(self, filter_dict, update_dict, upsert=False):
        async with self._sf() as session:
            set_values = update_dict.get("$set", update_dict)
            set_values.pop("_id", None)
            set_values.pop("id", None)

            if upsert:
                stmt = pg_insert(self._model).values(**self._filter_to_values(filter_dict), **set_values)
                pk_cols = [c.name for c in self._model.__table__.primary_key.columns]
                stmt = stmt.on_conflict_do_update(
                    index_elements=pk_cols[:1] or ["id"],
                    set_=set_values,
                )
                result = await session.execute(stmt)
                await session.commit()
                up_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
                return _UpdateResult(modified_count=result.rowcount, upserted_id=up_id)

            stmt = sa_update(self._model).values(**set_values)
            stmt = self._apply_filter(stmt, filter_dict)
            result = await session.execute(stmt)
            await session.commit()
            return _UpdateResult(modified_count=result.rowcount)

    def _filter_to_values(self, filter_dict):
        vals = {}
        for k, v in filter_dict.items():
            if not isinstance(v, dict):
                vals[k] = v
        return vals

    def _apply_filter(self, stmt, filter_dict):
        for k, v in filter_dict.items():
            if k in ("$or", "$and"):
                continue
            col = getattr(self._model, k, None)
            if col is None:
                continue
            if isinstance(v, dict):
                for op, val in v.items():
                    if op == "$ne":
                        stmt = stmt.where(col != val)
                    elif op == "$in":
                        stmt = stmt.where(col.in_(val))
                    elif op == "$gte":
                        stmt = stmt.where(col >= val)
                    elif op == "$lte":
                        stmt = stmt.where(col <= val)
                    elif op == "$gt":
                        stmt = stmt.where(col > val)
                    elif op == "$lt":
                        stmt = stmt.where(col < val)
            else:
                stmt = stmt.where(col == v)
        return stmt

    async def update_many(self, filter_dict, update_dict, upsert=False):
        return await self.update_one(filter_dict, update_dict, upsert=upsert)

    async def delete_one(self, filter_dict):
        async with self._sf() as session:
            stmt = delete(self._model)
            for k, v in filter_dict.items():
                stmt = stmt.where(getattr(self._model, k) == v)
            result = await session.execute(stmt)
            await session.commit()
            return _DeleteResult(deleted_count=result.rowcount)

    async def delete_many(self, filter_dict):
        return await self.delete_one(filter_dict)

    async def count_documents(self, query=None):
        async with self._sf() as session:
            stmt = select(func.count()).select_from(self._model)
            if query:
                stmt = self._apply_query(stmt, query)
            result = await session.execute(stmt)
            return result.scalar() or 0

    def aggregate(self, pipeline):
        return _AsyncCursor(select(self._model), self._sf)

    def create_index(self, *args, **kwargs):
        pass

    async def distinct(self, field):
        async with self._sf() as session:
            col = getattr(self._model, field)
            result = await session.execute(select(func.distinct(col)))
            return [r[0] for r in result.all()]


class PGDatabase:
    """MongoDB Database 兼容包装"""
    def __init__(self, session_factory=None):
        self._sf = session_factory or async_session_factory

    def __getattr__(self, name):
        model = COLLECTION_MODEL_MAP.get(name)
        if model is not None:
            return PGCollection(self._sf, model)
        raise AttributeError(f"No PG model for collection: '{name}'")

    def __getitem__(self, name):
        return self.__getattr__(name)


# 全局实例
_pg_db = PGDatabase()


def get_pg_db():
    """获取 PG 兼容数据库实例（替换 get_mongo_db()）"""
    return _pg_db
