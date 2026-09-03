# -*- coding: utf-8 -*-
"""突破策略 — 放量突破 20 日新高：强者恒强的右侧买入。

核心逻辑：
- 中期趋势向上（ma20 > ma60），只做多头排列的突破
- 股价接近或创 20 日新高（close/high20 - 1 >= -2%）：突破确认加分
- 量能配合：量比 > 1.2 的放量突破更可信；异常放量（>5 倍）警惕出货
- RSI > 85（过度亢奋）扣分

买卖价设计（基于 ATR 波动控制风险）：
- 目标买入价 = 最新收盘价 + atr_k_entry × ATR（默认 k=0，突破当日收盘介入）
- 目标卖出价 = 买入价 + atr_m_exit × ATR（止盈）
- 止损价     = 买入价 - atr_n_stop × ATR（止损，跌破突破位即离场）
- 期望持仓天数 = 由参数 hold_days 决定
"""
from __future__ import annotations

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.scorer import composite_score


class BreakoutStrategy(Strategy):
    """放量突破：接近/创 20 日新高 + 量能配合 + 均线多头。"""

    name = "突破策略"
    description = """### 🚀 突破策略

**核心思想**：价格突破前期高点并放量，是多头力量集中的信号（"强者恒强"）。做右侧交易，突破确认后介入，止损设在突破位下方。

**入选条件**
- 中期趋势向上：ma20 > ma60（硬性要求，只做多头排列）
- 接近或创 **20 日新高**：close 距 20 日最高价在 **-2% 以内**加分最多
- 放量确认：量比 > 1.2 加分；异常放量（量比 > 5）警惕出货扣分
- RSI > 85（过度亢奋）扣 30 分

**买卖规则（ATR 波动控制）**
- 买入价 = 收盘价 + k×ATR（默认 k=0，突破当日收盘介入；可调高 k 做回踩确认）
- 卖出价 = 买入价 + m×ATR（默认 m=4.0，突破行情空间大）
- 止损价 = 买入价 - n×ATR（默认 n=2.5，跌破突破位快速离场）
- 默认持仓 20 天：突破行情讲究"快进快出"，失效立即止损

**适用行情**：结构性行情、题材轮动市。放量突破在牛市/结构市胜率明显更高。

**主要风险**：假突破（放量诱多）；买在短期顶部；需严格止损纪律配合。"""
    params = {
        "atr_k_entry": 0.0,   # 买入价 = 收盘价 + k×ATR
        "atr_m_exit": 4.0,    # 止盈幅度（突破行情空间大）
        "atr_n_stop": 2.5,    # 止损幅度（假突破快跑）
        "hold_days": 20,      # 突破失效快离场
        "w_tech": 0.7,        # 技术面权重（突破主要看技术）
        "w_fund": 0.3,        # 基本面权重
    }

    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        return filter_universe(stock_list, bars_map)

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        """打分：接近 20 日新高 + 放量 + 均线多头。"""
        score = composite_score(
            df_factors["trend_score"],
            df_factors["fund_score"],
            weights=(self.params["w_tech"], self.params["w_fund"], 0.0),
        )

        # 必须中期趋势向上
        if "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            score -= (~(df_factors["ma20"] > df_factors["ma60"])) * 100

        # 接近/创 20 日新高：ratio 越接近 0（或为正）加分越多
        if "high20_ratio" in df_factors.columns:
            ratio = df_factors["high20_ratio"].clip(-0.2, 0.05)
            near_high = (ratio / 0.05).clip(0, 1)  # -0.05~0.05 映射 0~1
            score += near_high * 30
            score -= (ratio < -0.08) * 30  # 远离高点，突破失败

        # 放量确认：量比 1.2~5 加分，异常放量扣分
        if "vol_ratio" in df_factors.columns:
            vr = df_factors["vol_ratio"]
            score += ((vr > 1.2) & (vr <= 5)) * 15
            score -= (vr > 5) * 30  # 异常放量警惕出货

        # 过度亢奋
        if "rsi14" in df_factors.columns:
            score -= (df_factors["rsi14"] > 85) * 30

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
