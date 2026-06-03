"""
自选股服务
"""

import logging
from typing import List, Optional, Dict, Any
from datetime import datetime

from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_factory
from app.core.pg_models import UserFavorite, StockBasicInfo, MarketQuotes
from app.services.quotes_service import get_quotes_service

logger = logging.getLogger("webapi")


class FavoritesService:
    """自选股服务类"""

    def __init__(self):
        pass

    def _format_favorite(self, favorite: Dict[str, Any]) -> Dict[str, Any]:
        """格式化收藏条目（仅基础信息，不包含实时行情）。"""
        added_at = favorite.get("added_at") or favorite.get("created_at")
        if isinstance(added_at, datetime):
            added_at = added_at.isoformat()
        return {
            "stock_code": favorite.get("stock_code"),
            "stock_name": favorite.get("name") or favorite.get("stock_name"),
            "market": favorite.get("market", "A股"),
            "added_at": added_at,
            "tags": favorite.get("tags", []),
            "notes": favorite.get("notes", ""),
            "alert_price_high": favorite.get("alert_price_high"),
            "alert_price_low": favorite.get("alert_price_low"),
            "current_price": None,
            "change_percent": None,
            "volume": None,
        }

    async def get_user_favorites(self, user_id: str) -> List[Dict[str, Any]]:
        """获取用户自选股列表，并批量拉取实时行情进行富集。"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            uid = 0

        async with async_session_factory() as session:
            result = await session.execute(
                select(UserFavorite).where(UserFavorite.user_id == uid)
            )
            favorites = result.scalars().all()

            items = []
            codes = []
            for fav in favorites:
                code = fav.stock_code
                if code:
                    codes.append(code)
                items.append({
                    "stock_code": code,
                    "stock_name": fav.name or "",
                    "market": fav.market or "A股",
                    "added_at": fav.created_at,
                    "tags": fav.tags or [],
                    "notes": fav.alerts or {},
                    "alert_price_high": None,
                    "alert_price_low": None,
                })

            items = [self._format_favorite(fav) for fav in items]

            # 批量获取股票基础信息（板块等）
            if codes:
                try:
                    result = await session.execute(
                        select(StockBasicInfo).where(StockBasicInfo.code.in_(codes))
                    )
                    basic_docs = result.scalars().all()
                    basic_map = {}
                    for d in basic_docs:
                        code = str(d.code).zfill(6)
                        if code not in basic_map:
                            basic_map[code] = d

                    for it in items:
                        code = it.get("stock_code")
                        basic = basic_map.get(code)
                        if basic:
                            it["board"] = basic.market or "-"
                            it["exchange"] = "-"
                        else:
                            it["board"] = "-"
                            it["exchange"] = "-"
                except Exception as e:
                    for it in items:
                        it["board"] = "-"
                        it["exchange"] = "-"

                # 批量获取行情
                try:
                    result = await session.execute(
                        select(MarketQuotes).where(MarketQuotes.code.in_(codes))
                    )
                    docs = result.scalars().all()
                    quotes_map = {str(d.code).zfill(6): d for d in docs}
                    for it in items:
                        code = it.get("stock_code")
                        q = quotes_map.get(code)
                        if q:
                            it["current_price"] = q.close
                            it["change_percent"] = q.pct_chg
                    # 兜底：对未命中的代码使用在线源补齐
                    missing = [c for c in codes if c not in quotes_map]
                    if missing:
                        try:
                            quotes_online = await get_quotes_service().get_quotes(missing)
                            for it in items:
                                code = it.get("stock_code")
                                if it.get("current_price") is None:
                                    q2 = quotes_online.get(code, {}) if quotes_online else {}
                                    it["current_price"] = q2.get("close")
                                    it["change_percent"] = q2.get("pct_chg")
                        except Exception:
                            pass
                except Exception:
                    pass

            return items

    async def add_favorite(
        self,
        user_id: str,
        stock_code: str,
        stock_name: str,
        market: str = "A股",
        tags: List[str] = None,
        notes: str = "",
        alert_price_high: Optional[float] = None,
        alert_price_low: Optional[float] = None
    ) -> bool:
        """添加股票到自选股"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            uid = 0

        now = datetime.utcnow()
        alerts = {"notes": notes}
        if alert_price_high is not None:
            alerts["alert_price_high"] = alert_price_high
        if alert_price_low is not None:
            alerts["alert_price_low"] = alert_price_low

        async with async_session_factory() as session:
            stmt = insert(UserFavorite).values(
                user_id=uid,
                stock_code=stock_code,
                name=stock_name,
                market=market,
                tags=tags or [],
                alerts=alerts,
                created_at=now,
            ).on_conflict_do_update(
                index_elements=["user_id", "stock_code"],
                set_={
                    "name": stock_name,
                    "market": market,
                    "tags": tags or [],
                    "alerts": alerts,
                }
            )
            await session.execute(stmt)
            await session.commit()
            return True
        except Exception as e:
            logger.error(f"添加自选股异常: {type(e).__name__}: {str(e)}", exc_info=True)
            raise

    async def remove_favorite(self, user_id: str, stock_code: str) -> bool:
        """从自选股中移除股票"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            uid = 0

        async with async_session_factory() as session:
            stmt = delete(UserFavorite).where(
                UserFavorite.user_id == uid,
                UserFavorite.stock_code == stock_code
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount > 0

    async def update_favorite(
        self,
        user_id: str,
        stock_code: str,
        tags: Optional[List[str]] = None,
        notes: Optional[str] = None,
        alert_price_high: Optional[float] = None,
        alert_price_low: Optional[float] = None
    ) -> bool:
        """更新自选股信息"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            uid = 0

        if tags is None and notes is None and alert_price_high is None and alert_price_low is None:
            return True

        async with async_session_factory() as session:
            result = await session.execute(
                select(UserFavorite).where(
                    UserFavorite.user_id == uid,
                    UserFavorite.stock_code == stock_code
                ).limit(1)
            )
            fav = result.scalar_one_or_none()
            if not fav:
                return False

            updated_alerts = dict(fav.alerts or {})
            if notes is not None:
                updated_alerts["notes"] = notes
            if alert_price_high is not None:
                updated_alerts["alert_price_high"] = alert_price_high
            if alert_price_low is not None:
                updated_alerts["alert_price_low"] = alert_price_low

            fav.alerts = updated_alerts
            if tags is not None:
                fav.tags = tags
            await session.commit()
            return True

    async def is_favorite(self, user_id: str, stock_code: str) -> bool:
        """检查股票是否在自选股中"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            uid = 0

        async with async_session_factory() as session:
            result = await session.execute(
                select(UserFavorite.id).where(
                    UserFavorite.user_id == uid,
                    UserFavorite.stock_code == stock_code
                ).limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def get_user_tags(self, user_id: str) -> List[str]:
        """获取用户使用的所有标签"""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            uid = 0

        async with async_session_factory() as session:
            result = await session.execute(
                select(UserFavorite.tags).where(
                    UserFavorite.user_id == uid,
                    UserFavorite.tags.isnot(None)
                )
            )
            all_tags = set()
            for (tags,) in result.all():
                if isinstance(tags, list):
                    for t in tags:
                        if t:
                            all_tags.add(str(t))
            return sorted(all_tags)


# 创建全局实例
favorites_service = FavoritesService()
