"""updater.py 单元测试 — 合并去重 / 幂等跳过 / 期望交易日计算。"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from stock_plan.data import updater
from stock_plan.data.storage import Storage


def _bars(dates: list[str], close: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 1000.0,
            "amount": 1e6,
            "turnover": 0.01,
        }
    )


class FakeFetcher:
    """模拟在线拉取：返回固定近几天数据；记录调用次数用于断言幂等。"""

    def __init__(self, stock_list: pd.DataFrame, new_bars: pd.DataFrame):
        self.stock_list = stock_list
        self.new_bars = new_bars
        self.calls: list[str] = []

    def get_stock_list(self):
        return self.stock_list

    def get_daily_bars(self, code, start, end):
        self.calls.append(code)
        return self.new_bars

    def get_fundamentals(self, code):
        return {}


@pytest.fixture
def tmp_storage(tmp_path):
    return Storage(bars_dir=tmp_path / "bars", db_path=tmp_path / "meta.db")


def test_merge_bars_dedup_and_keep_new(tmp_storage):
    old = _bars(["2026-08-01", "2026-08-02"], close=10.0)
    new = _bars(["2026-08-02", "2026-08-03"], close=11.0)
    tmp_storage.save_bars("600000", old)

    n = updater.merge_bars(tmp_storage, "600000", new)
    df = tmp_storage.load_bars("600000")

    assert n == 3
    assert df["date"].tolist() == pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]).tolist()
    # 重叠日期保留新数据
    assert df.loc[df["date"] == "2026-08-02", "close"].iloc[0] == 11.0


def test_update_incremental_idempotent_skip(tmp_storage, monkeypatch):
    """本地数据已是最新时不应发起任何网络请求。"""
    stock_list = pd.DataFrame(
        {"code": ["600000", "000001"], "name": ["浦发银行", "平安银行"], "is_st": [0, 0], "industry": ["银行", "银行"], "list_date": ["19991110", "19910403"]}
    )
    new_bars = _bars(["2026-09-01"], close=10.0)
    fetcher = FakeFetcher(stock_list, new_bars)
    tmp_storage.save_bars("600000", _bars(["2026-09-01"]))

    # 固定期望最新交易日为 2026-09-01（周一）
    monkeypatch.setattr(
        updater, "latest_expected_trade_date", lambda now=None: pd.Timestamp("2026-09-01")
    )
    stats = updater.update_incremental(storage=tmp_storage, fetcher=fetcher)

    assert stats["updated"] == 0
    assert stats["current"] == 1  # 600000 已是最新
    assert stats["todo"] == 0
    assert fetcher.calls == []  # 幂等：没有网络请求
    assert set(stats["uncached"]) == {"000001"}


def test_update_incremental_fetches_stale(tmp_storage, monkeypatch):
    """落后于期望交易日的股票要被拉取并合并。"""
    stock_list = pd.DataFrame({"code": ["600000"], "name": ["浦发银行"], "is_st": [0], "industry": ["银行"], "list_date": ["20260901"]})
    new_bars = _bars(["2026-08-20", "2026-08-21"], close=9.5)
    fetcher = FakeFetcher(stock_list, new_bars)
    tmp_storage.save_bars("600000", _bars(["2026-08-01", "2026-08-20"]))

    monkeypatch.setattr(
        updater, "latest_expected_trade_date", lambda now=None: pd.Timestamp("2026-08-21")
    )
    stats = updater.update_incremental(storage=tmp_storage, fetcher=fetcher)

    assert stats["updated"] == 1 and stats["failed"] == 0
    assert fetcher.calls == ["600000"]
    df = tmp_storage.load_bars("600000")
    assert df["date"].max() == pd.Timestamp("2026-08-21")
    assert len(df) == 3  # 08-01 + 08-20(保留新价) + 08-21


def test_latest_expected_trade_date_rules(monkeypatch):
    """16:00 前 → 前一交易日；16:00 后（当天为交易日）→ 当天。"""
    cal = pd.DatetimeIndex(["2026-09-03", "2026-09-04"])  # 周四周五
    monkeypatch.setattr(updater, "get_trade_calendar", lambda refresh=False: cal)

    # 周五 15:00 → 期望周四
    assert updater.latest_expected_trade_date(datetime(2026, 9, 4, 15, 0)) == pd.Timestamp("2026-09-03")
    # 周五 16:00 → 期望周五
    assert updater.latest_expected_trade_date(datetime(2026, 9, 4, 16, 0)) == pd.Timestamp("2026-09-04")
    # 周六（非交易日）→ 期望周五
    assert updater.latest_expected_trade_date(datetime(2026, 9, 5, 9, 0)) == pd.Timestamp("2026-09-04")
    # 周日凌晨 0:30 → 期望周五
    assert updater.latest_expected_trade_date(datetime(2026, 9, 6, 0, 30)) == pd.Timestamp("2026-09-04")


def test_fetch_uncached_saves(tmp_storage):
    stock_list = pd.DataFrame({"code": ["301999"], "name": ["新股"], "is_st": [0], "industry": ["银行"], "list_date": ["20260901"]})
    fetcher = FakeFetcher(stock_list, _bars(["2026-09-01", "2026-09-02"]))
    stats = updater.fetch_uncached(["301999"], storage=tmp_storage, fetcher=fetcher)

    assert stats["updated"] == 1
    assert tmp_storage.cache_exists("301999")
    assert len(tmp_storage.load_bars("301999")) == 2
