"""策略对比页面 — 多策略横向对比 + Walk-Forward 滚动验证。

展示内容：
- 多策略对比：趋势策略 vs 动量策略，同一区间跑回测，横向对比指标
- Walk-Forward：滚动窗口样本外验证，展示每个窗口的最优参数与样本外表现

Revision History:
    2026-09-04  switch to windowed load_market_maps to avoid cloud OOM
    2026-09-04  show cloud sample-universe disclosure
    2026-10-28  (phase 2) charts go through chart_style factories with benchmark line
    2026-10-28  (branch4 phase 1) excess column, best-per-column tint and one-line verdict
    2026-10-29  (phase 16) share prepared cache, show progress and cache-hit caption, add clear-cache button
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_plan.backtest.engine import BacktestConfig
from stock_plan.data.storage import Storage
from stock_plan.strategy import store
from stock_plan.strategy.registry import STRATEGIES
from stock_plan.ui import backtest_cache as btc
from stock_plan.ui import chart_style as cs
from stock_plan.ui.widgets import (
    BENCHMARK_LABEL,
    benchmark_equity_fig,
    board_filter_ui,
    multi_line_fig,
    page_glossary,
    render_chart,
)

# 本页名词速览（R2 亲民化）
COMPARE_GLOSSARY = {
    "策略对比": "把几套买卖规则放进同一段历史里各跑一遍，看谁赚得多、谁跌得少。",
    "Walk-Forward": "「滚动验证」：用前一段历史找最优参数，再拿到紧随其后的一段没见过的历史里考试，防止「背答案」（过拟合）。",
    "样本外": "考试用的「没见过的数据」。样本外表现好，才说明策略是真有本事而不是运气。",
    "资金曲线": "账户总资产随时间变化的折线，一路向上是好策略的基本长相。",
}

def _equity_fig(results: dict, *, initial_cash: float = 100_000.0) -> go.Figure | None:
    """多策略资金曲线 + 等权基准（基准是归一化指数，乘初始资金换到同一量纲）。"""
    series: dict[str, pd.Series] = {}
    benchmark: pd.Series | None = None
    for name, data in results.items():
        equity = data.get("equity")
        if equity is None or equity.empty:
            continue
        series[name] = equity
        if benchmark is None:
            candidate = data.get("benchmark")
            if candidate is not None and len(candidate) > 0:
                benchmark = candidate
    return benchmark_equity_fig(series, benchmark=benchmark, initial_cash=initial_cash)


def _oos_fig(oos_equity: pd.Series) -> go.Figure | None:
    """Walk-Forward 样本外资金曲线（单序列，副图高度）。"""
    return multi_line_fig({"样本外资产": oos_equity}, y_title="总资产（元）", money=True)


_COMPARE_LEAD_DAYS = 180


def _universe_limit() -> int | None:
    """云端限制股票数量（控内存），本地返回 None 表示全市场。"""
    return btc.universe_limit()


def _strategy_sig(label: str) -> str:
    """已保存策略的配置指纹：配置在缓存函数体内按名从 DB 读，必须显式进缓存键。

    内置策略返回空串；「（自存）」后缀的显示名要按已保存策略处理（同一内置名
    可能同时存在内置版与自存版，两者的配置指纹必须不同）。
    """
    raw = store.strip_display_name(label)
    if raw in STRATEGIES and label == raw:
        return ""
    record = store.load_strategy(raw) or {}
    return json.dumps(record.get("config") or {}, ensure_ascii=False, sort_keys=True)


def _comparison_specs() -> dict[str, tuple[str, str]]:
    """对比对象：显示名 → (策略来源, 缓存键里的策略名)。保留旧实现的内置在前顺序。"""
    specs: dict[str, tuple[str, str]] = {name: ("registry", name) for name in STRATEGIES}
    for name in store.list_strategies():
        # 已保存策略（LLM 生成/拼装页保存）一并纳入对比。重名时用「（自存）」
        # 后缀区分，否则用户自存的同名策略会被静默丢掉、只剩内置版本。
        label = store.display_name(name)
        specs[label] = ("registry", label)
    return specs


def _run_comparison(
    start: str, end: str, initial_cash: float, rebalance_freq: str,
    market_timing: bool, max_hold_days: int, boards_json: str, exclude_st: bool,
) -> tuple[dict, str]:
    """跑全部策略对比：共享 prepared + 逐策略结果缓存 + 进度（Phase 16）。

    进度控件必须放在缓存函数外面：`st.status` / `st.progress` 是页面副作用，
    写进 `@st.cache_data` 函数里只会被缓存吞掉、第二次命中时不再显示。所以这里
    只做循环编排，每个策略的「构造策略 + 回测 + 指标」由 `btc.run_strategy_cached`
    按参数缓存；同一参数第二次点击时每个策略都命中，循环本身只剩毫秒级开销。
    """
    specs = _comparison_specs()
    total = len(specs)
    max_codes = _universe_limit()
    data_version = btc.market_data_version()
    state = {
        "start": start,
        "end": end,
        "initial_cash": initial_cash,
        "rebalance_freq": rebalance_freq,
        "market_timing": market_timing,
        "max_hold_days": max_hold_days,
        "boards_json": boards_json,
        "exclude_st": exclude_st,
        "max_codes": max_codes,
    }
    saw_before = btc.mark_cache_hit(("compare",) + btc.build_request_key(state))
    fundamental_as_of = Storage().fundamentals_snapshot_date()
    started = time.perf_counter()
    results: dict[str, dict] = {}
    status = st.status(f"正在跑 1/{total} …", expanded=True)
    progress = st.progress(0.0, text=f"正在跑 1/{total} …")
    try:
        for index, (label, (kind, name)) in enumerate(specs.items(), start=1):
            status.update(label=f"正在跑 {index}/{total}：{label}")
            progress.progress((index - 1) / total, text=f"正在跑 {index}/{total}：{label}")
            # 策略自带的 hold_days 优先于页面滑杆（LLM/拼装页保存的持仓天数是
            # 策略定义的一部分，一律用滑杆默认值会让对比结果与策略自己的回测对不上）
            hold_days = btc.strategy_hold_days(name, kind) or max_hold_days
            config = BacktestConfig(
                start=date.fromisoformat(start),
                end=date.fromisoformat(end),
                initial_cash=initial_cash,
                rebalance_freq=rebalance_freq,
                market_timing=market_timing,
                max_hold_days=hold_days,
                # 与回测页口径一致：财务快照前视修正
                fundamental_as_of=fundamental_as_of,
            )
            result, metrics = btc.run_strategy_cached(
                start, end, _COMPARE_LEAD_DAYS, boards_json, exclude_st,
                False, max_codes, data_version,
                name, kind, "", _strategy_sig(label),
                json.dumps(btc.config_to_dict(config), ensure_ascii=False, sort_keys=True),
            )
            if result is None:
                continue
            results[label] = {
                "metrics": metrics or {},
                "equity": result.equity_curve,
                "benchmark": result.benchmark,
            }
            progress.progress(index / total, text=f"已完成 {index}/{total}")
    finally:
        elapsed = time.perf_counter() - started
        # 跑完折叠成一行耗时；耗时与命中状态只在收尾时给一次，不刷屏
        status.update(
            label=f"对比完成：{len(results)} 个策略，耗时 {elapsed:.2f}s",
            state="complete",
            expanded=False,
        )
        progress.empty()
    return results, btc.cache_caption(elapsed, saw_before=saw_before)


def _run_walkforward(
    strategy_name: str, start: str, end: str, train_days: int, test_days: int,
    boards_json: str, exclude_st: bool,
):
    """Walk-Forward 验证：复用 shared prepared，结果按参数缓存（Phase 16）。"""
    max_codes = _universe_limit()
    state = {
        "start": start,
        "end": end,
        "boards_json": boards_json,
        "exclude_st": exclude_st,
        "max_codes": max_codes,
    }
    saw_before = btc.mark_cache_hit(("walkforward", strategy_name) + btc.build_request_key(state))
    started = time.perf_counter()
    with st.status(f"正在运行 Walk-Forward（{strategy_name}）…", expanded=False) as status:
        result = btc.run_walkforward_cached(
            start, end, _COMPARE_LEAD_DAYS, boards_json, exclude_st,
            False, max_codes, btc.market_data_version(),
            strategy_name, "registry", "", _strategy_sig(strategy_name),
            int(train_days), int(test_days),
        )
        status.update(label="Walk-Forward 运行完成", state="complete")
    elapsed = time.perf_counter() - started
    return result, btc.cache_caption(elapsed, saw_before=saw_before)


_BEST_COLUMNS = ("总收益%", "年化%", "夏普", "胜率%", "盈亏比", "超额%")


def _benchmark_return(results: dict) -> float | None:
    """从任一策略结果里取等权基准序列，换算成同期总收益（%）。"""
    for data in results.values():
        bench = data.get("benchmark")
        if bench is None or len(bench) < 2:
            continue
        series = pd.Series(bench, dtype="float64").dropna()
        if len(series) < 2 or float(series.iloc[0]) <= 0:
            continue
        return (float(series.iloc[-1]) / float(series.iloc[0]) - 1.0) * 100.0
    return None


def _excess_rows(rows: list[dict], bench_ret: float | None) -> list[dict]:
    """加「超额%」列 = 策略总收益 − 同期等权基准总收益；没有基准就留 None。"""
    for row in rows:
        row["超额%"] = None if bench_ret is None else round(float(row["总收益%"]) - bench_ret, 2)
    return rows


def _summary_line(rows: list[dict], bench_ret: float | None) -> str:
    """一句话结论：谁跑赢基准、赢多少；全跑输时也说清楚，不能糊弄成「表现良好」。"""
    if not rows:
        return ""
    scored = [r for r in rows if r.get("超额%") is not None]
    if bench_ret is None or not scored:
        return "本次没有可用的等权基准数据，无法计算超额收益。"
    winner = max(scored, key=lambda r: r["超额%"])
    if winner["超额%"] >= 0:
        return (
            f"同期等权基准 {bench_ret:+.2f}%；「{winner['策略']}」跑赢基准 "
            f"{winner['超额%']:.2f} 个百分点。"
        )
    return (
        f"同期等权基准 {bench_ret:+.2f}%；本次全部策略跑输基准，"
        f"最接近的是「{winner['策略']}」（落后 {abs(winner['超额%']):.2f} 个百分点）。"
    )


def _tint_best(column: pd.Series, colour: str) -> list[str]:
    """按列上底色：只给该列最优值中的最大值上色（NaN 不参与）。

    不用 ``Styler.highlight_max``：pandas 3.0 把它改成按子集整体/行方向选最大值，
    表现与 2.x 的「每列一个」不同，容易被静默改坏；这里的逐列写法与版本无关。
    """
    if column.dropna().empty:
        return ["" for _ in column]
    top = column.max()
    return [f"background-color: {colour}" if value == top else "" for value in column]


def _tint_lowest(column: pd.Series, colour: str) -> list[str]:
    """回撤列专用：最小（跌得最少）的那个上色。"""
    if column.dropna().empty:
        return ["" for _ in column]
    low = column.min()
    return [f"background-color: {colour}" if value == low else "" for value in column]


def _styled_table(frame: pd.DataFrame, *, has_benchmark: bool) -> object:
    """只给每列最优值上底色；不改字体色，深色主题下保持可读。"""
    keep = [c for c in _BEST_COLUMNS if c in frame.columns]
    if not has_benchmark and "超额%" in keep:
        keep.remove("超额%")
    styler = frame.style.format(precision=2, na_rep="—")
    if keep:
        styler = styler.apply(_tint_best, subset=keep, axis=0, colour=cs.BEST_UP)
    if "最大回撤%" in frame.columns:
        styler = styler.apply(_tint_lowest, subset=["最大回撤%"], axis=0, colour=cs.BEST_DOWN)
    return styler


def render():
    st.header("⚖️ 策略对比")
    from stock_plan.data.snapshot import is_cloud

    if is_cloud():
        st.info(
            "云端轻量模式说明：当前策略对比基于约 1800 只股票组成的样本市场，而非全部 A 股。"
            "收益、胜率与选股结果可能与全市场回测存在差异；本地运行不受此限制。"
        )
    st.caption("多策略横向对比 + Walk-Forward 滚动样本外验证，避免过拟合历史。")
    page_glossary(COMPARE_GLOSSARY)
    btc.render_clear_cache_button("cmp")

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
            max_hold_days = st.number_input(
                "最大持仓天数", 5, 120, 30, step=5, key="cmp_hold",
                help="对**没有**自带 hold_days 的策略生效；保存策略时设过持仓天数的，用策略自己的值。",
            )

        if st.button("▶️ 运行对比", type="primary", key="cmp_run"):
            if start >= end:
                st.error("开始日期必须早于结束日期")
                return
            results, cache_note = _run_comparison(
                start.isoformat(), end.isoformat(), float(initial_cash),
                rebalance_freq, bool(market_timing), int(max_hold_days),
                json.dumps(boards, ensure_ascii=False), exclude_st,
            )
            if not results:
                st.warning("回测无数据。请先到左侧「🔄 数据更新」页拉取数据。")
                return
            st.caption(cache_note)

            # 指标对比表 + 一句话结论
            bench_ret = _benchmark_return(results)
            rows = []
            for name, data in results.items():
                m = data["metrics"]
                rows.append({
                    "策略": name,
                    "总收益%": round(float(m.get("total_return", 0) or 0), 2),
                    "年化%": round(float(m.get("annual_return", 0) or 0), 2),
                    "超额%": None,
                    "最大回撤%": round(float(m.get("max_drawdown", 0) or 0), 2),
                    "夏普": round(float(m.get("sharpe", 0) or 0), 2),
                    "胜率%": round(float(m.get("win_rate", 0) or 0), 2),
                    "盈亏比": round(float(m.get("profit_loss", 0) or 0), 2),
                    "交易次数": int(m.get("trade_count", 0) or 0),
                })
            _excess_rows(rows, bench_ret)
            st.subheader("指标对比")
            st.caption(_summary_line(rows, bench_ret))
            st.dataframe(
                _styled_table(pd.DataFrame(rows), has_benchmark=bench_ret is not None),
                width="stretch",
                hide_index=True,
            )

            # 资金曲线对比
            st.subheader("资金曲线对比")
            render_chart(
                _equity_fig(results, initial_cash=float(initial_cash)),
                name="compare-equity",
                caption="每条线是一套策略在同一段历史里的账户资产；灰虚线是「等权持有全部候选股」"
                        "的基准，用来判断策略本身有没有超额，而不是靠大盘上涨。",
            )

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
            wf, cache_note = _run_walkforward(
                strategy_name, wf_start.isoformat(), date.today().isoformat(),
                int(train_days), int(test_days),
                json.dumps(boards, ensure_ascii=False), exclude_st,
            )
            if wf is None:
                st.warning("回测无数据。请先到左侧「🔄 数据更新」页拉取数据。")
                return
            if not wf.windows:
                st.warning("区间太短，无法形成完整窗口。请把开始日期提前（建议 2 年以上）。")
                return
            st.caption(cache_note)

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
            st.dataframe(rows, width="stretch", hide_index=True)

            st.subheader("样本外汇总")
            m = wf.oos_metrics
            cols = st.columns(4)
            cols[0].metric("总收益", f"{m.get('total_return', 0):.2f}%")
            cols[1].metric("最大回撤", f"{m.get('max_drawdown', 0):.2f}%")
            cols[2].metric("胜率", f"{m.get('win_rate', 0):.2f}%")
            cols[3].metric("交易次数", f"{m.get('trade_count', 0)}")

            if not wf.oos_equity.empty:
                st.subheader("样本外资金曲线")
                render_chart(
                    _oos_fig(wf.oos_equity),
                    name="compare-walkforward",
                    caption="样本外资产曲线：只包含「训练段之后就再没见过」的那段历史，"
                            "它比训练段好看才有意义。",
                )