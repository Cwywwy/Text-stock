"""LLM 输入预算：估算 token 数，并在超预算时按优先级裁剪上下文。

文件：src/stock_plan/llm/budget.py
作者：Cwywwy
用途：把「拼 prompt」和「怕拼太长」分开。调用方只声明若干段（Section），
      写清每段重要性（priority，小者优先保留）与单段上限（max_tokens），
      这里负责装箱、截断并记录「哪段被砍了」——被砍的事实要能显示给用户，
      否则用户会以为模型读到了全部数据。
所属模块：llm（基础设施层）

估算口径（保守，宁多勿少）：
- CJK 汉字/假名/全角标点：1 字 ≈ 1 token
- 其余可见 ASCII：4 字符 ≈ 1 token
- 空白：忽略
真实分词会把中文压得更紧（智谱/OpenAI 一般 1.5–2 汉字/token），
保守估算的代价只是少放几行，不会触发 400 超长。

修订记录：
2026-10-28 初版：estimate_tokens / truncate_text / Section / fit_sections（Phase 0）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# ASCII 可见字符折合 token 的系数（4 字符 ≈ 1 token）
_ASCII_CHARS_PER_TOKEN = 4.0

#: 截断后追加的省略标记
ELLIPSIS = "…"

#: 单段可用预算低于「该段需求的 20%」时整段丢弃，而不是塞一段无用的开头
_MIN_USEFUL_SHARE = 0.2


def _is_wide(ch: str) -> bool:
    """是否按「1 字 1 token」计的宽字符（CJK、假名、全角符号）。"""
    code = ord(ch)
    return (
        0x2E80 <= code <= 0x9FFF  # CJK 部首扩展 / 标点 / 假名 / 汉字
        or 0xF900 <= code <= 0xFAFF  # CJK 兼容汉字
        or 0xFF00 <= code <= 0xFFEF  # 全角字符
        or 0x20000 <= code <= 0x2FA1F  # 扩展 B 及之后
    )


def _char_cost(ch: str) -> float:
    """单个字符折算的 token 数。"""
    if ch.isspace():
        return 0.0
    return 1.0 if _is_wide(ch) else 1.0 / _ASCII_CHARS_PER_TOKEN


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数（向上取整）。空文本返回 0。"""
    if not text:
        return 0
    total = 0.0
    for ch in text:
        total += _char_cost(ch)
    return int(math.ceil(total))


def truncate_text(text: str, max_tokens: int, *, suffix: str = ELLIPSIS) -> str:
    """把文本压到 ``max_tokens`` 以内；尽量在换行/句号处断开。

    - ``max_tokens <= 0`` 返回空串
    - 未超限时原样返回（不做任何规范化）
    - 超限时按字符累加成本截断，末尾补 ``suffix``
    """
    if not text:
        return ""
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text

    budget = max_tokens - estimate_tokens(suffix)
    if budget <= 0:
        return ""
    used = 0.0
    kept: list[str] = []
    for ch in text:
        cost = _char_cost(ch)
        if used + cost > budget:
            break
        used += cost
        kept.append(ch)

    cut = "".join(kept).rstrip()
    # 尽量落在自然边界上：换行、句号、分号
    for sep in ("\n", "。", "；"):
        pos = cut.rfind(sep)
        if pos >= len(cut) * 0.6:
            cut = cut[: pos + len(sep)].rstrip()
            break
    return f"{cut}{suffix}" if cut else ""


@dataclass(frozen=True)
class Section:
    """一段待拼入 prompt 的上下文。

    Attributes:
        label: 段落标题（会作为 ``label：`` 输出，便于模型区分数据来源）。
        text: 正文。
        priority: 保留优先级，数字越小越先保留（0 = 最重要）。
        max_tokens: 单段上限；None 表示只受总预算约束。
        header: 是否输出标题行；False 时只拼正文（用于 system 提示这类段落）。
    """

    label: str
    text: str
    priority: int = 0
    max_tokens: int | None = None
    header: bool = True


@dataclass(frozen=True)
class FitResult:
    """装箱结果。

    Attributes:
        text: 拼好的 prompt 正文。
        estimated_tokens: 结果的实际估算 token 数。
        dropped: 因预算不足整段丢弃的 label（按丢弃顺序）。
        truncated: 被截断的 label。
    """

    text: str
    estimated_tokens: int
    dropped: tuple[str, ...] = ()
    truncated: tuple[str, ...] = ()
    sections: dict[str, str] = field(default_factory=dict)

    @property
    def lost(self) -> bool:
        """是否有任何内容被丢弃或截断（UI 可据此提示「本次上下文经裁剪」）。"""
        return bool(self.dropped or self.truncated)

    def loss_note(self) -> str:
        """给用户看的一句话说明，无损失时返回空串。"""
        parts: list[str] = []
        if self.truncated:
            parts.append("已截断：" + "、".join(self.truncated))
        if self.dropped:
            parts.append("已省略：" + "、".join(self.dropped))
        return "；".join(parts)


def fit_sections(
    sections: list[Section] | tuple[Section, ...],
    max_tokens: int,
    *,
    separator: str = "\n\n",
) -> FitResult:
    """按优先级把若干段装进 ``max_tokens`` 预算内。

    装箱顺序 = priority 升序（同优先级保持传入顺序）；输出顺序 = 传入顺序，
    这样 prompt 读起来仍是「策略 → 数据 → 问题」的自然结构。
    """
    if max_tokens <= 0:
        return FitResult(
            text="",
            estimated_tokens=0,
            dropped=tuple(s.label for s in sections),
        )

    remaining = max_tokens
    bodies: dict[str, str] = {}
    truncated: list[str] = []
    dropped: list[str] = []

    for _, section in sorted(
        enumerate(sections), key=lambda pair: (pair[1].priority, pair[0])
    ):
        header = f"{section.label}：\n" if section.header else ""
        overhead = estimate_tokens(header)
        if bodies:
            overhead += estimate_tokens(separator)
        allowed = remaining - overhead
        if allowed <= 0:
            dropped.append(section.label)
            continue
        usable = allowed if section.max_tokens is None else min(section.max_tokens, allowed)
        if usable <= 0:
            dropped.append(section.label)
            continue
        demand = estimate_tokens(section.text)
        # 只剩一点点预算时，塞一段被砍掉 95% 的文本只会误导模型 → 整段省略
        if demand > usable and len(sections) > 1 and usable < _MIN_USEFUL_SHARE * demand:
            dropped.append(section.label)
            continue
        body = truncate_text(section.text, usable)
        if not body:
            dropped.append(section.label)
            continue
        if body != section.text:
            truncated.append(section.label)
        bodies[section.label] = body
        remaining -= overhead + estimate_tokens(body)

    ordered = [
        f"{s.label}：\n{bodies[s.label]}" if s.header else bodies[s.label]
        for s in sections
        if s.label in bodies
    ]
    text = separator.join(ordered)
    return FitResult(
        text=text,
        estimated_tokens=estimate_tokens(text),
        dropped=tuple(dropped),
        truncated=tuple(truncated),
        sections=bodies,
    )
