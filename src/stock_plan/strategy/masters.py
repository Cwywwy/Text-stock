# -*- coding: utf-8 -*-
"""大师风格量化策略 — 参考段永平/李录投资风格的量化近似。

说明：段永平（商业模式为本，买"好生意"）与李录（长期确定性，
买"可以拿十年的公司"）均为长期基本面投资者，本模块用量化指标
（质量分/成长分/估值分）近似其选股风格，仅作盘前筛选参考，
深度研究请配合「四大师研究」页使用。

注意：本策略为长周期设计（持仓 90 天），与盘前短线策略互补。
"""
from __future__ import annotations

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.strategy.base import Strategy


class DuanYongpingStrategy(Strategy):
    """段永平风格：商业模式为本——高质量（ROE/毛利率/低负债）优先。

    "买股票就是买公司，生意模式最重要。价格合理即可，不贪便宜。"
    量化近似：质量分为主（好生意），成长分次之（商业模式的车道），
    估值仅要求不极端昂贵；技术面权重极低。
    """

    name = "段永平-高质量策略"
    description = """### 🎯 段永平-高质量策略（大师风格近似）

**核心思想**：段永平："买股票就是买公司，生意模式最重要。"
本策略以**质量分为主**（ROE 高、毛利率高、负债率低 = 好生意），
成长分次之，估值只要求不极端昂贵——"价格合理即可，不贪便宜"。

**入选条件**
- 综合分 = 质量分×0.5 + 成长分×0.3 + 估值分×0.2
- 质量分 < 50（生意模式差）：直接排除
- 估值分 < 25（极端高估）：排除（段永平也强调"不要用合理价格买平庸，更不能用昂贵价格买好公司"）
- 技术面权重仅 0.15，ma20>ma60 小幅加分（不做空头排列的票）

**买卖规则（ATR 波动控制）**
- 买入价 = 收盘价 + k×ATR（默认 k=0）
- 卖出价 = 买入价 + m×ATR（默认 m=5.0，长线空间）
- 止损价 = 买入价 - n×ATR（默认 n=3.0）
- 默认持仓 90 天：好生意拿得住

**适用行情**：全周期，熊市中好公司错杀是机会。

**主要风险**：量化指标无法判断商业模式/护城河/企业文化——
请配合「四大师研究」页做定性分析。"""
    params = {
        "atr_k_entry": 0.0,
        "atr_m_exit": 5.0,
        "atr_n_stop": 3.0,
        "hold_days": 90,
        "w_tech": 0.15,
        "w_fund": 0.85,
        "quality_min": 50.0,   # 质量分下限（生意模式差则排除）
        "value_min": 25.0,     # 估值分下限（极端高估排除）
    }

    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        return filter_universe(stock_list, bars_map)

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        has_detail = all(
            c in df_factors.columns
            for c in ("value_score", "quality_score", "growth_score")
        )
        if has_detail:
            fund = (
                df_factors["quality_score"] * 0.5
                + df_factors["growth_score"] * 0.3
                + df_factors["value_score"] * 0.2
            )
        else:
            fund = df_factors["fund_score"].astype(float)

        score = (
            df_factors["trend_score"] * self.params["w_tech"] + fund * self.params["w_fund"]
        ).clip(0, 100)

        if "quality_score" in df_factors.columns:
            score -= (df_factors["quality_score"] < self.params["quality_min"]) * 100
        if "value_score" in df_factors.columns:
            score -= (df_factors["value_score"] < self.params["value_min"]) * 50
        if "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            score += (df_factors["ma20"] > df_factors["ma60"]) * 10

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


class LiLuStrategy(Strategy):
    """李录风格：长期确定性——质量/成长/估值三者均衡，缺一不可。

    "投资是关于确定性的游戏，买入后可以安心持有很久。"
    量化近似：三大基本面分数均衡加权，任一维度过弱（<40）
    都视为"确定性不足"而惩罚；趋势不逆势。
    """

    name = "李录-长期确定性策略"
    description = """### 🌊 李录-长期确定性策略（大师风格近似）

**核心思想**：李录："投资是关于确定性的游戏。"
本策略要求**估值/成长/质量三维度均衡且都不过弱**——任何一
维度明显欠缺都视为"确定性不足"，宁可错过不可错买。

**入选条件**
- 综合分 = 质量分×0.35 + 成长分×0.35 + 估值分×0.30
- 任一维度 < 40（确定性不足）：扣 60 分（三缺一即出局）
- 技术面权重 0.2：ma20>ma60（不逆势）加分，深跌/过热扣分

**买卖规则（ATR 波动控制）**
- 买入价 = 收盘价 + k×ATR（默认 k=0）
- 卖出价 = 买入价 + m×ATR（默认 m=5.0）
- 止损价 = 买入价 - n×ATR（默认 n=2.5）
- 默认持仓 90 天：与长期确定性匹配

**适用行情**：全周期。波动市中"确定性"是最好的防御。

**主要风险**：确定性判断依赖财务历史，无法预知行业剧变——
请配合「四大师研究」页做定性分析。"""
    params = {
        "atr_k_entry": 0.0,
        "atr_m_exit": 5.0,
        "atr_n_stop": 2.5,
        "hold_days": 90,
        "w_tech": 0.2,
        "w_fund": 0.8,
        "dim_min": 40.0,   # 任一维度低于此值视为确定性不足
    }

    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        return filter_universe(stock_list, bars_map)

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        has_detail = all(
            c in df_factors.columns
            for c in ("value_score", "quality_score", "growth_score")
        )
        if has_detail:
            fund = (
                df_factors["quality_score"] * 0.35
                + df_factors["growth_score"] * 0.35
                + df_factors["value_score"] * 0.30
            )
        else:
            fund = df_factors["fund_score"].astype(float)

        score = (
            df_factors["trend_score"] * self.params["w_tech"] + fund * self.params["w_fund"]
        ).clip(0, 100)

        # 确定性：任一维度过弱扣大分
        if has_detail:
            weak = (
                (df_factors["value_score"] < self.params["dim_min"])
                | (df_factors["growth_score"] < self.params["dim_min"])
                | (df_factors["quality_score"] < self.params["dim_min"])
            )
            score -= weak * 60

        if "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            score += (df_factors["ma20"] > df_factors["ma60"]) * 10
        if "ma20" in df_factors.columns and "close" in df_factors.columns:
            dev = df_factors["close"] / df_factors["ma20"] - 1
            score -= (dev > 0.15) * 20
            score -= (dev < -0.15) * 20

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
