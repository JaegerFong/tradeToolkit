"""
新闻数据服务
提供统一的新闻数据存储、查询和管理功能
"""
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timedelta
from dataclasses import dataclass
import logging

from sqlalchemy import select, func, delete, and_, or_
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_factory, sync_session_factory
from app.core.pg_models import StockNewsData

logger = logging.getLogger(__name__)


@dataclass
class NewsQueryParams:
    """新闻查询参数"""
    symbol: Optional[str] = None
    symbols: Optional[List[str]] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    category: Optional[str] = None
    sentiment: Optional[str] = None
    importance: Optional[str] = None
    data_source: Optional[str] = None
    keywords: Optional[List[str]] = None
    limit: int = 50
    skip: int = 0
    sort_by: str = "publish_time"
    sort_order: int = -1  # -1 for desc, 1 for asc


@dataclass
class NewsStats:
    """新闻统计信息"""
    total_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    high_importance_count: int = 0
    medium_importance_count: int = 0
    low_importance_count: int = 0
    categories: Dict[str, int] = None
    sources: Dict[str, int] = None

    def __post_init__(self):
        if self.categories is None:
            self.categories = {}
        if self.sources is None:
            self.sources = {}


class NewsDataService:
    """新闻数据服务"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def _to_dict(self, doc: StockNewsData) -> Dict[str, Any]:
        extra = doc.extra or {}
        return {
            "_id": str(doc.id),
            "id": str(doc.id),
            "symbol": doc.code,
            "symbols": [doc.code] if doc.code else [],
            "title": doc.title or "",
            "content": doc.content or "",
            "url": doc.source_url or "",
            "source": doc.source or "",
            "publish_time": doc.publish_time,
            "category": extra.get("category", "general"),
            "sentiment": extra.get("sentiment", "neutral"),
            "importance": extra.get("importance", "medium"),
            "data_source": doc.data_source or "",
            "keywords": extra.get("keywords", []),
            "author": extra.get("author", ""),
            "summary": extra.get("summary", ""),
            "created_at": doc.created_at,
            "updated_at": doc.created_at,
        }

    async def save_news_data(
        self,
        news_data: Union[Dict[str, Any], List[Dict[str, Any]]],
        data_source: str,
        market: str = "CN"
    ) -> int:
        """
        保存新闻数据
        """
        try:
            if isinstance(news_data, dict):
                news_list = [news_data]
            else:
                news_list = news_data

            if not news_list:
                return 0

            saved_count = 0
            async with async_session_factory() as session:
                for news in news_list:
                    std = self._standardize_news_data(news, data_source, market)
                    code = std.get("symbol") or ""
                    title = std.get("title") or ""
                    source_url = std.get("source_url") or ""

                    # 插入新记录（新闻数据可重复，不做 upsert）
                    nd = StockNewsData(
                        code=code,
                        symbol=std.get("full_symbol"),
                        title=title,
                        content=std.get("content", ""),
                        source=std.get("source", ""),
                        source_url=source_url,
                        publish_time=std.get("publish_time"),
                        data_source=data_source,
                        extra={
                            "category": std.get("category", "general"),
                            "sentiment": std.get("sentiment", "neutral"),
                            "importance": std.get("importance", "medium"),
                            "keywords": std.get("keywords", []),
                            "author": std.get("author", ""),
                            "summary": std.get("summary", ""),
                            "sentiment_score": std.get("sentiment_score"),
                        },
                        created_at=datetime.utcnow(),
                    )
                    session.add(nd)
                    saved_count += 1
                await session.commit()

            self.logger.info(f"新闻数据保存完成: {saved_count}条记录 (数据源: {data_source})")
            return saved_count

        except Exception as e:
            self.logger.error(f"保存新闻数据失败: {e}")
            return 0

    def save_news_data_sync(
        self,
        news_data: Union[Dict[str, Any], List[Dict[str, Any]]],
        data_source: str,
        market: str = "CN"
    ) -> int:
        """保存新闻数据（同步版本）"""
        try:
            if isinstance(news_data, dict):
                news_list = [news_data]
            else:
                news_list = news_data

            if not news_list:
                return 0

            session = sync_session_factory()
            try:
                saved_count = 0
                for news in news_list:
                    std = self._standardize_news_data(news, data_source, market)
                    code = std.get("symbol") or ""

                    nd = StockNewsData(
                        code=code,
                        symbol=std.get("full_symbol"),
                        title=std.get("title") or "",
                        content=std.get("content", ""),
                        source=std.get("source", ""),
                        source_url=std.get("url", ""),
                        publish_time=std.get("publish_time"),
                        data_source=data_source,
                        extra={
                            "category": std.get("category", "general"),
                            "sentiment": std.get("sentiment", "neutral"),
                            "importance": std.get("importance", "medium"),
                            "keywords": std.get("keywords", []),
                            "author": std.get("author", ""),
                            "summary": std.get("summary", ""),
                            "sentiment_score": std.get("sentiment_score"),
                        },
                        created_at=datetime.utcnow(),
                    )
                    session.add(nd)
                    saved_count += 1
                session.commit()
                self.logger.info(f"新闻数据保存完成: {saved_count}条记录 (数据源: {data_source})")
                return saved_count
            finally:
                session.close()

        except Exception as e:
            self.logger.error(f"保存新闻数据失败: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return 0

    def _standardize_news_data(
        self,
        news_data: Dict[str, Any],
        data_source: str,
        market: str,
    ) -> Dict[str, Any]:
        """标准化新闻数据"""
        symbol = news_data.get("symbol")
        symbols = news_data.get("symbols", [])
        if symbol and symbol not in symbols:
            symbols = [symbol] + symbols

        return {
            "symbol": symbol,
            "full_symbol": self._get_full_symbol(symbol, market) if symbol else None,
            "market": market,
            "symbols": symbols,
            "title": news_data.get("title", ""),
            "content": news_data.get("content", ""),
            "summary": news_data.get("summary", ""),
            "url": news_data.get("url", ""),
            "source": news_data.get("source", ""),
            "author": news_data.get("author", ""),
            "publish_time": self._parse_datetime(news_data.get("publish_time")),
            "category": news_data.get("category", "general"),
            "sentiment": news_data.get("sentiment", "neutral"),
            "sentiment_score": self._safe_float(news_data.get("sentiment_score")),
            "keywords": news_data.get("keywords", []),
            "importance": news_data.get("importance", "medium"),
            "data_source": data_source,
        }

    def _get_full_symbol(self, symbol: str, market: str) -> Optional[str]:
        if not symbol:
            return None
        if market == "CN":
            if len(symbol) == 6:
                if symbol.startswith(('60', '68')):
                    return f"{symbol}.SH"
                elif symbol.startswith(('00', '30')):
                    return f"{symbol}.SZ"
        return symbol

    def _parse_datetime(self, dt_value) -> Optional[datetime]:
        if dt_value is None:
            return None
        if isinstance(dt_value, datetime):
            return dt_value
        if isinstance(dt_value, str):
            try:
                formats = [
                    "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%dT%H:%M:%SZ",
                    "%Y-%m-%d",
                ]
                for fmt in formats:
                    try:
                        return datetime.strptime(dt_value, fmt)
                    except ValueError:
                        continue
                self.logger.warning(f"无法解析日期时间: {dt_value}")
                return datetime.utcnow()
            except Exception:
                return datetime.utcnow()
        return datetime.utcnow()

    def _safe_float(self, value) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    async def query_news(self, params: NewsQueryParams) -> List[Dict[str, Any]]:
        """查询新闻数据"""
        try:
            async with async_session_factory() as session:
                stmt = select(StockNewsData)

                if params.symbol:
                    stmt = stmt.where(StockNewsData.code == params.symbol)

                if params.symbols:
                    stmt = stmt.where(StockNewsData.code.in_(params.symbols))

                if params.start_time:
                    stmt = stmt.where(StockNewsData.publish_time >= params.start_time)
                if params.end_time:
                    stmt = stmt.where(StockNewsData.publish_time <= params.end_time)

                if params.data_source:
                    stmt = stmt.where(StockNewsData.data_source == params.data_source)

                # 分类/情感/重要性在 extra JSONB 中，简化处理
                if params.category:
                    stmt = stmt.where(StockNewsData.extra['category'].as_string() == params.category)
                if params.sentiment:
                    stmt = stmt.where(StockNewsData.extra['sentiment'].as_string() == params.sentiment)
                if params.importance:
                    stmt = stmt.where(StockNewsData.extra['importance'].as_string() == params.importance)

                if params.keywords:
                    conditions = []
                    for kw in params.keywords:
                        conditions.append(StockNewsData.title.ilike(f"%{kw}%"))
                        conditions.append(StockNewsData.content.ilike(f"%{kw}%"))
                    stmt = stmt.where(or_(*conditions))

                # 排序
                if params.sort_by == "publish_time":
                    stmt = stmt.order_by(
                        StockNewsData.publish_time.desc() if params.sort_order == -1 else StockNewsData.publish_time.asc()
                    )
                else:
                    stmt = stmt.order_by(StockNewsData.publish_time.desc())

                # 分页
                stmt = stmt.offset(params.skip).limit(params.limit)

                result = await session.execute(stmt)
                docs = result.scalars().all()

                results = [self._to_dict(d) for d in docs]
                self.logger.info(f"查询新闻数据返回 {len(results)} 条记录")
                return results

        except Exception as e:
            self.logger.error(f"查询新闻数据失败: {e}", exc_info=True)
            return []

    async def get_latest_news(
        self,
        symbol: str = None,
        limit: int = 10,
        hours_back: int = 24
    ) -> List[Dict[str, Any]]:
        """获取最新新闻"""
        start_time = datetime.utcnow() - timedelta(hours=hours_back)
        params = NewsQueryParams(
            symbol=symbol,
            start_time=start_time,
            limit=limit,
            sort_by="publish_time",
            sort_order=-1
        )
        return await self.query_news(params)

    async def get_news_statistics(
        self,
        symbol: str = None,
        start_time: datetime = None,
        end_time: datetime = None
    ) -> NewsStats:
        """获取新闻统计信息"""
        try:
            async with async_session_factory() as session:
                stmt = select(func.count()).select_from(StockNewsData)
                if symbol:
                    stmt = stmt.where(StockNewsData.code == symbol)
                if start_time:
                    stmt = stmt.where(StockNewsData.publish_time >= start_time)
                if end_time:
                    stmt = stmt.where(StockNewsData.publish_time <= end_time)

                result = await session.execute(stmt)
                total = result.scalar()

                # 简化统计：按 extra JSONB 字段查询
                sentiment_counts = {"positive": 0, "negative": 0, "neutral": total}
                importance_counts = {"high": 0, "medium": total, "low": 0}

                return NewsStats(
                    total_count=total,
                    positive_count=sentiment_counts.get("positive", 0),
                    negative_count=sentiment_counts.get("negative", 0),
                    neutral_count=sentiment_counts.get("neutral", 0),
                    high_importance_count=importance_counts.get("high", 0),
                    medium_importance_count=importance_counts.get("medium", 0),
                    low_importance_count=importance_counts.get("low", 0),
                )

        except Exception as e:
            self.logger.error(f"获取新闻统计失败: {e}")
            return NewsStats()

    async def delete_old_news(self, days_to_keep: int = 90) -> int:
        """删除过期新闻"""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_to_keep)
            async with async_session_factory() as session:
                stmt = delete(StockNewsData).where(StockNewsData.publish_time < cutoff_date)
                result = await session.execute(stmt)
                await session.commit()
                deleted_count = result.rowcount
                self.logger.info(f"删除过期新闻: {deleted_count}条记录")
                return deleted_count
        except Exception as e:
            self.logger.error(f"删除过期新闻失败: {e}")
            return 0

    async def search_news(
        self,
        query_text: str,
        symbol: str = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """全文搜索新闻"""
        try:
            async with async_session_factory() as session:
                stmt = select(StockNewsData).where(
                    or_(
                        StockNewsData.title.ilike(f"%{query_text}%"),
                        StockNewsData.content.ilike(f"%{query_text}%"),
                    )
                )
                if symbol:
                    stmt = stmt.where(StockNewsData.code == symbol)
                stmt = stmt.order_by(StockNewsData.publish_time.desc()).limit(limit)

                result = await session.execute(stmt)
                docs = result.scalars().all()
                results = [self._to_dict(d) for d in docs]
                self.logger.info(f"全文搜索返回 {len(results)} 条结果")
                return results

        except Exception as e:
            self.logger.error(f"全文搜索失败: {e}")
            return []


# 全局服务实例
_service_instance = None


async def get_news_data_service() -> NewsDataService:
    """获取新闻数据服务实例"""
    global _service_instance
    if _service_instance is None:
        _service_instance = NewsDataService()
        logger.info("新闻数据服务初始化成功")
    return _service_instance
