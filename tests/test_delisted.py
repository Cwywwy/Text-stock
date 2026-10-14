"""退市股数据层测试（暂时需求 5c）。

覆盖不需要联网的部分：代码映射、A 股过滤、时点池推导与落盘、退市股筛选、
接口错误必须上抛。真实取数（baostock）由验证脚本单独跑。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_plan.data import delisted


def _basic_table() -> pd.DataFrame:
    """模拟 baostock query_stock_basic 的返回（含指数与已退市标的）。"""
    return pd.DataFrame(
        [
            # 在市深市 A 股
            {"code": "sz.000001", "code_name": "平安银行", "ipoDate": "1991-04-03",
             "outDate": None, "type": "1", "status": "1"},
            # 已退市深市 A 股（2026-07-14 退市）
            {"code": "sz.000004", "code_name": "国华退", "ipoDate": "1990-12-01",
             "outDate": "2026-07-14", "type": "1", "status": "0"},
            # 已退市沪市 A 股
            {"code": "sh.600001", "code_name": "邯郸钢铁", "ipoDate": "1998-01-22",
             "outDate": "2009-12-29", "type": "1", "status": "0"},
            # 上证指数：type=2，且 is_a_share 为 False
            {"code": "sh.000001", "code_name": "上证综合指数", "ipoDate": "1991-07-15",
             "outDate": None, "type": "2", "status": "1"},
            # 已退市的**指数**：绝不能当成退市股票
            {"code": "sh.000817", "code_name": "中证兴证海西主题指数",
             "ipoDate": "2011-09-27", "outDate": "2018-07-11", "type": "2", "status": "0"},
        ]
    )


# ---------- 代码映射 ----------
def test_to_bs_code() -> None:
    """前缀规则须与 fetcher._to_sina_symbol 保持一致。"""
    assert delisted.to_bs_code("600519") == "sh.600519"
    assert delisted.to_bs_code("000001") == "sz.000001"
    assert delisted.to_bs_code("300750") == "sz.300750"
    assert delisted.to_bs_code("688111") == "sh.688111"
    assert delisted.to_bs_code("920001") == "bj.920001"
    assert delisted.to_bs_code("830799") == "bj.830799"
    assert delisted.to_bs_code(" sh600519 ") == "sh.600519"


# ---------- A 股过滤：必须按交易所判断 ----------
def test_is_a_share_filters_by_exchange() -> None:
    """核心用例：上证指数 sh.000001 不能因为数字前缀像深市 A 股就被放进来。

    它若漏进来不仅虚增股票池，还会与平安银行 sz.000001 的 6 位代码撞车。
    """
    assert delisted.is_a_share("sh.600000") is True
    assert delisted.is_a_share("sz.000001") is True
    assert delisted.is_a_share("sz.300750") is True
    assert delisted.is_a_share("sh.688111") is True

    assert delisted.is_a_share("sh.000001") is False    # 上证指数
    assert delisted.is_a_share("sh.000300") is False    # 沪深300
    assert delisted.is_a_share("sh.000016") is False    # 上证50
    assert delisted.is_a_share("sz.399001") is False    # 深证成指
    assert delisted.is_a_share("sh.510300") is False    # ETF
    assert delisted.is_a_share("sh.900001") is False    # B 股
    assert delisted.is_a_share("sz.200001") is False    # B 股
    assert delisted.is_a_share("600000") is False       # 缺交易所前缀
    assert delisted.is_a_share("") is False


# ---------- 基础表筛选 ----------
def test_a_share_table_filters_type_and_exchange() -> None:
    """既要按交易所前缀滤指数，又要按 type=1 滤掉非股票证券。"""
    table = delisted.a_share_table(_basic_table())
    assert set(table["code6"]) == {"000001", "000004", "600001"}


def test_delisted_a_shares_only_stocks() -> None:
    """已退市的**指数**（如 sh.000817）不能被当成退市股票。"""
    d = delisted.delisted_a_shares(_basic_table())
    assert set(d["code6"]) == {"000004", "600001"}
    assert set(d["exchange"]) == {"SZ", "SH"}
    assert d["outDate"].notna().all()


# ---------- 时点股票池推导 ----------
def test_universe_on_uses_listing_interval() -> None:
    """在市判定必须以上市/退市区间为准，这才是消除幸存者偏差的关键。"""
    t = delisted.a_share_table(_basic_table())
    # 2026-08-01：000004 已于 2026-07-14 退市，不应再在市
    assert delisted.universe_on("2026-08-01", t) == ["000001"]
    # 2005-06-30：三只都已在市
    assert delisted.universe_on("2005-06-30", t) == ["000001", "000004", "600001"]
    # 1995-06-30：600001 尚未上市（1998-01-22）
    assert delisted.universe_on("1995-06-30", t) == ["000001", "000004"]


def test_universe_on_accepts_raw_table() -> None:
    """容错：直接传原始基础表（没有 code6 列）也要能算。"""
    raw = _basic_table()
    t = delisted.a_share_table(raw).drop(columns=["code6"])
    assert delisted.universe_on("2005-06-30", t) == ["000001", "000004", "600001"]


# ---------- 接口错误必须上抛 ----------
def test_drain_raises_on_error() -> None:
    """初版在 error_code != 0 时静默返回空列表，把"请求失败"伪装成"没有数据"，
    导致 baostock 长会话退化被埋了 24 个月。这里锁死这个行为。"""

    class FakeRS:
        error_code = "10001"
        error_msg = "network error"

        def next(self) -> bool:
            return False

    with pytest.raises(RuntimeError, match="network error"):
        delisted._drain(FakeRS())


# ---------- 自然月末 ----------
def test_month_ends() -> None:
    days = delisted.month_ends(date(2026, 1, 1), date(2026, 3, 31))
    assert days == ["2026-01-31", "2026-02-28", "2026-03-31"]
    days2 = delisted.month_ends(date(2026, 1, 1), date(2026, 2, 15))
    assert days2[-1] == "2026-02-15"


# ---------- 时点池落盘 / 读取 ----------
def test_universe_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(delisted, "UNIVERSE_DIR", tmp_path / "universe")
    delisted.save_universe("2024-04-25", ["000004", "600001", "000004"])
    assert delisted.load_universe("2024-04-25") == ["000004", "600001"]  # 去重且有序
    assert delisted.load_universe("1999-01-01") == []                    # 不存在返回空


def test_universe_parquet_is_tiny(tmp_path, monkeypatch) -> None:
    """时点池只存代码，文件要足够小（按月存几十个才不会撑爆数据目录）。"""
    monkeypatch.setattr(delisted, "UNIVERSE_DIR", tmp_path / "universe")
    codes = [f"{600000 + i:06d}" for i in range(5000)]
    path = delisted.save_universe("2026-01-30", codes)
    assert path.stat().st_size < 200 * 1024
