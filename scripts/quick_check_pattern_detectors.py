"""
快速自检：技术形态检测器（不依赖 pytest）
"""

from pathlib import Path
import sys

import pandas as pd

# 确保可从项目根目录导入 app/*
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root))


def _make_df(rows):
    return pd.DataFrame(rows)


def main():
    from app.services.pattern_detectors.laoyatou import detect_laoyatou
    from app.services.pattern_detectors.n_shape import detect_n_shape

    # N 字：先涨到 15，再回调到 13，再启动逼近前高
    rows = []
    for i in range(30):
        c = 10 + i * 0.1
        rows.append({"trade_date": f"2026-01-{i+1:02d}", "open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1200})
    for i in range(8):
        c = 13 + i * 0.25  # 13 -> 14.75（形成前高）
        rows.append({"trade_date": f"2026-02-{i+1:02d}", "open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 2200})
    for i in range(8):
        c = 14.75 - i * 0.22  # 回调到 ~13
        rows.append({"trade_date": f"2026-03-{i+1:02d}", "open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 1200})
    for i in range(8):
        c = 13 + i * 0.24  # 再启动到 ~14.68，逼近前高
        rows.append({"trade_date": f"2026-04-{i+1:02d}", "open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 2400})
    df = _make_df(rows)
    cand = detect_n_shape(df)
    assert cand is not None and cand.pattern_type == "n_shape"

    # 老鸭头：上升段(60日内显著上涨) + 缩量整理 + 放量突破
    rows = []
    dates = pd.date_range("2026-05-01", periods=66, freq="D")
    # 上升 45 天：10 -> 15.0（量能偏大；确保峰值早于突破日）
    for i in range(45):
        c = 10 + i * (5.0 / 44.0)
        rows.append({"trade_date": str(dates[i].date()), "open": c, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 3500})
    # 整理 20 天：围绕 14.6 波动（缩量，且低于峰值）
    base = 14.6
    for i in range(20):
        c = base + (0.08 if i % 2 == 0 else -0.08)
        rows.append({"trade_date": str(dates[45 + i].date()), "open": c, "high": c + 0.18, "low": c - 0.18, "close": c, "volume": 1600})
    # 突破 1 天：收盘接近/突破平台上沿（放量），且不超过峰值
    rows.append({"trade_date": str(dates[65].date()), "open": 14.7, "high": 14.98, "low": 14.65, "close": 14.95, "volume": 5200})
    df = _make_df(rows)
    cand = detect_laoyatou(df)
    assert cand is not None and cand.pattern_type == "laoyatou"

    print("OK: pattern detectors quick check passed")


if __name__ == "__main__":
    main()

