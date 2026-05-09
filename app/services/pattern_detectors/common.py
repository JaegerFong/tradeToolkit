from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class PatternCandidate:
    pattern_type: str
    pattern_score: int
    brief_reason: str
    breakdown: Dict[str, str]
    evidence: Dict[str, Any]


def ensure_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    """
    标准化 K 线数据列名，保证包含 open/high/low/close/volume/trade_date。
    """
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    # trade_date 统一为 datetime（便于切片与输出）
    if "trade_date" in out.columns:
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")

    # 兼容不同字段名
    rename_map = {
        "vol": "volume",
        "amount": "amount",
    }
    for k, v in rename_map.items():
        if k in out.columns and v not in out.columns:
            out[v] = out[k]

    required = ["open", "high", "low", "close", "volume", "trade_date"]
    for col in required:
        if col not in out.columns:
            out[col] = None

    out = out.dropna(subset=["trade_date", "close"]).sort_values("trade_date")
    return out


def rolling_ma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def drawdown_from_peak(series: pd.Series) -> pd.Series:
    peak = series.cummax()
    return (series - peak) / peak


def pct_change(a: float, b: float) -> float:
    if a is None or b is None:
        return 0.0
    if a == 0:
        return 0.0
    return (b - a) / a

