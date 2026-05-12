from __future__ import annotations

import re
from typing import Dict, List, Tuple

from app.models.strategy import StrategyConfig, StrategyParseResult, StrategyValidationStatus


class StrategyMarkdownParser:
    """Parse the structured strong-trend strategy Markdown into executable defaults."""

    REQUIRED_SECTIONS: Tuple[Tuple[str, str], ...] = (
        ("initial_filter", "每日选股过滤"),
        ("trend_confirmation", "强趋势形态确认"),
        ("quality_score", "走势气质量化"),
        ("rotation", "产业链轮动选股"),
        ("buy_rules", "买点规则"),
        ("expectation", "买点预期验证"),
        ("sell_rules", "持股与卖出规则"),
        ("position_rules", "仓位管理"),
        ("daily_review", "每日复盘流程"),
    )

    def parse(self, markdown: str) -> StrategyParseResult:
        text = markdown or ""
        errors: List[str] = []
        warnings: List[str] = []
        missing = [label for _, label in self.REQUIRED_SECTIONS if label not in text]

        if missing:
            errors.append(f"缺少必填章节: {', '.join(missing)}")

        config = StrategyConfig(
            name=self._extract_title(text) or "强趋势股量化交易系统",
            initial_filter={
                "pct_chg_5d_gt": self._extract_percent_near(text, "5日涨幅", 0.13),
                "limit_up_count_5d_lte": self._extract_number_near(text, "5日内涨停次数", 2),
                "close_above_ma5": True,
                "volume_gt_ma10": True,
                "listed_days_gt": self._extract_number_near(text, "上市天数", 60),
            },
            trend_confirmation={
                "consecutive_days": 3,
                "close_gte_ma5": True,
                "ma5_ma10_distance_lt": self._extract_percent_near(text, "5/10线贴合度", 0.02),
                "close_ma20_distance_gt": self._extract_percent_near(text, "远离20日线", 0.03),
                "ma_bullish_order": True,
            },
            quality_score={
                "lookback_days": 7,
                "min_score": 3,
                "signals": [
                    "limit_up_fail_not_weak",
                    "engulfing_reversal",
                    "sector_weak_stock_strong",
                    "high_level_sideways",
                    "new_120d_high",
                    "strong_sideways",
                ],
            },
            buy_rules={
                "breakout": {
                    "min_daily_gain": self._extract_percent_near(text, "阳线幅度", 0.06),
                    "volume_ratio_gt": self._extract_multiplier_near(text, "过去5日均量", 1.5),
                    "position": "50%-70%",
                },
                "first_bearish": {"min_drop": 0.02, "position": "30%-50%"},
                "ma_pullback": {"ma": ["MA5", "MA10"], "position": "30%-50%"},
                "first_limit_down": {"min_drop": 0.09, "requires_leader": True, "position": "40%-60%"},
            },
            sell_rules={
                "stagnation_days": 3,
                "fake_breakout_drop": 0.02,
                "top_confirmation_drop": 0.03,
                "break_ma10": True,
                "hard_stop_breakout": self._extract_percent_near(text, "追高买入", 0.08),
                "hard_stop_pullback": self._extract_percent_near(text, "低吸买入", 0.10),
            },
            position_rules={
                "max_single_position": self._extract_percent_near(text, "单票最大仓位", 0.20),
                "max_same_chain": self._extract_number_near(text, "同产业链最多持有", 3),
                "first_entry_position": "50%-70%",
            },
            backtest={"mode": "signal", "default_holding_days": 3},
            optional_enhancements=["sector_strength", "industry_chain", "dragon_tiger", "leader_identification"],
        )
        config.min_listed_days = int(config.initial_filter["listed_days_gt"])

        if "尾盘最后10分钟" in text:
            warnings.append("首版使用日线收盘数据，尾盘最后10分钟规则将作为次日计划提示处理。")
        if "连续60分钟" in text:
            warnings.append("首版不使用分钟线，连续60分钟跌破规则将以日线收盘近似。")
        if "龙虎榜" in text:
            warnings.append("龙虎榜/机构席位作为可选增强数据，缺失时不阻塞运行。")

        return StrategyParseResult(
            status=StrategyValidationStatus.INVALID if errors else StrategyValidationStatus.VALID,
            config=config,
            errors=errors,
            warnings=warnings,
            missing_sections=missing,
        )

    def _extract_title(self, text: str) -> str:
        match = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _extract_percent_near(self, text: str, anchor: str, default: float) -> float:
        value = self._find_near(anchor, text, r"([0-9]+(?:\.[0-9]+)?)\s*%")
        return float(value) / 100 if value is not None else default

    def _extract_number_near(self, text: str, anchor: str, default: int) -> int:
        value = self._find_near(anchor, text, r"([0-9]+(?:\.[0-9]+)?)")
        return int(float(value)) if value is not None else default

    def _extract_multiplier_near(self, text: str, anchor: str, default: float) -> float:
        value = self._find_near(anchor, text, r"[×xX]\s*([0-9]+(?:\.[0-9]+)?)")
        return float(value) if value is not None else default

    def _find_near(self, anchor: str, text: str, pattern: str) -> str | None:
        idx = text.find(anchor)
        if idx < 0:
            return None
        window = text[idx : idx + 240]
        match = re.search(pattern, window)
        return match.group(1) if match else None


def get_strategy_markdown_parser() -> StrategyMarkdownParser:
    return StrategyMarkdownParser()
