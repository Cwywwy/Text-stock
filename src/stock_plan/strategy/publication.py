# -*- coding: utf-8 -*-
"""共享策略发布服务 — 云端登记、本机全市场计算、云端只读结果。

所有策略均为公开内容。云端仅把待发布策略登记到 data-snapshot 分支；
本机在盘后任务或 localhost 启动时计算全 A 股信号，再随快照发布。

Revision History:
    2026-09-04  create shared strategy request and signal publication flow
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime

from stock_plan.data.snapshot import DEFAULT_REPO, SNAPSHOT_BRANCH, is_cloud, raw_base_url

PUBLIC_STRATEGIES_FILE = "public_strategies.json"
PUBLIC_SIGNALS_FILE = "public_strategy_signals.json"
MAX_STRATEGY_NAME_LENGTH = 30


class RemoteContentNotFoundError(RuntimeError):
    """GitHub Contents API 指定文件不存在。"""


def strategy_fingerprint(config: dict) -> str:
    """返回稳定配置指纹，用于阻止展示与当前配置不一致的旧信号。"""
    payload = json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_remote_json(filename: str) -> dict:
    """读取 data-snapshot 中的小型 JSON；文件尚未创建时返回空对象。"""
    url = f"{raw_base_url()}/{filename}"
    request = urllib.request.Request(url, headers={"User-Agent": "stock-plan-publication"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise RuntimeError(f"读取共享策略文件失败（HTTP {error.code}）：{filename}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"无法连接共享策略服务：{error.reason}") from error
    if not isinstance(data, dict):
        raise ValueError(f"共享策略文件格式错误：{filename}")
    return data


def load_public_strategies() -> dict[str, dict]:
    """读取全部公共策略请求，按名称返回记录。"""
    records = _read_remote_json(PUBLIC_STRATEGIES_FILE).get("strategies", {})
    if not isinstance(records, dict):
        raise ValueError("共享策略记录格式错误：strategies 必须为对象")
    return records


def load_public_signals() -> dict[str, dict]:
    """读取已发布的公共策略信号，按策略名称返回记录。"""
    signals = _read_remote_json(PUBLIC_SIGNALS_FILE).get("signals", {})
    if not isinstance(signals, dict):
        raise ValueError("共享策略信号格式错误：signals 必须为对象")
    return signals


def _github_json_request(method: str, url: str, token: str, body: dict | None = None) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "stock-plan-publication",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:300]
        if error.code == 404:
            raise RemoteContentNotFoundError(f"GitHub 文件不存在：{url}") from error
        raise RuntimeError(f"GitHub 共享策略请求失败（HTTP {error.code}）：{detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"无法连接 GitHub：{error.reason}") from error
    if not isinstance(payload, dict):
        raise ValueError("GitHub 返回了无效的 JSON 响应")
    return payload


def _write_remote_json(filename: str, payload: dict) -> None:
    """使用细粒度令牌原子更新 data-snapshot 分支中的一个 JSON 文件。"""
    token = os.getenv("DATA_SNAPSHOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "未配置 DATA_SNAPSHOT_TOKEN。请在 Streamlit Secrets 或本机环境变量中设置"
            "仅允许 Contents 读写的 GitHub 细粒度令牌。"
        )
    repo = (os.getenv("DATA_SNAPSHOT_REPO") or DEFAULT_REPO).strip("/")
    url = f"https://api.github.com/repos/{repo}/contents/{filename}"
    sha = None
    try:
        current = _github_json_request("GET", f"{url}?ref={SNAPSHOT_BRANCH}", token)
        sha = current.get("sha")
    except RemoteContentNotFoundError:
        pass
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    body = {
        "message": f"update shared strategy {filename}",
        "content": base64.b64encode(content).decode("ascii"),
        "branch": SNAPSHOT_BRANCH,
    }
    if sha:
        body["sha"] = sha
    _github_json_request("PUT", url, token, body)


def submit_public_strategy(
    name: str,
    config: dict,
    source: str,
    proposal_code: str = "",
    unsupported: list | None = None,
    notes: str = "",
) -> dict:
    """提交一个不可覆盖的公共策略，并标记为待全市场发布。"""
    name = (name or "").strip()
    if not 2 <= len(name) <= MAX_STRATEGY_NAME_LENGTH:
        raise ValueError(f"策略名称需为 2 至 {MAX_STRATEGY_NAME_LENGTH} 个字符")
    if not config:
        raise ValueError("策略配置不能为空")

    records = load_public_strategies()
    if name in records:
        raise ValueError(f"策略名称「{name}」已存在；公共策略不可覆盖，请使用新名称。")
    now = datetime.now().isoformat(timespec="seconds")
    record = {
        "name": name,
        "config": config,
        "fingerprint": strategy_fingerprint(config),
        "source": source,
        "proposal_code": proposal_code,
        "unsupported": unsupported or [],
        "notes": notes,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
    }
    records[name] = record
    _write_remote_json(PUBLIC_STRATEGIES_FILE, {"strategies": records})
    return record


def sync_public_strategies_to_local() -> int:
    """把远端公共策略镜像到当前运行实例的 SQLite，供下拉框和策略管理页使用。"""
    from stock_plan.strategy import store

    records = load_public_strategies()
    for record in records.values():
        store.save_strategy(
            record["name"], record["config"], source=record.get("source", "manual"),
            proposal_code=record.get("proposal_code", ""),
            unsupported=record.get("unsupported") or [], notes=record.get("notes", ""),
        )
    return len(records)


def prepare_pending_publications() -> tuple[dict[str, bytes], int]:
    """本机计算全部待发布公共策略，返回应随行情快照发布的 JSON 文件。"""
    if is_cloud():
        raise RuntimeError("云端不能执行全 A 股策略发布任务")

    from stock_plan.signal.generator import generate_signals
    from stock_plan.strategy.custom import CustomStrategy

    records = load_public_strategies()
    published_signals = load_public_signals()
    completed = 0
    for name, record in records.items():
        if record.get("status") != "pending":
            continue
        signals = generate_signals(strategy=CustomStrategy(record["config"]), top_n=20)
        now = datetime.now().isoformat(timespec="seconds")
        record["status"] = "published"
        record["published_at"] = now
        record["market_date"] = date_of_latest_signal(signals)
        record["updated_at"] = now
        published_signals[name] = {
            "fingerprint": record["fingerprint"],
            "published_at": now,
            "market_date": record["market_date"],
            "signals": [asdict(signal) for signal in signals],
        }
        completed += 1

    requests_payload = json.dumps({"strategies": records}, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    signals_payload = json.dumps({"signals": published_signals}, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    return {
        PUBLIC_STRATEGIES_FILE: requests_payload,
        PUBLIC_SIGNALS_FILE: signals_payload,
    }, completed


def date_of_latest_signal(signals: list) -> str:
    """信号采用运行时最近收盘数据；结果为空时也保留生成日期以便可追溯。"""
    del signals
    return datetime.now().date().isoformat()
