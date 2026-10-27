"""财务因子"失效"缺陷的回归测试（2026-10-28）。

背景：`fundamental_composite` 读的是 **value_score/growth_score/quality_score**
三个算好的分数键，而 `Storage.load_fundamentals` 返回的是 roe/revenue_growth/...
原始指标。历史上两条路径（今日信号 `build_factor_rows`、回测 `engine`）都把原始
dict 直接交给 `fundamental_composite`，读不到分数键就回退 50 —— 实测 578 只股票
fund_score **全部等于 50.0（唯一取值）**，所谓"技术 0.6 + 基本面 0.4"的基本面权重
实际不起作用。修复方式是统一走 `compute_fundamental_or_neutral` 做换算。

这些测试守三件事：
1. 原始指标要真的换成有区分度的分数（否则又回到恒 50）；
2. 没有数据时是中性 50，而不是"缺数据"的 30（30 分是利空，50 分才是无信息）；
3. 已经是分数 dict 的调用方要原样透传，不能被重新算成 50。
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.factors.fundamental import (
    NEUTRAL_FUNDAMENTAL,
    compute_fundamental_or_neutral,
)
from stock_plan.strategy.builtin import build_factor_rows
from stock_plan.strategy.scorer import fundamental_composite

GOOD = {
    "roe": 25.0,
    "revenue_growth": 45.0,
    "net_profit_growth": 60.0,
    "gross_margin": 65.0,
    "debt_ratio": 15.0,
}
BAD = {
    "roe": 1.0,
    "revenue_growth": -30.0,
    "net_profit_growth": -45.0,
    "gross_margin": 5.0,
    "debt_ratio": 78.0,
}


def _bars(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=len(closes))
    prev = closes[0]
    rows = []
    for d, c in zip(dates, closes):
        rows.append(
            {
                "date": d,
                "open": round(prev, 2),
                "high": round(max(c, prev) * 1.002, 2),
                "low": round(min(c, prev) * 0.998, 2),
                "close": round(float(c), 2),
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
                "turnover": 0.01,
            }
        )
        prev = c
    return pd.DataFrame(rows)


def test_原始财务指标换算成有区分度的分数():
    """好公司显著高于坏公司。坏版本（直接喂原始 dict）两者都是 50.0。"""
    good = fundamental_composite(compute_fundamental_or_neutral(GOOD))
    bad = fundamental_composite(compute_fundamental_or_neutral(BAD))
    assert good - bad > 15, f"好/坏公司财务分几乎没差别：{good} vs {bad}"


def test_无数据是中性50而非利空30():
    """空 dict / None 必须给 50。若误用 compute_fundamental({})，会得到 30 分。"""
    for empty in (None, {}):
        scores = compute_fundamental_or_neutral(empty)
        assert scores == NEUTRAL_FUNDAMENTAL
        assert fundamental_composite(scores) == pytest.approx(50.0)


def test_已算好的分数dict原样透传():
    """调用方若直接给分数 dict，不能被重新按缺失指标算成 50。"""
    pre = {"value_score": 90.0, "growth_score": 95.0, "quality_score": 85.0}
    assert compute_fundamental_or_neutral(pre) == pre
    assert fundamental_composite(compute_fundamental_or_neutral(pre)) == pytest.approx(90.0)


def test_今日信号路径财务分不再恒定():
    """`build_factor_rows` 是今日信号（选股入口）的打分函数。"""
    bars = {"000001": _bars([10.0] * 130), "000002": _bars([10.0] * 130)}
    fund_map = {"000001": GOOD, "000002": BAD}
    df = build_factor_rows(["000001", "000002"], bars, fund_map)
    scores = dict(zip(df["code"], df["fund_score"].astype(float)))
    assert scores["000001"] - scores["000002"] > 15
    assert scores["000001"] != pytest.approx(50.0), "财务分仍是中性值，说明换算没生效"
    # 三档细分分列也必须是真的算过的值，供价值/大师类策略使用
    assert df.loc[df["code"] == "000001", "quality_score"].iloc[0] > 70


def test_缺财务数据的股票按中性50打分():
    bars = {"000001": _bars([10.0] * 130)}
    df = build_factor_rows(["000001"], bars, {})
    assert df["fund_score"].iloc[0] == pytest.approx(50.0)
