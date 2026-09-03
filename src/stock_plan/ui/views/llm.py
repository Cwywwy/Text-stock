"""LLM 智能分析页面 — 信号解释 / 消息面分析 / 策略生成。

未配置 API Key 时自动降级为离线规则模式（mock），UI 仍可正常使用。
"""
from __future__ import annotations

import streamlit as st

from stock_plan.llm.analyzer import analyze_news, explain_signal, generate_strategy
from stock_plan.llm.client import get_client


def render():
    st.header("🤖 LLM 智能分析")
    st.caption("用大模型解释选股信号、分析消息面、生成策略。未配置 API Key 时自动降级为离线规则模式。")

    client = get_client()
    if client.mock:
        st.info(
            "当前为**离线规则模式**：未检测到 LLM 配置。请在项目根 `.env` 填写 "
            "`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 后重启应用，启用真实 LLM 分析。"
        )
    else:
        st.success(f"🟢 {client.status_text}")

    tab1, tab2, tab3 = st.tabs(["信号解释", "消息面分析", "策略生成"])

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

    # ============ Tab 3: 策略生成 ============
    with tab3:
        st.subheader("用自然语言生成策略")
        desc = st.text_area(
            "描述你的投资偏好",
            placeholder="例如：我喜欢趋势向上的股票，回调到 20 日均线附近买入，"
                        "涨 10% 止盈，跌 5% 止损，最多持有 20 天。",
            height=120,
        )
        if st.button("✨ 生成策略"):
            if not desc.strip():
                st.warning("请先描述你的投资偏好")
                return
            with st.spinner("正在生成策略…"):
                text = generate_strategy(desc)
            st.markdown(text)