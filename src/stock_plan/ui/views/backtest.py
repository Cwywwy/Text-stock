"""回测结果页面 — 选择策略与日期 → 运行回测 → 展示绩效。

展示内容：
- 策略选择 + 起止日期 + 初始资金
- 核心指标卡片（总收益/年化/最大回撤/夏普/胜率/盈亏比/交易次数）
- 首屏主图：策略净值 vs 等权基准 + 回撤阴影（金额 / 收益率可切换）
- 四个标签页：总览（回撤区间）/ 月度（柱状图 + 热力图）/ 交易（盈亏分布 + 明细）/ 归因（退出原因）

Revision History:
    2026-09-04  switch to windowed load_market_maps to avoid cloud OOM
    2026-09-04  show cloud sample-universe disclosure
    2026-10-28  (phase 1) tabbed result page with benchmark hero chart
    2026-10-28  (branch4 phase 0) reuse shared hero factory with optional trade markers
    2026-10-29  (phase 16) reuse shared prepared cache, show cache-hit caption, add clear-cache button
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from stock_plan.backtest.engine import BacktestConfig
from stock_plan.backtest.report import make_report
from stock_plan.data.storage import Storage
from stock_plan.llm.analyzer import review_backtest
from stock_plan.ui import backtest_cache as btc
from stock_plan.ui import chart_style as cs
from stock_plan.ui.widgets import (
    apply_time_axis,
    backtest_hero_fig,
    board_filter_ui,
    page_glossary,
    render_chart,
)

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

# 指标预热窗口：与对比页/拼装页共用同一口径，prepared 才能命中同一份缓存
_BACKTEST_LEAD_DAYS = 180


def _run_backtest(
    strategy_name: str, start: str, end: str, initial_cash: float,
    params_json: str, rebalance_freq: str, market_timing: bool, max_hold_days: int,
    boards: list[str], exclude_st: bool,
    commission_w: float, stamp_tax_w: float, slippage_w: float,
    min_commission: float, include_delisted: bool, config_sig: str,
    timing_tier: str = "off",
) -> tuple[dict, dict, object | None, str]:
    """单策略回测：复用全站 shared prepared 与结果缓存（Phase 16）。

    旧实现把「load_market_maps + 回测 + 报告」整体塞进一个 `@st.cache_data`：
    对比页/拼装页各存一份，同一区间会被重复加载、重复预计算。现在行情与因子表
    由 `btc.load_shared_prepared` 进程级共享，这里只缓存「单策略结果」。

    交易成本三个参数单位统一为**万分之**（万三 = 3），与券商报价习惯一致；
    它们全部进缓存键（通过 config_json），改动后自动重算。

    config_sig 是所选策略的**配置指纹**（JSON），不参与计算但必须进缓存键：
    策略配置是在缓存函数体内按名字从数据库读的，不出现在显式参数里。

    返回 (metrics, report, result, 缓存状态文案)；无行情数据时 result 为 None。
    """
    boards_json = json.dumps(boards, ensure_ascii=False)
    max_codes = btc.universe_limit()
    data_version = btc.market_data_version()
    state = {
        "start": start,
        "end": end,
        "initial_cash": initial_cash,
        "rebalance_freq": rebalance_freq,
        "market_timing": market_timing,
        "timing_tier": timing_tier,
        "max_hold_days": max_hold_days,
        "commission_w": commission_w,
        "stamp_tax_w": stamp_tax_w,
        "slippage_w": slippage_w,
        "min_commission": min_commission,
        "include_delisted": include_delisted,
        "boards_json": boards_json,
        "exclude_st": exclude_st,
        "max_codes": max_codes,
    }
    # 策略身份与配置也参与会话命中标记，避免"换了策略却提示缓存命中"
    saw_before = btc.mark_cache_hit(
        ("backtest", strategy_name, params_json, config_sig) + btc.build_request_key(state)
    )
    config = BacktestConfig(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        initial_cash=initial_cash,
        rebalance_freq=rebalance_freq,
        market_timing=market_timing,
        timing_tier=timing_tier,
        max_hold_days=max_hold_days,
        # UI 用"万分之"输入，这里换算成小数比例
        commission=commission_w / 10_000,
        stamp_tax=stamp_tax_w / 10_000,
        slippage=slippage_w / 10_000,
        # 佣金单笔下限：券商 5 元起收，小额单不设下限会把成本算少一个数量级
        min_commission=min_commission,
        # 财务数据只有一份当前快照（没有报告期），快照日之前必须中性化，
        # 否则等于用今天抓到的财报回测去年行情（前视）。取不到日期就不修正。
        fundamental_as_of=Storage().fundamentals_snapshot_date(),
    )
    started = time.perf_counter()
    with st.status("正在运行回测…", expanded=False) as status:
        result, metrics = btc.run_strategy_cached(
            start, end, _BACKTEST_LEAD_DAYS, boards_json, exclude_st,
            include_delisted, max_codes, data_version,
            strategy_name, "registry", params_json, config_sig,
            json.dumps(btc.config_to_dict(config), ensure_ascii=False, sort_keys=True),
        )
        status.update(label="回测运行完成", state="complete")
    elapsed = time.perf_counter() - started
    report = make_report(result) if result is not None else {}
    return metrics or {}, report, result, btc.cache_caption(elapsed, saw_before=saw_before)


# ---------------- 过拟合自查（暂时需求 5）----------------

# 经验阈值：样本太短或交易太少时，回测指标基本没有统计意义
_OVERFIT_MIN_YEARS = 2.0     # 建议至少 2 年样本，覆盖一轮涨跌
_OVERFIT_MIN_TRADES = 30     # 少于 30 笔交易，胜率/盈亏比的随机波动过大


def _render_overfit_guide(metrics: dict, config_dict: dict) -> None:
    """右侧「过拟合自查」面板：先用本次回测的真实数字做自动诊断，再给规避清单。

    过拟合 = 策略把历史噪声也"背下来"了，回测很漂亮但一到实盘就失效。它没法靠单一
    指标直接判定，所以这里给的是几个**强警示信号**，以及对应的应对动作；用真实数字
    而不是固定文案，用户才知道自己这次到底哪里可疑。
    """
    years = float(config_dict.get("years", 0) or 0)
    trades = int(metrics.get("trade_count", 0) or 0)
    avg_pnl = float(metrics.get("avg_pnl_pct", 0) or 0)
    round_trip = float(config_dict.get("round_trip_cost_pct", 0) or 0)

    st.markdown("#### 🚨 过拟合自查")
    st.caption("回测好看 ≠ 实盘能赚。以下是过拟合的强警示信号。")

    checks: list[tuple[bool, str]] = [
        (
            years >= _OVERFIT_MIN_YEARS,
            f"样本 {years:.1f} 年（建议 ≥ {_OVERFIT_MIN_YEARS:.0f} 年，需覆盖一轮涨跌）",
        ),
        (
            trades >= _OVERFIT_MIN_TRADES,
            f"交易 {trades} 笔（建议 ≥ {_OVERFIT_MIN_TRADES} 笔，否则胜率/盈亏比随机性太大）",
        ),
    ]
    if round_trip > 0:
        cover = avg_pnl > round_trip
        checks.append((
            cover,
            f"单笔均收益 {avg_pnl:.2f}% vs 往返成本 {round_trip:.2f}%"
            + ("" if cover else "（赚的不够覆盖成本）"),
        ))

    for ok, text in checks:
        if ok:
            st.success("✓ " + text)
        else:
            st.warning("⚠ " + text)

    st.info("还没做过样本外验证？去「⚖️ 策略对比」页的 **Walk-Forward** 标签跑一次。")

    if not config_dict.get("include_delisted"):
        st.caption(
            "⚠️ 本次未包含已退市股票：样本里只有今天还活着的公司，收益会被系统性高估"
            "（实测 5 年窗口约 3.5% 的标的在此期间退市）。勾选「包含已退市股票」可消除这一偏差。"
        )

    with st.expander("怎么避免过拟合（6 条）"):
        st.markdown(
            "1. **留出样本外**：用一半时间调参、另一半只验证；不要反复看验证段再去改参数。\n"
            "2. **跑 Walk-Forward**：滚动地「训练→验证」，看验证段是否还稳定。\n"
            "3. **参数别调太细**：在最优参数附近小幅扰动，若收益大幅下滑，就是过拟合了。\n"
            "4. **少用参数**：一个策略可调参数越多，越容易把噪声也拟合进去。\n"
            "5. **算上交易成本**：频繁换手常被佣金+印花税+滑点吃光（见上面的成本诊断）。\n"
            "6. **换股票池验证**：在不同板块/股票池上表现是否一致。"
        )


# 月度热力图色阶：绿(跌) → 面板色(平) → 红(涨)，与全站「红涨绿跌」一致
_HEAT_SCALE = [[0.0, cs.DOWN], [0.5, cs.PANEL], [1.0, cs.UP]]


def _excess_caption(metrics: dict, result) -> str:
    """一句话结论：策略累计收益 vs 同期等权基准（超额有多少个点）。"""
    total = float(metrics.get("total_return", 0) or 0)
    dd = float(metrics.get("max_drawdown", 0) or 0)
    bench = getattr(result, "benchmark", None)
    if bench is None or len(bench) < 2:
        return f"策略累计收益 {total:+.2f}%，最大回撤 {dd:.2f}%（本次未生成基准：候选池行情不足）。"
    b = bench.astype(float).dropna()
    if len(b) < 2 or float(b.iloc[0]) <= 0:
        return f"策略累计收益 {total:+.2f}%，最大回撤 {dd:.2f}%。"
    bench_ret = (float(b.iloc[-1]) / float(b.iloc[0]) - 1) * 100
    diff = total - bench_ret
    word = "跑赢" if diff >= 0 else "跑输"
    return (
        f"策略累计 {total:+.2f}%，同期「等权持有全部候选股」基准 {bench_ret:+.2f}%，"
        f"{word}基准 {abs(diff):.2f} 个百分点；最大回撤 {dd:.2f}%。"
    )


def _render_hero(result, metrics: dict) -> None:
    """首屏主图 + 口径切换 + 买卖点开关 + 一句话结论。"""
    left, right = st.columns([3, 1], vertical_alignment="bottom")
    with left:
        mode = st.segmented_control(
            "主图口径",
            ["收益率", "金额"],
            default="收益率",
            key="backtest_hero_mode",
            help="收益率 = 相对起点涨了多少（带红色回撤阴影）；金额 = 账户里有多少钱。",
        )
    with right:
        show_trades = st.toggle(
            "标注买卖点",
            value=False,
            key="backtest_show_trades",
            help="在曲线上标出每笔交易的买入 ▲ / 卖出 ▼。交易多时会比较密，默认关闭。",
        )
    trades = getattr(result, "trades", None) if show_trades else None
    fig = backtest_hero_fig(result, mode=mode or "收益率", trades=trades)
    if fig is None:
        st.info("回测数据不足，无法绘制资金曲线。")
        return
    caption = _excess_caption(metrics, result)
    if show_trades and trades is not None and len(trades) > 0:
        caption += f" 已标注 {len(trades)} 笔交易的买卖点（▲ 买入 / ▼ 卖出）。"
    render_chart(fig, name="backtest-equity", caption=caption)


def _monthly_fig(report: dict) -> go.Figure | None:
    """月度收益柱状图：真日期轴（缩放时刻度会重算）+ 柱顶数值 + 红涨绿跌。"""
    rows = report.get("monthly_returns") or []
    keep = []
    for row in rows:
        if row.get("return") is None:
            continue
        xi = pd.to_datetime(row.get("month"), errors="coerce")
        if xi is None or pd.isna(xi):
            continue
        keep.append((xi, float(row["return"])))
    if not keep:
        return None
    fig = go.Figure(go.Bar(
        x=[k[0] for k in keep],
        y=[k[1] for k in keep],
        marker_color=[cs.up_down_colour(k[1]) for k in keep],
        # 只在幅度够大时写柱顶数值：零轴附近的小数值会互相挤成一团（见 Phase 1 样张复查）。
        text=[f"{k[1]:.1f}%" if abs(k[1]) >= 2 else "" for k in keep],
        textposition="outside",
        textfont=dict(size=10, color=cs.AXIS_TEXT),
        name="月度收益",
        hovertemplate="%{x|%Y-%m}<br>%{y:.2f}%<extra></extra>",
    ))
    cs.apply_base_layout(fig, height=cs.H_SECONDARY, y_title="月度收益率（%）", zero_line=True)
    fig.update_yaxes(**cs.axis_pct_points())
    fig.update_layout(bargap=0.4)
    apply_time_axis(fig)
    return fig


def _heatmap_fig(report: dict) -> go.Figure | None:
    """月度收益热力图：一年一行、一月一列，一眼看出哪几个月在赚钱。"""
    hm = report.get("monthly_heatmap") or []
    if not hm:
        return None
    years = sorted({int(h["year"]) for h in hm})
    months = list(range(1, 13))
    z: list[list] = []
    for y in years:
        row = []
        for mo in months:
            hit = next(
                (h for h in hm if int(h["year"]) == y and int(h["month"]) == mo), None
            )
            row.append(None if hit is None else float(hit["return"]))
        z.append(row)
    fig = go.Figure(go.Heatmap(
        z=z,
        x=[f"{m}月" for m in months],
        y=[str(y) for y in years],
        colorscale=_HEAT_SCALE,
        zmid=0.0,
        text=[[("" if v is None else f"{v:.1f}") for v in row] for row in z],
        texttemplate="%{text}",
        textfont=dict(size=10),
        hovertemplate="%{y} 年 %{x}<br>%{z:.2f}%<extra></extra>",
        colorbar=dict(
            thickness=10,
            len=0.75,
            outlinewidth=0,
            ticksuffix="%",
            tickfont=dict(size=10, color=cs.AXIS_TEXT),
        ),
    ))
    cs.apply_base_layout(
        fig,
        height=max(200, 46 * len(years) + 70),
        hovermode="closest",
        legend=None,
        right_axis=False,  # 年份标签留在左侧，右边缘留给 colorbar
    )
    return fig


def _tab_overview(report: dict) -> None:
    """总览：回撤区间（策略最难走的几段路）。"""
    st.markdown("##### 回撤区间")
    periods = report.get("drawdown_periods") or []
    if not periods:
        st.info("回测期间无回撤。")
        return
    st.dataframe(
        [
            {
                "开始": d["start"],
                "结束": d["end"],
                "最大回撤": f"{d['max_drawdown']:.2f}%",
            }
            for d in periods
        ],
        width="stretch",
        hide_index=True,
    )
    st.caption("每一行是「从阶段高点跌到阶段低点」的一段，越靠上跌得越狠。")


def _tab_monthly(report: dict) -> None:
    """月度：收益柱 + 热力图。"""
    monthly = _monthly_fig(report)
    if monthly is not None:
        render_chart(
            monthly,
            name="backtest-monthly",
            caption="每月收益率：红涨绿跌，柱子越高说明这个月赚得越多。",
        )
    else:
        st.info("回测区间不足一个月，暂无月度收益。")

    heat = _heatmap_fig(report)
    if heat is not None:
        returns = [m for m in (report.get("monthly_returns") or []) if m.get("return") is not None]
        caption = "横轴是月份、纵轴是年份，红色代表这个月赚钱、绿色代表亏钱。"
        if returns:
            best = max(returns, key=lambda m: m["return"])
            worst = min(returns, key=lambda m: m["return"])
            caption = (
                f"最好 {best['month']}（{best['return']:+.1f}%），"
                f"最差 {worst['month']}（{worst['return']:+.1f}%）。{caption}"
            )
        render_chart(heat, name="backtest-monthly-heatmap", caption=caption)


def _tab_trades(report: dict, result) -> None:
    """交易：盈亏分布 + 最近交易明细。"""
    st.markdown("##### 交易盈亏分布")
    dist = report.get("trade_distribution") or []
    if dist:
        fig = go.Figure(go.Bar(
            x=[t["range"] for t in dist],
            y=[t["count"] for t in dist],
            marker_color=cs.series_colour(1),
            text=[t["count"] for t in dist],
            textposition="outside",
            textfont=dict(size=10, color=cs.AXIS_TEXT),
            name="笔数",
            hovertemplate="%{x}<br>%{y} 笔<extra></extra>",
        ))
        cs.apply_base_layout(
            fig,
            height=cs.H_SECONDARY,
            y_title="笔数",
            right_axis=False,
            hovermode="closest",
        )
        render_chart(
            fig,
            name="backtest-trade-dist",
            caption="单笔交易的最终收益率落在哪个区间：靠右的柱子越多说明大赚的越多。",
        )
    else:
        st.info("无交易数据。")

    st.markdown("##### 最近交易")
    trades = getattr(result, "trades", None)
    if trades is not None and not trades.empty:
        st.dataframe(trades.tail(20), width="stretch", hide_index=True)
    else:
        st.info("本次回测没有产生交易。")


def _tab_attribution(report: dict) -> None:
    """归因：按退出原因统计表现，用来判断止盈/止损位要不要调。"""
    rows = report.get("exit_attribution") or []
    if not rows:
        st.info("无交易数据。")
        return
    st.dataframe(
        [
            {
                "退出原因": a["reason"],
                "笔数": a["count"],
                "平均收益%": a["avg_pnl_pct"],
                "胜率%": a["win_rate"],
            }
            for a in rows
        ],
        width="stretch",
        hide_index=True,
    )
    st.caption("按「为什么卖」分组：哪类退出贡献最大，决定该收紧止损还是放宽止盈。")


def _render_result(metrics: dict, report: dict, result, config_dict: dict):
    """渲染回测结果（从 session_state 读取，避免按钮交互导致结果丢失）。"""
    # 左侧核心指标 + 右侧过拟合自查（暂时需求 5：回测右方教用户避免过拟合）
    left, right = st.columns([3, 1], gap="medium")
    with left:
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
    with right:
        _render_overfit_guide(metrics, config_dict)

    # 首屏主图：策略净值 + 等权基准 + 回撤阴影（金额 / 收益率可切换）
    _render_hero(result, metrics)

    tab_over, tab_month, tab_trade, tab_attr = st.tabs(
        ["📌 总览", "📅 月度", "🧾 交易", "🔍 归因"]
    )
    with tab_over:
        _tab_overview(report)

    with tab_month:
        _tab_monthly(report)
    with tab_trade:
        _tab_trades(report, result)
    with tab_attr:
        _tab_attribution(report)

    # LLM 复盘与参数优化建议
    st.divider()
    st.subheader("🤖 LLM 复盘与参数优化建议")
    if st.button("💡 生成复盘建议", key="llm_review"):
        with st.spinner("正在生成复盘建议…"):
            review = review_backtest(metrics, report, config_dict)
        st.markdown(review)


def render():
    st.header("📊 回测结果")
    from stock_plan.data.snapshot import is_cloud

    if is_cloud():
        st.info(
            "云端轻量模式说明：当前回测基于约 1800 只股票组成的样本市场，而非全部 A 股。"
            "收益、胜率与选股结果可能与全市场回测存在差异；本地运行不受此限制。"
        )
    st.caption("用历史数据验证策略表现：逐日模拟 T+1 交易，含手续费/印花税/滑点/涨跌停。")
    page_glossary(BACKTEST_GLOSSARY)
    btc.render_clear_cache_button("bt")

    # 选股范围（R5：板块筛选 + 剔除 ST）
    st.markdown("**选股范围**")
    boards, exclude_st = board_filter_ui("bt")

    # 参数设置
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        from stock_plan.strategy import store

        strategy_name = st.selectbox("策略", store.strategy_options())
    with col2:
        default_end = date.today()
        default_start = default_end - timedelta(days=365)
        start = st.date_input("开始日期", default_start)
    with col3:
        end = st.date_input("结束日期", default_end)
    with col4:
        initial_cash = st.number_input("初始资金", 10_000, 10_000_000, 100_000, step=10_000)

    # 已保存策略可能自带 hold_days（LLM 生成 / 策略拼装保存时写进 params 的）。
    # 原先这里的默认值写死 30，于是"选中自己保存的持 15 天策略"实际跑的是持 30 天
    # 版本——用户以为在验证自己的策略，其实验证的是另一个东西，还以为参数没生效。
    import json as _json

    _raw_name = store.strip_display_name(strategy_name)
    _saved_rec = store.load_strategy(_raw_name) or {}
    _saved_cfg = _saved_rec.get("config") or {}
    _hold_default = int((_saved_cfg.get("params") or {}).get("hold_days") or 30)
    _hold_default = max(5, min(120, _hold_default))
    # 配置指纹：参与回测缓存键。原先缓存键只有 14 个显式参数、不含策略配置，
    # 而配置是在函数体内从数据库读的 → 改了权重/规则后，最长 10 分钟仍拿旧结果。
    config_sig = _json.dumps(_saved_cfg, ensure_ascii=False, sort_keys=True)

    col5, col6, col7, col8 = st.columns(4)
    with col5:
        rebalance_freq = st.selectbox("选股频率", ["weekly", "daily"], index=0)
    with col6:
        # 档位择时（2026-10-29 Phase 19）：默认关闭 = 与历史结果完全一致。
        _tier_labels = {"关闭（默认）": "off", "双均线三档（减仓/清仓）": "dual_ma"}
        _tier_label = st.selectbox(
            "大盘择时档位",
            list(_tier_labels),
            index=0,
            help=(
                "关闭：不做档位择时（默认，与以往结果一致）。"
                "双均线三档：收盘在 MA20 上方满仓、跌破 MA20 减到 50%、"
                "跌破 MA60 且 MA20 下行则清仓；只减仓不追买，次日生效。"
            ),
        )
        timing_tier = _tier_labels[_tier_label]
        market_timing = st.checkbox(
            "弱择时（低于 MA20 只把新买入减半）",
            value=True,
            disabled=timing_tier != "off",
            help="这是旧口径的「半仓」近似：只影响新买入预算，不做真实减仓。选中档位时以档位为准。",
        )
    with col7:
        max_hold_days = st.number_input(
            "最大持仓天数", 5, 120, _hold_default, step=5,
            key=f"bt_hold_{_raw_name}",
            help="默认取所选策略自带的天数（保存策略时的 hold_days）；可手动覆盖。",
        )
    with col8:
        st.caption("")

    # 交易成本（暂时需求 5）：原先佣金万三 / 印花税千一 / 滑点千一是写死的，用户改不了
    with st.expander("💰 交易成本设置（佣金 / 印花税 / 滑点）", expanded=False):
        cc1, cc2, cc3, cc4 = st.columns(4)
        with cc1:
            commission_w = st.number_input(
                "佣金（万分之）", 0.0, 100.0, 3.0, step=0.5,
                help="买卖都收。万分之三 = 每 1 万元成交收 3 元。多数券商在万 1～万 3。",
            )
        with cc4:
            min_commission = st.number_input(
                "单笔佣金下限（元）", 0.0, 50.0, 5.0, step=1.0,
                help=(
                    "券商普遍「5 元起收」：买 1 万元按万三只有 3 元，实际收 5 元。"
                    "设为 0 表示不设下限（会把小额单的成本算少）。"
                ),
            )
        with cc2:
            stamp_tax_w = st.number_input(
                "印花税（万分之，仅卖出）", 0.0, 100.0, 10.0, step=1.0,
                help=(
                    "只在卖出时收。A 股为卖方单边征收，且该税率在历史上调整过 —— "
                    "所以用一个统一费率做跨年度回测会与实际有偏差。"
                    "本项目实测：全程按万分之十算，比按日期分段多收约初始资金的 1.4%"
                    "（方向是「高估成本、偏悲观」）。请按你回测区间的实际税率填写；"
                    "不确定时先查交易所/税务的最新公告。"
                ),
            )
        with cc3:
            slippage_w = st.number_input(
                "滑点（万分之）", 0.0, 200.0, 10.0, step=1.0,
                help="模拟实际成交价比看到的价格差一点。小盘股、流动性差的股票应调大。",
            )
        _round_trip_w = 2 * commission_w + stamp_tax_w + 2 * slippage_w
        round_trip_cost_pct = _round_trip_w / 100
        st.caption(
            f"买卖一个来回的总成本约 **{round_trip_cost_pct:.2f}%**（万分之 {_round_trip_w:.0f}）："
            f"一笔交易要先赚 {round_trip_cost_pct:.2f}% 才回本。换手率越高，这笔成本越致命。"
        )

    include_delisted = st.checkbox(
        "包含已退市股票（消除幸存者偏差）",
        value=False,
        key="bt_include_delisted",
        help=(
            "默认关闭。开启后回测样本会包含窗口内退市的股票——它们正是跌到退市的那批，"
            "原先被完全排除，导致收益被系统性高估（实测 5 年窗口约 3.5% 的标的在此期间退市）。"
            "需要先用 `python -m stock_plan.data.delisted --delisted` 补齐退市股行情。"
            "注意：开启后回测结果会明显变化，历史调参结论需要重跑。"
        ),
    )

    if st.button("▶️ 运行回测", type="primary"):
        if start >= end:
            st.error("开始日期必须早于结束日期")
        else:
            by_name = st.session_state.get("strategy_params_by_strategy", {})
            saved = by_name.get(strategy_name) or {}
            if not saved and strategy_name == "趋势跟随策略":
                saved = st.session_state.get("strategy_params") or {}
            metrics, report, result, cache_note = _run_backtest(
                strategy_name,
                start.isoformat(),
                end.isoformat(),
                float(initial_cash),
                json.dumps(saved, ensure_ascii=False),
                rebalance_freq,
                bool(market_timing),
                int(max_hold_days),
                boards,
                exclude_st,
                float(commission_w),
                float(stamp_tax_w),
                float(slippage_w),
                float(min_commission),
                bool(include_delisted),
                config_sig,
                timing_tier,
            )
            if not metrics:
                st.warning("回测无数据。请先到左侧「🔄 数据更新」页拉取数据（首次使用需全量拉取，耗时较久）。")
            else:
                # 存入 session_state，保证后续按钮交互（如 LLM 复盘）不丢失结果
                st.session_state["backtest_result"] = (metrics, report, result)
                st.session_state["backtest_cache_note"] = cache_note
                st.session_state["backtest_config"] = {
                    "m": saved.get("m", 3.5),
                    "n": saved.get("n", 3.5),
                    "hold": int(max_hold_days),
                    "rebalance_freq": rebalance_freq,
                    "market_timing": bool(market_timing),
                    "timing_tier": timing_tier,
                    # 供右侧「过拟合自查」用的上下文
                    "years": (end - start).days / 365.0,
                    "round_trip_cost_pct": round_trip_cost_pct,
                    "commission_w": float(commission_w),
                    "stamp_tax_w": float(stamp_tax_w),
                    "slippage_w": float(slippage_w),
                    "min_commission": float(min_commission),
                    "include_delisted": bool(include_delisted),
                }

    # 从 session_state 渲染结果（若已运行过回测）
    if "backtest_result" in st.session_state:
        metrics, report, result = st.session_state["backtest_result"]
        config_dict = st.session_state.get("backtest_config", {})
        cache_note = st.session_state.get("backtest_cache_note")
        if cache_note:
            st.caption(cache_note)
        _render_result(metrics, report, result, config_dict)
    else:
        st.info("设置参数后点击上方按钮运行回测。")
