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
from datetime import datetime

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


def test_validate_manifest_accepts_release_part_names() -> None:
    """Release 资产不能以 .zip 结尾，发布时改名成 bars-<时间戳>.partNN。"""
    m = _make_manifest(["bars-20261028230605.part00", "bars-20261028230605.part01"])
    assert validate_manifest(m) == []


def test_validate_manifest_rejects_path_traversal_and_duplicates() -> None:
    """分卷名必须是纯文件名，且不能重复。"""
    assert validate_manifest(_make_manifest(["../bars.zip.part00"]))
    assert validate_manifest(_make_manifest(["bars.zip.part00", "bars.zip.part00"]))


def test_part_name_regex_matches_both_layouts() -> None:
    assert snapshot.PART_NAME_RE.match("bars.zip.part00")
    assert snapshot.PART_NAME_RE.match("bars-20261028230605.part12")
    assert not snapshot.PART_NAME_RE.match("bars.part00")
    assert not snapshot.PART_NAME_RE.match("other.zip.part00")


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
        assert names == ["bars/000001.parquet", "bars/300750.parquet", "bars/600000.parquet"]

    # 校验函数应通过自己的清单
    assert validate_manifest(m) == []

    # 分卷拼回应等于整包字节
    blob = b"".join((out_dir / p["file"]).read_bytes() for p in m["parts"])
    assert blob == zip_path.read_bytes()


# ---------- 快照 v2：行情之外带上分类/情绪/公告，整体覆盖恢复 ----------

def _write_fake_processed(root, n_bars: int = 3):
    """造一个像 data/processed 的目录（行情 + 各类非行情数据）。"""
    from pathlib import Path

    root = Path(root)
    bars = root / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    for i in range(n_bars):
        pd.DataFrame({"date": [1, 2], "close": [10.0, 11.0]}).to_parquet(bars / f"{600000 + i}.parquet")
    (root / "classify" / "2026-10" / "sw_l1").mkdir(parents=True)
    (root / "classify" / "2026-10" / "sw_l1" / "map.json").write_text("{}", encoding="utf-8")
    (root / "market" / "2026-10-28").mkdir(parents=True)
    (root / "market" / "2026-10-28" / "sentiment.json").write_text("{}", encoding="utf-8")
    (root / "universe").mkdir(parents=True, exist_ok=True)
    (root / "universe" / "2026-10.parquet").write_bytes(b"x")
    (root / "stock_basic.parquet").write_bytes(b"x")
    (root / "trade_calendar.csv").write_text("date,is_open\n", encoding="utf-8")
    (root / "announcements.db").write_bytes(b"x")
    return bars


def test_build_snapshot_includes_extras(tmp_path) -> None:
    """快照 v2 的 zip 里既有 bars/ 子目录，也有分类/情绪/公告等数据。"""
    import zipfile

    from stock_plan.data.snapshot import build_snapshot

    bars = _write_fake_processed(tmp_path / "processed")
    m = build_snapshot(bars, tmp_path / "build")

    assert m["format"] == snapshot.SNAPSHOT_FORMAT == 2
    assert m["extras"] == {
        "classify": 1,
        "market": 1,
        "universe": 1,
        "stock_basic.parquet": 1,
        "trade_calendar.csv": 1,
        "announcements.db": 1,
    }
    with zipfile.ZipFile(tmp_path / "build" / "bars.zip") as zf:
        names = set(zf.namelist())
    assert "bars/600000.parquet" in names
    assert "classify/2026-10/sw_l1/map.json" in names
    assert "market/2026-10-28/sentiment.json" in names
    assert "announcements.db" in names
    assert validate_manifest(m) == []


def test_extract_snapshot_v2_replaces_everything(tmp_path) -> None:
    """展开快照 v2 后行情与分类/公告都被覆盖，旧分类目录不残留。"""
    from stock_plan.data.snapshot import build_snapshot

    bars = _write_fake_processed(tmp_path / "processed")
    m = build_snapshot(bars, tmp_path / "build")
    target = tmp_path / "target"
    bars_t = _write_fake_processed(target, n_bars=1)
    (target / "classify" / "2026-09").mkdir(parents=True)
    (target / "classify" / "2026-09" / "old.json").write_text("{}", encoding="utf-8")

    counts = snapshot._extract_snapshot(
        tmp_path / "build" / "bars.zip", bars_dir=bars_t, processed_dir=target,
        expect={"bars_count": m["bars_count"], **m["extras"]},
    )

    assert counts["bars_count"] == 3
    assert len(list(bars_t.glob("*.parquet"))) == 3
    assert not (target / "classify" / "2026-09").exists()
    assert (target / "classify" / "2026-10" / "sw_l1" / "map.json").is_file()
    assert (target / "announcements.db").is_file()
    assert not (target / snapshot.STAGING_DIRNAME).exists()
    assert not list(target.glob("*.snapshot-old"))


def test_extract_snapshot_legacy_flat_zip_still_works(tmp_path) -> None:
    """旧快照（parquet 平铺在根、没有 extras）仍能恢复。"""
    import zipfile

    legacy = tmp_path / "legacy.zip"
    with zipfile.ZipFile(legacy, "w") as zf:
        zf.writestr("600000.parquet", b"x")
        zf.writestr("600001.parquet", b"y")
    bars = tmp_path / "processed" / "bars"

    counts = snapshot._extract_snapshot(legacy, bars_dir=bars, processed_dir=tmp_path / "processed")

    assert counts["bars_count"] == 2
    assert sorted(p.name for p in bars.glob("*.parquet")) == ["600000.parquet", "600001.parquet"]


def test_extract_snapshot_rolls_back_on_count_mismatch(tmp_path) -> None:
    """清单说 99 只但只有 3 只时必须在替换后回滚，旧数据保持原样。"""
    from stock_plan.data.snapshot import build_snapshot

    build_snapshot(_write_fake_processed(tmp_path / "processed"), tmp_path / "build")
    target = tmp_path / "target"
    bars_t = _write_fake_processed(target, n_bars=1)
    before = sorted(p.name for p in bars_t.glob("*.parquet"))

    with pytest.raises(ValueError):
        snapshot._extract_snapshot(
            tmp_path / "build" / "bars.zip", bars_dir=bars_t, processed_dir=target,
            expect={"bars_count": 99},
        )

    assert sorted(p.name for p in bars_t.glob("*.parquet")) == before


def test_missing_extras_reports_groups(tmp_path) -> None:
    _write_fake_processed(tmp_path)
    assert snapshot.missing_extras(tmp_path) == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert "announcements.db" in snapshot.missing_extras(empty)
    assert "classify" in snapshot.missing_extras(empty)


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

    def fake_restore(base, bars_dir, tmp_dir, progress=None, part_workers=4, max_age_days=None, **kwargs):
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


# ---------- 恢复地址同源 + 旧快照安全阀（2026-10-28） ----------
# 事故复盘：发布早已改到 GitHub Releases，但「从云端快照恢复」按钮写死 raw_base_url()，
# 读的是 2026-09-04 冻结的孤儿分支；恢复"成功"却把云端行情退回 2026-09-03，
# 且启动恢复因 bars>=100 直接跳过，于是云端数据永久停在旧快照。

def test_snapshot_restore_bases_release_first_and_deduped(monkeypatch) -> None:
    """恢复地址表：当前发布后端（Release）优先，旧分支兜底，且不重复。"""
    monkeypatch.delenv("STOCK_PLAN_SNAPSHOT_BACKEND", raising=False)
    monkeypatch.setenv("DATA_SNAPSHOT_REPO", "Cwywwy/Text-stock")
    bases = snapshot.snapshot_restore_bases()
    assert bases[0] == "https://github.com/Cwywwy/Text-stock/releases/download/data-snapshot"
    assert any("raw.githubusercontent.com/Cwywwy/Text-stock/data-snapshot" in b for b in bases)
    assert len(bases) == len(set(bases))


def test_update_center_restore_plan_uses_shared_bases(monkeypatch) -> None:
    """手动恢复必须与启动恢复共用同一份地址表（本次事故的直接回归点）。"""
    monkeypatch.delenv("STOCK_PLAN_SNAPSHOT_BACKEND", raising=False)
    monkeypatch.setenv("DATA_SNAPSHOT_REPO", "Cwywwy/Text-stock")
    from stock_plan.ui.views import update_center

    bases, max_age_days = update_center._snapshot_restore_plan()
    assert bases == snapshot.snapshot_restore_bases()
    assert bases[0].startswith("https://github.com/")
    assert max_age_days == snapshot.SNAPSHOT_MAX_AGE_DAYS > 0


def test_bootstrap_uses_shared_bases(monkeypatch, tmp_path) -> None:
    """启动恢复也用同一份地址表，不能再各自写死一串地址。"""
    monkeypatch.setattr(snapshot, "BARS_DIR", tmp_path / "bars")
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(snapshot, "cloud_secrets_env", lambda: None)
    seen: dict = {}

    def fake_restore(bases, bars_dir, tmp_dir, progress=None, part_workers=4, max_age_days=None, **kwargs):
        seen["bases"] = bases
        seen["max_age_days"] = max_age_days
        return {"bars_count": 1234}

    monkeypatch.setattr(snapshot, "restore_with_fallback", fake_restore)
    stats = snapshot.bootstrap_if_needed()
    assert stats["bars_count"] == 1234
    assert seen["bases"] == snapshot.snapshot_restore_bases()
    # 空数据时不必卡年龄：有旧快照也胜过没有
    assert seen["max_age_days"] is None


def _manifest(created_at: str) -> dict:
    return {
        "bars_count": 1,
        "zip_sha256": "x",
        "zip_bytes": 1,
        "created_at": created_at,
        "parts": [{"file": part_filename(0), "bytes": 1, "sha256": "y"}],
    }


def test_manifest_age_days_handles_missing_and_bad_values() -> None:
    now = datetime(2026, 10, 28, 12, 0, 0)
    assert snapshot.manifest_age_days(_manifest("2026-10-27 16:37:00"), now=now) == 0
    assert snapshot.manifest_age_days(_manifest("2026-09-04 02:50:51"), now=now) == 54
    assert snapshot.manifest_age_days({}, now=now) is None
    assert snapshot.manifest_age_days({"created_at": "not-a-date"}, now=now) is None


def test_ensure_manifest_fresh_rejects_stale_snapshot() -> None:
    """55 天前的旧快照必须被拒绝，而不是静默覆盖本地行情。"""
    stale = _manifest("2026-09-04 02:50:51")
    with pytest.raises(ValueError, match="快照过旧"):
        snapshot._ensure_manifest_fresh(stale, snapshot.SNAPSHOT_MAX_AGE_DAYS, "https://x/releases")
    snapshot._ensure_manifest_fresh(_manifest("2026-10-27 16:37:00"),
                                    snapshot.SNAPSHOT_MAX_AGE_DAYS, "https://x/releases")
    # 不设阈值时保持原行为（启动恢复空数据场景）
    snapshot._ensure_manifest_fresh(stale, None, "https://x/releases")
    # created_at 无法解析时不误伤
    snapshot._ensure_manifest_fresh({"created_at": "bad"}, 7, "https://x/releases")


def test_restore_with_fallback_forwards_max_age_and_skips_stale(monkeypatch, tmp_path) -> None:
    """旧快照所在地址失败后应继续尝试下一个，并把阈值透传下去。"""
    tried: list[tuple[str, int | None]] = []

    def fake(base, bars_dir, tmp_dir, progress=None, part_workers=4, max_age_days=None, **kwargs):
        tried.append((base, max_age_days))
        if "releases/download" in base:
            raise ValueError("快照过旧：生成于 2026-09-04，已 54 天（阈值 7 天）")
        return {"bars_count": 7}

    monkeypatch.setattr(snapshot, "restore_from_url", fake)
    stats = snapshot.restore_with_fallback(
        ["https://github.com/x/releases/download/t",
         "https://raw.githubusercontent.com/x/t"],
        tmp_path, tmp_path / "tmp", max_age_days=7,
    )
    assert stats["bars_count"] == 7
    assert tried == [("https://github.com/x/releases/download/t", 7),
                     ("https://raw.githubusercontent.com/x/t", 7)]


def _ready_bars(root, count: int = None):
    """造一个「行情已就绪」的 bars 目录（内容不影响 bootstrap 判定）。"""
    from pathlib import Path

    from stock_plan.data.snapshot import MIN_BARS_READY

    bars = Path(root) / "bars"
    bars.mkdir(parents=True, exist_ok=True)
    for i in range(count or (MIN_BARS_READY + 1)):
        (bars / f"{600000 + i}.parquet").write_bytes(b"x")
    return bars


def test_bootstrap_skips_when_bars_ready_locally(monkeypatch, tmp_path) -> None:
    """本机数据已就绪且不是云端时，启动不再下载 360MB 快照。"""
    _ready_bars(tmp_path)
    monkeypatch.setattr(snapshot, "BARS_DIR", tmp_path / "bars")
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(snapshot, "cloud_secrets_env", lambda: None)
    monkeypatch.setattr(snapshot, "is_cloud", lambda: False)

    def boom(*args, **kwargs):
        raise AssertionError("数据已就绪，不该触发恢复")

    monkeypatch.setattr(snapshot, "restore_with_fallback", boom)
    assert snapshot.bootstrap_if_needed() is None


def test_bootstrap_restores_missing_extras_on_cloud(monkeypatch, tmp_path) -> None:
    """云端旧版快照只有行情：分类/情绪缺失时启动应自动补齐，而不是让页面空着。"""
    _ready_bars(tmp_path)
    monkeypatch.setattr(snapshot, "BARS_DIR", tmp_path / "bars")
    monkeypatch.setattr(snapshot, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(snapshot, "cloud_secrets_env", lambda: None)
    monkeypatch.setattr(snapshot, "is_cloud", lambda: True)
    assert snapshot.missing_extras()  # 只有 bars，其余都缺

    seen: dict = {}

    def fake_restore(bases, bars_dir, tmp_dir, progress=None, part_workers=4, max_age_days=None, **kwargs):
        seen["called"] = True
        return {"bars_count": 10, "classify": 2}

    monkeypatch.setattr(snapshot, "restore_with_fallback", fake_restore)
    stats = snapshot.bootstrap_if_needed()
    assert seen["called"] is True
    assert stats["classify"] == 2
