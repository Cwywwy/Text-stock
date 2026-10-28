# -*- coding: utf-8 -*-
"""文件名：test_signal_narrative.py
作者：Cwywwy
用途：`stock_plan.llm.signal_narrative` 的数字闸门、白名单校验、缓存与回退路径。
所属模块：tests

用假 client 验证，不烧真实额度；缓存目录通过 monkeypatch 指到 tmp_path。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from stock_plan.llm import cache
from stock_plan.llm import signal_narrative as sn
from stock_plan.llm.client import ChatResult
from stock_plan.signal.report import (
    CandidateFacts,
    EntryGap,
    Penalty,
    RejectedSample,
    SignalReport,
    UniverseFacts,
)


def _candidate(code: str, name: str, score: float, rank: int, *, picked: bool, penalties=()):
    return CandidateFacts(
        code=code,
        name=name,
        score=score,
        rank=rank,
        picked=picked,
        close=10.0,
        ma20=9.5,
        ma60=9.0,
        dev_ma20=0.05,
        rsi14=60.0,
        vol_ratio=1.2,
        trend_score=80.0,
        fund_score=70.0,
        bar_date="2026-10-27",
        penalties=tuple(penalties),
    )


def _report() -> SignalReport:
    picked = (
        _candidate("002956", "西麦食品", 89.8, 1, picked=True),
        _candidate(
            "002332",
            "仙琚制药",
            87.6,
            2,
            picked=True,
            penalties=(
                Penalty("chase_high", "追高", -100.0, 0.114),
                Penalty("rsi_hot", "RSI 过热", -20.0, 77.0),
            ),
        ),
    )
    runners = (
        _candidate("300908", "水羊股份", 87.6, 3, picked=False),
        _candidate(
            "600694",
            "大商股份",
            87.1,
            4,
            picked=False,
            penalties=(Penalty("dev_low", "偏离均线过远", -50.0, -0.058),),
        ),
    )
    return SignalReport(
        generated_at=datetime(2026, 10, 28, 15, 0, 0),
        universe=UniverseFacts(
            total_in_list=5576,
            with_bars=5379,
            passed=4130,
            rejected_counts={"st": 197, "no_bars": 49, "illiquid": 1200, "passed": 4130},
            data_until="2026-10-27",
            strategy_name="趋势跟踪策略",
            strategy_fingerprint="7c1f4a9b2d3e",
        ),
        picked=picked,
        runners_up=runners,
        rejected_samples=(
            RejectedSample("000013", "*ST石化", "st", "ST/*ST 风险警示（今天的状态，非历史时点）"),
            RejectedSample("000981", "流动性样本", "illiquid", "流动性不足（近 20 日均额过低）"),
        ),
        entry_gap=EntryGap(87.6, 87.6),
    )


GOOD = {
    "summary": "这次选出的名单偏向趋势已经走出来、均线排列向上的标的，门槛附近挤得很紧。",
    "picks": [
        {"code": "002956", "note": "均线多头排列、偏离幅度温和，属于规则最舒服的形态。"},
        {"code": "002332", "note": "原本分数更高，但偏离均线过远、指标过热被扣分，仍靠原始分入选。"},
    ],
    "runners": [
        {"code": "300908", "note": "与门槛同分，只是并列时的代码排序靠后，随时可能替换进来。"},
        {"code": "600694", "note": "跌破均线被扣分，趋势强度不够。"},
    ],
    "risks": ["门槛附近并列，名单稳定性差。", "ST 判据是今天的状态，不是历史时点。"],
}


class _FakeClient:
    """假 client：mock=False，返回预设文本，可记录调用次数。"""

    mock = False

    def __init__(self, payload: dict | str | None = None, *, error: str = ""):
        self.payload = payload
        self.error = error
        self.calls: list[str] = []

    def chat_result(self, system, user, *, task="chat", temperature=0.3, use_cache=True, max_tokens=None):
        self.calls.append(user)
        if self.error:
            return ChatResult(text="", error=self.error, task=task)
        text = json.dumps(self.payload, ensure_ascii=False) if isinstance(self.payload, dict) else str(self.payload)
        return ChatResult(text=text, usage={"total_tokens": 123}, task=task)


@pytest.fixture()
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.delenv("LLM_CACHE_DISABLE", raising=False)
    return tmp_path


# --- 校验 ---------------------------------------------------------------


def test_validate_accepts_good_payload():
    data, err = sn.validate_narrative(json.dumps(GOOD, ensure_ascii=False), _report())
    assert err == ""
    assert data is not None
    assert {item["code"] for item in data["picks"]} == {"002956", "002332"}
    assert {item["code"] for item in data["runners"]} == {"300908", "600694"}


def test_validate_accepts_quoted_numbers():
    """模型可以原样引用事实清单里的数字（如「入选 2 只」「门槛 87.6 分」）。"""
    payload = dict(GOOD)
    payload["summary"] = "本次入选 2 只、紧随 2 只，门槛 87.6 分，属于并列挤在一起的情形。"
    data, err = sn.validate_narrative(json.dumps(payload, ensure_ascii=False), _report())
    assert err == ""
    assert data is not None


def test_validate_rejects_invented_number():
    bad = dict(GOOD)
    bad["picks"] = [
        {"code": "002956", "note": "综合分 93.5 分，排第一。"},
        {"code": "002332", "note": "原始分很高。"},
    ]
    data, err = sn.validate_narrative(json.dumps(bad, ensure_ascii=False), _report())
    assert data is None
    assert "编造的数字" in err
    assert "93.5" in err


def test_validate_accepts_risk_objects():
    """模型把风险写成 {"code","note"} 也能接受（取 note 文本）。"""
    payload = dict(GOOD)
    payload["risks"] = [{"code": "000013", "note": "ST 风险警示样本，属于当前状态而非历史时点。"}]
    data, err = sn.validate_narrative(json.dumps(payload, ensure_ascii=False), _report())
    assert err == ""
    assert data is not None
    assert data["risks"] == ["ST 风险警示样本，属于当前状态而非历史时点。"]


def test_validate_rejects_unknown_code():
    bad = dict(GOOD)
    bad["picks"] = list(GOOD["picks"]) + [{"code": "600519", "note": "编造的股票。"}]
    data, err = sn.validate_narrative(json.dumps(bad, ensure_ascii=False), _report())
    assert data is None
    assert "事实清单之外" in err


def test_validate_rejects_runner_in_picks():
    bad = dict(GOOD)
    bad["picks"] = [
        {"code": "002956", "note": "形态好。"},
        {"code": "300908", "note": "其实它没选上。"},
    ]
    data, err = sn.validate_narrative(json.dumps(bad, ensure_ascii=False), _report())
    assert data is None
    assert "落选股" in err


def test_validate_rejects_missing_picks():
    bad = dict(GOOD)
    bad["picks"] = [{"code": "002956", "note": "只写了一只。"}]
    data, err = sn.validate_narrative(json.dumps(bad, ensure_ascii=False), _report())
    assert data is None
    assert "没有覆盖全部入选股" in err


@pytest.mark.parametrize("raw", ["", "不是 JSON", "[]", '{"summary": ""}'])
def test_validate_rejects_bad_shapes(raw):
    data, err = sn.validate_narrative(raw, _report())
    assert data is None
    assert err


# --- 缓存与回退 -----------------------------------------------------------


def test_explain_uses_llm_and_caches(isolated_cache):
    client = _FakeClient(GOOD)
    first = sn.explain_signal_report(_report(), client=client, now=1_700_000_000.0)
    assert first.source == "llm"
    assert "门槛附近" in first.text
    assert len(client.calls) == 1

    second = sn.explain_signal_report(_report(), client=client, now=1_700_000_000.0)
    assert second.source == "cache"
    assert second.age
    assert len(client.calls) == 1  # 命中缓存，没有再调模型


def test_explain_falls_back_on_invalid_output(isolated_cache):
    client = _FakeClient("模型开始胡说：本次综合分 93 分。")
    result = sn.explain_signal_report(_report(), client=client)
    assert result.source == "template"
    assert "回退模板" in result.error
    assert "入选" in result.text


def test_explain_falls_back_on_client_error(isolated_cache):
    client = _FakeClient(error="429 限流")
    result = sn.explain_signal_report(_report(), client=client)
    assert result.source == "template"
    assert "429" in result.error


def test_explain_mock_client_returns_template(isolated_cache):
    class _Mock:
        mock = True

    result = sn.explain_signal_report(_report(), client=_Mock())
    assert result.source == "template"
    assert "API key" in result.error


def test_template_contains_computed_numbers():
    text = sn.template_narrative(_report())
    assert "87.6" in text
    assert "002332" in text
    # 模板允许出现数字（代码算的），但必须说明 ST 口径
    assert "ST" in text


def test_prompt_lists_codes_and_forbids_digits():
    prompt = sn.build_prompt(_report())
    for code in ("002956", "002332", "300908", "600694"):
        assert code in prompt
    assert "禁止" not in prompt  # 禁止数字的要求在 system prompt 里
    assert "JSON" in prompt


def test_empty_report_short_circuits(isolated_cache):
    report = SignalReport(generated_at=datetime(2026, 10, 28), universe=UniverseFacts())
    result = sn.explain_signal_report(report, client=_FakeClient(GOOD))
    assert result.source == "template"
    assert result.error == "报告为空"
