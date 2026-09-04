# -*- coding: utf-8 -*-
"""Storage.load_market_maps 单元测试。

覆盖：日期窗口过滤（含 lead_days 预热）、板块/ST 预筛选、
max_codes 等间隔抽样上限、空数据与无缓存边界情况。

Revision History
----------------
2026-09-04  Cwywwy  首次创建：load_market_maps 窗口加载单测。
"""

from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.data.storage import Storage


def _make_bars(start: str, end: str) -> pd.DataFrame:
    """构造一段简单日线（每日 close=10，volume=1000，amount=1e7）。"""
    dates = pd.bdate_range(start, end)
    n = len(dates)
    return pd.DataFrame(
        {
            "date": dates,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10.0,
            "volume": 1000,
            "amount": 1e7,
            "turnover": 1.0,
        },
        index=range(n),
    )


@pytest.fixture()
def storage(tmp_path):
    """注入临时目录的 Storage，并写入 4 只股票的测试数据。"""
    st = Storage(bars_dir=tmp_path / "bars", db_path=tmp_path / "db" / "meta.db")
    stock_list = pd.DataFrame(
        {
            "code": ["000001", "600000", "300750", "688111"],
            "name": ["平安银行", "浦发银行", "宁德时代", "中芯国际"],
            "industry": ["银行", "银行", "电池", "半导体"],
            "list_date": ["19910403"] * 4,
            "is_st": [0, 1, 0, 0],
        }
    )
    st.save_stock_list(stock_list)
    for code in ("000001", "600000", "300750", "688111"):
        st.save_bars(code, _make_bars("2025-01-01", "2026-09-04"))
    st.save_fundamentals("000001", {"roe": 10.0})
    return st


# ---------- 日期窗口 ----------

def test_window_filters_rows(storage) -> None:
    _, bars_map, _ = storage.load_market_maps(start="2026-06-01", end="2026-08-01")
    assert set(bars_map) == {"000001", "300750", "688111"}  # 600000 为 ST 被剔除
    df = bars_map["000001"]
    assert df["date"].min() >= pd.Timestamp("2026-06-01")
    assert df["date"].max() <= pd.Timestamp("2026-08-01")


def test_lead_days_extends_window_backwards(storage) -> None:
    _, bars_map, _ = storage.load_market_maps(
        start="2026-06-01", end="2026-08-01", lead_days=180
    )
    df = bars_map["000001"]
    assert df["date"].min() < pd.Timestamp("2026-06-01")


def test_no_window_loads_full_history(storage) -> None:
    _, bars_map, _ = storage.load_market_maps()
    df = bars_map["000001"]
    assert df["date"].min() == pd.Timestamp("2025-01-01")
    assert df["date"].max() == pd.Timestamp("2026-09-04")


# ---------- 板块 / ST 预筛选 ----------

def test_boards_filter(storage) -> None:
    _, bars_map, _ = storage.load_market_maps(boards=["主板"], exclude_st=False)
    assert set(bars_map) == {"000001", "600000"}  # 沪深主板均保留
    _, bars_map, _ = storage.load_market_maps(boards=["创业板"])
    assert set(bars_map) == {"300750"}


def test_exclude_st(storage) -> None:
    _, bars_map, _ = storage.load_market_maps(exclude_st=False)
    assert "600000" in bars_map


# ---------- max_codes 上限 ----------

def test_max_codes_caps_universe(storage) -> None:
    _, bars_map, _ = storage.load_market_maps(exclude_st=False, max_codes=2)
    assert len(bars_map) == 2


def test_max_codes_not_applied_when_under_limit(storage) -> None:
    _, bars_map, _ = storage.load_market_maps(exclude_st=False, max_codes=100)
    assert len(bars_map) == 4


# ---------- fund_map / 边界情况 ----------

def test_fund_map_follows_bars_map(storage) -> None:
    _, _, fund_map = storage.load_market_maps()
    assert set(fund_map) == {"000001"}


def test_empty_stock_list(tmp_path) -> None:
    st = Storage(bars_dir=tmp_path / "bars", db_path=tmp_path / "db" / "meta.db")
    stock_list, bars_map, fund_map = st.load_market_maps()
    assert stock_list.empty
    assert bars_map == {}
    assert fund_map == {}


def test_window_excluding_all_rows(storage) -> None:
    _, bars_map, _ = storage.load_market_maps(start="2020-01-01", end="2020-02-01")
    assert bars_map == {}
