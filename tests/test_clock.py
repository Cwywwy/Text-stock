"""统一时钟（北京时间）回归测试。

2026-10-28 事故：云端容器跑在 UTC，页面「期望最新交易日」在北京时间 16:00–24:00
之间退回上一交易日，用户看到「期望 10-27、行情 09-03」以为数据没更新。这里钉住
三条契约：naive 视为北京时间、aware 按真实时刻换算、期望交易日不受容器时区影响。
"""

from __future__ import annotations

import re
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_plan.data import clock, updater


def test_cn_tz_is_fixed_utc_plus_8() -> None:
    now = clock.now_cn()
    assert now.tzinfo is not None
    assert now.utcoffset().total_seconds() == 8 * 3600


def test_to_cn_treats_naive_datetime_as_beijing() -> None:
    naive = datetime(2026, 10, 28, 9, 0, 0)
    assert clock.to_cn(naive) == naive.replace(tzinfo=clock.CN_TZ)
    assert clock.today_cn(naive) == date(2026, 10, 28)


def test_to_cn_converts_utc_aware_instant() -> None:
    """云端 UTC 15:07 == 北京 23:07（同一天）。"""
    utc_now = datetime(2026, 10, 28, 15, 7, 0, tzinfo=timezone.utc)
    cn = clock.to_cn(utc_now)
    assert cn.hour == 23 and cn.minute == 7
    assert clock.today_cn(utc_now) == date(2026, 10, 28)


def test_today_cn_crosses_date_boundary_at_utc_1600() -> None:
    """UTC 17:30 已进入北京次日 01:30，日期必须按北京算。"""
    utc_now = datetime(2026, 10, 28, 17, 30, 0, tzinfo=timezone.utc)
    assert clock.today_cn(utc_now) == date(2026, 10, 29)


def test_expected_trade_date_ignores_container_timezone(monkeypatch) -> None:
    """回归：UTC 容器的 15:07 是北京 23:07，应给出当天而不是上一交易日。"""
    cal = pd.DatetimeIndex(["2026-10-27", "2026-10-28", "2026-10-29"])
    monkeypatch.setattr(updater, "get_trade_calendar", lambda refresh=False: cal)

    utc_now = datetime(2026, 10, 28, 15, 7, 0, tzinfo=timezone.utc)  # 北京 23:07
    assert updater.latest_expected_trade_date(utc_now) == pd.Timestamp("2026-10-28")

    # 北京次日 01:30：当天（10-29）未过 16:00 → 期望仍是 10-28
    utc_next = datetime(2026, 10, 28, 17, 30, 0, tzinfo=timezone.utc)  # 北京 10-29 01:30
    assert updater.latest_expected_trade_date(utc_next) == pd.Timestamp("2026-10-28")


def test_expected_trade_date_keeps_naive_semantics(monkeypatch) -> None:
    """naive 值仍按“已经是北京时间”解释，既有调用与测试语义不变。"""
    cal = pd.DatetimeIndex(["2026-10-27", "2026-10-28"])
    monkeypatch.setattr(updater, "get_trade_calendar", lambda refresh=False: cal)

    assert updater.latest_expected_trade_date(datetime(2026, 10, 28, 15, 0)) == pd.Timestamp("2026-10-27")
    assert updater.latest_expected_trade_date(datetime(2026, 10, 28, 16, 0)) == pd.Timestamp("2026-10-28")


def test_stamp_cn_format_and_age() -> None:
    stamp = clock.stamp_cn()
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", stamp)

    from stock_plan.data import snapshot

    manifest = {"created_at": stamp}
    assert snapshot.manifest_age_days(manifest) == 0
    # 云端 UTC 时钟不能把北京时间生成的快照判成“未来/负数天”
    utc_now = datetime.now(timezone.utc)
    assert snapshot.manifest_age_days(manifest, now=utc_now) == 0


@pytest.mark.parametrize("module_name", ["freshness", "market_snapshot", "classification"])
def test_data_layer_has_no_local_date_calls(module_name: str) -> None:
    """data 层的日期判断必须走 clock，避免云端 UTC 再退一天。"""
    import importlib
    import inspect

    module = importlib.import_module(f"stock_plan.data.{module_name}")
    source = inspect.getsource(module)
    assert "date.today()" not in source
    assert "datetime.now()" not in source
