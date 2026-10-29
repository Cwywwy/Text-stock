# -*- coding: utf-8 -*-
"""财报披露日模块测试（全部离线，使用临时 SQLite）。

覆盖：报告期枚举、法定截止日（含年报跨年）、落盘 upsert 幂等、时点查询
（真实公告日优先 / 法定截止日兜底）、覆盖率统计、非法输入报错。
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.data import disclosure as dc


def _rows(code: str = "000001", period: str = "20251231", announce: str = "2026-03-20") -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "code": code,
                "report_period": period,
                "announce_date": announce,
                "actual_date": announce,
                "source": "akshare_yysj",
                "updated_at": "2026-10-29 05:00:00",
            }
        ]
    )


def test_report_periods_quarter_end_enumeration():
    """报告期按季度末枚举，含端点；不产生 2025 年报之后的越界期。"""
    periods = dc.report_periods("20251231", "20260930")
    assert periods == ["20251231", "20260331", "20260630", "20260930"]


def test_statutory_deadline_mapping_and_year_rollover():
    """一季报 04-30、半年报 08-31、三季报 10-31、年报次年 04-30。"""
    assert dc.statutory_deadline("20260331") == "2026-04-30"
    assert dc.statutory_deadline("20260630") == "2026-08-31"
    assert dc.statutory_deadline("20260930") == "2026-10-31"
    assert dc.statutory_deadline("20251231") == "2026-04-30"


@pytest.mark.parametrize("bad", ["20261331", "2025", "20250229", "2025123", ""])
def test_statutory_deadline_rejects_invalid_period(bad):
    with pytest.raises(ValueError):
        dc.statutory_deadline(bad)


def test_save_and_load_disclosure_roundtrip(tmp_path):
    db = tmp_path / "meta.db"
    assert dc.save_disclosure(_rows(), db) == 1

    loaded = dc.load_disclosure(db_path=db)
    assert len(loaded) == 1
    assert loaded["announce_date"].iloc[0] == "2026-03-20"
    assert list(loaded.columns) == dc.DISCLOSURE_COLUMNS


def test_save_disclosure_upserts_same_key(tmp_path):
    """同 (code, report_period) 重复写入是覆盖，不是追加。"""
    db = tmp_path / "meta.db"
    dc.save_disclosure(_rows(announce="2026-03-20"), db)
    dc.save_disclosure(_rows(announce="2026-03-25"), db)

    loaded = dc.load_disclosure(db_path=db)
    assert len(loaded) == 1
    assert loaded["announce_date"].iloc[0] == "2026-03-25"


def test_load_disclosure_filters(tmp_path):
    db = tmp_path / "meta.db"
    dc.save_disclosure(_rows(code="000001", period="20251231"), db)
    dc.save_disclosure(_rows(code="600519", period="20260331", announce="2026-04-20"), db)

    assert len(dc.load_disclosure("20251231", db_path=db)) == 1
    assert len(dc.load_disclosure(codes=["600519"], db_path=db)) == 1
    assert dc.load_disclosure("20261231", db_path=db).empty


def test_as_of_disclosure_prefers_announced_record(tmp_path):
    """有真实公告日时，取 announce_date ≤ date 的最近一期。"""
    db = tmp_path / "meta.db"
    dc.save_disclosure(_rows(period="20251231", announce="2026-03-20"), db)

    before = dc.as_of_disclosure("000001", "2026-03-19", db_path=db)
    assert before["source"] == "statutory"  # 真实公告日还没到 → 只能兜底
    assert before["value"] is None or before["value"] <= "2026-03-19"

    after = dc.as_of_disclosure("000001", "2026-03-21", db_path=db)
    assert after["source"] == "announce"
    assert after["value"] == "2026-03-20"
    assert after["report_period"] == "20251231"
    assert after["degraded"] is None


def test_as_of_disclosure_statutory_fallback_without_records(tmp_path):
    """没有记录时用法定截止日兜底（保守：信息可用时间推后，不引入未来函数）。"""
    db = tmp_path / "meta.db"
    result = dc.as_of_disclosure("000001", "2026-05-01", db_path=db)

    assert result["source"] == "statutory"
    assert result["value"] == "2026-04-30"  # 最近一个截止日已过的报告期
    assert result["report_period"] == "20260331"
    assert "法定截止日" in result["degraded"]


def test_as_of_disclosure_none_before_any_deadline(tmp_path):
    db = tmp_path / "meta.db"
    result = dc.as_of_disclosure("000001", "2022-01-01", db_path=db)
    assert result["value"] is None
    assert result["source"] is None
    assert result["degraded"]


def test_as_of_disclosure_rejects_bad_date(tmp_path):
    result = dc.as_of_disclosure("000001", "not-a-date", db_path=tmp_path / "meta.db")
    assert result["value"] is None
    assert "非法日期" in result["degraded"]


def test_coverage_counts_rows_codes_periods_and_actual(tmp_path):
    db = tmp_path / "meta.db"
    dc.save_disclosure(_rows(code="000001", period="20251231"), db)
    dc.save_disclosure(_rows(code="600519", period="20251231", announce="2026-04-10"), db)

    stats = dc.coverage(db)
    assert stats["rows"] == 2
    assert stats["codes"] == 2
    assert stats["periods"] == 1
    assert stats["with_actual_date"] == 2
    assert dc.stored_periods(db) == ["20251231"]


def test_refresh_disclosure_dry_run_lists_periods(tmp_path):
    stats = dc.refresh_disclosure(["20251231"], dry_run=True, db_path=tmp_path / "meta.db")
    assert stats["skipped"] == ["20251231"]
    assert stats["saved"] == 0


def test_parse_day_handles_common_forms():
    assert dc._parse_day("2026-03-20") == "2026-03-20"
    assert dc._parse_day(pd.Timestamp("2026-03-20")) == "2026-03-20"
    assert dc._parse_day("-") == ""
    assert dc._parse_day("None") == ""
    assert dc._parse_day(pd.NaT) == ""
