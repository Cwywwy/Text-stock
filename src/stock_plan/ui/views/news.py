"""新闻舆情时间线页面 — 个股公告 / 市场要闻 / 财经日历 / 分红停牌。

数据源：东方财富公告接口 + AKShare 新闻接口（详见 news/timeline.py）。
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from stock_plan.news.timeline import (
    get_caixin_news,
    get_cctv_news,
    get_dividend_notices,
    get_economic_calendar,
    get_market_news,
    get_stock_announcements,
    get_suspend_notices,
)
from stock_plan.ui.widgets import page_glossary

NEWS_GLOSSARY = {
    "舆情": "市场上大家在讨论、传播的消息面情绪，利好消息多偏暖，利空集中偏冷。",
    "公告": "上市公司官方发布的信息（业绩、股东变动、重大合同等），权威但需要自己解读。",
    "财经日历": "近期重要事件的时间表（如美联储议息、经济数据发布），大事件前后市场波动往往加大。",
    "停牌": "股票暂时停止交易，可能是重大事项或风险警示。停牌期间买不进也卖不出。",
}


def render():
    st.header("📰 新闻舆情时间线")
    st.caption("聚合个股公告、市场要闻、财经日历、分红送转与停复牌信息，辅助盘前决策。")
    page_glossary(NEWS_GLOSSARY)

    tab1, tab2, tab3, tab4 = st.tabs(["个股公告", "市场要闻", "财经日历", "分红/停牌"])

    # ============ Tab 1: 个股公告 ============
    with tab1:
        st.subheader("个股公告时间线")
        code = st.text_input("股票代码", placeholder="如 600519", key="news_code")
        if st.button("📄 查询公告", key="news_query"):
            if not code.strip():
                st.warning("请输入股票代码")
                return
            with st.spinner("正在拉取公告…"):
                items = get_stock_announcements(code.strip())
            if not items:
                st.info("未获取到公告数据。")
            else:
                st.success(f"共 {len(items)} 条公告")
                for it in items:
                    with st.expander(f"[{it['time']}] {it['title']}"):
                        st.caption(f"来源：{it['source']} ｜ 分类：{it['content'] or '—'}")
                        if it.get("url"):
                            st.markdown(f"[查看原文](https://data.eastmoney.com/notices/detail/{code.strip()}.html)")

    # ============ Tab 2: 市场要闻 ============
    with tab2:
        st.subheader("全球市场要闻")
        if st.button("🌍 刷新市场要闻", key="market_news"):
            with st.spinner("正在拉取市场要闻…"):
                items = get_market_news()
            if not items:
                st.info("未获取到市场要闻。")
            else:
                st.success(f"共 {len(items)} 条要闻")
                for it in items:
                    with st.expander(f"[{it['time']}] {it['title']}"):
                        st.write(it["summary"])
                        if it.get("url"):
                            st.markdown(f"[查看原文]({it['url']})")

        st.divider()
        st.subheader("财新要闻")
        if st.button("📰 刷新财新要闻", key="caixin_news"):
            with st.spinner("正在拉取财新要闻…"):
                items = get_caixin_news()
            if not items:
                st.info("未获取到财新要闻。")
            else:
                st.success(f"共 {len(items)} 条要闻")
                for it in items:
                    with st.expander(f"[{it['tag']}] {it['summary'][:80]}"):
                        st.write(it["summary"])
                        if it.get("url"):
                            st.markdown(f"[查看原文]({it['url']})")

        st.divider()
        st.subheader("CCTV 新闻联播")
        cctv_date = st.date_input("日期", date.today(), key="cctv_date")
        if st.button("📺 查询新闻联播", key="cctv_news"):
            with st.spinner("正在拉取新闻联播…"):
                items = get_cctv_news(cctv_date)
            if not items:
                st.info("该日期无新闻联播数据。")
            else:
                st.success(f"共 {len(items)} 条内容")
                for it in items:
                    with st.expander(it["title"]):
                        st.write(it["content"])

    # ============ Tab 3: 财经日历 ============
    with tab3:
        st.subheader("财经日历")
        cal_date = st.date_input("日期", date.today(), key="cal_date")
        if st.button("📅 查询财经日历", key="cal_query"):
            with st.spinner("正在拉取财经日历…"):
                items = get_economic_calendar(cal_date)
            if not items:
                st.info("该日期无财经日历数据。")
            else:
                st.success(f"共 {len(items)} 条事件")
                rows = [
                    {
                        "时间": it["time"],
                        "地区": it["region"],
                        "事件": it["event"],
                        "重要性": "★" * int(it["importance"] or 0),
                        "公布": it["actual"],
                        "预期": it["forecast"],
                        "前值": it["previous"],
                    }
                    for it in items
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)

    # ============ Tab 4: 分红/停牌 ============
    with tab4:
        st.subheader("分红送转")
        div_date = st.date_input("日期", date.today(), key="div_date")
        if st.button("💰 查询分红送转", key="div_query"):
            with st.spinner("正在拉取分红送转…"):
                items = get_dividend_notices(div_date)
            if not items:
                st.info("该日期无分红送转数据。")
            else:
                st.success(f"共 {len(items)} 条")
                rows = [
                    {
                        "代码": it["code"],
                        "名称": it["name"],
                        "除权日": it["ex_date"],
                        "分红": it["dividend"],
                        "送股": it["bonus"],
                        "转增": it["transfer"],
                        "交易所": it["exchange"],
                    }
                    for it in items
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("停复牌")
        sus_date = st.date_input("日期", date.today(), key="sus_date")
        if st.button("⏸️ 查询停复牌", key="sus_query"):
            with st.spinner("正在拉取停复牌…"):
                items = get_suspend_notices(sus_date)
            if not items:
                st.info("该日期无停复牌数据。")
            else:
                st.success(f"共 {len(items)} 条")
                rows = [
                    {
                        "代码": it["code"],
                        "名称": it["name"],
                        "停牌时间": it["suspend_time"],
                        "复牌时间": it["resume_time"],
                        "事项": it["reason"],
                    }
                    for it in items
                ]
                st.dataframe(rows, use_container_width=True, hide_index=True)