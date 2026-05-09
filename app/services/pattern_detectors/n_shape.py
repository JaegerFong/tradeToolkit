from __future__ import annotations

from typing import Optional

import pandas as pd

from app.services.pattern_detectors.common import (
    PatternCandidate,
    drawdown_from_peak,
    ensure_ohlcv_frame,
    pct_change,
)


def detect_n_shape(
    raw_df: pd.DataFrame,
    min_up_pct: float = 0.10,
    max_drawdown: float = 0.618,
    consolidation_volume_ratio: float = 0.90,
    breakout_volume_ratio: float = 1.10,
) -> Optional[PatternCandidate]:
    """
    N 字形态（首期启发式规则）：
    - 第一段上升：窗口内从阶段低点到阶段高点涨幅 >= min_up_pct
    - 回调：从高点回落，回撤不超过 max_drawdown（按第一段涨幅比例）
    - 第二段启动：最新收盘接近/突破第一段高点，且成交量不弱于回调期均量
    """

    df = ensure_ohlcv_frame(raw_df)
    if df.empty or len(df) < 30:
        return None

    close = df["close"].astype(float)
    vol = df["volume"].fillna(0).astype(float)

    # 1) 找阶段低点与阶段高点（第一段上升）
    low_idx = close.idxmin()
    high_idx = close.loc[low_idx:].idxmax()
    low_price = float(close.loc[low_idx])
    high_price = float(close.loc[high_idx])

    up_pct = pct_change(low_price, high_price)
    if up_pct < min_up_pct:
        return None

    # 2) 回调低点发生在 high_idx 之后
    post = close.loc[high_idx:]
    if len(post) < 5:
        return None
    pullback_low_idx = post.idxmin()
    pullback_low = float(close.loc[pullback_low_idx])

    # 回撤比例（相对于第一段涨幅）
    if high_price <= low_price:
        return None
    pullback_ratio = (high_price - pullback_low) / max(high_price - low_price, 1e-9)
    if pullback_ratio > max_drawdown:
        return None

    # 3) 第二段启动：最新收盘接近/突破前高
    last_close = float(close.iloc[-1])
    breakout_ok = last_close >= (high_price * 0.98)  # 允许略低于前高的“逼近”
    if not breakout_ok:
        return None

    # 4) 量能：回调期均量 vs 启动期
    # 回调期：high_idx -> pullback_low_idx
    pull_slice = df.loc[high_idx:pullback_low_idx]
    pull_vol_mean = float(pull_slice["volume"].fillna(0).astype(float).mean()) if len(pull_slice) else 0.0

    # 启动期：pullback_low_idx -> end
    rise_slice = df.loc[pullback_low_idx:]
    rise_vol_mean = float(rise_slice["volume"].fillna(0).astype(float).mean()) if len(rise_slice) else 0.0

    if pull_vol_mean > 0 and rise_vol_mean < pull_vol_mean * breakout_volume_ratio:
        # 第二段量能不足
        return None

    # scoring（粗略）
    score = 60
    score += int(min(20, up_pct * 100))  # 上升越强越好
    score += int(max(0, (1 - pullback_ratio) * 20))  # 回撤越浅越好
    score = max(0, min(100, score))

    low_date = df.loc[low_idx, "trade_date"]
    high_date = df.loc[high_idx, "trade_date"]
    pull_date = df.loc[pullback_low_idx, "trade_date"]

    breakdown = {
        "leg1": f"{low_date.date()} 至 {high_date.date()}，涨幅 {up_pct*100:.1f}%",
        "pullback": f"{high_date.date()} 至 {pull_date.date()}，回撤 {pullback_ratio*100:.1f}%",
        "leg2": f"{pull_date.date()} 至 {df['trade_date'].iloc[-1].date()}，第二段启动",
    }

    brief_reason = "N 字结构成立：上涨-回调-再启动，回撤可控且接近/突破前高"

    evidence = {
        "low_price": low_price,
        "high_price": high_price,
        "pullback_low": pullback_low,
        "up_pct": up_pct,
        "pullback_ratio": pullback_ratio,
        "pull_vol_mean": pull_vol_mean,
        "rise_vol_mean": rise_vol_mean,
        "last_close": last_close,
    }

    return PatternCandidate(
        pattern_type="n_shape",
        pattern_score=score,
        brief_reason=brief_reason,
        breakdown=breakdown,
        evidence=evidence,
    )

