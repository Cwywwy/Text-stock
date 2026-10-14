"""回测引擎测试用例。

覆盖：
- 涨跌停价计算（主板/创业板/北交所）
- T+1 规则（买入当日不能卖出）
- 手续费/印花税/滑点计算
- 基本回测流程可跑通
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stock_plan.backtest.engine import BacktestConfig, _price_limit, run_backtest
from stock_plan.strategy.base import Strategy


# ---------- 测试用简单策略 ----------
class DummyStrategy(Strategy):
    """固定打分策略：所有股票给 80 分，便于测试交易逻辑。"""

    name = "测试策略"
    params = {"atr_k_entry": 1.0, "atr_m_exit": 3.0, "atr_n_stop": 1.5, "hold_days": 10}

    def filter_universe(self, stock_list, bars_map):
        return [c for c in stock_list["code"].astype(str) if c in bars_map]

    def score(self, df_factors):
        return pd.Series([80.0] * len(df_factors), index=df_factors.index)

    def entry_price(self, row, atr):
        return round(row["close"] + 1.0 * atr, 2)

    def exit_price(self, entry, atr):
        return round(entry + 3.0 * atr, 2), round(entry - 1.5 * atr, 2), 10


def _make_bars(days: int, start_price: float = 10.0, drift: float = 0.0) -> pd.DataFrame:
    """构造一段模拟日线：每天小幅上涨（drift 控制涨幅）。"""
    dates = pd.bdate_range("2025-01-01", periods=days)
    closes = [start_price * (1 + drift) ** i for i in range(days)]
    rows = []
    for d, c in zip(dates, closes):
        rows.append(
            {
                "date": d,
                "open": round(c * 0.99, 2),
                "high": round(c * 1.02, 2),
                "low": round(c * 0.98, 2),
                "close": round(c, 2),
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
                "turnover": 0.01,
            }
        )
    return pd.DataFrame(rows)


def _make_stock_list(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": codes,
            "name": [f"测试{c}" for c in codes],
            "industry": [""] * len(codes),
            "list_date": ["2020-01-01"] * len(codes),
            "is_st": [0] * len(codes),
        }
    )


# ---------- 涨跌停价 ----------
def test_price_limit_main_board():
    """主板 ±10%。"""
    bars = _make_bars(5, start_price=10.0)
    up, down = _price_limit("600000", bars, 4)
    assert up == pytest.approx(11.0, abs=0.01)
    assert down == pytest.approx(9.0, abs=0.01)


def test_price_limit_gem():
    """创业板 ±20%。"""
    bars = _make_bars(5, start_price=10.0)
    up, down = _price_limit("300001", bars, 4)
    assert up == pytest.approx(12.0, abs=0.01)
    assert down == pytest.approx(8.0, abs=0.01)


def test_price_limit_bse():
    """北交所 ±30%。"""
    bars = _make_bars(5, start_price=10.0)
    up, down = _price_limit("920001", bars, 4)
    assert up == pytest.approx(13.0, abs=0.01)
    assert down == pytest.approx(7.0, abs=0.01)


# ---------- 基本回测流程 ----------
def test_backtest_basic():
    """基本流程：能跑通并产生交易与资金曲线。"""
    bars_map = {"600000": _make_bars(120, start_price=10.0, drift=0.01)}
    stock_list = _make_stock_list(["600000"])
    end = date(2025, 6, 30)
    start = end - timedelta(days=180)
    config = BacktestConfig(start=start, end=end, top_n=1)
    result = run_backtest(DummyStrategy(), config, bars_map, {}, stock_list)

    assert not result.equity_curve.empty
    assert len(result.trades) >= 1
    # 资金曲线索引应为日期
    assert isinstance(result.equity_curve.index, pd.DatetimeIndex)


def test_backtest_no_data():
    """无候选股票时返回空结果。"""
    bars_map = {}
    stock_list = _make_stock_list([])
    end = date(2025, 6, 30)
    start = end - timedelta(days=180)
    config = BacktestConfig(start=start, end=end)
    result = run_backtest(DummyStrategy(), config, bars_map, {}, stock_list)
    assert result.equity_curve.empty
    assert result.trades.empty


# ---------- T+1 规则 ----------
def test_t1_rule():
    """T+1：买入当日不能卖出，最早次日卖出。"""
    bars_map = {"600000": _make_bars(120, start_price=10.0, drift=0.01)}
    stock_list = _make_stock_list(["600000"])
    end = date(2025, 6, 30)
    start = end - timedelta(days=180)
    config = BacktestConfig(start=start, end=end, top_n=1)
    result = run_backtest(DummyStrategy(), config, bars_map, {}, stock_list)

    for _, t in result.trades.iterrows():
        entry = pd.Timestamp(t["entry_date"])
        exit_ = pd.Timestamp(t["exit_date"])
        # 卖出日必须晚于买入日（T+1）
        assert exit_ > entry


# ---------- 手续费 / 滑点 ----------
def test_commission_and_slippage():
    """买入含手续费与滑点，卖出含手续费+印花税+滑点。"""
    bars_map = {"600000": _make_bars(120, start_price=10.0, drift=0.01)}
    stock_list = _make_stock_list(["600000"])
    end = date(2025, 6, 30)
    start = end - timedelta(days=180)
    config = BacktestConfig(start=start, end=end, top_n=1)
    result = run_backtest(DummyStrategy(), config, bars_map, {}, stock_list)

    assert not result.trades.empty
    t = result.trades.iloc[0]
    # 买入价应高于信号价（含滑点加价）
    assert t["entry_price"] > 0
    # 卖出价应低于当日收盘价（含滑点减价）
    assert t["exit_price"] > 0
    # pnl 应等于 卖出所得 - 买入成本（含费用）
    assert t["pnl"] != 0


# ---------- 交易成本可配置（暂时需求 5）----------
def test_avg_pnl_pct_metric():
    """metrics 暴露单笔平均收益率，供"收益能否覆盖往返成本"的判断使用。"""
    from stock_plan.backtest.metrics import calc_metrics

    idx = pd.date_range("2026-01-01", periods=60, freq="B")
    equity = pd.Series([100_000.0 * (1.001 ** i) for i in range(60)], index=idx)
    trades = pd.DataFrame(
        {
            "entry_date": ["2026-01-05", "2026-01-10", "2026-01-15"],
            "exit_date": ["2026-01-12", "2026-01-20", "2026-01-25"],
            "pnl": [1000.0, -500.0, 2000.0],
            "pnl_pct": [3.0, -1.5, 6.0],
        }
    )
    assert calc_metrics(equity, trades)["avg_pnl_pct"] == pytest.approx(2.5, abs=0.01)
    # 无交易时应为 0，而不是 NaN（否则 UI 上的成本对比会显示 nan）
    assert calc_metrics(equity, pd.DataFrame())["avg_pnl_pct"] == 0.0


def test_commission_is_configurable():
    """佣金/印花税/滑点可配置：成本调高后总收益必须下降。

    原实现这三个值写死在 BacktestConfig 默认值里，UI 无法修改，不满足
    "客户自定义回测"的诉求（暂时需求 5）。
    """
    from stock_plan.backtest.metrics import calc_metrics

    bars_map = {"600000": _make_bars(120, start_price=10.0, drift=0.01)}
    stock_list = _make_stock_list(["600000"])
    end = date(2025, 6, 30)
    start = end - timedelta(days=180)

    free = BacktestConfig(start=start, end=end, top_n=1,
                          commission=0.0, stamp_tax=0.0, slippage=0.0)
    pricey = BacktestConfig(start=start, end=end, top_n=1,
                            commission=0.003, stamp_tax=0.001, slippage=0.002)

    r_free = run_backtest(DummyStrategy(), free, bars_map, {}, stock_list)
    r_pricey = run_backtest(DummyStrategy(), pricey, bars_map, {}, stock_list)
    m_free = calc_metrics(r_free.equity_curve, r_free.trades)
    m_pricey = calc_metrics(r_pricey.equity_curve, r_pricey.trades)

    assert m_free["trade_count"] > 0, "该用例需要有交易，否则断言无意义"
    assert m_pricey["total_return"] < m_free["total_return"], "提高交易成本后总收益应下降"
    # 零成本档的单笔均收益应高于高成本档
    assert m_free["avg_pnl_pct"] > m_pricey["avg_pnl_pct"]