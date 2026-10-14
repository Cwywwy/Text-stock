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
    2026-09-04  preserve shared strategy payloads with market snapshots
    2026-09-04  reuse local snapshot branch for repeated publications
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
# Release 的 tag（固定不变，因此下载基地址稳定；可用 DATA_SNAPSHOT_TAG 覆盖）
SNAPSHOT_TAG = "data-snapshot"
# 快照发布后端：release（默认，GitHub Releases 资产）或 branch（旧的孤儿分支）
DEFAULT_BACKEND = "release"
API_ROOT = "https://api.github.com"
UPLOAD_ROOT = "https://uploads.github.com"
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

        for key in ("STOCK_PLAN_CLOUD", "DATA_SNAPSHOT_REPO", "DATA_SNAPSHOT_TOKEN"):
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

def release_base_url(repo: str | None = None, tag: str | None = None) -> str:
    """GitHub Release 资产的下载基地址。

    与 raw_base_url 用法一致：拼 `{base}/manifest.json` 与 `{base}/{分卷名}` 即可下载 ——
    因为 Release 资产地址正是 `{base}/{资产名}`。相比孤儿分支，这条路的好处是：
    不往 git 历史里塞 300MB blob，且资产 URL 不经过 raw 的 CDN 缓存（force push 后
    短期内可能仍取到旧内容）。
    """
    repo = (repo or os.getenv("DATA_SNAPSHOT_REPO") or DEFAULT_REPO).strip("/")
    tag = tag or os.getenv("DATA_SNAPSHOT_TAG") or SNAPSHOT_TAG
    return f"https://github.com/{repo}/releases/download/{tag}"


def snapshot_base_url(backend: str | None = None) -> str:
    """按后端返回快照下载基地址（release 默认 / branch 为旧方案）。"""
    backend = (
        backend or os.getenv("STOCK_PLAN_SNAPSHOT_BACKEND") or DEFAULT_BACKEND
    ).strip().lower()
    return raw_base_url() if backend == "branch" else release_base_url()


# ---------- GitHub API（发布 Release 用） ----------

def _gh_request(
    method: str,
    url: str,
    token: str,
    body=None,
    content_type: str = "application/json",
    timeout: int = 120,
) -> dict:
    """调 GitHub REST API。body 为 dict 时按 JSON 发送，为 bytes 时按二进制发送。"""
    import urllib.error
    import urllib.request

    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8") if content_type == "application/json" else body
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "stock-plan-snapshot")
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"GitHub API {method} {url} 失败：HTTP {e.code} {detail}") from e
    return json.loads(raw.decode("utf-8")) if raw else {}


def _get_release(repo: str, tag: str, token: str) -> dict | None:
    """取指定 tag 的 Release；不存在返回 None。

    注意 404 在 _gh_request 里会被包成 RuntimeError，所以这里直接查列表再匹配 tag，
    避免把"还没有 Release"当成错误。
    """
    releases = _gh_request(f"GET", f"{API_ROOT}/repos/{repo}/releases?per_page=100", token)
    for rel in releases or []:
        if rel.get("tag_name") == tag:
            return rel
    return None


def _list_assets(repo: str, release_id: int, token: str) -> list[dict]:
    assets = _gh_request(
        "GET", f"{API_ROOT}/repos/{repo}/releases/{release_id}/assets?per_page=100", token
    )
    return assets or []


def _upload_asset(repo: str, release_id: int, name: str, content: bytes, token: str) -> dict:
    from urllib.parse import quote

    url = f"{UPLOAD_ROOT}/repos/{repo}/releases/{release_id}/assets?name={quote(name)}"
    return _gh_request("POST", url, token, body=content,
                       content_type="application/octet-stream", timeout=900)


def _delete_asset(repo: str, asset_id: int, token: str) -> None:
    _gh_request("DELETE", f"{API_ROOT}/repos/{repo}/releases/assets/{asset_id}", token)


def resolve_token(token: str | None = None) -> str:
    """取 GitHub token：显式参数 → 环境变量 → `.streamlit/secrets.toml`。

    最后这一层是必需的：本机计划任务（daily_job.ps1 → publish_snapshot.py）不经过
    Streamlit，读不到 `st.secrets`，若只认环境变量，定时发布会直接报缺少 token。
    """
    if token:
        return str(token).strip()
    env = (os.getenv("DATA_SNAPSHOT_TOKEN") or "").strip()
    if env:
        return env
    secrets = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    if secrets.exists():
        import re

        try:
            m = re.search(
                r'DATA_SNAPSHOT_TOKEN\s*=\s*"([^"]+)"',
                secrets.read_text(encoding="utf-8"),
            )
        except Exception:
            m = None
        if m:
            return m.group(1).strip()
    return ""


def publish_to_release(
    manifest: dict,
    build_dir: Path,
    extra_files: dict[str, bytes] | None = None,
    repo: str | None = None,
    tag: str | None = None,
    token: str | None = None,
) -> dict:
    """把快照发布为 GitHub Release 资产。

    **原子性靠 manifest 充当"提交指针"**，顺序刻意如下：
      1. 以**带版本号**的名字上传新分卷（`bars-<stamp>.partNN`）—— 不覆盖任何旧资产；
      2. 替换小体积的附带文件（如公共策略 JSON）；
      3. **替换 `manifest.json`** —— 这一步才是切换点，只有新分卷全部就位后才做；
      4. 删掉不再被引用的旧分卷与旧 manifest。
    因此上传中途失败时，线上仍是完整的旧快照；而成功切换后，Release 里只保留一份
    数据（约 300MB），不会像每天 force push 那样累积。
    """
    token = resolve_token(token)
    if not token:
        raise RuntimeError(
            "缺少 GitHub token，无法发布 Release。三种提供方式任选："
            "DATA_SNAPSHOT_TOKEN 环境变量、.streamlit/secrets.toml（本机已配置）、"
            "或 Streamlit Cloud 的 Secrets。"
        )
    repo = (repo or os.getenv("DATA_SNAPSHOT_REPO") or DEFAULT_REPO).strip("/")
    tag = tag or os.getenv("DATA_SNAPSHOT_TAG") or SNAPSHOT_TAG

    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    # 分卷改成带版本号的名字，manifest 里也要同步引用这个名字
    renamed_parts = []
    for p in manifest["parts"]:
        suffix = p["file"].rsplit("part", 1)[-1]
        renamed_parts.append({**p, "file": f"bars-{stamp}.part{suffix}"})
    release_manifest = {**manifest, "parts": renamed_parts, "tag": tag}

    rel = _get_release(repo, tag, token)
    if rel is None:
        rel = _gh_request(
            "POST", f"{API_ROOT}/repos/{repo}/releases", token,
            body={
                "tag_name": tag,
                "name": f"数据快照 {tag}",
                "body": "由本机 stock_plan 自动发布的行情数据快照（分卷 zip + manifest 双 sha256）。",
                "draft": False,
                "prerelease": False,
            },
        )
    release_id = rel["id"]
    existing = {a["name"]: a for a in _list_assets(repo, release_id, token)}

    # 1) 上传新分卷
    for p in renamed_parts:
        src = build_dir / p["file"].replace(f"bars-{stamp}.part", "bars.zip.part")
        _upload_asset(repo, release_id, p["file"], src.read_bytes(), token)

    # 2) 附带文件（体积小，直接替换）
    keep_names = {p["file"] for p in renamed_parts} | {"manifest.json"}
    for name, content in (extra_files or {}).items():
        if Path(name).name != name:
            raise ValueError(f"附加快照文件名不合法：{name}")
        if name in existing:
            _delete_asset(repo, existing[name]["id"], token)
        _upload_asset(repo, release_id, name, content, token)
        keep_names.add(name)

    # 3) 切换点：替换 manifest.json
    replaced = 0
    if "manifest.json" in existing:
        _delete_asset(repo, existing["manifest.json"]["id"], token)
        replaced += 1
    _upload_asset(
        repo, release_id, "manifest.json",
        json.dumps(release_manifest, ensure_ascii=False, indent=2).encode("utf-8"), token,
    )

    # 4) 清理被取代的旧资产（旧分卷等）
    for name, asset in existing.items():
        if name not in keep_names:
            _delete_asset(repo, asset["id"], token)
            replaced += 1

    return {
        "bars_count": manifest["bars_count"],
        "zip_bytes": manifest["zip_bytes"],
        "parts": len(renamed_parts),
        "backend": "release",
        "tag": tag,
        "release_id": release_id,
        "replaced_assets": replaced,
    }


def _publish_to_branch(
    build_dir: Path,
    manifest: dict,
    worktree_dir: Path | None = None,
    extra_files: dict[str, bytes] | None = None,
) -> dict:
    """旧的发布方式：force push 到 data-snapshot 孤儿分支（保留作为回退）。

    缺点是每天都往仓库里塞一份约 300MB 的 blob（GitHub 侧何时回收不可控），
    且 raw.githubusercontent.com 有 CDN 缓存，force push 后短期内可能仍取到旧快照。
    """
    wt = worktree_dir or (PROJECT_ROOT / ".snapshot_worktree")
    if wt.exists():
        shutil.rmtree(wt, ignore_errors=True)

    def _git(*args: str, cwd: Path = PROJECT_ROOT) -> None:
        subprocess_run(["git", *args], cwd)

    branch_exists = local_branch_exists(SNAPSHOT_BRANCH)
    if branch_exists:
        _git("worktree", "add", str(wt), SNAPSHOT_BRANCH)
    else:
        _git("worktree", "add", "--detach", str(wt))
    try:
        if not branch_exists:
            _git("checkout", "--orphan", SNAPSHOT_BRANCH, cwd=wt)
        _git("rm", "-rf", ".", cwd=wt)
        shutil.copy2(build_dir / "manifest.json", wt / "manifest.json")
        for p in manifest["parts"]:
            shutil.copy2(build_dir / p["file"], wt / p["file"])
        for name, content in (extra_files or {}).items():
            if Path(name).name != name:
                raise ValueError(f"附加快照文件名不合法：{name}")
            (wt / name).write_bytes(content)
        _git("add", "-A", cwd=wt)
        _git("-c", "user.name=snapshot-bot", "-c", "user.email=snapshot@local", "commit", "-m",
             f"data snapshot {manifest['created_at']} ({manifest['bars_count']} bars)", cwd=wt)
        _git("push", "--force", "origin", f"{SNAPSHOT_BRANCH}:{SNAPSHOT_BRANCH}", cwd=wt)
    finally:
        _git("worktree", "remove", "--force", str(wt))
        shutil.rmtree(build_dir, ignore_errors=True)

    return {"bars_count": manifest["bars_count"], "zip_bytes": manifest["zip_bytes"],
            "parts": len(manifest["parts"]), "backend": "branch", "branch": SNAPSHOT_BRANCH}


def publish(
    repo_url: str | None = None,
    worktree_dir: Path | None = None,
    extra_files: dict[str, bytes] | None = None,
    backend: str | None = None,
    build_only: bool = False,
) -> dict:
    """打包快照并按后端发布。

    backend 默认取 `STOCK_PLAN_SNAPSHOT_BACKEND`，未设置时用 **GitHub Releases**
    （`publish_to_release`）；传 `"branch"` 可回退到旧的孤儿分支方式。
    `build_only=True` 时只打包不发布（测试/排查用）。
    """
    from stock_plan.data.updater import LOG_DIR

    build_dir = LOG_DIR / "snapshot_build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    manifest = build_snapshot(BARS_DIR, build_dir)

    if build_only:
        return {
            "bars_count": manifest["bars_count"],
            "zip_bytes": manifest["zip_bytes"],
            "parts": len(manifest["parts"]),
            "backend": "build-only",
            "build_dir": str(build_dir),
        }

    resolved = (backend or os.getenv("STOCK_PLAN_SNAPSHOT_BACKEND") or DEFAULT_BACKEND).strip().lower()
    if resolved == "branch":
        return _publish_to_branch(build_dir, manifest, worktree_dir, extra_files)
    try:
        return publish_to_release(manifest, build_dir, extra_files)
    finally:
        shutil.rmtree(build_dir, ignore_errors=True)



def local_branch_exists(branch: str) -> bool:
    """返回本地是否已有指定分支；异常 git 状态明确报错。"""
    import subprocess

    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    if result.returncode in (0, 1):
        return result.returncode == 0
    raise RuntimeError(f"git 检查本地分支失败: {result.stderr.strip()[:400]}")


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


def restore_with_fallback(
    bases: list[str],
    bars_dir: Path,
    tmp_dir: Path,
    progress=None,
    part_workers: int = 4,
) -> dict:
    """依次尝试多个快照基地址，返回第一个成功的结果。

    用于发布后端切换期：优先取 GitHub Release 资产，取不到时回退到旧的
    data-snapshot 分支 —— 这样"改了发布方式"不会让云端在迁移窗口内拿不到数据。
    """
    errors: list[str] = []
    for base in bases:
        try:
            return restore_from_url(base, bars_dir, tmp_dir, progress=progress,
                                    part_workers=part_workers)
        except Exception as e:  # 单个地址失败就换下一个
            errors.append(f"{base} → {str(e)[:140]}")
    raise RuntimeError("所有快照地址均恢复失败：" + " ｜ ".join(errors))


def bootstrap_if_needed(ui: bool = False) -> dict | None:
    """app 启动恢复入口：bars 为空且快照可达时执行恢复。

    ui=True 时用 st.status/progress 展示；否则静默（CLI 用）。
    返回恢复统计；数据已存在或快照不可用时返回 None。
    """
    cloud_secrets_env()
    if BARS_DIR.exists() and len(list(BARS_DIR.glob("*.parquet"))) >= MIN_BARS_READY:
        return None

    # 首选当前后端（默认 Release），失败再回退到另一种地址
    bases: list[str] = [snapshot_base_url()]
    for alt in (release_base_url(), raw_base_url()):
        if alt not in bases:
            bases.append(alt)

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
                stats = restore_with_fallback(bases, BARS_DIR, tmp_dir, progress=_report)
                status.update(label="✅ 数据快照恢复完成", state="complete", expanded=False)
            return stats
        except Exception as e:
            import streamlit as st

            st.warning(f"数据快照恢复失败：{e}。可稍后在「🔄 数据更新」页重试或手动增量更新。")
            return None
    return restore_with_fallback(bases, BARS_DIR, tmp_dir, progress=_report)
