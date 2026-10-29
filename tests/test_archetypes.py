# -*- coding: utf-8 -*-
"""策略类型画像（两段式第一段）测试。

覆盖：六类型注册、事件驱动占位不可回测、白名单越界拒绝、三层默认值、
缺字段 → 规则禁用 + degraded 留痕、动态均线列、报告侧读取降级说明。
"""
from __future__ import annotations

import pytest

from stock_plan.strategy.archetypes import (
    ARCHETYPES,
    GLOBAL_DEFAULTS,
    get_archetype,
    resolve_config,
    rule_columns,
    strategy_degraded,
)
from stock_plan.strategy.codegen import normalize_config, normalize_config_typed


def test_six_archetypes_registered():
    assert set(ARCHETYPES) == {
        "trend",
        "momentum",
        "mean_reversion",
        "multi_factor",
        "small_cap",
        "event",
    }
    for key, arch in ARCHETYPES.items():
        assert arch.key == key
        assert arch.label
        assert arch.tagline


def test_event_archetype_is_pending_data():
    arch = ARCHETYPES["event"]
    assert arch.is_ready is False
    assert arch.note
    assert arch.allowed_weights == () and arch.allowed_rules == ()


def test_get_archetype_unknown_raises_chinese_error():
    with pytest.raises(ValueError) as exc:
        get_archetype("no-such-type")
    assert "未知策略类型" in str(exc.value)
    with pytest.raises(ValueError):
        get_archetype("")


def test_resolve_config_rejects_out_of_scope_field():
    clean, _unknown = normalize_config({"name": "t", "mom_ret": 0.3, "hold_days": 20})
    config, issues, degraded = resolve_config(
        "mean_reversion", clean, provided={"mom_ret", "hold_days"}
    )
    assert config["archetype"] == "mean_reversion"
    # 均值回归不允许 mom_ret 权重：越界被忽略、回落全局默认，并给出中文说明
    assert any("mom_ret" in item for item in issues)
    assert config["weights"]["mom_ret"] == 0.0
    # 白名单内的显式值保留
    assert config["params"]["hold_days"] == 20.0
    assert degraded == []


def test_resolve_config_three_layer_defaults():
    clean, _unknown = normalize_config({"name": "t"})
    config, _issues, _degraded = resolve_config("trend", clean, provided={"name"})
    # 第三层：类型默认值覆盖全局默认值
    assert config["params"]["hold_days"] == 30
    assert config["params"]["atr_n_stop"] == 3.5
    assert config["params"]["atr_m_exit"] == 3.5
    assert config["rules"]["ma20_gt_ma60"] is True
    # 类型层没写的参数沿用全局层
    assert config["params"]["atr_k_entry"] == GLOBAL_DEFAULTS["params"]["atr_k_entry"]


def test_resolve_config_disables_rules_when_columns_missing():
    clean, _unknown = normalize_config({"name": "t"})
    config, _issues, degraded = resolve_config(
        "trend", clean, provided={"name"}, available_columns={"close", "ma20"}
    )
    assert "ma20_gt_ma60" in config["disabled_rules"]
    assert any(item["rule"] == "ma20_gt_ma60" for item in degraded)


def test_event_resolve_returns_no_runnable_config():
    clean, _unknown = normalize_config({"name": "e"})
    config, _issues, degraded = resolve_config("event", clean, provided={"name"})
    assert config["disabled_rules"] == sorted(ARCHETYPES["event"].allowed_rules)
    assert any(item["rule"] == "archetype" for item in degraded)


def test_rule_columns_follow_configured_ma_period():
    assert rule_columns("dev_ma", {"dev_ma": 10}) == ("ma10",)
    assert rule_columns("trend_ma_fast", {"trend_ma_fast": 0}) == ()
    assert rule_columns("ma20_gt_ma60", {}) == ("ma20", "ma60")


def test_normalize_config_typed_reports_unknown_and_archetype():
    config, issues, degraded = normalize_config_typed(
        {"name": "x", "foo_bar": 1},
        "trend",
        available_columns={"close", "ma20", "ma60"},
    )
    assert config["archetype"] == "trend"
    assert any("foo_bar" in item for item in issues)
    # 趋势类型默认趋势均线是 ma10/ma30，当前列里没有 ma10 → 必须留下降级记录而不是静默跳过
    assert any("ma10" in item.get("missing", "") for item in degraded)


def test_strategy_degraded_reads_instance_messages():
    class _Stub:
        def degraded_messages(self):
            return ("缺少字段 total_mv：规则「小市值分位」未计算",)

    assert strategy_degraded(_Stub()) == ("缺少字段 total_mv：规则「小市值分位」未计算",)
    assert strategy_degraded(object()) == ()
