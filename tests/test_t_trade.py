"""做 T 底仓模型（`backtest/t_trade.py`）的行为测试。

这些测试用**合成日线**构造路径，因为它要验证的是交易规则本身
（触发/止损/周五平仓/T+1/整手），不是某种市场行情下的收益高低——
后者用真实数据跑，藏在 UI 和 `scripts/` 里。
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.backtest.t_trade import (
    TConfig,
    last_close_lookup,
    run_t_backtest,
)

# 2026-01-05 是周一，便于构造"周四买、周五强平"的场景
MONDAY = "2026-01-05"


def _bars(closes: list[float], start: str = MONDAY) -> pd.DataFrame:
    """按收盘价序列造一份最小日线（open/high/low 只用于满足列约定）。"""
    dates = pd.bdate_range(start, periods=len(closes))
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
            "amount": [10_000_000] * len(closes),
        }
    )


def _run(closes: list[float], cfg: TConfig | None = None, code: str = "600000"):
    bars = {"600000": _bars(closes)}
    return run_t_backtest(["600000"], bars, cfg or TConfig())


def test_正T_跌破阈值买入_反弹止盈卖出() -> None:
    """day2 跌 5% 触发买入，day3 反弹 3% 触及止盈 2% → 应有一笔止盈交易。"""
    res = _run([10.0, 10.0, 9.5, 9.8, 9.8])
    assert len(res.t_trades) == 1
    tr = res.t_trades.iloc[0]
    assert tr["exit_reason"] == "止盈"
    assert tr["pnl"] > 0
    assert tr["exit_date"] > tr["entry_date"]


def test_T加1_不可能同日买卖() -> None:
    """A 股 T+1：当日买入的 T 仓当日不能卖，所以每笔的平仓日必须严格晚于建仓日。"""
    # 连续下跌再暴涨：如果没有 T+1 限制，day2 买、day2 就可能被止盈卖掉
    res = _run([10.0, 10.0, 9.0, 10.5, 10.5])
    assert not res.t_trades.empty, "该路径应至少产生一笔 T 交易"
    assert (res.t_trades["exit_date"] > res.t_trades["entry_date"]).all()


def test_无底仓则不做T() -> None:
    """底仓比例设为 0 时没有可依托的底仓，T 逻辑必须完全不触发。"""
    res = _run([10.0, 10.0, 9.0, 9.5, 9.5], TConfig(base_pct=0.0))
    assert res.compare["base_code_count"] == 0
    assert res.t_trades.empty


def test_周五强制平仓不跨周() -> None:
    """周四买入、周五既不涨也不跌 → 应按"周五强平"卖出，而不是继续持有到下周。"""
    res = _run([10.0, 10.0, 10.0, 9.5, 9.5])
    assert len(res.t_trades) == 1
    tr = res.t_trades.iloc[0]
    assert tr["exit_reason"] == "周五强平"
    # 2026-01-08 是周四、01-09 是周五
    assert pd.Timestamp(tr["entry_date"]).weekday() == 3
    assert pd.Timestamp(tr["exit_date"]).weekday() == 4


def test_止损() -> None:
    """买入后继续跌 3% → 应按止损卖出，且亏损被费用放大。"""
    res = _run([10.0, 10.0, 9.5, 9.1, 9.1])
    assert len(res.t_trades) == 1
    tr = res.t_trades.iloc[0]
    assert tr["exit_reason"] == "止损"
    assert tr["pnl"] < 0


def test_触发阈值调高后不再交易() -> None:
    """把 entry_drop 调到 99% 就永远等不到买点 —— 证明该参数真的在控制行为。"""
    res = _run([10.0, 10.0, 9.0, 10.5, 10.5], TConfig(entry_drop=0.99))
    assert res.t_trades.empty


def test_对照曲线起点一致且已扣建仓成本() -> None:
    """"底仓不动"与"底仓+做T"必须从同一起点比，否则收益差是假的。"""
    res = _run([10.0, 10.0, 9.5, 9.8, 9.8])
    assert res.equity.iloc[0] == pytest.approx(res.base_only_equity.iloc[0])
    assert res.equity.iloc[0] < TConfig().initial_cash  # 建仓手续费已扣


def test_整手约束_买不起一手就不建仓() -> None:
    """茅台这种价位：1 手 100 股就要十几万，小资金等权分配必然买不起。"""
    res = _run([1500.0, 1500.0, 1400.0, 1450.0, 1450.0])
    assert res.compare["base_code_count"] == 0
    assert res.compare["skipped_code_count"] == 1


def test_估值取历史最近价不前视() -> None:
    """停牌日没有 bar 时，估值只能用**之前**的收盘价，不能用之后的。"""
    d1 = pd.Timestamp("2026-01-05")
    d3 = pd.Timestamp("2026-01-07")
    price = {"600000": {d1: 10.0, d3: 12.0}}
    assert last_close_lookup(price, "600000", pd.Timestamp("2026-01-06")) == 10.0
    assert last_close_lookup(price, "600000", d3) == 12.0
    assert last_close_lookup(price, "600000", pd.Timestamp("2026-01-01")) == 0.0


def test_交易成本为正且卖出高于买入() -> None:
    """同一笔 T 的卖出费用必须包含印花税，所以总费用 > 仅佣金。"""
    res = _run([10.0, 10.0, 9.5, 9.8, 9.8])
    tr = res.t_trades.iloc[0]
    buy_amount = tr["shares"] * tr["entry_price"]
    sell_amount = tr["shares"] * tr["exit_price"]
    assert tr["cost"] > buy_amount  # 买入含佣金
    assert tr["proceeds"] < sell_amount  # 卖出扣佣金+印花税
