# -*- coding: utf-8 -*-
"""数据快照模块单元测试。

覆盖：分卷文件名、清单校验（缺字段/顺序错/校验和不符）、
build_snapshot 打包全流程、is_cloud 环境变量判定逻辑。

Revision History
----------------
2026-09-04  Cwywwy  首次创建：快照模块单测。
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from stock_plan.data.snapshot import (
    MIN_BARS_READY,
    part_filename,
    validate_manifest,
)


# ---------- part_filename ----------

def test_part_filename_format() -> None:
    assert part_filename(0) == "bars.zip.part00"
    assert part_filename(3) == "bars.zip.part03"
    assert part_filename(17) == "bars.zip.part17"


# ---------- validate_manifest ----------

def _make_manifest(parts: list[str], size: int = 100, sha: str = "ab" * 32) -> dict:
    return {
        "created_at": "2026-09-04 21:30:00",
        "bars_count": 5553,
        "zip_bytes": size,
        "zip_sha256": sha,
        "parts": [{"file": name, "bytes": 50, "sha256": "cd" * 32} for name in parts],
    }


def test_validate_manifest_ok() -> None:
    m = _make_manifest(["bars.zip.part00", "bars.zip.part01"])
    assert validate_manifest(m) == []


def test_validate_manifest_missing_field() -> None:
    m = _make_manifest(["bars.zip.part00"])
    del m["zip_sha256"]
    assert any("zip_sha256" in p for p in validate_manifest(m))


def test_validate_manifest_empty_parts() -> None:
    assert any("parts" in p for p in validate_manifest(_make_manifest([])))


def test_validate_manifest_wrong_order() -> None:
    m = _make_manifest(["bars.zip.part01", "bars.zip.part00"])
    assert len(validate_manifest(m)) >= 2


def test_validate_manifest_not_dict() -> None:
    assert validate_manifest(["not", "a", "dict"])  # type: ignore[arg-type]


# ---------- build_snapshot ----------

def test_build_snapshot_roundtrip(tmp_path) -> None:
    """塞 3 只假 parquet → 打包 → 校验 zip 与 manifest 一致。"""
    import zipfile

    from stock_plan.data.snapshot import build_snapshot

    bars = tmp_path / "bars"
    bars.mkdir()
    for code in ("000001", "600000", "300750"):
        pd.DataFrame({"date": [1, 2], "close": [10.0, 11.0]}).to_parquet(bars / f"{code}.parquet")

    out_dir = tmp_path / "build"
    m = build_snapshot(bars, out_dir)

    assert m["bars_count"] == 3
    assert len(m["parts"]) >= 1
    assert [p["file"] for p in m["parts"]] == [part_filename(i) for i in range(len(m["parts"]))]

    zip_path = out_dir / "bars.zip"
    assert zip_path.exists()
    assert zip_path.stat().st_size == m["zip_bytes"]
    assert hashlib.sha256(zip_path.read_bytes()).hexdigest() == m["zip_sha256"]

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert names == ["000001.parquet", "300750.parquet", "600000.parquet"]

    # 校验函数应通过自己的清单
    assert validate_manifest(m) == []

    # 分卷拼回应等于整包字节
    blob = b"".join((out_dir / p["file"]).read_bytes() for p in m["parts"])
    assert blob == zip_path.read_bytes()


# ---------- is_cloud / bootstrap 阈值 ----------

def test_is_cloud_env_flag(monkeypatch) -> None:
    from stock_plan.data import snapshot

    monkeypatch.setattr(snapshot, "cloud_secrets_env", lambda: None)
    for key in ("STOCK_PLAN_CLOUD", "STREAMLIT_CLOUD", "STREAMLIT_SHARING_MODE", "HOSTNAME"):
        monkeypatch.delenv(key, raising=False)
    assert snapshot.is_cloud() is False

    monkeypatch.setenv("STOCK_PLAN_CLOUD", "1")
    assert snapshot.is_cloud() is True

    monkeypatch.setenv("STOCK_PLAN_CLOUD", "0")
    assert snapshot.is_cloud() is False

    monkeypatch.delenv("STOCK_PLAN_CLOUD")
    monkeypatch.setenv("STREAMLIT_CLOUD", "true")
    assert snapshot.is_cloud() is True

    monkeypatch.delenv("STREAMLIT_CLOUD")
    monkeypatch.setenv("HOSTNAME", "app-123456-streamlit")
    assert snapshot.is_cloud() is True


def test_min_bars_ready_threshold() -> None:
    assert 0 < MIN_BARS_READY < 1000
