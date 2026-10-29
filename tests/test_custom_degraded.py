# -*- coding: utf-8 -*-
"""自定义策略的降级链路测试（Phase 11）。

核心不变量：
1. 旧配置（不含 archetype/degraded/disabled_rules/mv_pct）行为逐值不变；
2. 缺因子列时**不再静默跳过**，而是禁用该规则并留下 degraded 说明；
3. 类型层禁用的规则即使列存在也不扣分；
4. mv_pct（小市值分位）在有/无 total_mv 两种情况下分别生效与降级；
5. SignalReport 能序列化并展示降级说明。
"""
from __future__ import annotations

import copy
from datetime import datetime

import pandas as pd
import pytest

from stock_plan.signal.report import SignalReport, UniverseFacts
from stock_plan.strategy.custom import CustomStrategy
from stock_plan.strategy.scorer import composite_score

OLD_CONFIG = {
    "name": "旧配置",
    "weights": {"trend_score": 0.6, "fund_score": 0.4, "mom_ret": 0.0},
    "rules": {
        "ma20_gt_ma60": True,
        "trend_ma_fast": 0,
        "trend_ma_slow": 0,
        "dev_ma": 20,
        "dev_min": -5,
        "dev_max": 5,
        "rsi_min": 0,
        "rsi_max": 75,
        "vol_ratio_max": 3.0,
        "mom_min": -100,
        "require_breakout": False,
        "liquidity_min": 0.0,
        "vol_surge_min": 0.0,
        "vol_surge_bonus": 10.0,
        "macd_golden": False,
        "macd_above_zero": False,
    },
    "params": {"atr_k_entry": 0.0, "atr_m_exit": 3.5, "atr_n_stop": 3.5, "hold_days": 30},
}


def _frame(**overrides) -> pd.DataFrame:
    base = {
        "trend_score": [60.0, 70.0],
        "fund_score": [50.0, 55.0],
        "close": [10.0, 20.0],
        "ma20": [10.0, 20.0],
        "ma60": [9.0, 19.0],
        "rsi14": [50.0, 55.0],
        "vol_ratio": [1.0, 1.2],
        "mom_ret": [0.05, 0.10],
        "high20_ratio": [0.01, 0.02],
        "avg_amount20": [1e9, 2e9],
        "macd": [0.1, 0.2],
        "macd_signal": [0.05, 0.1],
        "total_mv": [1e10, 5e11],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def _baseline(frame: pd.DataFrame) -> pd.Series:
    return composite_score(
        frame["trend_score"], frame["fund_score"], weights=(0.6, 0.4, 0.0)
    )


def test_old_config_numeric_behaviour_unchanged():
    frame = _frame()
    strategy = CustomStrategy(copy.deepcopy(OLD_CONFIG))
    got = strategy.score(frame)
    assert list(got) == pytest.approx(list(_baseline(frame)))
    assert strategy.degraded_messages() == ()


def test_missing_column_marks_degraded_instead_of_silent_skip():
    config = copy.deepcopy(OLD_CONFIG)
    config["rules"]["liquidity_min"] = 0.5
    frame = _frame().drop(columns=["avg_amount20"])
    strategy = CustomStrategy(config)
    got = strategy.score(frame)
    # 规则被禁用 → 不扣分（与基线一致），但必须留下降级说明
    assert list(got) == pytest.approx(list(_baseline(frame)))
    messages = strategy.degraded_messages()
    assert any("avg_amount20" in message for message in messages)
    assert any("流动性" in message for message in messages)


def test_type_disabled_rule_is_not_applied():
    config = copy.deepcopy(OLD_CONFIG)
    config["disabled_rules"] = ["ma20_gt_ma60"]
    frame = _frame(ma20=[10.0, 20.0], ma60=[11.0, 21.0])  # 均线空头，启用会 −100
    got = CustomStrategy(config).score(frame)
    assert list(got) == pytest.approx(list(_baseline(frame)))


def test_mv_pct_penalises_large_caps_only():
    frame = _frame()
    config = copy.deepcopy(OLD_CONFIG)
    config["rules"]["mv_pct"] = 0.5
    base = CustomStrategy(copy.deepcopy(OLD_CONFIG)).score(frame)
    adjusted = CustomStrategy(config).score(frame)
    # 小市值那只 rank=0.5 不超阈值，不扣分；大市值那只 rank=1.0 被扣 100
    assert adjusted.iloc[0] == pytest.approx(base.iloc[0])
    assert adjusted.iloc[1] == pytest.approx(base.iloc[1] - 100)
    assert CustomStrategy(config).degraded_messages() == ()


def test_mv_pct_without_total_mv_is_degraded():
    config = copy.deepcopy(OLD_CONFIG)
    config["rules"]["mv_pct"] = 0.3
    frame = _frame().drop(columns=["total_mv"])
    strategy = CustomStrategy(config)
    base = CustomStrategy(copy.deepcopy(OLD_CONFIG)).score(frame)
    assert list(strategy.score(frame)) == pytest.approx(list(base))
    assert any("total_mv" in message for message in strategy.degraded_messages())


def test_signal_report_carries_degraded_messages():
    message = "缺少字段 total_mv：规则「小市值分位」未计算"
    report = SignalReport(
        generated_at=datetime(2026, 10, 29, 9, 0),
        universe=UniverseFacts(total_in_list=10, with_bars=10, passed=5),
        degraded=(message,),
    )
    payload = report.to_dict()
    assert payload["degraded"] == [message]
    text = report.to_facts_text()
    assert "降级" in text
    assert "total_mv" in text
