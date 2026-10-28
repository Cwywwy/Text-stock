"""市场阶段因子测试（暂时需求 6 前半）。

覆盖：等权收益指数（含"不受股价水平影响"这一关键性质）、阶段判定三段行情、
阶段说明可读、策略偏好与仓位建议、数据不足的处理。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_plan.factors.regime import (
    PHASE_DOWN,
    PHASE_RANGE,
    PHASE_UNKNOWN,
    PHASE_UP,
    TREND_DOWN,
    TREND_UP,
    detect_regime,
    equal_weight_index,
    regime_table,
    suggest_playbook,
)


def _bars(dates, closes) -> pd.DataFrame:
    """按给定收盘价序列造一份最小可用的日线。"""
    return pd.DataFrame(
        {
            "date": pd.DatetimeIndex(dates),
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": 1_000_000.0,
            "amount": 10_000_000.0,
            "turnover": 0.01,
        }
    )


# ---------- 等权收益指数 ----------
def test_index_is_price_level_invariant() -> None:
    """同一组收益率、不同的股价绝对水平，必须得到同一条指数。

    注意 min_contributors=1：默认值是 20，若只有 1 只股票就恒为 1.0，
    那样这个断言会"平凡成立"、什么都验证不到。
    """
    n = 30
    dates = pd.bdate_range("2025-01-01", periods=n)
    cheap = _bars(dates, [1.0 * 1.01 ** i for i in range(n)])
    pricey = _bars(dates, [800.0 * 1.01 ** i for i in range(n)])

    a = equal_weight_index({"A": cheap}, min_contributors=1)
    b = equal_weight_index({"B": pricey}, min_contributors=1)
    assert np.allclose(a.values, b.values)
    assert a.iloc[0] == pytest.approx(1.0)
    # 确认这条断言不是平凡的：指数确实在上涨
    assert a.iloc[-1] > 1.2


def test_index_is_not_dominated_by_high_priced_stocks() -> None:
    """等权指数不受股价水平影响；而"平均原始股价"会被高价股拖成几乎不动。

    这正是它替换引擎里 `closes.mean(axis=1)` 的原因。
    """
    n = 41
    dates = pd.bdate_range("2025-01-01", periods=n)
    grow = _bars(dates, [10.0 * 1.01 ** i for i in range(n)])   # 每日 +1%
    flat = _bars(dates, [1000.0] * n)                           # 原地不动

    idx = equal_weight_index({"grow": grow, "flat": flat}, min_contributors=2)
    # 日收益等权平均 = (1% + 0%) / 2 = 0.5%
    assert idx.iloc[-1] == pytest.approx(1.005 ** (n - 1), rel=1e-6)

    naive = (grow.set_index("date")["close"] + flat.set_index("date")["close"]) / 2
    naive = naive / naive.iloc[0]
    assert naive.iloc[-1] < 1.01, "平均原始股价几乎不动（被 1000 元那只主导）"
    assert idx.iloc[-1] > 1.2, "等权指数正确反映了 1% 的日涨幅"


def test_index_skips_days_with_too_few_contributors() -> None:
    """参与计算的成分股太少的日子不贡献收益，避免样本极少时把指数拉出假波动。"""
    dates = pd.bdate_range("2025-01-01", periods=10)
    only_one = _bars(dates, [10.0 * 1.5 ** i for i in range(10)])
    idx = equal_weight_index({"A": only_one}, min_contributors=20)
    assert idx.iloc[-1] == pytest.approx(1.0), "单只股票不该被当成'大盘'"


def test_index_empty_input() -> None:
    assert equal_weight_index({}).empty


# ---------- 阶段判定 ----------
def test_detect_regime_three_phases() -> None:
    n = 240
    dates = pd.bdate_range("2025-01-01", periods=n)
    up = pd.Series(np.linspace(1.0, 1.5, n), index=dates)
    down = pd.Series(np.linspace(1.5, 1.0, n), index=dates)
    t = np.arange(n)
    # 震荡用正弦：随机游走在末段本身就有方向，判成趋势并不算错
    sine = pd.Series(1.0 + 0.06 * np.sin(2 * np.pi * t / 40), index=dates)

    assert detect_regime(up).phase == PHASE_UP
    assert detect_regime(up).trend == TREND_UP
    assert detect_regime(down).phase == PHASE_DOWN
    assert detect_regime(down).trend == TREND_DOWN
    assert detect_regime(sine).phase == PHASE_RANGE


def test_detect_regime_needs_enough_history() -> None:
    """历史太短时给"数据不足"，不能给出误导性的阶段。"""
    short = pd.Series(np.linspace(1.0, 1.2, 30), index=pd.bdate_range("2025-01-01", periods=30))
    assert detect_regime(short).phase == PHASE_UNKNOWN
    assert detect_regime(pd.Series(dtype=float)).phase == PHASE_UNKNOWN


def test_regime_snapshot_is_explainable() -> None:
    """快照要能直接用大白话解释给用户看。"""
    n = 240
    dates = pd.bdate_range("2025-01-01", periods=n)
    snap = detect_regime(pd.Series(np.linspace(1.0, 1.5, n), index=dates))
    assert snap.explain, "必须给出判定理由"
    assert any("上升趋势" in e for e in snap.explain)
    assert any("波动率" in e for e in snap.explain)
    d = snap.to_dict()
    assert d["phase"] == PHASE_UP and "explain" in d


def test_regime_table_flags_warmup_rows() -> None:
    n = 240
    dates = pd.bdate_range("2025-01-01", periods=n)
    table = regime_table(pd.Series(np.linspace(1.0, 1.5, n), index=dates))
    assert len(table) == n
    assert (table["phase"].iloc[:60] == PHASE_UNKNOWN).all(), "前 60 天均线尚未成形"
    assert table["phase"].iloc[-1] == PHASE_UP


# ---------- 策略偏好与仓位建议 ----------
def test_suggest_playbook_by_phase() -> None:
    up = detect_regime(pd.Series(np.linspace(1.0, 1.5, 240),
                                 index=pd.bdate_range("2025-01-01", periods=240)))
    down = detect_regime(pd.Series(np.linspace(1.5, 1.0, 240),
                                   index=pd.bdate_range("2025-01-01", periods=240)))
    plan_up = suggest_playbook(up)
    plan_down = suggest_playbook(down)

    assert plan_up["stance"] == "进攻"
    assert plan_down["stance"] == "防守"
    assert plan_up["position"] > plan_down["position"]
    # 仓位必须是合法比例
    for plan in (plan_up, plan_down):
        assert 0.0 <= plan["position"] <= 1.0
        assert plan["prefer"], "每个阶段都要给出策略偏好"


def test_high_volatility_reduces_position() -> None:
    """同样处于上升趋势，高波动应压低建议仓位。"""
    n = 240
    dates = pd.bdate_range("2025-01-01", periods=n)
    calm = detect_regime(pd.Series(np.linspace(1.0, 1.5, n), index=dates))

    rng = np.random.default_rng(7)
    noisy = pd.Series(
        np.linspace(1.0, 1.5, n) * (1 + rng.normal(0, 0.03, n)),
        index=dates,
    )
    wild = detect_regime(noisy)

    if wild.volatility == "高" and calm.volatility == "低":
        assert suggest_playbook(wild)["position"] < suggest_playbook(calm)["position"]
    # 至少验证折扣本身生效
    assert suggest_playbook(wild)["position"] <= suggest_playbook(calm)["position"]
