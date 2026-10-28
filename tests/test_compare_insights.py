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


def _tinted_cells(styler) -> dict[str, str]:
    """展开 Styler 的 CSS 规则，返回 {row行_col列: 样式}。

    同一款样式会被 pandas 合并成「多选择器一条规则」，所以必须先拆选择器再计数。
    """
    import re

    tinted: dict[str, str] = {}
    for selectors, body in re.findall(r"([^{}]*?)\{([^{}]*)\}", styler.to_html()):
        if "background-color" not in body:
            continue
        for row, col in re.findall(r"#T_[0-9a-f]+_row(\d+)_col(\d+)", selectors):
            tinted[f"row{row}_col{col}"] = body.strip()
    return tinted


def test_styled_table_tints_one_cell_per_metric_column():
    """每列只给最优值上色；pandas 3.0 的 highlight_* 语义变了，这里锁死行为。"""
    frame = pd.DataFrame(compare._excess_rows(_rows(), 10.0))
    tinted = _tinted_cells(compare._styled_table(frame, has_benchmark=True))
    # 5 个收益类指标 + 超额 + 回撤 = 7 个格子
    assert len(tinted) == 7, tinted
    assert compare.cs.BEST_UP in " ".join(tinted.values())
    assert compare.cs.BEST_DOWN in " ".join(tinted.values())


def test_styled_table_skips_excess_when_no_benchmark():
    frame = pd.DataFrame(compare._excess_rows(_rows(), None))
    tinted = _tinted_cells(compare._styled_table(frame, has_benchmark=False))
    assert len(tinted) == 6, tinted
    assert "—" in compare._styled_table(frame, has_benchmark=False).to_html()
