from __future__ import annotations

from typing import Optional

import pandas as pd

from app.services.pattern_detectors.common import (
    PatternCandidate,
    ensure_ohlcv_frame,
    pct_change,
    rolling_ma,
)


def detect_laoyatou(
    raw_df: pd.DataFrame,
    min_up_pct: float = 0.15,
    max_drawdown: float = 0.50,
    consolidation_volume_ratio: float = 0.75,
    breakout_volume_ratio: float = 1.30,
) -> Optional[PatternCandidate]:
    """
    老鸭头（首期启发式规则）：
    - 上升段：窗口前半段存在显著涨幅（以全窗口低点到高点近似）
    - 整理段：高点后回撤不深、波动收敛、量能缩小
    - 突破：最新收盘逼近/突破整理平台上沿，且量能恢复
    """

    df = ensure_ohlcv_frame(raw_df)
    if df.empty or len(df) < 60:
        return None

    close = df["close"].astype(float)
    vol = df["volume"].fillna(0).astype(float)

    ma5 = rolling_ma(close, 5)
    ma10 = rolling_ma(close, 10)
    ma20 = rolling_ma(close, 20)
    ma60 = rolling_ma(close, 60)

    # 1) 全窗口低点与高点（近似鸭颈到鸭头前）
    low_idx = close.idxmin()
    high_idx = close.loc[low_idx:].idxmax()
    low_price = float(close.loc[low_idx])
    high_price = float(close.loc[high_idx])
    up_pct = pct_change(low_price, high_price)
    if up_pct < min_up_pct:
        return None

    # 2) 整理段：高点后回撤
    post = close.loc[high_idx:]
    if len(post) < 10:
        return None
    head_low_idx = post.idxmin()
    head_low = float(close.loc[head_low_idx])
    if high_price <= 0:
        return None
    drawdown = (high_price - head_low) / high_price
    if drawdown > max_drawdown:
        return None

    # 3) 平台（整理段上沿/下沿）：取高点后最近 20 日区间
    tail = df.tail(20)
    platform_high = float(tail["high"].astype(float).max())
    platform_low = float(tail["low"].astype(float).min())
    last_close = float(close.iloc[-1])

    # 4) 突破：最新价接近/突破平台上沿
    breakout_ok = last_close >= platform_high * 0.99
    if not breakout_ok:
        return None

    # 5) 均线结构：短均线上穿/多头
    if len(df) < 60 or pd.isna(ma20.iloc[-1]) or pd.isna(ma60.iloc[-1]):
        return None
    ma_ok = (ma5.iloc[-1] >= ma10.iloc[-1] >= ma20.iloc[-1]) and (last_close >= ma20.iloc[-1])
    if not ma_ok:
        return None

    # 6) 量能：整理期缩量 + 突破期放量
    cons_slice = df.loc[high_idx:].tail(20)
    rise_slice = df.loc[low_idx:high_idx]

    cons_vol_mean = float(cons_slice["volume"].fillna(0).astype(float).mean()) if len(cons_slice) else 0.0
    rise_vol_mean = float(rise_slice["volume"].fillna(0).astype(float).mean()) if len(rise_slice) else 0.0

    if rise_vol_mean > 0 and cons_vol_mean > rise_vol_mean * consolidation_volume_ratio:
        return None

    breakout_vol = float(vol.iloc[-1])
    if cons_vol_mean > 0 and breakout_vol < cons_vol_mean * breakout_volume_ratio:
        return None

    score = 65
    score += int(min(20, up_pct * 100))
    score += int(max(0, (1 - drawdown) * 15))
    score = max(0, min(100, score))

    low_date = df.loc[low_idx, "trade_date"]
    high_date = df.loc[high_idx, "trade_date"]
    head_low_date = df.loc[head_low_idx, "trade_date"]
    end_date = df["trade_date"].iloc[-1]

    breakdown = {
        "neck": f"{low_date.date()} 至 {high_date.date()}，上升段涨幅 {up_pct*100:.1f}%",
        "head": f"{high_date.date()} 至 {head_low_date.date()}，回撤 {drawdown*100:.1f}%",
        "nose": f"{head_low_date.date()} 至 {end_date.date()}，均线拐头并逼近平台上沿",
        "breakout": f"{end_date.date()}，接近/突破平台上沿，量能恢复",
    }

    brief_reason = "老鸭头结构：强势上升后缩量整理，均线重新多头并在平台附近放量确认"

    evidence = {
        "low_price": low_price,
        "high_price": high_price,
        "up_pct": up_pct,
        "drawdown": drawdown,
        "platform_high": platform_high,
        "platform_low": platform_low,
        "breakout_vol": breakout_vol,
        "cons_vol_mean": cons_vol_mean,
        "rise_vol_mean": rise_vol_mean,
        "ma20": float(ma20.iloc[-1]),
        "ma60": float(ma60.iloc[-1]),
        "last_close": last_close,
    }

    return PatternCandidate(
        pattern_type="laoyatou",
        pattern_score=score,
        brief_reason=brief_reason,
        breakdown=breakdown,
        evidence=evidence,
    )

