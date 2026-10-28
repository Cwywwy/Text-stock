# -*- coding: utf-8 -*-
"""回测基准（等权持有候选池）测试（2026-10-28 可视化升级 Phase 1）。

基准是用来回答「策略到底有没有超额收益」的对照线，所以必须锁死两件事：
1. 首日归一为 1.0，后面是等权收益的累乘；
2. 候选池空 / 行情不足时返回 None，而不是抛异常或返回一条假的 1.0 直线。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_plan.backtest.engine import BacktestResult, equal_weight_benchmark


def _bars(closes: list[float], start: str = "2026-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1000.0] * len(closes),
        }
    )


def test_benchmark_normalised_to_one_and_compounds_equally() -> None:
    a = _bars([10.0, 11.0, 12.1])          # 每日 +10%
    b = _bars([20.0, 20.0, 20.0])          # 不动
    index = equal_weight_benchmark(["a", "b"], {"a": a, "b": b}, date(2026, 1, 1), date(2026, 1, 3))
    assert index is not None
    # 首日 1.0；之后每天 (1+10%) 与 (1+0%) 等权平均 = 1.05
    assert index.iloc[0] == 1.0
    assert index.iloc[1] == 1.05
    assert index.iloc[2] == pytest.approx(1.1025)


def test_benchmark_respects_date_window() -> None:
    a = _bars([10.0, 11.0, 12.1, 13.31])
    index = equal_weight_benchmark(["a"], {"a": a}, date(2026, 1, 2), date(2026, 1, 3))
    assert index is not None
    assert len(index) == 2
    assert index.iloc[0] == 1.0


def test_benchmark_returns_none_when_no_data() -> None:
    assert equal_weight_benchmark([], {}, date(2026, 1, 1), date(2026, 1, 3)) is None
    assert equal_weight_benchmark(["missing"], {}, date(2026, 1, 1), date(2026, 1, 3)) is None
    # 只有一根 K 线 → 算不出收益率
    assert equal_weight_benchmark(["a"], {"a": _bars([10.0])}, date(2026, 1, 1), date(2026, 1, 3)) is None


def test_backtest_result_benchmark_defaults_to_none() -> None:
    """旧调用方/旧调用签名构造的结果对象不应因新增字段而报错。"""
    result = BacktestResult(equity_curve=pd.Series(dtype=float), trades=pd.DataFrame())
    assert result.benchmark is None
