"""回测结果页面 — 选择策略与日期 → 运行回测 → 展示绩效。

展示内容：
- 策略选择 + 起止日期 + 初始资金
- 核心指标卡片（总收益/年化/最大回撤/夏普/胜率/盈亏比/交易次数）
- 资金曲线（Plotly 折线图）
- 月度收益柱状图
- 回撤区间与交易盈亏分布

Revision History:
    2026-09-04  switch to windowed load_market_maps to avoid cloud OOM
    2026-09-04  show cloud sample-universe disclosure
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
from stock_plan.ui.widgets import apply_time_axis, PLOTLY_CONFIG, board_filter_ui, page_glossary

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
    commission_w: float = 3.0, stamp_tax_w: float = 10.0, slippage_w: float = 10.0,
    min_commission: float = 5.0,
    include_delisted: bool = False, config_sig: str = "",
):
    """带缓存回测（10 分钟内不重复计算）。

    交易成本三个参数单位统一为**万分之**（万三 = 3），与券商报价习惯一致：
    - commission_w 佣金，买卖都收，默认 3（= 0.0003）
    - stamp_tax_w  印花税，仅卖出收，默认 10（= 0.001，即千分之一）
    - slippage_w   滑点，买卖都收，默认 10（= 0.001）
    这些参数参与缓存键，改动后会自动重算。
    config_sig 是所选策略的**配置指纹**（JSON）。它不参与计算，但必须进缓存键：
    策略配置是在函数体内按名字从数据库读的，不会出现在上面的显式参数里，
    少了它就会出现"改了策略参数、回测结果却不变"的假象（最多持续 10 分钟）。
    """
    import json

    from stock_plan.data.snapshot import CLOUD_MAX_CODES, is_cloud

    # 窗口 + 预筛选加载（指标预热 180 个日历日，云端限制股票数量）
    boards = json.loads(boards_json)
    stock_list, bars_map, fund_map = Storage().load_market_maps(
        start=start, end=end, lead_days=180,
        boards=boards, exclude_st=exclude_st,
        max_codes=CLOUD_MAX_CODES if is_cloud() else None,
        include_delisted=include_delisted,
    )

    params = json.loads(params_json) if params_json else None
    from stock_plan.strategy import store

    strategy = store.resolve_strategy(strategy_name, params)

    config = BacktestConfig(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        initial_cash=initial_cash,
        rebalance_freq=rebalance_freq,
        market_timing=market_timing,
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
    result = run_backtest(strategy, config, bars_map, fund_map, stock_list)
    metrics = calc_metrics(result.equity_curve, result.trades)
    report = make_report(result)
    return metrics, report, result


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

    # 资金曲线
    st.subheader("资金曲线")
    if report["equity_curve"]:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=pd.to_datetime([p["date"] for p in report["equity_curve"]]),
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
        apply_time_axis(fig)
        st.plotly_chart(fig, use_container_width=True, config=PLOTLY_CONFIG)

    # 收益曲线（R3 需求：累计收益率 % + 回撤区域标注）
    st.subheader("📈 收益曲线（动态变化）")
    st.caption(
        "蓝线是「累计收益率」：从回测第一天算起，账户总共赚/亏了百分之几。"
        "红色阴影是「回撤」：从阶段最高点跌下来的幅度，阴影越深说明中途跌得越狠。"
    )
    # 注意：这里不要 import apply_time_axis —— 函数内的 import 会把它变成该函数的
    # 局部名，导致上面模块级导入的那次调用（在本次 import 之前执行）抛 UnboundLocalError。
    from stock_plan.ui.widgets import equity_curve_fig

    eq = result.equity_curve if result is not None else None
    fig_ret = equity_curve_fig(eq)
    if fig_ret is not None:
        st.plotly_chart(fig_ret, use_container_width=True, config=PLOTLY_CONFIG)
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
        st.plotly_chart(fig2, use_container_width=True, config=PLOTLY_CONFIG)

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
        st.plotly_chart(fig3, use_container_width=True, config=PLOTLY_CONFIG)

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
        st.plotly_chart(fig4, use_container_width=True, config=PLOTLY_CONFIG)

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
    from stock_plan.data.snapshot import is_cloud

    if is_cloud():
        st.info(
            "云端轻量模式说明：当前回测基于约 1800 只股票组成的样本市场，而非全部 A 股。"
            "收益、胜率与选股结果可能与全市场回测存在差异；本地运行不受此限制。"
        )
    st.caption("用历史数据验证策略表现：逐日模拟 T+1 交易，含手续费/印花税/滑点/涨跌停。")
    page_glossary(BACKTEST_GLOSSARY)

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
        market_timing = st.checkbox("大盘择时（低于 MA20 减半仓）", value=True)
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
            import json

            by_name = st.session_state.get("strategy_params_by_strategy", {})
            saved = by_name.get(strategy_name) or {}
            if not saved and strategy_name == "趋势跟随策略":
                saved = st.session_state.get("strategy_params") or {}
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
                float(commission_w),
                float(stamp_tax_w),
                float(slippage_w),
                float(min_commission),
                bool(include_delisted),
                config_sig=config_sig,
            )
            if not metrics:
                st.warning("回测无数据。请先到左侧「🔄 数据更新」页拉取数据（首次使用需全量拉取，耗时较久）。")
            else:
                # 存入 session_state，保证后续按钮交互（如 LLM 复盘）不丢失结果
                st.session_state["backtest_result"] = (metrics, report, result)
                st.session_state["backtest_config"] = {
                    "m": saved.get("m", 3.5),
                    "n": saved.get("n", 3.5),
                    "hold": int(max_hold_days),
                    "rebalance_freq": rebalance_freq,
                    "market_timing": bool(market_timing),
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
        _render_result(metrics, report, result, config_dict)
    else:
        st.info("设置参数后点击上方按钮运行回测。")