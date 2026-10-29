# -*- coding: utf-8 -*-
"""动量因子（MA_L/close）与 MomentumStrategy 三模式单元测试。

覆盖：
- `ma_ratio_columns` 公式手算、预热 NaN、方向（上涨股比值 < 1）；
- `add_ma_ratio_columns` 与 `ma_ratio_columns` 等价；
- `cross_section_percentile` 的 NaN 保留与单调性；
- `MomentumStrategy` 默认模式（ret20）与旧实现逐值一致、`ma_ratio` / `ma_ratio_blend` 可用且量级一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_plan.factors.momentum import (
    add_ma_ratio_columns,
    cross_section_percentile,
    ma_ratio_columns,
    ma_ratio_strength,
)
from stock_plan.strategy.momentum import MomentumStrategy


def _rising_bars(n: int = 260, start: float = 10.0, step: float = 1.0) -> pd.DataFrame:
    """构造稳定上涨的日线：close 依次 +step（无波动，便于手算均线）。"""
    close = start + np.arange(n) * step
    return pd.DataFrame(
        {
            "date": pd.date_range("2021-01-01", periods=n, freq="D"),
            "close": close,
            "open": close,
            "high": close,
            "low": close,
            "volume": 1_000_000.0,
            "amount": close * 1_000_000.0,
            "turnover": 0.01,
        }
    )


def _factor_frame(**overrides) -> pd.DataFrame:
    """构造最小因子表（默认满足均线多头、不追高、RSI 正常）。"""
    base = {
        "code": ["600000", "600001"],
        "close": [12.0, 8.0],
        "ma20": [11.0, 9.0],
        "ma60": [10.0, 8.5],
        "rsi14": [55.0, 45.0],
        "trend_score": [60.0, 40.0],
        "fund_score": [60.0, 50.0],
        "mom_ret": [0.10, -0.05],
        "ma_ratio_20": [0.95, 1.02],
        "ma_ratio_60": [0.92, 1.05],
        "ma_ratio_120": [0.88, 1.10],
        "ma_ratio_240": [0.85, 1.15],
    }
    base.update(overrides)
    return pd.DataFrame(base)


class TestMaRatioColumns:
    def test_formula_matches_hand_calculation(self) -> None:
        """MA_L/close 应与手算完全一致。"""
        bars = _rising_bars(n=260)
        ratios = ma_ratio_columns(bars)
        for window in (20, 60, 120, 240):
            expected = bars["close"].rolling(window).mean().iloc[-1] / bars["close"].iloc[-1]
            assert ratios[f"ma_ratio_{window}"].iloc[-1] == pytest.approx(expected, rel=1e-12)

    def test_warmup_is_nan(self) -> None:
        """窗口不足的行必须是 NaN（不能拿半截窗口冒充）。"""
        bars = _rising_bars(n=260)
        ratios = ma_ratio_columns(bars)
        assert ratios["ma_ratio_240"].iloc[:239].isna().all()
        assert pd.notna(ratios["ma_ratio_240"].iloc[239])
        assert ratios["ma_ratio_20"].iloc[:19].isna().all()

    def test_direction_rising_stock_below_one(self) -> None:
        """上涨股的 MA/close 应 < 1（价格在均线上方 → 动量强）。"""
        ratios = ma_ratio_columns(_rising_bars())
        assert ratios["ma_ratio_60"].iloc[-1] < 1.0
        # 反向：下跌股 > 1
        falling = _rising_bars(n=260, start=60.0, step=-0.05)
        assert ma_ratio_columns(falling)["ma_ratio_60"].iloc[-1] > 1.0

    def test_missing_close_returns_all_nan(self) -> None:
        """没有 close 列时不抛错，四列全 NaN。"""
        frame = pd.DataFrame({"volume": [1.0, 2.0]})
        ratios = ma_ratio_columns(frame)
        assert list(ratios.columns) == ["ma_ratio_20", "ma_ratio_60", "ma_ratio_120", "ma_ratio_240"]
        assert ratios.isna().all().all()

    def test_add_columns_equals_plain_calculation(self) -> None:
        """add_ma_ratio_columns 只能追加列，不能改已有列/行序。"""
        bars = _rising_bars()
        added = add_ma_ratio_columns(bars)
        plain = ma_ratio_columns(bars)
        for column in plain.columns:
            pd.testing.assert_series_equal(added[column], plain[column])
        pd.testing.assert_frame_equal(added[bars.columns], bars)

    def test_strength_is_one_minus_ratio(self) -> None:
        frame = _factor_frame()
        strength = ma_ratio_strength(frame, 60)
        assert strength.tolist() == pytest.approx([0.08, -0.05])


class TestPercentile:
    def test_nan_preserved(self) -> None:
        series = pd.Series([1.0, np.nan, 3.0])
        pct = cross_section_percentile(series)
        assert np.isnan(pct.iloc[1])

    def test_monotonic(self) -> None:
        series = pd.Series([3.0, 1.0, 2.0])
        pct = cross_section_percentile(series)
        assert pct.iloc[0] > pct.iloc[2] > pct.iloc[1]


class TestMomentumModes:
    def test_default_mode_identical_to_old_formula(self) -> None:
        """默认 ret20：分数 = composite(0.6/0.4) + (mom+0.2)/0.5×30 − 惩罚。"""
        df = _factor_frame()
        strategy = MomentumStrategy()
        assert strategy.params["momentum_mode"] == "ret20"
        score = strategy.score(df)
        expected = (
            df["trend_score"] * 0.6
            + df["fund_score"] * 0.4
            + (df["mom_ret"].clip(-0.2, 0.3) + 0.2) / 0.5 * 30
        )
        assert score.tolist() == pytest.approx(expected.tolist(), rel=1e-12)

    def test_ma_ratio_mode_uses_ratio_not_mom_ret(self) -> None:
        """ma_ratio 模式下改 mom_ret 不影响分数，改 ma_ratio_60 才影响。"""
        strategy = MomentumStrategy()
        strategy.params["momentum_mode"] = "ma_ratio"
        df = _factor_frame()
        base = strategy.score(df)
        df_changed_mom = _factor_frame(mom_ret=[-0.3, 0.3])
        pd.testing.assert_series_equal(strategy.score(df_changed_mom), base)
        df_changed_ratio = _factor_frame(ma_ratio_60=[0.80, 1.20])
        assert strategy.score(df_changed_ratio).iloc[0] > base.iloc[0]
        assert strategy.score(df_changed_ratio).iloc[1] < base.iloc[1]

    def test_ma_ratio_mode_nan_is_neutral(self) -> None:
        """历史不足（ma_ratio_60 全 NaN）不能整列崩掉。"""
        strategy = MomentumStrategy()
        strategy.params["momentum_mode"] = "ma_ratio"
        df = _factor_frame(ma_ratio_60=[np.nan, np.nan])
        score = strategy.score(df)
        assert np.isfinite(score).all()

    def test_blend_mode_within_bonus_range(self) -> None:
        """blend 模式加分应在 0~30，且四窗口齐全。"""
        strategy = MomentumStrategy()
        strategy.params["momentum_mode"] = "ma_ratio_blend"
        df = _factor_frame()
        bonus = strategy._momentum_bonus(df)
        assert bonus is not None
        assert bonus.between(0, 30).all()

    def test_blend_mode_missing_columns_returns_none(self) -> None:
        strategy = MomentumStrategy()
        strategy.params["momentum_mode"] = "ma_ratio_blend"
        df = _factor_frame().drop(columns=["ma_ratio_240"])
        assert strategy._momentum_bonus(df) is None
