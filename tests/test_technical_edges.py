# -*- coding: utf-8 -*-
"""技术因子边界测试（2026-10-15 审计补充）。

重点锁死 RSI 的极值处理：只涨不跌的极限值是 100，不是中性的 50。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stock_plan.factors.technical import _rsi, compute_technical


def test_rsi_all_up_is_100_not_50() -> None:
    """窗口内只涨不跌 → RSI 极限值 100。

    原实现用 `fillna(50)` 兜底，把"连续涨停"这种最强动量填成中性的 50；
    而 trend_score 的 RSI 条件是「50 ≤ rsi ≤ 70（+20 分）」，这些股票反而白拿 20 分。
    """
    close = pd.Series([10.0 + i for i in range(30)])
    rsi = _rsi(close, 14)
    assert rsi.iloc[-1] == 100.0, f"只涨不跌应为 100，实际 {rsi.iloc[-1]}"


def test_rsi_all_down_is_0() -> None:
    """窗口内只跌不涨 → RSI 极限值 0（本来就正确，一并锁死）。"""
    close = pd.Series([40.0 - i for i in range(30)])
    rsi = _rsi(close, 14)
    assert rsi.iloc[-1] == 0.0


def test_rsi_warmup_is_neutral_50() -> None:
    """预热期（样本不足 14 天）仍用中性值 50，避免把 NaN 带到下游。"""
    close = pd.Series([10.0 + i for i in range(30)])
    rsi = _rsi(close, 14)
    assert (rsi.iloc[:13] == 50).all()


def test_rsi_flat_series_does_not_become_100() -> None:
    """完全横盘（无涨无跌）不应被判成 100：没有涨幅就不算超买。"""
    close = pd.Series([10.0] * 30)
    rsi = _rsi(close, 14)
    assert rsi.iloc[-1] == 50.0, f"横盘应为中性，实际 {rsi.iloc[-1]}"


def test_rsi_normal_case_in_range() -> None:
    """涨跌交替时应落在 0~100 之间且不含 NaN。"""
    rng = np.random.default_rng(0)
    close = pd.Series(10 + rng.normal(0, 0.3, 60).cumsum())
    rsi = _rsi(close, 14)
    assert rsi.notna().all()
    assert ((rsi >= 0) & (rsi <= 100)).all()


def test_trend_score_rewards_overbought_less_than_before() -> None:
    """修正后：只涨不跌的股票不再因"中性 RSI"白拿那 20 分。

    构造一段持续上涨的行情：均线多头 + MACD 正 + 收盘站上 ma20 都能拿到分，
    但 RSI=100（超买）不该再加分。
    """
    n = 90
    close = pd.Series(np.linspace(10, 20, n))
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2026-01-01", periods=n),
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": 1_000_000.0,
            "amount": 10_000_000.0, "turnover": 0.01,
        }
    )
    out = compute_technical(df)
    last = out.iloc[-1]
    assert last["rsi14"] == 100.0
    # 均线多头 40 + 站上 ma20 20 + MACD 20 = 80；RSI 超买不应加分
    assert last["trend_score"] == 80.0, f"实际 trend_score = {last['trend_score']}"
