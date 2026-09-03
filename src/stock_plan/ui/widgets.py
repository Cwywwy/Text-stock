"""共享 UI 组件 — 板块筛选控件 + 名词速览 + 收益曲线图。

供今日信号/回测/对比/策略拼装等页面复用（V4 需求 R2/R3/R5）。
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_plan.factors.board import BOARDS, BOARD_DESC, filter_universe_ui


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
    x = [str(d)[:10] for d in pct.index]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=x, y=peak, mode="lines", name="历史最高点",
        line=dict(width=0), hoverinfo="skip", showlegend=False,
    ))
    fig.add_trace(go.Scatter(
        x=x, y=pct, mode="lines", name="累计收益率",
        fill="tonexty", fillcolor="rgba(214,39,40,0.18)",
        line=dict(color="#1f77b4", width=2),
        hovertemplate="%{x}<br>累计收益率 %{y:.2f}%<extra></extra>",
    ))
    fig.update_layout(
        height=400,
        margin=dict(l=40, r=20, t=30, b=30),
        yaxis_title="累计收益率（%）",
        xaxis_title="日期",
        hovermode="x unified",
    )
    return fig
