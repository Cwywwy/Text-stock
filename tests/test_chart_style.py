# -*- coding: utf-8 -*-
"""图表样式基线测试（2026-10-28 可视化升级 Phase 0）。

只测纯函数与 Plotly Figure 的结构属性，不启动 Streamlit runtime：
- 色板/token 完整性
- 金额与百分比格式化的边界（0、负数、亿级、None、NaN、非法字符串）
- apply_base_layout 的观感属性（透明背景、右侧 y 轴、无网格、x unified、uirevision）
- 回撤阴影 trace 的顺序与填充关系（fill="tonexty" 依赖上一条 trace，顺序错了就画不出来）
- 旧配色与弃用写法不再回流到 widgets.py
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from stock_plan.ui import chart_style as cs

_SRC = Path(cs.__file__).resolve().parent


def _curve() -> pd.Series:
    """构造一段有涨有跌的累计收益率序列（单位：百分点）。"""
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    values = [0.0, 5.0, -3.0, 8.0, -12.0] + [0.0] * 25
    return pd.Series(values, index=idx)


# ---------------------------------------------------------------------------
# token
# ---------------------------------------------------------------------------

def test_palette_has_no_duplicates_and_primary_first() -> None:
    assert len(cs.SERIES) >= 6
    assert len(set(cs.SERIES)) == len(cs.SERIES)
    assert cs.SERIES[0] == cs.PRIMARY
    for colour in cs.SERIES:
        assert colour.startswith("#") and len(colour) == 7


def test_up_down_colours_follow_a_share_convention() -> None:
    """A 股习惯：红涨绿跌，与观潮一致。"""
    assert cs.UP == "#F0616D"
    assert cs.DOWN == "#2EBD85"
    assert cs.up_down_colour(1.0) == cs.UP
    assert cs.up_down_colour(-0.01) == cs.DOWN
    assert cs.up_down_colour(0) == cs.UP  # 平盘算涨色
    assert cs.up_down_colour(None) == cs.NEUTRAL
    assert cs.up_down_colour(float("nan")) == cs.NEUTRAL


def test_heights_and_fonts() -> None:
    assert cs.H_PRIMARY > cs.H_SECONDARY > 0
    assert cs.FONT_AXIS <= cs.FONT_TEXT
    assert cs.UIREVISION  # 必须固定，否则 rerun 后缩放复位


def test_series_colour_wraps_around() -> None:
    assert cs.series_colour(0) == cs.SERIES[0]
    assert cs.series_colour(len(cs.SERIES)) == cs.SERIES[0]
    assert cs.series_colour(len(cs.SERIES) + 2) == cs.SERIES[2]


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------

def test_fmt_money_thousand_and_units() -> None:
    assert cs.fmt_money(0) == "0"
    assert cs.fmt_money(9999) == "9,999"
    assert cs.fmt_money(12345) == "1.23 万"
    assert cs.fmt_money(-123456) == "-12.35 万"
    assert cs.fmt_money(123456789) == "1.23 亿"
    assert cs.fmt_money(np.float64(1_000_000)) == "100.00 万"


def test_fmt_money_bad_values_return_dash() -> None:
    for bad in (None, float("nan"), float("inf"), "abc", {}):
        assert cs.fmt_money(bad) == "—"


def test_fmt_pct_ratio_and_points() -> None:
    assert cs.fmt_pct(0.1234) == "12.34%"
    assert cs.fmt_pct(0) == "0.00%"
    assert cs.fmt_pct(-0.05) == "-5.00%"
    assert cs.fmt_pct(None) == "—"
    assert cs.fmt_pct(float("nan")) == "—"
    assert cs.fmt_pct_points(12.34) == "12.34%"
    assert cs.fmt_pct_points(None) == "—"


def test_axis_format_helpers() -> None:
    assert cs.axis_money()["tickformat"] == ",.0f"
    assert cs.axis_pct(digits=2)["tickformat"] == ".2%"
    assert cs.axis_pct_points(digits=1)["ticksuffix"] == "%"


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

def test_apply_base_layout_defaults() -> None:
    fig = cs.apply_base_layout(go.Figure(), height=cs.H_PRIMARY, y_title="累计收益率（%）")
    layout = fig.layout
    assert layout.paper_bgcolor == cs.CANVAS
    assert layout.plot_bgcolor == cs.CANVAS
    assert layout.height == cs.H_PRIMARY
    assert layout.hovermode == "x unified"
    assert layout.uirevision == cs.UIREVISION
    assert layout.dragmode == "pan"
    assert layout.yaxis.side == "right"
    # 右轴的标题改成右上角横排注释（竖排标题会压住刻度标签，2026-10-28 样张复查）。
    assert not layout.yaxis.title.text
    assert [a.text for a in layout.annotations] == ["累计收益率（%）"]
    assert not layout.xaxis.showgrid
    assert not layout.yaxis.showgrid  # 观潮手法：默认无网格
    assert layout.font.family == cs.FONT_FAMILY


def test_apply_base_layout_left_axis_and_grid() -> None:
    fig = cs.apply_base_layout(go.Figure(), right_axis=False, show_grid=True, zero_line=True)
    assert fig.layout.yaxis.side == "left"
    assert fig.layout.yaxis.showgrid
    assert fig.layout.yaxis.zeroline


def test_apply_base_layout_legend_options() -> None:
    horizontal = cs.apply_base_layout(go.Figure(), legend="h")
    assert horizontal.layout.legend.orientation == "h"
    hidden = cs.apply_base_layout(go.Figure(), legend=None)
    assert hidden.layout.showlegend is False


# ---------------------------------------------------------------------------
# 回撤阴影
# ---------------------------------------------------------------------------

def test_add_drawdown_band_orders_peak_before_curve() -> None:
    curve = _curve()
    fig = go.Figure()
    cs.add_drawdown_band(fig, curve.index, curve)

    assert len(fig.data) == 2
    peak, line = fig.data
    # 最高点必须紧邻曲线之前：fill="tonexty" 填充的是上一条 trace 到本 trace 之间的区域
    assert peak.hoverinfo == "skip"
    assert peak.showlegend is False
    assert float(peak.line.width) == 0.0
    assert line.fill == "tonexty"
    assert line.fillcolor == cs.DRAWDOWN
    assert list(line.y) == list(curve.to_numpy())
    assert list(peak.y) == list(curve.cummax().to_numpy())


def test_add_drawdown_band_accepts_dates_as_objects() -> None:
    curve = _curve()
    fig = go.Figure()
    cs.add_drawdown_band(fig, pd.to_datetime(curve.index), curve, name="策略", line_colour=cs.PRIMARY)
    assert fig.data[1].name == "策略"
    assert "策略" in fig.data[1].hovertemplate


# ---------------------------------------------------------------------------
# 防回流：旧配色 / 弃用写法不应再出现在 widgets.py
# ---------------------------------------------------------------------------

def test_widgets_no_legacy_plotly_default_colours() -> None:
    source = (_SRC / "widgets.py").read_text(encoding="utf-8")
    for legacy in ("#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"):
        assert legacy not in source, f"widgets.py 仍写死旧配色 {legacy}，请改用 chart_style"


def test_widgets_no_deprecated_use_container_width() -> None:
    source = (_SRC / "widgets.py").read_text(encoding="utf-8")
    # 文档字符串里可提到这个词（解释为什么不用），所以只检查代码行
    offenders = [
        line for line in source.splitlines()
        if "use_container_width" in line and not line.lstrip().startswith(("#", "-", '"""', "*"))
    ]
    assert offenders == [], f"widgets.py 代码里仍用 use_container_width：{offenders}"
