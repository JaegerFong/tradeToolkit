from __future__ import annotations

from typing import List

from app.models.strategy import StrategyRunResult, StrategyRunStats


class StrategyNarrativeService:
    """Generate deterministic fallback narrative; LLM hooks can be added without changing callers."""

    async def build_daily_review(self, results: List[StrategyRunResult], stats: StrategyRunStats) -> str:
        if not results:
            return f"今日扫描{stats.total_scanned}只股票，未发现满足强趋势条件的标的。"
        top = sorted(results, key=lambda item: item.total_score, reverse=True)[:5]
        names = "、".join(f"{item.name}({item.code})" for item in top)
        return (
            f"今日扫描{stats.total_scanned}只股票，初筛{stats.initial_candidates}只，"
            f"强趋势确认{stats.trend_confirmed}只，最终入池{stats.selected_count}只。"
            f"重点关注：{names}。所有结论仅供研究与教育用途。"
        )

    async def build_next_day_plan(self, results: List[StrategyRunResult]) -> str:
        planned = [item for item in results if item.buy_signals]
        exit_watch = [item for item in results if item.sell_signals]
        lines: List[str] = []
        if planned:
            lines.append("买入观察：" + "；".join(f"{x.name}({x.code}) {x.buy_signals[0].name} 止损{x.stop_loss}" for x in planned[:8]))
        if exit_watch:
            lines.append("卖出/降级观察：" + "；".join(f"{x.name}({x.code}) {x.sell_signals[0].name}" for x in exit_watch[:8]))
        if not lines:
            lines.append("明日以观察为主，等待放量突破、首阴低吸或均线回踩信号。")
        lines.append("本计划不构成投资建议，不自动下单。")
        return "\n".join(lines)


def get_strategy_narrative_service() -> StrategyNarrativeService:
    return StrategyNarrativeService()
