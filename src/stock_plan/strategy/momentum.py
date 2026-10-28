"""动量策略 — 追强势股：近期涨幅 + 均线多头 + 量能配合。

与趋势策略（回调买入）形成对比：动量策略偏好"强者恒强"，
在强势股回调企稳后买入，适合趋势延续行情。

买卖价设计（基于 ATR 波动控制风险）：
- 目标买入价 = 最新收盘价 + atr_k_entry × ATR（突破买入）
- 目标卖出价 = 买入价 + atr_m_exit × ATR（止盈）
- 止损价     = 买入价 - atr_n_stop × ATR（止损）
- 期望持仓天数 = 由参数 hold_days 决定

动量口径（2026-10-29 起可切换，默认行为不变）：
- `momentum_mode="ret20"`（默认）：用近 20 日涨幅 `mom_ret`，与旧实现逐值一致；
- `momentum_mode="ma_ratio"`：改用研报口径 `1 − MA_60/close`（越强越大），映射方式与 ret20 相同；
- `momentum_mode="ma_ratio_blend"`：四个窗口（20/60/120/240）的强度各自取截面百分位后平均。
  研报实测 A 股裸涨幅动量 IC 全负，`MA_L/close` 方向相反；但方向修正后是否真能赚钱，以回测为准。

Revision History:
    2026-10-29  (phase 8) add momentum_mode ret20/ma_ratio/ma_ratio_blend, remove duplicate builder
"""
from __future__ import annotations

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.factors.momentum import cross_section_percentile, ma_ratio_strength
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.scorer import composite_score


class MomentumStrategy(Strategy):
    """示例：动量 + 均线多头 + 量能放大。"""

    name = "动量策略"
    description = """### 💪 动量策略

**核心思想**：强者恒强。买近期涨幅领先、趋势向上、量能配合的强势股，
赚趋势延续的钱（与趋势策略的"回调买入"互补）。

**入选条件**
- 近 20 日涨幅（动量）越高分越高：-20%~+30% 线性映射 0~30 加分
- 中期趋势向上：ma20 > ma60（硬性要求）
- 偏离 ma20 超过 10%（追高）：排除
- RSI 超过 80（严重超买）：扣 20 分

**买卖规则（ATR 波动控制）**
- 买入价 = 收盘价 + k×ATR（默认 k=0）
- 卖出价 = 买入价 + m×ATR（默认 m=3.5）
- 止损价 = 买入价 - n×ATR（默认 n=3.5）
- 默认持仓 30 天

**适用行情**：趋势延续、题材主升行情。动量策略在单边牛市表现最佳，
震荡市动量衰减快、容易买在高点。

**主要风险**：动量反转（高位补跌）；需配合止损纪律使用。"""
    params = {
        "mom_period": 20,     # 动量回看周期（近 N 日涨幅）
        "atr_k_entry": 0.0,   # 买入价 = 收盘价 + k×ATR（0 = 收盘价买入）
        "atr_m_exit": 3.5,    # 卖出价 = 买入价 + m×ATR（止盈）
        "atr_n_stop": 3.5,    # 止损价 = 买入价 - n×ATR（止损）
        "hold_days": 30,      # 期望持仓天数
        "w_tech": 0.6,        # 技术面权重
        "w_fund": 0.4,        # 基本面权重
        # 动量口径：ret20（默认，旧行为）/ ma_ratio（MA_60/close 方向修正）/ ma_ratio_blend（四窗口合成）
        "momentum_mode": "ret20",
    }

    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        """硬过滤：复用通用过滤规则（ST/停牌/流动性/次新）。"""
        return filter_universe(stock_list, bars_map)

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        """打分：动量分 × 0.6 + 基本面分 × 0.4，并惩罚超买与追高。

        动量逻辑：
        - 近 N 日涨幅（mom_ret）越高分越高（强者恒强）
        - 中期趋势向上（ma20 > ma60）：不满足则排除
        - 偏离 ma20 超过 10%（追高）：排除
        - RSI 超过 80（严重超买）：扣 20 分
        """
        w_tech = self.params["w_tech"]
        w_fund = self.params["w_fund"]
        score = composite_score(
            df_factors["trend_score"],
            df_factors["fund_score"],
            weights=(w_tech, w_fund, 0.0),
        )

        # 动量加分：口径由 momentum_mode 决定（默认 ret20 = 旧行为，逐值不变）
        bonus = self._momentum_bonus(df_factors)
        if bonus is not None:
            score += bonus

        # 中期趋势向上
        if "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            trend_up = df_factors["ma20"] > df_factors["ma60"]
            score -= (~trend_up) * 100
        # 追高排除
        if "ma20" in df_factors.columns and "close" in df_factors.columns:
            dev = df_factors["close"] / df_factors["ma20"] - 1
            score -= (dev > 0.10) * 100
        # 严重超买
        if "rsi14" in df_factors.columns:
            score -= (df_factors["rsi14"] > 80) * 20

        return score

    def _momentum_bonus(self, df_factors: pd.DataFrame) -> pd.Series | None:
        """按 `momentum_mode` 计算 0~30 分的动量加分；缺列时返回 None（不加分）。

        三种口径的映射都尽量保持同一量纲（0~30 分），保证模式切换只改「用什么衡量动量」，
        不改分数量级：
        - `ret20`：`mom_ret` 裁剪到 [-20%, +30%] 后线性映射（旧实现，逐值不变）；
        - `ma_ratio`：`1 − MA_60/close`（越强越大）同样裁剪映射；NaN（历史不足）按中性 0 处理；
        - `ma_ratio_blend`：四窗口强度各自转截面百分位后平均，×30；NaN 按中性 0.5 处理。
        """
        mode = str(self.params.get("momentum_mode", "ret20"))
        if mode == "ma_ratio":
            strength = ma_ratio_strength(df_factors, 60)
            if strength.isna().all():
                return None
            clipped = strength.clip(-0.2, 0.3).fillna(0.0)
            return (clipped + 0.2) / 0.5 * 30
        if mode == "ma_ratio_blend":
            parts = []
            for window in (20, 60, 120, 240):
                if f"ma_ratio_{window}" not in df_factors.columns:
                    return None
                strength = ma_ratio_strength(df_factors, window)
                parts.append(cross_section_percentile(strength).fillna(0.5))
            return sum(parts) / len(parts) * 30
        # 默认：ret20（旧行为）
        if "mom_ret" not in df_factors.columns:
            return None
        mom = df_factors["mom_ret"].clip(-0.2, 0.3)  # 限制极端值
        return (mom + 0.2) / 0.5 * 30  # -20%~+30% 映射到 0~30 分

    def entry_price(self, row: pd.Series, atr: float) -> float:
        """目标买入价 = 最新收盘价 + k×ATR（突破买入）。"""
        k = self.params["atr_k_entry"]
        return round(row["close"] + k * atr, 2)

    def exit_price(self, entry: float, atr: float) -> tuple[float, float, int]:
        """返回 (目标卖出价, 止损价, 期望持仓天数)。"""
        m = self.params["atr_m_exit"]
        n = self.params["atr_n_stop"]
        exit_price = round(entry + m * atr, 2)
        stop_loss = round(entry - n * atr, 2)
        return exit_price, stop_loss, self.params["hold_days"]
