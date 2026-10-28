# -*- coding: utf-8 -*-
"""字段消费接口（data/fields.py）测试：降级语义 + 取值正确性。

覆盖：流通市值优先用「成交额 ÷ 换手率」、总市值 = 总股本 × 收盘价、历史优先于快照、
缺数据返回 degraded 而不抛错、批量字段表、披露日透传。
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.data import disclosure as dc
from stock_plan.data import fields
from stock_plan.data import share_capital as sc


@pytest.fixture(autouse=True)
def _clear_cache():
    """每个用例前后清缓存，避免 tmp_path 之间串数据。"""
    fields.clear_cache()
    yield
    fields.clear_cache()


def _seed(tmp_path, codes=("000001",)):
    """在 tmp 目录造快照 + 历史表。"""
    snapshot = pd.DataFrame(
        {
            "code": list(codes),
            "total_shares": [2e10] * len(codes),
            "float_shares": [1e10] * len(codes),
            "total_mv": [2e11] * len(codes),
            "float_mv": [1e11] * len(codes),
            "close": [10.0] * len(codes),
            "as_of": ["2026-10-28"] * len(codes),
            "source": ["akshare"] * len(codes),
        }
    )
    sc.save_snapshot(snapshot, tmp_path)
    history_float = sc.derive_float_shares_history({"000001": _bars()})
    history_total = sc.derive_total_shares_history(history_float, snapshot)
    sc.save_history(history_float, history_total, tmp_path)
    return tmp_path


def _bars(n: int = 3, close: float = 10.0, amount: float = 1e9, turnover: float = 0.01):
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-10-26", periods=n, freq="D"),
            "close": [close] * n,
            "amount": [amount] * n,
            "turnover": [turnover] * n,
        }
    )


def test_as_of_float_shares_prefers_history(tmp_path):
    base = _seed(tmp_path)
    result = fields.as_of_float_shares("000001", "2026-10-27", out_dir=base)

    assert result["value"] == pytest.approx(1e10)
    assert result["source"] == "history_float"
    assert result["degraded"] is None


def test_as_of_float_shares_falls_back_to_snapshot(tmp_path):
    """日期早于历史表首日 → 退回最新快照，并说明降级原因。"""
    base = _seed(tmp_path)
    result = fields.as_of_float_shares("000001", "2026-01-02", out_dir=base)

    assert result["value"] == pytest.approx(1e10)
    assert result["source"] == "snapshot"
    assert "退回最新快照" in result["degraded"]


def test_as_of_total_shares_marks_proxy(tmp_path):
    base = _seed(tmp_path)
    result = fields.as_of_total_shares("000001", "2026-10-27", out_dir=base)

    assert result["value"] == pytest.approx(2e10)
    assert result["source"] == "proxy"
    assert "按当期比例外推" in result["degraded"]


def test_as_of_total_mv_multiplies_shares_by_close(tmp_path):
    base = _seed(tmp_path)
    result = fields.as_of_total_mv("000001", "2026-10-27", 12.0, out_dir=base)

    assert result["value"] == pytest.approx(2e10 * 12.0)
    assert result["source"] == "proxy"


def test_as_of_total_mv_requires_valid_close(tmp_path):
    base = _seed(tmp_path)
    result = fields.as_of_total_mv("000001", "2026-10-27", None, out_dir=base)
    assert result["value"] is None
    assert "缺有效收盘价" in result["degraded"]


def test_as_of_float_mv_prefers_amount_over_turnover(tmp_path):
    """当日有成交额/换手率时用它（1000 亿），不用股本×价格。"""
    base = _seed(tmp_path)
    result = fields.as_of_float_mv(
        "000001", "2026-10-27", close=10.0, amount=1e9, turnover=0.01, out_dir=base
    )
    assert result["value"] == pytest.approx(1e11)
    assert result["source"] == "amount_turnover"
    assert result["degraded"] is None


def test_as_of_float_mv_falls_back_to_shares(tmp_path):
    base = _seed(tmp_path)
    result = fields.as_of_float_mv("000001", "2026-10-27", close=10.0, out_dir=base)
    assert result["value"] == pytest.approx(1e10 * 10.0)
    assert result["source"] == "shares_close"


def test_missing_data_degrades_instead_of_raising(tmp_path):
    """完全没有数据：返回 value=None + 中文降级说明，不抛异常。"""
    empty = tmp_path / "empty"
    result = fields.as_of_total_shares("000001", "2026-10-27", out_dir=empty)

    assert result["value"] is None
    assert result["source"] is None
    assert "无可用数据" in result["degraded"]


def test_unknown_code_degrades(tmp_path):
    base = _seed(tmp_path)
    result = fields.as_of_float_shares("600000", "2026-10-27", out_dir=base)
    assert result["value"] is None
    assert result["degraded"]


def test_bad_date_degrades(tmp_path):
    base = _seed(tmp_path)
    result = fields.as_of_float_shares("000001", "bad-date", out_dir=base)
    assert result["value"] is None


def test_as_of_disclosure_delegates(tmp_path):
    db = tmp_path / "meta.db"
    dc.save_disclosure(
        pd.DataFrame(
            [{"code": "000001", "report_period": "20251231", "announce_date": "2026-03-20",
              "actual_date": "2026-03-20", "source": "akshare_yysj", "updated_at": "x"}]
        ),
        db,
    )
    result = fields.as_of_disclosure("000001", "2026-04-01", db_path=db)

    assert result["value"] == "2026-03-20"
    assert result["source"] == "announce"
    assert result["report_period"] == "20251231"


def test_field_snapshot_batch(tmp_path):
    base = _seed(tmp_path)
    table = fields.field_snapshot(
        ["000001", "600000"], "2026-10-27", bars_map={"000001": _bars()}, out_dir=base
    )

    assert list(table["code"]) == ["000001", "600000"]
    row = table[table["code"] == "000001"].iloc[0]
    assert row["float_mv"] == pytest.approx(1e11)  # 走 amount/turnover
    assert row["float_mv_source"] == "amount_turnover"
    assert row["total_mv"] == pytest.approx(2e11)
    # 600000 没有数据 → 降级说明非空
    assert table[table["code"] == "600000"].iloc[0]["degraded"]
