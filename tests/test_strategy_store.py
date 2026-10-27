# -*- coding: utf-8 -*-
"""策略存储测试（暂时需求 4：策略分批 / 批量删除）。

覆盖：标签规范化、保存与读回、按标签精确筛选（不能子串误匹配）、
标签汇总、批量删除、单独改标签，以及**老库迁移**（缺 tags 列时自动补上且不丢数据）。
"""
from __future__ import annotations

import sqlite3

import pytest

from stock_plan.strategy import store


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """把策略库指到临时文件，避免污染真实 strategies.db。"""
    db = tmp_path / "strategies.db"
    monkeypatch.setattr(store, "STRATEGY_DB", db)
    return db


CFG = {"weights": {"trend": 1.0}, "rules": {"ma_cross": True}, "params": {"m": 3.5}}


# ---------- 标签规范化 ----------
def test_normalize_tags() -> None:
    assert store.normalize_tags("短线, 波段") == "短线,波段"
    assert store.normalize_tags("短线，波段") == "短线,波段"       # 全角逗号
    assert store.normalize_tags(["a", " a ", "b"]) == "a,b"       # 去重去空白
    assert store.normalize_tags("") == ""
    assert store.normalize_tags(None) == ""
    assert store.normalize_tags("x,,y,") == "x,y"


# ---------- 保存 / 读回 ----------
def test_save_and_load_with_tags(tmp_db) -> None:
    assert store.save_strategy("策略A", CFG, tags="客户-张三,短线")
    rec = store.load_strategy("策略A")
    assert rec is not None
    assert rec["tags"] == "客户-张三,短线"
    assert rec["config"] == CFG


def test_save_without_tags_defaults_empty(tmp_db) -> None:
    store.save_strategy("策略B", CFG)
    assert store.load_strategy("策略B")["tags"] == ""


# ---------- 按标签筛选：必须精确，不能子串误匹配 ----------
def test_list_strategies_filter_by_tag(tmp_db) -> None:
    store.save_strategy("A1", CFG, tags="客户-张三,短线")
    store.save_strategy("A2", CFG, tags="客户-张三")
    store.save_strategy("A3", CFG, tags="客户-张三丰")      # 前缀相同但不是同一个标签
    store.save_strategy("A4", CFG, tags="")

    assert set(store.list_strategies(tag="客户-张三")) == {"A1", "A2"}
    assert set(store.list_strategies(tag="客户-张三丰")) == {"A3"}
    assert set(store.list_strategies(tag="短线")) == {"A1"}
    assert set(store.list_strategies(tag="不存在")) == set()
    assert set(store.list_strategies()) == {"A1", "A2", "A3", "A4"}


def test_all_tags(tmp_db) -> None:
    store.save_strategy("A1", CFG, tags="短线,客户-张三")
    store.save_strategy("A2", CFG, tags="客户-李四")
    store.save_strategy("A3", CFG)
    assert store.all_tags() == ["短线", "客户-张三", "客户-李四"]


# ---------- 改标签 ----------
def test_set_tags(tmp_db) -> None:
    store.save_strategy("A1", CFG, tags="旧标签")
    assert store.set_tags("A1", "新标签,附加") is True
    assert store.load_strategy("A1")["tags"] == "新标签,附加"
    assert store.set_tags("不存在", "x") is False


# ---------- 批量删除 ----------
def test_delete_strategies_batch(tmp_db) -> None:
    for n in ("A1", "A2", "A3"):
        store.save_strategy(n, CFG)
    assert store.delete_strategies(["A1", "A3"]) == 2
    assert set(store.list_strategies()) == {"A2"}


def test_delete_strategies_edge_cases(tmp_db) -> None:
    store.save_strategy("A1", CFG)
    assert store.delete_strategies([]) == 0
    assert store.delete_strategies(None) == 0
    # 不存在的名字不计入删除数，也不报错
    assert store.delete_strategies(["A1", "不存在"]) == 1
    assert store.list_strategies() == {}


# ---------- 老库迁移 ----------
def test_migration_adds_tags_column_without_losing_data(tmp_db) -> None:
    """老库（没有 tags 列）打开后应自动补列，且已有策略一条不丢。

    这是兼容性保证：用户现有的 strategies.db 是 2026-10-15 之前建的。
    """
    # 手工建出"老版本"表结构并插入一条记录
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        """CREATE TABLE strategies (
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
    conn.execute(
        "INSERT INTO strategies (name, config_json, source, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("旧策略", '{"weights": {"trend": 1.0}}', "llm", "旧说明", "2026-09-01T10:00:00"),
    )
    conn.commit()
    conn.close()

    # 用新代码打开：应自动补 tags 列
    rec = store.load_strategy("旧策略")
    assert rec is not None, "迁移不能丢数据"
    assert rec["tags"] == "", "新列应有默认空值"
    assert rec["notes"] == "旧说明"
    assert rec["config"] == {"weights": {"trend": 1.0}}

    # 迁移后新功能可用
    assert store.set_tags("旧策略", "迁移后标签") is True
    assert store.list_strategies(tag="迁移后标签") == {"旧策略": store.load_strategy("旧策略")}


def test_migration_is_idempotent(tmp_db) -> None:
    """重复打开不应重复 ALTER（多次调用 _conn 要安全）。"""
    store.save_strategy("A1", CFG, tags="x")
    for _ in range(3):
        assert store.load_strategy("A1")["tags"] == "x"


# ---------- owner（分客户保存，暂时需求 4） ----------
def test_owner_defaults_to_local(tmp_db) -> None:
    assert store.current_owner() == store.DEFAULT_OWNER == "local"
    store.save_strategy("A1", CFG)
    assert store.load_strategy("A1")["owner"] == "local"


def test_current_owner_persists_and_scopes_saves(tmp_db) -> None:
    """「当前客户」要能持久化（存 settings 表），且保存时自动带上它。"""
    store.set_current_owner("客户-张三")
    # 新建连接也要读得到（不是内存状态）
    assert store.current_owner() == "客户-张三"
    store.save_strategy("A1", CFG)
    assert store.load_strategy("A1")["owner"] == "客户-张三"

    store.set_current_owner("客户-李四")
    store.save_strategy("A2", CFG)
    assert store.load_strategy("A2")["owner"] == "客户-李四"
    # 清空应回落默认
    store.set_current_owner("   ")
    assert store.current_owner() == "local"


def test_list_strategies_filter_by_owner(tmp_db) -> None:
    store.save_strategy("A1", CFG, owner="客户-张三")
    store.save_strategy("A2", CFG, owner="客户-张三")
    store.save_strategy("A3", CFG, owner="客户-李四")
    assert set(store.list_strategies(owner="客户-张三")) == {"A1", "A2"}
    assert set(store.list_strategies(owner="客户-李四")) == {"A3"}
    assert set(store.list_strategies(owner="不存在")) == set()
    assert set(store.list_strategies()) == {"A1", "A2", "A3"}
    # 与标签筛选可叠加
    store.set_tags("A1", "短线")
    assert set(store.list_strategies(tag="短线", owner="客户-张三")) == {"A1"}
    assert set(store.list_strategies(tag="短线", owner="客户-李四")) == set()


def test_all_owners_includes_current(tmp_db) -> None:
    store.save_strategy("A1", CFG, owner="客户-张三")
    store.set_current_owner("客户-王五")
    owners = store.all_owners()
    assert "客户-张三" in owners and "客户-王五" in owners


def test_same_name_does_not_steal_owner(tmp_db) -> None:
    """**归属冲突规则**：同名策略已存在时不得改变其 owner。

    否则 A 客户保存一个同名策略，就会把 B 客户的策略悄悄划到自己名下，
    接着在自己的列表里看到它、甚至删掉它。
    """
    store.save_strategy("A1", CFG, owner="客户-张三", notes="张三的")
    # 李四用同名保存（内容不同）
    store.save_strategy("A1", {"weights": {"x": 2.0}}, owner="客户-李四", notes="李四的")
    rec = store.load_strategy("A1")
    assert rec["owner"] == "客户-张三", "同名保存不得夺走归属"
    assert rec["notes"] == "李四的", "其余字段仍按最新覆盖（沿用原有语义）"


def test_set_owner_reassigns(tmp_db) -> None:
    store.save_strategy("A1", CFG, owner="客户-张三")
    assert store.set_owner("A1", "客户-李四") is True
    assert store.load_strategy("A1")["owner"] == "客户-李四"
    assert store.set_owner("不存在", "x") is False


def test_delete_strategies_cannot_cross_owner(tmp_db) -> None:
    """批量删除必须限定在当前客户，不能误删别人的策略。"""
    store.save_strategy("A1", CFG, owner="客户-张三")
    store.save_strategy("A2", CFG, owner="客户-李四")
    # 在"张三"的身份下尝试同时删掉李四的 A2
    deleted = store.delete_strategies(["A1", "A2"], owner="客户-张三")
    assert deleted == 1, "只应删掉归属于自己的那一只"
    assert store.load_strategy("A1") is None
    assert store.load_strategy("A2") is not None, "李四的策略必须还在"


def test_owner_migration_from_old_db(tmp_db) -> None:
    """老库（既没有 tags 也没有 owner）打开后应自动补列且不丢数据。"""
    conn = sqlite3.connect(tmp_db)
    conn.execute(
        """CREATE TABLE strategies (
            name TEXT PRIMARY KEY, config_json TEXT NOT NULL, source TEXT DEFAULT 'manual',
            proposal_code TEXT DEFAULT '', unsupported_json TEXT DEFAULT '[]',
            notes TEXT DEFAULT '', created_at TEXT, updated_at TEXT
        )"""
    )
    conn.execute(
        "INSERT INTO strategies (name, config_json, created_at) VALUES (?, ?, ?)",
        ("旧策略", '{"weights": {"trend": 1.0}}', "2026-09-01T10:00:00"),
    )
    conn.commit()
    conn.close()

    rec = store.load_strategy("旧策略")
    assert rec is not None and rec["config"] == {"weights": {"trend": 1.0}}
    assert rec["tags"] == ""
    assert rec["owner"] == "local", "老数据应落到默认归属"