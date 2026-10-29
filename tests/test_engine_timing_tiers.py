# -*- coding: utf-8 -*-
"""双均线三档择时（Phase 19）单元测试。

覆盖三层：
1. `factors/timing.py` 的档位判定（100% / 50% / 0% + 预热 + 次日生效）；
2. 回测引擎的接入（默认 off 逐值不变、档位优先于弱择时、降仓真的降敞口）；
3. 非法档位的显式报错（不静默回落）。

修订记录：
2026-10-29 (phase 19) 新建：覆盖双均线三档择时的档位判定与引擎接入
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.factors.timing import (
    DEFAULT_TIMING_TIER,
    TIMING_TIERS,
    exposure_series,
    tier_counts,
    tier_series,
    validate_timing_tier,
)
from stock_plan.strategy.base import Strategy


class _AlwaysBuyStrategy(Strategy):
    """每天都给所有股票打同分：用于观察仓位随档位的变化。"""

    name = "测试全覆盖策略"
    params = {
        "atr_k_entry": 0.0,
        "atr_m_exit": 99.0,
        "atr_n_stop": 99.0,
        "hold_days": 999,
    }

    def filter_universe(self, stock_list, bars_map):
        return [str(c) for c in stock_list["code"] if str(c) in bars_map]

    def score(self, df_factors):
        return pd.Series([80.0] * len(df_factors), index=df_factors.index)

    def entry_price(self, row, atr):
        return float(row["close"])

    def exit_price(self, entry, atr):
        return entry * 100.0, entry * 0.01, 999


def _rising_then_crashing(days: int = 200, rise: int = 100) -> pd.DataFrame:
    """先慢涨 100 天、再持续下跌 100 天的行情。"""
    dates = pd.bdate_range("2025-01-01", periods=days)
    prices = []
    price = 10.0
    for i in range(days):
        price *= 1.004 if i < rise else 0.99
        prices.append(price)
    rows = []
    for d, c in zip(dates, prices):
        rows.append(
            {
                "date": d,
                "open": round(c * 0.999, 4),
                "high": round(c * 1.002, 4),
                "low": round(c * 0.998, 4),
                "close": round(c, 4),
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
                "turnover": 0.01,
            }
        )
    return pd.DataFrame(rows)


def _stock_list(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": codes,
            "name": [f"测试{c}" for c in codes],
            "industry": [""] * len(codes),
            "list_date": ["2020-01-01"] * len(codes),
            "is_st": [0] * len(codes),
        }
    )


# ---------- 档位判定 ----------
def test_tier_series_uptrend_is_full():
    """单边上涨：收盘恒在 MA20 上方 → 满仓。"""
    closes = pd.Series(np.linspace(10.0, 20.0, 120))
    tiers = tier_series(closes)
    assert (tiers.iloc[30:] == 1.0).all()


def test_tier_series_break_ma20_without_slope_is_half():
    """跌破 MA20 但 MA20 未下行 → 半仓。"""
    closes = pd.Series(
        list(np.linspace(10.0, 20.0, 120)) + list(np.linspace(20.0, 19.0, 12))
    )
    tiers = tier_series(closes)
    assert tiers.iloc[-1] == pytest.approx(0.5)


def test_tier_series_deep_break_is_empty():
    """跌破 MA60 且 MA20 下行 → 清仓。"""
    closes = pd.Series(
        list(np.linspace(10.0, 20.0, 120)) + list(np.linspace(20.0, 12.0, 40))
    )
    tiers = tier_series(closes)
    assert tiers.iloc[-1] == pytest.approx(0.0)
    assert tiers.min() == pytest.approx(0.0)


def test_tier_series_warmup_defaults_full():
    """均线未成形（不足 60 根）时按满仓处理，不做半吊子估算。"""
    closes = pd.Series(np.linspace(10.0, 11.0, 40))
    assert (tier_series(closes) == 1.0).all()


def test_exposure_series_is_next_day_effective():
    """敞口序列必须是档位序列右移一天：当天收盘算、次日生效。"""
    closes = pd.Series(
        list(np.linspace(10.0, 20.0, 120)) + list(np.linspace(20.0, 12.0, 40))
    )
    tiers = tier_series(closes)
    exposure = exposure_series(closes)
    assert exposure.iloc[0] == pytest.approx(1.0)
    for k in range(1, len(tiers)):
        assert exposure.iloc[k] == pytest.approx(tiers.iloc[k - 1])


def test_validate_timing_tier_accepts_and_rejects():
    """合法档位通过；非法档位抛中文错误（不静默回落）。"""
    assert TIMING_TIERS == ("off", "dual_ma")
    assert DEFAULT_TIMING_TIER == "off"
    for tier in TIMING_TIERS:
        validate_timing_tier(tier)
    with pytest.raises(ValueError, match="择时"):
        validate_timing_tier("ma_cross")


def test_tier_counts_sums_to_length():
    """档位分布统计的计数之和等于样本数。"""
    closes = pd.Series(
        list(np.linspace(10.0, 20.0, 120)) + list(np.linspace(20.0, 12.0, 40))
    )
    counts = tier_counts(tier_series(closes))
    assert sum(counts.values()) == len(closes)


# ---------- 引擎接入 ----------
def _run(timing_tier: str, *, market_timing: bool = False):
    bars = _rising_then_crashing()
    bars_map = {code: bars.copy() for code in ("600000", "600001", "600002")}
    stock_list = _stock_list(list(bars_map))
    config = BacktestConfig(
        start=date(2025, 3, 1),
        end=date(2025, 3, 1) + pd.Timedelta(days=300),
        top_n=2,
        market_timing=market_timing,
        timing_tier=timing_tier,
        enable_price_limit=False,
    )
    return run_backtest(_AlwaysBuyStrategy(), config, bars_map, {}, stock_list)


def test_engine_default_off_matches_explicit_off():
    """默认不传档位 = off，结果与显式 off 完全一致（零行为变化）。"""
    bars = _rising_then_crashing()
    bars_map = {code: bars.copy() for code in ("600000", "600001", "600002")}
    stock_list = _stock_list(list(bars_map))
    config = BacktestConfig(
        start=date(2025, 3, 1),
        end=date(2025, 3, 1) + pd.Timedelta(days=300),
        top_n=2,
        enable_price_limit=False,
    )
    implicit = run_backtest(_AlwaysBuyStrategy(), config, bars_map, {}, stock_list)
    explicit = _run("off")
    pd.testing.assert_series_equal(implicit.equity_curve, explicit.equity_curve)


def test_engine_tier_beats_weak_timing():
    """档位择时与弱择时同时开：以档位为准（弱择时不再二次减半）。"""
    both = _run("dual_ma", market_timing=True)
    tier_only = _run("dual_ma", market_timing=False)
    pd.testing.assert_series_equal(both.equity_curve, tier_only.equity_curve)


def test_engine_dual_ma_reduces_drawdown_and_records_de_risk():
    """持续下跌时档位会降敞口：回撤更浅，并留下「择时降仓」交易记录。"""
    off = _run("off")
    tier = _run("dual_ma")
    off_metrics = calc_metrics(off.equity_curve, off.trades)
    tier_metrics = calc_metrics(tier.equity_curve, tier.trades)
    assert tier_metrics["max_drawdown"] > off_metrics["max_drawdown"]
    reasons = set(tier.trades["reason"].astype(str)) if not tier.trades.empty else set()
    assert "择时降仓" in reasons


def test_engine_invalid_tier_raises():
    """引擎收到非法档位直接报错，不悄悄按 off 跑。"""
    bars = _rising_then_crashing(120)
    bars_map = {"600000": bars}
    stock_list = _stock_list(["600000"])
    config = BacktestConfig(
        start=date(2025, 2, 1),
        end=date(2025, 5, 1),
        top_n=1,
        timing_tier="bad_tier",
    )
    with pytest.raises(ValueError, match="择时"):
        run_backtest(_AlwaysBuyStrategy(), config, bars_map, {}, stock_list)
