# -*- coding: utf-8 -*-
"""因子列一致性测试（2026-10-15 审计）。

**背景**：回测引擎与「今日信号」各自内联构造候选因子行，两边的列集原先不同，而且是
**双向不一致**：

| 因子列 | 回测引擎 | 今日信号 | 后果 |
|---|---|---|---|
| `mom_ret`、`ma5/7/10/30` | 有 | 无 | 自定义策略的动量下限/动量加分与 dev_ma∈{5,7,10,30} 偏离过滤在**实盘被静默跳过** |
| `avg_amount20`、`macd`、`macd_signal` | 无 | 有 | 流动性下限与 MACD 条件在**回测中被静默跳过** |

因为 `CustomStrategy.score` 全部用 `if "列名" in df_factors.columns` 守卫，列缺失时
**不报错、直接跳过规则** —— 用户看到的是"同一个策略在两个页面选出不同的股票"。

本测试把两条路径的列集锁成一致，防止再漂移。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.builtin import build_factor_rows


def _bars(n: int = 200, seed: int = 0, start: float = 10.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = start + rng.normal(0, 0.15, n).cumsum()
    close = np.maximum(close, 1.0)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-01", periods=n),
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
            "amount": rng.integers(1e8, 5e8, n).astype(float),
            "turnover": 0.01,
        }
    )


def _stock_list(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {"code": codes, "name": codes, "is_st": [0] * len(codes), "board": ["主板"] * len(codes)}
    )


class _ColumnSpy(Strategy):
    """记录 score() 实际收到的因子列，用于比对两条路径的 schema。"""

    name = "列集探针"
    params = {}
    seen: set[str] = set()

    def filter_universe(self, stock_list, bars_map):
        return [c for c in stock_list["code"].astype(str) if c in bars_map]

    def score(self, df_factors):
        type(self).seen |= set(df_factors.columns)
        return pd.Series(1.0, index=df_factors.index)

    def entry_price(self, row, atr):
        return round(row["close"], 2)

    def exit_price(self, entry, atr):
        return entry * 10, entry * 0.01, 9999


def test_backtest_and_live_factor_columns_match() -> None:
    """回测引擎与 build_factor_rows 提供的因子列必须**完全一致**。

    不一致会让自定义策略的规则在一条路径上被静默跳过。
    """
    bars_map = {"600000": _bars(seed=1), "600001": _bars(seed=2, start=20.0)}
    stock_list = _stock_list(["600000", "600001"])

    # 回测路径：让策略把收到的列记下来
    _ColumnSpy.seen = set()
    cfg = BacktestConfig(
        start=bars_map["600000"]["date"].min().date(),
        end=bars_map["600000"]["date"].max().date(),
        top_n=1, max_hold_days=5,
    )
    run_backtest(_ColumnSpy(), cfg, bars_map, {}, stock_list)
    engine_cols = set(_ColumnSpy.seen)
    assert engine_cols, "前提：回测确实调用过 score()"

    live_cols = set(build_factor_rows(["600000", "600001"], bars_map, {}).columns)

    missing_in_live = engine_cols - live_cols
    missing_in_engine = live_cols - engine_cols
    assert not missing_in_live, (
        f"这些列回测有、今日信号没有 → 对应规则在实盘会被静默跳过：{sorted(missing_in_live)}"
    )
    assert not missing_in_engine, (
        f"这些列今日信号有、回测没有 → 对应规则在回测会被静默跳过：{sorted(missing_in_engine)}"
    )


def test_build_factor_rows_includes_custom_strategy_columns() -> None:
    """自定义策略打分依赖的列必须在场（否则规则会被静默跳过）。"""
    bars_map = {"600000": _bars(seed=3)}
    rows = build_factor_rows(["600000"], bars_map, {})
    for col in ("mom_ret", "ma5", "ma7", "ma10", "ma30",
                "avg_amount20", "macd", "macd_signal", "high20_ratio"):
        assert col in rows.columns, f"缺少自定义策略打分依赖的列：{col}"


def test_engine_avg_amount20_is_causal_not_series_tail() -> None:
    """引擎的 avg_amount20 必须是**当日**的滚动均值，不能用整条序列的 tail。

    这是本类 bug 的经典陷阱：`bars["amount"].tail(20)` 在"序列止于现在"的实盘路径里
    是对的，但放进逐日回测就变成未来数据。
    """
    bars = _bars(n=120, seed=4)
    codes = [f"6{i:05d}" for i in range(12)]
    bars_map = {c: bars.copy() for c in codes}
    # 让每只股票最后 20 天的成交额爆炸式放大：若引擎误用 tail，则会看到这个放大值
    for c in codes:
        bars_map[c].loc[bars_map[c].index[-20:], "amount"] = 1e12
    stock_list = _stock_list(codes)

    class AmountSpy(_ColumnSpy):
        amounts: list[float] = []

        def score(self, df_factors):
            type(self).amounts.extend(
                [float(v) for v in df_factors["avg_amount20"].dropna().tolist()]
            )
            return pd.Series(1.0, index=df_factors.index)

    AmountSpy.amounts = []
    cfg = BacktestConfig(
        start=bars["date"].min().date(), end=bars["date"].max().date(),
        rebalance_freq="weekly", top_n=1, max_hold_days=2,
    )
    run_backtest(AmountSpy(), cfg, bars_map, {}, stock_list)

    assert AmountSpy.amounts, "前提：确实取到了 avg_amount20"
    # 早期（未进入最后 20 天）的取值必须远小于 1e12
    early = [a for a in AmountSpy.amounts if a < 1e11]
    assert early, (
        "所有取值都接近 1e12 —— 说明引擎用的是整条序列末尾的成交额（未来数据），"
        "而不是当日滚动均值"
    )


def test_engine_provides_avg_amount20_so_liquidity_rule_applies() -> None:
    """**引擎侧**必须提供 avg_amount20，否则流动性下限规则在回测里被静默跳过。

    注意别测错方向：`avg_amount20` 原本**只在** `build_factor_rows`（实盘路径）里有，
    缺的是**引擎**。所以这条用例走回测、在 score() 里用与 `CustomStrategy` 相同的规则，
    并用 top_n=1 放大排序效果。

    修前：引擎的候选行没有该列 → score() 里取列直接 KeyError → 用例失败（有判别力）。
    """
    base = _bars(n=200, seed=5)
    liquid = base.copy()
    liquid["amount"] = 1e9
    illiquid = base.copy()
    illiquid["amount"] = 1e7
    bars_map = {"600000": liquid, "600001": illiquid}
    stock_list = _stock_list(["600000", "600001"])

    class LiquiditySpy(Strategy):
        name = "流动性探针"
        params = {}

        def filter_universe(self, stock_list, bars_map):
            return [c for c in stock_list["code"].astype(str) if c in bars_map]

        def score(self, df_factors):
            # 与 CustomStrategy 的 liquidity_min 规则一致
            s = pd.Series(0.0, index=df_factors.index)
            s -= (df_factors["avg_amount20"] < 1.0 * 1e8) * 100
            return s

        def entry_price(self, row, atr):
            return round(row["close"], 2)

        def exit_price(self, entry, atr):
            return entry * 10, entry * 0.01, 9999

    cfg = BacktestConfig(
        start=base["date"].min().date(), end=base["date"].max().date(),
        top_n=1, max_hold_days=9999,
    )
    result = run_backtest(LiquiditySpy(), cfg, bars_map, {}, stock_list)
    traded = set(result.trades["code"].astype(str)) if not result.trades.empty else set()
    assert traded == {"600000"}, (
        f"top_n=1 时应选中流动性达标的那只，实际成交 {traded} —— "
        "说明引擎的候选行缺少 avg_amount20（或数值不是当日口径）"
    )


def test_factor_values_match_between_paths() -> None:
    """两条路径不仅列集要一致，**数值也必须一致**。

    两边用不同写法算同一个量，容易差一格：
      mom_ret     引擎 `iloc[p-20]`            实盘 `iloc[-21]`
      high20_ratio 引擎 `rolling(20).max()`    实盘 `iloc[-20:].max()`
      avg_amount20 引擎 `rolling(20).mean()`   实盘 `tail(20).mean()`
    在同一根 K 线上这些都该相等（p 为最后一行时三者窗口完全相同）。
    本用例把"末日终值"逐列比对，任何差一格都会暴露。
    """
    bars_map = {"600000": _bars(n=200, seed=11)}
    stock_list = _stock_list(["600000"])
    fund_map = {"600000": {"value_score": 66.0, "growth_score": 55.0, "quality_score": 77.0}}

    engine_row: dict = {}

    class RowSpy(Strategy):
        name = "数值探针"
        params = {}

        def filter_universe(self, stock_list, bars_map):
            return [c for c in stock_list["code"].astype(str) if c in bars_map]

        def score(self, df_factors):
            self.seen = df_factors[df_factors["code"].astype(str) == "600000"]
            if not self.seen.empty:
                engine_row.clear()
                engine_row.update(self.seen.iloc[-1].to_dict())
            return pd.Series(1.0, index=df_factors.index)

        def entry_price(self, row, atr):
            return round(row["close"], 2)

        def exit_price(self, entry, atr):
            return entry * 10, entry * 0.01, 9999

    cfg = BacktestConfig(
        start=bars_map["600000"]["date"].min().date(),
        end=bars_map["600000"]["date"].max().date(),
        rebalance_freq="daily", top_n=1, max_hold_days=9999,
    )
    run_backtest(RowSpy(), cfg, bars_map, fund_map, stock_list)

    live = build_factor_rows(["600000"], bars_map, fund_map).iloc[0]
    assert engine_row, "前提：回测确实产生了候选行"

    numeric = [
        "close", "atr14", "ma20", "ma60", "rsi14", "vol_ratio", "mom_ret",
        "high20_ratio", "avg_amount20", "macd", "macd_signal",
        "trend_score", "fund_score", "ma5", "ma7", "ma10", "ma30",
        # 均线收敛因子（2026-10-29 新增）：引擎整列预计算 vs 实盘末行计算必须同值。
        # PVCF 在单只股票截面上 z-score 方差为 0 → 两边都应得 0（这条同时锁住该退化口径）。
        "conv_pcf", "conv_vcf", "conv_acf", "conv_trcf", "conv_pvcf",
    ]
    diffs = []
    for col in numeric:
        a, b = float(engine_row[col]), float(live[col])
        if a != pytest.approx(b, rel=1e-9, abs=1e-9):
            diffs.append(f"{col}: 回测={a} 实盘={b}")
    assert not diffs, "两条路径的因子数值不一致：\n  " + "\n  ".join(diffs)