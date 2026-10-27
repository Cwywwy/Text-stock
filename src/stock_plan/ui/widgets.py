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


def industry_filter_ui(key_prefix: str = "uni", level: str = "sw_l1") -> list[str]:
    """申万行业多选（暂时需求 3：有时要「通过行业板块指定策略」的适用范围）。

    为什么默认只给一级行业（31 个）：
    - 二级 124 个、三级 335 个，做常用筛选太长，用户自己也记不住；
    - 本轮分类快照里**申万三级为 0**（主源乐咕乐股整站 504，已降级到申万官网接口，
      而官网只提供一级/二级名录），拿三级出来会是空列表；
    - 一级 31 个正好对应"选赛道"这个真实用法。

    返回空列表 = 不过滤（与板块筛选的口径一致：不勾选就是不限）。
    """
    from stock_plan.data import classification

    opts = _industry_options_cached(level)
    if not opts:
        st.caption("暂无行业分类快照，行业筛选不可用（到左侧「🔄 数据更新」页抓取分类数据）。")
        return []
    return st.multiselect(
        "行业板块（申万一级，可多选；不选 = 不限行业）",
        opts,
        default=[],
        key=f"{key_prefix}_industries",
        help="只在这些行业里选股。适合「这只策略只做某个赛道」的用法。",
    )


@st.cache_data(ttl=3600, show_spinner=False)
def _industry_options_cached(level: str = "sw_l1") -> list[str]:
    """行业名列表缓存 1 小时：每次页面渲染都读一次 parquet 太浪费。"""
    from stock_plan.data import classification

    return classification.industry_options(level)


def page_glossary(terms: dict[str, str]):
    """页面顶部可折叠的「本页名词速览」。terms: {术语: 大白话解释}。"""
    with st.expander("📖 本页名词速览（不懂的词点这里）"):
        for term, desc in terms.items():
            st.markdown(f"- **{term}**：{desc}")


# ---------------------------------------------------------------------------
# 市场阶段面板（暂时需求 6）
# ---------------------------------------------------------------------------

def _fmt_pct(value, digits: int = 1, suffix: str = "%") -> str:
    """把可能是 NaN 的比例值格式化成字符串。"""
    try:
        if value is None or pd.isna(value):
            return "—"
        return f"{float(value) * 100:.{digits}f}{suffix}"
    except Exception:
        return "—"


@st.cache_data(ttl=1800, show_spinner="正在判断市场阶段…")
def _regime_data(max_codes: int = 800, lookback_days: int = 900):
    """算等权指数与市场阶段，返回纯 dict / DataFrame 以便被缓存。"""
    from datetime import date, timedelta

    from stock_plan.data.storage import Storage
    from stock_plan.factors.regime import (
        MIN_CONTRIBUTORS,
        detect_regime,
        equal_weight_index,
        regime_table,
        suggest_playbook,
    )

    end = date.today()
    start = end - timedelta(days=lookback_days)
    _, bars_map, _ = Storage().load_market_maps(start=start, end=end, max_codes=max_codes)
    if not bars_map:
        return None
    index = equal_weight_index(
        {code: bars_map[code] for code in bars_map},
        min_contributors=max(1, min(MIN_CONTRIBUTORS, len(bars_map))),
    )
    if index is None or index.empty:
        return None
    snap = detect_regime(index)
    return {
        "snapshot": snap.to_dict(),
        "plan": suggest_playbook(snap),
        "table": regime_table(index).tail(300),
        "n_codes": len(bars_map),
    }


def market_regime_panel(expanded: bool = False) -> None:
    """「当前市场处于什么阶段」面板 + 分阶段策略与仓位建议。

    大盘用**等权收益指数**（不是平均原始股价）：不受高价股主导，也不会因成分股
    增减而跳空。阶段判定规则见 factors/regime.py 的模块文档。
    """
    data = _regime_data()
    if not data or not data.get("snapshot"):
        st.caption("暂无足够数据判断市场阶段（请先到「🔄 数据更新」页拉取数据）。")
        return

    snap = data["snapshot"]
    plan = data["plan"]

    st.markdown("#### 🧭 当前市场阶段")
    cols = st.columns(4)
    cols[0].metric("阶段", snap.get("phase", "—"))
    cols[1].metric(
        "波动率", snap.get("volatility", "—"),
        f"历史 {_fmt_pct(snap.get('vol_percentile'), 0)} 分位",
    )
    cols[2].metric("建议仓位", _fmt_pct(plan.get("position"), 0), plan.get("stance", ""))
    cols[3].metric("通道位置", _fmt_pct(snap.get("channel_pos"), 0), f"样本 {data['n_codes']} 只")

    for line in snap.get("explain", []):
        st.caption(f"· {line}")

    prefer = "、".join(plan.get("prefer", ()) or ())
    st.info(f"**{plan.get('stance', '')}**｜偏好策略：{prefer}。{plan.get('note', '')}")

    with st.expander("📈 等权指数与均线", expanded=expanded):
        table = data.get("table")
        if table is not None and not table.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=table.index, y=table["close"], mode="lines", name="等权指数",
                line=dict(color="#1f77b4", width=2),
            ))
            fig.add_trace(go.Scatter(
                x=table.index, y=table["ma20"], mode="lines", name="MA20",
                line=dict(color="#ff7f0e", width=1),
            ))
            fig.add_trace(go.Scatter(
                x=table.index, y=table["ma60"], mode="lines", name="MA60",
                line=dict(color="#2ca02c", width=1),
            ))
            fig.update_layout(
                height=280, margin=dict(l=40, r=20, t=20, b=30),
                yaxis_title="等权指数（起点 = 1）", hovermode="x unified",
            )
            apply_time_axis(fig)
            st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)
        st.caption(
            "等权指数 = 每只股票的日收益率等权平均后累乘 —— 与个股价位无关，"
            "也不会因为成分股增减而跳空。"
        )


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
