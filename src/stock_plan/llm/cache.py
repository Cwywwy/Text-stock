"""LLM 结果落盘缓存：同一任务 + 同一输入在 TTL 内直接复用，避免重复烧额度。

文件：src/stock_plan/llm/cache.py
作者：Cwywwy
用途：信号解释、每日复盘这类「输入只跟当天数据有关」的调用，一天里可能被点很多次；
      不缓存就会重复付费、重复触发服务商限流（429）。这里以
      ``sha1(task + payload)`` 为键存到 ``data/cache/llm/<key>.json``，
      文件名即内容指纹，可随时整目录删除重建。
所属模块：llm（基础设施层）

环境变量：
- ``LLM_CACHE_DISABLE=1``  关闭读写（测试或强制刷新时用）
- ``LLM_CACHE_TTL_HOURS``  覆盖默认 TTL（168 小时 = 7 天）

注意：缓存目录在 ``data/`` 下，已被 .gitignore 覆盖，不得入库。

修订记录：
2026-10-28 初版：make_key / load / store / clear / stats，原子写入 + 过期清理（Phase 0）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from stock_plan.llm.config import PROJECT_ROOT

#: 默认 TTL：7 天（策略参数不变时，同一批数据一周内复用足够）
DEFAULT_TTL_HOURS = 168.0

#: 缓存根目录（测试里 monkeypatch 本变量或显式传 cache_dir）
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "llm"


def enabled() -> bool:
    """缓存是否启用（``LLM_CACHE_DISABLE=1`` 时关闭）。"""
    return os.getenv("LLM_CACHE_DISABLE", "").strip().lower() not in {"1", "true", "yes", "on"}


def ttl_hours() -> float:
    """当前 TTL（小时）；环境变量非法时回退默认值。"""
    raw = os.getenv("LLM_CACHE_TTL_HOURS", "").strip()
    if not raw:
        return DEFAULT_TTL_HOURS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TTL_HOURS
    return value if value > 0 else DEFAULT_TTL_HOURS


def make_key(task: str, payload: str) -> str:
    """由任务名 + 输入内容生成稳定键（sha1 十六进制）。"""
    digest = hashlib.sha1()
    digest.update(task.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(payload.encode("utf-8"))
    return digest.hexdigest()


def cache_path(key: str, *, cache_dir: Path | None = None) -> Path:
    """键对应的文件路径。"""
    return Path(cache_dir or CACHE_DIR) / f"{key}.json"


@dataclass(frozen=True)
class CacheEntry:
    """一条缓存记录。"""

    key: str
    text: str
    created_at: float
    meta: dict[str, Any]
    path: Path | None = None

    @property
    def age_hours(self) -> float:
        """距今小时数（负数按 0 处理，防系统时钟回拨）。"""
        return max(0.0, (time.time() - self.created_at) / 3600.0)

    @property
    def age_minutes(self) -> float:
        return self.age_hours * 60.0

    def describe_age(self) -> str:
        """人类可读的新鲜度，如「刚刚」「12 分钟前」「3 小时前」。"""
        minutes = self.age_minutes
        if minutes < 1:
            return "刚刚"
        if minutes < 60:
            return f"{int(minutes)} 分钟前"
        hours = self.age_hours
        if hours < 48:
            return f"{int(hours)} 小时前"
        return f"{int(hours / 24)} 天前"


def load(
    task: str,
    payload: str,
    *,
    cache_dir: Path | None = None,
    now: float | None = None,
) -> CacheEntry | None:
    """读取缓存；未命中、已过期或文件损坏都返回 None。

    损坏文件会被删除（否则每次调用都要解析一遍坏 JSON）。
    """
    if not enabled():
        return None
    key = make_key(task, payload)
    path = cache_path(key, cache_dir=cache_dir)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _discard(path)
        return None

    text = raw.get("text")
    created = raw.get("created_at")
    if not isinstance(text, str) or not isinstance(created, (int, float)):
        _discard(path)
        return None

    current = time.time() if now is None else float(now)
    age_hours = max(0.0, (current - float(created)) / 3600.0)
    if age_hours > ttl_hours():
        _discard(path)
        return None

    meta = raw.get("meta")
    return CacheEntry(
        key=key,
        text=text,
        created_at=float(created),
        meta=meta if isinstance(meta, dict) else {},
        path=path,
    )


def store(
    task: str,
    payload: str,
    *,
    text: str,
    meta: Mapping[str, Any] | None = None,
    cache_dir: Path | None = None,
) -> CacheEntry | None:
    """写入缓存（原子替换）。禁用缓存或空文本时返回 None。"""
    if not enabled() or not text:
        return None
    key = make_key(task, payload)
    path = cache_path(key, cache_dir=cache_dir)
    created = time.time()
    record = {
        "task": task,
        "key": key,
        "created_at": created,
        "created_at_iso": datetime.fromtimestamp(created, tz=timezone.utc).isoformat(),
        "text": text,
        "meta": dict(meta or {}),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, path)
    except OSError:
        return None
    return CacheEntry(
        key=key,
        text=text,
        created_at=created,
        meta=dict(meta or {}),
        path=path,
    )


def clear(*, cache_dir: Path | None = None) -> int:
    """删除全部缓存文件，返回删除条数。"""
    directory = Path(cache_dir or CACHE_DIR)
    if not directory.exists():
        return 0
    removed = 0
    for item in directory.glob("*.json"):
        try:
            item.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def stats(*, cache_dir: Path | None = None) -> dict[str, Any]:
    """缓存概况（条目数 / 占用字节），供设置页或排查使用。"""
    directory = Path(cache_dir or CACHE_DIR)
    if not directory.exists():
        return {"entries": 0, "bytes": 0, "dir": str(directory), "enabled": enabled()}
    entries = 0
    size = 0
    for item in directory.glob("*.json"):
        entries += 1
        try:
            size += item.stat().st_size
        except OSError:
            continue
    return {
        "entries": entries,
        "bytes": size,
        "dir": str(directory),
        "enabled": enabled(),
        "ttl_hours": ttl_hours(),
    }


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
