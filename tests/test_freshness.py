"""数据新鲜度体检的回归测试（离线，不联网）。

覆盖四件事：
1. 扫描行情文件能算出「最新交易日 / 覆盖只数 / 落后只数」——这是用户问"我现在用的是
   哪一天数据"的唯一准确答案；
2. 体检结果能缓存并脱离扫描复用（页面每次 rerun 都扫 5916 个 parquet 是不可接受的）；
3. 增量更新在数据落后超过窗口时会把窗口自动撑大（否则"更新成功"却留下几十天空洞）；
4. 四类数据（行情/分类/情绪/公告）的日期能汇总到一起。

第 3 条是这次唯一会改变抓取行为的逻辑，所以正反两个方向都要断言：落后要扩、不落后不许扩
（不落后还扩，等于每次更新都多抓几十天，白白拖慢速度）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.data import freshness, updater
from stock_plan.data.storage import Storage


def _write_bars(path, dates: list[str], close: float = 10.0) -> None:
    """写一份最小可用的行情 parquet（只要求 date 列，扫描只读 date）。"""
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000,
            "amount": 10_000_000.0,
            "turnover": 1.0,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def _stock_list(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": codes,
            "name": codes,
            "industry": ["" for _ in codes],
            "list_date": ["20200101" for _ in codes],
            "is_st": [0 for _ in codes],
        }
    )


def _bars_frame(dates: list[str]) -> pd.DataFrame:
    close = 10.0
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": 1_000_000,
            "amount": 10_000_000.0,
            "turnover": 1.0,
        }
    )


# ---------- 1. 扫描 ----------

def test_扫描行情能算出最新交易日与落后只数(tmp_path):
    bars = tmp_path / "bars"
    _write_bars(bars / "600000.parquet", ["2026-10-26", "2026-10-27"])
    _write_bars(bars / "600001.parquet", ["2026-10-27"])
    _write_bars(bars / "600002.parquet", ["2026-10-10"])  # 落后 17 天
    _write_bars(bars / "600003.parquet", ["2025-01-02"])  # 长期停牌/退市

    info = freshness.scan_bars(bars)
    assert info["latest_date"] == "2026-10-27"
    assert info["total"] == 4
    # current 必须是"最后交易日等于最新交易日"，不是"能读出来就算新"
    assert info["current"] == 2
    assert info["stale_7"] == 2
    assert info["stale_30"] == 1
    assert "600003" in info["stale_codes"]
    assert "600001" not in info["stale_codes"]


def test_空目录不报错(tmp_path):
    info = freshness.scan_bars(tmp_path / "nothing")
    assert info["latest_date"] is None
    assert info["total"] == 0


# ---------- 2. 缓存 ----------

def test_marker可读写并可脱离扫描复用(tmp_path, monkeypatch):
    marker = tmp_path / "_freshness.json"
    monkeypatch.setattr(freshness, "MARKER", marker)
    freshness.save_marker({"latest_date": "2026-10-27", "total": 3, "current": 2})

    def _boom(*a, **kw):  # 有缓存就不该再扫盘
        raise AssertionError("有 marker 时不应重新扫描")

    monkeypatch.setattr(freshness, "scan_bars", _boom)
    assert freshness.load_marker()["latest_date"] == "2026-10-27"
    assert freshness.latest_bar_date() == pd.Timestamp("2026-10-27")


def test_marker缺失时才扫描(tmp_path, monkeypatch):
    monkeypatch.setattr(freshness, "MARKER", tmp_path / "_freshness.json")
    _write_bars(tmp_path / "bars" / "600000.parquet", ["2026-10-27"])
    called = {"n": 0}
    real_scan = freshness.scan_bars

    def _counting(bars_dir=None, workers=32):
        called["n"] += 1
        return real_scan(tmp_path / "bars")

    monkeypatch.setattr(freshness, "scan_bars", _counting)
    assert freshness.latest_bar_date() == pd.Timestamp("2026-10-27")
    assert called["n"] == 1


# ---------- 3. 增量窗口的缺口保护 ----------

class _RecordingFetcher:
    """记录每次抓取的 start 日期，用来验证窗口是否扩大。"""

    def __init__(self, stock_list: pd.DataFrame, bars: pd.DataFrame):
        self.stock_list = stock_list
        self.bars = bars
        self.starts: list = []

    def get_stock_list(self):
        return self.stock_list

    def get_daily_bars(self, code, start, end):
        self.starts.append(start)
        return self.bars

    def get_fundamentals(self, code, latest_price=None):
        return {}


@pytest.fixture
def env(tmp_path, monkeypatch):
    storage = Storage(bars_dir=tmp_path / "bars", db_path=tmp_path / "meta.db")
    storage.save_stock_list(_stock_list(["600000"]))
    # 必须先有一份「旧的」本地行情：增量只处理已缓存的股票，没有缓存就不会发起请求
    storage.save_bars("600000", _bars_frame(["2026-07-01"]))

    def _fake_list(fetcher, storage_):
        return _stock_list(["600000"])

    monkeypatch.setattr(updater, "_refresh_stock_list", _fake_list)
    monkeypatch.setattr(updater, "latest_expected_trade_date", lambda *a, **kw: pd.Timestamp("2026-10-28"))
    monkeypatch.setattr(updater, "refresh_marker", lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(freshness, "refresh_marker", lambda *a, **kw: {"latest_date": "2026-10-28"})
    return storage


def test_数据落后时增量窗口自动扩大(env, monkeypatch):
    monkeypatch.setattr(freshness, "latest_bar_date", lambda *a, **kw: pd.Timestamp("2026-07-01"))
    fetcher = _RecordingFetcher(_stock_list(["600000"]), _bars_frame(["2026-10-27"]))
    stats = updater.update_incremental(days=30, workers=2, storage=env, fetcher=fetcher)

    assert stats["data_lag_days"] and stats["data_lag_days"] > 100
    assert stats["window_days"] > 100, "落后 100 天以上却仍按 30 天窗口抓，会留下空洞"
    # start 必须覆盖到本地最新日之前，否则合并后中间断档
    assert fetcher.starts and fetcher.starts[0] <= pd.Timestamp("2026-07-01").date()
    assert fetcher.starts[0] <= (pd.Timestamp("2026-10-28") - pd.Timedelta(days=30)).date()


def test_数据不落后时窗口保持默认(env, monkeypatch):
    monkeypatch.setattr(freshness, "latest_bar_date", lambda *a, **kw: pd.Timestamp("2026-10-27"))
    fetcher = _RecordingFetcher(_stock_list(["600000"]), _bars_frame(["2026-10-27"]))
    stats = updater.update_incremental(days=30, workers=2, storage=env, fetcher=fetcher)
    assert stats["window_days"] == 30, "没落后就不该扩窗口，否则每次更新都多抓几十天"


# ---------- 4. 汇总 ----------

def test_汇总包含四类数据日期(tmp_path, monkeypatch):
    monkeypatch.setattr(freshness, "MARKER", tmp_path / "_freshness.json")
    freshness.save_marker(
        {"latest_date": "2026-10-27", "total": 10, "current": 8, "stale_7": 2, "scanned_at": "x"}
    )

    from stock_plan.data import announcements, classification, market_snapshot

    monkeypatch.setattr(classification, "available_months", lambda: ["2026-09", "2026-10"])
    monkeypatch.setattr(market_snapshot, "available_days", lambda: ["2026-10-27"])
    monkeypatch.setattr(
        announcements, "stats", lambda: {"rows": 5, "codes": 2, "last_date": "2026-10-27"}
    )

    info = freshness.status(pd.Timestamp("2026-10-28"))
    assert info["bars_latest"] == "2026-10-27"
    assert info["lag_days"] == 1
    assert info["classify_latest"] == "2026-10"
    assert info["market_latest"] == "2026-10-27"
    assert info["ann_rows"] == 5
