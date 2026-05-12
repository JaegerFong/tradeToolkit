from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from app.models.strategy import StrategyValidationStatus
from app.services.strategy_markdown_parser import StrategyMarkdownParser
from app.services.strong_trend_rule_engine import StrongTrendRuleEngine


def test_strategy_markdown_parser_accepts_strong_trend_doc():
    markdown = open("docs/traders/strong_trend_quant_system.md", encoding="utf-8").read()

    result = StrategyMarkdownParser().parse(markdown)

    assert result.status == StrategyValidationStatus.VALID
    assert result.config.initial_filter["pct_chg_5d_gt"] == 0.13
    assert result.config.trend_confirmation["consecutive_days"] == 3
    assert result.config.quality_score["min_score"] == 3
    assert result.warnings


def test_strategy_markdown_parser_reports_missing_sections():
    result = StrategyMarkdownParser().parse("# 策略\n\n只有一个标题")

    assert result.status == StrategyValidationStatus.INVALID
    assert "每日选股过滤" in result.missing_sections
    assert result.errors


def test_strong_trend_rule_engine_detects_candidate_and_buy_signal():
    start = datetime(2026, 1, 1)
    rows = []
    close = 10.0
    for i in range(55):
        close *= 1.006
        rows.append(
            {
                "trade_date": start + timedelta(days=i),
                "open": close * 0.99,
                "high": close * 1.02,
                "low": close * 0.985,
                "close": close,
                "volume": 1000 + i * 8,
            }
        )

    # Last five days create strong acceleration, volume breakout, and a 120d high.
    for j, pct in enumerate([0.018, 0.022, 0.025, 0.03, 0.07], start=50):
        prev = rows[j - 1]["close"]
        rows[j]["close"] = prev * (1 + pct)
        rows[j]["open"] = rows[j]["close"] * 0.97
        rows[j]["high"] = rows[j]["close"] * 1.01
        rows[j]["low"] = rows[j]["close"] * 0.96
        rows[j]["volume"] = 5000 + j * 100

    parser_result = StrategyMarkdownParser().parse(open("docs/traders/strong_trend_quant_system.md", encoding="utf-8").read())
    engine = StrongTrendRuleEngine()

    evaluation = engine.evaluate(
        "000001",
        "测试股票",
        pd.DataFrame(rows),
        {"code": "000001", "name": "测试股票", "list_date": "20200101"},
        parser_result.config,
    )

    assert evaluation.passed_initial is True
    assert evaluation.trend_confirmed is True
    assert evaluation.result is not None
    assert evaluation.result.total_score > 60
    assert evaluation.result.buy_signals
    assert evaluation.result.stop_loss is not None
