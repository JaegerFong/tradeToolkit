from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from app.models.strategy import StrategyConfig, StrategyPoolStatus, StrategyRunResult, StrategySignal


@dataclass
class StrongTrendEvaluation:
    result: Optional[StrategyRunResult]
    passed_initial: bool = False
    trend_confirmed: bool = False
    quality_passed: bool = False


class StrongTrendRuleEngine:
    """Daily-close strong-trend evaluator shared by runs and backtests."""

    def evaluate(
        self,
        code: str,
        name: str,
        daily_rows: List[Dict[str, Any]] | pd.DataFrame,
        basic_info: Optional[Dict[str, Any]],
        config: StrategyConfig,
        as_of_date: Optional[str] = None,
    ) -> StrongTrendEvaluation:
        df = self._prepare_df(daily_rows, as_of_date)
        if len(df) < 30:
            return StrongTrendEvaluation(None)

        missing: List[str] = []
        basic = basic_info or {}
        latest = df.iloc[-1]
        signal_date = self._format_date(latest["trade_date"])

        passed_initial, initial_reasons = self._passes_initial_filter(df, basic, config, missing)
        if not passed_initial:
            return StrongTrendEvaluation(None, passed_initial=False)

        trend_confirmed, trend_reasons = self._trend_confirmed(df, config)
        quality_score, quality_signals = self._quality_signals(df)
        quality_passed = quality_score >= int(config.quality_score.get("min_score", 3))

        buy_signals = self._buy_signals(df, trend_confirmed)
        sell_signals = self._sell_signals(df)

        trend_score = 40.0 if trend_confirmed else 18.0
        quality_component = min(25.0, quality_score / max(float(config.quality_score.get("min_score", 3)), 1.0) * 25.0)
        buy_score = 20.0 if buy_signals else 0.0
        enhancement_score = 0.0
        if "sector_weak_stock_strong" in config.quality_score.get("signals", []):
            missing.append("sector_strength")
        missing.extend(["industry_chain", "dragon_tiger", "leader_identification"])

        known_weight = 85.0
        raw_score = trend_score + quality_component + buy_score + enhancement_score
        total_score = round(min(100.0, raw_score / known_weight * 100.0), 2)

        if buy_signals:
            status = StrategyPoolStatus.PLANNED_BUY
        elif trend_confirmed and quality_passed:
            status = StrategyPoolStatus.WATCHING
        else:
            status = StrategyPoolStatus.CANDIDATE

        close = float(latest["close"])
        stop_loss = self._stop_loss(close, buy_signals, config)
        invalid_conditions = [
            "收盘跌破10日线且次日无法收回",
            "创出新高后次日收盘跌破新高日收盘价2%以上",
            "连续滞涨或趋势评分低于入池阈值",
        ]

        entry_reason = "；".join(initial_reasons + trend_reasons + quality_signals[:3])
        result = StrategyRunResult(
            code=self._normalize_code(code),
            name=name or str(basic.get("name") or ""),
            signal_date=signal_date,
            status=status,
            total_score=total_score,
            trend_score=trend_score,
            quality_score=round(quality_component, 2),
            buy_score=buy_score,
            enhancement_score=enhancement_score,
            close=close,
            pct_chg_5d=round(float(df["close"].iloc[-1] / df["close"].iloc[-6] - 1), 4) if len(df) >= 6 else 0,
            quality_signals=quality_signals,
            buy_signals=buy_signals,
            sell_signals=sell_signals,
            missing_evidence=sorted(set(missing)),
            entry_reason=entry_reason or "满足强趋势策略基础条件",
            review=self._build_review(total_score, trend_confirmed, quality_signals, buy_signals, sell_signals),
            next_day_plan=self._build_plan(close, buy_signals, sell_signals, stop_loss),
            stop_loss=stop_loss,
            invalid_conditions=invalid_conditions,
            evidence={
                "initial_filter": initial_reasons,
                "trend_confirmation": trend_reasons,
                "ma5": float(latest["ma5"]),
                "ma10": float(latest["ma10"]),
                "ma20": float(latest["ma20"]),
                "volume_ma10": float(latest["volume_ma10"]),
            },
        )
        return StrongTrendEvaluation(result, passed_initial, trend_confirmed, quality_passed)

    def _prepare_df(self, rows: List[Dict[str, Any]] | pd.DataFrame, as_of_date: Optional[str]) -> pd.DataFrame:
        df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
        if df.empty:
            return df
        df = df.copy()
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        if as_of_date:
            df = df[df["trade_date"] <= pd.to_datetime(as_of_date)]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"]).sort_values("trade_date")
        df["ma5"] = df["close"].rolling(5).mean()
        df["ma10"] = df["close"].rolling(10).mean()
        df["ma20"] = df["close"].rolling(20).mean()
        df["volume_ma5"] = df["volume"].rolling(5).mean()
        df["volume_ma10"] = df["volume"].rolling(10).mean()
        df["pct_chg"] = df["close"].pct_change()
        return df.dropna(subset=["ma20", "volume_ma10"])

    def _passes_initial_filter(
        self,
        df: pd.DataFrame,
        basic: Dict[str, Any],
        config: StrategyConfig,
        missing: List[str],
    ) -> tuple[bool, List[str]]:
        rules = config.initial_filter
        latest = df.iloc[-1]
        reasons: List[str] = []
        if len(df) < 11:
            return False, reasons

        pct_5d = float(df["close"].iloc[-1] / df["close"].iloc[-6] - 1)
        if pct_5d <= float(rules.get("pct_chg_5d_gt", 0.13)):
            return False, reasons
        reasons.append(f"5日涨幅{pct_5d:.2%}")

        limit_up_count = int((df["pct_chg"].tail(5) >= 0.095).sum())
        if limit_up_count > int(rules.get("limit_up_count_5d_lte", 2)):
            return False, reasons
        reasons.append(f"近5日涨停/近似涨停{limit_up_count}次")

        if float(latest["close"]) < float(latest["ma5"]):
            return False, reasons
        reasons.append("收盘价站上MA5")

        if float(latest.get("volume") or 0) <= float(latest.get("volume_ma10") or 0):
            return False, reasons
        reasons.append("成交量大于10日均量")

        listed_days = self._listed_days(basic, latest["trade_date"])
        if listed_days is None:
            missing.append("listed_days")
        elif listed_days <= int(rules.get("listed_days_gt", 60)):
            return False, reasons
        return True, reasons

    def _trend_confirmed(self, df: pd.DataFrame, config: StrategyConfig) -> tuple[bool, List[str]]:
        rules = config.trend_confirmation
        n = int(rules.get("consecutive_days", 3))
        if len(df) < n:
            return False, []
        recent = df.tail(n)
        checks = (
            (recent["close"] >= recent["ma5"])
            & ((recent["ma5"] - recent["ma10"]).abs() / recent["ma10"] < float(rules.get("ma5_ma10_distance_lt", 0.02)))
            & ((recent["close"] - recent["ma20"]) / recent["ma20"] > float(rules.get("close_ma20_distance_gt", 0.03)))
            & (recent["ma5"] > recent["ma10"])
            & (recent["ma10"] > recent["ma20"])
        )
        if bool(checks.all()):
            return True, [f"连续{n}日满足强趋势确认"]
        return False, []

    def _quality_signals(self, df: pd.DataFrame) -> tuple[int, List[str]]:
        signals: List[str] = []
        recent = df.tail(7)
        if len(recent) < 7:
            return 0, signals

        for i in range(max(1, len(df) - 7), len(df)):
            prev = df.iloc[i - 1]
            cur = df.iloc[i]
            if float(prev["pct_chg"]) >= 0.095 and float(cur["close"]) >= float(prev["close"]) * 0.98:
                signals.append("连板失败不跌")
                break
        for i in range(max(1, len(df) - 7), len(df)):
            prev = df.iloc[i - 1]
            cur = df.iloc[i]
            if float(prev["pct_chg"]) <= -0.02 and float(cur["close"]) > float(cur["open"]) and float(cur["close"]) >= float(prev["open"]):
                signals.append("反包")
                break
        last5 = df.tail(5)
        amplitude_5 = float((last5["high"].max() - last5["low"].min()) / max(last5["low"].min(), 0.01))
        if amplitude_5 < 0.08 and bool((last5["close"] >= last5["ma5"]).all()):
            signals.append("高位横盘不跌")
        if float(df["close"].iloc[-1]) >= float(df["close"].tail(min(120, len(df))).max()):
            signals.append("历史新高")
        if len(df) >= 8:
            before = df.iloc[-8:-3]
            after = df.tail(3)
            pct = float(before["close"].iloc[-1] / before["close"].iloc[0] - 1)
            drawdown = float(after["close"].min() / max(before["close"].iloc[-1], 0.01) - 1)
            if pct > 0.10 and drawdown > -0.03:
                signals.append("强势横盘")
        return len(signals), signals

    def _buy_signals(self, df: pd.DataFrame, trend_confirmed: bool) -> List[StrategySignal]:
        signals: List[StrategySignal] = []
        latest = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else latest
        close = float(latest["close"])
        if trend_confirmed:
            high20 = float(df["close"].tail(20).max())
            high120 = float(df["close"].tail(min(120, len(df))).max())
            if close >= min(high20, high120) and float(latest["pct_chg"]) >= 0.06 and float(latest["volume"]) > float(latest["volume_ma5"]) * 1.5:
                signals.append(StrategySignal(signal_type="breakout", name="追高买法/首阳突破", reason="收盘新高、涨幅达标且放量", confidence="high", suggested_position="计划仓位50%-70%"))

        if trend_confirmed and float(latest["pct_chg"]) <= -0.02 and float(latest["low"]) >= float(latest["ma5"]):
            signals.append(StrategySignal(signal_type="first_bearish", name="低吸买法/首阴", reason="趋势形成后阴线未破MA5", confidence="medium", suggested_position="计划仓位30%-50%"))

        touched_ma = float(latest["low"]) <= float(latest["ma5"]) <= close or float(latest["low"]) <= float(latest["ma10"]) <= close
        if trend_confirmed and touched_ma:
            signals.append(StrategySignal(signal_type="ma_pullback", name="低吸买法/均线回踩", reason="日线触及MA5/MA10后收回", confidence="medium", suggested_position="计划仓位30%-50%"))

        if float(latest["pct_chg"]) <= -0.09 and float(prev["close"]) > float(prev["ma5"]):
            signals.append(StrategySignal(signal_type="first_limit_down", name="低吸买法/第一次跌停", reason="主升浪中首次跌停的日线近似信号，需人工确认板块龙头", confidence="low", suggested_position="计划仓位40%-60%"))
        return signals

    def _sell_signals(self, df: pd.DataFrame) -> List[StrategySignal]:
        signals: List[StrategySignal] = []
        latest = df.iloc[-1]
        if len(df) >= 4 and bool((df["pct_chg"].tail(3).abs() < 0.01).all()):
            signals.append(StrategySignal(signal_type="stagnation", name="连续滞涨", reason="连续3日涨跌幅小于1%", confidence="medium"))
        if len(df) >= 2:
            prev = df.iloc[-2]
            if float(prev["close"]) >= float(df["close"].tail(min(120, len(df))).max()) and float(latest["close"]) < float(prev["close"]) * 0.98:
                signals.append(StrategySignal(signal_type="fake_breakout", name="假突破", reason="新高后次日回落超过2%", confidence="high"))
        if float(latest["close"]) < float(latest["ma10"]):
            signals.append(StrategySignal(signal_type="break_ma10", name="破位", reason="收盘跌破10日线", confidence="high"))
        return signals

    def _stop_loss(self, close: float, buy_signals: List[StrategySignal], config: StrategyConfig) -> float:
        hard_stop = float(config.sell_rules.get("hard_stop_breakout", 0.08))
        if buy_signals and buy_signals[0].signal_type in {"first_bearish", "ma_pullback", "first_limit_down"}:
            hard_stop = float(config.sell_rules.get("hard_stop_pullback", 0.10))
        return round(close * (1 - hard_stop), 3)

    def _build_review(self, score: float, trend: bool, quality: List[str], buys: List[StrategySignal], sells: List[StrategySignal]) -> str:
        parts = [f"综合评分{score:.1f}"]
        parts.append("强趋势已确认" if trend else "趋势仍在候选阶段")
        if quality:
            parts.append("气质信号：" + "、".join(quality))
        if buys:
            parts.append("触发买点：" + "、".join(s.name for s in buys))
        if sells:
            parts.append("风险/卖点：" + "、".join(s.name for s in sells))
        return "；".join(parts)

    def _build_plan(self, close: float, buys: List[StrategySignal], sells: List[StrategySignal], stop_loss: float) -> str:
        if sells:
            return f"次日优先验证卖出/降级信号：{sells[0].name}；若不能快速修复，关注止损位{stop_loss:.2f}。"
        if buys:
            return f"次日关注{buys[0].name}，参考价区间{close * 0.99:.2f}-{close * 1.02:.2f}，止损位{stop_loss:.2f}。"
        return f"次日以观察为主，等待放量突破或均线回踩确认，风控参考{stop_loss:.2f}。"

    def _listed_days(self, basic: Dict[str, Any], current_date: Any) -> Optional[int]:
        raw = basic.get("list_date") or basic.get("listing_date") or basic.get("上市日期")
        if not raw:
            return None
        try:
            cur = pd.to_datetime(current_date).date()
            listed = pd.to_datetime(str(raw)).date()
            return (cur - listed).days
        except Exception:
            return None

    def _format_date(self, value: Any) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return pd.to_datetime(value).date().isoformat()

    def _normalize_code(self, code: str) -> str:
        c = str(code)
        if "." in c:
            return c
        return f"{c}.SZ" if c.startswith(("0", "3")) else f"{c}.SH"


def get_strong_trend_rule_engine() -> StrongTrendRuleEngine:
    return StrongTrendRuleEngine()
