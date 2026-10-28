# -*- coding: utf-8 -*-
"""信号解释报告测试（2026-10-28 phase 1）。

覆盖三层：
1. 硬过滤「可解释」版本与旧版**结果逐位一致**（否则今日信号会悄悄变样）；
2. 打分扣分与旧 `score()` 数值一致，且扣分明细能对上规则；
3. 报告结构与「禁止 LLM 编数」的边界：事实文本只含代码算出来的数字，
   序列化不产生 NaN，缓存键不含生成时间。

测试用假 Storage 注入 `load_market_maps`，不读真实数据库、不联网。
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from stock_plan.factors.filter import (
    MIN_AVG_AMOUNT,
    filter_universe,
    filter_universe_explained,
)
from stock_plan.signal.generator import generate_signal_report, generate_signals
from stock_plan.signal.report import (
    CandidateFacts,
    EntryGap,
    Penalty,
    RejectedSample,
    UniverseFacts,
    strategy_fingerprint,
)
from stock_plan.strategy.builtin import TrendFollowingStrategy


def _bars(
    n: int = 150,
    seed: int = 0,
    start: float = 10.0,
    drift: float = 0.004,
    volume: float | None = None,
    amount: float | None = None,
) -> pd.DataFrame:
    """造一段确定性日线（缓涨为主，保证 ma20 > ma60，避免被 -100 排除）。"""
    rng = np.random.default_rng(seed)
    returns = rng.normal(drift, 0.008, n)
    close = start * np.cumprod(1 + returns)
    volumes = rng.integers(1_000_000, 5_000_000, n).astype(float)
    amounts = rng.integers(1e8, 5e8, n).astype(float)
    if volume is not None:
        volumes[-1] = volume
    if amount is not None:
        amounts[:] = amount
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-01", periods=n),
            "open": close * 0.995,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": volumes,
            "amount": amounts,
            "turnover": 0.01,
        }
    )


def _stock_list(codes: list[str], st_codes: tuple[str, ...] = ()) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": codes,
            "name": codes,
            "is_st": [1 if code in st_codes else 0 for code in codes],
            "board": ["主板"] * len(codes),
        }
    )


class _FakeStorage:
    """假 Storage：只实现 generate_signal_report / generate_signals 用到的入口。"""

    def __init__(self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame],
                 fund_map: dict[str, dict] | None = None) -> None:
        self._stock_list = stock_list
        self._bars_map = bars_map
        self._fund_map = fund_map or {}

    def load_market_maps(self, **kwargs) -> tuple[pd.DataFrame, dict, dict]:
        return self._stock_list, self._bars_map, self._fund_map


def _market(n_good: int = 9, seed_base: int = 0) -> tuple[pd.DataFrame, dict, dict]:
    """9 只正常股 + 每类硬过滤各 1 只样本（ST/无日线/次新/停牌/流动性不足）。"""
    codes = [f"60{i:04d}" for i in range(1, n_good + 1)]
    bars_map = {code: _bars(seed=seed_base + index) for index, code in enumerate(codes)}
    bars_map["900003"] = _bars(n=30, seed=91)            # 次新：不足 60 根
    bars_map["900004"] = _bars(seed=92, volume=0.0)       # 停牌：最后一根成交量为 0
    bars_map["900005"] = _bars(seed=93, amount=1e6)       # 流动性不足：日均额 100 万
    stock_list = _stock_list(
        codes + ["900001", "900002", "900003", "900004", "900005"],
        st_codes=("900001",),
    )
    bars_map["900001"] = _bars(seed=90)  # ST 但有日线：应因 ST 被淘汰，而不是"无日线"
    return stock_list, bars_map, {}


# ---------------------------------------------------------------------------
# 1. 硬过滤可解释版：结果必须与旧版一致
# ---------------------------------------------------------------------------


def test_filter_explained_passed_matches_legacy():
    stock_list, bars_map, _ = _market()
    legacy = filter_universe(stock_list, bars_map)
    outcome = filter_universe_explained(stock_list, bars_map)
    assert list(outcome.passed) == legacy
    assert outcome.passed_count == len(legacy) == 9
    assert outcome.total == len(stock_list)


def test_filter_counts_each_reason_once():
    stock_list, bars_map, _ = _market()
    outcome = filter_universe_explained(stock_list, bars_map)
    assert outcome.count_of("st") == 1
    assert outcome.count_of("no_bars") == 1
    assert outcome.count_of("new_listing") == 1
    assert outcome.count_of("suspended") == 1
    assert outcome.count_of("illiquid") == 1

    samples = outcome.samples(per_reason=1)
    assert len(samples) == 5
    assert {item.reason for item in samples} == {
        "st", "no_bars", "new_listing", "suspended", "illiquid",
    }
    # 样本里要带"为什么"的数值，而不是只有一句话
    illiquid = next(item for item in samples if item.reason == "illiquid")
    assert illiquid.detail["avg_amount20"] == pytest.approx(1e6)
    assert illiquid.detail["min_amount"] == MIN_AVG_AMOUNT
    assert "流动性" in illiquid.reason_text


def test_filter_samples_per_reason_limits():
    stock_list, bars_map, _ = _market()
    bars_map["900006"] = _bars(seed=94, amount=2e6)  # 再加一只流动性不足
    stock_list = pd.concat(
        [stock_list, _stock_list(["900006"])], ignore_index=True
    )
    outcome = filter_universe_explained(stock_list, bars_map)
    assert outcome.count_of("illiquid") == 2
    assert len(outcome.samples(per_reason=1)) == 5
    assert len(outcome.samples(per_reason=2)) == 6


# ---------------------------------------------------------------------------
# 2. 扣分明细与旧打分一致
# ---------------------------------------------------------------------------


def _factor_frame() -> pd.DataFrame:
    """三只股票：正常 / 追高 / 趋势向下，用于验证扣分规则命中。"""
    return pd.DataFrame(
        {
            "code": ["600001", "600002", "600003"],
            "close": [101.0, 112.0, 90.0],
            "ma20": [100.0, 100.0, 95.0],
            "ma60": [95.0, 95.0, 100.0],
            "rsi14": [60.0, 80.0, 55.0],
            "trend_score": [80.0, 70.0, 50.0],
            "fund_score": [60.0, 50.0, 40.0],
        }
    )


def test_score_with_penalties_matches_legacy_score():
    strategy = TrendFollowingStrategy()
    frame = _factor_frame()
    legacy = strategy.score(frame)
    score, penalties = strategy.score_with_penalties(frame)
    pd.testing.assert_series_equal(score, legacy)
    # 每只股票的扣分合计应与"分数差"一致（分数 = 原始分 - 扣分）
    totals = penalties.groupby("code")["points"].sum().to_dict()
    for code, total in totals.items():
        row = frame.index[frame["code"] == code][0]
        base = legacy.loc[row] - total
        assert base == pytest.approx(
            (frame.loc[row, "trend_score"] * strategy.params["w_tech"]
             + frame.loc[row, "fund_score"] * strategy.params["w_fund"])
        )


def test_score_penalties_rules_and_basis():
    strategy = TrendFollowingStrategy()
    _, penalties = strategy.score_with_penalties(_factor_frame())
    by_code = penalties.groupby("code")["rule"].apply(list).to_dict()
    # 600002 追高 +12%、RSI 80 → chase_high、dev_high、rsi_hot 全部命中（顺序与旧实现一致）
    assert by_code["600002"] == ["chase_high", "dev_high", "rsi_hot"]
    # 600003 ma20 < ma60 → 趋势向下；且偏离 ma20 -5.26% → 同时触发 dev_low
    assert by_code["600003"] == ["trend_down", "dev_low"]
    # 600001 全部合规 → 没有扣分行
    assert "600001" not in by_code
    chase = penalties[(penalties["code"] == "600002") & (penalties["rule"] == "chase_high")].iloc[0]
    assert chase["points"] == pytest.approx(-100.0)
    assert chase["basis"] == pytest.approx(0.12)
    assert "追高" in chase["label"]


# ---------------------------------------------------------------------------
# 3. 报告结构 / 序列化 / 事实文本
# ---------------------------------------------------------------------------


def test_report_structure_and_ranking():
    stock_list, bars_map, fund_map = _market()
    report = generate_signal_report(
        storage=_FakeStorage(stock_list, bars_map, fund_map),
        top_n=5,
        runners_up=3,
    )
    assert len(report.picked) == 5
    assert len(report.runners_up) == 3
    assert [item.rank for item in report.picked] == [1, 2, 3, 4, 5]
    assert [item.rank for item in report.runners_up] == [6, 7, 8]
    assert all(item.picked for item in report.picked)
    assert not any(item.picked for item in report.runners_up)
    scores = [item.score for item in report.picked]
    assert scores == sorted(scores, reverse=True)
    # 入选末位 >= 紧随第一名（并列时也成立）
    assert report.picked[-1].score >= report.runners_up[0].score
    assert report.entry_gap is not None
    assert report.entry_gap.last_picked_score == pytest.approx(report.picked[-1].score)
    assert len(report.rejected_samples) == 5
    assert report.universe.total_in_list == 14
    assert report.universe.with_bars == 13
    assert report.universe.passed == 9


def test_report_matches_generate_signals():
    """报告里的入选股票必须与信号页完全一致（同源同分）。"""
    stock_list, bars_map, fund_map = _market()
    storage = _FakeStorage(stock_list, bars_map, fund_map)
    report = generate_signal_report(storage=storage, top_n=5)
    signals = generate_signals(
        strategy=TrendFollowingStrategy(), top_n=5, storage=storage
    )
    assert [item.code for item in report.picked] == [sig.code for sig in signals]
    assert [item.name for item in report.picked] == [sig.name for sig in signals]


def test_report_data_until_comes_from_bars():
    stock_list, bars_map, fund_map = _market()
    report = generate_signal_report(storage=_FakeStorage(stock_list, bars_map, fund_map))
    expected = bars_map["600001"]["date"].max().strftime("%Y-%m-%d")
    assert report.universe.data_until == expected
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", report.universe.data_until)


def test_report_to_dict_is_json_safe_and_cache_payload_drops_time():
    stock_list, bars_map, fund_map = _market()
    report = generate_signal_report(storage=_FakeStorage(stock_list, bars_map, fund_map))
    payload = report.to_dict()
    text = json.dumps(payload, ensure_ascii=False)
    assert "NaN" not in text  # NaN 不是合法 JSON
    assert payload["version"] == 1
    assert payload["picked"][0]["penalties"] == []

    cache = report.cache_payload()
    assert "generated_at" not in cache
    assert cache["picked"] == payload["picked"]


def test_report_facts_text_contains_only_computed_numbers():
    stock_list, bars_map, fund_map = _market()
    report = generate_signal_report(storage=_FakeStorage(stock_list, bars_map, fund_map))
    text = report.to_facts_text()
    assert "【入选 5 只（按分数降序）】" in text
    assert "【紧随其后 3 只（未入选）】" in text
    assert "【入选门槛】" in text
    assert "【硬过滤淘汰】" in text
    for item in report.picked:
        assert item.code in text
        assert f"{item.score:.1f}" in text
    assert report.universe.data_until in text
    assert report.universe.strategy_fingerprint[:6] in text


def test_report_empty_when_stock_list_missing():
    report = generate_signal_report(
        storage=_FakeStorage(_stock_list([]), {}),
    )
    assert report.is_empty
    assert report.picked == ()
    assert any("股票列表为空" in item for item in report.warnings)


def test_report_without_scoring_penalties_keeps_working():
    """策略没有 score_with_penalties 时降级为只打分，并明确记录这一点。"""
    stock_list, bars_map, fund_map = _market()

    class _PlainStrategy(TrendFollowingStrategy):
        """模拟「未实现扣分明细」的第三方策略：只保留旧的 score()。"""

        name = "无扣分明细策略"

        def score_with_penalties(self, df_factors):
            raise AttributeError("not implemented")

        def score(self, df_factors):
            return pd.Series(50.0, index=df_factors.index)

    report = generate_signal_report(
        strategy=_PlainStrategy(), storage=_FakeStorage(stock_list, bars_map, fund_map)
    )
    assert len(report.picked) == 5
    assert any("扣分明细" in item for item in report.warnings)


# ---------------------------------------------------------------------------
# 4. 纯数据结构（不跑管道）
# ---------------------------------------------------------------------------


def test_candidate_facts_line_includes_penalty_explanation():
    penalty = Penalty(rule="dev_high", label="偏离 ma20 超过 +5%", points=-30.0, basis=0.064)
    facts = CandidateFacts(
        code="600001",
        name="测试股",
        score=72.5,
        rank=6,
        picked=False,
        close=10.0,
        ma20=9.4,
        dev_ma20=0.064,
        rsi14=66.0,
        vol_ratio=1.4,
        trend_score=78.0,
        fund_score=60.0,
        bar_date="2025-07-29",
        penalties=(penalty,),
    )
    line = facts.facts_line()
    assert "第6名 600001 测试股" in line
    assert "偏离 ma20 +6.40%" in line
    assert "扣分 -30" in line
    assert facts.penalty_points == pytest.approx(-30.0)
    assert facts.base_score == pytest.approx(102.5)


def test_entry_gap_note_levels():
    assert EntryGap(70.0, 69.6).note().startswith("门槛非常接近")
    assert EntryGap(70.0, 66.0).gap == pytest.approx(4.0)
    assert EntryGap(70.0, 60.0).note().startswith("门槛清晰")
    assert EntryGap(None, 60.0).gap is None
    assert "无法计算" in EntryGap(None, None).note()


def test_rejected_sample_rounds_numeric_detail():
    sample = RejectedSample(
        code="900005",
        name="样本",
        reason="illiquid",
        reason_text="流动性不足（20 日均成交额低于 5000 万）",
        detail={"avg_amount20": 1234567.891, "min_amount": 5e7},
    )
    data = sample.to_dict()
    assert data["detail"]["avg_amount20"] == pytest.approx(1234567.89)
    assert "流动性" in sample.facts_line()


def test_strategy_fingerprint_stable_and_param_sensitive():
    first = strategy_fingerprint(TrendFollowingStrategy())
    second = strategy_fingerprint(TrendFollowingStrategy())
    assert first == second
    assert len(first) == 12

    other = TrendFollowingStrategy()
    other.params = {**other.params, "top_n": 3}
    assert strategy_fingerprint(other) != first


def test_universe_facts_roundtrip():
    universe = UniverseFacts(
        total_in_list=10,
        with_bars=8,
        passed=6,
        rejected_counts={"st": 1, "passed": 6},
        data_until="2025-07-29",
        strategy_name="趋势跟随策略",
        strategy_fingerprint="abcdef123456",
    )
    assert universe.to_dict()["passed"] == 6
    assert universe.to_dict()["st_is_current"] is True
