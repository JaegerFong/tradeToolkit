#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一股票数据服务（跨市场，支持多数据源）
"""

import logging
from typing import Dict, List, Optional

from sqlalchemy import select, or_

from app.core.database import async_session_factory
from app.core.pg_models import StockBasicInfo, MarketQuotes, DataSourceGrouping

logger = logging.getLogger("webapi")


class UnifiedStockService:
    """统一股票数据服务（跨市场，支持多数据源）"""

    def __init__(self):
        pass

    async def get_stock_info(
        self,
        market: str,
        code: str,
        source: Optional[str] = None
    ) -> Optional[Dict]:
        """
        获取股票基础信息（支持多数据源）
        """
        async with async_session_factory() as session:
            if source:
                result = await session.execute(
                    select(StockBasicInfo).where(
                        StockBasicInfo.code == code,
                        StockBasicInfo.source == source,
                    ).limit(1)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    return self._stock_to_dict(doc)
            else:
                source_priority = await self._get_source_priority(market)
                for src in source_priority:
                    result = await session.execute(
                        select(StockBasicInfo).where(
                            StockBasicInfo.code == code,
                            StockBasicInfo.source == src,
                        ).limit(1)
                    )
                    doc = result.scalar_one_or_none()
                    if doc:
                        return self._stock_to_dict(doc)

                # 兼容：不指定source查询
                result = await session.execute(
                    select(StockBasicInfo).where(StockBasicInfo.code == code).limit(1)
                )
                doc = result.scalar_one_or_none()
                if doc:
                    return self._stock_to_dict(doc)

        return None

    def _stock_to_dict(self, doc: StockBasicInfo) -> Dict:
        return {
            "code": doc.code,
            "symbol": doc.symbol,
            "name": doc.name,
            "industry": doc.industry,
            "market": doc.market,
            "source": doc.source,
            "data_source": doc.data_source,
            "total_mv": doc.total_mv,
            "circ_mv": doc.circ_mv,
            "pe": doc.pe,
            "pb": doc.pb,
            "created_at": doc.created_at,
            "updated_at": doc.updated_at,
        }

    async def _get_source_priority(self, market: str) -> List[str]:
        """从数据库获取数据源优先级"""
        market_category_map = {
            "CN": "a_shares",
            "HK": "hk_stocks",
            "US": "us_stocks"
        }
        market_category_id = market_category_map.get(market)

        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(DataSourceGrouping).where(
                        DataSourceGrouping.market_categories.contains([market_category_id]),
                        DataSourceGrouping.is_active == True,
                    )
                )
                groupings = result.scalars().all()
                if groupings:
                    # 按 data_sources JSONB 中的 order 排序
                    priority_list = []
                    for g in groupings:
                        ds = g.data_sources or []
                        for item in ds if isinstance(ds, list) else []:
                            if isinstance(item, dict) and item.get("name"):
                                priority_list.append(item["name"])
                    return priority_list or default_priority.get(market, [])
        except Exception as e:
            logger.warning(f"从数据库读取数据源优先级失败: {e}")

        default_priority = {
            "CN": ["akshare", "baostock"],
            "HK": ["yfinance_hk", "akshare_hk"],
            "US": ["yfinance_us"]
        }
        return default_priority.get(market, [])

    async def get_stock_quote(self, market: str, code: str) -> Optional[Dict]:
        """获取实时行情"""
        async with async_session_factory() as session:
            result = await session.execute(
                select(MarketQuotes).where(MarketQuotes.code == code).limit(1)
            )
            doc = result.scalar_one_or_none()
            if doc:
                return {
                    "code": doc.code,
                    "name": doc.name,
                    "open": doc.open,
                    "high": doc.high,
                    "low": doc.low,
                    "close": doc.close,
                    "pre_close": doc.pre_close,
                    "pct_chg": doc.pct_chg,
                    "change": doc.change,
                    "volume": doc.volume,
                    "amount": doc.amount,
                    "trade_date": doc.trade_date,
                    "data_source": doc.data_source,
                }
        return None

    async def search_stocks(
        self,
        market: str,
        query: str,
        limit: int = 20
    ) -> List[Dict]:
        """搜索股票（去重，只返回每个股票的最优数据源）"""
        async with async_session_factory() as session:
            kw = f"%{query}%"
            result = await session.execute(
                select(StockBasicInfo).where(
                    or_(
                        StockBasicInfo.code.ilike(kw),
                        StockBasicInfo.name.ilike(kw),
                    )
                ).limit(limit * 2)
            )
            docs = result.scalars().all()

            # 按 code 去重
            unique_results = {}
            source_priority = await self._get_source_priority(market)
            for doc in docs:
                code = doc.code
                if code not in unique_results:
                    unique_results[code] = doc
                else:
                    current = unique_results[code]
                    try:
                        if doc.source in source_priority and current.source in source_priority:
                            if source_priority.index(doc.source) < source_priority.index(current.source):
                                unique_results[code] = doc
                    except ValueError:
                        pass

            result_list = [self._stock_to_dict(d) for d in list(unique_results.values())[:limit]]
            logger.info(f"搜索 {market} 市场: '{query}' -> {len(result_list)} 条结果（已去重）")
            return result_list

    async def get_daily_quotes(
        self,
        market: str,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """获取历史K线数据 (从 tdx2db public.daily_data)"""
        from app.core.pg_models import DailyData

        async with async_session_factory() as session:
            stmt = select(DailyData).where(DailyData.code == code)
            if start_date:
                stmt = stmt.where(DailyData.date >= start_date)
            if end_date:
                stmt = stmt.where(DailyData.date <= end_date)
            stmt = stmt.order_by(DailyData.date.desc()).limit(limit)

            result = await session.execute(stmt)
            docs = result.scalars().all()
            return [
                {
                    "code": d.code,
                    "date": d.date.isoformat() if d.date else None,
                    "open": d.open,
                    "high": d.high,
                    "low": d.low,
                    "close": d.close,
                    "volume": d.volume,
                    "amount": d.amount,
                }
                for d in docs
            ]

    async def get_supported_markets(self) -> List[Dict]:
        """获取支持的市场列表"""
        return [
            {
                "code": "CN",
                "name": "A股",
                "name_en": "China A-Share",
                "currency": "CNY",
                "timezone": "Asia/Shanghai"
            },
            {
                "code": "HK",
                "name": "港股",
                "name_en": "Hong Kong Stock",
                "currency": "HKD",
                "timezone": "Asia/Hong_Kong"
            },
            {
                "code": "US",
                "name": "美股",
                "name_en": "US Stock",
                "currency": "USD",
                "timezone": "America/New_York"
            }
        ]
