"""
AKShare数据同步服务
基于AKShare提供器的统一数据同步方案
"""
import asyncio
import logging
import os
import random
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from app.core.database import async_session_factory
from app.core.pg_models import StockBasicInfo, MarketQuotes
from sqlalchemy import select, func, update as sql_update, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from app.services.historical_data_service import get_historical_data_service
from app.services.news_data_service import get_news_data_service
from tradingagents.dataflows.providers.china.akshare import get_akshare_provider

logger = logging.getLogger(__name__)


def _model_to_dict(model_instance):
    """将 SQLAlchemy 模型实例转换为字典"""
    if model_instance is None:
        return {}
    return {c.name: getattr(model_instance, c.name)
            for c in model_instance.__table__.columns}


class AKShareSyncService:
    """
    AKShare数据同步服务

    提供完整的数据同步功能：
    - 股票基础信息同步
    - 实时行情同步
    - 历史数据同步
    - 财务数据同步
    """

    def __init__(self):
        self.provider = None
        self.historical_service = None
        self.news_service = None
        self.batch_size = 100
        self.rate_limit_delay = float(os.getenv("AKSHARE_RATE_LIMIT_DELAY", os.getenv("AKSHARE_RATE_LIMIT", "0.2")))
        self.historical_retries = max(1, int(os.getenv("AKSHARE_HISTORICAL_RETRIES", "3")))
        self.historical_retry_delay = float(os.getenv("AKSHARE_HISTORICAL_RETRY_DELAY", "2.0"))
        self.single_symbol_timeout = float(os.getenv("AKSHARE_SINGLE_SYMBOL_TIMEOUT", "90"))

    async def initialize(self):
        """初始化同步服务"""
        try:
            self.historical_service = await get_historical_data_service()
            self.news_service = await get_news_data_service()
            self.provider = get_akshare_provider()

            if not await self.provider.test_connection():
                raise RuntimeError("AKShare连接失败，无法启动同步服务")

            logger.info("AKShare同步服务初始化完成")

        except Exception as e:
            logger.error(f"AKShare同步服务初始化失败: {e}")
            raise

    async def sync_stock_basic_info(self, force_update: bool = False) -> Dict[str, Any]:
        """同步股票基础信息"""
        logger.info("开始同步股票基础信息...")

        stats = {
            "total_processed": 0,
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "start_time": datetime.utcnow(),
            "end_time": None,
            "duration": 0,
            "errors": []
        }

        try:
            stock_list = await self.provider.get_stock_list()
            if not stock_list:
                logger.warning("未获取到股票列表")
                return stats

            stats["total_processed"] = len(stock_list)
            logger.info(f"获取到 {len(stock_list)} 只股票信息")

            for i in range(0, len(stock_list), self.batch_size):
                batch = stock_list[i:i + self.batch_size]
                batch_stats = await self._process_basic_info_batch(batch, force_update)

                stats["success_count"] += batch_stats["success_count"]
                stats["error_count"] += batch_stats["error_count"]
                stats["skipped_count"] += batch_stats["skipped_count"]
                stats["errors"].extend(batch_stats["errors"])

                progress = min(i + self.batch_size, len(stock_list))
                logger.info(f"基础信息同步进度: {progress}/{len(stock_list)} "
                           f"(成功: {stats['success_count']}, 错误: {stats['error_count']})")

                if i + self.batch_size < len(stock_list):
                    await asyncio.sleep(self.rate_limit_delay)

            stats["end_time"] = datetime.utcnow()
            stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()

            logger.info(f"股票基础信息同步完成！总计: {stats['total_processed']}只")

            return stats

        except Exception as e:
            logger.error(f"股票基础信息同步失败: {e}")
            stats["errors"].append({"error": str(e), "context": "sync_stock_basic_info"})
            return stats

    async def _process_basic_info_batch(self, batch: List[Dict[str, Any]], force_update: bool) -> Dict[str, Any]:
        """处理基础信息批次"""
        batch_stats = {
            "success_count": 0,
            "error_count": 0,
            "skipped_count": 0,
            "errors": []
        }

        for stock_info in batch:
            try:
                code = stock_info["code"]

                if not force_update:
                    async with async_session_factory() as session:
                        result = await session.execute(
                            select(StockBasicInfo).where(StockBasicInfo.code == code)
                        )
                        existing = result.scalar_one_or_none()
                        if existing and self._is_data_fresh(existing.updated_at, hours=24):
                            batch_stats["skipped_count"] += 1
                            continue

                basic_info = await self.provider.get_stock_basic_info(code)

                if basic_info:
                    if hasattr(basic_info, 'model_dump'):
                        basic_data = basic_info.model_dump()
                    elif hasattr(basic_info, 'dict'):
                        basic_data = basic_info.dict()
                    else:
                        basic_data = basic_info

                    if "source" not in basic_data:
                        basic_data["source"] = "akshare"
                    if "symbol" not in basic_data:
                        basic_data["symbol"] = code

                    try:
                        async with async_session_factory() as session:
                            stmt = pg_insert(StockBasicInfo).values(**basic_data)
                            stmt = stmt.on_conflict_do_update(
                                constraint="uq_stock_basic_code_source",
                                set_=basic_data
                            )
                            await session.execute(stmt)
                            await session.commit()
                        batch_stats["success_count"] += 1
                    except Exception as e:
                        batch_stats["error_count"] += 1
                        batch_stats["errors"].append({
                            "code": code,
                            "error": f"数据库更新失败: {str(e)}",
                            "context": "update_stock_basic_info"
                        })
                else:
                    batch_stats["error_count"] += 1

            except Exception as e:
                batch_stats["error_count"] += 1
                batch_stats["errors"].append({
                    "code": stock_info.get("code", "unknown"),
                    "error": str(e),
                    "context": "_process_basic_info_batch"
                })

        return batch_stats

    def _is_data_fresh(self, updated_at: Any, hours: int = 24) -> bool:
        """检查数据是否新鲜"""
        if not updated_at:
            return False

        try:
            if isinstance(updated_at, str):
                updated_at = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
            elif isinstance(updated_at, datetime):
                pass
            else:
                return False

            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=None)
            else:
                updated_at = updated_at.replace(tzinfo=None)

            now = datetime.utcnow()
            time_diff = now - updated_at

            return time_diff.total_seconds() < (hours * 3600)

        except Exception as e:
            logger.debug(f"检查数据新鲜度失败: {e}")
            return False

    async def sync_realtime_quotes(self, symbols: List[str] = None, force: bool = False) -> Dict[str, Any]:
        """同步实时行情数据"""
        if symbols:
            logger.info(f"开始同步指定股票的实时行情（共 {len(symbols)} 只）: {symbols}")
        else:
            logger.info("开始同步全市场实时行情...")

        stats = {
            "total_processed": 0,
            "success_count": 0,
            "error_count": 0,
            "start_time": datetime.utcnow(),
            "end_time": None,
            "duration": 0,
            "errors": []
        }

        try:
            if symbols is None:
                async with async_session_factory() as session:
                    result = await session.execute(select(StockBasicInfo.code))
                    symbols = [r[0] for r in result.all()]

            if not symbols:
                logger.warning("没有找到要同步的股票")
                return stats

            stats["total_processed"] = len(symbols)
            logger.info(f"准备同步 {len(symbols)} 只股票的行情")

            if len(symbols) == 1:
                logger.info(f"单个股票同步，直接使用 get_stock_quotes 接口")
                symbol = symbols[0]
                success = await self._get_and_save_quotes(symbol)
                if success:
                    stats["success_count"] = 1
                else:
                    stats["error_count"] = 1
                logger.info(f"行情同步进度: 1/1 (成功: {stats['success_count']})")
            else:
                logger.info("获取全市场实时行情快照...")
                quotes_map = await self.provider.get_batch_stock_quotes(symbols)

                if not quotes_map:
                    logger.warning("获取全市场快照失败，回退到逐个获取模式")
                    for i in range(0, len(symbols), self.batch_size):
                        batch = symbols[i:i + self.batch_size]
                        batch_stats = await self._process_quotes_batch_fallback(batch)
                        stats["success_count"] += batch_stats["success_count"]
                        stats["error_count"] += batch_stats["error_count"]
                        stats["errors"].extend(batch_stats["errors"])
                        progress = min(i + self.batch_size, len(symbols))
                        logger.info(f"行情同步进度: {progress}/{len(symbols)}")
                        if i + self.batch_size < len(symbols):
                            await asyncio.sleep(self.rate_limit_delay)
                else:
                    logger.info(f"获取到 {len(quotes_map)} 只股票的行情数据，开始保存...")
                    for i in range(0, len(symbols), self.batch_size):
                        batch = symbols[i:i + self.batch_size]
                        for symbol in batch:
                            try:
                                quotes = quotes_map.get(symbol)
                                if quotes:
                                    if hasattr(quotes, 'model_dump'):
                                        quotes_data = quotes.model_dump()
                                    elif hasattr(quotes, 'dict'):
                                        quotes_data = quotes.dict()
                                    else:
                                        quotes_data = quotes

                                    if "symbol" not in quotes_data:
                                        quotes_data["symbol"] = symbol
                                    if "code" not in quotes_data:
                                        quotes_data["code"] = symbol

                                    async with async_session_factory() as session:
                                        stmt = pg_insert(MarketQuotes).values(**quotes_data)
                                        stmt = stmt.on_conflict_do_update(
                                            constraint="uq_market_quotes_code",
                                            set_=quotes_data
                                        )
                                        await session.execute(stmt)
                                        await session.commit()
                                    stats["success_count"] += 1
                                else:
                                    stats["error_count"] += 1
                            except Exception as e:
                                stats["error_count"] += 1
                                stats["errors"].append({"code": symbol, "error": str(e)})

                        progress = min(i + self.batch_size, len(symbols))
                        logger.info(f"行情保存进度: {progress}/{len(symbols)}")

            stats["end_time"] = datetime.utcnow()
            stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()
            logger.info(f"实时行情同步完成！总计: {stats['total_processed']}只")

            return stats

        except Exception as e:
            logger.error(f"实时行情同步失败: {e}")
            stats["errors"].append({"error": str(e), "context": "sync_realtime_quotes"})
            return stats

    async def _process_quotes_batch(self, batch: List[str]) -> Dict[str, Any]:
        """处理行情批次 - 优化版"""
        batch_stats = {"success_count": 0, "error_count": 0, "errors": []}

        try:
            logger.debug(f"获取全市场快照以处理 {len(batch)} 只股票...")
            quotes_map = await self.provider.get_batch_stock_quotes(batch)

            if not quotes_map:
                logger.warning("获取全市场快照失败，回退到逐个获取")
                return await self._process_quotes_batch_fallback(batch)

            for symbol in batch:
                try:
                    quotes = quotes_map.get(symbol)
                    if quotes:
                        if hasattr(quotes, 'model_dump'):
                            quotes_data = quotes.model_dump()
                        elif hasattr(quotes, 'dict'):
                            quotes_data = quotes.dict()
                        else:
                            quotes_data = quotes

                        if "symbol" not in quotes_data:
                            quotes_data["symbol"] = symbol
                        if "code" not in quotes_data:
                            quotes_data["code"] = symbol

                        async with async_session_factory() as session:
                            stmt = pg_insert(MarketQuotes).values(**quotes_data)
                            stmt = stmt.on_conflict_do_update(
                                constraint="uq_market_quotes_code",
                                set_=quotes_data
                            )
                            await session.execute(stmt)
                            await session.commit()
                        batch_stats["success_count"] += 1
                    else:
                        batch_stats["error_count"] += 1
                except Exception as e:
                    batch_stats["error_count"] += 1
                    batch_stats["errors"].append({"code": symbol, "error": str(e)})

            return batch_stats

        except Exception as e:
            logger.error(f"批量处理行情失败: {e}")
            return await self._process_quotes_batch_fallback(batch)

    async def _process_quotes_batch_fallback(self, batch: List[str]) -> Dict[str, Any]:
        """处理行情批次 - 回退方案"""
        batch_stats = {"success_count": 0, "error_count": 0, "errors": []}

        for symbol in batch:
            try:
                success = await self._get_and_save_quotes(symbol)
                if success:
                    batch_stats["success_count"] += 1
                else:
                    batch_stats["error_count"] += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                batch_stats["error_count"] += 1
                batch_stats["errors"].append({"code": symbol, "error": str(e)})

        return batch_stats

    async def _get_and_save_quotes(self, symbol: str) -> bool:
        """获取并保存单个股票行情"""
        try:
            quotes = await self.provider.get_stock_quotes(symbol)
            if quotes:
                if hasattr(quotes, 'model_dump'):
                    quotes_data = quotes.model_dump()
                elif hasattr(quotes, 'dict'):
                    quotes_data = quotes.dict()
                else:
                    quotes_data = quotes

                if "symbol" not in quotes_data:
                    quotes_data["symbol"] = symbol
                if "code" not in quotes_data:
                    quotes_data["code"] = symbol

                logger.info(f"准备保存 {symbol} 行情到数据库: price={quotes_data.get('price')}")

                async with async_session_factory() as session:
                    stmt = pg_insert(MarketQuotes).values(**quotes_data)
                    stmt = stmt.on_conflict_do_update(
                        constraint="uq_market_quotes_code",
                        set_=quotes_data
                    )
                    await session.execute(stmt)
                    await session.commit()

                logger.info(f"{symbol} 行情已保存到数据库")
                return True
            return False
        except Exception as e:
            logger.error(f"获取 {symbol} 行情失败: {e}", exc_info=True)
            return False

    async def sync_historical_data(
        self,
        start_date: str = None,
        end_date: str = None,
        symbols: List[str] = None,
        incremental: bool = True,
        period: str = "daily"
    ) -> Dict[str, Any]:
        """同步历史数据"""
        period_name = {"daily": "日线", "weekly": "周线", "monthly": "月线"}.get(period, "日线")
        logger.info(f"开始同步{period_name}历史数据...")

        stats = {
            "total_processed": 0,
            "success_count": 0,
            "error_count": 0,
            "total_records": 0,
            "start_time": datetime.utcnow(),
            "end_time": None,
            "duration": 0,
            "errors": []
        }

        try:
            if not end_date:
                end_date = datetime.now().strftime('%Y-%m-%d')

            if symbols is None:
                async with async_session_factory() as session:
                    result = await session.execute(select(StockBasicInfo.code))
                    symbols = [r[0] for r in result.all()]

            if not symbols:
                logger.warning("没有找到要同步的股票")
                return stats

            stats["total_processed"] = len(symbols)

            logger.info(f"历史数据同步: 结束日期={end_date}, 股票数量={len(symbols)}, 模式={'增量' if incremental else '全量'}")

            for i in range(0, len(symbols), self.batch_size):
                batch = symbols[i:i + self.batch_size]
                batch_stats = await self._process_historical_batch(
                    batch, start_date, end_date, period, incremental
                )

                stats["success_count"] += batch_stats["success_count"]
                stats["error_count"] += batch_stats["error_count"]
                stats["total_records"] += batch_stats["total_records"]
                stats["errors"].extend(batch_stats["errors"])

                progress = min(i + self.batch_size, len(symbols))
                logger.info(f"历史数据同步进度: {progress}/{len(symbols)}")

                if i + self.batch_size < len(symbols):
                    await asyncio.sleep(self.rate_limit_delay)

            stats["end_time"] = datetime.utcnow()
            stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()

            logger.info(f"历史数据同步完成！总计: {stats['total_processed']}只股票")
            return stats

        except Exception as e:
            logger.error(f"历史数据同步失败: {e}")
            stats["errors"].append({"error": str(e), "context": "sync_historical_data"})
            return stats

    async def _process_historical_batch(
        self, batch: List[str], start_date: str, end_date: str,
        period: str = "daily", incremental: bool = False
    ) -> Dict[str, Any]:
        """处理历史数据批次"""
        batch_stats = {"success_count": 0, "error_count": 0, "total_records": 0, "errors": []}

        for symbol in batch:
            try:
                symbol_start_date = start_date
                if not symbol_start_date:
                    if incremental:
                        symbol_start_date = await self._get_last_sync_date(symbol)
                    else:
                        symbol_start_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')

                hist_data = None
                last_error = None
                for attempt in range(1, self.historical_retries + 1):
                    try:
                        hist_data = await asyncio.wait_for(
                            self.provider.get_historical_data(symbol, symbol_start_date, end_date, period),
                            timeout=self.single_symbol_timeout
                        )
                        if hist_data is not None and not hist_data.empty:
                            break
                        last_error = RuntimeError("历史数据为空")
                    except Exception as e:
                        last_error = e

                    if attempt < self.historical_retries:
                        sleep_seconds = self.historical_retry_delay * attempt + random.uniform(0, self.rate_limit_delay)
                        logger.debug(f"{symbol}{period}历史数据获取失败/为空，{sleep_seconds:.1f}秒后重试 {attempt}/{self.historical_retries}")
                        await asyncio.sleep(sleep_seconds)

                if hist_data is not None and not hist_data.empty:
                    if self.historical_service is None:
                        self.historical_service = await get_historical_data_service()

                    saved_count = await self.historical_service.save_historical_data(
                        symbol=symbol, data=hist_data, data_source="akshare", market="CN", period=period
                    )
                    batch_stats["success_count"] += 1
                    batch_stats["total_records"] += saved_count
                    logger.debug(f"{symbol}历史数据同步成功: {saved_count}条记录")
                else:
                    batch_stats["error_count"] += 1
                    batch_stats["errors"].append({
                        "code": symbol,
                        "error": str(last_error) if last_error else "历史数据为空",
                        "context": "_process_historical_batch"
                    })

            except Exception as e:
                batch_stats["error_count"] += 1
                batch_stats["errors"].append({"code": symbol, "error": str(e), "context": "_process_historical_batch"})

        return batch_stats

    async def _get_last_sync_date(self, symbol: str = None) -> str:
        """获取最后同步日期"""
        try:
            if self.historical_service is None:
                self.historical_service = await get_historical_data_service()

            if symbol:
                latest_date = await self.historical_service.get_latest_date(symbol, "akshare")
                if latest_date:
                    try:
                        last_date_obj = datetime.strptime(latest_date, '%Y-%m-%d')
                        next_date = last_date_obj + timedelta(days=1)
                        return next_date.strftime('%Y-%m-%d')
                    except ValueError:
                        return latest_date
                else:
                    async with async_session_factory() as session:
                        result = await session.execute(
                            select(StockBasicInfo.list_date).where(StockBasicInfo.code == symbol)
                        )
                        row = result.scalar_one_or_none()
                        if row and row:
                            list_date = row
                            if isinstance(list_date, str):
                                if len(list_date) == 8 and list_date.isdigit():
                                    return f"{list_date[:4]}-{list_date[4:6]}-{list_date[6:]}"
                                else:
                                    return list_date
                            elif hasattr(list_date, 'strftime'):
                                return list_date.strftime('%Y-%m-%d')

                    logger.warning(f"{symbol}: 未找到上市日期，从1990-01-01开始同步")
                    return "1990-01-01"

            return (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

        except Exception as e:
            logger.error(f"获取最后同步日期失败 {symbol}: {e}")
            return (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

    async def sync_financial_data(self, symbols: List[str] = None) -> Dict[str, Any]:
        """同步财务数据"""
        logger.info("开始同步财务数据...")

        stats = {
            "total_processed": 0, "success_count": 0, "error_count": 0,
            "start_time": datetime.utcnow(), "end_time": None, "duration": 0, "errors": []
        }

        try:
            if symbols is None:
                async with async_session_factory() as session:
                    result = await session.execute(
                        select(StockBasicInfo.code).where(
                            StockBasicInfo.market.in_(["主板", "创业板", "科创板", "北交所"])
                        )
                    )
                    symbols = [r[0] for r in result.all()]
                logger.info(f"从 stock_basic_info 获取到 {len(symbols)} 只股票")

            if not symbols:
                logger.warning("没有找到要同步的股票")
                return stats

            stats["total_processed"] = len(symbols)
            logger.info(f"准备同步 {len(symbols)} 只股票的财务数据")

            for i in range(0, len(symbols), self.batch_size):
                batch = symbols[i:i + self.batch_size]
                batch_stats = await self._process_financial_batch(batch)

                stats["success_count"] += batch_stats["success_count"]
                stats["error_count"] += batch_stats["error_count"]
                stats["errors"].extend(batch_stats["errors"])

                progress = min(i + self.batch_size, len(symbols))
                logger.info(f"财务数据同步进度: {progress}/{len(symbols)}")

                if i + self.batch_size < len(symbols):
                    await asyncio.sleep(self.rate_limit_delay)

            stats["end_time"] = datetime.utcnow()
            stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()

            logger.info(f"财务数据同步完成！总计: {stats['total_processed']}只股票")
            return stats

        except Exception as e:
            logger.error(f"财务数据同步失败: {e}")
            stats["errors"].append({"error": str(e), "context": "sync_financial_data"})
            return stats

    async def _process_financial_batch(self, batch: List[str]) -> Dict[str, Any]:
        """处理财务数据批次"""
        batch_stats = {"success_count": 0, "error_count": 0, "errors": []}

        for symbol in batch:
            try:
                financial_data = await self.provider.get_financial_data(symbol)
                if financial_data:
                    success = await self._save_financial_data(symbol, financial_data)
                    if success:
                        batch_stats["success_count"] += 1
                    else:
                        batch_stats["error_count"] += 1
                else:
                    batch_stats["error_count"] += 1
            except Exception as e:
                batch_stats["error_count"] += 1
                batch_stats["errors"].append({"code": symbol, "error": str(e)})

        return batch_stats

    async def _save_financial_data(self, symbol: str, financial_data: Dict[str, Any]) -> bool:
        """保存财务数据"""
        try:
            from app.services.financial_data_service import get_financial_data_service

            financial_service = await get_financial_data_service()
            saved_count = await financial_service.save_financial_data(
                symbol=symbol, financial_data=financial_data,
                data_source="akshare", market="CN", report_type="quarterly"
            )
            return saved_count > 0
        except Exception as e:
            logger.error(f"保存 {symbol} 财务数据失败: {e}")
            return False

    async def run_status_check(self) -> Dict[str, Any]:
        """运行状态检查"""
        try:
            logger.info("开始AKShare状态检查...")

            provider_connected = await self.provider.test_connection()

            async with async_session_factory() as session:
                basic_result = await session.execute(select(func.count(StockBasicInfo.id)))
                basic_count = basic_result.scalar()

                latest_result = await session.execute(
                    select(StockBasicInfo.updated_at).order_by(StockBasicInfo.updated_at.desc()).limit(1)
                )
                latest_basic = latest_result.scalar_one_or_none()

                quotes_result = await session.execute(select(func.count(MarketQuotes.id)))
                quotes_count = quotes_result.scalar()

                latest_q_result = await session.execute(
                    select(MarketQuotes.updated_at).order_by(MarketQuotes.updated_at.desc()).limit(1)
                )
                latest_quotes = latest_q_result.scalar_one_or_none()

            collections_status = {
                "stock_basic_info": {
                    "count": basic_count,
                    "latest_update": latest_basic.isoformat() if latest_basic else None
                },
                "market_quotes": {
                    "count": quotes_count,
                    "latest_update": latest_quotes.isoformat() if latest_quotes else None
                }
            }

            status_result = {
                "provider_connected": provider_connected,
                "collections": collections_status,
                "status_time": datetime.utcnow()
            }

            logger.info(f"AKShare状态检查完成")
            return status_result

        except Exception as e:
            logger.error(f"AKShare状态检查失败: {e}")
            return {"provider_connected": False, "error": str(e), "status_time": datetime.utcnow()}

    # ==================== 新闻数据同步 ====================

    async def _get_favorite_stocks(self) -> List[str]:
        """获取所有用户的自选股列表（去重）"""
        try:
            from app.core.pg_adapter import get_pg_db as get_mongo_db
            db = get_mongo_db()
            favorite_codes = set()

            # users 和 user_favorites 暂无 PG 模型, 保留 MongoDB 查询
            users_cursor = db.users.find(
                {"favorite_stocks": {"$exists": True, "$ne": []}},
                {"favorite_stocks.stock_code": 1}
            )

            async for user in users_cursor:
                for fav in user.get("favorite_stocks", []):
                    code = fav.get("stock_code")
                    if code:
                        favorite_codes.add(code)

            latest_doc = await db.user_favorites.find_one(
                {"favorites": {"$exists": True, "$ne": []}},
                {"favorites.stock_code": 1},
                sort=[("updated_at", -1)]
            )

            if latest_doc:
                for fav in latest_doc.get("favorites", []):
                    code = fav.get("stock_code")
                    if code:
                        favorite_codes.add(code)

            result = sorted(list(favorite_codes))
            logger.info(f"获取到 {len(result)} 只自选股")
            return result

        except Exception as e:
            logger.error(f"获取自选股列表失败: {e}")
            return []

    async def sync_news_data(
        self, symbols: List[str] = None, max_news_per_stock: int = 20,
        force_update: bool = False, favorites_only: bool = True
    ) -> Dict[str, Any]:
        """同步新闻数据"""
        logger.info("开始同步AKShare新闻数据...")

        stats = {
            "total_processed": 0, "success_count": 0, "error_count": 0,
            "news_count": 0, "start_time": datetime.utcnow(),
            "favorites_only": favorites_only, "errors": []
        }

        try:
            if symbols is None:
                if favorites_only:
                    symbols = await self._get_favorite_stocks()
                    logger.info(f"只同步自选股，共 {len(symbols)} 只")
                else:
                    async with async_session_factory() as session:
                        result = await session.execute(select(StockBasicInfo.code))
                        symbols = [r[0] for r in result.all()]
                    logger.info(f"同步所有股票，共 {len(symbols)} 只")

            if not symbols:
                logger.warning("没有找到需要同步新闻的股票")
                return stats

            stats["total_processed"] = len(symbols)
            logger.info(f"需要同步 {len(symbols)} 只股票的新闻")

            for i in range(0, len(symbols), self.batch_size):
                batch = symbols[i:i + self.batch_size]
                batch_stats = await self._process_news_batch(batch, max_news_per_stock)

                stats["success_count"] += batch_stats["success_count"]
                stats["error_count"] += batch_stats["error_count"]
                stats["news_count"] += batch_stats["news_count"]
                stats["errors"].extend(batch_stats["errors"])

                progress = min(i + self.batch_size, len(symbols))
                logger.info(f"新闻同步进度: {progress}/{len(symbols)}")

                if i + self.batch_size < len(symbols):
                    await asyncio.sleep(self.rate_limit_delay)

            stats["end_time"] = datetime.utcnow()
            stats["duration"] = (stats["end_time"] - stats["start_time"]).total_seconds()

            logger.info(f"AKShare新闻数据同步完成: 总计 {stats['total_processed']} 只股票")
            return stats

        except Exception as e:
            logger.error(f"AKShare新闻数据同步失败: {e}")
            stats["errors"].append({"error": str(e), "context": "sync_news_data"})
            return stats

    async def _process_news_batch(self, batch: List[str], max_news_per_stock: int) -> Dict[str, Any]:
        """处理新闻批次"""
        batch_stats = {"success_count": 0, "error_count": 0, "news_count": 0, "errors": []}

        for symbol in batch:
            try:
                news_data = await self.provider.get_stock_news(symbol=symbol, limit=max_news_per_stock)
                if news_data:
                    saved_count = await self.news_service.save_news_data(
                        news_data=news_data, data_source="akshare", market="CN"
                    )
                    batch_stats["success_count"] += 1
                    batch_stats["news_count"] += saved_count
                    logger.debug(f"{symbol} 新闻同步成功: {saved_count}条")
                else:
                    logger.debug(f"{symbol} 未获取到新闻数据")
                    batch_stats["success_count"] += 1
                await asyncio.sleep(0.2)
            except Exception as e:
                batch_stats["error_count"] += 1
                error_msg = f"{symbol}: {str(e)}"
                batch_stats["errors"].append(error_msg)
                logger.error(f"{symbol} 新闻同步失败: {e}")
                await asyncio.sleep(1.0)

        return batch_stats


# 全局同步服务实例
_akshare_sync_service = None

async def get_akshare_sync_service() -> AKShareSyncService:
    """获取AKShare同步服务实例"""
    global _akshare_sync_service
    if _akshare_sync_service is None:
        _akshare_sync_service = AKShareSyncService()
        await _akshare_sync_service.initialize()
    return _akshare_sync_service


# APScheduler兼容的任务函数
async def _is_trading_day() -> bool:
    """检查今天是否为 A 股交易日"""
    from tradingagents.dataflows.providers.china.akshare_network import is_trading_day
    return is_trading_day()


async def run_akshare_basic_info_sync(force_update: bool = False):
    """APScheduler任务：同步股票基础信息"""
    try:
        service = await get_akshare_sync_service()
        result = await service.sync_stock_basic_info(force_update=force_update)
        logger.info(f"AKShare基础信息同步完成: {result}")
        return result
    except Exception as e:
        logger.error(f"AKShare基础信息同步失败: {e}")
        raise


async def run_akshare_quotes_sync(force: bool = False):
    """APScheduler任务：同步实时行情"""
    if not force and not await _is_trading_day():
        logger.info("AKShare 行情同步跳过: 非交易日")
        return {"skipped": True, "reason": "非交易日"}
    try:
        service = await get_akshare_sync_service()
        result = await service.sync_realtime_quotes(force=force)
        logger.info(f"AKShare行情同步完成: {result}")
        return result
    except Exception as e:
        logger.error(f"AKShare行情同步失败: {e}")
        raise


async def run_akshare_historical_sync(incremental: bool = True):
    """APScheduler任务：同步历史数据"""
    if not await _is_trading_day():
        logger.info("AKShare 历史数据同步跳过: 非交易日")
        return {"skipped": True, "reason": "非交易日"}
    try:
        service = await get_akshare_sync_service()
        result = await service.sync_historical_data(incremental=incremental)
        logger.info(f"AKShare历史数据同步完成: {result}")
        return result
    except Exception as e:
        logger.error(f"AKShare历史数据同步失败: {e}")
        raise


async def run_akshare_financial_sync():
    """APScheduler任务：同步财务数据"""
    try:
        service = await get_akshare_sync_service()
        result = await service.sync_financial_data()
        logger.info(f"AKShare财务数据同步完成: {result}")
        return result
    except Exception as e:
        logger.error(f"AKShare财务数据同步失败: {e}")
        raise


async def run_akshare_status_check():
    """APScheduler任务：状态检查"""
    try:
        service = await get_akshare_sync_service()
        result = await service.run_status_check()
        logger.info(f"AKShare状态检查完成: {result}")
        return result
    except Exception as e:
        logger.error(f"AKShare状态检查失败: {e}")
        raise


async def run_akshare_news_sync(max_news_per_stock: int = 20):
    """APScheduler任务：同步新闻数据"""
    try:
        service = await get_akshare_sync_service()
        result = await service.sync_news_data(max_news_per_stock=max_news_per_stock)
        logger.info(f"AKShare新闻数据同步完成: {result}")
        return result
    except Exception as e:
        logger.error(f"AKShare新闻数据同步失败: {e}")
        raise
