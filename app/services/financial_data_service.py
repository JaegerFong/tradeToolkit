#!/usr/bin/env python3
"""
财务数据服务
统一管理多数据源的财务数据存储和查询
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import pandas as pd

from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert

from app.core.database import async_session_factory
from app.core.pg_models import StockFinancialData

logger = logging.getLogger(__name__)


class FinancialDataService:
    """财务数据统一管理服务"""

    async def save_financial_data(
        self,
        symbol: str,
        financial_data: Dict[str, Any],
        data_source: str,
        market: str = "CN",
        report_period: str = None,
        report_type: str = "quarterly"
    ) -> int:
        """
        保存财务数据到数据库
        """
        try:
            logger.info(f"开始保存 {symbol} 财务数据 (数据源: {data_source})")

            # 标准化财务数据
            standardized_data = self._standardize_financial_data(
                symbol, financial_data, data_source, market, report_period, report_type
            )

            if not standardized_data:
                logger.warning(f"{symbol} 财务数据标准化后为空")
                return 0

            saved_count = 0

            # 如果是多期数据，分别处理每期
            data_list = standardized_data if isinstance(standardized_data, list) else [standardized_data]

            async with async_session_factory() as session:
                for data_item in data_list:
                    code = data_item.get("code") or data_item.get("symbol", symbol)
                    rp = data_item.get("report_period", "")
                    ds = data_item.get("data_source", data_source)

                    values = {
                        "code": code,
                        "symbol": data_item.get("symbol", symbol),
                        "data_source": ds,
                        "report_period": rp,
                        "roe": data_item.get("roe"),
                        "revenue": data_item.get("revenue"),
                        "net_profit": data_item.get("net_income"),
                        "total_assets": data_item.get("total_assets"),
                        "total_equity": data_item.get("total_equity"),
                        "extra": {k: v for k, v in data_item.items() if k not in {
                            "code", "symbol", "data_source", "report_period",
                            "roe", "revenue", "net_income", "total_assets",
                            "total_equity", "net_profit", "created_at", "updated_at"
                        }},
                        "created_at": datetime.now(timezone.utc),
                        "updated_at": datetime.now(timezone.utc),
                    }

                    stmt = insert(StockFinancialData).values(**values).on_conflict_do_update(
                        index_elements=[],
                        set_={
                            "revenue": values["revenue"],
                            "net_profit": values["net_profit"],
                            "total_assets": values["total_assets"],
                            "total_equity": values["total_equity"],
                            "roe": values["roe"],
                            "extra": values["extra"],
                            "updated_at": datetime.now(timezone.utc),
                        }
                    )
                    await session.execute(stmt)
                    saved_count += 1
                await session.commit()

            logger.info(f"{symbol} 财务数据保存完成: {saved_count}条记录")
            return saved_count

        except Exception as e:
            logger.error(f"保存财务数据失败 {symbol}: {e}")
            return 0

    async def get_financial_data(
        self,
        symbol: str,
        report_period: str = None,
        data_source: str = None,
        report_type: str = None,
        limit: int = None
    ) -> List[Dict[str, Any]]:
        """查询财务数据"""
        try:
            async with async_session_factory() as session:
                stmt = select(StockFinancialData).where(
                    StockFinancialData.code == symbol
                )

                if report_period:
                    stmt = stmt.where(StockFinancialData.report_period == report_period)

                if data_source:
                    stmt = stmt.where(StockFinancialData.data_source == data_source)

                stmt = stmt.order_by(StockFinancialData.report_period.desc())

                if limit:
                    stmt = stmt.limit(limit)

                result = await session.execute(stmt)
                docs = result.scalars().all()

                results = []
                for doc in docs:
                    extra = doc.extra or {}
                    results.append({
                        "symbol": doc.code,
                        "code": doc.code,
                        "report_period": doc.report_period,
                        "data_source": doc.data_source,
                        "revenue": doc.revenue,
                        "net_income": doc.net_profit,
                        "total_assets": doc.total_assets,
                        "total_equity": doc.total_equity,
                        "roe": doc.roe,
                        "created_at": doc.created_at,
                        "updated_at": doc.updated_at,
                        **extra,
                    })

                logger.info(f"查询财务数据: {symbol} 返回 {len(results)} 条记录")
                return results

        except Exception as e:
            logger.error(f"查询财务数据失败 {symbol}: {e}")
            return []

    async def get_latest_financial_data(
        self,
        symbol: str,
        data_source: str = None
    ) -> Optional[Dict[str, Any]]:
        """获取最新财务数据"""
        results = await self.get_financial_data(
            symbol=symbol,
            data_source=data_source,
            limit=1
        )
        return results[0] if results else None

    async def get_financial_statistics(self) -> Dict[str, Any]:
        """获取财务数据统计信息"""
        try:
            async with async_session_factory() as session:
                # 按数据源统计
                result = await session.execute(
                    select(
                        StockFinancialData.data_source,
                        func.count().label("count"),
                        func.max(StockFinancialData.report_period).label("latest_period"),
                        func.count(func.distinct(StockFinancialData.code)).label("symbol_count")
                    ).group_by(StockFinancialData.data_source)
                )
                rows = result.all()

                stats: Dict[str, Any] = {}
                total_records = 0
                total_symbols = set()

                for row in rows:
                    source = row[0] or "unknown"
                    count = row[1]
                    latest = row[2]
                    sym_count = row[3]

                    if source not in stats:
                        stats[source] = {}

                    stats[source]["all"] = {
                        "count": count,
                        "latest_period": latest,
                        "symbol_count": sym_count,
                    }

                    total_records += count

                return {
                    "total_records": total_records,
                    "total_symbols": len(total_symbols),
                    "by_source": stats,
                    "last_updated": datetime.utcnow().isoformat(),
                }

        except Exception as e:
            logger.error(f"获取财务数据统计失败: {e}")
            return {}

    def _standardize_financial_data(
        self,
        symbol: str,
        financial_data: Dict[str, Any],
        data_source: str,
        market: str,
        report_period: str = None,
        report_type: str = "quarterly"
    ) -> Optional[Dict[str, Any]]:
        """标准化财务数据"""
        try:
            now = datetime.now(timezone.utc)
            if data_source == "akshare":
                return self._standardize_akshare_data(
                    symbol, financial_data, market, report_period, report_type, now
                )
            elif data_source == "baostock":
                return self._standardize_baostock_data(
                    symbol, financial_data, market, report_period, report_type, now
                )
            else:
                logger.warning(f"不支持的数据源: {data_source}")
                return None
        except Exception as e:
            logger.error(f"标准化财务数据失败 {symbol}: {e}")
            return None

    def _standardize_akshare_data(
        self,
        symbol: str,
        financial_data: Dict[str, Any],
        market: str,
        report_period: str,
        report_type: str,
        now: datetime
    ) -> Dict[str, Any]:
        """标准化AKShare财务数据"""
        base_data = {
            "code": symbol,
            "symbol": symbol,
            "full_symbol": self._get_full_symbol(symbol, market),
            "market": market,
            "report_period": report_period or self._extract_latest_period(financial_data),
            "report_type": report_type,
            "data_source": "akshare",
            "created_at": now,
            "updated_at": now,
            "version": 1
        }
        base_data.update(self._extract_akshare_indicators(financial_data))
        return base_data

    def _standardize_baostock_data(
        self,
        symbol: str,
        financial_data: Dict[str, Any],
        market: str,
        report_period: str,
        report_type: str,
        now: datetime
    ) -> Dict[str, Any]:
        """标准化BaoStock财务数据"""
        base_data = {
            "code": symbol,
            "symbol": symbol,
            "full_symbol": self._get_full_symbol(symbol, market),
            "market": market,
            "report_period": report_period or self._generate_current_period(),
            "report_type": report_type,
            "data_source": "baostock",
            "created_at": now,
            "updated_at": now,
            "version": 1
        }
        base_data.update(financial_data)
        return base_data

    def _get_full_symbol(self, symbol: str, market: str) -> str:
        if market == "CN":
            if symbol.startswith("6"):
                return f"{symbol}.SH"
            else:
                return f"{symbol}.SZ"
        return symbol

    def _extract_latest_period(self, financial_data: Dict[str, Any]) -> str:
        for key in ['main_indicators', 'balance_sheet', 'income_statement']:
            if key in financial_data and financial_data[key]:
                records = financial_data[key]
                if isinstance(records, list) and records:
                    first_record = records[0]
                    for date_field in ['报告期', '报告日期', 'date', '日期']:
                        if date_field in first_record:
                            return str(first_record[date_field]).replace('-', '')
        return self._generate_current_period()

    def _extract_akshare_indicators(self, financial_data: Dict[str, Any]) -> Dict[str, Any]:
        """从AKShare数据中提取关键财务指标"""
        indicators = {}
        if 'main_indicators' in financial_data and financial_data['main_indicators']:
            main_data = financial_data['main_indicators'][0] if financial_data['main_indicators'] else {}
            indicators.update({
                "revenue": self._safe_float(main_data.get('营业收入')),
                "net_income": self._safe_float(main_data.get('净利润')),
                "total_assets": self._safe_float(main_data.get('总资产')),
                "total_equity": self._safe_float(main_data.get('股东权益合计')),
            })
            roe = main_data.get('净资产收益率(ROE)') or main_data.get('净资产收益率')
            if roe is not None:
                indicators["roe"] = self._safe_float(roe)
            debt_ratio = main_data.get('资产负债率') or main_data.get('负债率')
            if debt_ratio is not None:
                indicators["debt_to_assets"] = self._safe_float(debt_ratio)

        if 'balance_sheet' in financial_data and financial_data['balance_sheet']:
            balance_data = financial_data['balance_sheet'][0] if financial_data['balance_sheet'] else {}
            indicators.update({
                "total_liab": self._safe_float(balance_data.get('负债合计')),
                "cash_and_equivalents": self._safe_float(balance_data.get('货币资金')),
            })
            if "debt_to_assets" not in indicators:
                total_liab = indicators.get("total_liab")
                total_assets = indicators.get("total_assets")
                if total_liab is not None and total_assets is not None and total_assets > 0:
                    indicators["debt_to_assets"] = (total_liab / total_assets) * 100
        return indicators

    def _generate_current_period(self) -> str:
        now = datetime.now()
        year = now.year
        month = now.month
        if month <= 3:
            quarter = 1
        elif month <= 6:
            quarter = 2
        elif month <= 9:
            quarter = 3
        else:
            quarter = 4
        quarter_end_months = {1: "03", 2: "06", 3: "09", 4: "12"}
        quarter_end_days = {1: "31", 2: "30", 3: "30", 4: "31"}
        return f"{year}{quarter_end_months[quarter]}{quarter_end_days[quarter]}"

    def _safe_float(self, value) -> Optional[float]:
        if value is None:
            return None
        try:
            if isinstance(value, str):
                value = value.replace(',', '').replace('万', '').replace('亿', '')
            return float(value)
        except (ValueError, TypeError):
            return None


# 全局服务实例
_financial_data_service = None


async def get_financial_data_service() -> FinancialDataService:
    """获取财务数据服务实例"""
    global _financial_data_service
    if _financial_data_service is None:
        _financial_data_service = FinancialDataService()
    return _financial_data_service
