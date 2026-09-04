# -*- coding: utf-8 -*-
"""数据获取模块单元测试。

用途：验证新浪日线解码调用在并发更新时被互斥，避免 MiniRacer 原生 DLL 崩溃。
所属模块：data.fetcher。

Revision History:
    2026-09-04  add concurrent Sina daily request lock coverage
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

import pandas as pd

from stock_plan.data import fetcher


def test_daily_bar_requests_are_serialised(monkeypatch) -> None:
    """多个更新线程不能同时进入 AkShare 的 MiniRacer 解码器。"""
    counter_lock = Lock()
    active = 0
    peak_active = 0

    def fake_daily_bars(**kwargs) -> pd.DataFrame:
        nonlocal active, peak_active
        with counter_lock:
            active += 1
            peak_active = max(peak_active, active)
        time.sleep(0.02)
        with counter_lock:
            active -= 1
        return pd.DataFrame(
            {
                "date": ["2026-09-04"],
                "open": [10.0],
                "high": [10.1],
                "low": [9.9],
                "close": [10.0],
                "volume": [1000.0],
                "amount": [10000.0],
                "turnover": [0.1],
            }
        )

    monkeypatch.setattr(fetcher.ak, "stock_zh_a_daily", fake_daily_bars)
    data_fetcher = fetcher.DataFetcher()
    with ThreadPoolExecutor(max_workers=4) as pool:
        bars = list(
            pool.map(
                lambda code: data_fetcher.get_daily_bars(code, "20260901", "20260904"),
                ["000001", "000002", "000003", "000004"],
            )
        )

    assert peak_active == 1
    assert all(len(df) == 1 for df in bars)
