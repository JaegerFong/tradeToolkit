"""
TDX (通达信) 本地数据提供器
基于 pytdx 读取通达信 vipdoc 目录下的 .day / .lc5 文件

优势:
- 纯本地读取，无网络依赖，不受反爬虫限制
- 数据来源于通达信官方盘后数据下载，质量可靠
- 支持日K线和5分钟K线

前置条件:
- 需要通达信客户端完成"盘后数据下载"
- macOS 下 vipdoc 路径示例: ~/.wine/drive_c/new_tdx/vipdoc
- 需要安装 pytdx: pip install pytdx
"""
import asyncio
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import pandas as pd

from ..base_provider import BaseStockDataProvider

logger = logging.getLogger(__name__)

# A 股代码正则 (深圳 + 上海)
_SZ_A_PATTERN = re.compile(r"^(000|001|002|300)\d{3}$")
_SH_A_PATTERN = re.compile(r"^(60|688)\d{4}$")


def _is_a_stock(code: str) -> bool:
    """判断是否为 A 股代码"""
    return bool(_SZ_A_PATTERN.match(code) or _SH_A_PATTERN.match(code))


def _code_to_market(code: str) -> int:
    """股票代码 → pytdx market 编号 (0=深圳, 1=上海)"""
    if code.startswith(("00", "30")):
        return 0
    elif code.startswith(("60", "68")):
        return 1
    return 0


def _code_to_prefix(code: str) -> str:
    """股票代码 → 文件名前缀 (sz/sh)"""
    if code.startswith(("00", "30")):
        return "sz"
    elif code.startswith(("60", "68")):
        return "sh"
    return "sz"


class TDXProvider(BaseStockDataProvider):
    """
    通达信本地数据提供器

    读取通达信客户端已下载的盘后数据文件 (.day / .lc5),
    提供标准化的股票数据接口。
    """

    def __init__(self, vipdoc_path: Optional[str] = None):
        super().__init__("TDX")
        self._vipdoc_path: Optional[Path] = None
        self._pytdx = None
        self._stock_list_cache: Optional[List[Dict[str, Any]]] = None
        self._cache_time: Optional[datetime] = None
        self._init(vipdoc_path)

    def _init(self, vipdoc_path: Optional[str] = None):
        """初始化 pytdx 并验证 vipdoc 路径"""
        path_str = vipdoc_path or os.getenv("TDX_VIPDOC_PATH", "")
        if not path_str:
            logger.warning("TDX VIPDOC 路径未设置 (TDX_VIPDOC_PATH)，请先配置通达信数据目录")
            self.connected = False
            return

        candidate = Path(path_str).expanduser().resolve()
        if not candidate.exists():
            logger.warning(f"TDX VIPDOC 路径不存在: {candidate}")
            self.connected = False
            return

        self._vipdoc_path = candidate

        try:
            import pytdx.reader as tdx_reader
            self._pytdx = tdx_reader
            self.connected = True
            logger.info(f"TDX 提供器已初始化, vipdoc={candidate}, pytdx 已加载")
        except ImportError:
            logger.error("pytdx 未安装, 请执行: pip install pytdx")
            self.connected = False

    # ── 连接管理 ──────────────────────────────────────────

    async def connect(self) -> bool:
        return await self.test_connection()

    async def test_connection(self) -> bool:
        if not self.connected:
            return False
        # 检查 lday 目录是否存在
        for sub in ["sz/lday", "sh/lday"]:
            d = self._vipdoc_path / sub
            if d.exists() and any(d.glob("*.day")):
                logger.info(f"TDX 连接测试成功, 数据目录存在: {d}")
                return True
        logger.warning("TDX 数据目录为空, 请先在通达信中执行盘后数据下载")
        return False

    # ── 股票列表 ──────────────────────────────────────────

    async def get_stock_list(self) -> List[Dict[str, Any]]:
        """扫描 vipdoc 目录, 获取所有日K数据文件对应的股票列表"""
        if not self.connected:
            return []

        now = datetime.now()
        if self._stock_list_cache is not None and self._cache_time is not None:
            if now - self._cache_time < timedelta(hours=1):
                return self._stock_list_cache

        stocks: List[Dict[str, Any]] = []
        try:
            for market_dir, prefix in [("sz/lday", "sz"), ("sh/lday", "sh")]:
                d = self._vipdoc_path / market_dir
                if not d.exists():
                    continue
                for f in d.glob("*.day"):
                    code = f.stem  # 文件名如 "sz000001.day" → "sz000001"
                    # 提取纯数字代码
                    code_num = code.replace(prefix, "")
                    if _is_a_stock(code_num):
                        stocks.append({
                            "code": code_num,
                            "name": f"股票{code_num}",
                            "source": "tdx",
                            "market": "SH" if prefix == "sh" else "SZ",
                        })

            stocks.sort(key=lambda x: x["code"])
            self._stock_list_cache = stocks
            self._cache_time = now
            logger.info(f"TDX 股票列表扫描完成: {len(stocks)} 只 A 股")
        except Exception as e:
            logger.error(f"TDX 扫描股票列表失败: {e}")

        return stocks

    # ── 基础信息 ──────────────────────────────────────────

    async def get_stock_basic_info(self, code: str) -> Optional[Dict[str, Any]]:
        """获取股票基础信息 (TDX 文件不含行业/地区, 仅返回基本字段)"""
        if not self.connected:
            return None

        code = str(code).zfill(6)
        try:
            return {
                "code": code,
                "name": f"股票{code}",
                "area": "未知",
                "industry": "未知",
                "market": self._determine_market(code),
                "list_date": "",
                "full_symbol": self._get_full_symbol(code),
                "market_info": self._get_market_info(code),
                "data_source": "tdx",
                "last_sync": datetime.now(timezone.utc),
                "sync_status": "success",
            }
        except Exception as e:
            logger.error(f"TDX 获取 {code} 基础信息失败: {e}")
            return None

    # ── 历史数据 (日K) ────────────────────────────────────

    async def get_historical_data(
        self,
        code: str,
        start_date: str,
        end_date: Optional[str] = None,
        period: str = "daily",
    ) -> Optional[pd.DataFrame]:
        """获取历史 K 线数据

        Args:
            code: 股票代码
            start_date: 开始日期 (YYYY-MM-DD)
            end_date: 结束日期 (YYYY-MM-DD), 默认今天
            period: daily / 5min

        Returns:
            标准化 DataFrame, 列为 date/open/high/low/close/volume/amount
        """
        if not self.connected:
            return None

        code = str(code).zfill(6)
        if not _is_a_stock(code):
            logger.warning(f"TDX: {code} 不是 A 股代码")
            return None

        try:
            if period == "daily":
                return await self._read_daily_data(code, start_date, end_date)
            elif period in ("5min", "5m", "min5"):
                return await self._read_minute_data(code, start_date, end_date)
            else:
                logger.warning(f"TDX 不支持的周期: {period}")
                return None
        except Exception as e:
            logger.error(f"TDX 读取 {code} {period} 数据失败: {e}")
            return None

    def _read_daily_data(
        self, code: str, start_date: str, end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """读取日K线数据 (.day 文件)"""
        prefix = _code_to_prefix(code)
        filename = f"{prefix}{code}.day"
        filepath = self._vipdoc_path / prefix / "lday" / filename

        if not filepath.exists():
            # 尝试在另一个市场目录查找 (有些股票代码的文件前缀可能不对)
            alt_prefix = "sh" if prefix == "sz" else "sz"
            alt_filepath = self._vipdoc_path / alt_prefix / "lday" / f"{alt_prefix}{code}.day"
            if alt_filepath.exists():
                filepath = alt_filepath
            else:
                logger.debug(f"TDX 日K文件不存在: {filepath}")
                return None

        try:
            reader = self._pytdx.TdxDailyBarReader()
            df = reader.get_df(str(filepath))

            if df is None or df.empty:
                return None

            # 标准化列名
            df = self._standardize_tdx_columns(df, code)

            # 按日期过滤
            start = pd.Timestamp(start_date)
            if end_date:
                end = pd.Timestamp(end_date)
            else:
                end = pd.Timestamp.now()

            if "date" in df.columns:
                df = df[(df["date"] >= start) & (df["date"] <= end)]

            return df
        except Exception as e:
            logger.error(f"TDX 读取日K文件失败 {filepath}: {e}")
            return None

    def _read_minute_data(
        self, code: str, start_date: str, end_date: Optional[str] = None
    ) -> Optional[pd.DataFrame]:
        """读取5分钟K线数据 (.lc5 文件)"""
        prefix = _code_to_prefix(code)
        filename = f"{prefix}{code}.lc5"
        filepath = self._vipdoc_path / prefix / "fzline" / filename

        if not filepath.exists():
            alt_prefix = "sh" if prefix == "sz" else "sz"
            alt_filepath = self._vipdoc_path / alt_prefix / "fzline" / f"{alt_prefix}{code}.lc5"
            if alt_filepath.exists():
                filepath = alt_filepath
            else:
                logger.debug(f"TDX 5分钟K文件不存在: {filepath}")
                return None

        try:
            reader = self._pytdx.TdxMinBarReader()
            df = reader.get_df(str(filepath))

            if df is None or df.empty:
                return None

            df = self._standardize_tdx_columns(df, code)

            start = pd.Timestamp(start_date)
            if end_date:
                end = pd.Timestamp(end_date)
            else:
                end = pd.Timestamp.now()

            if "date" in df.columns:
                df = df[(df["date"] >= start) & (df["date"] <= end)]

            return df
        except Exception as e:
            logger.error(f"TDX 读取5分钟K文件失败 {filepath}: {e}")
            return None

    def _standardize_tdx_columns(self, df: pd.DataFrame, code: str) -> pd.DataFrame:
        """标准化 pytdx 返回的 DataFrame 列名

        pytdx 返回的列名可能是中文或英文, 统一映射为:
        date, open, high, low, close, volume, amount
        """
        col_map = {
            "date": "date",
            "日期": "date",
            "open": "open",
            "开盘": "open",
            "开盘价": "open",
            "high": "high",
            "最高": "high",
            "最高价": "high",
            "low": "low",
            "最低": "low",
            "最低价": "low",
            "close": "close",
            "收盘": "close",
            "收盘价": "close",
            "volume": "volume",
            "成交量": "volume",
            "amount": "amount",
            "成交额": "amount",
            "成交金额": "amount",
        }

        existing_map = {k: v for k, v in col_map.items() if k in df.columns}
        df = df.rename(columns=existing_map)

        # 确保必要列存在
        for col in ["date", "open", "high", "low", "close", "volume", "amount"]:
            if col not in df.columns:
                df[col] = 0.0

        df["code"] = code
        df["full_symbol"] = self._get_full_symbol(code)
        df["data_source"] = "tdx"

        # 日期标准化
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])

        # 数值类型转换
        for col in ["open", "high", "low", "close", "volume", "amount"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

        return df

    # ── 实时行情 (用最新日K 作为近似) ─────────────────────

    async def get_stock_quotes(self, code: str) -> Optional[Dict[str, Any]]:
        """获取实时行情 (TDX 为离线数据, 返回最新日K 作为近似价格)"""
        if not self.connected:
            return None

        code = str(code).zfill(6)
        try:
            hist = await self.get_historical_data(
                code,
                start_date=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
                period="daily",
            )
            if hist is None or hist.empty:
                return None

            latest = hist.iloc[-1]
            cn_tz = timezone(timedelta(hours=8))
            now_cn = datetime.now(cn_tz)

            return {
                "code": code,
                "symbol": code,
                "name": f"股票{code}",
                "price": float(latest.get("close", 0)),
                "close": float(latest.get("close", 0)),
                "open": float(latest.get("open", 0)),
                "high": float(latest.get("high", 0)),
                "low": float(latest.get("low", 0)),
                "volume": int(latest.get("volume", 0)),
                "amount": float(latest.get("amount", 0)),
                "change": 0.0,
                "change_percent": 0.0,
                "full_symbol": self._get_full_symbol(code),
                "market_info": self._get_market_info(code),
                "data_source": "tdx",
                "quote_source": "tdx_latest_daily",
                "trade_date": str(latest.get("date", now_cn.strftime("%Y-%m-%d"))),
                "updated_at": now_cn.isoformat(),
                "last_sync": datetime.now(timezone.utc),
                "sync_status": "success",
            }
        except Exception as e:
            logger.error(f"TDX 获取 {code} 行情失败: {e}")
            return None

    async def get_batch_stock_quotes(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        """批量获取行情 (遍历每只股票读取最新日K)"""
        result = {}
        for code in codes:
            quotes = await self.get_stock_quotes(code)
            if quotes:
                result[code] = quotes
        return result

    # ── 辅助方法 ──────────────────────────────────────────

    def _determine_market(self, code: str) -> str:
        if code.startswith(("60", "68")):
            return "上海证券交易所"
        elif code.startswith(("00", "30")):
            return "深圳证券交易所"
        return "未知市场"

    def _get_full_symbol(self, code: str) -> str:
        code = str(code).strip().zfill(6)
        if code.startswith(("60", "68", "90")):
            return f"{code}.SS"
        elif code.startswith(("00", "30", "20")):
            return f"{code}.SZ"
        elif code.startswith(("8", "4")):
            return f"{code}.BJ"
        return code

    def _get_market_info(self, code: str) -> Dict[str, Any]:
        if code.startswith(("60", "68")):
            return {
                "market_type": "CN", "exchange": "SSE",
                "exchange_name": "上海证券交易所",
                "currency": "CNY", "timezone": "Asia/Shanghai",
            }
        elif code.startswith(("00", "30")):
            return {
                "market_type": "CN", "exchange": "SZSE",
                "exchange_name": "深圳证券交易所",
                "currency": "CNY", "timezone": "Asia/Shanghai",
            }
        elif code.startswith("8"):
            return {
                "market_type": "CN", "exchange": "BSE",
                "exchange_name": "北京证券交易所",
                "currency": "CNY", "timezone": "Asia/Shanghai",
            }
        return {
            "market_type": "CN", "exchange": "UNKNOWN",
            "exchange_name": "未知交易所",
            "currency": "CNY", "timezone": "Asia/Shanghai",
        }

    @property
    def vipdoc_path(self) -> Optional[Path]:
        return self._vipdoc_path


# ── 全局单例 ─────────────────────────────────────────────

_tdx_provider: Optional[TDXProvider] = None


def get_tdx_provider(vipdoc_path: Optional[str] = None) -> TDXProvider:
    """获取全局 TDX 提供器实例"""
    global _tdx_provider
    if _tdx_provider is None:
        _tdx_provider = TDXProvider(vipdoc_path=vipdoc_path)
    return _tdx_provider
