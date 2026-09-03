"""Walk-Forward 滚动验证模块 — 用滚动窗口验证策略的样本外表现。

原理：
1. 把回测区间切成多个"训练段 + 测试段"窗口
2. 每个窗口：在训练段做参数寻优，用最优参数在测试段回测（样本外）
3. 汇总所有测试段结果，得到策略的真实样本外表现

相比单次回测，Walk-Forward 能避免"过拟合历史"的假象。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.strategy.base import Strategy


@dataclass
class WalkForwardResult:
    """Walk-Forward 结果。"""

    windows: list[dict]       # 每个窗口的结果
    oos_metrics: dict         # 样本外汇总指标
    oos_equity: pd.Series     # 样本外资金曲线（拼接）
    oos_trades: pd.DataFrame  # 样本外交易明细（拼接）


def walk_forward(
    strategy_factory,
    bars_map: dict[str, pd.DataFrame],
    fund_map: dict[str, dict],
    stock_list: pd.DataFrame,
    start: date,
    end: date,
    train_days: int = 180,
    test_days: int = 90,
    param_grid: list[dict] | None = None,
    base_config: BacktestConfig | None = None,
) -> WalkForwardResult:
    """执行 Walk-Forward 滚动验证。

    参数：
        strategy_factory: 无参函数，返回新的策略实例（每次窗口用新实例避免状态污染）。
        bars_map:         {code: 日线 DataFrame}。
        fund_map:         {code: 财务指标 dict}。
        stock_list:       全 A 股列表。
        start/end:        回测总区间。
        train_days:       训练段长度（自然日）。
        test_days:        测试段长度（自然日）。
        param_grid:       训练段参数寻优的候选参数列表（每项是 params dict）。
        base_config:      基础回测配置（不含 start/end，会按窗口覆盖）。

    返回：
        WalkForwardResult（窗口结果 + 样本外汇总）。
    """
    if param_grid is None:
        param_grid = [
            {"atr_m_exit": 3.0, "atr_n_stop": 3.5, "hold_days": 30},
            {"atr_m_exit": 3.5, "atr_n_stop": 3.5, "hold_days": 30},
            {"atr_m_exit": 3.5, "atr_n_stop": 4.0, "hold_days": 30},
            {"atr_m_exit": 4.0, "atr_n_stop": 3.5, "hold_days": 30},
        ]
    if base_config is None:
        base_config = BacktestConfig(
            start=start, end=end, rebalance_freq="weekly", market_timing=True
        )

    windows: list[dict] = []
    oos_equity_parts: list[pd.Series] = []
    oos_trades_parts: list[pd.DataFrame] = []

    cursor = start
    while cursor + timedelta(days=train_days + test_days) <= end:
        train_start = cursor
        train_end = cursor + timedelta(days=train_days)
        test_start = train_end
        test_end = min(test_start + timedelta(days=test_days), end)

        # ---- 训练段：参数寻优 ----
        best_params, best_ret = None, float("-inf")
        for params in param_grid:
            strat = strategy_factory()
            strat.params = {**strat.params, **params}
            cfg = BacktestConfig(
                start=train_start, end=train_end,
                rebalance_freq=base_config.rebalance_freq,
                market_timing=base_config.market_timing,
                max_hold_days=params.get("hold_days", base_config.max_hold_days),
            )
            result = run_backtest(strat, cfg, bars_map, fund_map, stock_list)
            metrics = calc_metrics(result.equity_curve, result.trades)
            if metrics and metrics["total_return"] > best_ret:
                best_ret = metrics["total_return"]
                best_params = params

        # ---- 测试段：用最优参数做样本外回测 ----
        strat = strategy_factory()
        if best_params:
            strat.params = {**strat.params, **best_params}
        cfg = BacktestConfig(
            start=test_start, end=test_end,
            rebalance_freq=base_config.rebalance_freq,
            market_timing=base_config.market_timing,
            max_hold_days=(best_params or {}).get("hold_days", base_config.max_hold_days),
        )
        result = run_backtest(strat, cfg, bars_map, fund_map, stock_list)
        metrics = calc_metrics(result.equity_curve, result.trades)

        windows.append(
            {
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "best_params": best_params,
                "train_return": best_ret,
                "oos_return": metrics.get("total_return", 0) if metrics else 0,
                "oos_drawdown": metrics.get("max_drawdown", 0) if metrics else 0,
                "oos_win_rate": metrics.get("win_rate", 0) if metrics else 0,
                "oos_trades": metrics.get("trade_count", 0) if metrics else 0,
            }
        )
        if not result.equity_curve.empty:
            oos_equity_parts.append(result.equity_curve)
        if result.trades is not None and not result.trades.empty:
            oos_trades_parts.append(result.trades)

        cursor = test_end

    # ---- 汇总样本外结果 ----
    oos_equity = pd.concat(oos_equity_parts).sort_index() if oos_equity_parts else pd.Series(dtype=float)
    oos_trades = pd.concat(oos_trades_parts, ignore_index=True) if oos_trades_parts else pd.DataFrame()
    oos_metrics = calc_metrics(oos_equity, oos_trades) or {}

    return WalkForwardResult(
        windows=windows,
        oos_metrics=oos_metrics,
        oos_equity=oos_equity,
        oos_trades=oos_trades,
    )


if __name__ == "__main__":
    # 简单自测：用已缓存数据跑一次 Walk-Forward
    from stock_plan.data.storage import Storage
    from stock_plan.strategy.builtin import TrendFollowingStrategy

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

    end = date.today()
    start = end - timedelta(days=365)
    result = walk_forward(
        lambda: TrendFollowingStrategy(),
        bars_map, fund_map, stock_list,
        start, end,
        train_days=150, test_days=90,
    )
    print(f"窗口数: {len(result.windows)}")
    for w in result.windows:
        print(f"  {w['train_start']}~{w['train_end']} | 测试 {w['test_start']}~{w['test_end']} "
              f"| 最优参数 {w['best_params']} | 训练 {w['train_return']:.2f}% | 样本外 {w['oos_return']:.2f}%")
    print(f"样本外汇总: {result.oos_metrics}")