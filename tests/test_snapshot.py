# -*- coding: utf-8 -*-
"""数据快照模块单元测试。

覆盖：分卷文件名、清单校验（缺字段/顺序错/校验和不符）、
build_snapshot 打包全流程、is_cloud 环境变量判定逻辑、
以及 GitHub Releases 发布后端（2026-10-15 新增）。

Revision History
----------------
2026-09-04  Cwywwy  首次创建：快照模块单测。
2026-10-15  Cwywwy  补充 Releases 发布后端与多地址回退的测试。
"""

from __future__ import annotations

import hashlib
import json
import subprocess

import pandas as pd
import pytest

from stock_plan.data import snapshot
from stock_plan.data.snapshot import (
    MIN_BARS_READY,
    local_branch_exists,
    part_filename,
    validate_manifest,
)


# ---------- part_filename ----------

def test_part_filename_format() -> None:
    assert part_filename(0) == "bars.zip.part00"
    assert part_filename(3) == "bars.zip.part03"
    assert part_filename(17) == "bars.zip.part17"


# ---------- local_branch_exists ----------

def test_local_branch_exists_returns_true_for_existing_branch(monkeypatch) -> None:
    """git 返回 0 时应识别为已有本地快照分支。"""
    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    assert local_branch_exists("data-snapshot") is True


def test_local_branch_exists_returns_false_for_missing_branch(monkeypatch) -> None:
    """git 返回 1 时应允许首次创建孤儿分支。"""
    class Result:
        returncode = 1
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: Result())
    assert local_branch_exists("data-snapshot") is False


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


# ---------- GitHub Releases 发布后端（2026-10-15） ----------

def test_release_and_branch_base_urls() -> None:
    """两种后端的下载基地址都要能直接拼 {base}/manifest.json 与 {base}/{分卷}。"""
    rel = snapshot.release_base_url(repo="owner/name", tag="data-snapshot")
    assert rel == "https://github.com/owner/name/releases/download/data-snapshot"

    raw = snapshot.raw_base_url(repo="owner/name", branch="data-snapshot")
    assert raw == "https://raw.githubusercontent.com/owner/name/data-snapshot"


def test_snapshot_base_url_selects_backend(monkeypatch) -> None:
    """后端默认 Release；显式切到 branch 时用 raw 地址。"""
    monkeypatch.delenv("STOCK_PLAN_SNAPSHOT_BACKEND", raising=False)
    assert "releases/download" in snapshot.snapshot_base_url()
    assert "releases/download" in snapshot.snapshot_base_url("release")

    monkeypatch.setenv("STOCK_PLAN_SNAPSHOT_BACKEND", "branch")
    assert "raw.githubusercontent.com" in snapshot.snapshot_base_url()
    assert "raw.githubusercontent.com" in snapshot.snapshot_base_url("branch")


def _fake_api(monkeypatch, existing_assets: list[dict] | None = None):
    """把 _gh_request 换成记录器，返回 (calls, uploaded)。"""
    calls: list[tuple] = []
    uploaded: dict[str, bytes] = {}
    existing = list(existing_assets or [])

    def fake(method, url, token, body=None, content_type="application/json", timeout=120):
        calls.append((method, url, body if not isinstance(body, bytes) else b"<bytes>"))
        if method == "GET" and "/releases?" in url:
            return [{"id": 7, "tag_name": "data-snapshot"}] if existing else []
        if method == "GET" and "/assets" in url:
            return existing
        if method == "POST" and "/releases/" in url and "/assets" in url:
            name = url.split("name=")[-1]
            if isinstance(body, bytes):
                uploaded[name] = body
            asset = {"id": 100 + len(uploaded), "name": name}
            existing.append(asset)
            return asset
        if method == "POST" and url.endswith("/releases"):
            return {"id": 7, "tag_name": "data-snapshot"}
        if method == "DELETE":
            return {}
        return {}

    monkeypatch.setattr(snapshot, "_gh_request", fake)
    return calls, uploaded, existing


def _build_dir(tmp_path):
    """造一份最小可用的构建产物：两卷分卷 + manifest。"""
    build = tmp_path / "build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "bars.zip.part00").write_bytes(b"A" * 10)
    (build / "bars.zip.part01").write_bytes(b"B" * 10)
    (build / "manifest.json").write_text("{}", encoding="utf-8")
    manifest = {
        "created_at": "2026-10-15 12:00:00",
        "bars_count": 3,
        "zip_bytes": 20,
        "zip_sha256": "x" * 64,
        "parts": [
            {"file": "bars.zip.part00", "bytes": 10, "sha256": "a" * 64},
            {"file": "bars.zip.part01", "bytes": 10, "sha256": "b" * 64},
        ],
    }
    return build, manifest


def test_publish_to_release_switches_manifest_last(monkeypatch, tmp_path) -> None:
    """**顺序是这份实现的核心**：manifest.json 必须在全部分卷上传成功后才替换。

    否则上传中途失败会让线上只剩一份不完整的快照。这里锁死顺序，并把
    "旧分卷在切换后被删除"一并验证。
    """
    build, manifest = _build_dir(tmp_path)
    old = [
        {"id": 1, "name": "manifest.json"},
        {"id": 2, "name": "bars.zip.part00"},
        {"id": 3, "name": "bars.zip.part01"},
    ]
    calls, uploaded, existing = _fake_api(monkeypatch, old)

    result = snapshot.publish_to_release(manifest, build, token="t0ken")

    posts = [c for c in calls if c[0] == "POST" and "/assets" in c[1]]
    names = [c[1].split("name=")[-1] for c in posts]
    assert names[-1] == "manifest.json", f"manifest 必须最后上传，实际顺序 {names}"

    part_names = [n for n in names if n != "manifest.json"]
    assert len(part_names) == 2
    assert all(n.startswith("bars-") for n in part_names), "分卷要用带版本号的新名字"
    assert not any(n == "bars.zip.part00" for n in names), "不能覆盖旧分卷"

    # manifest 内容里引用的分卷名必须与实际上传的名字一致
    new_manifest = json.loads(uploaded["manifest.json"].decode("utf-8"))
    assert [p["file"] for p in new_manifest["parts"]] == part_names
    assert new_manifest["tag"] == "data-snapshot"

    # 旧分卷与旧 manifest 都应被清理，Release 里只留一份数据
    deleted = {c[1].rsplit("/", 1)[-1] for c in calls if c[0] == "DELETE"}
    assert deleted == {"1", "2", "3"}
    assert result["backend"] == "release"
    assert result["replaced_assets"] == 3
    assert result["parts"] == 2


def test_publish_to_release_keeps_extra_files(monkeypatch, tmp_path) -> None:
    """附带文件（如公共策略 JSON）也要上传，且不能在上传后被清理掉。"""
    build, manifest = _build_dir(tmp_path)
    calls, uploaded, existing = _fake_api(monkeypatch, [{"id": 9, "name": "manifest.json"}])

    snapshot.publish_to_release(
        manifest, build, extra_files={"public_strategies.json": b"[]"}, token="t0ken"
    )
    assert "public_strategies.json" in uploaded
    deleted = {c[1].rsplit("/", 1)[-1] for c in calls if c[0] == "DELETE"}
    assert "9" in deleted, "旧 manifest 应被替换"
    # 附带文件不在删除列表里（它的 id 是上传后新生成的）
    assert all(d != "public_strategies.json" for d in deleted)


def test_publish_to_release_requires_token(monkeypatch, tmp_path) -> None:
    build, manifest = _build_dir(tmp_path)
    monkeypatch.delenv("DATA_SNAPSHOT_TOKEN", raising=False)
    # 也要屏蔽 secrets 文件兜底，否则本机有配置时这个用例就不成立了
    monkeypatch.setattr(snapshot, "resolve_token", lambda token=None: "")
    with pytest.raises(RuntimeError, match="缺少 GitHub token"):
        snapshot.publish_to_release(manifest, build)


def test_resolve_token_falls_back_to_secrets_file(monkeypatch, tmp_path) -> None:
    """本机计划任务不经过 Streamlit，读不到 st.secrets，必须能回落到 secrets 文件。

    否则定时发布会直接报"缺少 token"——这是实现时真实存在的缺口。
    """
    monkeypatch.delenv("DATA_SNAPSHOT_TOKEN", raising=False)
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", tmp_path)
    (tmp_path / ".streamlit").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".streamlit" / "secrets.toml").write_text(
        '# 注释\nDATA_SNAPSHOT_TOKEN = "github_pat_abc"\n', encoding="utf-8"
    )
    assert snapshot.resolve_token() == "github_pat_abc"

    # 环境变量优先于文件
    monkeypatch.setenv("DATA_SNAPSHOT_TOKEN", "from-env")
    assert snapshot.resolve_token() == "from-env"
    # 显式参数优先于一切
    assert snapshot.resolve_token("explicit") == "explicit"


def test_resolve_token_missing_returns_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DATA_SNAPSHOT_TOKEN", raising=False)
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", tmp_path)
    assert snapshot.resolve_token() == ""


def test_restore_with_fallback_tries_bases_in_order(monkeypatch, tmp_path) -> None:
    """回退顺序：首选地址失败后应继续尝试下一个，并返回第一个成功的结果。"""
    tried: list[str] = []

    def fake_restore(base, bars_dir, tmp_dir, progress=None, part_workers=4):
        tried.append(base)
        if "release" in base:
            raise RuntimeError("404 Not Found")
        return {"bars_count": 42}

    monkeypatch.setattr(snapshot, "restore_from_url", fake_restore)
    stats = snapshot.restore_with_fallback(
        ["https://x/releases/download/t", "https://raw.githubusercontent.com/x/t"],
        tmp_path, tmp_path / "tmp",
    )
    assert stats["bars_count"] == 42
    assert len(tried) == 2 and "releases" in tried[0]


def test_restore_with_fallback_reports_all_failures(monkeypatch, tmp_path) -> None:
    def always_fail(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(snapshot, "restore_from_url", always_fail)
    with pytest.raises(RuntimeError, match="所有快照地址均恢复失败"):
        snapshot.restore_with_fallback(["u1", "u2"], tmp_path, tmp_path / "tmp")
