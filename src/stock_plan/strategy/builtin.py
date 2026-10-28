"""内置趋势策略 — 均线多头 + MACD 金叉 + 量能放大。

这是 MVP 的第一个示例策略，用于打通"数据 → 因子 → 打分 → 信号"全链路。
后续可在 UI 中复制此策略并调整参数，形成更多自定义策略。

买卖价设计（基于 ATR 波动控制风险）：
- 目标买入价 = 最新收盘价 + atr_k_entry × ATR（突破买入）
- 目标卖出价 = 买入价 + atr_m_exit × ATR（止盈）
- 止损价     = 买入价 - atr_n_stop × ATR（止损）
- 期望持仓天数 = 由参数 hold_days 决定

修订记录：
2026-10-28  (phase 1) 抽出 score_with_penalties 记录逐条扣分明细，score() 数值不变
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.factors.fundamental import compute_fundamental_or_neutral
from stock_plan.factors.technical import compute_technical
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.scorer import composite_score, fundamental_composite

#: 扣分规则：键 → 文案 + 分值。报告层与页面直接引用，避免"分数改了、文案没改"。
#: 每条规则只在这里定义一次，score_with_penalties() 是唯一实现处。
PENALTY_RULES: dict[str, dict[str, Any]] = {
    "trend_down": {"label": "中期趋势向下（ma20 低于 ma60）", "points": -100.0},
    "chase_high": {"label": "偏离 ma20 超过 +10%（追高排除）", "points": -100.0},
    "dev_high": {"label": "偏离 ma20 超过 +5%（偏高，宜等回调）", "points": -30.0},
    "dev_low": {"label": "偏离 ma20 低于 -5%（趋势转弱）", "points": -50.0},
    "rsi_hot": {"label": "RSI14 超过 75（严重超买）", "points": -20.0},
}


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

    def score_with_penalties(
        self, df_factors: pd.DataFrame
    ) -> tuple[pd.Series, pd.DataFrame]:
        """打分，并把每一笔扣分记录成可解释的明细。

        回调买入逻辑（避免追高被套）：
        - 中期趋势向上（ma20 > ma60）：不满足则扣 100（排除）
        - 偏离 ma20 在 -5%~+5%（回调到位）：最佳，不扣分
        - 偏离 ma20 在 +5%~+10%：扣 30 分
        - 偏离 ma20 超过 +10%（追高）：扣 100 分（排除）
        - 偏离 ma20 低于 -5%（趋势转弱）：扣 50 分
        - RSI 超过 75（严重超买）：扣 20 分
        - 注：经回测隔离测试，vol_ratio 与 RSI<40 惩罚会拖累收益，故不启用

        返回：
            (score, penalties)。penalties 为长表，列 code/rule/label/points/basis，
            每只股票每触发一条规则一行；`score()` 取第一个返回值，
            两条路径共用同一份计算，数值不会漂移。
        """
        w_tech = self.params["w_tech"]
        w_fund = self.params["w_fund"]
        score = composite_score(
            df_factors["trend_score"],
            df_factors["fund_score"],
            weights=(w_tech, w_fund, 0.0),
        )

        rows: list[dict[str, Any]] = []
        codes = df_factors["code"].astype(str) if "code" in df_factors.columns else None

        def _log(mask: pd.Series, rule: str, basis: pd.Series) -> None:
            """把命中的行记成扣分明细（basis = 触发该规则时的原始数值）。"""
            for idx in df_factors.index[mask.to_numpy()]:
                raw = basis.loc[idx]
                rows.append(
                    {
                        "code": str(codes.loc[idx]) if codes is not None else str(idx),
                        "rule": rule,
                        "label": PENALTY_RULES[rule]["label"],
                        "points": float(PENALTY_RULES[rule]["points"]),
                        "basis": float(raw) if pd.notna(raw) else None,
                    }
                )

        # 回调买入：只买趋势向上且回调到 ma20 附近的股票
        if "ma20" in df_factors.columns and "ma60" in df_factors.columns:
            trend_up = df_factors["ma20"] > df_factors["ma60"]
            mask = ~trend_up
            score = score - mask * 100
            _log(mask, "trend_down", df_factors["ma20"] / df_factors["ma60"] - 1)
        if "ma20" in df_factors.columns and "close" in df_factors.columns:
            dev = df_factors["close"] / df_factors["ma20"] - 1
            mask = dev > 0.10
            score = score - mask * 100  # 追高，排除
            _log(mask, "chase_high", dev)
            mask = dev > 0.05
            score = score - mask * 30  # 偏高，扣分
            _log(mask, "dev_high", dev)
            mask = dev < -0.05
            score = score - mask * 50  # 趋势转弱，扣分
            _log(mask, "dev_low", dev)
        if "rsi14" in df_factors.columns:
            mask = df_factors["rsi14"] > 75
            score = score - mask * 20  # 严重超买
            _log(mask, "rsi_hot", df_factors["rsi14"])

        penalties = pd.DataFrame(
            rows, columns=["code", "rule", "label", "points", "basis"]
        )
        return score, penalties

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        """打分（旧签名保持不变）：返回综合分，扣分逻辑见 score_with_penalties()。"""
        return self.score_with_penalties(df_factors)[0]

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
        # 技术分：趋势综合分；基本面分：三个基本面分数平均。
        # 2026-10-28 修正：这里原本直接把原始财务 dict 交给 fundamental_composite，
        # 而它读的是 value_score/growth_score/quality_score 三个**算好的键**，读不到就
        # 回退 50 —— 于是"基本面"在今日信号里恒为 50，权重形同虚设。改走
        # compute_fundamental_or_neutral 做一次真正的换算（空数据才给中性 50）。
        fund = compute_fundamental_or_neutral(fund_map.get(code))
        fund_score = fundamental_composite(fund)
        # 近 20 日最高收盘价（供突破策略使用）
        high20 = factored["close"].iloc[-20:].max() if len(factored) >= 20 else last["close"]
        high20_ratio = (last["close"] / high20 - 1) if high20 > 0 else 0.0
        # 近 20 日平均成交额（元，供量价配置的流动性筛选使用）
        avg_amount20 = float(bars["amount"].tail(20).mean()) if "amount" in bars.columns else float("nan")
        # 近 20 日动量与自定义均线（2026-10-15 审计修正）
        # ---------------------------------------------------------------
        # 回测引擎（engine.py）的候选行里带 mom_ret 与 ma5/7/10/30，本函数原先没有，
        # 于是自定义策略的「动量下限 / 动量加分」与 dev_ma∈{5,7,10,30} 的偏离过滤
        # 在**今日信号**里被静默跳过、只在回测里生效 —— 两条路径结果不同。
        # 反之本函数有 avg_amount20/macd/macd_signal 而引擎没有，所以不一致是**双向**的。
        # 现在把两边的因子列集统一，并由 tests/test_factor_parity.py 锁死。
        mom_ret = (
            float(last["close"] / factored["close"].iloc[-21] - 1)
            if len(factored) >= 21
            else 0.0
        )
        ma_cols = {
            f"ma{_p}": float(factored["close"].rolling(_p).mean().iloc[-1])
            for _p in (5, 7, 10, 30)
        }
        rows.append(
            {
                "code": code,
                "close": last["close"],
                "atr14": last["atr14"],
                "ma20": last["ma20"],
                "ma60": last["ma60"],
                "rsi14": last["rsi14"],
                "vol_ratio": last["vol_ratio"],
                "avg_amount20": avg_amount20,
                "high20_ratio": high20_ratio,
                "macd": last["macd"],
                "macd_signal": last["macd_signal"],
                "mom_ret": mom_ret,
                "value_score": fund.get("value_score", 50.0),
                "growth_score": fund.get("growth_score", 50.0),
                "quality_score": fund.get("quality_score", 50.0),
                "trend_score": last["trend_score"],
                "fund_score": fund_score,
                **ma_cols,
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
