"""pe/pb 回填与「必须传最新价」的防回归测试（2026-10-28）。

为什么要有这组测试：`fundamentals.pe` / `pb` 曾经**全表 NULL**（实测 5553 行全空），
根因不是数据源、而是调用方漏传 `latest_price`。这类漏参不会报错、只会静默降级
——估值分恒等于 50，"便宜"这一维在选股里等于死掉，回测也不会给出任何异常。
所以必须用测试钉住两件事：

1. 抓财务的调用链真的把最新收盘价传进去了（用假 fetcher 记录实参）；
2. `refresh_valuation` 只改 pe/pb、**不动** `updated_at`（它是 PIT 快照日，
   动了会让 `fundamental_as_of` 前视修正失效）。
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.data import updater
from stock_plan.data.storage import Storage


def _bars(dates: list[str], close: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.to_datetime(dates),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1_000_000,
            "amount": 10_000_000.0,
            "turnover": 1.5,
        }
    )


@pytest.fixture
def tmp_storage(tmp_path):
    return Storage(bars_dir=tmp_path / "bars", db_path=tmp_path / "meta.db")


def test_refresh_valuation_用现有eps乘价格(tmp_storage):
    tmp_storage.save_fundamentals("600000", {"eps": 2.0, "bvps": 10.0, "roe": 12.0})
    tmp_storage.save_bars("600000", _bars(["2026-10-24"], close=20.0))

    assert tmp_storage.refresh_valuation("600000") is True

    row = tmp_storage.load_fundamentals("600000")
    assert row["pe"] == pytest.approx(10.0)  # 20 / 2
    assert row["pb"] == pytest.approx(2.0)  # 20 / 10


def test_refresh_valuation_不修改快照日期(tmp_storage):
    """pe/pb 是"旧 eps × 新价格"，不能让 PIT 快照日漂到今天。"""
    tmp_storage.save_fundamentals("600000", {"eps": 2.0, "bvps": 10.0})
    with tmp_storage._conn() as conn:
        conn.execute(
            "UPDATE fundamentals SET updated_at = '2026-09-03T10:00:00' WHERE code = ?",
            ("600000",),
        )
    before = tmp_storage.fundamentals_snapshot_date()
    tmp_storage.save_bars("600000", _bars(["2026-10-24"], close=20.0))

    tmp_storage.refresh_valuation("600000")

    assert tmp_storage.fundamentals_snapshot_date() == before
    assert before.isoformat() == "2026-09-03"


def test_refresh_valuation_缺eps和bvps返回False(tmp_storage):
    tmp_storage.save_fundamentals("000001", {"roe": 10.0})
    tmp_storage.save_bars("000001", _bars(["2026-10-24"], close=12.0))

    assert tmp_storage.refresh_valuation("000001") is False
    # 没有有效值就不该写入，pe/pb 仍为空
    assert tmp_storage.load_fundamentals("000001")["pe"] is None


def test_last_close_取缓存最后一根且无缓存返回None(tmp_storage):
    bars = _bars(["2026-10-23", "2026-10-24"], close=17.0)
    bars.loc[bars.index[-1], "close"] = 18.0  # 两根不同价，才能区分"取第一根"的坏版本
    tmp_storage.save_bars("600000", bars)
    assert tmp_storage.last_close("600000") == pytest.approx(18.0)
    assert tmp_storage.last_close("999999") is None


class _RecordingFetcher:
    """记录抓财务时收到的 latest_price，用来钉住"参数真的传了"。"""

    def __init__(self, bars: pd.DataFrame):
        self.bars = bars
        self.latest_prices: list[float | None] = []

    def get_daily_bars(self, code, start, end):
        return self.bars

    def get_fundamentals(self, code, latest_price=None):
        self.latest_prices.append(latest_price)
        eps = 2.0
        return {
            "eps": eps,
            "bvps": 10.0,
            "pe": (latest_price / eps) if latest_price else None,
            "pb": (latest_price / 10.0) if latest_price else None,
        }


def test_补拉未缓存股时把最新收盘价传给财务接口(tmp_storage):
    """回归：漏传 latest_price 会让 pe/pb 永远是 None（本轮修复的根因）。"""
    fetcher = _RecordingFetcher(_bars(["2026-10-24"], close=20.0))

    updater.fetch_uncached(
        ["600000"], years=1, workers=1, storage=tmp_storage, fetcher=fetcher
    )

    assert fetcher.latest_prices == [pytest.approx(20.0)]
    row = tmp_storage.load_fundamentals("600000")
    assert row["pe"] == pytest.approx(10.0)
    assert row["pb"] == pytest.approx(2.0)
