"""增量更新提速的回归测试（离线，不联网）。

覆盖三件事：
1. 线程本地 Session 的复用与隔离（keep-alive 的核心，复用错线程会串会话）；
2. 请求默认超时注入（没有超时，一条半死连接能卡住整个 worker）；
3. 股票列表「当天只刷一次」与预筛并行后的结果正确性（省 20 秒但不能省错股票）。

为什么这些要单独测：这三个改动都是"为了快"，而快最容易换来的副作用就是
悄悄改变了行为（比如列表不刷新 → 新股永远进不来）。所以每条都要有断言。
"""
from __future__ import annotations

import threading
from datetime import date, timedelta

import pandas as pd
import pytest
import requests

from stock_plan.data import updater
from stock_plan.data.http_pool import _request_with_timeout, keepalive_requests, thread_session
from stock_plan.data.storage import Storage


def _bars(dates: list[str], close: float = 10.0) -> pd.DataFrame:
    rows = []
    for d in dates:
        rows.append(
            {
                "date": pd.Timestamp(d),
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": 1_000_000,
                "amount": 10_000_000.0,
                "turnover": 1.0,
            }
        )
    return pd.DataFrame(rows)


def _stock_list(codes: list[str], names: list[str] | None = None) -> pd.DataFrame:
    """stock_list 表要求 code/name/industry/list_date/is_st 五列（save_stock_list 按列名取）。"""
    return pd.DataFrame(
        {
            "code": codes,
            "name": names or codes,
            "industry": ["" for _ in codes],
            "list_date": ["20200101" for _ in codes],
            "is_st": [0 for _ in codes],
        }
    )


class CountingFetcher:
    """记录连网次数：列表刷新几次、逐只抓了几只。"""

    def __init__(self, stock_list: pd.DataFrame, new_bars: pd.DataFrame):
        self.stock_list = stock_list
        self.new_bars = new_bars
        self.list_calls = 0
        self.bar_calls: list[str] = []

    def get_stock_list(self):
        self.list_calls += 1
        return self.stock_list

    def get_daily_bars(self, code, start, end):
        self.bar_calls.append(code)
        return self.new_bars

    def get_fundamentals(self, code, latest_price=None):
        return {}


@pytest.fixture
def tmp_storage(tmp_path):
    return Storage(bars_dir=tmp_path / "bars", db_path=tmp_path / "meta.db")


# ---------- 1. 线程本地 Session ----------

def test_同一线程内复用同一个session():
    s1 = thread_session(timeout=5)
    s2 = thread_session(timeout=5)
    assert s1 is s2
    s1.close()


def test_不同线程各自独立session():
    """Session 不能跨线程共享：连接池与 cookie 罐都不是线程安全的。"""
    main_session = thread_session(timeout=5)
    box: dict = {}

    def worker():
        box["session"] = thread_session(timeout=5)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert box["session"] is not main_session
    main_session.close()
    box["session"].close()


def test_keepalive块退出后还原requests():
    original_get = requests.get
    original_post = requests.post
    with keepalive_requests(timeout=3):
        assert requests.get is not original_get
        assert requests.post is not original_post
    assert requests.get is original_get
    assert requests.post is original_post


# ---------- 2. 默认超时 ----------

class _RecordingSession:
    def __init__(self):
        self.kwargs: list[dict] = []

    def request(self, method, url, **kwargs):
        self.kwargs.append(kwargs)
        return "ok"


def test_默认超时注入且不覆盖显式超时():
    fake = _RecordingSession()
    wrapped = _request_with_timeout(fake, 30)
    wrapped("GET", "https://example.com/a")
    wrapped("GET", "https://example.com/b", timeout=5)
    assert fake.kwargs[0]["timeout"] == 30
    assert fake.kwargs[1]["timeout"] == 5  # 调用方显式给了就用它的


# ---------- 3. 股票列表当天只刷一次 ----------

def test_当天已刷新则不再联网(tmp_storage):
    stock_list = _stock_list(["600000"], ["浦发银行"])
    tmp_storage.save_stock_list(stock_list)
    marker = updater._list_marker(tmp_storage)
    marker.write_text(date.today().isoformat(), encoding="utf-8")
    fetcher = CountingFetcher(stock_list, _bars(["2026-09-01"]))

    got = updater._refresh_stock_list(fetcher, tmp_storage)

    assert fetcher.list_calls == 0  # 没联网
    assert got["code"].tolist() == ["600000"]


def test_标记过期会联网并重写标记(tmp_storage):
    old_list = _stock_list(["600000"], ["浦发银行"])
    new_list = _stock_list(["600000", "301266"], ["浦发银行", "新股"])
    tmp_storage.save_stock_list(old_list)
    marker = updater._list_marker(tmp_storage)
    marker.write_text((date.today() - timedelta(days=1)).isoformat(), encoding="utf-8")
    fetcher = CountingFetcher(new_list, _bars(["2026-09-01"]))

    got = updater._refresh_stock_list(fetcher, tmp_storage)

    assert fetcher.list_calls == 1
    assert got["code"].tolist() == ["600000", "301266"]  # 新股能进来
    assert marker.read_text(encoding="utf-8").strip() == date.today().isoformat()


def test_增量更新连跑两次只刷新一次列表(tmp_storage, monkeypatch):
    """幂等的关键：第二次跑不该再翻 70 页列表。"""
    stock_list = _stock_list(["600000"], ["浦发银行"])
    tmp_storage.save_bars("600000", _bars(["2026-09-01"]))
    fetcher = CountingFetcher(stock_list, _bars(["2026-09-01"]))
    monkeypatch.setattr(
        updater, "latest_expected_trade_date", lambda now=None: pd.Timestamp("2026-09-01")
    )

    updater.update_incremental(storage=tmp_storage, fetcher=fetcher)
    updater.update_incremental(storage=tmp_storage, fetcher=fetcher)

    assert fetcher.list_calls == 1


def test_预筛并行结果与逐只判断一致(tmp_storage, monkeypatch):
    """并行只是省 28 秒；挑出来的 todo 必须和串行一模一样。"""
    codes = [f"60000{i}" for i in range(6)]
    for i, code in enumerate(codes):
        # 前 3 只已到 2026-09-01，后 3 只停在 2026-08-01
        day = "2026-09-01" if i < 3 else "2026-08-01"
        tmp_storage.save_bars(code, _bars([day]))
    stock_list = _stock_list(codes)
    tmp_storage.save_stock_list(stock_list)
    updater._list_marker(tmp_storage).write_text(date.today().isoformat(), encoding="utf-8")
    fetcher = CountingFetcher(stock_list, _bars(["2026-09-01"]))
    monkeypatch.setattr(
        updater, "latest_expected_trade_date", lambda now=None: pd.Timestamp("2026-09-01")
    )

    stats = updater.update_incremental(storage=tmp_storage, fetcher=fetcher, workers=8)

    assert stats["current"] == 3
    assert stats["todo"] == 3
    assert sorted(fetcher.bar_calls) == sorted(codes[3:])
