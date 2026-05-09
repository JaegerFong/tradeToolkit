import pandas as pd


def _make_df(rows):
    return pd.DataFrame(rows)


def test_detect_n_shape_basic():
    from app.services.pattern_detectors.n_shape import detect_n_shape

    # 构造：先涨(10->15)，回调(15->13)，再启动逼近前高(->14.9)
    rows = []
    for i in range(40):
        rows.append(
            {
                "trade_date": f"2026-01-{i+1:02d}",
                "open": 10 + i * 0.05,
                "high": 10 + i * 0.05 + 0.2,
                "low": 10 + i * 0.05 - 0.2,
                "close": 10 + i * 0.05,
                "volume": 1000,
            }
        )
    # 拉高到 15
    for i in range(10):
        rows.append(
            {
                "trade_date": f"2026-02-{i+1:02d}",
                "open": 12 + i * 0.3,
                "high": 12 + i * 0.3 + 0.2,
                "low": 12 + i * 0.3 - 0.2,
                "close": 12 + i * 0.3,
                "volume": 2000,
            }
        )
    # 回调到 13
    for i in range(8):
        rows.append(
            {
                "trade_date": f"2026-03-{i+1:02d}",
                "open": 15 - i * 0.25,
                "high": 15 - i * 0.25 + 0.2,
                "low": 15 - i * 0.25 - 0.2,
                "close": 15 - i * 0.25,
                "volume": 1200,
            }
        )
    # 再启动逼近前高
    for i in range(6):
        rows.append(
            {
                "trade_date": f"2026-04-{i+1:02d}",
                "open": 13 + i * 0.32,
                "high": 13 + i * 0.32 + 0.2,
                "low": 13 + i * 0.32 - 0.2,
                "close": 13 + i * 0.32,
                "volume": 2200,
            }
        )

    df = _make_df(rows)
    cand = detect_n_shape(df, min_up_pct=0.1)
    assert cand is not None
    assert cand.pattern_type == "n_shape"
    assert 0 <= cand.pattern_score <= 100


def test_detect_laoyatou_basic():
    from app.services.pattern_detectors.laoyatou import detect_laoyatou

    rows = []
    # 上升段：10 -> 14，量能较大
    for i in range(40):
        rows.append(
            {
                "trade_date": f"2026-01-{i+1:02d}",
                "open": 10 + i * 0.1,
                "high": 10 + i * 0.1 + 0.2,
                "low": 10 + i * 0.1 - 0.2,
                "close": 10 + i * 0.1,
                "volume": 3000,
            }
        )
    # 整理段：围绕 13.5 小幅波动，缩量
    base = 13.5
    for i in range(20):
        c = base + (0.1 if i % 2 == 0 else -0.1)
        rows.append(
            {
                "trade_date": f"2026-02-{i+1:02d}",
                "open": c,
                "high": c + 0.2,
                "low": c - 0.2,
                "close": c,
                "volume": 1500,
            }
        )
    # 突破：接近平台上沿且放量
    rows.append(
        {
            "trade_date": "2026-03-01",
            "open": 13.7,
            "high": 14.2,
            "low": 13.6,
            "close": 14.15,
            "volume": 4000,
        }
    )

    df = _make_df(rows)
    cand = detect_laoyatou(df, min_up_pct=0.15)
    assert cand is not None
    assert cand.pattern_type == "laoyatou"
    assert 0 <= cand.pattern_score <= 100

