"""今日信号页面 — 选择策略与持仓周期 → 生成盘前信号 → 展示 Top5 标的。

展示内容：
- 策略选择（下拉框）+ 持仓周期（短线/中短线/中线/多周期并存）
- 生成信号按钮（带缓存，避免重复计算）
- Top5 信号表格（代码/名称/综合分/买入价/卖出价/止损/持仓天数/理由）
- 一键把信号发送到模拟交易

Revision History:
    2026-09-04  show version-matched published full-market public signals
"""
from __future__ import annotations

import streamlit as st

from stock_plan.signal.generator import Signal, generate_signals
from stock_plan.ui.widgets import board_filter_ui, page_glossary

# 本页名词速览（R2 亲民化）
TODAY_GLOSSARY = {
    "综合分": "给每只股票打的 总分（0-100），分数越高越值得买。由趋势、基本面等多个角度加权算出。",
    "目标买入价": "建议的买入价格。可以挂限价单等股价到了再买，不追高。",
    "目标卖出价": "建议的止盈价格。涨到这里就落袋为安。",
    "止损价": "跌到这个价就认赔卖出，防止亏损扩大。纪律比预测更重要。",
    "持仓天数": "建议拿多久。到期即使没涨没跌也建议换股，让资金效率更高。",
    "板块": "股票所在的「市场分区」，如主板、创业板、科创板。下方可自由勾选。",
    "ST": "被交易所标记「有退市风险」的股票，名字带 ST 两个字，新手建议避开。",
}

# 持仓周期配置：名称 → (hold_days, 说明)
PERIODS = {
    "短线": (10, "快进快出，适合波动大的标的"),
    "中短线": (20, "波段操作，兼顾趋势与节奏"),
    "中线": (30, "趋势持有，容忍回调"),
}


def _get_strategy(name: str, params: dict | None = None):
    """根据名称返回策略实例（应用已保存的参数）。"""
    if name == "自定义策略":
        from stock_plan.strategy.custom import CustomStrategy

        config = st.session_state.get("custom_strategy_config", {})
        strategy = CustomStrategy(config)
        if params:
            strategy.params = {**strategy.params, **params}
        return strategy
    # 内置策略 + 已保存策略（LLM 生成/拼装页保存）统一走 store
    from stock_plan.strategy import store

    return store.resolve_strategy(name, params)


@st.cache_data(ttl=600, show_spinner="正在生成信号…")
def _cached_signals(
    strategy_name: str, top_n: int, params_json: str, config_json: str,
    boards_json: str = "[]", exclude_st: bool = True,
):
    """带缓存的信号生成（10 分钟内不重复计算）。"""
    import json

    params = json.loads(params_json) if params_json else None
    boards = json.loads(boards_json)
    if strategy_name == "自定义策略":
        from stock_plan.strategy.custom import CustomStrategy

        config = json.loads(config_json) if config_json else {}
        strategy = CustomStrategy(config)
        if params:
            strategy.params = {**strategy.params, **params}
    else:
        strategy = _get_strategy(strategy_name, params)
    from stock_plan.data.snapshot import is_cloud

    if is_cloud():
        from stock_plan.strategy import store
        from stock_plan.strategy.publication import load_public_signals, strategy_fingerprint

        record = store.load_strategy(strategy_name)
        if record is not None:
            published = load_public_signals().get(strategy_name)
            if published is None or published.get("fingerprint") != strategy_fingerprint(record["config"]):
                return []
            return [Signal(**signal) for signal in published.get("signals", [])[:top_n]]
    return generate_signals(
        strategy=strategy, top_n=top_n, boards=boards, exclude_st=exclude_st
    )


def _public_strategy_status(strategy_name: str, boards: list[str], exclude_st: bool) -> str | None:
    """云端公共策略只能使用版本匹配的全 A 股已发布结果。"""
    from stock_plan.data.snapshot import is_cloud

    if not is_cloud():
        return None
    from stock_plan.strategy import store
    from stock_plan.strategy.publication import load_public_signals, strategy_fingerprint

    record = store.load_strategy(strategy_name)
    if record is None:
        return None
    if boards or not exclude_st:
        return "公共策略的全 A 股信号不支持本页板块/ST 二次筛选；请使用“全部板块”并勾选“剔除 ST”。"
    published = load_public_signals().get(strategy_name)
    if published is None or published.get("fingerprint") != strategy_fingerprint(record["config"]):
        return (
            f"策略「{strategy_name}」正在等待开发者本机完成全 A 股计算并发布；"
            "发布后将在下一个交易日早上 9:00 生效。"
        )
    return None


def _signals_to_rows(signals):
    """把 Signal 列表转成表格行。"""
    return [
        {
            "代码": s.code,
            "名称": s.name,
            "综合分": s.score,
            "目标买入价": s.entry_price,
            "目标卖出价": s.exit_price,
            "止损价": s.stop_loss,
            "持仓天数": s.hold_days,
        }
        for s in signals
    ]


def _store_signals(signals, period_label: str):
    """把信号存入 session_state（供 LLM 页信号解释使用）。"""
    if "today_signals_by_period" not in st.session_state:
        st.session_state["today_signals_by_period"] = {}
    st.session_state["today_signals_by_period"][period_label] = [
        {
            "code": s.code,
            "name": s.name,
            "score": s.score,
            "entry_price": s.entry_price,
            "exit_price": s.exit_price,
            "stop_loss": s.stop_loss,
            "hold_days": s.hold_days,
            "period": period_label,
            "reason": "；".join(s.reasons),
        }
        for s in signals
    ]
    # 兼容旧版：today_signals 指向最近一组（供 LLM 页信号解释使用）
    st.session_state["today_signals"] = st.session_state["today_signals_by_period"][period_label]


def _render_signal_block(signals, period_label: str):
    """渲染一组信号的表格 + 理由 + 发送按钮。"""
    st.subheader(f"📌 {period_label}信号（持仓 {signals[0].hold_days} 天）")
    st.dataframe(_signals_to_rows(signals), use_container_width=True, hide_index=True)

    st.markdown("**入选理由**")
    for s in signals:
        with st.expander(f"{s.code} {s.name}（综合分 {s.score}）"):
            st.write("；".join(s.reasons))
            st.caption(
                f"买入 {s.entry_price} → 卖出 {s.exit_price}，止损 {s.stop_loss}，"
                f"建议持仓 {s.hold_days} 天"
            )

    if st.button(
        f"📥 发送{period_label}信号到模拟交易",
        type="secondary",
        key=f"send_{period_label}",
    ):
        from stock_plan.simulator.paper import PaperTrader

        trader = PaperTrader()
        for s in signals:
            trader.on_signal(s)
        st.success(f"已将 {len(signals)} 条{period_label}信号发送到模拟交易账户")

        # 推送（飞书/微信）
        if st.button(f"📤 推送{period_label}信号", type="secondary", key=f"push_{period_label}"):
            from stock_plan.push.notify import format_signals_text, push_feishu, push_wechat

            text = format_signals_text(signals)
            ok_feishu = push_feishu(text)
            ok_wechat = push_wechat(text)
            if ok_feishu or ok_wechat:
                st.success("信号推送成功（飞书/微信）")
            else:
                st.warning(
                    "未配置推送渠道。请在环境变量设置 FEISHU_WEBHOOK（飞书）"
                    "或 WECHAT_SENDKEY（Server酱）后重启应用。"
                )


def render():
    st.header("📋 今日信号")
    st.caption("基于最新缓存数据，按策略打分选出 Top 标的，输出目标买入/卖出价与止损位。")
    page_glossary(TODAY_GLOSSARY)

    # 板块筛选（R5：自定义股票板块 + 剔除 ST）
    st.markdown("**选股范围**")
    boards, exclude_st = board_filter_ui("today")

    # 策略选择
    from stock_plan.strategy import store

    has_custom = bool(st.session_state.get("custom_strategy_config"))
    options = store.strategy_options()
    if has_custom and "自定义策略" not in options:
        options.append("自定义策略")
    strategy_name = st.selectbox(
        "选择策略",
        options,
        help="内置策略 + 已保存策略（LLM 生成或拼装页保存）+ 自定义策略（需先在「策略拼装」页保存）；参数可在「策略管理」页调整。",
    )
    top_n = st.slider("信号数量", 3, 10, 5)

    # 持仓周期选择
    period_options = ["多周期并存"] + list(PERIODS.keys())
    period_label = st.selectbox(
        "持仓周期",
        period_options,
        index=2,  # 默认中线
        help="多周期并存：同时输出短线/中短线/中线三组信号，便于对比。",
    )

    if st.button("🔄 生成今日信号", type="primary"):
        import json

        status = _public_strategy_status(strategy_name, boards, exclude_st)
        if status is not None:
            st.warning(status)
            return

        by_name = st.session_state.get("strategy_params_by_strategy", {})
        saved = by_name.get(strategy_name) or {}
        if not saved and strategy_name == "趋势跟随策略":
            saved = st.session_state.get("strategy_params") or {}  # 兼容旧版
        config = st.session_state.get("custom_strategy_config", {})

        if period_label == "多周期并存":
            # 多周期并存：为每个持仓周期生成一组信号
            all_groups = {}
            for label, (hold_days, _desc) in PERIODS.items():
                period_params = {**saved, "hold_days": hold_days}
                signals = _cached_signals(
                    strategy_name, top_n,
                    json.dumps(period_params, ensure_ascii=False),
                    json.dumps(config, ensure_ascii=False),
                    json.dumps(boards, ensure_ascii=False),
                    exclude_st,
                )
                if signals:
                    all_groups[label] = signals
            if not all_groups:
                st.warning("未生成信号。请先到左侧「🔄 数据更新」页拉取数据（首次使用需全量拉取，耗时较久）。")
                return
            st.success(f"已生成 {len(all_groups)} 组信号（短线/中短线/中线）")
            for label, signals in all_groups.items():
                _store_signals(signals, label)
                _render_signal_block(signals, label)
        else:
            # 单周期
            hold_days = PERIODS[period_label][0]
            period_params = {**saved, "hold_days": hold_days}
            signals = _cached_signals(
                strategy_name, top_n,
                json.dumps(period_params, ensure_ascii=False),
                json.dumps(config, ensure_ascii=False),
                json.dumps(boards, ensure_ascii=False),
                exclude_st,
            )
            if not signals:
                st.warning("未生成信号。请先到左侧「🔄 数据更新」页拉取数据（首次使用需全量拉取，耗时较久）。")
                return
            st.success(f"共生成 {len(signals)} 条{period_label}信号")
            _store_signals(signals, period_label)
            _render_signal_block(signals, period_label)
    else:
        st.info("点击上方按钮生成今日盘前信号。")