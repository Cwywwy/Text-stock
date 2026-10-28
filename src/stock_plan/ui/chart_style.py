"""可视化设计基线：全站图表的颜色、字号、坐标轴与交互参数单一真源。

文件：src/stock_plan/ui/chart_style.py
作者：Cwywwy
用途：把「观感」拆成可复用的 token 与样式函数，供 widgets.py 的图表工厂调用。
      各 view **不直接**引用本模块（见 .github/plans/2026-10-28-1 第 4 节），
      这样将来换渲染后端（例如小程序用 ECharts）只改工厂一处。
所属模块：ui（展示层）

设计来源：观潮 TideView 的看盘界面（近黑画布、无背景网格、价格轴靠右、小字号紧凑
行高、克制的单色线 + 红涨绿跌）。参考图：.dsh-tmp/ref/screen-pre.webp。

图表八条规范（widgets 工厂内部即按此实现）：
1. 一图一结论：有标题 + 一句 caption，无标题不出图
2. 无背景网格；金额/价格类 y 轴靠右；x 轴必须是真日期轴
3. 颜色只表达数据语义（涨跌/基准/回撤），界面元素低饱和
4. 数值格式：金额千分位 + 万/亿缩写，百分比 2 位小数，时间 YYYY-MM-DD
5. hovermode="x unified" + 十字光标 spikes
6. uirevision 固定 → 控件 rerun 后保持缩放
7. 高度：主图 420 / 副图 240，窄屏由调用方用 columns 控制
8. 导出文件名带「页面-图名」

修订记录：
2026-10-28 初版：token、格式化函数与 apply_base_layout，供 Phase 0 图表工厂使用。
"""
from __future__ import annotations

import math

import pandas as pd
import plotly.graph_objects as go

# ---------------------------------------------------------------------------
# 设计 token —— 改配色只改这里
# ---------------------------------------------------------------------------

FONT_FAMILY = '"Inter", "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif'

CANVAS = "rgba(0, 0, 0, 0)"  # 图背景透明，直接融入 config.toml 的 #0E1117
PANEL = "#12161C"  # 面板 / 悬停框底色
GRID = "rgba(27, 32, 39, 0.55)"  # 极淡网格，默认关闭
AXIS_TEXT = "#8A9099"  # 轴刻度、图例文字
TEXT = "#E6E8EB"  # 正文
UP = "#F0616D"  # 涨 / 正收益（A 股习惯：红涨）
DOWN = "#2EBD85"  # 跌 / 负收益（绿跌）
PRIMARY = "#00C896"  # 品牌色 / 主曲线
BENCHMARK = "#8A9099"  # 基准曲线（虚线）
DRAWDOWN = "rgba(240, 97, 109, 0.16)"  # 回撤阴影
WARN = "#F5A524"  # 警示标注
NEUTRAL = "#5B6573"  # 次要元素

# 多序列配色：按出场顺序取色，保证「自己的策略」不会是看不见的 #333
SERIES: tuple[str, ...] = (
    PRIMARY,
    "#5B9BD5",
    "#B07FD0",
    "#F5A524",
    "#4FD1C5",
    "#E88BA8",
    "#9BA3AF",
    "#7BC96F",
)

H_PRIMARY = 420  # 主图高度
H_SECONDARY = 240  # 副图高度
FONT_AXIS = 11
FONT_TEXT = 12

# 固定 uirevision：控件 rerun 后 Plotly 保留缩放/平移状态，图不会跳回全览
UIREVISION = "stock-plan-charts-v1"


def series_colour(index: int) -> str:
    """按序号取调色板颜色（循环使用）。"""
    return SERIES[int(index) % len(SERIES)]


def up_down_colour(value: float | None) -> str:
    """按数值正负取涨跌色；0 也算涨（A 股习惯：平盘用涨色）。"""
    try:
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return NEUTRAL
        return DOWN if float(value) < 0 else UP
    except (TypeError, ValueError):
        return NEUTRAL


def fmt_money(value, *, digits: int = 0) -> str:
    """金额格式化：千分位，超过 1 万转「万」、超过 1 亿转「亿」。

    例：12345 → "12,345"；123456 → "12.35 万"；123456789 → "1.23 亿"。
    空值 / NaN / 非数值统一返回 "—"（表格里不会出现 nan）。
    """
    try:
        if value is None or pd.isna(value):
            return "—"
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if math.isnan(v) or math.isinf(v):
        return "—"
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e8:
        return f"{sign}{a / 1e8:.2f} 亿"
    if a >= 1e4:
        return f"{sign}{a / 1e4:.2f} 万"
    return f"{sign}{a:,.{digits}f}"


def fmt_pct(ratio, *, digits: int = 2) -> str:
    """比例 → 百分比字符串：0.1234 → "12.34%"。空值返回 "—"。"""
    try:
        if ratio is None or pd.isna(ratio):
            return "—"
        return f"{float(ratio) * 100:.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def fmt_pct_points(points, *, digits: int = 2) -> str:
    """已是百分比单位的数值 → 字符串：12.34 → "12.34%"（不再乘 100）。"""
    try:
        if points is None or pd.isna(points):
            return "—"
        return f"{float(points):.{digits}f}%"
    except (TypeError, ValueError):
        return "—"


def axis_money() -> dict:
    """金额轴：千分位、无小数。"""
    return {"tickformat": ",.0f", "tickprefix": "", "ticksuffix": ""}


def axis_pct(*, digits: int = 0) -> dict:
    """比例轴（0~1 的数据）：显示成百分比。"""
    return {"tickformat": f".{digits}%"}


def axis_pct_points(*, digits: int = 0) -> dict:
    """已是百分点的轴：直接加 % 后缀。"""
    return {"ticksuffix": "%", "tickformat": f",.{digits}f"}


def apply_base_layout(
    fig: go.Figure,
    *,
    height: int | None = None,
    y_title: str | None = None,
    right_axis: bool = True,
    hovermode: str = "x unified",
    show_grid: bool = False,
    zero_line: bool = False,
    legend: str | None = "h",
    margin: dict | None = None,
    **extra,
) -> go.Figure:
    """给任意 Figure 套上全站统一观感。

    参数：
        height: 图高（px）；None 用 Plotly 默认。
        y_title: y 轴标题。
        right_axis: y 轴放右侧（金额/价格类为 True）。
        hovermode: 默认 "x unified"（多线共用一个提示框）。
        show_grid: 是否显示网格，默认关闭（观潮手法）。
        zero_line: 是否画 0 轴参考线（柱状/收益类建议 True）。
        legend: 图例位置，"h" 横排置顶；None 不显示。
    """
    layout: dict = dict(
        paper_bgcolor=CANVAS,
        plot_bgcolor=CANVAS,
        font=dict(family=FONT_FAMILY, size=FONT_TEXT, color=TEXT),
        margin=margin or dict(l=8, r=56 if right_axis else 12, t=34, b=28),
        hovermode=hovermode,
        dragmode="pan",
        uirevision=UIREVISION,
        hoverlabel=dict(
            bgcolor=PANEL,
            bordercolor=GRID,
            font=dict(family=FONT_FAMILY, size=FONT_TEXT, color=TEXT),
            align="left",
        ),
        xaxis=dict(
            showgrid=False,
            zeroline=False,
            linecolor=GRID,
            tickfont=dict(size=FONT_AXIS, color=AXIS_TEXT),
            showspikes=True,
            spikemode="across",
            spikethickness=1,
            spikedash="dot",
            spikecolor=AXIS_TEXT,
        ),
        yaxis=dict(
            side="right" if right_axis else "left",
            showgrid=show_grid,
            gridcolor=GRID,
            zeroline=zero_line,
            zerolinecolor=GRID,
            zerolinewidth=1,
            linecolor=GRID,
            tickfont=dict(size=FONT_AXIS, color=AXIS_TEXT),
            automargin=True,
        ),
    )
    if height:
        layout["height"] = int(height)
    if y_title:
        layout["yaxis"]["title"] = dict(text=y_title, font=dict(size=FONT_AXIS, color=AXIS_TEXT))
    if legend == "h":
        layout["legend"] = dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="left",
            x=0,
            font=dict(size=FONT_AXIS, color=AXIS_TEXT),
            bgcolor=CANVAS,
        )
    elif legend is None:
        layout["showlegend"] = False
    else:
        layout["legend"] = dict(font=dict(size=FONT_AXIS, color=AXIS_TEXT), bgcolor=CANVAS)
    layout.update(extra)
    fig.update_layout(**layout)
    return fig


def add_drawdown_band(
    fig: go.Figure,
    x,
    cum_pct: pd.Series,
    *,
    name: str = "累计收益率",
    line_colour: str = PRIMARY,
    fill_colour: str = DRAWDOWN,
    width: int = 2,
) -> go.Figure:
    """画出「累计收益率曲线 + 回撤阴影」这一对 trace。

    实现：先加一条隐藏的最高点线（历史新高），再给收益率线加 fill="tonexty"，
    两条线之间的区域就是回撤区间。x 必须是日期类型（字符串会被当成分类轴）。

    注意：add_trace 顺序不能反 —— fill="tonexty" 填充的是「上一条 trace 到本 trace」
    之间的区域，所以最高点 trace 必须紧邻本 trace 之前加入。
    """
    peak = cum_pct.cummax()
    fig.add_trace(
        go.Scatter(
            x=x,
            y=peak,
            mode="lines",
            name="历史最高点",
            line=dict(width=0),
            hoverinfo="skip",
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=x,
            y=cum_pct,
            mode="lines",
            name=name,
            fill="tonexty",
            fillcolor=fill_colour,
            line=dict(color=line_colour, width=width),
            hovertemplate="%{x|%Y-%m-%d}<br>" + name + " %{y:.2f}%<extra></extra>",
        )
    )
    return fig


def zero_line_shape() -> dict:
    """横贯全图的 0 轴参考线（柱状收益图用）。"""
    return dict(
        type="line",
        xref="paper",
        x0=0,
        x1=1,
        yref="y",
        y0=0,
        y1=0,
        line=dict(color=GRID, width=1),
        layer="below",
    )
