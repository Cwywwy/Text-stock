# -*- coding: utf-8 -*-
"""Storage.load_market_maps 单元测试。

覆盖：日期窗口过滤（含 lead_days 预热）、板块/ST 预筛选、
max_codes 等间隔抽样上限、空数据与无缓存边界情况；
Phase 15 起额外覆盖并行读取路径：并行/串行逐值对拍、顺序、缺失文件、
坏文件异常不被吞、真实多线程取证、fund_map 完整性，以及 pyarrow 过滤下推
（窗口/非法价位行）与 `load_bars` 参照实现的逐值等价。

Revision History
----------------
2026-09-04  Cwywwy  首次创建：load_market_maps 窗口加载单测。
2026-10-29  (phase 15) add parallel-reader parity, ordering and error-isolation cases
2026-10-29  (phase 15) add pyarrow-pushdown parity and schema-fallback cases
"""

from __future__ import annotations

import hashlib
import threading
import time

import numpy as np
import pandas as pd
import pytest

from stock_plan.data import storage as storage_module
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


# ---------- 并行读取路径（Phase 15） ----------

MANY_CODES = [f"{i:06d}" for i in range(1, 41)]


def _make_varied_bars(periods: int, base: float) -> pd.DataFrame:
    """构造每只股票都不同的日线（价格基准、条数都不同，避免内容比较退化成常量）。"""
    dates = pd.bdate_range("2025-01-02", periods=periods)
    n = len(dates)
    return pd.DataFrame(
        {
            "date": dates,
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base + 0.01 * np.arange(n),
            "volume": 1000.0 + np.arange(n),
            "amount": 1e7 + np.arange(n),
            "turnover": 0.01,
        }
    )


@pytest.fixture()
def many_storage(tmp_path):
    """40 只股票的临时市场：行情内容各不相同，每 3 只中有 1 只有财务数据。"""
    st = Storage(bars_dir=tmp_path / "bars", db_path=tmp_path / "db" / "meta.db")
    st.save_stock_list(
        pd.DataFrame(
            {
                "code": MANY_CODES,
                "name": [f"测试股{i}" for i in range(len(MANY_CODES))],
                "industry": ["测试"] * len(MANY_CODES),
                "list_date": ["20000101"] * len(MANY_CODES),
                "is_st": [0] * len(MANY_CODES),
            }
        )
    )
    for i, code in enumerate(MANY_CODES):
        st.save_bars(code, _make_varied_bars(periods=120 + i, base=10.0 + i))
    for i, code in enumerate(MANY_CODES[::3]):
        st.save_fundamentals(code, {"roe": 10.0 + i, "eps": 0.5, "bvps": 3.0})
    return st


def _load_with_workers(monkeypatch, st: Storage, workers: int, **kwargs):
    """用指定并发度调用 load_market_maps（workers=1 即串行参照实现）。"""
    monkeypatch.setattr(storage_module, "_BARS_LOAD_WORKERS", workers)
    return st.load_market_maps(**kwargs)


def _bars_digest(bars_map: dict[str, pd.DataFrame]) -> str:
    """bars_map 的稳定内容摘要：键顺序 + 列名 + 日期 + 全部数值逐字节参与哈希。"""
    digest = hashlib.sha256()
    for code, df in bars_map.items():
        digest.update(code.encode("utf-8"))
        digest.update(str(tuple(df.columns)).encode("utf-8"))
        dates = df["date"].to_numpy(dtype="datetime64[ns]").astype("int64")
        digest.update(dates.tobytes())
        digest.update(df.drop(columns=["date"]).to_numpy(dtype="float64").tobytes())
    return digest.hexdigest()


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


# ---------- 并行读取路径（Phase 15）：对拍 / 顺序 / 异常 ----------

def test_parallel_workers_are_bounded() -> None:
    """并发度必须有界（云端 2 GB 容器不能按文件数开线程）。"""
    assert isinstance(storage_module._BARS_LOAD_WORKERS, int)
    assert 1 <= storage_module._BARS_LOAD_WORKERS <= 16


def test_parallel_matches_serial(many_storage, monkeypatch) -> None:
    """并行（8 线程）与串行（1 线程）结果逐值一致：键顺序、日期、全部数值。"""
    kwargs = dict(start="2025-03-03", end="2025-09-30", lead_days=20, exclude_st=False)
    stock_serial, bars_serial, fund_serial = _load_with_workers(
        monkeypatch, many_storage, 1, **kwargs
    )
    stock_par, bars_par, fund_par = _load_with_workers(
        monkeypatch, many_storage, 8, **kwargs
    )

    assert len(bars_serial) > 0
    assert list(bars_par) == list(bars_serial), "并行读取必须保持 codes 顺序"
    for code, expected in bars_serial.items():
        pd.testing.assert_frame_equal(bars_par[code], expected, check_exact=True)
    pd.testing.assert_frame_equal(stock_par, stock_serial)
    assert fund_par == fund_serial
    assert _bars_digest(bars_par) == _bars_digest(bars_serial)


def test_parallel_path_uses_multiple_threads(many_storage, monkeypatch) -> None:
    """取证：40 只股票确实由多个工作线程读取（不是串行伪装成并行）。"""
    seen: set[str] = set()
    lock = threading.Lock()
    original = storage_module._read_bar_window_frame

    def slow_read(path, lo, hi):
        time.sleep(0.005)  # 放大单文件读取耗时，避免线程太快导致取证不稳定
        with lock:
            seen.add(threading.current_thread().name)
        return original(path, lo, hi)

    monkeypatch.setattr(storage_module, "_read_bar_window_frame", slow_read)
    monkeypatch.setattr(storage_module, "_BARS_LOAD_WORKERS", 8)
    _, bars_map, _ = many_storage.load_market_maps(exclude_st=False)

    assert len(bars_map) == len(MANY_CODES)
    assert len(seen) >= 2, f"应有多线程参与读取，实际线程：{sorted(seen)}"


def test_bars_map_order_follows_codes_order(many_storage) -> None:
    """bars_map 的键顺序 = 预筛选后的 codes 顺序（与串行 dict 插入顺序一致）。"""
    stock_list = many_storage.load_stock_list()
    _, bars_map, _ = many_storage.load_market_maps(exclude_st=False)
    assert list(bars_map) == stock_list["code"].astype(str).tolist()


def test_parallel_skips_missing_cache_file(many_storage) -> None:
    """缺缓存文件的 code 照旧跳过（不报错），其余顺序不变。"""
    missing = MANY_CODES[2]
    (many_storage.bars_dir / f"{missing}.parquet").unlink()
    stock_list = many_storage.load_stock_list()

    _, bars_map, _ = many_storage.load_market_maps(exclude_st=False)

    assert missing not in bars_map
    assert len(bars_map) == len(MANY_CODES) - 1
    expected = [c for c in stock_list["code"].astype(str).tolist() if c != missing]
    assert list(bars_map) == expected


def test_corrupt_parquet_not_silently_skipped(many_storage, monkeypatch) -> None:
    """坏 parquet 不能被静默跳过：串行/并行都必须抛同一种异常，修好后恢复。"""
    bad = many_storage.bars_dir / f"{MANY_CODES[5]}.parquet"
    bad.write_bytes(b"this is not a parquet file")

    with pytest.raises(Exception) as serial_exc:
        _load_with_workers(monkeypatch, many_storage, 1, exclude_st=False)
    with pytest.raises(Exception) as parallel_exc:
        _load_with_workers(monkeypatch, many_storage, 8, exclude_st=False)

    assert type(parallel_exc.value) is type(serial_exc.value)

    bad.unlink()
    _, bars_map, _ = many_storage.load_market_maps(exclude_st=False)
    assert len(bars_map) == len(MANY_CODES) - 1


def test_worker_exception_propagates_unwrapped(many_storage, monkeypatch) -> None:
    """工作线程里的异常原样穿出线程池（不被包成 Future/Executor 异常）。"""

    class Boom(RuntimeError):
        pass

    original = storage_module._read_bar_window_frame
    target = f"{MANY_CODES[7]}.parquet"

    def boom_read(path, lo, hi):
        if path.name == target:
            raise Boom(f"bad bars for {path.stem}")
        return original(path, lo, hi)

    monkeypatch.setattr(storage_module, "_read_bar_window_frame", boom_read)
    monkeypatch.setattr(storage_module, "_BARS_LOAD_WORKERS", 8)

    with pytest.raises(Boom, match="bad bars"):
        many_storage.load_market_maps(exclude_st=False)


def test_max_codes_sampling_order_unchanged(many_storage) -> None:
    """max_codes 等间隔抽样规则与串行实现一致（步长、取样点、顺序都不变）。"""
    stock_list = many_storage.load_stock_list()
    codes = stock_list["code"].astype(str).tolist()
    step = -(-len(codes) // 10)  # 与实现同一公式：向上取整
    expected = codes[::step]

    _, bars_map, _ = many_storage.load_market_maps(exclude_st=False, max_codes=10)

    assert list(bars_map) == expected


def test_fund_map_completeness_parallel(many_storage) -> None:
    """fund_map 与 bars_map 同步：只含有财务数据的 code，值逐字段相同。"""
    _, bars_map, fund_map = many_storage.load_market_maps(exclude_st=False)

    expected_codes = [
        code for code in bars_map if many_storage.load_fundamentals(code) is not None
    ]
    assert list(fund_map) == expected_codes
    for code in expected_codes:
        assert fund_map[code] == many_storage.load_fundamentals(code)


# ---------- pyarrow 过滤下推（Phase 15）：与 load_bars 参照实现逐值等价 ----------

def _reference_window_bars(
    st: Storage,
    code: str,
    start: str,
    end: str,
    lead_days: int = 0,
) -> pd.DataFrame:
    """参照实现：全量 `load_bars`（pandas 清洗）后再按窗口过滤。"""
    frame = st.load_bars(code)
    lo = pd.Timestamp(start) - pd.Timedelta(days=lead_days)
    hi = pd.Timestamp(end)
    frame = frame[(frame["date"] >= lo) & (frame["date"] <= hi)]
    return frame.reset_index(drop=True)


def test_pushdown_matches_load_bars_reference(many_storage) -> None:
    """快速路径（pyarrow 过滤下推）与 load_bars + pandas 窗口过滤逐值一致。"""
    kwargs = dict(start="2025-03-03", end="2025-09-30", lead_days=20, exclude_st=False)
    _, bars_map, _ = many_storage.load_market_maps(**kwargs)

    assert bars_map
    for code, frame in bars_map.items():
        expected = _reference_window_bars(
            many_storage, code, kwargs["start"], kwargs["end"], kwargs["lead_days"]
        )
        pd.testing.assert_frame_equal(frame, expected, check_exact=True)


def test_pushdown_drops_invalid_price_rows(many_storage) -> None:
    """open/high/low ≤ 0 的占位行必须剔除，与 `_sanitize_bars` 行为一致。"""
    code = MANY_CODES[0]
    dirty = many_storage.load_bars(code).copy()
    dirty.loc[3, ["open", "high", "low"]] = 0.0
    dirty.loc[7, "high"] = -1.0
    many_storage.save_bars(code, dirty)

    _, bars_map, _ = many_storage.load_market_maps(exclude_st=False)

    assert code in bars_map
    assert len(bars_map[code]) == len(dirty) - 2
    expected = _reference_window_bars(many_storage, code, "2000-01-01", "2099-12-31")
    pd.testing.assert_frame_equal(bars_map[code], expected, check_exact=True)


def test_pushdown_schema_fallback_on_string_dates(many_storage) -> None:
    """date 为字符串的非标准 parquet 必须回退 pandas 路径，窗口语义不变。"""
    code = MANY_CODES[1]
    frame = many_storage.load_bars(code).copy()
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    frame.to_parquet(many_storage.bars_dir / f"{code}.parquet", index=False)

    kwargs = dict(start="2025-03-03", end="2025-09-30", lead_days=20, exclude_st=False)
    _, bars_map, _ = many_storage.load_market_maps(**kwargs)

    assert code in bars_map
    expected = _reference_window_bars(
        many_storage, code, kwargs["start"], kwargs["end"], kwargs["lead_days"]
    )
    pd.testing.assert_frame_equal(bars_map[code], expected, check_exact=True)


def test_pushdown_missing_price_columns(many_storage) -> None:
    """缺 open/high/low 列的文件不能报错，语义与 load_bars（跳过清洗）一致。"""
    code = MANY_CODES[2]
    frame = many_storage.load_bars(code)[["date", "close"]].copy()
    frame.to_parquet(many_storage.bars_dir / f"{code}.parquet", index=False)

    _, bars_map, _ = many_storage.load_market_maps(exclude_st=False)

    assert code in bars_map
    expected = _reference_window_bars(many_storage, code, "2000-01-01", "2099-12-31")
    pd.testing.assert_frame_equal(bars_map[code], expected, check_exact=True)


def test_pushdown_empty_parquet_skips_code(many_storage) -> None:
    """空 parquet（0 行）照旧跳过该 code，不抛异常、不影响其他 code。"""
    code = MANY_CODES[3]
    empty = many_storage.load_bars(code).iloc[0:0].copy()
    empty.to_parquet(many_storage.bars_dir / f"{code}.parquet", index=False)

    _, bars_map, _ = many_storage.load_market_maps(exclude_st=False)

    assert code not in bars_map
    assert len(bars_map) == len(MANY_CODES) - 1


def test_pushdown_window_bounds_are_inclusive(many_storage) -> None:
    """窗口两端闭区间：起止日当天的行都要保留（与 pandas `>=`/`<=` 一致）。"""
    code = MANY_CODES[0]
    dates = many_storage.load_bars(code)["date"]
    day = dates.iloc[10]
    kwargs = dict(start=str(day.date()), end=str(day.date()), exclude_st=False)

    _, bars_map, _ = many_storage.load_market_maps(**kwargs)

    assert bars_map
    for frame in bars_map.values():
        assert len(frame) == 1
        assert frame["date"].iloc[0] == day
