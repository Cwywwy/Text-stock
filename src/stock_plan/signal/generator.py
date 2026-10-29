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

`generate_signals_with_report()`：一次打分同时产出「信号 + 解释报告」。
今日信号页用它，避免为报告再跑一遍 40 多秒的加载与指标计算。
`generate_signals()` 保持原样不动——云端发布与既有回测口径都依赖它。

Revision History:
    2026-09-04  load only 180-day window to avoid cloud OOM
    2026-10-28  (phase 1) add generate_signal_report for explainability
    2026-10-28  (phase 2) share one scoring pass between signals and report
    2026-10-29  (phase 11) report carries degraded rules (missing columns no longer silent)
    2026-10-29  (phase 14) report scoring passes enabled=True to reuse penalty collection switch (values unchanged)
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from stock_plan.data.storage import Storage
from stock_plan.factors.filter import FilterOutcome, filter_universe_explained
from stock_plan.strategy.archetypes import strategy_degraded
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
    ranked = _rank_factor_rows(factor_rows)

    # 5. 生成信号
    return _signals_from_ranked(
        ranked,
        top_n=top_n,
        name_map=name_map,
        strategy=strategy,
    )


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


@dataclass(frozen=True)
class _RankedFacts:
    """一次打分产生的中间结果 —— 报告与信号共用，避免重复加载与重复计算。

    为什么单独抽出来：
    `load_market_maps()` 约 19 秒、`build_factor_rows()` 约 26 秒（真实 4130 只
    候选股实测），今日信号页若为了解释报告再跑一遍，冷启动会从 46 秒变成 92 秒。
    因此把「加载 → 硬过滤 → 因子行 → 打分 → 排名」做成可复用的中间层，
    报告与信号各自只做最后的装配。
    """

    strategy: Strategy
    name_map: dict[str, str]
    bars_map: dict[str, pd.DataFrame]
    outcome: FilterOutcome
    codes: list[str]
    ranked: pd.DataFrame
    penalties_map: dict[str, list[Penalty]]
    warnings: tuple[str, ...]
    universe: UniverseFacts
    aborted: bool
    industry_note: str = ""


def _collect_ranked(
    strategy: Strategy,
    *,
    storage: Storage | None,
    boards: list[str] | None,
    exclude_st: bool,
    industries: list[str] | None,
    industry_level: str,
) -> _RankedFacts:
    """加载数据、硬过滤、算因子、打分排名 —— 报告与信号共用的一次性开销。"""
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
    base_universe = UniverseFacts(
        total_in_list=len(stock_list),
        with_bars=len(bars_map),
        strategy_name=strategy_name,
        strategy_fingerprint=strategy_fingerprint(strategy),
    )
    empty_ranked = pd.DataFrame()

    if stock_list.empty:
        warnings.append("股票列表为空（数据尚未更新），无法生成报告。")
        return _RankedFacts(
            strategy=strategy,
            name_map={},
            bars_map=bars_map,
            outcome=FilterOutcome(passed=(), rejected=(), counts={}, total=0),
            codes=[],
            ranked=empty_ranked,
            penalties_map={},
            warnings=tuple(warnings),
            universe=base_universe,
            aborted=True,
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
        # 报告需要逐条扣分明细：内置策略显式 enabled=True（引擎打分默认关，省掉长表）。
        # 先看签名再决定是否传关键字，兼容签名里没有 enabled 的第三方策略；
        # 方法体内部抛 AttributeError 仍按旧语义降级为「只打分」，但 TypeError 等真实
        # 错误照常抛出（不能吞掉策略实现里的 bug）。
        try:
            scorer = strategy.score_with_penalties
        except AttributeError:
            scorer = None
        if scorer is None:
            warnings.append("该策略未提供扣分明细（score_with_penalties），报告不含扣分项。")
            scores = strategy.score(factor_rows)
        else:
            try:
                supports_enabled = "enabled" in inspect.signature(scorer).parameters
            except (TypeError, ValueError):
                supports_enabled = False
            try:
                if supports_enabled:
                    scores, penalties = scorer(factor_rows, enabled=True)
                else:
                    scores, penalties = scorer(factor_rows)
            except AttributeError:
                # 旧式策略常用「方法体内抛 AttributeError」表示未实现扣分明细
                warnings.append(
                    "该策略未提供扣分明细（score_with_penalties），报告不含扣分项。"
                )
                scores = strategy.score(factor_rows)
            else:
                penalties_map = _penalty_map(penalties)
        ranked = _rank_factor_rows(factor_rows.assign(score=scores))

    # 4. 数据截止日：取实际参与打分的股票里最后一根日线（如实反映"数据到哪天"）
    data_codes = [str(c) for c in ranked["code"].tolist()] if not ranked.empty else []
    used_dates = [d for d in (_last_bar_date(bars_map.get(c)) for c in data_codes) if d]
    data_until = max(used_dates) if used_dates else ""
    if not used_dates:
        warnings.append("日线数据缺失，无法确认数据截止日。")

    universe = UniverseFacts(
        total_in_list=base_universe.total_in_list,
        with_bars=base_universe.with_bars,
        passed=len(codes),
        rejected_counts=dict(outcome.counts),
        data_until=data_until,
        strategy_name=base_universe.strategy_name,
        strategy_fingerprint=base_universe.strategy_fingerprint,
    )
    industry_note = ""
    if industries:
        industry_note = (
            f"已按行业筛选（{'、'.join(industries)}）：通过硬过滤 {outcome.passed_count} 只，"
            f"可打分候选 {len(codes)} 只。"
        )
    return _RankedFacts(
        strategy=strategy,
        name_map=name_map,
        bars_map=bars_map,
        outcome=outcome,
        codes=codes,
        ranked=ranked,
        penalties_map=penalties_map,
        warnings=tuple(warnings),
        universe=universe,
        aborted=False,
        industry_note=industry_note,
    )


def _build_report(
    facts: _RankedFacts,
    *,
    top_n: int,
    runners_up: int,
    rejected_per_reason: int,
    strategy: Strategy | None = None,
) -> SignalReport:
    """把共用中间结果装配成 SignalReport。"""
    warnings = list(facts.warnings)
    degraded = strategy_degraded(strategy) if strategy is not None else ()
    if facts.aborted:
        return SignalReport(
            generated_at=datetime.now(),
            universe=facts.universe,
            warnings=tuple(warnings),
            degraded=degraded,
        )

    # 入选者 + 紧随者（按名次连续切片，不重复计算）
    picked: list[CandidateFacts] = []
    runners: list[CandidateFacts] = []
    if not facts.ranked.empty:
        limit = top_n + runners_up
        for position, row in facts.ranked.iterrows():
            rank = int(position) + 1
            if rank > limit:
                break
            picked_flag = rank <= top_n
            candidate = _candidate_facts(
                row,
                rank=rank,
                picked=picked_flag,
                name_map=facts.name_map,
                penalties_map=facts.penalties_map,
                bars_map=facts.bars_map,
            )
            (picked if picked_flag else runners).append(candidate)

    entry_gap = (
        EntryGap(picked[-1].score, runners[0].score) if picked and runners else None
    )
    if facts.industry_note:
        warnings.append(facts.industry_note)

    # 硬过滤淘汰样本（每类各 N 只，便于逐类解释）
    samples = tuple(
        RejectedSample(
            code=item.code,
            name=item.name,
            reason=item.reason,
            reason_text=item.reason_text,
            detail=dict(item.detail),
        )
        for item in facts.outcome.samples(per_reason=rejected_per_reason)
    )

    return SignalReport(
        generated_at=datetime.now(),
        universe=facts.universe,
        picked=tuple(picked),
        runners_up=tuple(runners),
        rejected_samples=samples,
        entry_gap=entry_gap,
        warnings=tuple(warnings),
        degraded=degraded,
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
    facts = _collect_ranked(
        strategy,
        storage=storage,
        boards=boards,
        exclude_st=exclude_st,
        industries=industries,
        industry_level=industry_level,
    )
    return _build_report(
        facts,
        top_n=top_n,
        runners_up=runners_up,
        rejected_per_reason=rejected_per_reason,
        strategy=strategy,
    )


def _signals_from_ranked(
    ranked: pd.DataFrame,
    *,
    top_n: int,
    name_map: dict[str, str],
    strategy: Strategy,
) -> list[Signal]:
    """把已排好名的因子行装配成 Signal 列表（与 generate_signals 第 5 步等价）。"""
    if ranked.empty:
        return []
    signals: list[Signal] = []
    for _, row in ranked.head(top_n).iterrows():
        atr = row["atr14"]
        entry = strategy.entry_price(row, atr)
        exit_price, stop_loss, hold_days = strategy.exit_price(entry, atr)
        signals.append(
            Signal(
                code=str(row["code"]),
                name=name_map.get(str(row["code"]), str(row["code"])),
                score=round(float(row["score"]), 1),
                entry_price=entry,
                exit_price=exit_price,
                stop_loss=stop_loss,
                hold_days=hold_days,
                reasons=_build_reasons(row),
            )
        )
    return signals


def generate_signals_with_report(
    strategy: Strategy | None = None,
    top_n: int = 5,
    runners_up: int = 3,
    rejected_per_reason: int = 1,
    storage: Storage | None = None,
    boards: list[str] | None = None,
    exclude_st: bool = True,
    industries: list[str] | None = None,
    industry_level: str = "sw_l1",
) -> tuple[list[Signal], SignalReport]:
    """一次打分同时产出「今日信号」与「信号解释报告」。

    参数与 `generate_signal_report()` 相同。之所以单独提供这个入口、而不是让页面
    分别调 `generate_signals()` + `generate_signal_report()`：两次调用会把
    「加载 5576 只 + 算 4130 只指标」这 40 多秒重复一遍，冷启动从 46 秒变 92 秒。
    这里只跑一次，信号与报告必然同源同名次（见同一批 `ranked` 行）。

    `generate_signals()` 本身保持原样不动 —— 云端发布路径与既有回测口径都依赖它，
    两条路径的一致性由 `tests/test_signal_report.py` 的逐项相等断言守住。
    """
    strategy = strategy or TrendFollowingStrategy()
    facts = _collect_ranked(
        strategy,
        storage=storage,
        boards=boards,
        exclude_st=exclude_st,
        industries=industries,
        industry_level=industry_level,
    )
    signals = _signals_from_ranked(
        facts.ranked,
        top_n=top_n,
        name_map=facts.name_map,
        strategy=strategy,
    )
    report = _build_report(
        facts,
        top_n=top_n,
        runners_up=runners_up,
        rejected_per_reason=rejected_per_reason,
        strategy=strategy,
    )
    return signals, report


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
