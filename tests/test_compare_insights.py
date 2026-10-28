# -*- coding: utf-8 -*-
"""策略对比页「超额收益 / 一句话结论 / 最优值高亮」的单元测试。

这几处都是纯函数，不启动 Streamlit 也能测，所以单独放在这里，
避免以后有人改表头时把结论算错还没人发现。
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.ui.views import compare


def _rows() -> list[dict]:
    return [
        {"策略": "趋势跟随", "总收益%": 30.0, "年化%": 12.0, "最大回撤%": -18.0,
         "夏普": 1.1, "胜率%": 55.0, "盈亏比": 1.6, "交易次数": 40},
        {"策略": "动量", "总收益%": 8.0, "年化%": 3.0, "最大回撤%": -25.0,
         "夏普": 0.5, "胜率%": 48.0, "盈亏比": 1.1, "交易次数": 30},
    ]


def _bench(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.bdate_range("2025-01-01", periods=len(values)))


def test_benchmark_return_uses_first_available_series():
    results = {"a": {"benchmark": None}, "b": {"benchmark": _bench([1.0, 1.1, 1.2])}}
    assert compare._benchmark_return(results) == pytest.approx(20.0)


def test_benchmark_return_none_when_missing():
    assert compare._benchmark_return({"a": {"benchmark": None}}) is None
    assert compare._benchmark_return({"a": {"benchmark": _bench([1.0])}}) is None


def test_excess_rows_subtract_benchmark():
    rows = compare._excess_rows(_rows(), 10.0)
    assert [r["超额%"] for r in rows] == [20.0, -2.0]


def test_excess_rows_without_benchmark_leave_none():
    rows = compare._excess_rows(_rows(), None)
    assert all(r["超额%"] is None for r in rows)


def test_summary_line_names_winner():
    rows = compare._excess_rows(_rows(), 10.0)
    line = compare._summary_line(rows, 10.0)
    assert "趋势跟随" in line
    assert "20.00 个百分点" in line
    assert "跑赢基准" in line


def test_summary_line_says_all_lost_when_nobody_beats_benchmark():
    rows = compare._excess_rows(_rows(), 40.0)
    line = compare._summary_line(rows, 40.0)
    assert "全部策略跑输基准" in line
    assert "趋势跟随" in line


def test_summary_line_without_benchmark_is_explicit():
    line = compare._summary_line(_rows(), None)
    assert "无法计算超额收益" in line


def test_styled_table_tints_best_and_formats_missing_values():
    frame = pd.DataFrame(compare._excess_rows(_rows(), None))
    html = compare._styled_table(frame, has_benchmark=False).to_html()
    assert "background-color" in html
    assert "—" in html
