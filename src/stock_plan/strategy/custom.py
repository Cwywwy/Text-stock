"""自定义策略 — 由可视化策略拼装器（Visual Builder）动态生成。

用户通过 UI 选择因子、权重、阈值，组合成一个可回测的自定义策略。
配置结构（dict）：
{
    "name": "我的策略",
    "weights": {"trend_score": 0.5, "fund_score": 0.3, "rsi14": 0.2},
    "rules": {
        "ma20_gt_ma60": true,          # 中期趋势向上（旧版规则，trend_ma_* 未配置时生效）
        "trend_ma_fast": 0,            # 趋势条件快线周期（如 5；0=未配置）
        "trend_ma_slow": 0,            # 趋势条件慢线周期（如 7；条件为 快线>慢线）
        "dev_ma": 20,                  # 偏离基准均线周期（如仅看 ma20 偏离）
        "rsi_min": 0, "rsi_max": 75,   # RSI 区间
        "dev_min": -5, "dev_max": 5,   # 偏离基准均线区间（%）
        "vol_ratio_max": 3.0,          # 量比上限
        "mom_min": -100,               # 近20日动量下限（%，低于排除；-100 不启用）
        "require_breakout": false,     # 要求接近/创20日新高（high20_ratio >= -2%）
    },
    "params": {"atr_k_entry": 0.0, "atr_m_exit": 3.5, "atr_n_stop": 3.5, "hold_days": 30},
}
"""
from __future__ import annotations

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.builtin import build_factor_rows
from stock_plan.strategy.scorer import composite_score, fundamental_composite

# 可选因子及其方向（higher_better / lower_better）
FACTOR_OPTIONS = {
    "trend_score": "趋势分（越高越好）",
    "fund_score": "基本面分（越高越好）",
    "rsi14": "RSI14（中性区间最佳）",
    "vol_ratio": "量比（越低越好）",
    "mom_ret": "动量（越高越好）",
}


class CustomStrategy(Strategy):
    """可视化拼装的自定义策略。"""

    name = "自定义策略"

    def __init__(self, config: dict):
        self.config = config
        self.name = config.get("name", "自定义策略")
        self.params = {
            "atr_k_entry": config.get("params", {}).get("atr_k_entry", 0.0),
            "atr_m_exit": config.get("params", {}).get("atr_m_exit", 3.5),
            "atr_n_stop": config.get("params", {}).get("atr_n_stop", 3.5),
            "hold_days": config.get("params", {}).get("hold_days", 30),
        }

    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        return filter_universe(stock_list, bars_map)

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        weights = self.config.get("weights", {"trend_score": 0.6, "fund_score": 0.4})
        w_tech = weights.get("trend_score", 0.6)
        w_fund = weights.get("fund_score", 0.4)
        score = composite_score(
            df_factors["trend_score"],
            df_factors["fund_score"],
            weights=(w_tech, w_fund, 0.0),
        )

        rules = self.config.get("rules", {})
        # 趋势条件：优先用自定义均线对（快线>慢线，周期可任选 5/7/10/20/30/60），
        # 未配置时回退旧版 ma20 > ma60
        fast = int(rules.get("trend_ma_fast", 0) or 0)
        slow = int(rules.get("trend_ma_slow", 0) or 0)
        fast_col = df_factors.get(f"ma{fast}") if fast else None
        slow_col = df_factors.get(f"ma{slow}") if slow else None
        if fast_col is not None and slow_col is not None:
            score -= (~(fast_col > slow_col)) * 100
        elif rules.get("ma20_gt_ma60", True) and "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            score -= (~(df_factors["ma20"] > df_factors["ma60"])) * 100
        # 偏离区间：基准均线可选（dev_ma，如仅看 ma20 偏离或 ma10 偏离）
        dev_ma = int(rules.get("dev_ma", 20) or 0)
        dev_col = df_factors.get(f"ma{dev_ma}") if dev_ma else None
        if dev_col is not None and "close" in df_factors.columns:
            dev = (df_factors["close"] / dev_col - 1) * 100
            dev_min = rules.get("dev_min", -5)
            dev_max = rules.get("dev_max", 5)
            score -= (dev > dev_max) * 100
            score -= (dev < dev_min) * 50
        # RSI 区间
        if "rsi14" in df_factors.columns:
            rsi_min = rules.get("rsi_min", 0)
            rsi_max = rules.get("rsi_max", 75)
            score -= (df_factors["rsi14"] > rsi_max) * 20
            score -= (df_factors["rsi14"] < rsi_min) * 20
        # 量比上限
        if "vol_ratio" in df_factors.columns:
            vr_max = rules.get("vol_ratio_max", 3.0)
            score -= (df_factors["vol_ratio"] > vr_max) * 30
        # 动量下限（低于排除）
        mom_min = rules.get("mom_min", -100)
        if "mom_ret" in df_factors.columns and mom_min > -100:
            score -= (df_factors["mom_ret"] * 100 < mom_min) * 100
        # 要求接近/创 20 日新高（突破确认）
        if rules.get("require_breakout", False) and "high20_ratio" in df_factors.columns:
            score -= (df_factors["high20_ratio"] < -0.02) * 100
        # 动量加分
        if "mom_ret" in df_factors.columns:
            w_mom = weights.get("mom_ret", 0.0)
            if w_mom > 0:
                score += df_factors["mom_ret"].rank(pct=True) * w_mom * 100

        return score

    def entry_price(self, row: pd.Series, atr: float) -> float:
        k = self.params["atr_k_entry"]
        return round(row["close"] + k * atr, 2)

    def exit_price(self, entry: float, atr: float) -> tuple[float, float, int]:
        m = self.params["atr_m_exit"]
        n = self.params["atr_n_stop"]
        exit_price = round(entry + m * atr, 2)
        stop_loss = round(entry - n * atr, 2)
        return exit_price, stop_loss, self.params["hold_days"]


def build_custom_factor_rows(
    codes: list[str], bars_map: dict[str, pd.DataFrame], fund_map: dict[str, dict]
) -> pd.DataFrame:
    """为自定义策略构建因子行（含 mom_ret 与自定义均线列 ma5/7/10/30）。"""
    rows = build_factor_rows(codes, bars_map, fund_map)
    if rows.empty:
        return rows
    # 补充 mom_ret（近 20 日涨幅）与自定义均线列（ma20/ma60 已含）
    mom = []
    for code in rows["code"]:
        bars = bars_map.get(code)
        if bars is None or len(bars) < 21:
            mom.append(0.0)
            continue
        mom.append(float(bars["close"].iloc[-1] / bars["close"].iloc[-21] - 1))
    rows["mom_ret"] = mom
    for p in (5, 7, 10, 30):
        rows[f"ma{p}"] = [
            float(bars_map[code]["close"].iloc[-p:].mean())
            if code in bars_map and not bars_map[code].empty
            else float("nan")
            for code in rows["code"]
        ]
    return rows