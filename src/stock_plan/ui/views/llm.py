"""LLM 智能分析页面 — 信号解释 / 消息面分析。

未配置 API Key 时自动降级为离线规则模式（mock），UI 仍可正常使用。

为什么这里不再有「策略生成」tab（2026-10-28 用户要求）：
    LLM 生成的产物是权重/规则/买卖参数，产物要在「策略拼装」页继续调参并回测。
    拆在两页时，用户在生成页拿到建议还得去拼装页手工把二三十个滑杆再调一遍，
    等于 AI 白算。现在生成区块内嵌在策略拼装页（`ui/strategy_ai.py`），可一键套用。

Revision History:
    2026-09-04  require public acknowledgement before shared strategy submission
    2026-10-28  「策略生成」tab 迁移到「策略拼装」页，本页只留解释类分析
"""
from __future__ import annotations

import streamlit as st

from stock_plan.llm.analyzer import analyze_news, explain_signal
from stock_plan.llm.client import get_client
from stock_plan.ui.widgets import page_glossary

LLM_GLOSSARY = {
    "LLM（大语言模型）": "像 ChatGPT 那样的 AI，能读懂新闻和信号并用自然语言给你解释。",
    "API Key": "调用 AI 服务的「钥匙」，在项目根 `.env` 里配置。没配也能用——系统会自动切换成离线规则模式。",
    "信号解释": "AI 用大白话说明「为什么选这只股票、买卖价怎么定」，帮助你理解而不是盲从。",
    "离线规则模式": "没配 AI 时的备用方案：用固定模板生成说明，不需要联网。",
}


def render():
    st.header("🤖 LLM 智能分析")
    st.caption(
        "用大模型解释选股信号、分析消息面。未配置 API Key 时自动降级为离线规则模式。"
        "（策略生成已移到「策略拼装」页顶部，可一键套用到表单后直接回测。）"
    )
    page_glossary(LLM_GLOSSARY)

    client = get_client()
    if client.mock:
        st.info(
            "当前为**离线规则模式**：未检测到 LLM 配置。请在项目根 `.env` 填写 "
            "`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 后重启应用，启用真实 LLM 分析。"
        )
    else:
        st.success(f"🟢 {client.status_text}")

    tab1, tab2 = st.tabs(["信号解释", "消息面分析"])

    # ============ Tab 1: 信号解释 ============
    with tab1:
        st.subheader("解释今日选股信号")
        signals = st.session_state.get("today_signals", [])
        if not signals:
            st.info("请先到「今日信号」页生成信号，再回到这里解释。")
        else:
            options = {f"{s.get('code')} {s.get('name', '')}": s for s in signals}
            sel = st.selectbox("选择信号", list(options.keys()))
            if st.button("💬 解释该信号", type="primary"):
                with st.spinner("正在分析…"):
                    text = explain_signal(options[sel])
                st.markdown(text)

    # ============ Tab 2: 消息面分析 ============
    with tab2:
        st.subheader("个股消息面分析")
        code = st.text_input("股票代码", placeholder="如 600519")
        if st.button("📰 分析消息面"):
            if not code.strip():
                st.warning("请输入股票代码")
                return
            with st.spinner("正在拉取并分析新闻…"):
                from stock_plan.data.fetcher import DataFetcher

                fetcher = DataFetcher()
                try:
                    news = fetcher.get_news(code.strip())
                except Exception as e:
                    st.error(f"新闻拉取失败：{e}")
                    return
                text = analyze_news(code.strip(), "", news or [])
            st.markdown(text)
