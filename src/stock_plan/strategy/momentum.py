"""动量策略 — 追强势股：近期涨幅 + 均线多头 + 量能配合。

与趋势策略（回调买入）形成对比：动量策略偏好"强者恒强"，
在强势股回调企稳后买入，适合趋势延续行情。

买卖价设计（基于 ATR 波动控制风险）：
- 目标买入价 = 最新收盘价 + atr_k_entry × ATR（突破买入）
- 目标卖出价 = 买入价 + atr_m_exit × ATR（止盈）
- 止损价     = 买入价 - atr_n_stop × ATR（止损）
- 期望持仓天数 = 由参数 hold_days 决定
"""
from __future__ import annotations

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.factors.technical import compute_technical
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.scorer import composite_score, fundamental_composite


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

        # 动量加分：近 N 日涨幅越高分越高（0~100 映射）
        if "mom_ret" in df_factors.columns:
            mom = df_factors["mom_ret"].clip(-0.2, 0.3)  # 限制极端值
            score += (mom + 0.2) / 0.5 * 30  # -20%~+30% 映射到 0~30 分

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


def build_factor_rows(
    codes: list[str], bars_map: dict[str, pd.DataFrame], fund_map: dict[str, dict]
) -> pd.DataFrame:
    """为候选股票构建打分所需的因子行（含动量列）。"""
    rows = []
    for code in codes:
        bars = bars_map.get(code)
        if bars is None or bars.empty:
            continue
        factored = compute_technical(bars)
        if factored.empty:
            continue
        last = factored.iloc[-1]
        fund = fund_map.get(code, {})
        fund_score = fundamental_composite(fund)
        # 近 N 日动量
        mom_period = MomentumStrategy.params["mom_period"]
        mom_ret = 0.0
        if len(factored) > mom_period:
            mom_ret = last["close"] / factored.iloc[-1 - mom_period]["close"] - 1
        rows.append(
            {
                "code": code,
                "close": last["close"],
                "atr14": last["atr14"],
                "ma20": last["ma20"],
                "ma60": last["ma60"],
                "rsi14": last["rsi14"],
                "vol_ratio": last["vol_ratio"],
                "mom_ret": mom_ret,
                "trend_score": last["trend_score"],
                "fund_score": fund_score,
            }
        )
    return pd.DataFrame(rows)


if __name__ == "__main__":
    # 简单自测：用已缓存数据跑一遍完整打分流程
    from stock_plan.data.storage import Storage

    storage = Storage()
    stock_list = storage.load_stock_list()
    bars_map = {}
    for code in stock_list["code"].astype(str).tolist():
        if storage.cache_exists(code):
            bars_map[code] = storage.load_bars(code)
    fund_map = {}
    for code in bars_map:
        fin = storage.load_fundamentals(code)
        if fin:
            fund_map[code] = fin

    strat = MomentumStrategy()
    codes = strat.filter_universe(stock_list, bars_map)
    print(f"通过硬过滤: {len(codes)} 只")
    factor_rows = build_factor_rows(codes, bars_map, fund_map)
    print(f"因子行: {len(factor_rows)} 只")
    if not factor_rows.empty:
        factor_rows["score"] = strat.score(factor_rows)
        top = factor_rows.sort_values("score", ascending=False).head(5)
        print("\nTop 5:")
        print(top[["code", "close", "mom_ret", "trend_score", "fund_score", "score"]].to_string(index=False))