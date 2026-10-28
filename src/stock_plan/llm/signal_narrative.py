# -*- coding: utf-8 -*-
"""文件名：signal_narrative.py
作者：Cwywwy
用途：把「信号解释报告」交给大模型写一段可读解读，并在模型输出不可信时回退模板。
所属模块：llm（大模型分析）

设计要点（对应 `.github/plans/2026-10-28-2-signal-explain-report.md` Phase 3）：

1. **数字闸门**：所有分数、价格、百分比都由代码计算并显示在表格里。模型可以**原样引用**
   事实清单里已有的数字，但输出里出现任何清单中不存在的数字（编造/推算的分数、比例）就判为
   不合格 → 回退模板。这样用户不会看到「模型说 93 分、表格写 87.6 分」这种编造。
2. **结构化白名单**：只接受 ``{"summary", "picks", "runners", "risks"}`` 四个字段，
   代码集合必须落在报告给出的代码里（不能编造不存在的股票），多余字段直接丢弃。
3. **模板兜底**：没配 key（mock）、超时、坏 JSON、编造代码、含数字、空串，全部回退到
   确定性的中文模板，并把原因一路带到页面显式展示 —— 绝不假装成功。
4. **落盘缓存**：同一份报告（任务名 + 事实哈希）只调用一次；命中时告诉用户「缓存于 x 分钟前」。

修订记录：
2026-10-28  (phase 3) llm narrative for signal comparison with digit guard and template fallback
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from stock_plan.llm import cache
from stock_plan.llm.client import ChatResult, get_client

TASK = "signal-narrative"
"""缓存任务名（缓存目录 data/cache/llm/）。"""

MAX_PROMPT_TOKENS = 1400
"""单次 prompt 的输入预算（估算值），超出时截断事实文本。"""

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
"""数字 token：模型可以**原样引用**事实清单里的数字，但不能自己编造或推算。"""

_PICK_NOTE_MAX = 80
_SUMMARY_MAX = 160
_RISK_MAX = 6

SYSTEM_PROMPT = """你是 A 股选股结果的解读助手。用户会给你一份「今天选出的股票 + 紧随其后没选上的股票」的客观事实清单，
请你写一段中文解读，帮助用户理解这次筛选的结果。

硬性要求（违反任何一条，本次输出会被丢弃）：
1. 输出**一个 JSON 对象**，不要 markdown 代码块、不要任何解释性前后文。
2. 字段只能是：summary（字符串）、picks（数组）、runners（数组）、risks（字符串数组）。
3. picks / runners 的每一项是 {"code": "股票代码", "note": "一句话理由"}；code 必须来自事实清单，
   不许编造；picks 只写入选的，runners 只写没选上的。
4. 可以**原样引用**事实清单里已经出现的数字（例如分数、数量），但不许自己编造、改写或推算
   任何新数字；拿不准就不写数字，改用「分数最高」「接近门槛」这类相对说法。
5. 不要给出买卖建议、不要预测涨跌、不要承诺收益；只解释这次的筛选结果。
6. 语气克制、具体，避免「值得关注」「有望」这类空话。"""


@dataclass(frozen=True)
class NarrativeResult:
    """一次解读的结果。

    ``source`` 取值：``llm``（模型生成）、``cache``（落盘缓存命中）、``template``（模板兜底）。
    ``error`` 只在回退时非空，页面必须原样展示，不要吞掉。
    """

    text: str
    source: str
    error: str = ""
    age: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """是否由模型生成（缓存也算）。"""
        return self.source in {"llm", "cache"}


def _codes(report: Any, attr: str) -> list[str]:
    """报告里某一组候选的代码（保持原顺序）。"""
    return [str(getattr(item, "code", "")) for item in (getattr(report, attr, ()) or ())]


def allowed_codes(report: Any) -> dict[str, str]:
    """允许出现的代码 → 角色（``picked`` / ``runner``），用于校验模型有没有编造股票。"""
    mapping: dict[str, str] = {}
    for code in _codes(report, "picked"):
        mapping.setdefault(code, "picked")
    for code in _codes(report, "runners_up"):
        mapping.setdefault(code, "runner")
    return mapping


def build_prompt(report: Any) -> str:
    """把报告事实拼成 prompt（过长时截断，保证单次调用有界）。"""
    facts = report.to_facts_text()
    # 事实文本本身已经压缩过（约 1–2 千字），这里只做一次上限保护，避免未来字段变多后失控。
    if len(facts) > MAX_PROMPT_TOKENS * 3:
        facts = facts[: MAX_PROMPT_TOKENS * 3] + "\n…（事实清单过长，已截断）"
    codes = "、".join(f"{code}（{role}）" for code, role in allowed_codes(report).items())
    return (
        f"{facts}\n\n"
        f"可引用的代码与角色：{codes}\n\n"
        "请严格按 JSON 输出。"
    )


def _pct_gap(report: Any) -> str:
    gap = getattr(report, "entry_gap", None)
    if gap is None:
        return "无法计算"
    note = gap.note() if hasattr(gap, "note") else ""
    return f"{gap.gap:+.1f} 分（{note}）" if hasattr(gap, "gap") else note


def template_narrative(report: Any) -> str:
    """确定性模板解读：数字全部来自代码计算，不依赖模型。"""
    universe = getattr(report, "universe", None)
    picked = list(getattr(report, "picked", ()) or ())
    runners = list(getattr(report, "runners_up", ()) or ())
    lines: list[str] = []

    if universe is not None:
        lines.append(
            f"本次从 {universe.total_in_list} 只股票里，先按硬过滤留下 {universe.passed} 只，"
            f"再按「{universe.strategy_name}」打分排序（数据截至 {universe.data_until}）。"
        )
    if picked:
        gate = picked[-1]
        lines.append(
            f"入选 {len(picked)} 只，门槛 {gate.score:.1f} 分（第 {gate.rank} 名 {gate.code} {gate.name}）。"
        )
        penalised = [item for item in picked if getattr(item, "penalties", ())]
        if penalised:
            names = "、".join(
                f"{item.code} {item.name}（{item.penalty_points:+.0f}）" for item in penalised
            )
            lines.append(f"其中被扣分拉低、靠原始分仍然入选的有：{names}。")
    if runners:
        lines.append(f"紧随其后 {len(runners)} 只与门槛的分差：{_pct_gap(report)}。")
        behind = [item for item in runners if getattr(item, "penalties", ())]
        for item in behind[:3]:
            reasons = "；".join(p.explain() for p in item.penalties)
            lines.append(f"- 第 {item.rank} 名 {item.code} {item.name}：{item.score:.1f} 分，{reasons}。")
    samples = list(getattr(report, "rejected_samples", ()) or ())
    if samples:
        kinds = "、".join(dict.fromkeys(item.reason_text for item in samples))
        lines.append(f"被硬过滤挡在门外的原因包括：{kinds}。")
    lines.append(
        "提醒：ST 判据用的是「今天」的状态而不是历史时点；分数只反映当天的规则打分，"
        "不代表未来收益。"
    )
    return "\n\n".join(lines)


def _facts_numbers(report: Any) -> set[str]:
    """事实清单里出现过的数字 token（模型只能引用这些）。"""
    try:
        facts = report.to_facts_text()
    except Exception:  # noqa: BLE001 - 事实文本异常不该让解读整块崩掉
        return set()
    return set(_NUM_RE.findall(facts or ""))


def _quoted(token: str, facts_numbers: set[str]) -> bool:
    """``token`` 是否可从事实清单里原样得到（允许 "13" 引用 "13.00" 这种整数部分）。"""
    if token in facts_numbers:
        return True
    return any(item == token or item.startswith(f"{token}.") for item in facts_numbers)


def _check_text_field(
    text: Any,
    *,
    limit: int,
    label: str,
    facts_numbers: set[str],
) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{label}为空")
    text = text.strip()
    if len(text) > limit:
        raise ValueError(f"{label}过长（{len(text)} 字 > {limit}）")
    invented = [token for token in _NUM_RE.findall(text) if not _quoted(token, facts_numbers)]
    if invented:
        raise ValueError(f"{label}出现了编造的数字 {'、'.join(dict.fromkeys(invented))}")
    return text


def validate_narrative(raw: str, report: Any) -> tuple[dict[str, Any] | None, str]:
    """校验模型输出；合格返回 ``(数据, "")``，不合格返回 ``(None, 原因)``。"""
    from stock_plan.llm.analyzer import _parse_json_dict

    data, err = _parse_json_dict(raw or "")
    if err:
        return None, f"不是合法 JSON（{err}）"
    if not isinstance(data, dict):
        return None, "不是 JSON 对象"

    allowed = allowed_codes(report)
    if not allowed:
        return None, "报告里没有可引用的股票"
    facts_numbers = _facts_numbers(report)

    try:
        summary = _check_text_field(
            data.get("summary"), limit=_SUMMARY_MAX, label="summary", facts_numbers=facts_numbers
        )
        picks_raw = data.get("picks") or []
        runners_raw = data.get("runners") or []
        risks_raw = data.get("risks") or []
        if not isinstance(picks_raw, list) or not isinstance(runners_raw, list):
            raise ValueError("picks / runners 不是数组")
        if not isinstance(risks_raw, list):
            raise ValueError("risks 不是数组")
        if len(risks_raw) > _RISK_MAX:
            raise ValueError(f"risks 超过 {_RISK_MAX} 条")

        picks: list[dict[str, str]] = []
        for item in picks_raw:
            if not isinstance(item, dict):
                raise ValueError("picks 的条目不是对象")
            code = str(item.get("code") or "").strip()
            if code not in allowed:
                raise ValueError(f"picks 里出现了事实清单之外的代码 {code or '(空)'}")
            if allowed[code] != "picked":
                raise ValueError(f"{code} 是落选股，不应出现在 picks")
            note = _check_text_field(
                item.get("note"), limit=_PICK_NOTE_MAX, label=f"{code} 的 note",
                facts_numbers=facts_numbers,
            )
            picks.append({"code": code, "note": note})

        runners: list[dict[str, str]] = []
        for item in runners_raw:
            if not isinstance(item, dict):
                raise ValueError("runners 的条目不是对象")
            code = str(item.get("code") or "").strip()
            if code not in allowed:
                raise ValueError(f"runners 里出现了事实清单之外的代码 {code or '(空)'}")
            if allowed[code] != "runner":
                raise ValueError(f"{code} 是入选股，不应出现在 runners")
            note = _check_text_field(
                item.get("note"), limit=_PICK_NOTE_MAX, label=f"{code} 的 note",
                facts_numbers=facts_numbers,
            )
            runners.append({"code": code, "note": note})

        risks: list[str] = []
        for item in risks_raw:
            # 模型有时会把风险写成 {"code": "...", "note": "..."}，取其中的文字即可
            raw_text = item.get("note") if isinstance(item, dict) else item
            risks.append(
                _check_text_field(
                    raw_text, limit=_PICK_NOTE_MAX, label="risks 的条目",
                    facts_numbers=facts_numbers,
                )
            )
    except ValueError as exc:
        return None, str(exc)

    if {item["code"] for item in picks} != {code for code, role in allowed.items() if role == "picked"}:
        return None, "picks 没有覆盖全部入选股"
    if runners and {item["code"] for item in runners} != {
        code for code, role in allowed.items() if role == "runner"
    }:
        return None, "runners 没有覆盖全部落选股"

    return {"summary": summary, "picks": picks, "runners": runners, "risks": risks}, ""


def render_narrative(data: dict[str, Any]) -> str:
    """把校验通过的结构渲染成页面用的 markdown。"""
    parts = [str(data.get("summary") or "").strip()]
    picks = data.get("picks") or []
    if picks:
        parts.append("**入选者为什么在名单里**")
        parts.extend(f"- `{item['code']}` {item['note']}" for item in picks)
    runners = data.get("runners") or []
    if runners:
        parts.append("**紧随者差在哪里**")
        parts.extend(f"- `{item['code']}` {item['note']}" for item in runners)
    risks = data.get("risks") or []
    if risks:
        parts.append("**风险提醒**")
        parts.extend(f"- {item}" for item in risks)
    return "\n\n".join(part for part in parts if part)


def _payload(report: Any) -> str:
    """缓存键用的输入指纹（报告已去掉生成时间，同一份数据稳定命中）。"""
    return json.dumps(report.cache_payload(), ensure_ascii=False, sort_keys=True)


def explain_signal_report(
    report: Any,
    *,
    client: Any | None = None,
    use_cache: bool = True,
    now: float | None = None,
) -> NarrativeResult:
    """生成（或回退）一段信号解读。

    顺序：空报告 → 缓存 → mock 检查 → 真实调用 → 校验 → 渲染；任何一步失败都回退模板并
    把原因写进 ``error``，绝不返回「看起来像模型说的」内容。
    """
    if report is None or getattr(report, "is_empty", False):
        return NarrativeResult(
            text="这次没有可解读的候选（数据未更新或全部被硬过滤）。",
            source="template",
            error="报告为空",
        )

    payload = _payload(report)
    if use_cache:
        entry = cache.load(TASK, payload, now=now)
        if entry is not None:
            return NarrativeResult(
                text=entry.text,
                source="cache",
                age=entry.describe_age(),
                usage=dict(getattr(entry, "meta", {}) or {}).get("usage", {}) or {},
            )

    c = client or get_client()
    if getattr(c, "mock", False):
        return NarrativeResult(
            text=template_narrative(report),
            source="template",
            error="未配置可用的 API key，显示模板解读",
        )

    prompt = build_prompt(report)
    result: ChatResult = c.chat_result(
        SYSTEM_PROMPT,
        prompt,
        task=TASK,
        temperature=0.4,
        use_cache=False,  # 缓存由本模块按「报告指纹」管理，粒度比 chat 的内部缓存更合适
    )
    if not result.ok:
        return NarrativeResult(
            text=template_narrative(report),
            source="template",
            error=result.error or "模型调用失败",
        )

    data, err = validate_narrative(result.text, report)
    if data is None:
        return NarrativeResult(
            text=template_narrative(report),
            source="template",
            error=f"模型输出不合格（{err}），已回退模板",
        )

    text = render_narrative(data)
    if use_cache:
        cache.store(TASK, payload, text=text, meta={"usage": result.usage or {}})
    return NarrativeResult(text=text, source="llm", usage=dict(result.usage or {}))
