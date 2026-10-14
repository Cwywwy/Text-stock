# -*- coding: utf-8 -*-
"""数据获取模块单元测试。

用途：验证新浪日线的并发模型 —— 网络请求并发执行，而 V8 上下文"每线程一个、
创建动作串行化"（**并发创建** MiniRacer 上下文才是原生崩溃的成因）。
所属模块：data.fetcher。

Revision History:
    2026-09-04  add concurrent Sina daily request lock coverage
    2026-10-15  global lock replaced by per-thread V8 context; tests updated
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

import pandas as pd

from stock_plan.data import fetcher


def _fake_bars(**kwargs) -> pd.DataFrame:
    """构造一行合法日线，替代真实的新浪请求。"""
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


def test_daily_bar_requests_are_concurrent(monkeypatch) -> None:
    """默认不加全局锁：多线程必须能同时进入取数函数。

    历史背景：曾用一把全局锁包住整个 stock_zh_a_daily 调用以规避 MiniRacer
    崩溃，但那段调用含 3 个串行 HTTP 请求，导致并发完全失效（实测 8 线程只有
    1.03x 加速，全市场要 78~104 分钟）。现在改为线程本地 V8 上下文。
    """
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
        return _fake_bars()

    monkeypatch.setattr(fetcher.ak, "stock_zh_a_daily", fake_daily_bars)
    monkeypatch.setattr(fetcher, "SINA_DAILY_LOCK", None)
    data_fetcher = fetcher.DataFetcher()
    with ThreadPoolExecutor(max_workers=4) as pool:
        bars = list(
            pool.map(
                lambda code: data_fetcher.get_daily_bars(code, "20260901", "20260904"),
                ["000001", "000002", "000003", "000004"],
            )
        )

    assert peak_active == 4, "取数应完全并发（曾被全局锁串行化为 1）"
    assert all(len(df) == 1 for df in bars)


def test_v8_context_is_thread_local_singleton(monkeypatch) -> None:
    """V8 上下文每线程只创建一次 —— 消除"并发创建"这一崩溃成因。"""
    created: list[int] = []

    class FakeRacer:
        def __init__(self, *args, **kwargs) -> None:
            created.append(threading.get_ident())

        def eval(self, _code: str) -> None:
            return None

        def call(self, _name: str, _payload: str):
            return []

    monkeypatch.setattr(fetcher, "_RealMiniRacer", FakeRacer)
    if hasattr(fetcher._thread_local, "v8"):
        del fetcher._thread_local.v8  # 从干净状态开始

    first = fetcher._ThreadLocalMiniRacer()
    second = fetcher._ThreadLocalMiniRacer()
    assert first is second, "同一线程必须复用同一个 V8 上下文"
    assert len(created) == 1, "同一线程只应创建一次上下文"

    other: list[object] = []

    def worker() -> None:
        other.append(fetcher._ThreadLocalMiniRacer())

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert other[0] is not first, "不同线程必须各持独立上下文"
    assert len(created) == 2, "每个线程只创建一次上下文"


def test_sina_lock_is_opt_in() -> None:
    """默认不启用全局串行锁；仅当显式设置 STOCK_PLAN_SINA_LOCK=1 时回退。"""
    locked = os.environ.get("STOCK_PLAN_SINA_LOCK") == "1"
    assert (fetcher.SINA_DAILY_LOCK is not None) == locked
