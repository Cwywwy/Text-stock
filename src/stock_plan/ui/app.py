# -*- coding: utf-8 -*-
"""盘前选股系统 — Streamlit 入口。

启动方式：
    uv run streamlit run src/stock_plan/ui/app.py

导航为平铺结构（便于后续功能模块化拆分）：
    今日信号 / 模拟交易 / 回测结果 / 策略对比 / 策略拼装 /
    策略管理 / 四大师研究 / LLM 智能分析 / 新闻舆情
"""
import streamlit as st

st.set_page_config(page_title="盘前选股系统", page_icon="📈", layout="wide")

from stock_plan.ui.views import (
    backtest,
    compare,
    four_masters,
    guide,
    llm,
    news,
    paper,
    strategy_mgr,
    today,
    visual_builder,
)

nav = st.navigation(
    [
        st.Page(today.render, title="今日信号", icon="📋", url_path="today", default=True),
        st.Page(paper.render, title="模拟交易", icon="💼", url_path="paper"),
        st.Page(backtest.render, title="回测结果", icon="📊", url_path="backtest"),
        st.Page(compare.render, title="策略对比", icon="⚖️", url_path="compare"),
        st.Page(visual_builder.render, title="策略拼装", icon="🧩", url_path="builder"),
        st.Page(strategy_mgr.render, title="策略管理", icon="⚙️", url_path="strategy"),
        st.Page(four_masters.render, title="四大师研究", icon="🏛️", url_path="masters"),
        st.Page(llm.render, title="LLM 智能分析", icon="🤖", url_path="llm"),
        st.Page(news.render, title="新闻舆情", icon="📰", url_path="news"),
        st.Page(guide.render, title="新手指南", icon="🎓", url_path="guide"),
    ]
)
nav.run()
