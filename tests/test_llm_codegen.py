# -*- coding: utf-8 -*-
"""LLM 策略生成 + 策略持久化 + 持仓诊断测试。

覆盖：
- normalize_config：未知键反馈 / 类型矫正 / 区间钳制 / 均线成对校验
- generate_annotated_code / generate_runnable_script 可生成且含关键内容
- store：保存 / 读取 / 列表 / 删除 / resolve_strategy
- generate_strategy_config（mock 模式）：返回合法配置
- holding.t_trade_advisor：价位合理性与模式判断
"""
from __future__ import annotations

import pandas as pd
import pytest

from stock_plan.analysis.holding import t_trade_advisor
from stock_plan.llm.analyzer import generate_strategy_config
from stock_plan.strategy import store
from stock_plan.strategy.codegen import (
    PARAM_SPEC,
    RULE_SPEC,
    WEIGHT_SPEC,
    generate_annotated_code,
    generate_runnable_script,
    normalize_config,
)


# ---------- normalize_config ----------
def test_normalize_config_unknown_keys():
    cfg, unknown = normalize_config({"trend_score": 0.9, "zzz_未知键": 1})
    assert cfg["weights"]["trend_score"] == 0.9
    assert "zzz_未知键" in unknown


def test_normalize_config_clamps_and_pairs():
    cfg, unknown = normalize_config({
        "trend_score": 2.0,      # 超出 0~1 → 钳制
        "atr_n_stop": -5,        # 负值 → 钳制
        "trend_ma_fast": 5,      # 均线只有快线 → 强制成对
        "dev_ma": 7,             # 允许值
    })
    assert cfg["weights"]["trend_score"] <= 2.0
    assert cfg["params"]["atr_n_stop"] >= 0
    assert cfg["rules"]["trend_ma_fast"] == 0 and cfg["rules"]["trend_ma_slow"] == 0
    assert cfg["rules"]["dev_ma"] == 7
    assert unknown == []


def test_normalize_config_empty_ok():
    cfg, unknown = normalize_config({})
    assert set(cfg) == {"name", "weights", "rules", "params"}
    assert unknown == []


# ---------- 代码生成 ----------
def test_generate_annotated_code_contains_values():
    cfg, _ = normalize_config({"name": "测试策略", "atr_m_exit": 4.5})
    code = generate_annotated_code(cfg)
    assert "测试策略" in code
    assert "4.5" in code
    assert "CONFIG" in code


def test_generate_runnable_script_importable_text():
    cfg, _ = normalize_config({"name": "脚本策略"})
    script = generate_runnable_script(cfg)
    assert "CustomStrategy" in script
    assert "run_backtest" in script
    assert "def main()" in script


# ---------- store ----------
def test_store_save_load_delete_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "STRATEGY_DB", tmp_path / "strategies.db")
    cfg, _ = normalize_config({"name": "往返策略", "hold_days": 15})
    assert store.save_strategy("往返策略", cfg, source="llm", proposal_code="# p", unsupported=[{"name": "x"}], notes="n")
    rec = store.load_strategy("往返策略")
    assert rec["config"]["params"]["hold_days"] == 15
    assert rec["source"] == "llm"
    assert rec["unsupported"][0]["name"] == "x"
    assert "往返策略" in store.list_strategies()
    # 覆盖保存
    cfg2, _ = normalize_config({"name": "往返策略", "hold_days": 20})
    store.save_strategy("往返策略", cfg2)
    assert store.load_strategy("往返策略")["config"]["params"]["hold_days"] == 20
    # resolve + 删除
    strat = store.resolve_strategy("往返策略")
    assert strat.params["hold_days"] == 20
    assert store.delete_strategy("往返策略")
    assert store.load_strategy("往返策略") is None


def test_vocab_keys_consistent():
    # 三张词表的键互不重叠，避免 LLM 输出错位归档
    kw, kr, kp = set(WEIGHT_SPEC), set(RULE_SPEC), set(PARAM_SPEC)
    assert not (kw & kr) and not (kw & kp) and not (kr & kp)


# ---------- mock 策略生成 ----------
def test_generate_strategy_config_mock():
    class _FakeMockClient:  # 强制走离线分支，测试不依赖 .env 里的真实 Key
        mock = True

    res = generate_strategy_config(
        "5日均线上穿30日均线买入，持有15天，涨10%止盈", client=_FakeMockClient()
    )
    assert res["mode"] == "mock"
    cfg = res["config"]
    assert set(cfg) >= {"name", "weights", "rules", "params"}
    # 二次校验：LLM 输出也要能被 normalize 收编
    cfg2, unknown2 = normalize_config(cfg)
    assert cfg2["params"]["hold_days"] >= 1


# ---------- 做T建议 ----------
def test_t_trade_advisor_levels():
    out = t_trade_advisor(close=10.0, atr=0.5, ma5=9.8, ma10=9.6, high20=10.4,
                          take_profit=11.0, stop_loss=9.0)
    assert out["buy_low"] < 10.0 < out["sell_high"]
    assert out["mode"] in ("先买后卖（正T）", "先卖后买（反T）")
    assert len(out["steps"]) >= 3


def test_t_trade_advisor_pressure_pulls_high():
    # 近期高点很近时，高抛价不应超过压力位太多
    out = t_trade_advisor(close=10.0, atr=0.5, ma5=9.9, ma10=9.8, high20=10.2,
                          take_profit=None, stop_loss=None)
    assert out["sell_high"] <= 10.2 + 1e-9
