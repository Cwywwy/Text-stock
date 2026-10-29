# -*- coding: utf-8 -*-
"""股本/市值模块测试（全部离线，不发网络请求）。

覆盖：快照口径（总股本 = 总市值 ÷ 价格）、落盘/读取、按「成交额 ÷ 换手率」反推流通股本、
总股本比例外推、覆盖率统计、缺文件降级。
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.data import share_capital as sc


def _raw_snapshot() -> pd.DataFrame:
    """构造 3 只股票的快照原始返回（模拟 akshare/东财字段）。"""
    return pd.DataFrame(
        {
            "code": ["000001", "600519", "300750"],
            "close": [10.0, 1500.0, 200.0],
            "total_mv": [2.0e11, 1.9e12, 8.8e11],
            "float_mv": [1.94e11, 1.9e12, 7.6e11],
        }
    )


def test_normalise_snapshot_derives_shares_from_mv_and_price():
    """总股本 = 总市值 ÷ 最新价，流通股本 = 流通市值 ÷ 最新价。"""
    frame = sc._normalise_snapshot(_raw_snapshot(), "akshare")

    assert list(frame.columns) == sc.SNAPSHOT_COLUMNS
    first = frame[frame["code"] == "000001"].iloc[0]
    assert first["total_shares"] == pytest.approx(2.0e11 / 10.0)
    assert first["float_shares"] == pytest.approx(1.94e11 / 10.0)
    assert first["source"] == "akshare"
    # as_of 是 'YYYY-MM-DD' 字符串（month 切片依赖这一点）
    assert len(str(first["as_of"])) == 10


def test_normalise_snapshot_drops_invalid_rows_and_dedupes():
    """价格 ≤0 / 总市值 ≤0 的行丢弃；同代码重复保留最后一行。"""
    raw = pd.DataFrame(
        {
            "code": ["000001", "000001", "600519"],
            "close": [0.0, 10.0, None],
            "total_mv": [1e10, 2e11, 1e12],
            "float_mv": [1e10, 1.9e11, 1e12],
        }
    )
    frame = sc._normalise_snapshot(raw, "eastmoney")

    assert list(frame["code"]) == ["000001"]
    assert frame["total_shares"].iloc[0] == pytest.approx(2e10)


def test_normalise_snapshot_normalises_prefixed_codes():
    """sh.600519 / 600519.SH 这类写法要归一成 6 位数字。"""
    raw = pd.DataFrame(
        {"code": ["sh.600519", "000001.SZ"], "close": [1.0, 1.0],
         "total_mv": [1e9, 2e9], "float_mv": [1e9, 2e9]}
    )
    frame = sc._normalise_snapshot(raw, "akshare")
    assert sorted(frame["code"]) == ["000001", "600519"]


def test_save_and_load_snapshot_roundtrip(tmp_path):
    """快照落盘后可按月份读回，且重复保存同代码不重复。"""
    frame = sc._normalise_snapshot(_raw_snapshot(), "akshare")
    path = sc.save_snapshot(frame, out_dir=tmp_path)

    assert path.name.endswith(".parquet")
    loaded = sc.load_snapshot(out_dir=tmp_path)
    assert len(loaded) == 3

    duplicate = pd.concat([frame, frame], ignore_index=True)
    sc.save_snapshot(duplicate, out_dir=tmp_path)
    assert len(sc.load_snapshot(out_dir=tmp_path)) == 3


def test_load_snapshot_missing_returns_empty(tmp_path):
    """没有快照文件时返回空表而不是抛错（供降级链路使用）。"""
    frame = sc.load_snapshot(out_dir=tmp_path)
    assert frame.empty
    assert list(frame.columns) == sc.SNAPSHOT_COLUMNS
    assert sc.available_months(tmp_path) == []


def _bars(n: int = 5, close: float = 10.0, amount: float = 1e9, turnover: float = 0.01):
    """构造等值日线：amount=10 亿、turnover=1% → 流通市值 1000 亿。"""
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-05", periods=n, freq="D"),
            "close": [close] * n,
            "amount": [amount] * n,
            "turnover": [turnover] * n,
        }
    )


def test_derive_float_shares_history_formula():
    """流通股本 = amount ÷ turnover ÷ close（中间量即流通市值）。"""
    history = sc.derive_float_shares_history({"000001": _bars()})

    assert list(history.columns) == sc.HISTORY_FLOAT_COLUMNS
    assert len(history) == 5
    assert history["float_mv"].iloc[0] == pytest.approx(1e11)
    assert history["float_shares"].iloc[0] == pytest.approx(1e10)


def test_derive_float_shares_history_drops_invalid_and_filters_codes():
    """换手率/成交额 ≤0 或停牌行丢弃；快照里没有的代码不参与。"""
    bars = _bars()
    bars.loc[0, "turnover"] = 0.0
    bars.loc[1, "amount"] = 0.0
    snapshot = pd.DataFrame({"code": ["600519"]})

    history = sc.derive_float_shares_history({"000001": bars}, snapshot)
    assert history.empty  # 快照只含 600519，000001 被过滤

    history = sc.derive_float_shares_history({"000001": bars})
    assert len(history) == 3  # 5 行中去掉 2 行无效


def test_derive_float_shares_history_missing_columns_yields_empty():
    """日线缺 amount/turnover 列时整只跳过，不抛错。"""
    bars = _bars().drop(columns=["turnover"])
    assert sc.derive_float_shares_history({"000001": bars}).empty


def test_derive_total_shares_history_uses_snapshot_ratio():
    """历史总股本 = 历史流通股本 × (当期总股本 ÷ 当期流通股本)，source=proxy。"""
    snapshot = pd.DataFrame(
        {"code": ["000001"], "total_shares": [2e10], "float_shares": [1e10]}
    )
    history_float = sc.derive_float_shares_history({"000001": _bars()})
    total = sc.derive_total_shares_history(history_float, snapshot)

    assert list(total.columns) == sc.HISTORY_TOTAL_COLUMNS
    assert total["total_shares"].iloc[0] == pytest.approx(2e10)
    assert total["total_mv"].iloc[0] == pytest.approx(2e11)
    assert set(total["source"]) == {"proxy"}


def test_derive_total_shares_history_empty_inputs():
    """空历史 / 空快照都返回空表（不抛错）。"""
    assert sc.derive_total_shares_history(pd.DataFrame(), pd.DataFrame()).empty
    empty_float = pd.DataFrame(columns=sc.HISTORY_FLOAT_COLUMNS)
    assert sc.derive_total_shares_history(empty_float, pd.DataFrame({"code": ["1"]})).empty


def test_save_and_load_history_roundtrip(tmp_path):
    """两张历史长表落盘后可读回（幂等：重写不追加）。"""
    snapshot = pd.DataFrame({"code": ["000001"], "total_shares": [2e10], "float_shares": [1e10]})
    history_float = sc.derive_float_shares_history({"000001": _bars()})
    history_total = sc.derive_total_shares_history(history_float, snapshot)

    sc.save_history(history_float, history_total, tmp_path)
    assert len(sc.load_history("float", tmp_path)) == 5
    assert len(sc.load_history("total", tmp_path)) == 5

    sc.save_history(history_float, history_total, tmp_path)
    assert len(sc.load_history("float", tmp_path)) == 5


def test_load_history_missing_returns_empty(tmp_path):
    assert sc.load_history("total", tmp_path).empty


def test_coverage_reports_snapshot_and_history():
    """覆盖率统计要给出股票数、日期区间与来源。"""
    snapshot = sc._normalise_snapshot(_raw_snapshot(), "akshare")
    history = sc.derive_float_shares_history({"000001": _bars()})
    stats = sc.coverage(snapshot, history)

    assert stats["snapshot_codes"] == 3
    assert stats["history_rows"] == 5
    assert stats["history_codes"] == 1
    assert stats["history_first"] == "2026-01-05"
    assert stats["snapshot_source"] == "akshare"


def test_snapshot_path_uses_month_of_as_of(tmp_path):
    path = sc.snapshot_path("2026-10-29", out_dir=tmp_path)
    assert path.name == "2026-10.parquet"


def test_norm_code_rejects_non_numeric():
    with pytest.raises(ValueError):
        sc.norm_code("AAPL")
