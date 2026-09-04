"""信号生成模块 — 每日盘前调用，输出 Top 5 买入信号。

流程：
1. 加载全 A 股列表 + 日线缓存 + 财务缓存
2. 策略硬过滤（剔除 ST/停牌/流动性差/次新）
3. 构建因子行（技术分 + 基本面分）
4. 策略打分，取 Top 5
5. 为每只股票生成 Signal（含目标买入/卖出价、止损、持仓天数、入选理由）

Revision History:
    2026-09-04  load only 180-day window to avoid cloud OOM
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from stock_plan.data.storage import Storage
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


def generate_signals(
    strategy: Strategy | None = None,
    top_n: int = 5,
    storage: Storage | None = None,
    boards: list[str] | None = None,
    exclude_st: bool = True,
) -> list[Signal]:
    """生成盘前信号。

    参数：
        strategy:   策略实例，默认用内置趋势策略。
        top_n:      返回的信号数量（默认 5）。
        storage:    数据存储实例（便于测试注入）。
        boards:     保留的板块列表（R5 板块自定义筛选）；None/全选 = 不过滤。
        exclude_st: 是否剔除 ST/*ST 股票（默认 True）。

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

    # 3. 构建因子行
    factor_rows = build_factor_rows(codes, bars_map, fund_map)
    if factor_rows.empty:
        return []

    # 4. 打分并取 Top N
    factor_rows["score"] = strategy.score(factor_rows)
    top = factor_rows.sort_values("score", ascending=False).head(top_n)

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