# -*- coding: utf-8 -*-
"""walk-forward 复用只读准备层（Phase 15b）的对拍测试。

为什么需要：walk_forward 每个窗口都要重算全区间因子表（每个训练参数一次、测试段一次），
Phase 15b 改为「整段行情 prepare_backtest 一次，各窗口 run_backtest 复用」。本文件用
合成行情对拍「不传 prepared（逐次重建因子表）」与「传 prepared（复用）」两条路径，
要求 windows / oos_metrics / OOS 净值 / OOS 交易逐值一致。

15b 代码未落地时（walk_forward 尚无 prepared 参数）本文件自动 skip，避免拖红 Phase 15 提交。

Revision History:
    2026-10-29  (phase 15b) add walk-forward prepared reuse parity test
"""
from __future__ import annotations

import inspect
from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_plan.backtest.engine import BacktestConfig, prepare_backtest
from stock_plan.backtest.walkforward import walk_forward

_SUPPORTS_PREPARED = "prepared" in inspect.signature(walk_forward).parameters
pytestmark = pytest.mark.skipif(
    not _SUPPORTS_PREPARED,
    reason="walk_forward(prepared=...) 属 Phase 15b，尚未落地",
)


def _make_bars(n: int, seed: int, start_price: float) -> pd.DataFrame:
    """构造一只股票的合成日线（bdate 日历、成交额充足、无缺失）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-02", periods=n)
    close = start_price * np.exp(np.cumsum(0.001 + rng.normal(0.0, 0.01, n)))
    open_ = close * (1.0 + rng.normal(0.0, 0.002, n))
    high = np.fmax(open_, close) * 1.004
    low = np.fmin(open_, close) * 0.996
    volume = rng.integers(2_000_000, 6_000_000, n).astype(float)
    amount = rng.uniform(2e8, 5e8, n)
    turnover = rng.uniform(0.005, 0.02, n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
            "turnover": turnover,
        }
    )


def _universe() -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, dict]]:
    """4 只 code × 300 根日线：足够切出 2 个窗口且每个训练段都 > 60 根。"""
    codes = ["600000", "600001", "600002", "600003"]
    bars_map = {
        code: _make_bars(n=300, seed=i + 1, start_price=10.0 + 3.0 * i)
        for i, code in enumerate(codes)
    }
    stock_list = pd.DataFrame(
        {
            "code": codes,
            "name": [f"测试{code}" for code in codes],
            "is_st": [0] * len(codes),
            "board": ["主板"] * len(codes),
        }
    )
    fund_map = {
        code: {"value_score": 50.0 + 5.0 * i, "growth_score": 55.0, "quality_score": 60.0}
        for i, code in enumerate(codes)
    }
    return stock_list, bars_map, fund_map


def _window_bounds(bars_map: dict[str, pd.DataFrame]) -> tuple[date, date]:
    """窗口取主股票第 80~299 根：pool_bars 有 220 根，预热 60 根与全部窗口都够。"""
    dates = bars_map["600000"]["date"]
    return dates.iloc[80].date(), dates.iloc[299].date()


def test_walkforward_prepared_matches_plain() -> None:
    """walk_forward 内部一次 prepare 后各窗口复用，与逐窗口重算因子表逐值一致。"""
    from stock_plan.strategy.builtin import TrendFollowingStrategy

    stock_list, bars_map, fund_map = _universe()
    start, end = _window_bounds(bars_map)
    base = BacktestConfig(start=start, end=end, rebalance_freq="weekly", market_timing=True)
    kwargs = dict(
        train_days=100,
        test_days=50,
        param_grid=[{"hold_days": 5}, {"hold_days": 10}],
        base_config=base,
    )

    plain = walk_forward(
        lambda: TrendFollowingStrategy(), bars_map, fund_map, stock_list, start, end, **kwargs
    )
    prepared = prepare_backtest(bars_map, fund_map, stock_list, base)
    reused = walk_forward(
        lambda: TrendFollowingStrategy(),
        bars_map,
        fund_map,
        stock_list,
        start,
        end,
        prepared=prepared,
        **kwargs,
    )

    assert len(plain.windows) >= 2, "合成数据应切出至少 2 个窗口，才有复用意义"
    assert plain.windows == reused.windows
    assert plain.oos_metrics == reused.oos_metrics
    pd.testing.assert_series_equal(plain.oos_equity, reused.oos_equity, check_exact=True)
    pd.testing.assert_frame_equal(plain.oos_trades, reused.oos_trades, check_exact=True)

    # prepared 复用不得修改调用方传入的只读快照。
    assert len(prepared.factor_tables) == len(bars_map)
