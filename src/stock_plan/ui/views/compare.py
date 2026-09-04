"""策略对比页面 — 多策略横向对比 + Walk-Forward 滚动验证。

展示内容：
- 多策略对比：趋势策略 vs 动量策略，同一区间跑回测，横向对比指标
- Walk-Forward：滚动窗口样本外验证，展示每个窗口的最优参数与样本外表现

Revision History:
    2026-09-04  switch to windowed load_market_maps to avoid cloud OOM
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.backtest.walkforward import walk_forward
from stock_plan.data.storage import Storage
from stock_plan.strategy import store
from stock_plan.strategy.registry import STRATEGIES
from stock_plan.ui.widgets import board_filter_ui, page_glossary

# 本页名词速览（R2 亲民化）
COMPARE_GLOSSARY = {
    "策略对比": "把几套买卖规则放进同一段历史里各跑一遍，看谁赚得多、谁跌得少。",
    "Walk-Forward": "「滚动验证」：用前一段历史找最优参数，再拿到紧随其后的一段没见过的历史里考试，防止「背答案」（过拟合）。",
    "样本外": "考试用的「没见过的数据」。样本外表现好，才说明策略是真有本事而不是运气。",
    "资金曲线": "账户总资产随时间变化的折线，一路向上是好策略的基本长相。",
}


def _load_and_filter(start: str, end: str, boards: list[str] | None, exclude_st: bool):
    """加载数据并应用板块筛选 + 剔除 ST（窗口加载，控制内存占用）。"""
    from stock_plan.data.snapshot import CLOUD_MAX_CODES, is_cloud

    return Storage().load_market_maps(
        start=start, end=end, lead_days=180,
        boards=boards, exclude_st=exclude_st,
        max_codes=CLOUD_MAX_CODES if is_cloud() else None,
    )


@st.cache_data(ttl=600, show_spinner="正在运行策略对比…")
def _cached_compare(
    start: str, end: str, initial_cash: float,
    rebalance_freq: str, market_timing: bool, max_hold_days: int,
    boards_json: str = "[]", exclude_st: bool = True,
):
    """多策略对比（带缓存）。"""
    import json

    boards = json.loads(boards_json)
    stock_list, bars_map, fund_map = _load_and_filter(start, end, boards, exclude_st)

    factories = {name: (lambda cls=cls: cls()) for name, cls in STRATEGIES.items()}
    for n in store.list_strategies():
        if n not in STRATEGIES:  # 已保存策略（LLM 生成/拼装页保存）一并纳入对比
            factories[n] = lambda n=n: store.resolve_strategy(n)

    results = {}
    for name, factory in factories.items():
        strat = factory()
        config = BacktestConfig(
            start=date.fromisoformat(start),
            end=date.fromisoformat(end),
            initial_cash=initial_cash,
            rebalance_freq=rebalance_freq,
            market_timing=market_timing,
            max_hold_days=max_hold_days,
        )
        result = run_backtest(strat, config, bars_map, fund_map, stock_list)
        metrics = calc_metrics(result.equity_curve, result.trades)
        results[name] = {"metrics": metrics, "equity": result.equity_curve}
    return results


@st.cache_data(ttl=600, show_spinner="正在运行 Walk-Forward…")
def _cached_walkforward(
    strategy_name: str, start: str, end: str,
    train_days: int, test_days: int,
    boards_json: str = "[]", exclude_st: bool = True,
):
    """Walk-Forward 滚动验证（带缓存）。"""
    import json

    boards = json.loads(boards_json)
    stock_list, bars_map, fund_map = _load_and_filter(start, end, boards, exclude_st)

    if strategy_name in STRATEGIES:
        factory = lambda: STRATEGIES[strategy_name]()
    else:  # 已保存策略
        factory = lambda: store.resolve_strategy(strategy_name)
    result = walk_forward(
        factory,
        bars_map, fund_map, stock_list,
        date.fromisoformat(start), date.fromisoformat(end),
        train_days=train_days, test_days=test_days,
    )
    return result


def render():
    st.header("⚖️ 策略对比")
    st.caption("多策略横向对比 + Walk-Forward 滚动样本外验证，避免过拟合历史。")
    page_glossary(COMPARE_GLOSSARY)

    # 选股范围（R5：板块筛选 + 剔除 ST）
    st.markdown("**选股范围**")
    boards, exclude_st = board_filter_ui("cmp")

    tab1, tab2 = st.tabs(["多策略对比", "Walk-Forward 验证"])

    # ============ Tab 1: 多策略对比 ============
    with tab1:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            default_end = date.today()
            default_start = default_end - timedelta(days=365)
            start = st.date_input("开始日期", default_start, key="cmp_start")
        with col2:
            end = st.date_input("结束日期", default_end, key="cmp_end")
        with col3:
            initial_cash = st.number_input("初始资金", 10_000, 10_000_000, 100_000, step=10_000, key="cmp_cash")
        with col4:
            rebalance_freq = st.selectbox("选股频率", ["weekly", "daily"], index=0, key="cmp_freq")

        col5, col6 = st.columns(2)
        with col5:
            market_timing = st.checkbox("大盘择时", value=True, key="cmp_timing")
        with col6:
            max_hold_days = st.number_input("最大持仓天数", 5, 120, 30, step=5, key="cmp_hold")

        if st.button("▶️ 运行对比", type="primary", key="cmp_run"):
            if start >= end:
                st.error("开始日期必须早于结束日期")
                return
            results = _cached_compare(
                start.isoformat(), end.isoformat(), float(initial_cash),
                rebalance_freq, bool(market_timing), int(max_hold_days),
                json.dumps(boards, ensure_ascii=False), exclude_st,
            )

            # 指标对比表
            st.subheader("指标对比")
            rows = []
            for name, data in results.items():
                m = data["metrics"]
                rows.append({
                    "策略": name,
                    "总收益%": m.get("total_return", 0),
                    "年化%": m.get("annual_return", 0),
                    "最大回撤%": m.get("max_drawdown", 0),
                    "夏普": m.get("sharpe", 0),
                    "胜率%": m.get("win_rate", 0),
                    "盈亏比": m.get("profit_loss", 0) or 0,
                    "交易次数": m.get("trade_count", 0),
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

            # 资金曲线对比
            st.subheader("资金曲线对比")
            fig = go.Figure()
            colors = {"趋势跟随策略": "#1f77b4", "动量策略": "#d62728"}
            for name, data in results.items():
                eq = data["equity"]
                if eq.empty:
                    continue
                fig.add_trace(go.Scatter(
                    x=[str(d)[:10] for d in eq.index],
                    y=eq.values,
                    mode="lines",
                    name=name,
                    line=dict(color=colors.get(name, "#333"), width=2),
                ))
            fig.update_layout(height=400, margin=dict(l=40, r=20, t=30, b=30),
                              yaxis_title="资产（元）", xaxis_title="日期")
            st.plotly_chart(fig, use_container_width=True)

    # ============ Tab 2: Walk-Forward ============
    with tab2:
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            strategy_name = st.selectbox("策略", store.strategy_options(), key="wf_strat")
        with col2:
            wf_start = st.date_input("开始日期", date.today() - timedelta(days=730), key="wf_start")
        with col3:
            train_days = st.number_input("训练段天数", 60, 365, 180, step=30, key="wf_train")
        with col4:
            test_days = st.number_input("测试段天数", 30, 180, 90, step=30, key="wf_test")

        if st.button("▶️ 运行 Walk-Forward", type="primary", key="wf_run"):
            if wf_start >= date.today():
                st.error("开始日期必须早于今天")
                return
            wf = _cached_walkforward(
                strategy_name, wf_start.isoformat(), date.today().isoformat(),
                int(train_days), int(test_days),
                json.dumps(boards, ensure_ascii=False), exclude_st,
            )
            if not wf.windows:
                st.warning("区间太短，无法形成完整窗口。请把开始日期提前（建议 2 年以上）。")
                return

            st.subheader("窗口明细")
            rows = []
            for w in wf.windows:
                rows.append({
                    "训练段": f"{w['train_start']} ~ {w['train_end']}",
                    "测试段": f"{w['test_start']} ~ {w['test_end']}",
                    "最优参数": str(w["best_params"]),
                    "训练收益%": round(w["train_return"], 2),
                    "样本外收益%": round(w["oos_return"], 2),
                    "样本外回撤%": round(w["oos_drawdown"], 2),
                    "样本外胜率%": round(w["oos_win_rate"], 2),
                    "样本外笔数": w["oos_trades"],
                })
            st.dataframe(rows, use_container_width=True, hide_index=True)

            st.subheader("样本外汇总")
            m = wf.oos_metrics
            cols = st.columns(4)
            cols[0].metric("总收益", f"{m.get('total_return', 0):.2f}%")
            cols[1].metric("最大回撤", f"{m.get('max_drawdown', 0):.2f}%")
            cols[2].metric("胜率", f"{m.get('win_rate', 0):.2f}%")
            cols[3].metric("交易次数", f"{m.get('trade_count', 0)}")

            if not wf.oos_equity.empty:
                st.subheader("样本外资金曲线")
                fig2 = go.Figure()
                fig2.add_trace(go.Scatter(
                    x=[str(d)[:10] for d in wf.oos_equity.index],
                    y=wf.oos_equity.values,
                    mode="lines",
                    name="样本外资产",
                    line=dict(color="#2ca02c", width=2),
                ))
                fig2.update_layout(height=350, margin=dict(l=40, r=20, t=30, b=30),
                                   yaxis_title="资产（元）", xaxis_title="日期")
                st.plotly_chart(fig2, use_container_width=True)