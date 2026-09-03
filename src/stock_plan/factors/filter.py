"""硬过滤模块 — 选股前的必要排除条件。

作用：在打分之前，先把明显不合格的股票剔除，避免浪费计算资源，
也避免把垃圾股选进 Top 5。

排除条件（满足任一即剔除）：
1. ST / *ST 股票（名称含 ST）
2. 停牌股票（最新交易日成交量为 0 或没有日线数据）
3. 流动性差的股票（近 20 个交易日平均成交额 < 5000 万元）
4. 上市时间过短的次新股（日线数据不足 60 根，无法计算 ma60）
"""
from __future__ import annotations

import pandas as pd

# 流动性阈值：近 20 日均成交额低于该值（元）则剔除
MIN_AVG_AMOUNT = 50_000_000  # 5000 万元
# 最少日线根数（保证 ma60 等长周期指标可计算）
MIN_BARS = 60


def filter_universe(
    stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
) -> list[str]:
    """硬过滤，返回通过筛选的股票代码列表。

    参数：
        stock_list: 全 A 股列表（含 code/name/is_st 列）。
        bars_map:   {code: 日线 DataFrame}，只包含有缓存的股票。

    返回：
        通过所有过滤条件的股票代码列表（list[str]）。
    """
    # 1. 排除 ST 股票
    st_codes = set(stock_list.loc[stock_list["is_st"] == 1, "code"].astype(str))
    # 2. 排除没有日线缓存的股票
    candidates = [c for c in bars_map if c not in st_codes]

    passed: list[str] = []
    for code in candidates:
        bars = bars_map[code]
        if bars is None or bars.empty:
            continue
        # 3. 排除日线不足的次新股
        if len(bars) < MIN_BARS:
            continue
        # 4. 排除停牌（最新交易日成交量为 0）
        if bars["volume"].iloc[-1] == 0:
            continue
        # 5. 排除流动性差（近 20 日均成交额 < 阈值）
        avg_amount = bars["amount"].tail(20).mean()
        if pd.isna(avg_amount) or avg_amount < MIN_AVG_AMOUNT:
            continue
        passed.append(code)

    return passed


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
    passed = filter_universe(stock_list, bars_map)
    print(f"通过硬过滤: {len(passed)} 只")
    print("示例:", passed[:10])