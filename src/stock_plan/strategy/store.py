# -*- coding: utf-8 -*-
"""策略持久化存储 — LLM 生成 / 拼装页保存的自定义策略统一入库（SQLite）。

保存后的策略在今日信号 / 回测 / 策略对比页的下拉框立即可选。

Revision History:
    2026-10-15  新增 tags 列（策略分组）与批量删除 delete_strategies；
                老库通过 PRAGMA table_info + ALTER TABLE 轻量迁移，已有策略不丢。
    2026-10-15  新增 owner 列与「当前客户」概念（暂时需求 4 的"分客户保存"）。
                ⚠️ owner 只是**便于分类**的自由标签，**不是安全边界**，
                不能用于对外服务的权限控制（详见
                .github/plans/2026-10-15-3-new-strategy-grouping-and-per-customer.md）。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STRATEGY_DB = PROJECT_ROOT / "data" / "db" / "strategies.db"

# 默认归属：本机单用户场景下的"客户"
DEFAULT_OWNER = "local"
_SETTING_CURRENT_OWNER = "current_owner"


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """轻量迁移：老库缺列时补上（SQLite 没有 ADD COLUMN IF NOT EXISTS）。"""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _conn() -> sqlite3.Connection:
    STRATEGY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STRATEGY_DB)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS strategies (
            name TEXT PRIMARY KEY,
            config_json TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            proposal_code TEXT DEFAULT '',
            unsupported_json TEXT DEFAULT '[]',
            notes TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            owner TEXT DEFAULT 'local',
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    # 键值设置表（当前存放"当前客户"），与策略同库以便一起备份/迁移
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )
    # 老库迁移：tags / owner 都是 2026-10-15 新增
    _ensure_column(conn, "strategies", "tags", "TEXT DEFAULT ''")
    _ensure_column(conn, "strategies", "owner", "TEXT DEFAULT 'local'")
    return conn


# ---------- 设置（当前客户） ----------

def get_setting(key: str, default: str = "") -> str:
    with _conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row is not None and row[0] is not None else default


def set_setting(key: str, value: str) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def current_owner() -> str:
    """当前"客户"（归属标签）。未设置时为 `local`。

    ⚠️ 这只是便于分类的自由字符串，**不是身份认证**。
    """
    return (get_setting(_SETTING_CURRENT_OWNER, DEFAULT_OWNER) or DEFAULT_OWNER).strip() or DEFAULT_OWNER


def set_current_owner(owner: str) -> str:
    name = (owner or "").strip() or DEFAULT_OWNER
    set_setting(_SETTING_CURRENT_OWNER, name)
    return name


def all_owners() -> list[str]:
    """出现过的全部归属（去重，包含当前客户）。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT owner FROM strategies WHERE owner IS NOT NULL AND owner != ''"
            " ORDER BY owner"
        ).fetchall()
    owners = [r[0] for r in rows]
    cur = current_owner()
    if cur not in owners:
        owners.append(cur)
    return owners


def normalize_tags(raw) -> str:
    """把标签规范成逗号分隔的去重字符串。

    接受 "a,b" 字符串或 ["a","b"] 列表；去空白、去重、保持出现顺序。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        parts = raw.replace("，", ",").split(",")
    else:
        parts = [str(x) for x in raw]
    seen: list[str] = []
    for p in parts:
        t = str(p).strip()
        if t and t not in seen:
            seen.append(t)
    return ",".join(seen)


def save_strategy(
    name: str,
    config: dict,
    source: str = "manual",
    proposal_code: str = "",
    unsupported: list | None = None,
    notes: str = "",
    tags="",
    owner: str | None = None,
) -> bool:
    """保存/覆盖一个策略（按名字主键）。

    tags 为分组标签（逗号分隔字符串或列表）；owner 为归属（客户），
    不传则用"当前客户"。

    **归属冲突规则**：同名策略已存在时**不改变其 owner** —— 避免 A 客户保存
    同名策略时把 B 客户的策略悄悄划到自己名下。要改归属请显式调 `set_owner()`。
    """
    name = (name or "").strip()
    if not name or not config:
        return False
    owner = (owner if owner is not None else current_owner()).strip() or DEFAULT_OWNER
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        conn.execute(
            """INSERT INTO strategies
               (name, config_json, source, proposal_code, unsupported_json, notes, tags, owner,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 config_json=excluded.config_json, source=excluded.source,
                 proposal_code=excluded.proposal_code,
                 unsupported_json=excluded.unsupported_json, notes=excluded.notes,
                 tags=excluded.tags,
                 updated_at=excluded.updated_at""",
            (
                name,
                json.dumps(config, ensure_ascii=False),
                source,
                proposal_code or "",
                json.dumps(unsupported or [], ensure_ascii=False),
                notes or "",
                normalize_tags(tags),
                owner,
                now,
                now,
            ),
        )
    return True


def set_owner(name: str, owner: str) -> bool:
    """显式改某个策略的归属（客户）。"""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE strategies SET owner = ?, updated_at = ? WHERE name = ?",
            ((owner or "").strip() or DEFAULT_OWNER,
             datetime.now().isoformat(timespec="seconds"), name),
        )
    return cur.rowcount > 0


def set_tags(name: str, tags) -> bool:
    """只更新某个策略的标签（策略管理页用）。"""
    with _conn() as conn:
        cur = conn.execute(
            "UPDATE strategies SET tags = ?, updated_at = ? WHERE name = ?",
            (normalize_tags(tags), datetime.now().isoformat(timespec="seconds"), name),
        )
    return cur.rowcount > 0


def load_strategy(name: str) -> dict | None:
    """按名字取策略记录；不存在返回 None。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT name, config_json, source, proposal_code, unsupported_json, notes,"
            " tags, owner, created_at FROM strategies WHERE name = ?",
            (name,),
        ).fetchone()
    if row is None:
        return None
    return {
        "name": row[0],
        "config": json.loads(row[1]),
        "source": row[2],
        "proposal_code": row[3],
        "unsupported": json.loads(row[4]),
        "notes": row[5],
        "tags": row[6] or "",
        "owner": row[7] or DEFAULT_OWNER,
        "created_at": row[8],
    }


def list_strategies(tag: str | None = None, owner: str | None = None) -> dict[str, dict]:
    """全部已保存策略：{name: record}，按创建时间倒序。

    tag 非空时只返回带该标签的策略（策略分组）。
    owner 非空时只返回归属该客户的策略（暂时需求 4 的"分客户保存"）。
    """
    where, params = [], []
    if tag:
        where.append("',' || tags || ',' LIKE ?")
        params.append(f"%,{tag},%")
    if owner is not None:
        where.append("owner = ?")
        params.append(owner)
    sql = "SELECT name FROM strategies"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC"
    with _conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {r[0]: load_strategy(r[0]) for r in rows}


def all_tags() -> list[str]:
    """所有出现过的标签（去重、按出现顺序）。"""
    tags: list[str] = []
    for rec in list_strategies().values():
        for t in (rec.get("tags") or "").split(","):
            t = t.strip()
            if t and t not in tags:
                tags.append(t)
    return tags


def delete_strategy(name: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM strategies WHERE name = ?", (name,))
    return cur.rowcount > 0


def delete_strategies(names, owner: str | None = None) -> int:
    """批量删除，返回实际删除条数（暂时需求 4：多余策略清理）。

    owner 非空时**只删归属该客户的策略** —— 防止在"当前客户 A"的界面上
    误删 B 客户的策略。返回值小于请求数量即表示有跨客户的项被跳过。
    """
    targets = [str(n) for n in (names or []) if str(n).strip()]
    if not targets:
        return 0
    marks = ",".join("?" * len(targets))
    sql = f"DELETE FROM strategies WHERE name IN ({marks})"
    params: list = list(targets)
    if owner is not None:
        sql += " AND owner = ?"
        params.append(owner)
    with _conn() as conn:
        cur = conn.execute(sql, params)
    return cur.rowcount


def strategy_options() -> list[str]:
    """内置策略 + 已保存策略（UI 下拉框用）。"""
    from stock_plan.strategy.registry import STRATEGIES

    return list(STRATEGIES.keys()) + [
        n for n in list_strategies() if n not in STRATEGIES
    ]


def resolve_strategy(name: str, params: dict | None = None):
    """按名字实例化策略：内置走注册表，已保存走 CustomStrategy(config)。"""
    from stock_plan.strategy.custom import CustomStrategy
    from stock_plan.strategy.registry import create_strategy

    if name in load_registry_names():
        return create_strategy(name, params)
    rec = load_strategy(name)
    if rec is None:
        raise KeyError(f"策略不存在：{name}")
    strategy = CustomStrategy(rec["config"])
    if params:
        strategy.params = {**strategy.params, **params}
    return strategy


def load_registry_names() -> set[str]:
    from stock_plan.strategy.registry import STRATEGIES

    return set(STRATEGIES.keys())
