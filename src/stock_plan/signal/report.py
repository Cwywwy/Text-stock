"""信号解释报告 — 把「为什么是这 5 只」变成可序列化、可校验的事实。

设计原则（对应 .github/plans/2026-10-28-2 的 P0 风险「最怕 LLM 编造数据」）：
1. **所有数字都由本模块从真实计算结果里读出来**，不允许 LLM 自己算或自己编。
2. 报告分三层：事实（本文件）→ 叙事（llm/analyzer 写）→ 兜底（模板）。
3. `SignalReport.to_facts_text()` 是喂给大模型的**唯一数字来源**；
   页面上展示的对比表/瀑布图也直接读同一份数据，因此两边不会不一致。

组成：
- `Penalty`          一条扣分明细（规则键、文案、分值、触发时的数值）
- `CandidateFacts`   一只候选股的完整事实（入选者与紧随者共用）
- `RejectedSample`   一只被硬过滤淘汰的样本股（含原因与数值）
- `EntryGap`         入选末位与紧随第 1 名的分差
- `UniverseFacts`    候选池口径（样本总数、硬过滤淘汰统计、数据截止日、策略指纹）
- `SignalReport`     以上全部 + 生成时间，`to_dict()` 供 UI 与缓存使用

⚠️ 事实边界：ST 判据是**今天**的 ST 状态（见 factors/filter.py 模块文档），
报告里会显式标注 `st_is_current=True`，不要把它说成历史时点的判定。

修订记录：
2026-10-28 新建（phase 1）信号解释报告数据层。
2026-10-29 (phase 11) SignalReport 增加 degraded 字段：缺字段被禁用的规则在报告里留痕。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

REPORT_VERSION = 1

#: 硬过滤原因 → 短标签（只用于给大模型的事实文本；完整文案见 factors.filter.REASON_LABELS）
_SHORT_REASON: dict[str, str] = {
    "st": "ST",
    "no_bars": "无日线",
    "empty_bars": "日线为空",
    "new_listing": "次新",
    "suspended": "停牌",
    "illiquid": "流动性不足",
}


def _round(value: Any, digits: int = 4) -> float | None:
    """安全取整：None/NaN 一律变 None，避免 JSON 里出现 NaN。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:  # NaN
        return None
    return round(number, digits)


def _pct_points(ratio: float | None, digits: int = 2) -> str:
    """把比率转成「+1.23%」形式；None 显示占位符。"""
    if ratio is None:
        return "—"
    return f"{ratio * 100:+.{digits}f}%"


def _num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def _signed(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:+.{digits}f}"


@dataclass(frozen=True)
class Penalty:
    """一条扣分记录（与 strategy/builtin.py 的 PENALTY_RULES 一一对应）。"""

    rule: str        # 规则键，如 dev_high
    label: str       # 中文文案
    points: float    # 扣分值（负数）
    basis: float | None = None  # 触发规则的原始数值（偏离率/RSI/均线比等）

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "label": self.label,
            "points": _round(self.points, 2),
            "basis": _round(self.basis, 6),
        }

    def explain(self) -> str:
        """一句话解释，供页面与 LLM 事实文本复用。"""
        if self.basis is None:
            return f"{self.label}（{self.points:+.0f} 分）"
        if self.rule in ("trend_down",):
            return f"{self.label}：ma20/ma60-1 = {_pct_points(self.basis)}（{self.points:+.0f} 分）"
        if self.rule in ("chase_high", "dev_high", "dev_low"):
            return f"{self.label}：偏离 ma20 {_pct_points(self.basis)}（{self.points:+.0f} 分）"
        if self.rule == "rsi_hot":
            return f"{self.label}：RSI14 = {_num(self.basis, 1)}（{self.points:+.0f} 分）"
        return f"{self.label}：{_num(self.basis)}（{self.points:+.0f} 分）"


@dataclass(frozen=True)
class CandidateFacts:
    """一只候选股的事实快照（入选者 rank<=picked_n，紧随者 rank 更大）。"""

    code: str
    name: str
    score: float
    rank: int
    picked: bool
    close: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    dev_ma20: float | None = None  # close/ma20 - 1
    rsi14: float | None = None
    vol_ratio: float | None = None
    trend_score: float | None = None
    fund_score: float | None = None
    bar_date: str = ""             # 该股最后一根日线的日期
    penalties: tuple[Penalty, ...] = ()

    @property
    def penalty_points(self) -> float:
        """扣分合计（负数或 0）。"""
        return float(sum(item.points for item in self.penalties))

    @property
    def base_score(self) -> float | None:
        """未扣分时的原始分（= 分数 - 扣分合计），用于解释"本来能排第几"。"""
        if self.score is None:
            return None
        return round(self.score - self.penalty_points, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "score": _round(self.score, 1),
            "rank": self.rank,
            "picked": self.picked,
            "close": _round(self.close, 2),
            "ma20": _round(self.ma20, 2),
            "ma60": _round(self.ma60, 2),
            "dev_ma20": _round(self.dev_ma20, 6),
            "rsi14": _round(self.rsi14, 1),
            "vol_ratio": _round(self.vol_ratio, 2),
            "trend_score": _round(self.trend_score, 1),
            "fund_score": _round(self.fund_score, 1),
            "bar_date": self.bar_date,
            "penalties": [item.to_dict() for item in self.penalties],
            "penalty_points": _round(self.penalty_points, 1),
        }

    def facts_line(self) -> str:
        """一行紧凑事实（供 LLM 读，也用于日志核对）。"""
        head = (
            f"第{self.rank}名 {self.code} {self.name}｜分数 {_num(self.score, 1)}"
            f"（趋势 {_num(self.trend_score, 0)} / 基本面 {_num(self.fund_score, 0)}）"
        )
        body = (
            f"收盘 {_num(self.close)}，偏离 ma20 {_pct_points(self.dev_ma20)}，"
            f"RSI14 {_num(self.rsi14, 1)}，量比 {_num(self.vol_ratio)}，"
            f"日线截至 {self.bar_date or '—'}"
        )
        if self.penalties:
            pen = "；".join(item.explain() for item in self.penalties)
            body += f"；扣分 {self.penalty_points:+.0f}：{pen}"
        else:
            body += "；无扣分"
        return f"{head}｜{body}"


@dataclass(frozen=True)
class RejectedSample:
    """一只被硬过滤淘汰的样本股。"""

    code: str
    name: str
    reason: str
    reason_text: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "reason": self.reason,
            "reason_text": self.reason_text,
            "detail": {k: _round(v, 2) if isinstance(v, (int, float)) else v
                       for k, v in self.detail.items()},
        }

    def facts_line(self) -> str:
        extra = "，".join(f"{k}={v}" for k, v in self.detail.items())
        return f"{self.code} {self.name}｜{self.reason_text}" + (f"（{extra}）" if extra else "")


@dataclass(frozen=True)
class EntryGap:
    """入选末位与紧随第 1 名的分差 —— 门槛有多"挤"的直接证据。"""

    last_picked_score: float | None
    next_score: float | None

    @property
    def gap(self) -> float | None:
        if self.last_picked_score is None or self.next_score is None:
            return None
        return round(self.last_picked_score - self.next_score, 4)

    def note(self) -> str:
        gap = self.gap
        if gap is None:
            return "分差无法计算（样本不足）"
        if gap < 0:
            return f"入选末位反而低于紧随第 1 名 {abs(gap):.1f} 分（并列或截断边界）"
        if gap < 1:
            return f"门槛非常接近：仅相差 {gap:.1f} 分，紧随者随时可能反超"
        if gap < 5:
            return f"门槛接近：相差 {gap:.1f} 分"
        return f"门槛清晰：相差 {gap:.1f} 分"

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_picked_score": _round(self.last_picked_score, 1),
            "next_score": _round(self.next_score, 1),
            "gap": _round(self.gap, 1),
            "note": self.note(),
        }


@dataclass(frozen=True)
class UniverseFacts:
    """候选池口径：样本从哪来、排除了多少、数据到哪天。"""

    total_in_list: int = 0          # 股票列表总数
    with_bars: int = 0              # 有日线缓存的只数
    passed: int = 0                 # 通过硬过滤的只数
    rejected_counts: dict[str, int] = field(default_factory=dict)
    data_until: str = ""            # 所用日线的最后日期（如实反映"数据到哪天"）
    strategy_name: str = ""
    strategy_fingerprint: str = ""  # sha1(name+params)[:12]
    st_is_current: bool = True      # ST 判据是否为"今天"的状态（审计护栏）

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_in_list": self.total_in_list,
            "with_bars": self.with_bars,
            "passed": self.passed,
            "rejected_counts": dict(self.rejected_counts),
            "data_until": self.data_until,
            "strategy_name": self.strategy_name,
            "strategy_fingerprint": self.strategy_fingerprint,
            "st_is_current": self.st_is_current,
        }


def strategy_fingerprint(strategy: Any) -> str:
    """策略指纹：名字 + 参数，用于判断信号出自哪份配置。"""
    name = str(getattr(strategy, "name", type(strategy).__name__))
    params = getattr(strategy, "params", {}) or {}
    payload = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(f"{name}\x00{payload}".encode()).hexdigest()[:12]


@dataclass(frozen=True)
class SignalReport:
    """一次完整的信号解释报告。"""

    generated_at: datetime
    universe: UniverseFacts
    picked: tuple[CandidateFacts, ...] = ()
    runners_up: tuple[CandidateFacts, ...] = ()
    rejected_samples: tuple[RejectedSample, ...] = ()
    entry_gap: EntryGap | None = None
    warnings: tuple[str, ...] = ()
    # 缺字段降级：早期 CustomStrategy 遇到缺列会「静默跳过」那条规则，
    # 用户看到的是「策略跑完了」其实少算了几条。现在逐条记录，报告里显式展示。
    degraded: tuple[str, ...] = ()

    @property
    def version(self) -> int:
        return REPORT_VERSION

    @property
    def is_empty(self) -> bool:
        return not self.picked

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "generated_at": self.generated_at.isoformat(timespec="seconds"),
            "universe": self.universe.to_dict(),
            "picked": [item.to_dict() for item in self.picked],
            "runners_up": [item.to_dict() for item in self.runners_up],
            "rejected_samples": [item.to_dict() for item in self.rejected_samples],
            "entry_gap": self.entry_gap.to_dict() if self.entry_gap else None,
            "warnings": list(self.warnings),
            "degraded": list(self.degraded),
        }

    def cache_payload(self) -> dict[str, Any]:
        """缓存键用载荷：去掉生成时间，同一份事实才算命中。"""
        payload = self.to_dict()
        payload.pop("generated_at", None)
        return payload

    def to_facts_text(self) -> str:
        """供 LLM 使用的紧凑事实文本（唯一数字来源，禁止模型自行推算）。"""
        lines: list[str] = []
        universe = self.universe
        lines.append(
            f"【策略】{universe.strategy_name or '未命名'}（指纹 {universe.strategy_fingerprint or '—'}）"
        )
        lines.append(
            f"【数据】日线截至 {universe.data_until or '—'}；股票列表 {universe.total_in_list} 只，"
            f"有日线 {universe.with_bars} 只，通过硬过滤 {universe.passed} 只"
        )
        if universe.rejected_counts:
            drop = "／".join(
                f"{_SHORT_REASON.get(key, key)} {value}"
                for key, value in universe.rejected_counts.items()
                if value and key != "passed"
            )
            lines.append(f"【硬过滤淘汰】{drop or '无'}")
        lines.append("")
        if self.picked:
            lines.append(f"【入选 {len(self.picked)} 只（按分数降序）】")
            lines.extend(f"{item.facts_line()}" for item in self.picked)
        else:
            lines.append("【入选】本次没有股票通过全部条件")
        if self.runners_up:
            lines.append("")
            lines.append(f"【紧随其后 {len(self.runners_up)} 只（未入选）】")
            lines.extend(f"{item.facts_line()}" for item in self.runners_up)
        if self.entry_gap:
            lines.append("")
            lines.append(
                f"【入选门槛】末位 {_num(self.entry_gap.last_picked_score, 1)} 分，"
                f"紧随第 1 名 {_num(self.entry_gap.next_score, 1)} 分；"
                f"分差 {_signed(self.entry_gap.gap, 1)}（{self.entry_gap.note()}）"
            )
        if self.rejected_samples:
            lines.append("")
            lines.append("【硬过滤淘汰样本（每类各 1 只）】")
            lines.extend(item.facts_line() for item in self.rejected_samples)
        if self.warnings:
            lines.append("")
            lines.append("【口径提醒】")
            lines.extend(f"- {item}" for item in self.warnings)
        if self.degraded:
            lines.append("")
            lines.append("【降级规则：因缺字段今天没算】")
            lines.extend(f"- {item}" for item in self.degraded)
        return "\n".join(lines)
