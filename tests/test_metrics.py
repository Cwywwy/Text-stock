"""回测指标扩展测试：分年度指标与换手率。

覆盖：
- 分年度拆分（按自然年度、按 exit_date 归属交易）
- 换手率口径（单边/双边/年度）
- 空输入、无交易、跨年持仓等边界
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.backtest.metrics import calc_annual_metrics, calc_turnover, calc_metrics


def _equity(values: list[float], start: str = "2024-01-02") -> pd.Series:
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx)


def _trades(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


TRADE_TEMPLATE = {
    "entry_price": 10.0,
    "exit_price": 12.0,
    "shares": 1000,
    "pnl": 1900.0,
    "pnl_pct": 19.0,
}


def test_calc_annual_metrics_splits_by_year():
    """两年资金曲线应拆出两个年度键，且收益按年内首末值计算。"""
    eq = _equity([100000, 110000, 120000], "2024-01-02")
    # 2024 年的点
    eq_2025_idx = pd.bdate_range("2025-01-02", periods=3)
    eq = pd.concat(
        [
            eq,
            pd.Series([132000, 138600, 145530], index=eq_2025_idx),
        ]
    )
    tr = _trades(
        [
            {**TRADE_TEMPLATE, "entry_date": "2024-02-01", "exit_date": "2024-03-01"},
            {**TRADE_TEMPLATE, "entry_date": "2024-12-01", "exit_date": "2025-02-01"},
        ]
    )
    out = calc_annual_metrics(eq, tr)
    assert list(out.keys()) == ["2024", "2025"]
    # 2024：100000 → 120000 = +20%
    assert out["2024"]["total_return"] == pytest.approx(20.0, abs=0.01)
    # 2025：132000 → 145530 = +10.25%
    assert out["2025"]["total_return"] == pytest.approx(10.25, abs=0.01)
    # 交易按 exit_date 归属：2024 一笔、2025 一笔
    assert out["2024"]["trade_count"] == 1
    assert out["2025"]["trade_count"] == 1


def test_calc_annual_metrics_empty_and_no_trades():
    assert calc_annual_metrics(pd.Series(dtype=float), pd.DataFrame()) == {}
    eq = _equity([100000, 105000])
    out = calc_annual_metrics(eq, pd.DataFrame())
    assert out["2024"]["trade_count"] == 0
    assert out["2024"]["total_return"] == pytest.approx(5.0, abs=0.01)


def test_calc_turnover_basic():
    """买入 1 万 / 平均资产 10 万 → 单边 0.1；卖出 1.2 万 → 双边 0.22。"""
    eq = _equity([100000] * 10, "2024-01-02")
    tr = _trades(
        [
            {
                **TRADE_TEMPLATE,
                "entry_date": "2024-01-05",
                "exit_date": "2024-03-05",
            }
        ]
    )
    out = calc_turnover(eq, tr)
    assert out["single_side"] == pytest.approx(0.1, abs=1e-6)
    assert out["double_side"] == pytest.approx(0.22, abs=1e-6)
    assert out["buy_amount"] == pytest.approx(10000.0)
    assert out["sell_amount"] == pytest.approx(12000.0)
    assert out["annual"]["2024"]["single_side"] == pytest.approx(0.1, abs=1e-6)
    assert out["annual"]["2024"]["buy_trades"] == 1
    assert out["annual"]["2024"]["sell_trades"] == 1


def test_calc_turnover_cross_year_position():
    """跨年持仓：买入计入买入年，卖出计入卖出年。"""
    eq = pd.concat(
        [
            _equity([100000] * 5, "2024-06-03"),
            _equity([100000] * 5, "2025-06-02"),
        ]
    )
    tr = _trades(
        [
            {
                **TRADE_TEMPLATE,
                "entry_date": "2024-12-20",
                "exit_date": "2025-01-10",
            }
        ]
    )
    out = calc_turnover(eq, tr)
    # 2024 有买入没有卖出；2025 有卖出没有买入
    assert out["annual"]["2024"]["buy_trades"] == 1
    assert out["annual"]["2024"]["sell_trades"] == 0
    assert out["annual"]["2024"]["single_side"] == pytest.approx(0.1, abs=1e-6)
    assert out["annual"]["2025"]["buy_trades"] == 0
    assert out["annual"]["2025"]["sell_trades"] == 1


def test_calc_turnover_empty():
    assert calc_turnover(None, None)["single_side"] == 0.0
    assert calc_turnover(pd.Series(dtype=float), pd.DataFrame())["double_side"] == 0.0
    eq = _equity([100000, 100000])
    out = calc_turnover(eq, pd.DataFrame())
    assert out["single_side"] == 0.0
    assert out["avg_equity"] == pytest.approx(100000.0)


def test_calc_metrics_unchanged_contract():
    """锁死旧契约：calc_metrics 的 9 个键一个都不能少（UI/缓存依赖）。"""
    eq = _equity([100000, 102000, 101000])
    tr = _trades(
        [{**TRADE_TEMPLATE, "entry_date": "2024-01-03", "exit_date": "2024-01-05"}]
    )
    m = calc_metrics(eq, tr)
    assert set(m) == {
        "total_return",
        "annual_return",
        "max_drawdown",
        "sharpe",
        "win_rate",
        "profit_loss",
        "trade_count",
        "avg_hold_days",
        "avg_pnl_pct",
    }
