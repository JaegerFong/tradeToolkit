"""
用户自定义标签服务
"""
from __future__ import annotations
from typing import List, Optional, Dict, Any
from datetime import datetime

from sqlalchemy import select, update, delete

from app.core.database import async_session_factory
from app.core.pg_models import StockTag


class TagsService:
    def __init__(self) -> None:
        pass

    def _format_doc(self, doc: StockTag) -> Dict[str, Any]:
        return {
            "id": str(doc.id),
            "name": doc.tag_name,
            "color": "#409EFF",
            "sort_order": 0,
            "created_at": doc.created_at.isoformat() if doc.created_at else datetime.utcnow().isoformat(),
            "updated_at": doc.created_at.isoformat() if doc.created_at else datetime.utcnow().isoformat(),
        }

    async def list_tags(self, user_id: str) -> List[Dict[str, Any]]:
        async with async_session_factory() as session:
            result = await session.execute(
                select(StockTag).where(StockTag.user_id == int(user_id)).order_by(
                    StockTag.tag_name
                )
            )
            docs = result.scalars().all()
            tags_map: Dict[str, Dict[str, Any]] = {}
            for d in docs:
                # 去重：同名标签只返回一条
                if d.tag_name not in tags_map:
                    tags_map[d.tag_name] = self._format_doc(d)
            return list(tags_map.values())

    async def create_tag(self, user_id: str, name: str, color: Optional[str] = None, sort_order: int = 0) -> Dict[str, Any]:
        now = datetime.utcnow()
        async with async_session_factory() as session:
            # 检查是否已存在
            existing = await session.execute(
                select(StockTag).where(
                    StockTag.user_id == int(user_id),
                    StockTag.tag_name == name.strip()
                ).limit(1)
            )
            if existing.scalar_one_or_none():
                # 已存在，返回已有记录
                return self._format_doc(existing.scalar_one_or_none())

            tag = StockTag(
                user_id=int(user_id),
                stock_code="__tag__",  # 通用标签用占位符
                tag_name=name.strip(),
                created_at=now,
            )
            session.add(tag)
            await session.commit()
            await session.refresh(tag)
            return self._format_doc(tag)

    async def update_tag(self, user_id: str, tag_id: str, *, name: Optional[str] = None, color: Optional[str] = None, sort_order: Optional[int] = None) -> bool:
        if name is None:
            return True  # 不需要更新
        async with async_session_factory() as session:
            stmt = (
                update(StockTag)
                .where(StockTag.id == int(tag_id), StockTag.user_id == int(user_id))
                .values(tag_name=name.strip())
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def delete_tag(self, user_id: str, tag_id: str) -> bool:
        async with async_session_factory() as session:
            stmt = delete(StockTag).where(
                StockTag.id == int(tag_id),
                StockTag.user_id == int(user_id)
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0


# 全局实例
tags_service = TagsService()
