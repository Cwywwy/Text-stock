"""「为什么是这几只」区块的单元测试：对比表、淘汰样本表、分差瀑布图、页面冒烟。

全部用构造出来的 SignalReport，不连数据库、不跑全 A 股打分。
"""
from __future__ import annotations

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from stock_plan.signal.report import (
    CandidateFacts,
    EntryGap,
    Penalty,
    RejectedSample,
    SignalReport,
    UniverseFacts,
)
from stock_plan.ui import chart_style as cs
from stock_plan.ui.signal_report_section import (
    COMPARISON_COLUMNS,
    comparison_frame,
    rejected_frame,
    render_report_section,
)
from stock_plan.ui.widgets import score_waterfall_fig


def _candidate(
    code: str,
    *,
    rank: int,
    score: float,
    picked: bool,
    penalties: tuple[Penalty, ...] = (),
) -> CandidateFacts:
    return CandidateFacts(
        code=code,
        name=f"股票{code[-2:]}",
        score=score,
        rank=rank,
        picked=picked,
        close=10.0 + rank,
        ma20=10.0,
        ma60=9.5,
        dev_ma20=0.12 if rank >= 4 else 0.04,
        rsi14=76.0 if rank == 4 else 60.0,
        vol_ratio=1.2,
        trend_score=85.0,
        fund_score=72.0,
        bar_date="2026-10-27",
        penalties=penalties,
    )


def _report() -> SignalReport:
    picked = [
        _candidate("600001", rank=1, score=90.2, picked=True),
        _candidate("600002", rank=2, score=89.4, picked=True),
        _candidate("600003", rank=3, score=88.8, picked=True),
        _candidate(
            "600004",
            rank=4,
            score=88.0,
            picked=True,
            penalties=(
                Penalty(rule="chase_high", label="追高（偏离 ma20 过大）", points=-100.0, basis=0.12),
                Penalty(rule="rsi_hot", label="RSI 过热", points=-20.0, basis=76.0),
            ),
        ),
    ]
    runners = [
        _candidate("600005", rank=5, score=88.0, picked=False),
        _candidate("600006", rank=6, score=87.5, picked=False),
        _candidate("600007", rank=7, score=86.6, picked=False),
    ]
    return SignalReport(
        generated_at=pd.Timestamp("2026-10-27 21:00:00").to_pydatetime(),
        universe=UniverseFacts(
            total_in_list=5576,
            with_bars=5379,
            passed=4130,
            rejected_counts={"st": 197, "no_bars": 49, "illiquid": 1200},
            data_until="2026-10-27",
            strategy_name="趋势跟随策略",
            strategy_fingerprint="abc123def456",
        ),
        picked=tuple(picked),
        runners_up=tuple(runners),
        rejected_samples=(
            RejectedSample(
                code="900001",
                name="ST样本",
                reason="st",
                reason_text="ST/*ST 风险警示（今天的状态，非历史时点）",
                detail={"is_st": 1},
            ),
            RejectedSample(
                code="900005",
                name="流动性样本",
                reason="illiquid",
                reason_text="流动性不足",
                detail={"avg_amount20": 1e6, "min_amount": 5e7},
            ),
        ),
        entry_gap=EntryGap(last_picked_score=88.0, next_score=88.0),
        warnings=("硬过滤使用的 ST 判据是「今天」的 ST 状态，不是历史时点。",),
    )


def test_comparison_frame_lists_picked_then_runners():
    report = _report()
    frame = comparison_frame(report)
    assert list(frame.columns) == list(COMPARISON_COLUMNS)
    assert len(frame) == 7
    assert list(frame["名次"]) == [1, 2, 3, 4, 5, 6, 7]
    assert list(frame["状态"]) == ["入选"] * 4 + ["紧随未入选"] * 3
    # 综合分保留 1 位小数，避免浮点尾巴
    assert frame.loc[0, "综合分"] == "90.2"
    assert frame.loc[3, "扣分"] == "-120"
    assert frame.loc[0, "扣分"] == "—"
    # 扣分原因要能读懂，而不是只给个规则键
    assert "追高" in frame.loc[3, "扣分原因"]
    assert "RSI" in frame.loc[3, "扣分原因"]


def test_comparison_frame_handles_missing_numbers():
    frame = comparison_frame(_report())
    assert frame.loc[0, "偏离MA20"] == "+4.0%"
    assert frame.loc[5, "RSI14"] == "60"


def test_rejected_frame_translates_trigger_values():
    frame = rejected_frame(_report().rejected_samples)
    assert list(frame.columns) == ["代码", "名称", "原因", "触发数值"]
    assert "ST" in frame.loc[0, "原因"]
    assert "标记为 ST" in frame.loc[0, "触发数值"]
    # 金额要按「万」显示，用户不用数零
    assert "近 20 日均额 100 万 < 门槛 5000 万" in frame.loc[1, "触发数值"]


def test_rejected_frame_keeps_unknown_detail_keys():
    sample = RejectedSample(
        code="900009", name="X", reason="suspended", reason_text="停牌", detail={"custom": "值"}
    )
    frame = rejected_frame([sample])
    assert "custom=值" in frame.loc[0, "触发数值"]


def test_score_waterfall_uses_gate_as_base():
    fig = score_waterfall_fig(_report())
    assert fig is not None
    bars = fig.data[0]
    gate = 88.0
    assert list(bars.base) == [gate, gate, gate]
    assert [round(value, 4) for value in bars.y] == [0.0, -0.5, -1.4]
    # 持平灰、更低绿（红涨绿跌：落后用绿）
    assert list(bars.marker.color) == [cs.NEUTRAL, cs.DOWN, cs.DOWN]
    # 并列/落后都靠圆点标注真实分数（柱子高度为 0 时也看得见）
    dots = fig.data[1]
    assert dots.mode == "markers+text"
    assert [round(value, 4) for value in dots.y] == [88.0, 87.5, 86.6]
    assert dots.text == ("88.0", "87.5", "86.6")


def test_score_waterfall_returns_none_without_runners():
    report = _report()
    only_picked = SignalReport(
        generated_at=report.generated_at,
        universe=report.universe,
        picked=report.picked,
    )
    assert score_waterfall_fig(only_picked) is None
    assert score_waterfall_fig(None) is None


def test_render_report_section_smoke(tmp_path):
    """用 Streamlit AppTest 跑一遍真实渲染路径（含 dataframe 与图）。"""
    app = tmp_path / "report_app.py"
    app.write_text(
        """
from datetime import datetime

from stock_plan.signal.report import CandidateFacts, EntryGap, Penalty, RejectedSample, SignalReport, UniverseFacts
from stock_plan.ui.signal_report_section import render_report_section

picked = (
    CandidateFacts(code="600001", name="A股", score=90.0, rank=1, picked=True),
    CandidateFacts(
        code="600002", name="B股", score=87.0, rank=2, picked=True,
        penalties=(Penalty(rule="dev_high", label="偏离过大", points=-30.0, basis=0.08),),
    ),
)
runners = (
    CandidateFacts(code="600003", name="C股", score=86.4, rank=3, picked=False),
    CandidateFacts(code="600004", name="D股", score=86.0, rank=4, picked=False),
)
report = SignalReport(
    generated_at=datetime(2026, 10, 27, 21, 0, 0),
    universe=UniverseFacts(
        total_in_list=5576, with_bars=5379, passed=4130,
        rejected_counts={"st": 197}, data_until="2026-10-27",
        strategy_name="趋势跟随策略", strategy_fingerprint="abc123def456",
    ),
    picked=picked,
    runners_up=runners,
    rejected_samples=(
        RejectedSample(code="900001", name="ST样本", reason="st",
                       reason_text="ST/*ST 风险警示", detail={"is_st": 1}),
    ),
    entry_gap=EntryGap(last_picked_score=87.0, next_score=86.4),
    warnings=("硬过滤使用的 ST 判据是「今天」的 ST 状态，不是历史时点。",),
)
render_report_section(report, "中线")
""",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(app), default_timeout=60).run()
    assert not at.exception
    assert len(at.dataframe) == 2          # 对比表 + 硬过滤样本表
    assert "数据截至 2026-10-27" in at.caption[0].value or "数据截至" in " ".join(
        item.value for item in at.caption
    )
    assert len(at.caption) >= 2
    # 大模型解读默认不调用：只有按钮 + 一句说明
    assert [button.label for button in at.button] == ["🤖 让大模型写一段解读"]
    assert any("不点按钮不会调用大模型" in item.value for item in at.caption)


def test_render_report_section_empty_report(tmp_path):
    app = tmp_path / "empty_app.py"
    app.write_text(
        """
from datetime import datetime

from stock_plan.signal.report import SignalReport, UniverseFacts
from stock_plan.ui.signal_report_section import render_report_section

render_report_section(
    SignalReport(
        generated_at=datetime(2026, 10, 27, 21, 0, 0),
        universe=UniverseFacts(total_in_list=0, with_bars=0, strategy_name="趋势跟随策略",
                               strategy_fingerprint="abc123def456"),
        warnings=("股票列表为空（数据尚未更新），无法生成报告。",),
    )
)
""",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(app), default_timeout=60).run()
    assert not at.exception
    assert len(at.dataframe) == 0
    assert any("没有可对比的候选股" in item.value for item in at.caption)


def test_render_report_section_skips_none(tmp_path):
    """云端只读发布信号时 report=None：只给一句说明，不画表不画图。"""
    app = tmp_path / "none_app.py"
    app.write_text(
        """
from stock_plan.ui.signal_report_section import render_report_section

render_report_section(None)
""",
        encoding="utf-8",
    )
    at = AppTest.from_file(str(app), default_timeout=60).run()
    assert not at.exception
    assert len(at.dataframe) == 0
    assert any("云端只读发布信号" in item.value for item in at.caption)
