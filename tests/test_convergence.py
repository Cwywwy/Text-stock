"""均线收敛因子测试（PCF / VCF / PVCF / ACF / TRCF）。

覆盖：
- 公式恒等（与逐行手算一致）
- 常数价格 → 收敛度 0（std=0）
- 预热期 NaN（不足 120 根）
- 五个变体的数据源映射与窗口定义
- 量纲问题：相对口径对价格缩放不变、绝对口径随价格放大
- PVCF 截面 z-score 相加合成
- 缺列降级

Revision History:
    2026-10-29  (phase 7) align API with plan: convergence_columns, compose_pvcf
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_plan.factors.convergence import (
    CONV_DIAG_COLUMNS,
    CONV_RAW_COLUMNS,
    CONV_VARIANTS,
    CONVERGENCE_WINDOWS,
    MA_WINDOWS,
    add_conv_columns,
    compose_pvcf,
    conv_variant_series,
    convergence_columns,
    convergence_series,
    cross_section_zscore,
    ma_stack,
    relative_convergence_series,
)


def _bars(n: int = 200, close=None, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    if close is None:
        close = 10.0 * np.cumprod(1 + rng.normal(0, 0.02, n))
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000 + rng.integers(0, 200_000, n),
            "amount": 10_000_000 + rng.integers(0, 2_000_000, n),
            "turnover": 0.01 + rng.random(n) * 0.005,
        }
    )


def test_window_definitions():
    """研报窗口：MA_5/10/20/60/120 加 MA_1。"""
    assert CONVERGENCE_WINDOWS == (5, 10, 20, 60, 120)
    assert MA_WINDOWS == (1, 5, 10, 20, 60, 120)


def test_convergence_formula_matches_manual():
    """与手算 −log(1+std(MA_1..MA_120)) 逐点一致。"""
    close = pd.Series(np.linspace(10.0, 20.0, 130))
    got = convergence_series(close)
    ma = ma_stack(close)
    expected = -np.log1p(ma.std(axis=1, ddof=1, skipna=False))
    pd.testing.assert_series_equal(got, expected, check_names=False)


def test_constant_price_is_zero():
    """价格恒定 → 各均线相同 → std=0 → 收敛度 0。"""
    got = convergence_series(pd.Series([10.0] * 150))
    assert got.iloc[119:].abs().max() == pytest.approx(0.0, abs=1e-12)
    assert got.iloc[:119].isna().all()


def test_warmup_is_nan_not_zero():
    """不足 120 根时为 NaN——不能把「没有历史」当「完美收敛」。"""
    got = convergence_series(pd.Series(np.arange(1.0, 120.0)))
    assert got.isna().all()
    got2 = convergence_series(pd.Series(np.arange(1.0, 121.0)))
    assert pd.notna(got2.iloc[-1])


def test_all_variants_present_and_sourced():
    """四个原始变体各自的列、数据源映射，以及诊断列在场。"""
    bars = _bars()
    out = convergence_columns(bars)
    for column in CONV_RAW_COLUMNS:
        assert column in out.columns
        assert out[column].notna().sum() > 0
    for column in CONV_DIAG_COLUMNS:
        assert column in out.columns
    expected = {
        "conv_pcf": convergence_series(bars["close"]).to_numpy(),
        "conv_vcf": convergence_series(bars["volume"]).to_numpy(),
        "conv_acf": convergence_series(bars["amount"]).to_numpy(),
        "conv_trcf": convergence_series(bars["turnover"]).to_numpy(),
    }
    for column, values in expected.items():
        np.testing.assert_allclose(out[column].to_numpy(), values, equal_nan=True)


def test_add_conv_columns_matches_convergence_columns():
    """引擎侧（追加到既有 df）与纯计算列必须逐值一致。"""
    bars = _bars()
    df = bars[["date"]].copy()
    out = add_conv_columns(df, bars)
    conv = convergence_columns(bars)
    for column in CONV_RAW_COLUMNS:
        np.testing.assert_allclose(out[column].to_numpy(), conv[column].to_numpy(), equal_nan=True)


def test_missing_turnover_column_degrades_to_nan():
    bars = _bars().drop(columns=["turnover"])
    out = convergence_columns(bars)
    assert out["conv_trcf"].isna().all()
    assert out["conv_pcf"].notna().sum() > 0


def test_dimension_issue_absolute_vs_relative():
    """量纲问题：同一形状不同价位，绝对口径差 100 倍，相对口径几乎相同。"""
    shape = np.cumprod(1 + np.random.default_rng(3).normal(0, 0.02, 200))
    low = pd.Series(5.0 * shape)
    high = pd.Series(500.0 * shape)

    abs_low = convergence_series(low).dropna()
    abs_high = convergence_series(high).dropna()
    # 绝对口径：高价股 factor 大幅更负（std 放大 100 倍）
    assert abs_high.mean() < abs_low.mean() - 1.0

    rel_low = relative_convergence_series(low).dropna()
    rel_high = relative_convergence_series(high).dropna()
    pd.testing.assert_series_equal(rel_low, rel_high, check_names=False, rtol=1e-9)


def test_cross_section_zscore_and_pvcf():
    df = pd.DataFrame(
        {
            "conv_pcf": [1.0, 2.0, 3.0, np.nan],
            "conv_vcf": [2.0, 2.0, 2.0, 2.0],
        }
    )
    z = cross_section_zscore(df, "conv_pcf")
    # ddof=0 的截面标准化：均值 2、标准差 sqrt(2/3)；z(1) = -1 / sqrt(2/3)
    assert z.iloc[0] == pytest.approx(-1.0 / np.sqrt(2.0 / 3.0), abs=1e-9)
    pvcf = compose_pvcf(df)
    # vcf 列无方差 → z=0，PVCF = z(pcf)
    assert pvcf.iloc[2] == pytest.approx(z.iloc[2], abs=1e-9)
    assert pvcf.iloc[3] == 0.0  # NaN 填 0，保持中性

    constant = pd.DataFrame({"conv_pcf": [5.0, 5.0], "conv_vcf": [1.0, 1.0]})
    assert (compose_pvcf(constant) == 0.0).all()


def test_pvcf_missing_columns_is_nan():
    df = pd.DataFrame({"conv_pcf": [1.0]})
    assert compose_pvcf(df).isna().all()


def test_conv_variant_series_covers_all_plan_variants():
    """复筛矩阵的 5 个变体 + multi 都要能取到，且非法值给出中文报错。"""
    df = pd.DataFrame(
        {
            "conv_pcf": [3.0, 1.0, 2.0],
            "conv_vcf": [1.0, 2.0, 3.0],
            "conv_pvcf": [2.0, 3.0, 1.0],
            "conv_acf": [1.0, 3.0, 2.0],
            "conv_trcf": [3.0, 2.0, 1.0],
        }
    )
    for variant in CONV_VARIANTS:
        series = conv_variant_series(df, variant)
        assert len(series) == len(df)
        assert not series.isna().any()
        if variant != "multi":
            column = {
                "pcf": "conv_pcf",
                "vcf": "conv_vcf",
                "pvcf": "conv_pvcf",
                "acf": "conv_acf",
                "trcf": "conv_trcf",
            }[variant]
            expected = cross_section_zscore(df, column)
            pd.testing.assert_series_equal(series, expected, check_names=False, rtol=1e-12)

    # 缺列 → 全 0（中性），不抛错
    missing = conv_variant_series(pd.DataFrame({"conv_pcf": [1.0, 2.0]}), "trcf")
    assert (missing == 0.0).all()

    with pytest.raises(ValueError, match="未知的收敛因子变体"):
        conv_variant_series(df, "nope")
