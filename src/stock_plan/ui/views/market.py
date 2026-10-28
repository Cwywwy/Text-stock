"""市场情绪 / 板块资金 / 公告存档页面（暂时需求续·工作流①③）。

展示内容：
- 市场情绪：涨停/跌停/炸板、连板结构、个股广度（来自按日快照，不现抓）
- 板块与资金：行业/概念资金流排行、申万一级行业里的涨停家数
- 分类快照：申万（一级/二级）、证监会 84 类、同花顺概念 375 个的覆盖状况
- 公告存档：持仓 + 自选股的公告，增量入库供以后做周频统计

为什么页面默认只读、抓取要点按钮：页面每次渲染都抓网会让 Streamlit 卡住，
也会把上游限流当成本地故障。抓取单独放按钮 + 日频任务（scripts/daily_job.ps1）。

Revision History:
    2026-10-27  新建：接入 data/market_snapshot.py + factors/emotion.py + data/announcements.py
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from stock_plan.data import announcements, classification, market_snapshot as ms
from stock_plan.factors import emotion
from stock_plan.ui.widgets import page_glossary

MARKET_GLOSSARY = {
    "涨停家数": "当天涨幅到顶、买不进去的股票数量。越多说明情绪越热，但过热往往是短线顶。",
    "炸板率": "盘中封住涨停、收盘又被打开的股票占比。越高说明接力资金越不坚决。",
    "连板": "连续多天涨停。二板、三板属于短线资金最活跃的位置。",
    "资金净流入": "该行业/概念当天大单买入减卖出的金额。注意行业与个股口径不同，不能相加。",
    "公告存档": "把持仓和自选股的公告存进本地库，方便以后按周统计「谁在密集发公告」。",
}


def _safe(fn, *args, **kwargs):
    """调用取数函数并吞掉异常：页面不能因为某天没数据就整页崩掉。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        st.warning(f"取数失败：{type(exc).__name__}: {exc}")
        return None


def _yi(x: float) -> str:
    """元 → 亿元字符串；NaN 显示为 —。"""
    try:
        if pd.isna(x):
            return "—"
        return f"{float(x) / 1e8:+.2f} 亿"
    except (TypeError, ValueError):
        return "—"


def _pct(x: float) -> str:
    """0~1 的比例 → 百分数字符串。"""
    try:
        if pd.isna(x):
            return "—"
        return f"{float(x) * 100:.1f}%"
    except (TypeError, ValueError):
        return "—"


def _render_emotion(day: str | None) -> None:
    st.markdown("**市场情绪总览**")
    stats = _safe(emotion.limit_stats, day)
    if stats:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("涨停", stats.get("limit_up", "—"))
        c2.metric("跌停", stats.get("limit_down", "—"))
        c3.metric("炸板率", _pct(stats.get("break_rate")))
        c4.metric("最高连板", f"{stats.get('max_boards', 0)} 板")

    breadth = _safe(emotion.flow_breadth, day)
    if breadth:
        c1, c2, c3 = st.columns(3)
        c1.metric("上涨家数占比", _pct(breadth.get("up_share")))
        c2.metric("资金净流入为正占比", _pct(breadth.get("inflow_share")))
        c3.metric("样本股票数", breadth.get("n_stocks", 0))

    text = _safe(emotion.emotion_text, day)
    if text:
        st.markdown("**一句话解读**")
        st.info(text)

    st.markdown("**涨停梯队**")
    frames = _safe(ms.load_day, day) or {}
    zt = frames.get(ms.LIMIT_UP)
    if zt is not None and not zt.empty:
        cols = [c for c in ("code", "name", "industry", "boards", "seal_fund", "break_count") if c in zt.columns]
        show = zt[cols].sort_values("boards", ascending=False).head(20).copy()
        if "seal_fund" in show.columns:
            show["seal_fund"] = show["seal_fund"].map(_yi)
        st.dataframe(show, width="stretch", hide_index=True)
        st.caption(f"当天涨停 {len(zt)} 只（表格最多显示 20 行，按连板数排序）。")
    else:
        st.caption("当天没有涨停池快照。")


def _render_boards(day: str | None) -> None:
    st.markdown("**行业 / 概念资金流**")
    ranks = _safe(emotion.fund_flow_rank, day, 5) or {}
    c1, c2 = st.columns(2)
    for col, key, label in ((c1, "industry_top", "行业净流入前 5"), (c2, "concept_top", "概念净流入前 5")):
        with col:
            df = ranks.get(key)
            if df is not None and not df.empty:
                show = df.rename(columns={"board": "板块", "pct": "涨跌幅(%)", "net": "净流入", "n_stocks": "家数"})
                if "净流入" in show.columns:
                    show["净流入"] = show["净流入"].map(_yi)
                st.caption(label)
                st.dataframe(show, width="stretch", hide_index=True)
            else:
                st.caption(f"没有{label[0:2]}数据。")

    st.markdown("**申万一级行业里的涨停家数**")
    res = _safe(emotion.limit_by_sw_l1, day)
    if res is not None:
        df, src = res
        if df is not None and not df.empty:
            st.dataframe(df.rename(columns={"sw_l1": "行业", "limit_up": "涨停家数"}),
                         width="stretch", hide_index=True)
        st.caption(f"口径：{src}")


def _render_classification() -> None:
    st.markdown("**分类快照状况**")
    months = classification.available_months()
    if not months:
        st.info(
            "还没有行业/概念分类快照。到「数据更新」页抓取，"
            "或运行 `python -m stock_plan.data.classification --all`。"
        )
        return
    latest = months[-1]
    st.caption(f"已积累 {len(months)} 个月快照，最新：{latest}")
    table = classification.industry_table(latest)
    if table.empty:
        st.warning("最新快照是空的。")
        return

    def _filled(col: str) -> int:
        if col not in table.columns:
            return 0
        return int((table[col].astype(str).str.strip() != "").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("股票数", len(table))
    c2.metric("申万一级覆盖", _filled("sw_l1"))
    c3.metric("证监会行业覆盖", _filled("csr_industry"))
    c4.metric("申万三级覆盖", _filled("sw_l3"))
    if _filled("sw_l3") == 0:
        st.warning(
            "申万**三级**为 0：主数据源（乐咕乐股）当晚整站 504，已降级到申万官网接口，"
            "而官网只提供一级/二级名录。等主源恢复后重跑 `--industry` 即可补齐三级；"
            "不影响其它功能（行业筛选用的是申万一级）。"
        )


def _render_announcements() -> None:
    st.markdown("**公告存档**")
    st.caption(
        "把持仓/自选股的公告增量存进本地库（按 代码+日期+标题 去重）。"
        "抓取要点按钮：页面渲染时不联网。"
    )
    tracked = announcements.tracked_codes()
    stats = _safe(announcements.stats) or {}
    c1, c2, c3 = st.columns(3)
    c1.metric("追踪代码数", len(tracked))
    c2.metric("已存公告", stats.get("rows", 0))
    c3.metric("覆盖代码", stats.get("codes", 0))

    a1, a2 = st.columns(2)
    with a1:
        code = st.text_input("手动加入追踪（例如 600000）", max_chars=6)
    with a2:
        days = st.number_input("抓取最近几天", 1, 90, 7)

    b1, b2 = st.columns(2)
    with b1:
        if st.button("加入追踪", key="ann_add") and code.strip():
            try:
                st.success(announcements.add_watch(code.strip()))
            except Exception as exc:  # noqa: BLE001
                st.error(f"加入失败：{exc}")
    with b2:
        if st.button("抓取最新公告", type="primary", key="ann_fetch"):
            with st.spinner("正在抓取…"):
                try:
                    res = announcements.capture(days=int(days))
                except Exception as exc:  # noqa: BLE001
                    st.error(f"抓取失败：{type(exc).__name__}: {exc}")
                    res = None
            if res:
                st.success(
                    f"完成：{res.get('codes', 0)} 只代码、抓到 {res.get('rows', 0)} 条、"
                    f"新增 {res.get('added', 0)} 条。"
                )
                if res.get("errors"):
                    st.caption("失败的：" + "；".join(res["errors"][:3]))

    df = _safe(announcements.load, None, None, 100)
    if df is not None and not df.empty:
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.caption("本地还没有公告。点上面的按钮抓取一次。")


def render() -> None:
    """渲染市场情绪 / 板块 / 公告页面。"""
    st.subheader("🌡️ 市场情绪 · 板块资金 · 公告存档")
    page_glossary(MARKET_GLOSSARY)

    days = ms.available_days()
    if days:
        day = st.selectbox("交易日（快照）", list(reversed(days)))
    else:
        day = None
        st.info(
            "还没有日频市场快照。到「数据更新」页抓取，"
            "或运行 `python -m stock_plan.data.market_snapshot`。"
        )

    tab1, tab2, tab3 = st.tabs(["市场情绪", "板块与资金", "公告存档"])
    with tab1:
        _render_emotion(day)
        _render_classification()
    with tab2:
        _render_boards(day)
    with tab3:
        _render_announcements()
