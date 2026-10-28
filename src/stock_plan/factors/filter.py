"""硬过滤模块 — 选股前的必要排除条件。

作用：在打分之前，先把明显不合格的股票剔除，避免浪费计算资源，
也避免把垃圾股选进 Top 5。

排除条件（满足任一即剔除）：
1. ST / *ST 股票（名称含 ST）
2. 停牌股票（最新交易日成交量为 0 或没有日线数据）
3. 流动性差的股票（近 20 个交易日平均成交额 < 5000 万元）
4. 上市时间过短的次新股（日线数据不足 60 根，无法计算 ma60）

⚠️ **ST 过滤是"今天的"状态，用于回测时构成未来函数**（2026-10-15 审计发现）
------------------------------------------------------------------
本模块用 `stock_list["is_st"]` 判断 ST，而回测里的 `stock_list` 来自
`Storage.load_stock_list()`，也就是 `SELECT * FROM stock_list` —— **只有今天的状态**。
引擎（`backtest/engine.py`）在开跑前一次性调用本函数建立候选池，于是：

    用 2026 年的 ST 状态，决定了 2022 年能不能买。

股票被戴帽 ST 往往正是因为前期跌得多，所以这是一种**前瞻性偏差**。
目前**没有修正**，因为缺少 ST 的时点历史（`stock_list` 只有当前状态，
baostock 也不提供）；要修必须新增数据源。

另外要小心：**不要用单次 A/B 的收益差来量化这个偏差**。实测同一策略、同一区间，
仅把 201 只今天的 ST 股放回候选池（池子 +3.75%），总收益就差 +20.97pp，
但成交股票的 Jaccard 重合度只有 **0.226** —— 差异主要来自选股被大面积重排
（路径依赖），而不是 ST 股本身的业绩（它们只成交 8 笔）。详见
`.github/plans/2026-10-15-4-audit-path-dependence-and-lookahead.md`。

排查用入口：
- `filter_universe()`     只要「通过的代码」，行为与引入本函数前逐位一致
- `filter_universe_explained()` 同时返回「谁被淘汰、为什么、相关数值」，
  供「为什么是这 5 只」报告解释落选股票（.github/plans/2026-10-28-2 的 Phase 1）

修订记录：
2026-10-28 新增 RejectedStock / FilterOutcome / filter_universe_explained，
           原 filter_universe 改为薄封装（数值与顺序不变）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# 流动性阈值：近 20 日均成交额低于该值（元）则剔除
MIN_AVG_AMOUNT = 50_000_000  # 5000 万元
# 最少日线根数（保证 ma60 等长周期指标可计算）
MIN_BARS = 60

#: 淘汰原因 → 人类可读文案。报告层与页面共用这一份，避免两边文案漂移。
REASON_LABELS: dict[str, str] = {
    "st": "ST/*ST 风险警示（今天的状态，非历史时点）",
    "no_bars": "无日线缓存（行情未更新）",
    "empty_bars": "日线数据为空（数据异常）",
    "new_listing": "次新股（日线不足 60 根，无法计算 ma60）",
    "suspended": "停牌（最新交易日成交量为 0）",
    "illiquid": "流动性不足（20 日均成交额低于 5000 万）",
}


@dataclass(frozen=True)
class RejectedStock:
    """一只被硬过滤淘汰的股票（含原因与触发数值）。"""

    code: str
    name: str
    reason: str  # 见 REASON_LABELS 的键
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def reason_text(self) -> str:
        """原因的中文描述。"""
        return REASON_LABELS.get(self.reason, self.reason)


@dataclass(frozen=True)
class FilterOutcome:
    """硬过滤结果：通过列表 + 淘汰清单 + 各原因计数。"""

    passed: tuple[str, ...]
    rejected: tuple[RejectedStock, ...]
    counts: dict[str, int]
    total: int

    @property
    def passed_count(self) -> int:
        return len(self.passed)

    def samples(self, per_reason: int = 1) -> list[RejectedStock]:
        """每类原因取前 ``per_reason`` 只（保持原始顺序），用于报告展示。"""
        picked: list[RejectedStock] = []
        seen: dict[str, int] = {}
        for item in self.rejected:
            used = seen.get(item.reason, 0)
            if used >= per_reason:
                continue
            seen[item.reason] = used + 1
            picked.append(item)
        return picked

    def count_of(self, reason: str) -> int:
        return self.counts.get(reason, 0)


def filter_universe_explained(
    stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
) -> FilterOutcome:
    """硬过滤，同时记录「谁被淘汰、为什么」。

    与 `filter_universe()` 使用**完全相同的判据与顺序**，只是不再丢弃信息。
    `passed` 的成员与顺序都和旧实现一致（先遍历 bars_map 的键），
    这样下游 `build_factor_rows` 的行顺序不变，打分出现并列时的排序也不变。

    参数：
        stock_list: 全 A 股列表（含 code/name/is_st 列）。
        bars_map:   {code: 日线 DataFrame}，只包含有缓存的股票。

    返回：
        FilterOutcome（通过代码、淘汰清单、按原因计数、总样本数）。
    """
    codes = [str(c) for c in stock_list["code"].tolist()] if "code" in stock_list.columns else []
    names = (
        dict(zip(stock_list["code"].astype(str), stock_list["name"].astype(str)))
        if {"code", "name"}.issubset(stock_list.columns)
        else {}
    )
    st_codes = (
        set(stock_list.loc[stock_list["is_st"] == 1, "code"].astype(str))
        if "is_st" in stock_list.columns
        else set()
    )

    passed: list[str] = []
    rejected: list[RejectedStock] = []
    counts: dict[str, int] = {key: 0 for key in REASON_LABELS}

    def _reject(code: str, reason: str, **detail: Any) -> None:
        counts[reason] = counts.get(reason, 0) + 1
        rejected.append(
            RejectedStock(code=code, name=names.get(code, code), reason=reason, detail=detail)
        )

    # 第 1 轮：遍历有日线缓存的股票（顺序 = bars_map 的键顺序，与旧实现一致）
    for code in bars_map:
        code = str(code)
        if code in st_codes:
            _reject(code, "st", is_st=1)
            continue
        bars = bars_map[code]
        if bars is None or bars.empty:
            _reject(code, "empty_bars")
            continue
        bar_count = len(bars)
        if bar_count < MIN_BARS:
            _reject(code, "new_listing", bars=bar_count, min_bars=MIN_BARS)
            continue
        last_volume = float(bars["volume"].iloc[-1]) if "volume" in bars.columns else 0.0
        if last_volume == 0:
            _reject(code, "suspended", last_volume=last_volume)
            continue
        avg_amount = bars["amount"].tail(20).mean() if "amount" in bars.columns else float("nan")
        if pd.isna(avg_amount) or avg_amount < MIN_AVG_AMOUNT:
            _reject(
                code,
                "illiquid",
                avg_amount20=None if pd.isna(avg_amount) else round(float(avg_amount), 2),
                min_amount=MIN_AVG_AMOUNT,
            )
            continue
        passed.append(code)

    # 第 2 轮：股票列表里有、但没有日线缓存的（旧实现里它们只是"消失"，原因无从解释）
    covered = set(bars_map)
    for code in codes:
        if code in covered:
            continue
        if code in st_codes:
            _reject(code, "st", is_st=1)
        else:
            _reject(code, "no_bars")

    counts["passed"] = len(passed)
    return FilterOutcome(
        passed=tuple(passed),
        rejected=tuple(rejected),
        counts=counts,
        total=len(set(codes) | set(str(c) for c in bars_map)),
    )


def filter_universe(
    stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
) -> list[str]:
    """硬过滤，返回通过筛选的股票代码列表。

    ⚠️ 第 1 步用的是 `stock_list["is_st"]`，即**今天**的 ST 状态。
    用于回测时会构成未来函数，详见模块文档。

    参数：
        stock_list: 全 A 股列表（含 code/name/is_st 列）。
        bars_map:   {code: 日线 DataFrame}，只包含有缓存的股票。

    返回：
        通过所有过滤条件的股票代码列表（list[str]）。
    """
    return list(filter_universe_explained(stock_list, bars_map).passed)


if __name__ == "__main__":
    # 简单自测：用已缓存数据跑一遍硬过滤
    from stock_plan.data.storage import Storage

    storage = Storage()
    stock_list = storage.load_stock_list()
    bars_map = {}
    for code in stock_list["code"].astype(str).tolist():
        if storage.cache_exists(code):
            bars_map[code] = storage.load_bars(code)

    print(f"股票总数: {len(stock_list)}, 有日线缓存: {len(bars_map)}")
    outcome = filter_universe_explained(stock_list, bars_map)
    print(f"通过硬过滤: {outcome.passed_count} 只")
    print("淘汰统计:", {k: v for k, v in outcome.counts.items() if v})
    print("示例:", outcome.passed[:10])
    for item in outcome.samples(per_reason=1):
        print(f"  淘汰样本: {item.code} {item.name} | {item.reason_text} | {item.detail}")
