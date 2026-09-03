# -*- coding: utf-8 -*-
"""价值投资策略 — 低估值 + 高质量优先，技术面仅作买卖时机确认。

核心逻辑：
- 基本面权重高（默认 0.8）：估值分（PE/PB 低）× 0.4 + 质量分（ROE/毛利率）× 0.35 + 成长分 × 0.25
- 估值分 < 40（明显高估）直接排除
- 技术面仅作辅助确认：ma20 > ma60 加分；偏离 ma20 过热（>15%）扣分
- 持仓周期长（默认 60 天），止损宽松（价值波动容忍度高）

买卖价设计（基于 ATR 波动控制风险）：
- 目标买入价 = 最新收盘价 + atr_k_entry × ATR（默认 k=0）
- 目标卖出价 = 买入价 + atr_m_exit × ATR（止盈幅度大）
- 止损价     = 买入价 - atr_n_stop × ATR（止损）
- 期望持仓天数 = 由参数 hold_days 决定

注意：本策略是"量化化的价值筛选"，与真正的深度价值投资（研报级基本面
分析）不同，仅用财务指标打分做粗筛，适合作为盘前观察池的补充。
"""
from __future__ import annotations

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.strategy.base import Strategy


class ValueInvestingStrategy(Strategy):
    """价值投资：低估值 + 高质量优先，技术确认时机。"""

    name = "价值投资策略"
    description = """### 🏛️ 价值投资策略

**核心思想**：以基本面为主（权重 0.8）、技术面为辅（权重 0.2）。买"便宜的好公司"，用技术指标确认买入时机，长周期持有等待价值回归。

**入选条件**
- 综合基本面分 = 估值分×0.4 + 质量分×0.35 + 成长分×0.25
  - 估值分：PE/PB 越低越好（便宜是安全边际）
  - 质量分：ROE 高、毛利率高、负债率低（好生意）
  - 成长分：营收/净利润增速（成长是价值的加速器）
- 估值分 **< 40（明显高估）直接排除**
- 技术面辅助：ma20 > ma60（趋势不逆势）加分；偏离 ma20 > 15%（过热）扣分

**买卖规则（ATR 波动控制）**
- 买入价 = 收盘价 + k×ATR（默认 k=0）
- 卖出价 = 买入价 + m×ATR（默认 m=5.0，给足价值回归空间）
- 止损价 = 买入价 - n×ATR（默认 n=3.0，容忍波动但守住底线）
- 默认持仓 60 天：价值回归需要时间，不追求短期博弈

**适用行情**：全周期。熊市/震荡市是价值策略的收割期（错杀便宜货多）。

**主要风险**：价值陷阱（便宜但基本面持续恶化）；本策略仅用财务指标粗筛，未做商业模式/护城河等深度分析，可在「LLM 智能分析 → 四大师价值分析」中做进一步研究。"""
    params = {
        "atr_k_entry": 0.0,   # 买入价 = 收盘价 + k×ATR
        "atr_m_exit": 5.0,    # 止盈幅度（价值回归空间大）
        "atr_n_stop": 3.0,    # 止损幅度（容忍波动）
        "hold_days": 60,      # 价值回归需要时间
        "w_tech": 0.2,        # 技术面权重（仅作时机确认）
        "w_fund": 0.8,        # 基本面权重
        "value_min": 40.0,    # 估值分下限（低于此直接排除）
    }

    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        return filter_universe(stock_list, bars_map)

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        """打分：估值×0.4 + 质量×0.35 + 成长×0.25，技术面仅作确认。"""
        # 基本面细分打分（缺列时回退到 fund_score）
        fund = df_factors["fund_score"].astype(float)
        has_detail = all(
            c in df_factors.columns
            for c in ("value_score", "quality_score", "growth_score")
        )
        if has_detail:
            fund = (
                df_factors["value_score"] * 0.4
                + df_factors["quality_score"] * 0.35
                + df_factors["growth_score"] * 0.25
            )

        score = (
            df_factors["trend_score"] * self.params["w_tech"] + fund * self.params["w_fund"]
        ).clip(0, 100)

        # 估值过低分（明显高估）直接排除
        if "value_score" in df_factors.columns:
            score -= (df_factors["value_score"] < self.params["value_min"]) * 100

        # 技术面辅助确认
        if "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            score += (df_factors["ma20"] > df_factors["ma60"]) * 10
        if "ma20" in df_factors.columns and "close" in df_factors.columns:
            dev = df_factors["close"] / df_factors["ma20"] - 1
            score -= (dev > 0.15) * 30  # 过热不追
            score -= (dev < -0.15) * 20  # 深跌可能有雷

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
