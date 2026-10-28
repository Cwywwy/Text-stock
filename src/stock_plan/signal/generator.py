"""信号生成模块 — 每日盘前调用，输出 Top 5 买入信号。

流程：
1. 加载全 A 股列表 + 日线缓存 + 财务缓存
2. 策略硬过滤（剔除 ST/停牌/流动性差/次新）
3. 构建因子行（技术分 + 基本面分）
4. 策略打分，取 Top 5
5. 为每只股票生成 Signal（含目标买入/卖出价、止损、持仓天数、入选理由）

另有 `generate_signal_report()`：与上面的管道同源，但额外记录
「入选者为什么入选、紧随其后 3 只差在哪、硬过滤淘汰了谁」，
供「信号解释报告」页面与 LLM 解读使用（见 signal/report.py）。

Revision History:
    2026-09-04  load only 180-day window to avoid cloud OOM
    2026-10-28  (phase 1) add generate_signal_report for explainability
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from stock_plan.data.storage import Storage
from stock_plan.factors.filter import filter_universe_explained
from stock_plan.signal.report import (
    CandidateFacts,
    EntryGap,
    Penalty,
    RejectedSample,
    SignalReport,
    UniverseFacts,
    strategy_fingerprint,
)
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.builtin import TrendFollowingStrategy, build_factor_rows


@dataclass
class Signal:
    """一条盘前买入信号。"""

    code: str          # 股票代码
    name: str          # 股票名称
    score: float       # 综合分（0-100）
    entry_price: float  # 目标买入价
    exit_price: float   # 目标卖出价
    stop_loss: float    # 止损价
    hold_days: int      # 期望持仓天数
    reasons: list[str] = field(default_factory=list)  # 入选理由（自然语言）


def _build_reasons(row: pd.Series) -> list[str]:
    """根据因子数据生成入选理由（自然语言，供 UI 展示）。"""
    reasons = []
    if row.get("trend_score", 0) >= 80:
        reasons.append("趋势强（均线多头/MACD金叉）")
    elif row.get("trend_score", 0) >= 60:
        reasons.append("趋势偏强")
    if row.get("vol_ratio", 0) and row["vol_ratio"] > 1.5:
        reasons.append(f"放量（量比{row['vol_ratio']:.2f}）")
    if row.get("rsi14", 0) and 50 <= row["rsi14"] <= 70:
        reasons.append(f"RSI强势（{row['rsi14']:.0f}）")
    if row.get("fund_score", 0) >= 60:
        reasons.append("基本面良好")
    if not reasons:
        reasons.append("综合评分靠前")
    return reasons


def _rank_factor_rows(factor_rows: pd.DataFrame) -> pd.DataFrame:
    """按分数降序排名，**并列时按代码升序**。

    并列若交给 `sort_values` 的默认快速排序，顺序取决于实现细节；
    今日信号页与解释报告各自排一次就可能出现「同样的分数、不同的人入选」。
    固定次键（代码升序）后两条路径永远一致，也便于回归测试。
    """
    ordered = factor_rows.sort_values(
        ["score", "code"], ascending=[False, True], kind="stable"
    )
    return ordered.reset_index(drop=True)


def generate_signals(
    strategy: Strategy | None = None,
    top_n: int = 5,
    storage: Storage | None = None,
    boards: list[str] | None = None,
    exclude_st: bool = True,
    industries: list[str] | None = None,
    industry_level: str = "sw_l1",
) -> list[Signal]:
    """生成盘前信号。

    参数：
        strategy:   策略实例，默认用内置趋势策略。
        top_n:      返回的信号数量（默认 5）。
        storage:    数据存储实例（便于测试注入）。
        boards:     保留的板块列表（R5 板块自定义筛选）；None/全选 = 不过滤。
        exclude_st: 是否剔除 ST/*ST 股票（默认 True）。
        industries: 保留的行业名列表（暂时需求 3「通过行业板块指定策略」）；
                    None/空 = 不过滤。**默认不传时行为与改动前完全一致**。
        industry_level: 用哪一级行业名匹配，默认申万一级。

    返回：
        按 score 降序的 Signal 列表。
    """
    strategy = strategy or TrendFollowingStrategy()
    storage = storage or Storage()

    # 1. 加载数据（仅最近 180 个日历日窗口，指标预热足够且内存可控；云端限制股票数量）
    from datetime import timedelta

    from stock_plan.data.snapshot import CLOUD_MAX_CODES, is_cloud

    end = date.today()
    start = end - timedelta(days=180)
    stock_list, bars_map, fund_map = storage.load_market_maps(
        start=start, end=end,
        boards=boards, exclude_st=exclude_st,
        max_codes=CLOUD_MAX_CODES if is_cloud() else None,
    )
    if stock_list.empty:
        return []
    name_map = dict(zip(stock_list["code"].astype(str), stock_list["name"]))

    # 2. 硬过滤
    codes = strategy.filter_universe(stock_list, bars_map)

    # 2b. 行业筛选（暂时需求 3）：在打分之前剔除行业外的代码，
    #     这样 top_n 仍然能选满 N 只（若改成"先选后筛"，信号会莫名其妙变少）。
    #     分类数据缺失时 filter_codes_by_industries 会原样返回，不会把结果变成 0 只。
    if industries:
        from stock_plan.data import classification

        codes = classification.filter_codes_by_industries(codes, industries, level=industry_level)
        if not codes:
            return []

    # 3. 构建因子行
    factor_rows = build_factor_rows(codes, bars_map, fund_map)
    if factor_rows.empty:
        return []

    # 4. 打分并取 Top N（并列按代码升序，见 _rank_factor_rows）
    factor_rows["score"] = strategy.score(factor_rows)
    top = _rank_factor_rows(factor_rows).head(top_n)

    # 5. 生成信号
    signals: list[Signal] = []
    for _, row in top.iterrows():
        atr = row["atr14"]
        entry = strategy.entry_price(row, atr)
        exit_price, stop_loss, hold_days = strategy.exit_price(entry, atr)
        signals.append(
            Signal(
                code=row["code"],
                name=name_map.get(row["code"], row["code"]),
                score=round(row["score"], 1),
                entry_price=entry,
                exit_price=exit_price,
                stop_loss=stop_loss,
                hold_days=hold_days,
                reasons=_build_reasons(row),
            )
        )
    return signals


def _row_value(row: pd.Series, key: str) -> float | None:
    """从因子行安全取一个浮点数（缺失 / NaN → None）。"""
    if key not in row.index:
        return None
    value = row[key]
    if value is None or pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _last_bar_date(bars: pd.DataFrame | None) -> str:
    """该股最后一根日线的日期字符串；无数据返回空串。"""
    if bars is None or bars.empty:
        return ""
    try:
        values = bars["date"] if "date" in bars.columns else bars.index
        latest = pd.to_datetime(values).max()
    except (ValueError, TypeError):
        return ""
    return "" if pd.isna(latest) else latest.strftime("%Y-%m-%d")


def _penalty_map(penalties: pd.DataFrame | None) -> dict[str, list[Penalty]]:
    """把扣分明细长表按股票代码分组，供 CandidateFacts 使用。"""
    mapping: dict[str, list[Penalty]] = {}
    if penalties is None or penalties.empty:
        return mapping
    for _, item in penalties.iterrows():
        basis = item.get("basis")
        mapping.setdefault(str(item["code"]), []).append(
            Penalty(
                rule=str(item["rule"]),
                label=str(item["label"]),
                points=float(item["points"]),
                basis=None if basis is None or pd.isna(basis) else float(basis),
            )
        )
    return mapping


def _candidate_facts(
    row: pd.Series,
    *,
    rank: int,
    picked: bool,
    name_map: dict[str, str],
    penalties_map: dict[str, list[Penalty]],
    bars_map: dict[str, pd.DataFrame],
) -> CandidateFacts:
    """把一行因子数据转成 CandidateFacts（顺带算好偏离 ma20）。"""
    code = str(row["code"])
    close = _row_value(row, "close")
    ma20 = _row_value(row, "ma20")
    dev = (close / ma20 - 1) if close is not None and ma20 not in (None, 0.0) else None
    return CandidateFacts(
        code=code,
        name=name_map.get(code, code),
        score=float(row["score"]),
        rank=rank,
        picked=picked,
        close=close,
        ma20=ma20,
        ma60=_row_value(row, "ma60"),
        dev_ma20=dev,
        rsi14=_row_value(row, "rsi14"),
        vol_ratio=_row_value(row, "vol_ratio"),
        trend_score=_row_value(row, "trend_score"),
        fund_score=_row_value(row, "fund_score"),
        bar_date=_last_bar_date(bars_map.get(code)),
        penalties=tuple(penalties_map.get(code, ())),
    )


def generate_signal_report(
    strategy: Strategy | None = None,
    top_n: int = 5,
    runners_up: int = 3,
    rejected_per_reason: int = 1,
    storage: Storage | None = None,
    boards: list[str] | None = None,
    exclude_st: bool = True,
    industries: list[str] | None = None,
    industry_level: str = "sw_l1",
) -> SignalReport:
    """生成信号解释报告：入选者为什么入选、紧随者差在哪、硬过滤淘汰了谁。

    与 `generate_signals()` 使用**同一批数据、同一个硬过滤、同一套打分**，
    所以报告里出现的分数与今日信号页完全一致（差异只有"多留下了落选者信息"）。
    本函数是独立管道，不改动 `generate_signals()` 的行为（避免影响既有回测口径）。

    参数：
        strategy:            策略实例，默认内置趋势策略。
        top_n:               入选只数（默认 5，与信号页一致）。
        runners_up:          紧随其后、未入选的只数（默认 3）。
        rejected_per_reason: 每类硬过滤原因各取几只样本（默认 1）。
        storage:             数据存储（便于测试注入假 Storage）。
        boards:              保留的板块列表；None/全选 = 不过滤。
        exclude_st:          是否剔除 ST/*ST（默认 True）。
        industries:          保留的行业名列表；None/空 = 不过滤。
        industry_level:      行业名匹配级别，默认申万一级。

    返回：
        SignalReport（`to_dict()` 给页面/缓存，`to_facts_text()` 给大模型）。
        数据不可用时返回空报告，并在 `warnings` 里说明原因，不抛异常。
    """
    strategy = strategy or TrendFollowingStrategy()
    storage = storage or Storage()
    strategy_name = str(getattr(strategy, "name", type(strategy).__name__))

    # 1. 加载数据（窗口与 generate_signals 一致：最近 180 个日历日）
    from datetime import timedelta

    from stock_plan.data.snapshot import CLOUD_MAX_CODES, is_cloud

    end = date.today()
    start = end - timedelta(days=180)
    stock_list, bars_map, fund_map = storage.load_market_maps(
        start=start, end=end,
        boards=boards, exclude_st=exclude_st,
        max_codes=CLOUD_MAX_CODES if is_cloud() else None,
    )

    warnings: list[str] = [
        "硬过滤使用的 ST 判据是「今天」的 ST 状态，不是历史时点。",
    ]
    universe = UniverseFacts(
        total_in_list=len(stock_list),
        with_bars=len(bars_map),
        strategy_name=strategy_name,
        strategy_fingerprint=strategy_fingerprint(strategy),
    )
    if stock_list.empty:
        warnings.append("股票列表为空（数据尚未更新），无法生成报告。")
        return SignalReport(
            generated_at=datetime.now(),
            universe=universe,
            warnings=tuple(warnings),
        )

    # 2. 硬过滤（保留淘汰原因）
    name_map = dict(zip(stock_list["code"].astype(str), stock_list["name"].astype(str)))
    outcome = filter_universe_explained(stock_list, bars_map)
    codes = list(outcome.passed)

    # 2b. 行业筛选（与 generate_signals 同序：先筛行业再打分）
    if industries:
        from stock_plan.data import classification

        codes = classification.filter_codes_by_industries(codes, industries, level=industry_level)
        if not codes:
            warnings.append("行业筛选后没有候选股。")

    # 3. 构建因子行 + 打分
    factor_rows = build_factor_rows(codes, bars_map, fund_map) if codes else pd.DataFrame()
    penalties_map: dict[str, list[Penalty]] = {}
    if factor_rows.empty:
        warnings.append("没有股票能算出因子（日线不足或指标计算失败）。")
        ranked = factor_rows
    else:
        try:
            scores, penalties = strategy.score_with_penalties(factor_rows)
            penalties_map = _penalty_map(penalties)
        except AttributeError:
            warnings.append("该策略未提供扣分明细（score_with_penalties），报告不含扣分项。")
            scores = strategy.score(factor_rows)
        ranked = _rank_factor_rows(factor_rows.assign(score=scores))

    # 4. 入选者 + 紧随者（按名次连续切片，不重复计算）
    picked: list[CandidateFacts] = []
    runners: list[CandidateFacts] = []
    if not ranked.empty:
        limit = top_n + runners_up
        for position, row in ranked.iterrows():
            rank = int(position) + 1
            if rank > limit:
                break
            picked_flag = rank <= top_n
            facts = _candidate_facts(
                row,
                rank=rank,
                picked=picked_flag,
                name_map=name_map,
                penalties_map=penalties_map,
                bars_map=bars_map,
            )
            (picked if picked_flag else runners).append(facts)

    entry_gap = (
        EntryGap(picked[-1].score, runners[0].score) if picked and runners else None
    )

    # 5. 数据截止日：取实际参与打分的股票里最后一根日线（如实反映"数据到哪天"）
    data_codes = [str(c) for c in ranked["code"].tolist()] if not ranked.empty else []
    used_dates = [d for d in (_last_bar_date(bars_map.get(c)) for c in data_codes) if d]
    if used_dates:
        universe = UniverseFacts(
            total_in_list=universe.total_in_list,
            with_bars=universe.with_bars,
            passed=len(codes),
            rejected_counts=dict(outcome.counts),
            data_until=max(used_dates),
            strategy_name=universe.strategy_name,
            strategy_fingerprint=universe.strategy_fingerprint,
        )
    else:
        warnings.append("日线数据缺失，无法确认数据截止日。")
        universe = UniverseFacts(
            total_in_list=universe.total_in_list,
            with_bars=universe.with_bars,
            passed=len(codes),
            rejected_counts=dict(outcome.counts),
            strategy_name=universe.strategy_name,
            strategy_fingerprint=universe.strategy_fingerprint,
        )
    if industries:
        warnings.append(
            f"已按行业筛选（{'、'.join(industries)}）：通过硬过滤 {outcome.passed_count} 只，"
            f"可打分候选 {len(codes)} 只。"
        )

    # 6. 硬过滤淘汰样本（每类各 N 只，便于逐类解释）
    samples = tuple(
        RejectedSample(
            code=item.code,
            name=item.name,
            reason=item.reason,
            reason_text=item.reason_text,
            detail=dict(item.detail),
        )
        for item in outcome.samples(per_reason=rejected_per_reason)
    )

    return SignalReport(
        generated_at=datetime.now(),
        universe=universe,
        picked=tuple(picked),
        runners_up=tuple(runners),
        rejected_samples=samples,
        entry_gap=entry_gap,
        warnings=tuple(warnings),
    )


if __name__ == "__main__":
    # 简单自测：生成今日信号
    signals = generate_signals()
    print(f"生成 {len(signals)} 条信号：\n")
    for s in signals:
        print(
            f"{s.code} {s.name} | 综合分 {s.score} | "
            f"买入 {s.entry_price} 卖出 {s.exit_price} 止损 {s.stop_loss} "
            f"持仓 {s.hold_days}天"
        )
        print(f"  理由: {'; '.join(s.reasons)}")