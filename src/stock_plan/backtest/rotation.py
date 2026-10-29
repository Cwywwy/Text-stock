"""RRG 行业轮动回测（任务 C1，行业 ETF 口径）。

只做**行业层**：按 RRG「转强」象限选行业，等权持有，定期调仓；不对个股、不含印花税。
因此本模块的收益**不能**与 `backtest/engine.py` 的股票回测直接对比——引擎是买个股、双边
佣金 + 印花税 + 滑点，这里是买行业指数、只有单边 10bp 的调仓成本。

时点口径（防未来函数）
----------------------
调仓日 `t` 用 **`t` 之前最近一个 RRG 已成形交易日**的象限选行业，`t` 当日按行业指数
收益计入。也就是说信号只用到 `t-1` 收盘前的数据，不存在「当天收盘算完当天开盘买」。

成本口径
--------
每次调仓先算真实换手金额比例 `turnover = 0.5 × Σ|w_new − w_old|`（老权重按当日收益
自然漂移），再按**买卖两边各 10bp** 收费：`cost = turnover × 2 × cost_bps / 10000`。
全仓换一遍 = 20bp，与「单边 10bp」的表述一致。

修订记录：
2026-10-29  (phase 9) 新建：按 RRG「转强」象限做行业等权轮动回测（C1 证伪用）
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stock_plan.backtest.metrics import calc_metrics
from stock_plan.factors.rrg import QUADRANT_IMPROVING, rrg_ready, select_industries

TRADES_COLUMNS: tuple[str, ...] = (
    "code",
    "entry_date",
    "exit_date",
    "entry_price",
    "exit_price",
    "shares",
    "hold_days",
    "pnl",
    "pnl_pct",
    "reason",
)


@dataclass
class RotationResult:
    """行业轮动回测结果。"""

    equity_curve: pd.Series
    benchmark: pd.Series
    holdings: pd.DataFrame
    trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)
    rebalances: int = 0
    total_cost: float = 0.0
    cost_by_year: dict[int, float] = field(default_factory=dict)
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None


def _empty_metrics() -> dict:
    """空结果时也给出与 calc_metrics 同构的零值，避免报告里出现 KeyError。"""
    return {
        "total_return": 0.0,
        "annual_return": 0.0,
        "max_drawdown": 0.0,
        "sharpe": 0.0,
        "win_rate": 0.0,
        "profit_loss": None,
        "trade_count": 0,
        "avg_hold_days": 0.0,
        "avg_pnl_pct": 0.0,
    }


def run_industry_rotation(
    panel_wide: pd.DataFrame,
    rrg: pd.DataFrame,
    *,
    start=None,
    end=None,
    rebalance_days: int = 20,
    top_n: int = 6,
    cost_bps: float = 10.0,
) -> RotationResult:
    """按 RRG「转强」象限做行业等权轮动回测。

    参数：
        panel_wide: 行业指数宽表（index=日期，columns=行业）。
        rrg: `compute_rrg()` 的长表输出（需含 date/industry/rs_ratio/rs_momentum/quadrant）。
        start/end: 回测区间（含端点）；默认取 RRG 首个有效日之后。
        rebalance_days: 每隔多少个交易日调仓一次。
        top_n: 每次最多持有几个行业。
        cost_bps: 单边调仓成本（基点，默认 10 = 0.1%）。

    返回：
        `RotationResult`；`trades` 为按行业合成的持仓记录（code 列放行业名），仅用于
        统计胜率/持仓天数，不代表真实股票成交。
    """
    if panel_wide is None or panel_wide.empty:
        raise ValueError("行业指数为空，无法回测")
    if rebalance_days < 1:
        raise ValueError("rebalance_days 必须 ≥ 1")

    panel = panel_wide.sort_index().astype(float)
    ready = rrg_ready(rrg)
    if ready.empty:
        raise ValueError("RRG 没有成形的行（预热不足），无法回测")

    ready_dates = pd.DatetimeIndex(sorted(ready["date"].unique()))
    first_ready = ready_dates[0]
    lo = pd.Timestamp(start) if start is not None else first_ready
    hi = pd.Timestamp(end) if end is not None else panel.index[-1]
    lo = max(lo, first_ready)
    dates = panel.index[(panel.index >= lo) & (panel.index <= hi)]
    if len(dates) < 2:
        raise ValueError(f"回测区间内交易日不足（{len(dates)} 天）")

    # 信号按日索引：{date: 该日 RRG 长表}
    by_date = {d: frame for d, frame in ready.groupby("date")}
    signal_dates = ready_dates

    def _signal_before(day: pd.Timestamp) -> pd.DataFrame:
        """取 day 之前最近一个 RRG 已成形交易日的截面（严格小于 day，避免未来函数）。"""
        pos = signal_dates.searchsorted(day, side="left") - 1
        if pos < 0:
            return pd.DataFrame()
        return by_date[signal_dates[pos]]

    rets = panel.pct_change()
    benchmark = (1.0 + rets.mean(axis=1, skipna=True).fillna(0.0)).cumprod()
    benchmark = benchmark[(benchmark.index >= dates[0]) & (benchmark.index <= dates[-1])]
    if not benchmark.empty:
        benchmark = benchmark / benchmark.iloc[0]

    weights: dict[str, float] = {}
    open_positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity = 1.0
    curve: list[float] = []
    total_cost = 0.0
    rebalances = 0
    cost_by_year: dict[int, float] = {}
    holdings_rows: list[dict] = []

    for i, day in enumerate(dates):
        do_rebalance = i % int(rebalance_days) == 0
        if do_rebalance:
            day_signal = _signal_before(day)
            picked = select_industries(day_signal, top_n=top_n) if not day_signal.empty else []
            new_weights = {name: 1.0 / len(picked) for name in picked} if picked else {}
            old_weights = dict(weights)
            names = set(old_weights) | set(new_weights)
            turnover = 0.5 * sum(abs(new_weights.get(n, 0.0) - old_weights.get(n, 0.0)) for n in names)
            cost = turnover * 2.0 * float(cost_bps) / 10000.0
            if cost > 0:
                equity *= 1.0 - cost
                total_cost += cost
                year = int(day.year)
                cost_by_year[year] = cost_by_year.get(year, 0.0) + cost
            # 合成交易记录：卖出的行业结算一次，新买入的行业开仓
            for name in sorted(set(old_weights) - set(new_weights)):
                pos = open_positions.pop(name, None)
                if pos is not None:
                    exit_value = panel.at[day, name] if name in panel.columns else np.nan
                    trades.append(_make_trade(name, pos, exit_value, day, "调仓卖出"))
            for name in sorted(set(new_weights) - set(old_weights)):
                open_positions[name] = {
                    "entry_date": day,
                    "entry_value": panel.at[day, name] if name in panel.columns else np.nan,
                }
            weights = new_weights
            rebalances += 1
            for name, weight in sorted(weights.items()):
                holdings_rows.append({"date": day, "industry": name, "weight": weight})

        # 当日行业收益（按调仓后权重等权计入）
        if weights:
            day_ret = 0.0
            for name, weight in weights.items():
                r = rets.at[day, name] if name in rets.columns else 0.0
                if pd.notna(r):
                    day_ret += weight * float(r)
            equity *= 1.0 + day_ret
            # 权重按收益自然漂移，供下次调仓算真实换手
            if 1.0 + day_ret > 0:
                weights = {n: w * (1.0 + (rets.at[day, n] if (n in rets.columns and pd.notna(rets.at[day, n])) else 0.0)) / (1.0 + day_ret) for n, w in weights.items()}
        curve.append(equity)

    equity_curve = pd.Series(curve, index=dates, name="equity")
    # 期末仍持有的行业按最后一天平仓，保证 trades 完整
    for name, pos in sorted(open_positions.items()):
        exit_value = panel.at[dates[-1], name] if name in panel.columns else np.nan
        trades.append(_make_trade(name, pos, exit_value, dates[-1], "回测结束平仓"))

    trades_df = pd.DataFrame(trades, columns=list(TRADES_COLUMNS))
    holdings_df = pd.DataFrame(holdings_rows, columns=["date", "industry", "weight"])
    metrics = calc_metrics(equity_curve, trades_df) if not equity_curve.empty else _empty_metrics()
    if not metrics:
        metrics = _empty_metrics()
    return RotationResult(
        equity_curve=equity_curve,
        benchmark=benchmark,
        holdings=holdings_df,
        trades=trades_df,
        metrics=metrics,
        rebalances=rebalances,
        total_cost=round(total_cost, 6),
        cost_by_year={year: round(value, 6) for year, value in sorted(cost_by_year.items())},
        start=dates[0],
        end=dates[-1],
    )


def _make_trade(industry: str, position: dict, exit_value: float, exit_date, reason: str) -> dict:
    """把一次行业持仓合成为一行交易记录（供 `calc_metrics` 统计胜率与持仓天数）。"""
    entry_value = float(position.get("entry_value", np.nan))
    entry_date = position.get("entry_date")
    pnl_pct = (exit_value / entry_value - 1.0) * 100.0 if entry_value and np.isfinite(entry_value) else 0.0
    hold_days = (pd.Timestamp(exit_date) - pd.Timestamp(entry_date)).days if entry_date is not None else 0
    return {
        "code": industry,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "entry_price": round(entry_value, 6) if np.isfinite(entry_value) else np.nan,
        "exit_price": round(exit_value, 6) if np.isfinite(exit_value) else np.nan,
        "shares": 1.0,
        "hold_days": int(hold_days),
        "pnl": round(float(exit_value - entry_value), 6) if np.isfinite(exit_value) and np.isfinite(entry_value) else 0.0,
        "pnl_pct": round(pnl_pct, 4),
        "reason": reason,
    }


def quadrant_summary(rrg: pd.DataFrame) -> pd.DataFrame:
    """逐月统计四象限行业个数（报告用，帮助判断信号是否过少/过多）。

    返回的是**每个交易日的平均值**，不是按月把所有「行业 × 交易日」行加总，
    否则一个月里天数越多数字越大，无法横向比较。
    """
    ready = rrg_ready(rrg)
    if ready.empty:
        return pd.DataFrame(columns=["month", *sorted({QUADRANT_IMPROVING})])
    frame = ready.copy()
    frame["month"] = pd.to_datetime(frame["date"]).dt.to_period("M").astype(str)
    counts = (
        frame.groupby(["month", "date", "quadrant"], observed=True)["industry"]
        .nunique()
        .reset_index()
    )
    table = counts.pivot_table(index="month", columns="quadrant", values="industry", aggfunc="mean", fill_value=0)
    table = table.round(1)
    return table.reset_index()
