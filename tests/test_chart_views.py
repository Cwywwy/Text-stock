# -*- coding: utf-8 -*-
"""图表架构守护测试：视图层不得自带样式、所有 Plotly 图必须走工厂。

覆盖：
- `src/stock_plan/ui/` 下除 `chart_style.py` 外不得出现硬编码颜色（#RRGGBB / rgba(...)）。
- 不得再使用 Streamlit 1.63 已弃用的 `use_container_width`。
- `st.plotly_chart(` 只允许出现在 `widgets.py`（其他页面必须调 `render_chart`）。
- `benchmark_equity_fig`：基准指数必须乘初始资金换算成资产，且走统一样式。
- `price_level_fig`：买入/止损/止盈/做T 五条价位线齐全，缺数据返回 None。
"""
from __future__ import annotations

import ast
import io
import re
from pathlib import Path

import pandas as pd
import pytest

from stock_plan.ui import chart_style as cs
from stock_plan.ui.widgets import BENCHMARK_LABEL, benchmark_equity_fig, price_level_fig

UI_ROOT = Path(__file__).resolve().parents[1] / "src" / "stock_plan" / "ui"
COLOUR_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
RGBA_RE = re.compile(r"rgba?\(\s*\d")


def _ui_sources():
    for path in sorted(UI_ROOT.rglob("*.py")):
        if path.name == "chart_style.py":
            continue
        yield path, io.open(path, encoding="utf-8").read()


def test_ui_modules_have_no_hardcoded_colours():
    offenders = []
    for path, text in _ui_sources():
        for lineno, line in enumerate(text.splitlines(), 1):
            if COLOUR_RE.search(line) or RGBA_RE.search(line):
                offenders.append(f"{path.name}:{lineno}: {line.strip()}")
    assert not offenders, "视图层出现硬编码颜色，请改用 chart_style 的 token：\n" + "\n".join(offenders)


def _called_names(text: str) -> set[str]:
    """收集文件里所有被调用的函数名 / 关键字参数名（用 AST，避开文档字符串里的同名文字）。"""
    found: set[str] = set()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Call):
            for keyword in node.keywords:
                if keyword.arg:
                    found.add(f"kw:{keyword.arg}")
            func = node.func
            if isinstance(func, ast.Attribute):
                found.add(f"attr:{func.attr}")
            elif isinstance(func, ast.Name):
                found.add(f"name:{func.id}")
    return found


def test_ui_has_no_deprecated_use_container_width():
    offenders = [
        path.name
        for path, text in _ui_sources()
        if "kw:use_container_width" in _called_names(text)
    ]
    assert not offenders, f"Streamlit 1.63 已弃用 use_container_width，请改用 width=\"stretch\"：{offenders}"


def test_plotly_chart_calls_only_in_widgets():
    offenders = []
    for path, text in _ui_sources():
        if path.name == "widgets.py":
            continue
        if "attr:plotly_chart" in _called_names(text):
            offenders.append(path.name)
    assert not offenders, f"请在 widgets.py 里用 render_chart 统一渲染：{offenders}"


def _series(days: int = 20, start: float = 100_000.0, step: float = 400.0) -> pd.Series:
    index = pd.bdate_range("2026-01-05", periods=days)
    return pd.Series([start + step * i for i in range(days)], index=index, dtype=float)


def test_benchmark_equity_fig_scales_benchmark_to_cash():
    a = _series()
    b = _series(start=90_000.0, step=200.0)
    benchmark = pd.Series(
        [1.0 + 0.01 * i for i in range(len(a))], index=a.index, dtype=float
    )

    fig = benchmark_equity_fig({"策略A": a, "策略B": b}, benchmark=benchmark, initial_cash=100_000.0)

    assert fig is not None
    # 基准先画，保证折线压在其他策略下面
    names = [trace.name for trace in fig.data]
    assert names == [BENCHMARK_LABEL, "策略A", "策略B"]
    bench_trace = fig.data[0]
    assert float(bench_trace.y[0]) == pytest.approx(100_000.0)
    assert float(bench_trace.y[-1]) == pytest.approx(100_000.0 * (1 + 0.01 * (len(a) - 1)))
    assert bench_trace.line.dash == "dash"
    assert bench_trace.line.color == cs.BENCHMARK
    assert fig.layout.height == cs.H_PRIMARY
    assert fig.layout.uirevision == cs.UIREVISION
    assert fig.layout.hovermode == "x unified"
    assert fig.layout.yaxis.side == "right"


def test_benchmark_equity_fig_without_benchmark_has_no_benchmark_trace():
    fig = benchmark_equity_fig({"策略A": _series()})
    assert fig is not None
    assert BENCHMARK_LABEL not in [trace.name for trace in fig.data]


def test_benchmark_equity_fig_returns_none_when_empty():
    assert benchmark_equity_fig({}) is None
    assert benchmark_equity_fig({"空": pd.Series(dtype=float)}) is None


def _bars(days: int = 40) -> pd.DataFrame:
    index = pd.bdate_range("2026-06-01", periods=days)
    close = [10.0 + 0.05 * i for i in range(days)]
    return pd.DataFrame(
        {"date": [d.strftime("%Y-%m-%d") for d in index], "close": close, "open": close, "high": close, "low": close}
    )


def test_price_level_fig_draws_all_reference_lines():
    fig = price_level_fig(
        _bars(),
        buy_price=10.5,
        stop_loss=9.6,
        take_profit=12.4,
        t_buy=10.2,
        t_sell=11.1,
    )

    assert fig is not None
    assert len(fig.layout.shapes) == 5
    texts = [annotation.text for annotation in fig.layout.annotations]
    for label in ("买入价", "止损", "止盈", "做T低吸", "做T高抛"):
        assert any(label in text for text in texts), f"缺少价位标签：{label}"
    assert fig.layout.height == cs.H_PRIMARY
    assert fig.layout.yaxis.side == "right"
    assert fig.data[0].line.color == cs.PRIMARY
    assert fig.data[0].x[0] == pd.Timestamp("2026-06-01")


def test_price_level_fig_without_levels_has_no_shapes():
    fig = price_level_fig(_bars())
    assert fig is not None
    assert len(fig.layout.shapes) == 0


def test_price_level_fig_returns_none_without_bars():
    assert price_level_fig(None) is None
    assert price_level_fig(pd.DataFrame()) is None
    assert price_level_fig(pd.DataFrame({"date": ["2026-06-01"], "open": [1.0]})) is None
