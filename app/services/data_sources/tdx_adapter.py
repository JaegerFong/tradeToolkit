"""
TDX (通达信) 数据源适配器
基于本地通达信 vipdoc 文件的数据适配器, 提供日K/5分钟K线数据
"""
from typing import Optional, Dict
import logging
from datetime import datetime, timedelta
import pandas as pd

from .base import DataSourceAdapter

logger = logging.getLogger(__name__)


class TDXAdapter(DataSourceAdapter):
    """TDX 数据源适配器

    优先级为 1 (低于 AKShare 的 2), 因为:
    - TDX 数据非实时, 依赖用户定期进行盘后数据下载
    - 作为日K/5分钟K线的基础数据源时数据质量高
    """

    def __init__(self):
        super().__init__()

    @property
    def name(self) -> str:
        return "tdx"

    def _get_default_priority(self) -> int:
        return 1

    def is_available(self) -> bool:
        try:
            from tradingagents.dataflows.providers.china.tdx import get_tdx_provider
            provider = get_tdx_provider()
            return provider.connected
        except Exception:
            return False

    def _get_provider(self):
        from tradingagents.dataflows.providers.china.tdx import get_tdx_provider
        return get_tdx_provider()

    def get_stock_list(self) -> Optional[pd.DataFrame]:
        if not self.is_available():
            return None
        try:
            import asyncio
            provider = self._get_provider()
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
            stocks = asyncio.run(provider.get_stock_list())
            if not stocks:
                return None
            df = pd.DataFrame(stocks)
            df = df.rename(columns={"code": "symbol"})
            df["ts_code"] = df["symbol"].apply(lambda c: self._generate_ts_code(str(c)))
            df["market"] = df["symbol"].apply(lambda c: self._get_market(str(c)))
            df["area"] = ""
            df["industry"] = ""
            df["list_date"] = ""
            logger.info(f"TDX: 获取到 {len(df)} 只股票")
            return df
        except Exception as e:
            logger.error(f"TDX 获取股票列表失败: {e}")
            return None

    def get_daily_basic(self, trade_date: str) -> Optional[pd.DataFrame]:
        """TDX 不支持每日基础财务数据"""
        return None

    def get_realtime_quotes(self, source: str = "tdx") -> Optional[Dict[str, Dict[str, Optional[float]]]]:
        """获取全市场最新日K作为行情近似"""
        if not self.is_available():
            return None
        try:
            import asyncio
            provider = self._get_provider()

            async def _fetch():
                stocks = await provider.get_stock_list()
                if not stocks:
                    return {}
                result = {}
                for s in stocks[:500]:  # 限制数量
                    code = str(s.get("code", ""))
                    quotes = await provider.get_stock_quotes(code)
                    if quotes:
                        result[code] = {
                            "close": float(quotes.get("close", 0)),
                            "pct_chg": float(quotes.get("change_percent", 0)),
                            "amount": float(quotes.get("amount", 0)),
                            "volume": float(quotes.get("volume", 0)),
                            "open": float(quotes.get("open", 0)),
                            "high": float(quotes.get("high", 0)),
                            "low": float(quotes.get("low", 0)),
                            "pre_close": 0.0,
                        }
                return result

            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
            result = asyncio.run(_fetch())
            logger.info(f"TDX: 获取到 {len(result)} 只股票行情 (最新日K近似)")
            return result
        except Exception as e:
            logger.error(f"TDX 获取行情失败: {e}")
            return None

    def get_kline(self, code: str, period: str = "day", limit: int = 120, adj: Optional[str] = None):
        """获取 K 线数据"""
        if not self.is_available():
            return None
        try:
            import asyncio
            provider = self._get_provider()
            code6 = str(code).zfill(6)

            async def _fetch():
                end = datetime.now().strftime("%Y-%m-%d")
                start = (datetime.now() - timedelta(days=limit * 2)).strftime("%Y-%m-%d")
                if period in ("day", "daily"):
                    df = await provider.get_historical_data(code6, start, end, "daily")
                elif period in ("5min", "5m", "min5"):
                    df = await provider.get_historical_data(code6, start, end, "5min")
                else:
                    return None
                return df

            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
            df = asyncio.run(_fetch())

            if df is None or df.empty:
                return None

            df = df.tail(limit)
            items = []
            for _, row in df.iterrows():
                items.append({
                    "time": str(row.get("date", "")),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": float(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)),
                })
            return items
        except Exception as e:
            logger.error(f"TDX get_kline {code} 失败: {e}")
            return None

    def find_latest_trade_date(self) -> Optional[str]:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        return yesterday

    def get_news(self, code: str, days: int = 2, limit: int = 50, include_announcements: bool = True):
        """TDX 不支持新闻"""
        return None

    # ── 辅助 ──────────────────────────────────────────────

    @staticmethod
    def _generate_ts_code(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith(("60", "68", "90")):
            return f"{code}.SH"
        elif code.startswith(("00", "30", "20")):
            return f"{code}.SZ"
        elif code.startswith(("8", "4")):
            return f"{code}.BJ"
        return f"{code}.SZ"

    @staticmethod
    def _get_market(code: str) -> str:
        code = str(code).zfill(6)
        if code.startswith("60"):
            return "主板"
        elif code.startswith("00"):
            return "主板"
        elif code.startswith("002"):
            return "中小板"
        elif code.startswith("300"):
            return "创业板"
        elif code.startswith("688"):
            return "科创板"
        elif code.startswith("8"):
            return "北交所"
        return "未知"
