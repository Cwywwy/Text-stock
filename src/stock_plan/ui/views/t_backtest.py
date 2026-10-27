"""做T回测页面 — 底仓 + 周内高抛低吸（暂时需求续·工作流②）。

展示内容：
- 底仓设置：选股范围（板块/申万一级行业）+ 参与只数
- T 策略参数：触发跌幅、止盈/止损、周内天数、周五强平
- 结果：底仓不动 vs 底仓+做T 的对照、T 笔数与胜率、资金曲线、逐笔明细
- 参数敏感性：一组触发阈值下"做T贡献"的变化（避免只看单次 A/B 就下结论）

Revision History:
    2026-10-27  新建：接入 backtest/t_trade.py（独立模拟器，不改既有引擎）
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_plan.backtest.t_trade import TConfig, run_t_backtest
from stock_plan.data.storage import Storage
from stock_plan.ui.widgets import apply_time_axis, PLOTLY_CONFIG, board_filter_ui, page_glossary

T_GLOSSARY = {
    "底仓": "长期持有的那部分仓位，本页里买了就不动，作为对照。",
    "做T": "在底仓之上低买高卖同一只股票，赚当天的波动差价。本页只做「正T」：先买后卖。",
    "T+1": "A 股当天买入的股票当天不能卖，最早次日才能卖——本页强制遵守。",
    "胜率": "做T的每一笔里，赚钱的占多少比例。样本少于 30 笔时这个数字很不稳。",
}


def _pick_codes(
    boards: list[str], exclude_st: bool, industries: list[str], n_codes: int
) -> list[str]:
    """按板块 + 行业挑出参与回测的代码（取前 n 个，保证可复现）。"""
    storage = Storage()
    stock_list = storage.load_stock_list()
    if stock_list.empty:
        return []
    codes = stock_list["code"].astype(str).tolist()
    if exclude_st and "is_st" in stock_list.columns:
        codes = stock_list.loc[stock_list["is_st"] != 1, "code"].astype(str).tolist()

    from stock_plan.factors.board import filter_codes_by_boards

    codes = filter_codes_by_boards(codes, boards)
    if industries:
        from stock_plan.data.classification import filter_codes_by_industries

        codes = filter_codes_by_industries(codes, industries, level="sw_l1")
    # 只保留本地有缓存的代码，避免把"没数据"当成"没交易"
    codes = [c for c in codes if storage.cache_exists(c)]
    return codes[:n_codes]


@st.cache_data(ttl=600, show_spinner="正在跑做T回测…")
def _cached_t_backtest(
    codes_json: str,
    start: str,
    end: str,
    initial_cash: float,
    base_pct: float,
    t_pct: float,
    entry_drop: float,
    exit_gain: float,
    stop_loss: float,
    max_hold_days: int,
    close_on_friday: bool,
    commission: float,
    stamp_tax: float,
    slippage: float,
):
    """带缓存地跑一次做T回测。返回 (equity_df, trades_df, compare_dict, metrics_dict)。"""
    codes = json.loads(codes_json)
    storage = Storage()
    bars_map: dict[str, pd.DataFrame] = {}
    for c in codes:
        df = storage.load_bars(c)
        if df is None or df.empty:
            continue
        df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
        if not df.empty:
            bars_map[c] = df.reset_index(drop=True)
    cfg = TConfig(
        initial_cash=initial_cash,
        base_pct=base_pct,
        t_pct=t_pct,
        entry_drop=entry_drop,
        exit_gain=exit_gain,
        stop_loss=stop_loss,
        max_hold_days=max_hold_days,
        close_on_friday=close_on_friday,
        commission=commission,
        stamp_tax=stamp_tax,
        slippage=slippage,
    )
    res = run_t_backtest(codes, bars_map, cfg)
    eq = pd.DataFrame(
        {
            "date": res.equity.index,
            "底仓+做T": res.equity.to_numpy(),
            "仅底仓": res.base_only_equity.reindex(res.equity.index).to_numpy(),
        }
    )
    return eq, res.t_trades, res.compare, res.metrics


def _sensitivity(
    codes: list[str], start: str, end: str, cfg: TConfig, entry_drops: list[float],
    exit_gains: list[float],
) -> pd.DataFrame:
    """扫描 (触发跌幅 × 止盈) 网格，看"做T贡献"是否稳定 —— 单点 A/B 不可信。"""
    storage = Storage()
    bars_map: dict[str, pd.DataFrame] = {}
    for c in codes:
        df = storage.load_bars(c)
        if df is None or df.empty:
            continue
        df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
        if not df.empty:
            bars_map[c] = df.reset_index(drop=True)

    rows = []
    import dataclasses

    for ed in entry_drops:
        for eg in exit_gains:
            c2 = dataclasses.replace(cfg, entry_drop=ed, exit_gain=eg)
            res = run_t_backtest(codes, bars_map, c2)
            rows.append(
                {
                    "触发跌幅": f"{ed * 100:.0f}%",
                    "止盈": f"{eg * 100:.1f}%",
                    "做T贡献(pp)": round(res.compare["t_contribution"], 2),
                    "T笔数": res.compare["t_trades"],
                    "T胜率(%)": round(res.compare["t_win_rate"], 1),
                }
            )
    return pd.DataFrame(rows)


def render() -> None:
    """渲染做T回测页面。"""
    st.subheader("🔁 做T回测：底仓 + 周内高抛低吸")
    st.caption(
        "在同一批底仓上做「正T」（先买后卖）：跌深了加一笔、反弹或周五就卖掉，"
        "看这笔操作到底是加分还是减分。日线模拟，按收盘价成交。"
    )
    page_glossary(T_GLOSSARY)

    storage = Storage()
    if storage.load_stock_list().empty:
        st.warning("还没有股票列表数据，请先到「数据更新」页更新数据。")
        return

    st.markdown("**① 底仓范围**")
    boards, exclude_st = board_filter_ui("tt")

    from stock_plan.data.classification import industry_options

    sw1 = industry_options("sw_l1")
    industries: list[str] = []
    if sw1:
        industries = st.multiselect(
            "申万一级行业（可选，留空=不限）",
            sw1,
            help="按行业限定底仓范围：想让某个板块单独跑一套策略时用这里。",
        )
    else:
        st.caption("尚无行业分类快照；到「数据更新」页跑一次分类抓取后即可按行业筛选。")

    c1, c2, c3 = st.columns(3)
    with c1:
        n_codes = st.slider("参与股票数（等权底仓）", 5, 50, 10, step=5)
    with c2:
        default_end = date.today()
        start = st.date_input("开始日期", default_end - timedelta(days=250))
    with c3:
        end = st.date_input("结束日期", default_end)

    st.markdown("**② T 策略参数**")
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        base_pct = st.slider("底仓占资金比例", 0.0, 1.0, 0.6, step=0.05)
    with p2:
        t_pct = st.slider("单次T仓占底仓比例", 0.05, 1.0, 0.3, step=0.05)
    with p3:
        entry_drop = st.slider("跌多少算买点（%）", 1.0, 8.0, 3.0, step=0.5)
    with p4:
        exit_gain = st.slider("涨多少止盈（%）", 0.5, 8.0, 2.0, step=0.5)

    p5, p6, p7, p8 = st.columns(4)
    with p5:
        stop_loss = st.slider("跌多少止损（%）", 1.0, 10.0, 3.0, step=0.5)
    with p6:
        max_hold_days = st.slider("T仓最多持有几个交易日", 1, 10, 5)
    with p7:
        close_on_friday = st.checkbox("周五强制平掉T仓", value=True)
    with p8:
        initial_cash = st.number_input("初始资金", 50_000, 10_000_000, 100_000, step=10_000)

    with st.expander("💰 交易成本（默认万三 / 印花税千一 / 滑点千一）", expanded=False):
        q1, q2, q3 = st.columns(3)
        with q1:
            commission_w = st.number_input("佣金（万分之）", 0.0, 100.0, 3.0, step=0.5)
        with q2:
            stamp_w = st.number_input("印花税（万分之，仅卖出）", 0.0, 100.0, 10.0, step=1.0)
        with q3:
            slippage_w = st.number_input("滑点（万分之）", 0.0, 100.0, 10.0, step=1.0)

    codes = _pick_codes(boards, exclude_st, industries, n_codes)
    if not codes:
        st.warning("当前筛选条件下没有可用股票（可能是本地还没缓存行情，或行业筛得太窄）。")
        return
    st.caption(f"将用到 {len(codes)} 只：{'、'.join(codes[:12])}{' …' if len(codes) > 12 else ''}")

    if not st.button("开始回测", type="primary"):
        st.info("调好参数后点「开始回测」。结果里最重要的是「底仓不动」与「底仓+做T」的差值。")
        return

    eq, trades, compare, _metrics = _cached_t_backtest(
        json.dumps(codes, ensure_ascii=False),
        str(start),
        str(end),
        float(initial_cash),
        float(base_pct),
        float(t_pct),
        float(entry_drop) / 100,
        float(exit_gain) / 100,
        float(stop_loss) / 100,
        int(max_hold_days),
        bool(close_on_friday),
        float(commission_w) / 10_000,
        float(stamp_w) / 10_000,
        float(slippage_w) / 10_000,
    )

    if eq.empty:
        st.warning("所选区间内没有行情数据，换个区间试试。")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("底仓不动", f"{compare['base_only_return']:+.2f}%")
    m2.metric("底仓+做T", f"{compare['total_return']:+.2f}%", f"{compare['t_contribution']:+.2f}pp")
    m3.metric("做T笔数", f"{compare['t_trades']}", f"胜率 {compare['t_win_rate']:.0f}%")
    m4.metric("T仓累计盈亏", f"{compare['t_total_pnl']:+,.0f} 元")

    if compare["skipped_code_count"]:
        st.warning(
            f"有 {compare['skipped_code_count']} 只股票连一手都买不起（资金按只数等分后不足），"
            "实际底仓只有 "
            f"{compare['base_code_count']} 只。想让每只都建仓，就把「参与股票数」调小或加大初始资金。"
        )

    st.markdown("**资金曲线：底仓不动 vs 底仓+做T**")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(x=eq["date"], y=eq["仅底仓"], name="仅底仓", line={"color": "#9aa0a6"})
    )
    fig.add_trace(
        go.Scatter(x=eq["date"], y=eq["底仓+做T"], name="底仓+做T", line={"color": "#d93025"})
    )
    apply_time_axis(fig)
    st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)

    if trades.empty:
        st.info(
            "区间内一次买点都没触发（跌幅从未达到阈值）。把「跌多少算买点」调低一点，"
            "或换一段波动更大的区间再试。"
        )
    else:
        st.markdown("**逐笔明细**")
        show = trades.copy()
        for col in ("entry_date", "exit_date"):
            show[col] = pd.to_datetime(show[col]).dt.strftime("%Y-%m-%d")
        st.dataframe(
            show[
                ["code", "entry_date", "exit_date", "entry_price", "exit_price",
                 "shares", "pnl", "pnl_pct", "hold_days", "exit_reason"]
            ].round(3),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            f"平均单笔 {compare['t_avg_pnl_pct']:+.2f}%、平均持有 {compare['t_avg_hold_days']:.1f} 个交易日、"
            f"T 仓累计成本 {compare['t_total_cost']:.0f} 元。"
        )
        if len(trades) < 30:
            st.warning(
                f"只有 {len(trades)} 笔交易，统计意义很弱：本项目实测同一策略换一组持仓、"
                "总收益就可能差 ±20pp。别用这个数字直接判定「做T有效/无效」。"
            )

    with st.expander("🔬 参数敏感性扫描（看结论稳不稳）", expanded=False):
        st.caption(
            "固定底仓，扫描「触发跌幅 × 止盈」网格。如果「做T贡献」在不同参数下来了正负翻转，"
            "说明这个策略没有稳定的边际，别当真。"
        )
        if st.button("跑敏感性扫描（9 组，约几秒）"):
            cfg = TConfig(
                initial_cash=float(initial_cash),
                base_pct=float(base_pct),
                t_pct=float(t_pct),
                entry_drop=float(entry_drop) / 100,
                exit_gain=float(exit_gain) / 100,
                stop_loss=float(stop_loss) / 100,
                max_hold_days=int(max_hold_days),
                close_on_friday=bool(close_on_friday),
                commission=float(commission_w) / 10_000,
                stamp_tax=float(stamp_w) / 10_000,
                slippage=float(slippage_w) / 10_000,
            )
            with st.spinner("扫描中…"):
                grid = _sensitivity(
                    codes, str(start), str(end), cfg,
                    entry_drops=[0.02, 0.03, 0.05], exit_gains=[0.01, 0.02, 0.03],
                )
            st.dataframe(grid, use_container_width=True, hide_index=True)

    st.caption(
        "口径说明：底仓=回测首日等权买入后不动；只做正T（先买后卖），T+1 强制；"
        "按当日收盘价成交（买加滑点、卖减滑点）并计佣金/印花税/最低5元佣金。"
        "日线模拟无法还原盘中路径，因此本页结论只代表「收盘跌幅触发、尾盘成交」这一种做法。"
    )
