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

# 列表默认只显示最近 N 条：原实现每条要闻都是一个 expander，展开长文会把下方模块
# （尤其是 CCTV 的日期选择器与按钮）顶出屏幕。改为「紧凑列表 + 详情子视图」后，
# 列表高度可控，下面的控件不会再被挤走（暂时需求 2）。
_NEWS_PREVIEW_COUNT = 8


def _headline(it: dict) -> str:
    """列表里显示的一行标题（各数据源字段不同：市场要闻有 title，财新只有 summary）。"""
    return str(it.get("title") or it.get("summary") or "（无标题）")


def _body(it: dict) -> str:
    """详情视图里显示的正文。"""
    return str(it.get("content") or it.get("summary") or "")


def _meta(it: dict) -> str:
    """一行元信息（时间/标签/来源/日期），缺字段自动跳过。"""
    parts = [it.get(k) for k in ("time", "date", "tag", "source")]
    return " ｜ ".join(str(p) for p in parts if p)


def _render_feed_detail(items: list[dict], idx: int, detail_key: str) -> None:
    """详情子视图：只显示一条要闻的完整内容，带返回按钮。"""
    it = items[idx]
    if st.button("← 返回列表", key=f"{detail_key}_back"):
        st.session_state[detail_key] = None
        st.rerun()
    st.markdown(f"#### {_headline(it)}")
    meta = _meta(it)
    if meta:
        st.caption(meta)
    st.markdown(_body(it) or "_该来源未提供正文，请点下方链接查看原文。_")
    if it.get("url"):
        st.markdown(f"[🔗 查看原文]({it['url']})")


def _render_feed_section(
    items_key: str,
    detail_key: str,
    fetcher,
    button_label: str,
    button_key: str,
    spinner_text: str,
    empty_text: str,
) -> None:
    """渲染一个「刷新 → 紧凑列表 → 详情子视图」的要闻区块。

    注意：抓到的条目必须存进 session_state。因为「查看全文」是按钮会触发 rerun，
    若条目只存在局部变量里，进入详情视图时就会丢失（原实现用 expander 就地展开，
    是纯前端行为不触发 rerun，才侥幸没暴露这个问题）。
    """
    if st.button(button_label, key=button_key):
        with st.spinner(spinner_text):
            st.session_state[items_key] = fetcher()

    items = st.session_state.get(items_key)
    if items is None:
        st.caption("点击上方按钮拉取最新内容。")
        return
    if not items:
        st.info(empty_text)
        return

    idx = st.session_state.get(detail_key)
    if isinstance(idx, int) and 0 <= idx < len(items):
        _render_feed_detail(items, idx, detail_key)
        return

    total = len(items)
    if total > _NEWS_PREVIEW_COUNT:
        show_all = st.checkbox(
            f"显示全部 {total} 条（默认只显示最近 {_NEWS_PREVIEW_COUNT} 条）",
            key=f"{items_key}_all",
        )
        visible = items if show_all else items[:_NEWS_PREVIEW_COUNT]
    else:
        visible = items

    st.success(f"共 {total} 条，当前显示 {len(visible)} 条")
    for i, it in enumerate(visible):
        col_text, col_btn = st.columns([6, 1])
        with col_text:
            meta = _meta(it)
            st.markdown((f"`{meta}` " if meta else "") + _headline(it))
        with col_btn:
            if st.button("查看全文", key=f"{items_key}_open_{i}"):
                st.session_state[detail_key] = i
                st.rerun()


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
        _render_feed_section(
            items_key="news_market_items",
            detail_key="news_market_detail",
            fetcher=get_market_news,
            button_label="🌍 刷新市场要闻",
            button_key="market_news",
            spinner_text="正在拉取市场要闻…",
            empty_text="未获取到市场要闻。",
        )

        st.divider()
        st.subheader("财新要闻")
        _render_feed_section(
            items_key="news_caixin_items",
            detail_key="news_caixin_detail",
            fetcher=get_caixin_news,
            button_label="📰 刷新财新要闻",
            button_key="caixin_news",
            spinner_text="正在拉取财新要闻…",
            empty_text="未获取到财新要闻。",
        )

        st.divider()
        st.subheader("CCTV 新闻联播")
        cctv_date = st.date_input("日期", date.today(), key="cctv_date")
        _render_feed_section(
            items_key="news_cctv_items",
            detail_key="news_cctv_detail",
            fetcher=lambda: get_cctv_news(cctv_date),
            button_label="📺 查询新闻联播",
            button_key="cctv_news",
            spinner_text="正在拉取新闻联播…",
            empty_text="该日期无新闻联播数据。",
        )

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