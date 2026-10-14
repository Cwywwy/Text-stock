"""退市股数据层测试（暂时需求 5c）。

覆盖不需要联网的部分：代码映射、A 股过滤、时点池落盘与读取、退市代码推算、
自然月末生成。真实取数（baostock）不在单测范围内，由验证脚本单独跑。
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from stock_plan.data import delisted


# ---------- 代码映射 ----------
def test_to_bs_code() -> None:
    """前缀规则须与 fetcher._to_sina_symbol 保持一致。"""
    assert delisted.to_bs_code("600519") == "sh.600519"
    assert delisted.to_bs_code("000001") == "sz.000001"
    assert delisted.to_bs_code("300750") == "sz.300750"
    assert delisted.to_bs_code("688111") == "sh.688111"
    assert delisted.to_bs_code("920001") == "bj.920001"
    assert delisted.to_bs_code("830799") == "bj.830799"
    # 带前缀/空格的输入也能容忍
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

    # 指数：上证指数 / 沪深300 / 上证50 / 深证成指
    assert delisted.is_a_share("sh.000001") is False
    assert delisted.is_a_share("sh.000300") is False
    assert delisted.is_a_share("sh.000016") is False
    assert delisted.is_a_share("sz.399001") is False

    # 基金 / ETF / B 股 / 异常输入
    assert delisted.is_a_share("sh.510300") is False
    assert delisted.is_a_share("sh.900001") is False
    assert delisted.is_a_share("sz.200001") is False
    assert delisted.is_a_share("600000") is False
    assert delisted.is_a_share("") is False


# ---------- 自然月末 ----------
def test_month_ends() -> None:
    days = delisted.month_ends(date(2026, 1, 1), date(2026, 3, 31))
    assert days == ["2026-01-31", "2026-02-28", "2026-03-31"]
    # 区间末尾不是月末时也要包含 end
    days2 = delisted.month_ends(date(2026, 1, 1), date(2026, 2, 15))
    assert days2[-1] == "2026-02-15"


# ---------- 时点池落盘 / 读取 ----------
def test_universe_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(delisted, "UNIVERSE_DIR", tmp_path / "universe")
    delisted.save_universe("2024-04-25", ["000004", "600001", "000004"])
    got = delisted.load_universe("2024-04-25")
    assert got == ["000004", "600001"]          # 去重且有序
    assert delisted.load_universe("1999-01-01") == []   # 不存在时返回空列表


# ---------- 退市代码推算 ----------
def test_delisted_codes_from_universes(tmp_path, monkeypatch) -> None:
    """历史时点池的并集 − 今日在市 = 期间退市的股票，并按交易所分组。"""
    monkeypatch.setattr(delisted, "UNIVERSE_DIR", tmp_path / "universe")
    # 两个历史时点：一只沪市、一只深市后来退市
    delisted.save_universe("2021-12-31", ["600001", "000004", "600519"])
    delisted.save_universe("2024-12-31", ["000004", "600519", "300750"])

    groups = delisted.delisted_codes_from_universes(today_codes={"600519", "300750"})
    assert groups["SH"] == {"600001"}
    assert groups["SZ"] == {"000004"}
    assert groups["BJ"] == set()


def test_delisted_codes_groups_bj(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(delisted, "UNIVERSE_DIR", tmp_path / "universe")
    delisted.save_universe("2021-12-31", ["830799", "920001"])
    groups = delisted.delisted_codes_from_universes(today_codes=set())
    assert groups["BJ"] == {"830799", "920001"}


# ---------- 前端过滤：停牌行必须剔除 ----------
def test_universe_parquet_is_tiny(tmp_path, monkeypatch) -> None:
    """时点池只存代码，文件应当很小（否则按月存会撑爆数据目录）。"""
    monkeypatch.setattr(delisted, "UNIVERSE_DIR", tmp_path / "universe")
    codes = [f"{600000 + i:06d}" for i in range(5000)]
    path = delisted.save_universe("2026-01-30", codes)
    assert path.stat().st_size < 200 * 1024
