"""共享 UI 组件 — 板块筛选控件 + 名词速览 + 图表交互与收益曲线图。

供今日信号/回测/对比/策略拼装等页面复用（V4 需求 R2/R3/R5）。

图表交互（暂时需求 1）：所有 Plotly 图表统一走 PLOTLY_CONFIG，并通过
apply_time_axis 使用**真正的日期轴**，使刻度密度随缩放连续重算。

Revision History:
    2026-10-28  (phase 0) add chart_style tokens, chart_config/render_chart
    2026-10-28  (phase 2) add score_waterfall_fig for signal explainability
    2026-10-28  (phase 3) waterfall: drop axis title, pad x range so edge bars fit
2026-10-28  (branch4 phase 0) one shared backtest_hero_fig with optional trade markers
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_plan.factors.board import BOARDS, BOARD_DESC, filter_universe_ui
from stock_plan.ui import chart_style as cs

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
    "responsive": True,
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d"],
    "toImageButtonOptions": {
        "format": "png",
        "filename": "stock_plan_chart",
        "scale": 2,
    },
}
# 注意：dragmode=pan 是 layout 属性、不是 config 属性，由 chart_style.apply_base_layout
# 统一设置（原注释声称 config 里设了 dragmode，实际没有 —— 2026-10-28 修正）。


def chart_config(name: str = "chart") -> dict:
    """按图名生成 Plotly 配置：导出的 PNG 文件名带图名，不再所有图都叫 stock_plan_chart。"""
    cfg = dict(PLOTLY_CONFIG)
    cfg["toImageButtonOptions"] = dict(PLOTLY_CONFIG["toImageButtonOptions"])
    cfg["toImageButtonOptions"]["filename"] = f"stock_plan_{name}"
    return cfg


def render_chart(
    fig: go.Figure,
    *,
    name: str = "chart",
    caption: str | None = None,
    height: int | None = None,
) -> None:
    """统一渲染一张图。

    - 宽度用 1.63 起的新写法 `width="stretch"`（`use_container_width` 已弃用）
    - 导出名按图名区分
    - caption 是「一句话解读」，按图表规范第 1 条，图下面必须说明它在讲什么
    """
    if fig is None:
        st.info("暂无数据，无法绘图。")
        return
    if height:
        fig.update_layout(height=int(height))
    st.plotly_chart(fig, width="stretch", config=chart_config(name))
    if caption:
        st.caption(caption)


def multi_line_fig(
    series_map: dict[str, pd.Series],
    *,
    height: int = cs.H_PRIMARY,
    y_title: str | None = None,
    money: bool = False,
    pct_points: bool = False,
    benchmark: str | None = None,
    rangeslider: bool = False,
) -> go.Figure | None:
    """多序列折线图（真日期轴 + 统一 hover + 调色板）。

    参数：
        series_map: {系列名: 以日期为索引的 Series}。
        money: y 轴按金额千分位格式化。
        pct_points: y 轴数值已是百分点，直接加 %。
        benchmark: 基准系列名，画成灰色虚线并排在图例最后。
        rangeslider: 是否在图下方加区间选择条（会额外占高，默认关）。
    """
    if not series_map:
        return None
    fig = go.Figure()
    items = [(n, s) for n, s in series_map.items() if s is not None and len(s) > 0]
    if not items:
        return None
    # 基准先画，让它压在主线下面；但基准不占用系列色板，
    # 否则第一条策略会拿到第二种颜色，品牌主色白白浪费在灰虚线上。
    items.sort(key=lambda kv: 0 if kv[0] == benchmark else 1)
    colour_index = 0
    for name, ser in items:
        is_bench = name == benchmark
        colour = cs.BENCHMARK if is_bench else cs.series_colour(colour_index)
        if not is_bench:
            colour_index += 1
        fig.add_trace(
            go.Scatter(
                x=pd.to_datetime(ser.index),
                y=ser.to_numpy(),
                mode="lines",
                name=name,
                line=dict(
                    color=colour,
                    width=1.5 if is_bench else 2,
                    dash="dash" if is_bench else None,
                ),
            )
        )
    cs.apply_base_layout(fig, height=height, y_title=y_title, zero_line=pct_points)
    if money:
        fig.update_yaxes(**cs.axis_money())
    elif pct_points:
        fig.update_yaxes(**cs.axis_pct_points())
    apply_time_axis(fig, rangeslider=rangeslider)
    return fig


# 按缩放跨度自动切换日期格式（dtickrange 单位是毫秒 / "M1" 这类区间字符串）
_DATE_TICKFORMATSTOPS = [
    dict(dtickrange=[None, 604800000], value="%m-%d"),        # < 1 周
    dict(dtickrange=[604800000, "M1"], value="%m-%d"),        # 1 周 ~ 1 月
    dict(dtickrange=["M1", "M12"], value="%Y-%m"),            # 1 月 ~ 1 年
    dict(dtickrange=["M12", None], value="%Y"),               # > 1 年
]


def apply_time_axis(fig: go.Figure, title: str = "", rangeslider: bool = False) -> go.Figure:
    """把 x 轴设为真正的日期轴。

    原先不少图表把日期转成 `str(d)[:10]` 再画，Plotly 会当成**分类轴**：分类轴的
    刻度位置是固定的，缩放时既不重算密度也不换格式，所以出现"放大后标签仍挤在一起
    或仍很稀疏"。改用 DatetimeIndex 后，Plotly 会随缩放连续重算刻度间隔与格式
    （见 _DATE_TICKFORMATSTOPS），配合 PLOTLY_CONFIG 的 scrollZoom 即为连续缩放。

    标题默认留空：日期轴自解释，写上「日期」反而会和刻度标签抢位置（2026-10-28）。

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
        spikecolor=cs.AXIS_TEXT,
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

    from stock_plan.data.clock import today_cn
    from stock_plan.data.storage import Storage
    from stock_plan.factors.regime import (
        MIN_CONTRIBUTORS,
        detect_regime,
        equal_weight_index,
        regime_table,
        suggest_playbook,
    )

    end = today_cn()
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
                line=dict(color=cs.PRIMARY, width=2),
            ))
            fig.add_trace(go.Scatter(
                x=table.index, y=table["ma20"], mode="lines", name="MA20",
                line=dict(color=cs.series_colour(1), width=1),
            ))
            fig.add_trace(go.Scatter(
                x=table.index, y=table["ma60"], mode="lines", name="MA60",
                line=dict(color=cs.series_colour(2), width=1),
            ))
            cs.apply_base_layout(fig, height=cs.H_SECONDARY, y_title="等权指数（起点 = 1）")
            apply_time_axis(fig)
            render_chart(fig, name="market-regime")
        st.caption(
            "等权指数 = 每只股票的日收益率等权平均后累乘 —— 与个股价位无关，"
            "也不会因为成分股增减而跳空。"
        )


BENCHMARK_LABEL = "等权基准（持有全部候选股）"


def _trade_markers(fig: go.Figure, trades: pd.DataFrame | None, curve: pd.Series) -> None:
    """在资金曲线上标出买卖点：▲ 买（红）/ ▼ 卖（绿），与红涨绿跌一致。

    成交日不是每根 K 线都有，落在曲线上时取「当日或之前最近一个点」的曲线值，
    避免标记悬空。默认不画，由调用方按需传入 trades。
    """
    if trades is None or len(trades) == 0 or "entry_date" not in trades.columns:
        return
    values = pd.Series(curve, dtype="float64").dropna()
    if values.empty:
        return
    values.index = pd.to_datetime(values.index)

    for column, price_column, symbol, colour, name in (
        ("entry_date", "entry_price", "triangle-up", cs.UP, "买入"),
        ("exit_date", "exit_price", "triangle-down", cs.DOWN, "卖出"),
    ):
        if column not in trades.columns:
            continue
        xs: list[pd.Timestamp] = []
        ys: list[float] = []
        texts: list[str] = []
        for _, row in trades.iterrows():
            day = row.get(column)
            if pd.isna(day):
                continue
            stamp = pd.Timestamp(day)
            pos = values.index.searchsorted(stamp, side="right") - 1
            if pos < 0:
                continue
            price = row.get(price_column) if price_column in trades.columns else None
            label = f"{name} {row.get('code', '')}"
            if price is not None and pd.notna(price):
                label += f" · {float(price):.2f} 元"
            xs.append(stamp)
            ys.append(float(values.iloc[pos]))
            texts.append(label)
        if not xs:
            continue
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=ys,
                mode="markers",
                name=name,
                marker=dict(symbol=symbol, size=9, color=colour, line=dict(width=0)),
                text=texts,
                hovertemplate="%{text}<br>%{x|%Y-%m-%d}<extra></extra>",
            )
        )


def backtest_hero_fig(
    result,
    *,
    mode: str = "收益率",
    trades: pd.DataFrame | None = None,
    height: int | None = None,
) -> go.Figure | None:
    """回测主图：策略资金曲线 + 等权基准（灰色虚线）+ 回撤带，金额/收益率双口径。

    参数：
        result: ``BacktestResult``，需要 ``equity_curve``，``benchmark`` 可选。
        mode: 「收益率」（累计收益率 %）或「金额」（总资产，元）。
        trades: 传入则标出买卖点；默认不传，因为百笔交易糊在图上会看不清。
        height: 图高，默认 ``chart_style.H_PRIMARY``。

    这是全站唯一的主图工厂：回测页、可视化构建页、样张脚本都调它，
    避免「两处各写一遍、改一处忘另一处」。
    """
    equity = getattr(result, "equity_curve", None)
    if equity is None or len(equity) < 2:
        return None
    equity = pd.Series(equity, dtype="float64").dropna()
    if len(equity) < 2:
        return None
    base = float(equity.iloc[0])
    if base <= 0:
        return None
    x = pd.to_datetime(equity.index)
    benchmark = getattr(result, "benchmark", None)
    money = mode == "金额"

    fig = go.Figure()
    if money:
        curve = equity
        fig.add_trace(
            go.Scatter(
                x=x,
                y=curve.to_numpy(),
                mode="lines",
                name="总资产",
                line=dict(color=cs.PRIMARY, width=2),
                hovertemplate="%{x|%Y-%m-%d}<br>总资产 %{y:,.0f} 元<extra></extra>",
            )
        )
        cs.add_level_line(
            fig,
            base,
            label="初始资金",
            colour=cs.BENCHMARK,
            value_text=cs.fmt_money(base),
        )
    else:
        curve = (equity / base - 1.0) * 100.0
        cs.add_drawdown_band(fig, x, curve, name="策略")
        cs.add_level_line(fig, 0.0, label="零收益", colour=cs.GRID)

    if benchmark is not None and len(benchmark) > 0:
        bench = pd.Series(benchmark, dtype="float64").dropna()
        if len(bench) > 0:
            bench_base = float(bench.iloc[0])
            if money:
                bench_series = bench * base
                hover = "%{x|%Y-%m-%d}<br>基准 %{y:,.0f} 元<extra></extra>"
            else:
                bench_series = (bench / bench_base - 1.0) * 100.0 if bench_base > 0 else bench * 0.0
                hover = "%{x|%Y-%m-%d}<br>基准 %{y:.2f}%<extra></extra>"
            fig.add_trace(
                go.Scatter(
                    x=pd.to_datetime(bench.index),
                    y=bench_series.to_numpy(),
                    mode="lines",
                    name=BENCHMARK_LABEL,
                    line=dict(color=cs.BENCHMARK, width=1.4, dash="dot"),
                    hovertemplate=hover,
                )
            )

    _trade_markers(fig, trades, curve)

    cs.apply_base_layout(
        fig,
        height=height or cs.H_PRIMARY,
        y_title="总资产（元）" if money else "累计收益率（%）",
        zero_line=not money,
        legend="h",
    )
    if money:
        fig.update_yaxes(**cs.axis_money())
    else:
        fig.update_yaxes(**cs.axis_pct_points())
    apply_time_axis(fig)
    return fig


def benchmark_equity_fig(
    series_map: dict[str, pd.Series],
    *,
    benchmark: pd.Series | None = None,
    initial_cash: float = 100_000.0,
    height: int | None = None,
    y_title: str | None = "总资产（元）",
) -> go.Figure | None:
    """多条资金曲线 + 等权基准（策略对比页）。

    基准序列是「归一化到 1.0」的指数，必须乘初始资金才能和资产曲线同量纲——
    直接把 1.0~1.2 的指数画到 10 万的轴上会变成一条贴底的直线。
    """
    series: dict[str, pd.Series] = {
        name: value
        for name, value in (series_map or {}).items()
        if value is not None and len(value) > 0
    }
    bench_label: str | None = None
    if benchmark is not None and len(benchmark) > 0:
        series[BENCHMARK_LABEL] = benchmark.astype(float) * float(initial_cash)
        bench_label = BENCHMARK_LABEL
    if not series:
        return None
    return multi_line_fig(
        series,
        height=height or cs.H_PRIMARY,
        y_title=y_title,
        money=True,
        benchmark=bench_label,
    )


def price_level_fig(
    bars: pd.DataFrame,
    *,
    buy_price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    t_buy: float | None = None,
    t_sell: float | None = None,
    tail: int = 120,
    height: int | None = None,
) -> go.Figure | None:
    """个股收盘价 + 买入 / 止损 / 止盈 / 做T 价位参考线（持仓诊断页）。"""
    if bars is None or bars.empty or "close" not in bars.columns:
        return None
    window = bars.tail(int(tail)) if tail else bars
    fig = go.Figure(
        go.Scatter(
            x=pd.to_datetime(window["date"]),
            y=window["close"],
            mode="lines",
            name="收盘价",
            line=dict(color=cs.PRIMARY, width=2),
            hovertemplate="%{x|%Y-%m-%d}<br>收盘 %{y:.2f} 元<extra></extra>",
        )
    )
    for value, label, colour, dash in (
        (buy_price, "买入价", cs.LEVEL_BUY, "dot"),
        (stop_loss, "止损", cs.LEVEL_STOP, "dash"),
        (take_profit, "止盈", cs.LEVEL_TARGET, "dash"),
        (t_buy, "做T低吸", cs.LEVEL_T_BUY, "dot"),
        (t_sell, "做T高抛", cs.LEVEL_T_SELL, "dot"),
    ):
        if value:
            cs.add_level_line(fig, value, label=label, colour=colour, dash=dash)
    cs.apply_base_layout(fig, height=height or cs.H_PRIMARY, y_title="价格（元）", right_axis=True)
    apply_time_axis(fig)
    return fig


def score_waterfall_fig(report) -> go.Figure | None:
    """「入选门槛 vs 紧随其后」的分差瀑布图（信号解释报告用）。

    画法：每只落选股一根柱子，柱底落在**入选门槛**（第 N 名的综合分），柱高 = 它与
    门槛的分数差。这样所有柱子共用一条基准线，肉眼立刻看出「差 0.1 分」和「差 6 分」
    的区别；比把 87.6 / 87.1 / 86.2 三个数字画成从 0 开始的柱状图清楚得多。

    颜色沿用红涨绿跌：高于门槛（几乎不会出现）为红、低于门槛为绿、持平为灰。

    参数：
        report: SignalReport（只要有 `picked` / `runners_up` / `entry_gap` 即可）。

    返回：
        plotly Figure；没有入选者或没有落选者时返回 None（页面据此不渲染图）。
    """
    if report is None:
        return None
    picked = list(getattr(report, "picked", ()) or ())
    runners = list(getattr(report, "runners_up", ()) or ())
    if not picked or not runners:
        return None

    gate_item = picked[-1]
    gate = float(gate_item.score)
    labels: list[str] = []
    deltas: list[float] = []
    scores: list[float] = []
    names: list[str] = []
    for item in runners:
        score = float(item.score)
        # 不要用 "#6 300908" 这类标签：以 # 加数字开头的字符串会被 plotly.js 当成
        # 数值（"#6 300908" → 6300908），x 轴退化成 6M/7M 的数值轴。
        labels.append(f"第{item.rank}名 {item.code}")
        deltas.append(score - gate)
        scores.append(score)
        names.append(str(item.name))

    colours = [
        cs.DOWN if delta < 0 else (cs.NEUTRAL if delta == 0 else cs.UP)
        for delta in deltas
    ]
    score_labels = [f"{score:.1f}" for score in scores]
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=labels,
            y=deltas,
            base=[gate] * len(deltas),
            # 只有两三只落选股，柱子按容器宽度铺开就变成"大色块"、看着很肥；
            # width 是分类轴单位（一格 = 1），固定成 0.36 格让柱子窄下来、留白变多。
            width=0.36,
            marker=dict(color=colours, line=dict(width=0)),
            customdata=[[name, score, delta] for name, score, delta in zip(names, scores, deltas)],
            hovertemplate=(
                "%{x} %{customdata[0]}<br>综合分 %{customdata[1]:.1f}"
                "<br>与门槛 %{customdata[2]:+.1f} 分<extra></extra>"
            ),
            name="紧随其后",
            showlegend=False,
        )
    )
    # 分差为 0 时柱子高度是 0、什么都看不见，所以额外画一个分数圆点：圆点落在门槛
    # 虚线上就说明「并列」，落在下方就是落后多少，比只有柱子稳。
    fig.add_trace(
        go.Scatter(
            x=labels,
            y=scores,
            mode="markers+text",
            text=score_labels,
            textposition="top center",
            textfont=dict(size=cs.FONT_AXIS, color=cs.TEXT),
            marker=dict(size=6, color=colours, line=dict(width=0)),
            hoverinfo="skip",
            showlegend=False,
            cliponaxis=False,
        )
    )
    fig.add_hline(
        y=gate,
        line=dict(color=cs.BENCHMARK, width=1, dash="dot"),
    )
    fig.update_xaxes(type="category")
    span = max(scores + [gate]) - min(scores + [gate])
    pad = max(0.3, span * 0.25)
    cs.apply_base_layout(
        fig,
        height=cs.H_SECONDARY,
        y_title=None,
        right_axis=False,
        legend=None,
        margin=dict(l=44, r=14, t=24, b=26),
    )
    # 两边各留半格，避免首尾柱子贴着画布边缘被裁掉
    fig.update_xaxes(range=[-0.55, len(labels) - 0.45])
    fig.update_yaxes(range=[min(scores + [gate]) - pad, max(scores + [gate]) + pad])
    return fig
