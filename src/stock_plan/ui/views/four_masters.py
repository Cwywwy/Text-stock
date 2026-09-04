# -*- coding: utf-8 -*-
"""四大师价值投资研究页 — 巴菲特/芒格/段永平/李录多视角分析。

独立于 LLM 智能分析页（该页主定位为"模糊想法→策略实现"）。
长线价值投资框架，与盘前短线选股定位不同。
"""
from __future__ import annotations

import streamlit as st

from stock_plan.llm.analyzer import four_masters_analysis
from stock_plan.llm.client import get_client
from stock_plan.ui.widgets import page_glossary

MASTERS_GLOSSARY = {
    "四大师": "巴菲特（好生意+护城河）、芒格（多元思维）、段永平（生意本质+不懂不做）、李录（长期确定性）四位价值投资大师。",
    "护城河": "别人抢不走的竞争优势，如品牌、牌照、网络效应。像古城护城河一样挡住竞争者。",
    "价值投资": "买「价格低于内在价值」的好公司，长期持有赚公司成长的钱，而不是猜价格涨跌。",
    "否决项": "一票否决的硬标准。比如财报造假嫌疑、看不懂的生意——分数再高也不买。",
}


def render():
    st.header("🏛️ 四大师价值投资研究")
    st.caption(
        "从巴菲特（护城河）、芒格（多元思维）、段永平（生意本质）、李录（长期确定性）"
        "四个视角对个股做对抗式基本面分析。"
    )
    page_glossary(MASTERS_GLOSSARY)

    st.warning(
        "⚠️ 定位说明：本页是**长线价值投资研究框架**，与「今日信号」等盘前短线选股模块的定位不同，"
        "结论不直接用于短线买卖，仅供研究参考，不构成投资建议。"
    )

    # LLM 连接状态
    client = get_client()
    if client.mock:
        st.info(
            "当前为**离线规则模式**：未检测到 LLM 配置。请在项目根 `.env` 填写 "
            "`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 后重启应用，获得完整的多视角分析。"
        )
    else:
        st.success(f"🟢 {client.status_text}")

    code = st.text_input("股票代码", placeholder="如 600519")
    if st.button("🔍 四大师分析", type="primary"):
        if not code.strip():
            st.warning("请输入股票代码")
            return
        code = code.strip()

        # 加载基本面数据
        stock = {"code": code}
        try:
            from stock_plan.data.storage import Storage

            storage = Storage()
            fin = storage.load_fundamentals(code)
            if fin is None:
                st.warning("本地无该股财务数据，尝试在线拉取…")
                try:
                    from stock_plan.data.fetcher import DataFetcher

                    fin = DataFetcher().get_fundamentals(code)
                    if fin:
                        storage.save_fundamentals(code, fin)
                except Exception as e:
                    st.info(f"在线拉取失败（{e}），将使用本地可用数据继续分析。")
            if fin:
                from stock_plan.factors.fundamental import compute_fundamental

                scores = compute_fundamental(fin)
                stock.update(fin)
                stock.update(scores)

                # 基本面分数速览
                st.subheader("基本面分数")
                c1, c2, c3 = st.columns(3)
                c1.metric("估值分", scores.get("value_score"))
                c2.metric("成长分", scores.get("growth_score"))
                c3.metric("质量分", scores.get("quality_score"))
            else:
                st.info("未获取到财务数据，四大师分析将基于有限信息进行。")
        except Exception as e:
            st.error(f"数据加载失败：{e}")

        # 四大师分析
        with st.spinner("四位大师正在审视这只股票…"):
            text = four_masters_analysis(stock, client)
        st.markdown(text)
