"""
股票数据服务层 - PostgreSQL 版本
统一数据访问接口
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any, List

from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.pg_models import StockBasicInfo, MarketQuotes
from app.core.pg_repository import PgRepository
from app.models.stock_models import (
    StockBasicInfoExtended,
    MarketQuotesExtended,
)

logger = logging.getLogger(__name__)


class StockDataService:
    """股票数据服务 - 统一数据访问层"""

    def __init__(self):
        self.basic_info_table = "stock_basic_info"
        self.market_quotes_table = "market_quotes"

    async def get_stock_basic_info(
        self,
        symbol: str,
        source: Optional[str] = None
    ) -> Optional[StockBasicInfoExtended]:
        try:
            async with async_session_factory() as session:
                symbol6 = str(symbol).zfill(6)

                stmt = select(StockBasicInfo).where(
                    (StockBasicInfo.code == symbol6) | (StockBasicInfo.symbol == symbol6)
                )

                if source:
                    stmt = stmt.where(StockBasicInfo.source == source)
                else:
                    source_priority = ["tushare", "multi_source", "akshare", "baostock"]
                    for src in source_priority:
                        stmt_with_src = select(StockBasicInfo).where(
                            (StockBasicInfo.code == symbol6) | (StockBasicInfo.symbol == symbol6)
                        ).where(StockBasicInfo.source == src)
                        result = await session.execute(stmt_with_src.limit(1))
                        doc = result.scalar_one_or_none()
                        if doc:
                            logger.debug(f"✅ 使用数据源: {src}")
                            return StockBasicInfoExtended(**self._standardize_basic_info(self._model_to_dict(doc)))

                    # 兼容旧数据：不带 source 条件
                    result = await session.execute(stmt.limit(1))
                    doc = result.scalar_one_or_none()
                    if doc:
                        logger.warning(f"⚠️ 使用旧数据（无 source 字段）: {symbol6}")
                        return StockBasicInfoExtended(**self._standardize_basic_info(self._model_to_dict(doc)))
                    return None

                result = await session.execute(stmt.limit(1))
                doc = result.scalar_one_or_none()
                if not doc:
                    return None
                return StockBasicInfoExtended(**self._standardize_basic_info(self._model_to_dict(doc)))

        except Exception as e:
            logger.error(f"获取股票基础信息失败 symbol={symbol}: {e}")
            return None

    async def get_market_quotes(self, symbol: str) -> Optional[MarketQuotesExtended]:
        try:
            async with async_session_factory() as session:
                symbol6 = str(symbol).zfill(6)

                result = await session.execute(
                    select(MarketQuotes).where(
                        (MarketQuotes.code == symbol6) | (MarketQuotes.symbol == symbol6)
                    ).limit(1)
                )
                doc = result.scalar_one_or_none()
                if not doc:
                    return None
                return MarketQuotesExtended(**self._standardize_market_quotes(self._model_to_dict(doc)))

        except Exception as e:
            logger.error(f"获取实时行情失败 symbol={symbol}: {e}")
            return None

    async def get_stock_list(
        self,
        market: Optional[str] = None,
        industry: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        source: Optional[str] = None
    ) -> List[StockBasicInfoExtended]:
        try:
            async with async_session_factory() as session:
                if not source:
                    from app.core.unified_config import UnifiedConfigManager
                    config = UnifiedConfigManager()
                    data_source_configs = await config.get_data_source_configs_async()
                    enabled_sources = [
                        ds.type.lower() for ds in data_source_configs
                        if ds.enabled and ds.type.lower() in ['tushare', 'akshare', 'baostock']
                    ]
                    if not enabled_sources:
                        enabled_sources = ['tushare', 'akshare', 'baostock']
                    source = enabled_sources[0]

                stmt = select(StockBasicInfo).where(StockBasicInfo.source == source)
                if market:
                    stmt = stmt.where(StockBasicInfo.market == market)
                if industry:
                    stmt = stmt.where(StockBasicInfo.industry == industry)

                offset = (page - 1) * page_size
                stmt = stmt.offset(offset).limit(page_size)

                result = await session.execute(stmt)
                rows = result.scalars().all()
                return [
                    StockBasicInfoExtended(**self._standardize_basic_info(self._model_to_dict(r)))
                    for r in rows
                ]

        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return []

    async def update_stock_basic_info(
        self,
        symbol: str,
        update_data: Dict[str, Any],
        source: str = "tushare"
    ) -> bool:
        try:
            async with async_session_factory() as session:
                repo = PgRepository(session, StockBasicInfo)
                symbol6 = str(symbol).zfill(6)
                update_data.setdefault("symbol", symbol6)
                update_data.setdefault("code", symbol6)
                update_data.setdefault("source", source)
                update_data["updated_at"] = datetime.utcnow()

                result = await repo.upsert(
                    unique_keys=["code", "source"],
                    values=update_data,
                )
                await session.commit()
                return result is not None

        except Exception as e:
            logger.error(f"更新股票基础信息失败 symbol={symbol}: {e}")
            return False

    async def update_market_quotes(
        self,
        symbol: str,
        quote_data: Dict[str, Any]
    ) -> bool:
        try:
            async with async_session_factory() as session:
                repo = PgRepository(session, MarketQuotes)
                symbol6 = str(symbol).zfill(6)
                quote_data.setdefault("symbol", symbol6)
                quote_data.setdefault("code", symbol6)
                quote_data["updated_at"] = datetime.utcnow()

                result = await repo.upsert(
                    unique_keys=["code"],
                    values=quote_data,
                )
                await session.commit()
                return result is not None

        except Exception as e:
            logger.error(f"更新实时行情失败 symbol={symbol}: {e}")
            return False

    def _standardize_basic_info(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        result = doc.copy()
        symbol = doc.get("symbol") or doc.get("code", "")
        result["symbol"] = symbol
        if "code" in doc and "symbol" not in doc:
            result["code"] = doc["code"]
        if "full_symbol" not in result or not result["full_symbol"]:
            if symbol and len(symbol) == 6:
                if symbol.startswith(('60', '68', '90')):
                    result["full_symbol"] = f"{symbol}.SS"
                    exchange = "SSE"
                    exchange_name = "上海证券交易所"
                else:
                    result["full_symbol"] = f"{symbol}.SZ"
                    exchange = "SZSE"
                    exchange_name = "深圳证券交易所"
            else:
                exchange = "SZSE"
                exchange_name = "深圳证券交易所"
        else:
            full_symbol = result["full_symbol"]
            if ".SS" in full_symbol or ".SH" in full_symbol:
                exchange = "SSE"
                exchange_name = "上海证券交易所"
            else:
                exchange = "SZSE"
                exchange_name = "深圳证券交易所"
        result["market_info"] = {
            "market": "CN",
            "exchange": exchange,
            "exchange_name": exchange_name,
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
            "trading_hours": {"open": "09:30", "close": "15:00", "lunch_break": ["11:30", "13:00"]},
        }
        result["board"] = doc.get("sse")
        result["sector"] = doc.get("sec")
        result["status"] = "L"
        result["data_version"] = 1
        return result

    def _standardize_market_quotes(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        result = doc.copy()
        symbol = doc.get("symbol") or doc.get("code", "")
        result["symbol"] = symbol
        if "code" in doc and "symbol" not in doc:
            result["code"] = doc["code"]
        if "full_symbol" not in result or not result["full_symbol"]:
            if symbol and len(symbol) == 6:
                if symbol.startswith(('60', '68', '90')):
                    result["full_symbol"] = f"{symbol}.SS"
                else:
                    result["full_symbol"] = f"{symbol}.SZ"
        if "market" not in result:
            result["market"] = "CN"
        result["current_price"] = doc.get("close")
        if doc.get("close") and doc.get("pre_close"):
            try:
                result["change"] = float(doc["close"]) - float(doc["pre_close"])
            except (ValueError, TypeError):
                result["change"] = None
        result["data_source"] = "market_quotes"
        result["data_version"] = 1
        return result

    def _model_to_dict(self, obj) -> Dict[str, Any]:
        """将 ORM 对象转为 dict"""
        if obj is None:
            return {}
        if hasattr(obj, '__dict__'):
            d = {k: v for k, v in obj.__dict__.items() if not k.startswith('_')}
            return d
        return dict(obj)


_stock_data_service: Optional[StockDataService] = None


def get_stock_data_service() -> StockDataService:
    global _stock_data_service
    if _stock_data_service is None:
        _stock_data_service = StockDataService()
    return _stock_data_service
