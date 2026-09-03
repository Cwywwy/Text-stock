"""内置趋势策略 — 均线多头 + MACD 金叉 + 量能放大。

这是 MVP 的第一个示例策略，用于打通"数据 → 因子 → 打分 → 信号"全链路。
后续可在 UI 中复制此策略并调整参数，形成更多自定义策略。

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


class TrendFollowingStrategy(Strategy):
    """示例：均线多头 + MACD 金叉 + 量能放大。"""

    name = "趋势跟随策略"
    description = """### 📈 趋势跟随策略

**核心思想**：跟随中期趋势，买"强势股回调到位"的位置，不追高、不逆势。

**入选条件**
- 中期趋势向上：ma20 > ma60（均线多头，硬性要求）
- 技术面强：均线多头 + MACD 金叉 + 量能放大（趋势综合分）
- 回调买入：偏离 ma20 在 -5%~+5% 最佳；偏离 > 10%（追高）直接排除
- RSI > 75（严重超买）扣分

**买卖规则（ATR 波动控制）**
- 买入价 = 收盘价 + k×ATR（默认 k=0，回调价介入）
- 卖出价 = 买入价 + m×ATR（默认 m=3.5，止盈）
- 止损价 = 买入价 - n×ATR（默认 n=3.5，止损与止盈对称）
- 默认持仓 30 天（中线），容忍回调

**适用行情**：单边上行或震荡上行市。经历史回测验证（+9.37%/回撤-13.22%），
为本系统默认推荐策略。

**主要风险**：震荡市反复止损；趋势反转初期反应滞后。"""
    params = {
        "ma_period": 20,      # 主均线周期
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
        """打分：技术分 × 0.6 + 基本面分 × 0.4，并惩罚超买与追高。

        参数：
            df_factors: 每只股票一行，含 trend_score/fund_score/close/ma20/ma60/rsi14 列。

        回调买入逻辑（避免追高被套）：
        - 中期趋势向上（ma20 > ma60）：不满足则排除
        - 偏离 ma20 在 -5%~+5%（回调到位）：最佳，不扣分
        - 偏离 ma20 在 5%~10%：扣 30 分
        - 偏离 ma20 超过 10%（追高）：排除
        - 偏离 ma20 低于 -5%（趋势转弱）：扣 50 分
        - RSI 超过 75（严重超买）：扣 20 分
        - 注：经回测隔离测试，vol_ratio 与 RSI<40 惩罚会拖累收益，故不启用
        """
        w_tech = self.params["w_tech"]
        w_fund = self.params["w_fund"]
        score = composite_score(
            df_factors["trend_score"],
            df_factors["fund_score"],
            weights=(w_tech, w_fund, 0.0),
        )

        # 回调买入：只买趋势向上且回调到 ma20 附近的股票
        if "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            trend_up = df_factors["ma20"] > df_factors["ma60"]
            score -= (~trend_up) * 100
        if "ma20" in df_factors.columns and "close" in df_factors.columns:
            dev = df_factors["close"] / df_factors["ma20"] - 1
            score -= (dev > 0.10) * 100   # 追高，排除
            score -= (dev > 0.05) * 30    # 偏高，扣分
            score -= (dev < -0.05) * 50   # 趋势转弱，扣分
        if "rsi14" in df_factors.columns:
            score -= (df_factors["rsi14"] > 75) * 20   # 严重超买

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
    """为候选股票构建打分所需的因子行。

    参数：
        codes:    候选股票代码列表（已通过硬过滤）。
        bars_map: {code: 日线 DataFrame}。
        fund_map: {code: 财务指标 dict}。

    返回：
        DataFrame，每只股票一行，含 code/close/atr14/trend_score/fund_score。
    """
    rows = []
    for code in codes:
        bars = bars_map.get(code)
        if bars is None or bars.empty:
            continue
        factored = compute_technical(bars)
        if factored.empty:
            continue
        last = factored.iloc[-1]
        # 技术分：趋势综合分；基本面分：三个基本面分数平均
        fund = fund_map.get(code, {})
        fund_score = fundamental_composite(fund)
        # 近 20 日最高收盘价（供突破策略使用）
        high20 = factored["close"].iloc[-20:].max() if len(factored) >= 20 else last["close"]
        high20_ratio = (last["close"] / high20 - 1) if high20 > 0 else 0.0
        rows.append(
            {
                "code": code,
                "close": last["close"],
                "atr14": last["atr14"],
                "ma20": last["ma20"],
                "ma60": last["ma60"],
                "rsi14": last["rsi14"],
                "vol_ratio": last["vol_ratio"],
                "high20_ratio": high20_ratio,
                "value_score": fund.get("value_score", 50.0),
                "growth_score": fund.get("growth_score", 50.0),
                "quality_score": fund.get("quality_score", 50.0),
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

    strat = TrendFollowingStrategy()
    codes = strat.filter_universe(stock_list, bars_map)
    print(f"通过硬过滤: {len(codes)} 只")
    factor_rows = build_factor_rows(codes, bars_map, fund_map)
    print(f"因子行: {len(factor_rows)} 只")
    if not factor_rows.empty:
        factor_rows["score"] = strat.score(factor_rows)
        top = factor_rows.sort_values("score", ascending=False).head(5)
        print("\nTop 5:")
        print(top[["code", "close", "atr14", "trend_score", "fund_score", "score"]].to_string(index=False))
