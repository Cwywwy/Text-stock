# -*- coding: utf-8 -*-
"""策略持久化存储 — LLM 生成 / 拼装页保存的自定义策略统一入库（SQLite）。

保存后的策略在今日信号 / 回测 / 策略对比页的下拉框立即可选。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
STRATEGY_DB = PROJECT_ROOT / "data" / "db" / "strategies.db"


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
            created_at TEXT,
            updated_at TEXT
        )"""
    )
    return conn


def save_strategy(
    name: str,
    config: dict,
    source: str = "manual",
    proposal_code: str = "",
    unsupported: list | None = None,
    notes: str = "",
) -> bool:
    """保存/覆盖一个策略（按名字主键）。"""
    name = (name or "").strip()
    if not name or not config:
        return False
    now = datetime.now().isoformat(timespec="seconds")
    with _conn() as conn:
        conn.execute(
            """INSERT INTO strategies
               (name, config_json, source, proposal_code, unsupported_json, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 config_json=excluded.config_json, source=excluded.source,
                 proposal_code=excluded.proposal_code,
                 unsupported_json=excluded.unsupported_json, notes=excluded.notes,
                 updated_at=excluded.updated_at""",
            (
                name,
                json.dumps(config, ensure_ascii=False),
                source,
                proposal_code or "",
                json.dumps(unsupported or [], ensure_ascii=False),
                notes or "",
                now,
                now,
            ),
        )
    return True


def load_strategy(name: str) -> dict | None:
    """按名字取策略记录；不存在返回 None。"""
    with _conn() as conn:
        row = conn.execute(
            "SELECT name, config_json, source, proposal_code, unsupported_json, notes, created_at"
            " FROM strategies WHERE name = ?",
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
        "created_at": row[6],
    }


def list_strategies() -> dict[str, dict]:
    """全部已保存策略：{name: record}，按创建时间倒序。"""
    with _conn() as conn:
        rows = conn.execute(
            "SELECT name FROM strategies ORDER BY created_at DESC"
        ).fetchall()
    return {r[0]: load_strategy(r[0]) for r in rows}


def delete_strategy(name: str) -> bool:
    with _conn() as conn:
        cur = conn.execute("DELETE FROM strategies WHERE name = ?", (name,))
    return cur.rowcount > 0


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
