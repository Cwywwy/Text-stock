"""回测结果页面 — 选择策略与日期 → 运行回测 → 展示绩效。

展示内容：
- 策略选择 + 起止日期 + 初始资金
- 核心指标卡片（总收益/年化/最大回撤/夏普/胜率/盈亏比/交易次数）
- 资金曲线（Plotly 折线图）
- 月度收益柱状图
- 回撤区间与交易盈亏分布
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.backtest.report import make_report
from stock_plan.data.storage import Storage
from stock_plan.llm.analyzer import review_backtest
from stock_plan.strategy.registry import create_strategy
from stock_plan.ui.widgets import board_filter_ui, page_glossary

# 本页名词速览（R2 亲民化）
BACKTEST_GLOSSARY = {
    "回测": "用过去几年的历史数据「假装重新炒一遍股」，检验这套策略到底赚不赚钱。",
    "总收益率": "整个回测期间账户一共涨了百分之几。比如 9.37% 就是 10 万变 10.94 万。",
    "最大回撤": "账户从「最高点」跌到「最低点」的最大幅度，衡量最惨的时候亏多少。",
    "夏普比率": "每承担一份风险换来多少收益，越大越好。>1 不错，>2 很优秀。",
    "胜率": "所有买卖中赚钱的比例。50% 胜率配 2 倍盈亏比也能稳赚。",
    "盈亏比": "平均一笔赚的钱 ÷ 平均一笔亏的钱。大于 1 说明赚的时候比亏的时候多。",
    "滑点": "实际成交价比看到的价格差一点，就像网购的运费，回测里模拟了这个损耗。",
}


@st.cache_data(ttl=600, show_spinner="正在运行回测…")
def _cached_backtest(
    strategy_name: str, start: str, end: str, initial_cash: float,
    params_json: str, rebalance_freq: str, market_timing: bool, max_hold_days: int,
    boards_json: str = "[]", exclude_st: bool = True,
):
    """带缓存回测（10 分钟内不重复计算）。"""
    import json

    storage = Storage()
    stock_list = storage.load_stock_list()
    bars_map = {}
    for code in stock_list["code"].astype(str).tolist():
        if storage.cache_exists(code):
            bars_map[code] = storage.load_bars(code)
    fund_map = {}
    for code in bars_map:
        fin = storage.load_fundamentals(code)
        if fin:
            fund_map[code] = fin

    # 板块筛选 + 剔除 ST（R5 需求）
    from stock_plan.factors.board import filter_universe_ui

    boards = json.loads(boards_json)
    stock_list, bars_map = filter_universe_ui(stock_list, bars_map, boards, exclude_st)
    fund_map = {c: f for c, f in fund_map.items() if c in bars_map}

    params = json.loads(params_json) if params_json else None
    strategy = create_strategy(strategy_name, params)

    config = BacktestConfig(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        initial_cash=initial_cash,
        rebalance_freq=rebalance_freq,
        market_timing=market_timing,
        max_hold_days=max_hold_days,
    )
    result = run_backtest(strategy, config, bars_map, fund_map, stock_list)
    metrics = calc_metrics(result.equity_curve, result.trades)
    report = make_report(result)
    return metrics, report, result


def _render_result(metrics: dict, report: dict, result, config_dict: dict):
    """渲染回测结果（从 session_state 读取，避免按钮交互导致结果丢失）。"""
    # 核心指标卡片
    st.subheader("核心指标")
    cols = st.columns(4)
    cards = [
        ("总收益率", f"{metrics.get('total_return', 0):.2f}%"),
        ("年化收益率", f"{metrics.get('annual_return', 0):.2f}%"),
        ("最大回撤", f"{metrics.get('max_drawdown', 0):.2f}%"),
        ("夏普比率", f"{metrics.get('sharpe', 0):.2f}"),
    ]
    for col, (label, value) in zip(cols, cards):
        col.metric(label, value)
    cols = st.columns(4)
    cards2 = [
        ("胜率", f"{metrics.get('win_rate', 0):.2f}%"),
        ("盈亏比", f"{metrics.get('profit_loss', 0) or 0:.2f}"),
        ("交易次数", f"{metrics.get('trade_count', 0)}"),
        ("平均持仓", f"{metrics.get('avg_hold_days', 0):.1f} 天"),
    ]
    for col, (label, value) in zip(cols, cards2):
        col.metric(label, value)

    # 资金曲线
    st.subheader("资金曲线")
    if report["equity_curve"]:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=[p["date"] for p in report["equity_curve"]],
                y=[p["value"] for p in report["equity_curve"]],
                mode="lines",
                name="总资产",
                line=dict(color="#1f77b4", width=2),
            )
        )
        fig.update_layout(
            height=400,
            margin=dict(l=40, r=20, t=30, b=30),
            yaxis_title="资产（元）",
            xaxis_title="日期",
        )
        st.plotly_chart(fig, use_container_width=True)

    # 收益曲线（R3 需求：累计收益率 % + 回撤区域标注）
    st.subheader("📈 收益曲线（动态变化）")
    st.caption(
        "蓝线是「累计收益率」：从回测第一天算起，账户总共赚/亏了百分之几。"
        "红色阴影是「回撤」：从阶段最高点跌下来的幅度，阴影越深说明中途跌得越狠。"
    )
    from stock_plan.ui.widgets import equity_curve_fig

    eq = result.equity_curve if result is not None else None
    fig_ret = equity_curve_fig(eq)
    if fig_ret is not None:
        st.plotly_chart(fig_ret, use_container_width=True)
    else:
        st.info("回测数据不足，无法绘制收益曲线。")

    # 月度收益
    st.subheader("月度收益")
    if report["monthly_returns"]:
        fig2 = go.Figure()
        fig2.add_trace(
            go.Bar(
                x=[m["month"] for m in report["monthly_returns"]],
                y=[m["return"] for m in report["monthly_returns"]],
                name="月度收益",
                marker_color=[
                    "#d62728" if m["return"] < 0 else "#2ca02c"
                    for m in report["monthly_returns"]
                ],
            )
        )
        fig2.update_layout(
            height=300,
            margin=dict(l=40, r=20, t=30, b=30),
            yaxis_title="收益率（%）",
        )
        st.plotly_chart(fig2, use_container_width=True)

    # 回撤区间
    st.subheader("回撤区间")
    if report["drawdown_periods"]:
        st.dataframe(
            [
                {
                    "开始": d["start"],
                    "结束": d["end"],
                    "最大回撤": f"{d['max_drawdown']:.2f}%",
                }
                for d in report["drawdown_periods"]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("回测期间无回撤。")

    # 交易盈亏分布
    st.subheader("交易盈亏分布")
    if report["trade_distribution"]:
        fig3 = go.Figure()
        fig3.add_trace(
            go.Bar(
                x=[t["range"] for t in report["trade_distribution"]],
                y=[t["count"] for t in report["trade_distribution"]],
                name="交易分布",
                marker_color="#ff7f0e",
            )
        )
        fig3.update_layout(
            height=300,
            margin=dict(l=40, r=20, t=30, b=30),
            yaxis_title="笔数",
        )
        st.plotly_chart(fig3, use_container_width=True)

    # 退出原因归因
    st.subheader("退出原因归因")
    if report["exit_attribution"]:
        st.dataframe(
            [
                {
                    "退出原因": a["reason"],
                    "笔数": a["count"],
                    "平均收益%": a["avg_pnl_pct"],
                    "胜率%": a["win_rate"],
                }
                for a in report["exit_attribution"]
            ],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("无交易数据。")

    # 月度收益热力图
    st.subheader("月度收益热力图")
    if report["monthly_heatmap"]:
        hm = report["monthly_heatmap"]
        years = sorted({h["year"] for h in hm})
        months = list(range(1, 13))
        z = []
        for y in years:
            row = []
            for mo in months:
                val = next((h["return"] for h in hm if h["year"] == y and h["month"] == mo), None)
                row.append(val if val is not None else 0)
            z.append(row)
        fig4 = go.Figure(data=go.Heatmap(
            z=z,
            x=[f"{m}月" for m in months],
            y=[str(y) for y in years],
            colorscale="RdYlGn",
            zmid=0,
            text=[[f"{v:.1f}" for v in row] for row in z],
            texttemplate="%{text}",
            colorbar=dict(title="收益%"),
        ))
        fig4.update_layout(height=300, margin=dict(l=40, r=20, t=30, b=30))
        st.plotly_chart(fig4, use_container_width=True)

    # 最近交易明细
    st.subheader("最近交易")
    if result.trades is not None and not result.trades.empty:
        st.dataframe(
            result.trades.tail(20),
            use_container_width=True,
            hide_index=True,
        )

    # LLM 复盘与参数优化建议
    st.divider()
    st.subheader("🤖 LLM 复盘与参数优化建议")
    if st.button("💡 生成复盘建议", key="llm_review"):
        with st.spinner("正在生成复盘建议…"):
            review = review_backtest(metrics, report, config_dict)
        st.markdown(review)


def render():
    st.header("📊 回测结果")
    st.caption("用历史数据验证策略表现：逐日模拟 T+1 交易，含手续费/印花税/滑点/涨跌停。")
    page_glossary(BACKTEST_GLOSSARY)

    # 选股范围（R5：板块筛选 + 剔除 ST）
    st.markdown("**选股范围**")
    boards, exclude_st = board_filter_ui("bt")

    # 参数设置
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        from stock_plan.strategy.registry import STRATEGIES

        strategy_name = st.selectbox("策略", list(STRATEGIES.keys()))
    with col2:
        default_end = date.today()
        default_start = default_end - timedelta(days=365)
        start = st.date_input("开始日期", default_start)
    with col3:
        end = st.date_input("结束日期", default_end)
    with col4:
        initial_cash = st.number_input("初始资金", 10_000, 10_000_000, 100_000, step=10_000)

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        rebalance_freq = st.selectbox("选股频率", ["weekly", "daily"], index=0)
    with col6:
        market_timing = st.checkbox("大盘择时（低于 MA20 减半仓）", value=True)
    with col7:
        max_hold_days = st.number_input("最大持仓天数", 5, 120, 30, step=5)
    with col8:
        st.caption("")

    if st.button("▶️ 运行回测", type="primary"):
        if start >= end:
            st.error("开始日期必须早于结束日期")
        else:
            import json

            by_name = st.session_state.get("strategy_params_by_strategy", {})
            saved = by_name.get(strategy_name)
            if saved is None and strategy_name == "趋势跟随策略":
                saved = st.session_state.get("strategy_params", {})
            metrics, report, result = _cached_backtest(
                strategy_name,
                start.isoformat(),
                end.isoformat(),
                float(initial_cash),
                json.dumps(saved, ensure_ascii=False),
                rebalance_freq,
                bool(market_timing),
                int(max_hold_days),
                json.dumps(boards, ensure_ascii=False),
                exclude_st,
            )
            if not metrics:
                st.warning("回测无数据。请先运行数据拉取脚本（fetch_all.py）确保有缓存数据。")
            else:
                # 存入 session_state，保证后续按钮交互（如 LLM 复盘）不丢失结果
                st.session_state["backtest_result"] = (metrics, report, result)
                st.session_state["backtest_config"] = {
                    "m": saved.get("m", 3.5),
                    "n": saved.get("n", 3.5),
                    "hold": int(max_hold_days),
                    "rebalance_freq": rebalance_freq,
                    "market_timing": bool(market_timing),
                }

    # 从 session_state 渲染结果（若已运行过回测）
    if "backtest_result" in st.session_state:
        metrics, report, result = st.session_state["backtest_result"]
        config_dict = st.session_state.get("backtest_config", {})
        _render_result(metrics, report, result, config_dict)
    else:
        st.info("设置参数后点击上方按钮运行回测。")