"""模拟交易页面 — 账户快照、持仓列表、交易日志。

功能：
- 账户概览卡片（现金/市值/总资产/已实现盈亏）
- 当前持仓列表
- 交易日志
- 每日盘后更新按钮（用最新收盘价检查止盈止损）
- 重置账户按钮
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from stock_plan.data.storage import Storage
from stock_plan.simulator.paper import PaperTrader
from stock_plan.ui.widgets import page_glossary

PAPER_GLOSSARY = {
    "模拟交易（纸面交易）": "用「假钱」按真实规则买卖：T+1、手续费、印花税、滑点全模拟，先练手再谈实盘。",
    "T+1": "今天买的股票最早明天才能卖（A 股规则），防止当天来回折腾。",
    "滑点": "实际成交价比看到的价格差一点，就像网购标价之外还有运费。",
    "持仓成本": "买入价加上手续费等费用后，你的股票「实际」多少钱一股。",
}


def render():
    st.header("💼 模拟交易")
    st.caption("贴近实盘的纸面交易：T+1、手续费、印花税、滑点、涨跌停。")
    page_glossary(PAPER_GLOSSARY)

    trader = PaperTrader()
    status = trader.status()

    # 账户概览
    st.subheader("账户概览")
    cols = st.columns(4)
    cols[0].metric("总资产", f"{status['total_asset']:,.0f}")
    cols[1].metric("现金", f"{status['cash']:,.0f}")
    cols[2].metric("持仓市值", f"{status['market_value']:,.0f}")
    cols[3].metric("已实现盈亏", f"{trader.realized_pnl():,.0f}")

    # 当前持仓
    st.subheader("当前持仓")
    if trader.positions:
        rows = [
            {
                "代码": pos.code,
                "股数": pos.shares,
                "买入价": pos.entry_price,
                "买入日期": pos.entry_date.isoformat(),
                "止损价": pos.stop_loss,
                "目标价": pos.target_price,
                "持仓天数": (date.today() - pos.entry_date).days,
            }
            for pos in trader.positions.values()
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("当前无持仓。可在「今日信号」页生成信号后发送到模拟交易。")

    # 每日盘后更新
    st.subheader("每日盘后更新")
    st.caption("用最新收盘价检查止盈/止损/超时，触发卖出。")
    if st.button("🕒 执行盘后更新", type="primary"):
        storage = Storage()
        prices = {}
        for code in list(trader.positions.keys()):
            bars = storage.load_bars(code)
            if not bars.empty:
                prices[code] = float(bars.iloc[-1]["close"])
        if not prices:
            st.info("无持仓需要更新。")
        else:
            trader.on_bar(date.today(), prices)
            st.success("盘后更新完成，已检查止盈/止损。")
            st.rerun()

    # 交易日志
    st.subheader("交易日志")
    if trader.history:
        rows = [
            {
                "代码": t["code"],
                "方向": "买入" if t["action"] == "buy" else "卖出",
                "日期": t["date"],
                "价格": t["price"],
                "股数": t["shares"],
                "费用": t["fee"],
                "盈亏": t["pnl"] if t["pnl"] is not None else "",
                "原因": t["reason"],
            }
            for t in reversed(trader.history[-50:])
        ]
        st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.info("暂无交易记录。")

    # 重置账户
    st.subheader("重置账户")
    if st.button("🗑️ 清空模拟账户", type="secondary"):
        import sqlite3

        from stock_plan.simulator.paper import PAPER_DB

        conn = sqlite3.connect(PAPER_DB)
        for table in ("account", "positions", "trades"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
        conn.close()
        st.success("模拟账户已清空。")
        st.rerun()
