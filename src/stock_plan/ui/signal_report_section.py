"""今日信号页的「为什么是这几只」区块 —— 对比表 + 分差瀑布图 + 硬过滤样本。

设计约束：
- **所有数字都来自 `signal/report.py` 的计算结果**，本模块只做排版。
  将来的大模型解读只读这份事实，不允许自己算分或编原因（防编造）。
- 纯展示函数拆成两层：`comparison_frame()` / `rejected_frame()` 可单独测试，
  `render_report_section()` 只负责把它们交给 Streamlit。
- 不依赖具体策略：报告里没有的东西（例如别的策略的私有因子）就不展示。

Revision History:
    2026-10-28  (phase 2) new module: render signal explainability section
    2026-10-28  (phase 3) add optional llm narrative block with cached result
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from stock_plan.signal.report import RejectedSample, SignalReport
from stock_plan.ui import chart_style as cs
from stock_plan.ui.widgets import render_chart, score_waterfall_fig

COMPARISON_COLUMNS = (
    "名次", "代码", "名称", "状态", "综合分", "扣分", "原始分",
    "偏离MA20", "RSI14", "量比", "趋势分", "基本面分", "扣分原因",
)


def _ratio_pct(value: float | None) -> str:
    """比例 → 带符号百分比文本（None → 破折号）。"""
    if value is None:
        return "—"
    return f"{value * 100:+.1f}%"


def _number(value: float | None, digits: int = 1) -> str:
    """普通数值 → 定长文本（None → 破折号）。"""
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def comparison_frame(report: SignalReport) -> pd.DataFrame:
    """把入选者 + 紧随者排成一张对比表（名次升序，入选者在先）。

    表格里的「扣分」是策略扣分明细的合计，「原始分」= 综合分 - 扣分，
    这样能看出「本来排第几、被哪条规则拉了下来」。
    """
    rows: list[dict] = []
    for item in list(report.picked) + list(report.runners_up):
        rows.append(
            {
                "名次": int(item.rank),
                "代码": item.code,
                "名称": item.name,
                "状态": "入选" if item.picked else "紧随未入选",
                "综合分": _number(item.score, 1),
                "扣分": _number(item.penalty_points, 0) if item.penalties else "—",
                "原始分": _number(item.base_score, 1),
                "偏离MA20": _ratio_pct(item.dev_ma20),
                "RSI14": _number(item.rsi14, 0),
                "量比": _number(item.vol_ratio, 2),
                "趋势分": _number(item.trend_score, 0),
                "基本面分": _number(item.fund_score, 0),
                "扣分原因": "；".join(penalty.explain() for penalty in item.penalties) or "—",
            }
        )
    return pd.DataFrame(rows, columns=list(COMPARISON_COLUMNS))


def _detail_text(detail: dict) -> str:
    """把硬过滤触发数值翻译成人话（未知键原样列出，不吞信息）。"""
    parts: list[str] = []
    if "is_st" in detail:
        parts.append("股票列表里标记为 ST/*ST")
    if "bars" in detail and "min_bars" in detail:
        parts.append(f"只有 {detail['bars']} 根日线（要求 ≥ {detail['min_bars']} 根）")
    if "last_volume" in detail:
        parts.append(f"最后一根成交量 {detail['last_volume']:g}")
    if "avg_amount20" in detail or "min_amount" in detail:
        avg_amount = detail.get("avg_amount20")
        min_amount = detail.get("min_amount")
        if avg_amount is None:
            parts.append("没有可用的成交额数据")
        elif min_amount is not None:
            parts.append(
                f"近 20 日均额 {avg_amount / 1e4:.0f} 万 < 门槛 {min_amount / 1e4:.0f} 万"
            )
        else:
            parts.append(f"近 20 日均额 {avg_amount / 1e4:.0f} 万")
    known = {"is_st", "bars", "min_bars", "last_volume", "avg_amount20", "min_amount"}
    for key, value in detail.items():
        if key not in known:
            parts.append(f"{key}={value}")
    return "；".join(parts) if parts else "—"


def rejected_frame(samples: tuple[RejectedSample, ...] | list[RejectedSample]) -> pd.DataFrame:
    """硬过滤淘汰样本表：代码 / 名称 / 原因 / 触发数值。"""
    rows = [
        {
            "代码": item.code,
            "名称": item.name,
            "原因": item.reason_text,
            "触发数值": _detail_text(dict(item.detail)),
        }
        for item in samples
    ]
    return pd.DataFrame(rows, columns=["代码", "名称", "原因", "触发数值"])


def _universe_caption(report: SignalReport, period_label: str) -> str:
    """区块顶部的一行事实：数据到哪天、筛掉了多少、策略是谁。"""
    universe = report.universe
    head = f"**{period_label}** · " if period_label else ""
    return (
        f"{head}数据截至 {universe.data_until or '—'}｜股票列表 {universe.total_in_list} 只"
        f"｜有日线 {universe.with_bars} 只｜通过硬过滤 {universe.passed} 只"
        f"｜策略 {universe.strategy_name}（指纹 {universe.strategy_fingerprint}）"
    )


def render_report_section(report: SignalReport | None, period_label: str = "") -> None:
    """渲染「为什么是这几只」区块（调用方负责放进 expander）。

    参数：
        report: SignalReport；None 表示本次没算（例如云端只读发布文件），静默跳过。
        period_label: 持仓周期文案，例如「中线」，用于区分多周期结果。
    """
    if report is None:
        st.caption("本次未生成对比解释（云端只读发布信号，不在本地重算）。")
        return
    if report.is_empty:
        st.caption("没有可对比的候选股：数据可能尚未更新，或全部被硬过滤。")
        for warning in report.warnings:
            st.caption(f"· {warning}")
        return

    st.caption(_universe_caption(report, period_label))

    st.markdown("**候选对比（入选者 + 紧随其后）**")
    st.dataframe(
        comparison_frame(report),
        hide_index=True,
        width="stretch",
        column_config={
            "扣分原因": st.column_config.TextColumn("扣分原因", width="large"),
        },
    )
    st.caption(
        "「原始分」= 综合分 - 扣分，代表没被规则拉下来时的排名实力；"
        "扣分来自策略里写死的规则（偏离均线过远、均线空头、RSI 过热等）。"
    )

    figure = score_waterfall_fig(report)
    if figure is not None:
        gate = report.picked[-1]
        render_chart(
            figure,
            name="signal-score-gap",
            caption=(
                f"虚线 = 入选门槛 {gate.score:.1f} 分（第 {gate.rank} 名 {gate.code} {gate.name}）；"
                "柱高 = 紧随者与门槛的差距，圆点标出各自综合分。"
                "绿色表示分数更低、因此排在门外。"
            ),
        )

    if report.entry_gap is not None:
        st.caption(f"**入选门槛**：{report.entry_gap.note()}")

    penalty_lines = [
        f"{item.code} {item.name}：合计 {item.penalty_points:+.0f} 分 → "
        + "；".join(penalty.explain() for penalty in item.penalties)
        for item in report.picked
        if item.penalties
    ]
    if penalty_lines:
        st.markdown("**入选者也不是满分：扣分项逐条列出**")
        for line in penalty_lines:
            st.caption(f"· {line}")

    if report.rejected_samples:
        st.markdown("**被硬过滤挡在门外的样本（每类原因各 1 只）**")
        st.dataframe(
            rejected_frame(report.rejected_samples),
            hide_index=True,
            width="stretch",
            column_config={
                "触发数值": st.column_config.TextColumn("触发数值", width="large"),
            },
        )
        counts = report.universe.rejected_counts
        if counts:
            detail = "、".join(
                f"{_reason_label(key)} {value} 只"
                for key, value in counts.items()
                if key != "passed" and value
            )
            if detail:
                st.caption(f"各类淘汰数量：{detail}。")

    for warning in report.warnings:
        st.caption(f"· {warning}")

    render_narrative_block(report, period_label)


def _narrative_key(report: SignalReport, period_label: str) -> str:
    """同一天、同一策略、同一周期 → 同一个会话键（换周期/换策略各自独立）。"""
    universe = report.universe
    return (
        f"signal-narrative::{period_label}::{universe.strategy_fingerprint}"
        f"::{universe.data_until}::{len(report.picked)}"
    )


def render_narrative_block(report: SignalReport | None, period_label: str = "") -> None:
    """大模型解读：默认不调用，点按钮才发请求，结果存 session_state 并落盘缓存。

    为什么不自动跑：一次调用要几秒、要花钱；用户先看事实层，觉得有必要再点。
    兜底（no key / 超时 / 编造代码 / 含数字）都在 `llm/signal_narrative.py` 里判定，
    这里只负责把 `source` 与 `error` 如实显示出来，不美化、不静默。
    """
    if report is None or report.is_empty:
        return

    from stock_plan.llm.signal_narrative import explain_signal_report

    st.markdown("**大模型解读（可选）**")
    key = _narrative_key(report, period_label)
    if st.button(
        "🤖 让大模型写一段解读",
        key=f"btn::{key}",
        help="数字全部由代码计算，模型只负责解释性文字；同一份数据只会真正调用一次。",
    ):
        with st.spinner("正在调用大模型…"):
            st.session_state[key] = explain_signal_report(report)

    result = st.session_state.get(key)
    if result is None:
        st.caption("不点按钮不会调用大模型；生成后会落盘缓存，同一份数据重复点击不再消耗额度。")
        return

    st.markdown(result.text)
    if result.source == "cache":
        st.caption(f"缓存于{result.age}，本次没有重复调用模型。")
    elif result.source == "llm":
        st.caption("大模型生成：所有数字来自代码计算，模型只负责解释文字。")
    else:
        st.caption(f"模板兜底：{result.error}")


def _reason_label(reason: str) -> str:
    """原因键 → 中文短标签（与 factors/filter.py 的 REASON_LABELS 同源）。"""
    from stock_plan.factors.filter import REASON_LABELS

    return REASON_LABELS.get(reason, reason)
