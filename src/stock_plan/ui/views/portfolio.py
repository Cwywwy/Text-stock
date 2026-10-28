# -*- coding: utf-8 -*-
"""持仓诊断页面 — 输入买入信息 → 规则对照结论 + 做T价位建议 + 单股重回测。

数据优先读本地缓存；无缓存个股自动尝试在线拉取（新浪源），
次新股 / 无数据个股给出明确提示。

Revision History:
2026-10-28  (phase 2) route price and equity charts through widgets factories
"""
from __future__ import annotations

import streamlit as st

from stock_plan.analysis.holding import diagnose_holding, single_stock_backtest
from stock_plan.data.storage import Storage
from stock_plan.strategy import store
from stock_plan.ui.widgets import multi_line_fig, page_glossary, price_level_fig, render_chart

HOLD_GLOSSARY = {
    "持仓诊断": "把你手上的股票交给系统检查：赚了还是亏了、拿了多久、趋势好不好，再对照策略规则给个建议。",
    "做T": "持有底仓不变，盘中先卖后买或先买后卖赚差价，从而降低持仓成本。适合震荡行情。",
    "低吸 / 高抛": "跌到支撑位附近买一点（低吸），涨到压力位附近卖一点（高抛）。",
    "ATR": "这只股票平均一天的波动幅度，用来推算「今天大概能到的高点和低点」。",
    "止损位": "跌破这个价就卖出认赔，防止越亏越多。纪律比预测更重要。",
    "正T / 反T": "先买后卖叫正T（适合早盘急跌）；先卖后买叫反T（适合冲高时先锁定利润）。",
}


def render():
    st.header("🩺 持仓诊断")
    st.caption(
        "输入你持有的股票和买入信息，系统会：① 对照所选策略的止损/止盈/持仓天数规则给出"
        "「继续持有 / 做T / 减仓 / 清仓」建议；② 给出做T参考价位与操作步骤；③ 可一键用该策略重新回测这只股票作参考。"
    )
    page_glossary(HOLD_GLOSSARY)

    # ---------- 输入区 ----------
    with st.form("hold_diag_form"):
        col1, col2, col3 = st.columns(3)
        with col1:
            code = st.text_input("股票代码", placeholder="例如：600519", help="6 位 A 股代码")
        with col2:
            buy_date = st.date_input("买入日期")
        with col3:
            buy_price = st.number_input("买入价格（元/股）", min_value=0.01, step=0.01, format="%.2f")
        strategy_name = st.selectbox("用哪个策略来对照", store.strategy_options())
        submitted = st.form_submit_button("🔍 开始诊断", type="primary")

    if not submitted:
        _render_cached()
        return
    if not code.strip() or buy_price <= 0:
        st.warning("请先填写股票代码和买入价格")
        return

    with st.spinner("正在诊断（无缓存数据时会尝试在线拉取，请稍候）…"):
        try:
            res = diagnose_holding(
                code.strip(), buy_date.isoformat(), float(buy_price),
                strategy_name, storage=Storage(),
            )
        except Exception as e:  # noqa: BLE001 - 页面兜底
            st.error(f"诊断出错：{e}")
            return
    st.session_state["hold_diag_result"] = (res, strategy_name)
    _render_cached()


def _render_cached():
    """渲染诊断结果（从 session_state 读取，避免交互导致结果丢失）。"""
    cached = st.session_state.get("hold_diag_result")
    if not cached:
        return
    res, strategy_name = cached
    st.markdown("---")
    if not res.get("ok"):
        st.error(f"无法诊断：{res.get('msg', '未知原因')}")
        return

    # ---------- 状态卡片 ----------
    cur = res["current"]
    lv = res["levels"]
    action_color = {
        "hold": ("✅", "继续持有"), "t_trade": ("🔄", "可做T降成本"),
        "reduce": ("⚠️", "建议减仓"), "exit": ("🛑", "建议清仓"),
    }[res["action"]]
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("现价", f"{cur['close']:.2f}", f"{cur['profit_pct']:+.1f}%")
    c2.metric("已持有", f"{cur['hold_days']} 天", f"策略建议 {lv.get('hold_days_ref', '-')} 天" if lv.get("hold_days_ref") else None)
    c3.metric("盈亏", f"{cur['profit_pct']:+.2f}%")
    c4.metric("策略止损位", f"{lv['stop_loss']:.2f}" if lv.get("stop_loss") else "-")
    c5.metric("策略止盈区", f"{lv['take_profit']:.2f}" if lv.get("take_profit") else "-")

    st.markdown(f"## {action_color[0]} {action_color[1]}")
    if res.get("note"):
        st.info(res["note"])
    for r in res["reasons"]:
        st.markdown(f"- {r}")
    st.caption(
        f"数据来源：{res['source']}；行情截至 {cur['date']}；"
        "以上为策略规则对照结果，不构成投资建议。"
    )

    # ---------- 做T建议 ----------
    tt = res["t_trade"]
    st.subheader(f"🔄 做T建议：{tt['mode']}")
    t1, t2 = st.columns(2)
    t1.metric("低吸参考价", f"{tt['buy_low']:.2f}")
    t2.metric("高抛参考价", f"{tt['sell_high']:.2f}")
    for i, s in enumerate(tt["steps"], 1):
        st.markdown(f"{i}. {s}")

    # ---------- 走势图 ----------
    _render_chart(res)

    # ---------- 缠论结构（暂时需求 6 后半） ----------
    with st.expander("🧩 缠论结构分析（笔 / 中枢 / 三买）", expanded=False):
        _render_chan_structure(res)

    # ---------- 单股重回测 ----------
    st.subheader("📈 重新回测参考")
    if st.button("用该策略回测这只股票（近一年）"):
        with st.spinner("正在回测…"):
            bt = single_stock_backtest(res["code"], strategy_name, storage=Storage())
        if not bt.get("ok"):
            st.warning(f"回测失败：{bt.get('msg')}")
        else:
            m = bt["metrics"]
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("总收益%", round(m.get("total_return", 0), 2))
            m2.metric("最大回撤%", round(m.get("max_drawdown", 0), 2))
            m3.metric("胜率%", round(m.get("win_rate", 0), 2))
            m4.metric("交易次数", m.get("trade_count", 0))
            eq = bt["equity"]
            if eq is not None and not eq.empty:
                render_chart(
                    multi_line_fig({"单股回测": eq}, y_title="总资产（元）", money=True),
                    name="portfolio-single-backtest",
                    caption="只用这一只股票跑出来的资产曲线，样本单一，别当成策略的普遍表现。",
                )
            st.caption("单股回测仅反映该策略在这只股票上的历史表现，样本单一，参考意义有限。")


def _render_chan_structure(res) -> None:
    """缠论结构辅助分析（暂时需求 6 后半）。

    只展示**机械可算**的结构：包含处理后的分型、笔、中枢，以及机械判定的三买。
    并显式声明未实现的部分，避免把「识别出中枢」误当成买卖信号。
    """
    from stock_plan.factors.chan import chan_analysis

    bars = res.get("bars")
    if bars is None or bars.empty:
        return

    result = chan_analysis(bars)
    st.markdown("**🧩 缠论结构（辅助参考）**")
    if not result.strokes:
        st.caption("K 线不足或结构不完整，未形成有效的笔。")
        return

    cols = st.columns(4)
    cols[0].metric("笔", len(result.strokes))
    cols[1].metric("中枢", len(result.pivots))
    cols[2].metric("分型", len(result.fractals))
    cols[3].metric("三买", len(result.third_buys))

    if result.pivots:
        pv = result.pivots[-1]
        st.markdown(pv.describe())
        st.caption(
            f"价格在 [{pv.zd:.2f}, {pv.zg:.2f}] 这个区间里反复 → 中枢。"
            "站上上沿才算真正离开，跌回区间内则说明离开失败。"
        )
        last_close = float(bars["close"].iloc[-1])
        if last_close > pv.zg:
            st.caption(f"当前收盘 {last_close:.2f} 在中枢**上沿之上**。")
        elif last_close < pv.zd:
            st.caption(f"当前收盘 {last_close:.2f} 在中枢**下沿之下**。")
        else:
            st.caption(f"当前收盘 {last_close:.2f} **仍在中枢区间内**（方向未选择）。")

    if result.third_buys:
        tb = result.third_buys[-1]
        st.success(f"最近一个「三买」：{tb.describe()}")
    else:
        st.caption("近期没有出现三买（价格未在离开中枢后站稳于上沿之上）。")

    for line in result.explain[-1:]:
        st.caption(f"⚠️ {line}")


def _render_chart(res):
    """个股走势 + 买点 + 止损止盈 + 做T价位标注。"""
    render_chart(
        _price_level_fig(res),
        name="portfolio-price",
        caption="实线是收盘价；虚线是系统给的价位参考——绿色为目标买入价、红色为止损、"
                "琥珀色为止盈区，蓝/紫点线是做T的低吸与高抛位置。线只代表规则算出来的位置，不是预测。",
    )


def _price_level_fig(res):
    """走势图 + 价位参考线（纯函数，样张截图与页面同源）。"""
    lv = res.get("levels") or {}
    tt = res.get("t_trade") or {}
    return price_level_fig(
        res.get("bars"),
        buy_price=res.get("buy_price"),
        stop_loss=lv.get("stop_loss"),
        take_profit=lv.get("take_profit"),
        t_buy=tt.get("buy_low"),
        t_sell=tt.get("sell_high"),
    )
