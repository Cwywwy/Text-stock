# -*- coding: utf-8 -*-
"""行业轮动回测的三档择时接入（Phase 19）单元测试。

重点验证：默认 off 逐值不变、上涨行情下三档恒为满仓（与 off 等价）、
下跌行情下档位降低会压缩行业敞口并留下成本记录、非法档位显式报错。

修订记录：
2026-10-29 (phase 19) 新建：覆盖行业轮动 timing_tier 的接入与默认零变化
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_plan.backtest.rotation import run_industry_rotation
from stock_plan.factors.rrg import QUADRANT_IMPROVING

INDUSTRIES = ("银行", "医药", "电子")


def _panel(days: int = 260, *, crash: bool = True) -> pd.DataFrame:
    """构造三个同涨同跌的行业指数（等于把「大盘」直接搬到行业上）。"""
    dates = pd.bdate_range("2023-01-02", periods=days)
    daily: list[float] = []
    for i in range(days):
        if crash and 140 <= i < 200:
            daily.append(-0.004)
        else:
            daily.append(0.002)
    level = 100.0 * np.cumprod(np.array([1.0] + [1.0 + r for r in daily[:-1]]))
    return pd.DataFrame({name: level for name in INDUSTRIES}, index=dates)


def _rrg(panel: pd.DataFrame) -> pd.DataFrame:
    """每行都是「转强」象限，保证选行业这步不成为干扰项。"""
    rows = []
    for day in panel.index:
        for name in INDUSTRIES:
            rows.append(
                {
                    "date": day,
                    "industry": name,
                    "rs": 101.0,
                    "rs_ratio": 105.0,
                    "rs_momentum": 102.0,
                    "quadrant": QUADRANT_IMPROVING,
                }
            )
    return pd.DataFrame(rows)


def _run(panel: pd.DataFrame, **kwargs):
    return run_industry_rotation(panel, _rrg(panel), rebalance_days=20, top_n=3, **kwargs)


def test_rotation_default_off_matches_explicit_off():
    """不传档位 = off：结果与显式 off 完全一致。"""
    panel = _panel()
    implicit = _run(panel)
    explicit = _run(panel, timing_tier="off")
    pd.testing.assert_series_equal(implicit.equity_curve, explicit.equity_curve)


def test_rotation_dual_ma_equals_off_in_rising_market():
    """全程上涨时三档恒为满仓 → 与 off 等价（只差浮点误差）。"""
    panel = _panel(crash=False)
    off = _run(panel, timing_tier="off")
    tier = _run(panel, timing_tier="dual_ma")
    pd.testing.assert_series_equal(tier.equity_curve, off.equity_curve)


def test_rotation_dual_ma_cuts_drawdown_in_crash():
    """下跌段档位降到 0/50% → 回撤明显更浅，且成本被记录。"""
    panel = _panel()
    off = _run(panel, timing_tier="off")
    tier = _run(panel, timing_tier="dual_ma")
    assert tier.metrics["max_drawdown"] > off.metrics["max_drawdown"]
    assert tier.total_cost >= off.total_cost
    assert tier.equity_curve.iloc[-1] > off.equity_curve.iloc[-1]


def test_rotation_invalid_tier_raises():
    """非法档位直接报中文错误。"""
    panel = _panel(crash=False)
    with pytest.raises(ValueError, match="择时"):
        _run(panel, timing_tier="ma_cross")
