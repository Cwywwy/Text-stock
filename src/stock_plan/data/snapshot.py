# -*- coding: utf-8 -*-
"""数据快照持久化 — 云端容器磁盘临时盘问题的解决方案。

架构（快照以本机为权威源，云端只读恢复）：
    本机 data/processed/bars (~300MB parquet)
      → build_snapshot(): 打包 zip 分卷（每卷 <90MB，GitHub 单文件 100MB 硬限制）
      → publish(): git worktree 孤儿分支 data-snapshot，单 commit force push
      → GitHub raw URL 分发

    云端启动 / 手动触发
      → bootstrap_if_needed(): bars 为空时下载分卷 → sha256 校验 → 解压落盘

云端轻量模式（方案 2）：
    is_cloud() 检测（STOCK_PLAN_CLOUD=1 显式指定，或 Streamlit Cloud 特征），
    更新任务 workers 8→3、全量拉取默认 5 年→1 年，避免 2.7GB 内存容器超限。

Revision History:
    2026-09-04  new: snapshot build/publish/restore + cloud mode detect
    2026-09-04  add CLOUD_MAX_CODES cap for backtest/signal universe
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from stock_plan.data.storage import PROJECT_ROOT

BARS_DIR = PROJECT_ROOT / "data" / "processed" / "bars"
SNAPSHOT_BRANCH = "data-snapshot"
# 默认发布/恢复目标仓库（与部署仓库一致；可用环境变量 DATA_SNAPSHOT_REPO 覆盖）
DEFAULT_REPO = "Cwywwy/Text-stock"
# GitHub raw 分卷单文件硬限制 100MB，取 90MB 留余量
PART_SIZE = 90 * 1024 * 1024
PART_TMPL = "bars.zip.part{:02d}"
# 本地 bars 少于此数视为"无数据"，触发恢复
MIN_BARS_READY = 100
# 云端轻量模式参数
CLOUD_WORKERS = 3
CLOUD_FULL_DAYS = 366
# 云端回测/信号参与计算的股票数上限（全市场约 5500 只会撑爆容器内存）
CLOUD_MAX_CODES = 1800


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def part_filename(index: int) -> str:
    return PART_TMPL.format(index)


def raw_base_url(repo: str | None = None, branch: str | None = None) -> str:
    """快照分卷的 raw 下载基地址（可经 DATA_SNAPSHOT_REPO 覆盖）。"""
    repo = (repo or os.getenv("DATA_SNAPSHOT_REPO") or DEFAULT_REPO).strip("/")
    return f"https://raw.githubusercontent.com/{repo}/{branch or SNAPSHOT_BRANCH}"


def is_cloud() -> bool:
    """云端轻量模式检测：显式环境变量优先，其次 Streamlit Cloud 特征。"""
    explicit = os.getenv("STOCK_PLAN_CLOUD", "").strip()
    if explicit:
        return explicit == "1"
    # Streamlit Community Cloud 特征环境变量（尽力检测）
    for key in ("STREAMLIT_CLOUD", "STREAMLIT_SHARING_MODE"):
        if os.getenv(key):
            return True
    host = (os.getenv("HOSTNAME") or "").lower()
    return host.startswith("app-") and "streamlit" in host


def cloud_secrets_env() -> None:
    """把 st.secrets 中的运行配置落到环境变量（云端 Secrets → 进程可见）。"""
    try:
        import streamlit as st

        for key in ("STOCK_PLAN_CLOUD", "DATA_SNAPSHOT_REPO"):
            if key in st.secrets and not os.getenv(key):
                os.environ[key] = str(st.secrets[key])
    except Exception:
        pass


# ---------- 打包与清单（纯函数，便于测试） ----------

def build_snapshot(bars_dir: Path, out_dir: Path, part_size: int = PART_SIZE) -> dict:
    """打包 bars 目录为单 zip 并二进制分卷，返回 manifest dict。

    manifest.parts 每项含文件名/字节数/sha256；zip_sha256 为整包校验值。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    n_files = len(list(bars_dir.glob("*.parquet")))
    if n_files == 0:
        raise ValueError(f"{bars_dir} 下没有 parquet 文件，无法打包快照")

    zip_path = out_dir / "bars.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for pq in sorted(bars_dir.glob("*.parquet")):
            zf.write(pq, pq.name)

    parts: list[dict] = []
    with zip_path.open("rb") as f:
        index = 0
        while True:
            block = f.read(part_size)
            if not block:
                break
            part_path = out_dir / part_filename(index)
            part_path.write_bytes(block)
            parts.append({"file": part_filename(index), "bytes": len(block), "sha256": _sha256_file(part_path)})
            index += 1

    manifest = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "bars_count": n_files,
        "zip_bytes": zip_path.stat().st_size,
        "zip_sha256": _sha256_file(zip_path),
        "parts": parts,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def validate_manifest(manifest: dict) -> list[str]:
    """校验 manifest 结构，返回问题列表（空列表 = 通过）。"""
    problems: list[str] = []
    if not isinstance(manifest, dict):
        return ["manifest 不是 JSON 对象"]
    for key in ("bars_count", "zip_sha256", "parts"):
        if key not in manifest:
            problems.append(f"缺少字段 {key}")
    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        problems.append("parts 为空或不是列表")
    else:
        for i, p in enumerate(parts):
            for k in ("file", "bytes", "sha256"):
                if k not in p:
                    problems.append(f"parts[{i}] 缺少 {k}")
            if p.get("file") != part_filename(i):
                problems.append(f"parts[{i}].file 应为 {part_filename(i)}，实际 {p.get('file')}")
    return problems


# ---------- 发布（本机） ----------

def publish(repo_url: str | None = None, worktree_dir: Path | None = None) -> dict:
    """打包并 force push 到 data-snapshot 孤儿分支（单 commit，不占仓库历史）。

    返回 {"bars_count", "zip_bytes", "parts", "branch"}。
    """
    from stock_plan.data.updater import LOG_DIR

    build_dir = LOG_DIR / "snapshot_build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    manifest = build_snapshot(BARS_DIR, build_dir)

    wt = worktree_dir or (PROJECT_ROOT / ".snapshot_worktree")
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)

    def _git(*args: str, cwd: Path = PROJECT_ROOT) -> None:
        subprocess_run(["git", *args], cwd)

    _git("worktree", "add", "--detach", str(wt))
    try:
        _git("checkout", "--orphan", SNAPSHOT_BRANCH, cwd=wt)
        _git("rm", "-rf", ".", cwd=wt)
        shutil.copy2(build_dir / "manifest.json", wt / "manifest.json")
        for p in manifest["parts"]:
            shutil.copy2(build_dir / p["file"], wt / p["file"])
        _git("add", "-A", cwd=wt)
        _git("-c", "user.name=snapshot-bot", "-c", "user.email=snapshot@local", "commit", "-m",
             f"data snapshot {manifest['created_at']} ({manifest['bars_count']} bars)", cwd=wt)
        _git("push", "--force", "origin", f"{SNAPSHOT_BRANCH}:{SNAPSHOT_BRANCH}", cwd=wt)
    finally:
        _git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(build_dir, ignore_errors=True)

    return {"bars_count": manifest["bars_count"], "zip_bytes": manifest["zip_bytes"],
            "parts": len(manifest["parts"]), "branch": SNAPSHOT_BRANCH}


def subprocess_run(cmd: list[str], cwd: Path) -> None:
    import subprocess

    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", shell=False)
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} 失败: {r.stderr.strip()[:400]}")


# ---------- 恢复（云端/空数据环境） ----------

def _download(url: str, dest: Path, timeout: int = 120) -> None:
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "stock-plan-snapshot"})
    with urllib.request.urlopen(req, timeout=timeout) as resp, dest.open("wb") as f:
        while True:
            block = resp.read(1024 * 1024)
            if not block:
                break
            f.write(block)


def restore_from_url(base_url: str, bars_dir: Path, tmp_dir: Path,
                     progress=None, part_workers: int = 4) -> dict:
    """从 raw 基地址下载分卷 → 校验 → 解压到 bars_dir。返回恢复统计。

    progress(done_bytes, total_bytes) 可选；任一分卷校验失败即整体失败，
    不污染既有数据（先落临时目录，成功后再原子替换）。
    """
    import urllib.request

    manifest_url = f"{base_url}/manifest.json"
    with urllib.request.urlopen(
        urllib.request.Request(manifest_url, headers={"User-Agent": "stock-plan-snapshot"}), timeout=60
    ) as resp:
        manifest = json.loads(resp.read().decode("utf-8"))

    problems = validate_manifest(manifest)
    if problems:
        raise ValueError("快照 manifest 校验失败: " + "; ".join(problems[:3]))

    tmp_dir.mkdir(parents=True, exist_ok=True)
    parts = manifest["parts"]
    total = sum(p["bytes"] for p in parts)
    done = 0

    def _pull(p: dict) -> tuple[dict, int]:
        dest = tmp_dir / p["file"]
        _download(f"{base_url}/{p['file']}", dest)
        got = _sha256_file(dest)
        if got != p["sha256"]:
            raise ValueError(f"分卷 {p['file']} sha256 不符（网络传输损坏？）")
        return p, dest.stat().st_size

    with ThreadPoolExecutor(max_workers=part_workers) as pool:
        futures = [pool.submit(_pull, p) for p in parts]
        for fut in as_completed(futures):
            _, size = fut.result()
            done += size
            if progress:
                progress(min(done, total), total)

    if progress:
        progress(total, total)

    # 整包校验：按序拼接分卷应等于 zip_sha256
    zip_tmp = tmp_dir / "bars.zip"
    with zip_tmp.open("wb") as out:
        for p in parts:
            out.write((tmp_dir / p["file"]).read_bytes())
    if _sha256_file(zip_tmp) != manifest["zip_sha256"]:
        raise ValueError("整包 sha256 校验失败")

    # 原子替换：旧数据挪走 → 解压 → 清理
    bars_dir.parent.mkdir(parents=True, exist_ok=True)
    backup = bars_dir.with_name("bars_backup_old")
    if backup.exists():
        shutil.rmtree(backup)
    if bars_dir.exists():
        bars_dir.rename(backup)
    try:
        with zipfile.ZipFile(zip_tmp) as zf:
            zf.extractall(bars_dir)
        n = len(list(bars_dir.glob("*.parquet")))
        if n < manifest["bars_count"]:
            raise ValueError(f"解压后文件数 {n} 少于清单 {manifest['bars_count']}")
    except Exception:
        if backup.exists() and not bars_dir.exists():
            backup.rename(bars_dir)
        raise
    finally:
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {"bars_count": manifest["bars_count"], "zip_bytes": manifest["zip_bytes"],
            "created_at": manifest["created_at"], "restored_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def bootstrap_if_needed(ui: bool = False) -> dict | None:
    """app 启动恢复入口：bars 为空且快照可达时执行恢复。

    ui=True 时用 st.status/progress 展示；否则静默（CLI 用）。
    返回恢复统计；数据已存在或快照不可用时返回 None。
    """
    cloud_secrets_env()
    if BARS_DIR.exists() and len(list(BARS_DIR.glob("*.parquet"))) >= MIN_BARS_READY:
        return None

    base = raw_base_url()

    def _report(done: int, total: int) -> None:
        if not ui:
            return
        import streamlit as st

        pct = min(done / total, 1.0) if total else 0.0
        prog = st.session_state.get("_snapshot_progress")
        if prog is not None:
            prog.progress(pct, text=f"⬇️ 下载行情数据快照 {done / 1048576:.0f}/{total / 1048576:.0f} MB")

    tmp_dir = PROJECT_ROOT / "data" / "snapshot_tmp"
    if ui:
        import streamlit as st

        try:
            with st.status("⬇️ 正在从云端快照恢复行情数据（首次访问约 1~3 分钟）…", expanded=True) as status:
                st.write("检测到本地无缓存数据，正在自动恢复，完成后页面自动可用。")
                st.session_state["_snapshot_progress"] = st.progress(0.0)
                stats = restore_from_url(base, BARS_DIR, tmp_dir, progress=_report)
                status.update(label="✅ 数据快照恢复完成", state="complete", expanded=False)
            return stats
        except Exception as e:
            import streamlit as st

            st.warning(f"数据快照恢复失败：{e}。可稍后在「🔄 数据更新」页重试或手动增量更新。")
            return None
    return restore_from_url(base, BARS_DIR, tmp_dir, progress=_report)
