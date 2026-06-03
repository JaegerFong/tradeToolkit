"""
PostgreSQL 通用数据访问层
提供与 MongoDB collection 操作类似的 CRUD 接口
"""

import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, TypeVar, Type

from sqlalchemy import select, func, delete, update, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from app.core.pg_models import Base

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=Base)


class PgRepository:
    """
    PostgreSQL 通用 Repository
    异步版本，用于 FastAPI 请求处理
    """

    def __init__(self, session: AsyncSession, model_class: Type[T]):
        self.session = session
        self.model = model_class

    async def get_by_id(self, id_val: int) -> Optional[T]:
        result = await self.session.execute(
            select(self.model).where(self.model.id == id_val)
        )
        return result.scalar_one_or_none()

    async def find_one(self, **filters) -> Optional[T]:
        stmt = select(self.model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none()

    async def find(
        self,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = 0,
    ) -> List[T]:
        stmt = select(self.model)

        if filters:
            for key, value in filters.items():
                if isinstance(value, dict):
                    # 支持范围查询: {"$gte": ..., "$lte": ...}
                    col = getattr(self.model, key)
                    for op, val in value.items():
                        if op == "$gte":
                            stmt = stmt.where(col >= val)
                        elif op == "$lte":
                            stmt = stmt.where(col <= val)
                        elif op == "$gt":
                            stmt = stmt.where(col > val)
                        elif op == "$lt":
                            stmt = stmt.where(col < val)
                        elif op == "$ne":
                            stmt = stmt.where(col != val)
                        elif op == "$in":
                            stmt = stmt.where(col.in_(val))
                elif isinstance(value, list):
                    stmt = stmt.where(getattr(self.model, key).in_(value))
                else:
                    stmt = stmt.where(getattr(self.model, key) == value)

        if order_by:
            col = getattr(self.model, order_by)
            stmt = stmt.order_by(col.desc() if order_desc else col.asc())

        if limit is not None:
            stmt = stmt.limit(limit).offset(offset or 0)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        stmt = select(func.count()).select_from(self.model)
        if filters:
            for key, value in filters.items():
                if isinstance(value, dict):
                    col = getattr(self.model, key)
                    for op, val in value.items():
                        if op == "$gte":
                            stmt = stmt.where(col >= val)
                        elif op == "$lte":
                            stmt = stmt.where(col <= val)
                else:
                    stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def create(self, **values) -> T:
        instance = self.model(**values)
        self.session.add(instance)
        await self.session.flush()
        return instance

    async def bulk_create(self, items: List[Dict[str, Any]]) -> int:
        if not items:
            return 0
        instances = [self.model(**item) for item in items]
        self.session.add_all(instances)
        await self.session.flush()
        return len(instances)

    async def upsert(
        self,
        unique_keys: List[str],
        values: Dict[str, Any],
    ) -> T:
        """Upsert (INSERT ON CONFLICT UPDATE)"""
        stmt = insert(self.model).values(**values)
        update_cols = {k: v for k, v in values.items() if k not in unique_keys}
        if "updated_at" in self.model.__table__.columns:
            update_cols["updated_at"] = datetime.now(timezone.utc)

        stmt = stmt.on_conflict_do_update(
            index_elements=[getattr(self.model, k) for k in unique_keys],
            set_=update_cols,
        )
        await self.session.execute(stmt)
        await self.session.flush()
        # Return the upserted row
        filter_kwargs = {k: values[k] for k in unique_keys}
        return await self.find_one(**filter_kwargs)

    async def update_by_id(self, id_val: int, **values) -> bool:
        values["updated_at"] = datetime.now(timezone.utc)
        stmt = update(self.model).where(self.model.id == id_val).values(**values)
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def update_one(self, filters: Dict[str, Any], values: Dict[str, Any]) -> bool:
        values["updated_at"] = datetime.now(timezone.utc)
        stmt = update(self.model).values(**values)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def delete_by_id(self, id_val: int) -> bool:
        stmt = delete(self.model).where(self.model.id == id_val)
        result = await self.session.execute(stmt)
        return result.rowcount > 0

    async def delete_many(self, **filters) -> int:
        stmt = delete(self.model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        result = await self.session.execute(stmt)
        return result.rowcount

    async def raw_query(self, stmt):
        """执行原始 SQLAlchemy 查询"""
        result = await self.session.execute(stmt)
        return result

    async def paginate(
        self,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        page_size: int = 20,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> Dict[str, Any]:
        items = await self.find(
            filters=filters,
            order_by=order_by,
            order_desc=order_desc,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = await self.count(filters)
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if total > 0 else 0,
        }


class PgRepositorySync:
    """
    PostgreSQL 通用 Repository
    同步版本，用于非异步上下文（如 tradingagents 包）
    """

    def __init__(self, session: Session, model_class: Type[T]):
        self.session = session
        self.model = model_class

    def get_by_id(self, id_val: int) -> Optional[T]:
        return self.session.get(self.model, id_val)

    def find_one(self, **filters) -> Optional[T]:
        stmt = select(self.model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        result = self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none()

    def find(
        self,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        order_desc: bool = False,
        limit: Optional[int] = None,
        offset: Optional[int] = 0,
    ) -> List[T]:
        stmt = select(self.model)

        if filters:
            for key, value in filters.items():
                if isinstance(value, dict):
                    col = getattr(self.model, key)
                    for op, val in value.items():
                        if op == "$gte":
                            stmt = stmt.where(col >= val)
                        elif op == "$lte":
                            stmt = stmt.where(col <= val)
                elif isinstance(value, list):
                    stmt = stmt.where(getattr(self.model, key).in_(value))
                else:
                    stmt = stmt.where(getattr(self.model, key) == value)

        if order_by:
            col = getattr(self.model, order_by)
            stmt = stmt.order_by(col.desc() if order_desc else col.asc())

        if limit is not None:
            stmt = stmt.limit(limit).offset(offset or 0)

        result = self.session.execute(stmt)
        return list(result.scalars().all())

    def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        stmt = select(func.count()).select_from(self.model)
        if filters:
            for key, value in filters.items():
                if isinstance(value, dict):
                    col = getattr(self.model, key)
                    for _op, val in value.items():
                        stmt = stmt.where(col >= val) if _op == "$gte" else stmt.where(col <= val) if _op == "$lte" else stmt
                else:
                    stmt = stmt.where(getattr(self.model, key) == value)
        result = self.session.execute(stmt)
        return result.scalar() or 0

    def create(self, **values) -> T:
        instance = self.model(**values)
        self.session.add(instance)
        self.session.flush()
        return instance

    def upsert(
        self,
        unique_keys: List[str],
        values: Dict[str, Any],
    ) -> T:
        stmt = insert(self.model).values(**values)
        update_cols = {k: v for k, v in values.items() if k not in unique_keys}
        if "updated_at" in self.model.__table__.columns:
            update_cols["updated_at"] = datetime.now(timezone.utc)
        stmt = stmt.on_conflict_do_update(
            index_elements=[getattr(self.model, k) for k in unique_keys],
            set_=update_cols,
        )
        self.session.execute(stmt)
        self.session.flush()
        filter_kwargs = {k: values[k] for k in unique_keys}
        return self.find_one(**filter_kwargs)

    def update_one(self, filters: Dict[str, Any], values: Dict[str, Any]) -> bool:
        if "updated_at" in self.model.__table__.columns:
            values["updated_at"] = datetime.now(timezone.utc)
        stmt = update(self.model).values(**values)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        result = self.session.execute(stmt)
        return result.rowcount > 0

    def delete_many(self, **filters) -> int:
        stmt = delete(self.model)
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        result = self.session.execute(stmt)
        return result.rowcount

    def raw_query(self, stmt):
        return self.session.execute(stmt)
