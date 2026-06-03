"""
历史数据服务 - PostgreSQL 版本
K线数据从 tdx2db 的 public schema 表读取（只读）
"""

import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional

from sqlalchemy import select, func, desc

from app.core.database import async_session_factory
from app.core.pg_models import DailyData, Minute5Data, Minute15Data, Minute30Data, Minute60Data, StockInfo

logger = logging.getLogger(__name__)

# 周期 → tdx2db 表映射
PERIOD_TABLE_MAP = {
    "daily": DailyData,
    "minute5": Minute5Data,
    "minute15": Minute15Data,
    "minute30": Minute30Data,
    "minute60": Minute60Data,
}


class HistoricalDataService:
    """历史K线数据查询服务（基于 tdx2db 表）"""

    def __init__(self):
        self._initialized = True

    async def initialize(self):
        pass

    async def get_historical_data(
        self,
        symbol: str,
        start_date: str = None,
        end_date: str = None,
        data_source: str = None,
        period: str = "daily",
        limit: int = None,
    ) -> List[Dict[str, Any]]:
        """
        查询历史K线数据

        Args:
            symbol: 6位股票代码
            start_date: 开始日期
            end_date: 结束日期
            period: daily / minute5 / minute15 / minute30 / minute60
            limit: 返回数量限制
        """
        model = PERIOD_TABLE_MAP.get(period, DailyData)
        code6 = str(symbol).zfill(6)

        async with async_session_factory() as session:
            stmt = select(model).where(model.code == code6)

            if start_date:
                stmt = stmt.where(model.date >= start_date)
            if end_date:
                stmt = stmt.where(model.date <= end_date)

            # 日K按日期排序，分钟数据按 datetime 排序
            if period == "daily":
                stmt = stmt.order_by(model.date.desc())
            else:
                stmt = stmt.order_by(model.datetime.desc())

            if limit:
                stmt = stmt.limit(limit)

            result = await session.execute(stmt)
            rows = result.scalars().all()

            return [self._row_to_dict(row, period) for row in rows]

    async def get_latest_date(self, symbol: str, data_source: str = None) -> Optional[str]:
        """获取最新交易日期"""
        code6 = str(symbol).zfill(6)

        async with async_session_factory() as session:
            stmt = (
                select(DailyData.date)
                .where(DailyData.code == code6)
                .order_by(DailyData.date.desc())
                .limit(1)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row:
                return row.strftime('%Y-%m-%d') if hasattr(row, 'strftime') else str(row)
            return None

    async def get_data_statistics(self) -> Dict[str, Any]:
        """获取数据统计"""
        async with async_session_factory() as session:
            # 日K记录总数
            total = await session.execute(select(func.count()).select_from(DailyData))
            total_count = total.scalar() or 0

            # 股票数量
            symbols = await session.execute(
                select(func.count(func.distinct(DailyData.code)))
            )
            symbol_count = symbols.scalar() or 0

            # 最新日期
            latest = await session.execute(
                select(DailyData.date).order_by(DailyData.date.desc()).limit(1)
            )
            latest_date = latest.scalar_one_or_none()

            return {
                "total_records": total_count,
                "total_symbols": symbol_count,
                "latest_date": latest_date.strftime('%Y-%m-%d') if latest_date and hasattr(latest_date, 'strftime') else str(latest_date) if latest_date else None,
                "by_source": {"tdx2db": {"count": total_count, "latest_date": str(latest_date) if latest_date else None}},
                "by_market": {},
                "last_updated": datetime.utcnow().isoformat(),
            }

    async def get_stock_list(self, market: Optional[int] = None) -> List[Dict[str, Any]]:
        """从 tdx2db stock_info 获取股票列表"""
        async with async_session_factory() as session:
            stmt = select(StockInfo)
            if market is not None:
                stmt = stmt.where(StockInfo.market == market)
            stmt = stmt.order_by(StockInfo.code)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [{"code": r.code, "name": r.name, "market": r.market} for r in rows]

    def _row_to_dict(self, row, period: str = "daily") -> Dict[str, Any]:
        """将 ORM 行转为 dict"""
        date_field = row.date
        if period != "daily" and hasattr(row, 'datetime'):
            date_field = row.datetime

        return {
            "symbol": row.code,
            "code": row.code,
            "market": row.market,
            "trade_date": date_field.strftime('%Y-%m-%d') if hasattr(date_field, 'strftime') else (str(date_field) if date_field else None),
            "datetime": row.datetime.isoformat() if hasattr(row, 'datetime') and row.datetime else (date_field.isoformat() if hasattr(date_field, 'isoformat') else str(date_field)),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.volume,
            "amount": row.amount,
            "change": round(row.close - row.open, 4) if row.close and row.open else None,
            "pct_chg": round((row.close - row.open) / row.open * 100, 4) if row.close and row.open and row.open != 0 else None,
            "ma5": row.ma5,
            "ma10": row.ma10,
            "ma20": row.ma21,
            "ma60": row.ma60,
            "ma250": row.ma250,
            "period": period,
            "data_source": "tdx2db",
        }


_historical_data_service: Optional[HistoricalDataService] = None


async def get_historical_data_service() -> HistoricalDataService:
    global _historical_data_service
    if _historical_data_service is None:
        _historical_data_service = HistoricalDataService()
    return _historical_data_service
