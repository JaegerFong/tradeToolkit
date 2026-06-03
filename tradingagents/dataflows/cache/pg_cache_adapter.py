#!/usr/bin/env python3
"""
PostgreSQL 缓存适配器
根据 TA_USE_APP_CACHE 配置，优先使用 PostgreSQL 中的同步数据
（从 mongodb_cache_adapter 迁移而来）
"""

import pandas as pd
from typing import Optional, Dict, Any, List, Union
from datetime import datetime, timedelta, timezone

from tradingagents.utils.logging_manager import get_logger
logger = get_logger('agents')

from tradingagents.config.runtime_settings import use_app_cache_enabled


class PgCacheAdapter:
    """PostgreSQL 缓存适配器（从 app 的 PostgreSQL 读取同步数据）"""

    def __init__(self):
        self.use_app_cache = use_app_cache_enabled(False)
        self._session = None

        if self.use_app_cache:
            self._init_pg_connection()
            logger.info("🔄 PostgreSQL缓存适配器已启用 - 优先使用PG数据")
        else:
            logger.info("📁 PostgreSQL缓存适配器使用传统缓存模式")

    def _init_pg_connection(self):
        """初始化 PostgreSQL 连接"""
        try:
            from app.core.database import sync_session_factory
            self._session_factory = sync_session_factory
            # 测试连接
            session = sync_session_factory()
            try:
                from sqlalchemy import text
                session.execute(text("SELECT 1"))
                logger.debug("✅ PostgreSQL连接初始化成功")
            finally:
                session.close()
        except Exception as e:
            logger.warning(f"⚠️ PostgreSQL连接初始化失败: {e}")
            self.use_app_cache = False
            self._session_factory = None

    def _get_session(self):
        """获取一个新的同步数据库会话"""
        if self._session_factory is None:
            self._init_pg_connection()
        if self._session_factory:
            return self._session_factory()
        return None

    def _get_data_source_priority(self, symbol: str) -> list:
        """
        获取数据源优先级顺序

        Args:
            symbol: 股票代码

        Returns:
            按优先级排序的数据源列表，例如: ["tushare", "akshare", "baostock"]
        """
        try:
            from tradingagents.utils.stock_utils import StockUtils, StockMarket
            from app.core.pg_models import SystemConfig
            from sqlalchemy import select, desc

            market = StockUtils.identify_stock_market(symbol)

            market_mapping = {
                StockMarket.CHINA_A: 'a_shares',
                StockMarket.US: 'us_stocks',
                StockMarket.HONG_KONG: 'hk_stocks',
            }
            market_category = market_mapping.get(market)
            logger.info(f"📊 [数据源优先级] 股票代码: {symbol}, 市场分类: {market_category}")

            session = self._get_session()
            if session is None:
                logger.warning("⚠️ [数据源优先级] PG会话不可用，使用默认顺序")
                return ['tushare', 'akshare', 'baostock']

            try:
                stmt = (
                    select(SystemConfig)
                    .where(SystemConfig.is_active == True)
                    .order_by(desc(SystemConfig.version))
                    .limit(1)
                )
                result = session.execute(stmt)
                config_data = result.scalars().first()

                if config_data and config_data.data_source_configs:
                    configs = config_data.data_source_configs
                    logger.info(f"📊 [数据源优先级] 从数据库读取到 {len(configs)} 个数据源配置")

                    enabled = []
                    for ds in configs:
                        ds_type = ds.get('type', '')
                        ds_enabled = ds.get('enabled', True)
                        ds_priority = ds.get('priority', 0)
                        ds_categories = ds.get('market_categories', [])

                        logger.info(f"📊 [数据源配置] 类型: {ds_type}, 启用: {ds_enabled}, 优先级: {ds_priority}, 市场: {ds_categories}")

                        if not ds_enabled:
                            logger.info(f"⚠️ [数据源优先级] {ds_type} 未启用，跳过")
                            continue

                        if ds_categories and market_category:
                            if market_category not in ds_categories:
                                logger.info(f"⚠️ [数据源优先级] {ds_type} 不支持市场 {market_category}，跳过")
                                continue

                        enabled.append(ds)

                    logger.info(f"📊 [数据源优先级] 过滤后启用的数据源: {len(enabled)} 个")
                    enabled.sort(key=lambda x: x.get('priority', 0), reverse=True)

                    result_list = [ds.get('type', '').lower() for ds in enabled if ds.get('type')]
                    if result_list:
                        logger.info(f"✅ [数据源优先级] {symbol} ({market_category}): {result_list}")
                        return result_list
                    else:
                        logger.warning(f"⚠️ [数据源优先级] 没有可用的数据源配置，使用默认顺序")
                else:
                    logger.warning(f"⚠️ [数据源优先级] 数据库中没有找到数据源配置")
            finally:
                session.close()

        except Exception as e:
            logger.error(f"❌ 获取数据源优先级失败: {e}", exc_info=True)

        logger.info(f"📊 [数据源优先级] 使用默认顺序: ['tushare', 'akshare', 'baostock']")
        return ['tushare', 'akshare', 'baostock']

    def get_stock_basic_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取股票基础信息（按数据源优先级查询）"""
        if not self.use_app_cache:
            return None

        session = self._get_session()
        if session is None:
            return None

        try:
            from app.core.pg_models import StockBasicInfo
            from sqlalchemy import select

            code6 = str(symbol).zfill(6)
            source_priority = self._get_data_source_priority(symbol)

            doc = None
            for src in source_priority:
                stmt = select(StockBasicInfo).where(
                    StockBasicInfo.code == code6,
                    StockBasicInfo.source == src
                )
                result = session.execute(stmt)
                row = result.scalars().first()
                if row:
                    logger.debug(f"✅ 从PG获取基础信息: {symbol}, 数据源: {src}")
                    doc = {
                        "code": row.code,
                        "symbol": row.symbol,
                        "name": row.name,
                        "industry": row.industry,
                        "area": row.area,
                        "market": row.market,
                        "list_date": row.list_date,
                        "source": row.source,
                        "data_source": row.data_source,
                        "total_mv": row.total_mv,
                        "circ_mv": row.circ_mv,
                        "pe": row.pe,
                        "pb": row.pb,
                        "pe_ttm": row.pe_ttm,
                        "pb_mrq": row.pb_mrq,
                        "turnover_rate": row.turnover_rate,
                        "volume_ratio": row.volume_ratio,
                        "roe": row.roe,
                        "roa": row.roa,
                        "netprofit_margin": row.netprofit_margin,
                        "gross_margin": row.gross_margin,
                        "extra": row.extra,
                        "updated_at": row.updated_at,
                    }
                    break

            if not doc:
                stmt = select(StockBasicInfo).where(StockBasicInfo.code == code6)
                result = session.execute(stmt)
                row = result.scalars().first()
                if row:
                    logger.debug(f"✅ 从PG获取基础信息（旧数据）: {symbol}")
                    doc = {"code": row.code, "name": row.name, "source": row.source}
                    # copy all columns
                    for col in StockBasicInfo.__table__.columns:
                        colname = col.name
                        if hasattr(row, colname) and colname not in doc:
                            doc[colname] = getattr(row, colname)
                else:
                    logger.debug(f"📊 PG中未找到基础信息: {symbol}")
                    return None

            return doc

        except Exception as e:
            logger.warning(f"⚠️ 获取基础信息失败: {e}")
            return None
        finally:
            session.close()

    def get_historical_data(self, symbol: str, start_date: str = None, end_date: str = None,
                          period: str = "daily") -> Optional[pd.DataFrame]:
        """
        获取历史数据，支持多周期，按数据源优先级查询

        Args:
            symbol: 股票代码
            start_date: 开始日期
            end_date: 结束日期
            period: 数据周期（daily/weekly/monthly），默认为daily

        Returns:
            DataFrame: 历史数据
        """
        if not self.use_app_cache:
            return None

        session = self._get_session()
        if session is None:
            return None

        try:
            from app.core.pg_models import DailyData, Minute5Data, Minute15Data, Minute30Data, Minute60Data
            from sqlalchemy import select, and_

            code6 = str(symbol).zfill(6)

            if period == "daily":
                model = DailyData
            elif period == "5min":
                model = Minute5Data
            elif period == "15min":
                model = Minute15Data
            elif period == "30min":
                model = Minute30Data
            elif period == "60min":
                model = Minute60Data
            else:
                model = DailyData

            conditions = [model.code.like(f'%{code6}')]
            date_col = model.date if hasattr(model, 'date') else model.datetime

            if start_date:
                conditions.append(date_col >= start_date)
            if end_date:
                conditions.append(date_col <= end_date)

            stmt = (
                select(model)
                .where(and_(*conditions))
                .order_by(date_col.asc())
            )
            result = session.execute(stmt)
            rows = result.scalars().all()

            if rows:
                data = []
                for r in rows:
                    d = {
                        "code": r.code,
                        "date": r.date if hasattr(r, 'date') else r.datetime,
                        "datetime": r.datetime if hasattr(r, 'datetime') else r.date,
                        "open": r.open,
                        "high": r.high,
                        "low": r.low,
                        "close": r.close,
                        "volume": r.volume,
                        "amount": r.amount,
                    }
                    data.append(d)
                df = pd.DataFrame(data)
                logger.info(f"✅ [数据来源: PG] {symbol}, {len(df)}条记录 (period={period})")
                return df
            else:
                logger.debug(f"⚠️ [PG] 未找到{period}数据: {symbol}")
                return None

        except Exception as e:
            logger.warning(f"⚠️ 获取历史数据失败: {e}")
            return None
        finally:
            session.close()

    def get_financial_data(self, symbol: str, report_period: str = None) -> Optional[Dict[str, Any]]:
        """获取财务数据，按数据源优先级查询"""
        if not self.use_app_cache:
            return None

        session = self._get_session()
        if session is None:
            return None

        try:
            from app.core.pg_models import StockFinancialData
            from sqlalchemy import select, desc

            code6 = str(symbol).zfill(6)
            priority_order = self._get_data_source_priority(symbol)

            for data_source in priority_order:
                stmt = select(StockFinancialData).where(
                    StockFinancialData.code == code6,
                    StockFinancialData.data_source == data_source
                )
                if report_period:
                    stmt = stmt.where(StockFinancialData.report_period == report_period)

                stmt = stmt.order_by(desc(StockFinancialData.report_period)).limit(1)
                result = session.execute(stmt)
                row = result.scalars().first()

                if row:
                    logger.info(f"✅ [数据来源: PG-{data_source}] {symbol}财务数据")
                    doc = {
                        "code": row.code,
                        "symbol": row.symbol,
                        "data_source": row.data_source,
                        "report_period": row.report_period,
                        "roe": row.roe,
                        "roa": row.roa,
                        "netprofit_margin": row.netprofit_margin,
                        "gross_margin": row.gross_margin,
                        "revenue": row.revenue,
                        "net_profit": row.net_profit,
                        "total_assets": row.total_assets,
                        "total_equity": row.total_equity,
                        "eps": row.eps,
                        "bps": row.bps,
                        "extra": row.extra,
                        "updated_at": row.updated_at,
                    }
                    logger.debug(f"📊 [财务数据] 成功提取{symbol}的财务数据，包含字段: {list(doc.keys())}")
                    return doc

            logger.debug(f"📊 [数据来源: PG] 所有数据源都没有财务数据: {symbol}")
            return None

        except Exception as e:
            logger.warning(f"⚠️ [数据来源: PG-财务数据] 获取财务数据失败: {e}")
            return None
        finally:
            session.close()

    def get_news_data(self, symbol: str = None, hours_back: int = 24, limit: int = 20) -> Optional[List[Dict[str, Any]]]:
        """获取新闻数据"""
        if not self.use_app_cache:
            return None

        session = self._get_session()
        if session is None:
            return None

        try:
            from app.core.pg_models import StockNewsData
            from sqlalchemy import select, desc

            conditions = []
            if symbol:
                code6 = str(symbol).zfill(6)
                conditions.append(StockNewsData.code == code6)

            if hours_back:
                start_time = datetime.now(timezone.utc) - timedelta(hours=hours_back)
                conditions.append(StockNewsData.publish_time >= start_time)

            stmt = (
                select(StockNewsData)
                .where(*conditions)
                .order_by(desc(StockNewsData.publish_time))
                .limit(limit)
            )
            result = session.execute(stmt)
            rows = result.scalars().all()

            if rows:
                data = []
                for r in rows:
                    data.append({
                        "code": r.code,
                        "symbol": r.symbol,
                        "title": r.title,
                        "content": r.content,
                        "source": r.source,
                        "source_url": r.source_url,
                        "publish_time": r.publish_time.isoformat() if r.publish_time else None,
                        "data_source": r.data_source,
                    })
                logger.debug(f"✅ [数据来源: PG-新闻数据] 从PG获取新闻数据: {len(data)}条")
                return data
            else:
                logger.debug(f"📊 [数据来源: PG-新闻数据] PG中未找到新闻数据")
                return None

        except Exception as e:
            logger.warning(f"⚠️ [数据来源: PG-新闻数据] 获取新闻数据失败: {e}")
            return None
        finally:
            session.close()

    def get_social_media_data(self, symbol: str = None, hours_back: int = 24, limit: int = 20) -> Optional[List[Dict[str, Any]]]:
        """获取社媒数据（当前版本使用新闻数据作为降级方案）"""
        # PG 中没有单独的社会媒体表，降级到新闻数据
        return self.get_news_data(symbol, hours_back, limit)

    def get_market_quotes(self, symbol: str) -> Optional[Dict[str, Any]]:
        """获取实时行情数据"""
        if not self.use_app_cache:
            return None

        session = self._get_session()
        if session is None:
            return None

        try:
            from app.core.pg_models import MarketQuotes
            from sqlalchemy import select, desc

            code6 = str(symbol).zfill(6)

            stmt = (
                select(MarketQuotes)
                .where(MarketQuotes.code == code6)
                .order_by(desc(MarketQuotes.updated_at))
                .limit(1)
            )
            result = session.execute(stmt)
            row = result.scalars().first()

            if row:
                logger.debug(f"✅ 从PG获取行情数据: {symbol}")
                return {
                    "code": row.code,
                    "symbol": row.symbol,
                    "name": row.name,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "pre_close": row.pre_close,
                    "pct_chg": row.pct_chg,
                    "change": row.change,
                    "volume": row.volume,
                    "amount": row.amount,
                    "trade_date": row.trade_date,
                    "data_source": row.data_source,
                    "updated_at": row.updated_at,
                }
            else:
                logger.debug(f"📊 PG中未找到行情数据: {symbol}")
                return None

        except Exception as e:
            logger.warning(f"⚠️ 获取行情数据失败: {e}")
            return None
        finally:
            session.close()


# 全局实例
_pg_cache_adapter = None


def get_pg_cache_adapter() -> PgCacheAdapter:
    """获取 PG 缓存适配器实例"""
    global _pg_cache_adapter
    if _pg_cache_adapter is None:
        _pg_cache_adapter = PgCacheAdapter()
    return _pg_cache_adapter


# 向后兼容的别名
def get_enhanced_data_adapter() -> PgCacheAdapter:
    """获取增强数据适配器实例（向后兼容，推荐使用 get_pg_cache_adapter）"""
    return get_pg_cache_adapter()


def get_stock_data_with_fallback(symbol: str, start_date: str = None, end_date: str = None,
                                fallback_func=None) -> Union[pd.DataFrame, str, None]:
    """
    带降级的股票数据获取

    Args:
        symbol: 股票代码
        start_date: 开始日期
        end_date: 结束日期
        fallback_func: 降级函数

    Returns:
        优先返回PG数据，失败时调用降级函数
    """
    adapter = get_enhanced_data_adapter()

    if adapter.use_app_cache:
        df = adapter.get_historical_data(symbol, start_date, end_date)
        if df is not None and not df.empty:
            logger.info(f"📊 使用PG历史数据: {symbol}")
            return df

    if fallback_func:
        logger.info(f"🔄 降级到传统数据源: {symbol}")
        return fallback_func(symbol, start_date, end_date)

    return None


def get_financial_data_with_fallback(symbol: str, fallback_func=None) -> Union[Dict[str, Any], str, None]:
    """
    带降级的财务数据获取

    Args:
        symbol: 股票代码
        fallback_func: 降级函数

    Returns:
        优先返回PG数据，失败时调用降级函数
    """
    adapter = get_enhanced_data_adapter()

    if adapter.use_app_cache:
        data = adapter.get_financial_data(symbol)
        if data:
            logger.info(f"💰 使用PG财务数据: {symbol}")
            return data

    if fallback_func:
        logger.info(f"🔄 降级到传统数据源: {symbol}")
        return fallback_func(symbol)

    return None
