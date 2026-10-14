"""共享 UI 组件 — 板块筛选控件 + 名词速览 + 图表交互与收益曲线图。

供今日信号/回测/对比/策略拼装等页面复用（V4 需求 R2/R3/R5）。

图表交互（暂时需求 1）：所有 Plotly 图表统一走 PLOTLY_CONFIG，并通过
apply_time_axis 使用**真正的日期轴**，使刻度密度随缩放连续重算。
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_plan.factors.board import BOARDS, BOARD_DESC, filter_universe_ui

# ---------------------------------------------------------------------------
# 统一的 Plotly 交互配置（暂时需求 1：操作更便捷、缩放更连续）
#
# Streamlit 里 Plotly 图默认**不能用滚轮缩放**，只能框选做"阶梯式"放大，
# 而且双击、复位、导出都不顺手。这里统一打开：
#   - scrollZoom：滚轮 = 连续缩放，滚轮位置即缩放中心（最直接的"连续调整"）
#   - doubleClick=reset：双击回到全览
#   - dragmode=pan：拖拽默认平移而不是框选，框选缩放仍可用 Shift+拖拽
#   - 去掉与选股无关的 select2d/lasso2d/autoScale2d 按钮，工具条更清爽
# ---------------------------------------------------------------------------
PLOTLY_CONFIG: dict = {
    "scrollZoom": True,
    "displaylogo": False,
    "doubleClick": "reset",
    "showTips": True,
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
    "toImageButtonOptions": {
        "format": "png",
        "filename": "stock_plan_chart",
        "scale": 2,
    },
}

# 按缩放跨度自动切换日期格式（dtickrange 单位是毫秒 / "M1" 这类区间字符串）
_DATE_TICKFORMATSTOPS = [
    dict(dtickrange=[None, 604800000], value="%m-%d"),        # < 1 周
    dict(dtickrange=[604800000, "M1"], value="%m-%d"),        # 1 周 ~ 1 月
    dict(dtickrange=["M1", "M12"], value="%Y-%m"),            # 1 月 ~ 1 年
    dict(dtickrange=["M12", None], value="%Y"),               # > 1 年
]


def apply_time_axis(fig: go.Figure, title: str = "日期", rangeslider: bool = False) -> go.Figure:
    """把 x 轴设为真正的日期轴。

    原先不少图表把日期转成 `str(d)[:10]` 再画，Plotly 会当成**分类轴**：分类轴的
    刻度位置是固定的，缩放时既不重算密度也不换格式，所以出现"放大后标签仍挤在一起
    或仍很稀疏"。改用 DatetimeIndex 后，Plotly 会随缩放连续重算刻度间隔与格式
    （见 _DATE_TICKFORMATSTOPS），配合 PLOTLY_CONFIG 的 scrollZoom 即为连续缩放。

    rangeslider=True 时在图下方加一条可拖拽的区间选择条（另一种连续缩放方式），
    会额外占用高度，默认关闭。
    """
    fig.update_xaxes(
        type="date",
        title_text=title,
        tickformatstops=_DATE_TICKFORMATSTOPS,
        rangeslider=dict(visible=bool(rangeslider)),
        showspikes=True,
        spikemode="across",
        spikethickness=1,
        spikedash="dot",
        spikecolor="#888888",
    )
    return fig


def board_filter_ui(key_prefix: str = "uni") -> tuple[list[str], bool]:
    """渲染板块选择 + 剔除ST 控件，返回 (选中板块列表, 是否剔除ST)。默认全选。"""
    boards = st.multiselect(
        "股票板块（可多选，灵活组合）",
        list(BOARDS.keys()),
        default=list(BOARDS.keys()),
        key=f"{key_prefix}_boards",
        help="按交易所板块筛选股票。主板=沪深主板大中型企业；创业板/科创板=成长创新型（波动更大）；北交所=专精特新中小企业。想只看某类股票就只勾选它。",
    )
    for b in boards:
        st.caption(f"· {b}：{BOARD_DESC[b]}")
    exclude_st = st.checkbox(
        "剔除 ST / *ST 风险警示股（推荐开启）",
        value=True,
        key=f"{key_prefix}_st",
        help="ST 是被交易所标记「有退市风险」的股票，新手建议避开。",
    )
    return boards, bool(exclude_st)


def apply_universe_filter(
    stock_list: pd.DataFrame,
    bars_map: dict[str, pd.DataFrame],
    boards: list[str] | None,
    exclude_st: bool,
):
    """应用板块筛选与剔除 ST（内部转发到 factors.board.filter_universe_ui）。"""
    return filter_universe_ui(stock_list, bars_map, boards, exclude_st)


def page_glossary(terms: dict[str, str]):
    """页面顶部可折叠的「本页名词速览」。terms: {术语: 大白话解释}。"""
    with st.expander("📖 本页名词速览（不懂的词点这里）"):
        for term, desc in terms.items():
            st.markdown(f"- **{term}**：{desc}")


def equity_curve_fig(equity: pd.Series) -> go.Figure | None:
    """累计收益率曲线（%）+ 回撤区域标注（R3 需求）。

    参数：
        equity: 以日期为索引、总资产为值的 Series。

    返回：
        plotly Figure；数据为空时返回 None。
    """
    if equity is None or len(equity) < 2:
        return None
    base = float(equity.iloc[0])
    if base <= 0:
        return None
    pct = (equity / base - 1) * 100
    peak = pct.cummax()
    # 用真正的日期类型（不是 'YYYY-MM-DD' 字符串）：字符串会被 Plotly 当成分类轴，
    # 缩放下刻度密度与格式不会重算。
    x = pd.to_datetime(pct.index)
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=peak, mode="lines", name="历史最高点",
        line=dict(width=0), hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=x, y=pct, mode="lines", name="累计收益率",
        fill="tonexty", fillcolor="rgba(214,39,40,0.18)",
        line=dict(color="#1f77b4", width=2),
        hovertemplate="%{x|%Y-%m-%d}<br>累计收益率 %{y:.2f}%<extra></extra>",
    ))
    fig.update_layout(
        height=400,
        margin=dict(l=40, r=20, t=30, b=30),
        yaxis_title="累计收益率（%）",
        hovermode="x unified",
        dragmode="pan",
    )
    apply_time_axis(fig, title="日期", rangeslider=True)
    return fig
