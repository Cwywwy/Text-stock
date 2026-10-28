"""新闻舆情时间线页面 — 个股公告 / 市场要闻 / 财经日历 / 分红停牌。

数据源：东方财富公告接口 + AKShare 新闻接口（详见 news/timeline.py）。
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from stock_plan.llm.analyzer import analyze_news
from stock_plan.news.timeline import (
    get_caixin_news,
    get_cctv_news,
    get_dividend_notices,
    get_economic_calendar,
    get_market_news,
    get_stock_announcements,
    get_suspend_notices,
    resolve_stock,
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

# 时间跨度选项（暂时需求 3）：公告接口只支持页码/条数，日期在本地过滤
_SPAN_DAYS = {
    "近 3 天": 3,
    "近 1 周": 7,
    "近 1 月": 30,
    "近 3 月": 90,
    "近 1 年": 365,
}


def _render_external_links(code: str, name: str | None) -> None:
    """到外部平台搜索该股新闻/舆情的快捷入口（暂时需求 3 的"联动浏览器"）。

    站内的公告接口只能覆盖公告，媒体报道、研报、讨论区需要外部平台，
    所以这里给出一排直达链接，点开即在新标签页搜索。
    """
    from urllib.parse import quote

    keyword = name or code
    links = [
        ("东方财富搜索", f"https://so.eastmoney.com/news/s?keyword={quote(keyword)}"),
        ("百度新闻", f"https://www.baidu.com/s?wd={quote(keyword + ' 新闻')}"),
        ("雪球讨论", f"https://xueqiu.com/k?q={quote(keyword)}"),
        ("巨潮公告原文", f"http://www.cninfo.com.cn/new/fulltextSearch?keyWord={quote(keyword)}"),
    ]
    cols = st.columns(len(links))
    for col, (label, url) in zip(cols, links):
        with col:
            st.markdown(f"[🔗 {label}]({url})")


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
    empty_text,
) -> None:
    """渲染一个「刷新 → 紧凑列表 → 详情子视图」的要闻区块。

    empty_text 可以是字符串，也可以是返回字符串的可调用对象（用于按标的/跨度
    生成更有用的空状态提示）。

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
        st.info(empty_text() if callable(empty_text) else empty_text)
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


def _stock_empty_hint(code: str, span_label: str, days: int) -> str:
    """某股在所选跨度内没有公告时的提示。

    实测：接口按日期严格降序返回，"没有公告"往往是**真的没有**（例如 600519 最新
    一条是 2026-08-15，因为半年报之后就没再发）。但用户看到 0 条会以为功能坏了，
    所以这里顺手取最新一条的日期告诉他，并提示把跨度调大。
    """
    try:
        latest = get_stock_announcements(code, page_size=1, days=None)
        if latest:
            d = str(latest[0].get("time", ""))[:10]
            if d:
                return (
                    f"{code} 在{span_label}内没有公告 —— **最新一条是 {d}**，"
                    f"可以把时间跨度调大再看看。"
                )
    except Exception:
        pass
    return f"{code} 在{span_label}内没有公告。"


def render():
    st.header("📰 新闻舆情时间线")
    st.caption("聚合个股公告、市场要闻、财经日历、分红送转与停复牌信息，辅助盘前决策。")
    page_glossary(NEWS_GLOSSARY)

    tab1, tab2, tab3, tab4 = st.tabs(["个股公告", "市场要闻", "财经日历", "分红/停牌"])

    # ============ Tab 1: 个股新闻与舆情（暂时需求 3）============
    with tab1:
        st.subheader("个股新闻与舆情")
        st.caption(
            "**代码或名称都能查**（如 `600519` 或 `贵州茅台`）。查到后可进详情读全文，"
            "也可以一键跳到外部平台，或交给 LLM 做舆情分析。"
        )

        col_q, col_span = st.columns([2, 1])
        with col_q:
            query = st.text_input(
                "股票代码或名称", placeholder="如 600519 或 贵州茅台", key="news_query_text"
            )
        with col_span:
            span_label = st.selectbox(
                "时间跨度", list(_SPAN_DAYS), index=3, key="news_span"
            )
        days = _SPAN_DAYS[span_label]

        code, name, candidates = resolve_stock(query)

        if candidates:
            # 名称命中多只时不猜，交给用户选
            labels = [f"{c}　{n}" for c, n in candidates]
            pick = st.selectbox(
                f"「{query}」匹配到 {len(candidates)} 只，请选择",
                labels,
                key="news_candidate",
            )
            code, name = candidates[labels.index(pick)]
        elif query.strip() and code is None:
            st.warning(
                f"没找到「{query}」对应的股票。可以用 6 位代码，或确认名称是否准确。"
            )

        if not code:
            st.caption("输入代码或名称后即可查询公告。")
            return

        st.markdown(
            f"当前标的：**{code}　{name or ''}** ｜ 时间跨度：**{span_label}**（{days} 天）"
        )
        _render_external_links(code, name)

        # 按"代码 + 跨度"分桶缓存，换标的/换跨度不会串数据
        items_key = f"news_stock_items_{code}_{days}"
        detail_key = f"news_stock_detail_{code}_{days}"
        # 接口单次上限 100 条，这里按跨度给一个合理的取回量
        page_size = min(100, max(30, days))

        _render_feed_section(
            items_key=items_key,
            detail_key=detail_key,
            fetcher=lambda: get_stock_announcements(code, page_size=page_size, days=days),
            button_label="🔎 查询该股公告",
            button_key="news_query",
            spinner_text="正在拉取公告…",
            empty_text=lambda: _stock_empty_hint(code, span_label, days),
        )

        items = st.session_state.get(items_key) or []
        if items:
            if st.button("🤖 让 LLM 分析该股舆情", key=f"{items_key}_llm"):
                with st.spinner("正在分析…"):
                    st.session_state[f"{items_key}_llm_text"] = analyze_news(
                        code, name or "", items
                    )
            llm_text = st.session_state.get(f"{items_key}_llm_text")
            if llm_text:
                st.markdown(llm_text)

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
                st.dataframe(rows, width="stretch", hide_index=True)

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
                st.dataframe(rows, width="stretch", hide_index=True)

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
                st.dataframe(rows, width="stretch", hide_index=True)