# -*- coding: utf-8 -*-
"""均值回归策略 — 买趋势向上的强势股回调：偏离均线深 + RSI 超卖。

核心逻辑：
- 中期趋势向上（ma20 > ma60），只买"强势股的回调"，不接下跌趋势的飞刀
- 股价偏离 ma20 越深（-2% ~ -12% 区间），回归空间越大，加分越高
- RSI 越低（20~45 区间）加分越高；RSI > 65 惩罚
- 偏离超过 -15%（可能基本面恶化）视为"落刀"，直接排除

买卖价设计（基于 ATR 波动控制风险）：
- 目标买入价 = 最新收盘价 + atr_k_entry × ATR（默认 k=0，回调价买入）
- 目标卖出价 = 买入价 + atr_m_exit × ATR（止盈，默认回归到均线附近即止盈）
- 止损价     = 买入价 - atr_n_stop × ATR（止损）
- 期望持仓天数 = 由参数 hold_days 决定（默认较短，回归通常 1-2 周完成）
"""
from __future__ import annotations

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.scorer import composite_score


class MeanReversionStrategy(Strategy):
    """均值回归：趋势向上 + 深度回调 + RSI 超卖。"""

    name = "均值回归策略"
    description = """### 📉 均值回归策略

**核心思想**：价格短期偏离均值后倾向回归。只买**趋势向上的强势股回调**，赚"回归到均线"的钱，不接下跌趋势的飞刀。

**入选条件**
- 中期趋势向上：ma20 > ma60（硬性要求，排除下跌趋势）
- 股价偏离 ma20 在 **-2% ~ -12%**（回调越深，回归空间越大，加分越多）
- RSI14 越低越好（20~45 区间加分，>65 惩罚超买）
- 偏离超过 **-15%** 视为"落刀"（可能基本面恶化），直接排除

**买卖规则（ATR 波动控制）**
- 买入价 = 收盘价 + k×ATR（默认 k=0，回调价挂单）
- 卖出价 = 买入价 + m×ATR（默认 m=2.5，回归到均线附近即止盈）
- 止损价 = 买入价 - n×ATR（默认 n=2.0）
- 持仓周期短（默认 15 天）：均值回归通常 1-2 周完成，超时未回归则离场

**适用行情**：震荡上行市。单边下跌市会连续触发止损，趋势单边市机会少。

**主要风险**：回调可能是趋势反转的开始；偏离过深的股票往往有利空，需结合消息面判断。"""
    params = {
        "atr_k_entry": 0.0,   # 买入价 = 收盘价 + k×ATR
        "atr_m_exit": 2.5,    # 止盈幅度（回归行情空间有限）
        "atr_n_stop": 2.0,    # 止损幅度
        "hold_days": 15,      # 回归通常 1-2 周完成
        "w_tech": 0.4,        # 技术面权重（回调时技术分天然偏低）
        "w_fund": 0.6,        # 基本面权重（优质股回调才值得买）
        "dev_min": -0.12,     # 偏离下限（越深加分越多）
        "dev_exclude": -0.15, # 偏离低于此值视为落刀，排除
    }

    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        return filter_universe(stock_list, bars_map)

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        """打分：偏离越深 + RSI 越低加分，趋势向下/落刀排除。"""
        score = composite_score(
            df_factors["trend_score"],
            df_factors["fund_score"],
            weights=(self.params["w_tech"], self.params["w_fund"], 0.0),
        )

        # 必须中期趋势向上（强势股回调，不接下跌趋势的飞刀）
        if "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            score -= (~(df_factors["ma20"] > df_factors["ma60"])) * 100

        if "ma20" in df_factors.columns and "close" in df_factors.columns:
            dev = df_factors["close"] / df_factors["ma20"] - 1
            # 落刀排除
            score -= (dev < self.params["dev_exclude"]) * 100
            # 回调越深加分越多：dev 在 [dev_min, -0.02] 区间线性映射 0~30 分
            in_zone = (dev <= -0.02) & (dev >= self.params["dev_min"])
            depth = ((-dev) - 0.02) / max(0.01, -self.params["dev_min"] - 0.02)
            score += in_zone * depth.clip(0, 1) * 30

        # RSI 越低越好（超卖回归）
        if "rsi14" in df_factors.columns:
            rsi = df_factors["rsi14"]
            oversold = ((45 - rsi) / 25).clip(0, 1)  # 20~45 映射 0~1
            score += ((rsi < 45) & (rsi > 0)) * oversold * 20
            score -= (rsi > 65) * 20  # 超买不回归

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
