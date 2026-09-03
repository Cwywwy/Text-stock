"""LLM 分析器 — 消息面分析 / 策略生成 / 信号解释 / 回测复盘。

所有函数在无 API Key 时自动降级为规则化模板输出（mock 模式），
保证 UI 在未配置 LLM 时也能正常展示。
"""
from __future__ import annotations

from typing import Optional

from stock_plan.llm.client import LLMClient, get_client

_SYSTEM_NEWS = (
    "你是一位资深 A 股分析师。请基于给定的个股新闻/公告摘要，"
    "给出简短的消息面判断（利好/利空/中性）、影响程度（高/中/低）与一句话理由。"
    "输出格式：\n"
    "判断：利好|利空|中性\n"
    "影响：高|中|低\n"
    "理由：一句话"
)

_SYSTEM_STRATEGY_CFG = (
    "你是量化策略专家。把用户的自然语言投资偏好转换为结构化选股策略配置。\n"
    "只输出一个 JSON 对象（不要 markdown 代码块、不要多余文字），格式：\n"
    "{\n"
    '  "name": "策略名（10字内）",\n'
    '  "config": {weights/rules/params 可平铺或分组，见下方词表},\n'
    '  "unsupported": [{"name": "缺失能力名", "description": "用户想要什么", "suggestion": "建议补充什么参数/数据"}],\n'
    '  "code": "仅当词表无法表达用户意图时，给出一段 Python 选股过滤/打分逻辑代码（pandas，'
    "输入 df_factors 含 code/close/atr14/ma5/ma10/ma20/ma60/rsi14/vol_ratio/mom_ret/"
    'high20_ratio/macd/macd_signal/avg_amount20/trend_score/fund_score 列），否则留空",\n'
    '  "reason": "一句话说明你的取值逻辑"\n'
    "}\n"
    "词表（只能使用这些键，取值范围见说明）：\n"
)


_SYSTEM_SIGNAL = (
    "你是一位 A 股盘前选股助手。请解释以下选股信号：为什么选这只票、"
    "买入/卖出/止损价位的含义、以及风险提示。用 3-5 句话，口语化。"
)

_SYSTEM_REVIEW = (
    "你是一位量化策略复盘专家。请基于回测指标、退出原因归因与当前策略参数，"
    "输出一份复盘报告，包含：\n"
    "1. 总体评价（收益/回撤/胜率/盈亏比是否健康）\n"
    "2. 问题诊断（亏损主要来自哪种退出原因、哪些环节拖累收益）\n"
    "3. 参数优化建议（针对 m/n/hold 等给出具体调整方向与理由）\n"
    "4. 风险提示\n"
    "用 5-8 句话，口语化，不要输出 JSON。"
)


def analyze_news(code: str, name: str, news_items: list[dict], client: Optional[LLMClient] = None) -> str:
    """分析个股消息面。

    参数：
        code: 股票代码。
        name: 股票名称。
        news_items: 新闻列表，每项含 title/time/source。
        client: LLM 客户端（默认全局单例）。

    返回：
        分析文本。
    """
    c = client or get_client()
    if not news_items:
        return "暂无该股新闻数据。"
    lines = []
    for n in news_items[:8]:
        lines.append(f"- [{n.get('time', '')}] {n.get('title', '')}（{n.get('source', '')}）")
    user = f"股票：{code} {name}\n新闻摘要：\n" + "\n".join(lines)
    return c.chat(_SYSTEM_NEWS, user)


def _vocab_text() -> str:
    """把参数词表拼成 LLM 提示（来自 codegen，单一事实来源）。"""
    from stock_plan.strategy.codegen import PARAM_SPEC, RULE_SPEC, WEIGHT_SPEC

    lines = []
    for key, (cn, desc, default, _t) in WEIGHT_SPEC.items():
        lines.append(f"- weights.{key}（{cn}，默认 {default}）：{desc}")
    for key, (cn, desc, default, _t) in RULE_SPEC.items():
        lines.append(f"- rules.{key}（{cn}，默认 {_default_show(default)}）：{desc}")
    for key, (cn, desc, default, _t) in PARAM_SPEC.items():
        lines.append(f"- params.{key}（{cn}，默认 {default}）：{desc}")
    lines.append("- name（策略名）")
    return "\n".join(lines)


def _default_show(default) -> str:
    if isinstance(default, bool):
        return "true/false" if default else "false"
    return str(default)


def generate_strategy_config(description: str, client: Optional[LLMClient] = None) -> dict:
    """根据自然语言描述生成结构化策略配置（V4 需求：参数优先，代码提案兜底）。

    返回 dict：
        config:         清洗后的合法 CustomStrategy 配置
        unknown:        无法识别的键（词表外）
        unsupported:    未支持参数反馈清单 [{name, description, suggestion}]
        proposal_code:  自定义代码提案（仅当词表不够表达时；不参与实际选股）
        reason:         LLM 一句话取值逻辑
        mode:           "llm" | "mock"
    """
    c = client or get_client()
    system = _SYSTEM_STRATEGY_CFG + _vocab_text()

    def _empty(reason: str, mode: str) -> dict:
        return {
            "config": {},
            "unknown": [],
            "unsupported": [],
            "proposal_code": "",
            "reason": reason,
            "mode": mode,
        }

    if c.mock:
        return _mock_strategy_config(description)

    raw = c.chat(system, description)
    data, err = _parse_json_dict(raw)
    if err:
        return _empty(f"LLM 输出解析失败（{err}），请重试或换个描述。", "llm")

    from stock_plan.strategy.codegen import normalize_config

    raw_cfg = data.get("config", data)
    config, unknown = normalize_config(raw_cfg)
    unsupported = [
        {
            "name": str(u.get("name", ""))[:50],
            "description": str(u.get("description", ""))[:200],
            "suggestion": str(u.get("suggestion", ""))[:200],
        }
        for u in (data.get("unsupported") or [])
        if isinstance(u, dict)
    ]
    code = str(data.get("code") or "").strip()
    # 剥离可能的 markdown 围栏
    if code.startswith("```"):
        code = _strip_code_fence(code)
    return {
        "config": config,
        "unknown": unknown,
        "unsupported": unsupported,
        "proposal_code": code,
        "reason": str(data.get("reason") or "")[:300],
        "mode": "llm",
    }


def _parse_json_dict(text: str) -> tuple[dict | None, str]:
    """尽力从 LLM 回复中提取 JSON 对象。"""
    import json

    text = (text or "").strip()
    candidates = [text]
    if "```" in text:
        candidates.insert(0, _strip_code_fence(text))
    # 截取第一处 { 到最后一处 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.insert(0, text[start : end + 1])
    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data, ""
        except (ValueError, TypeError):
            continue
    return None, "未找到合法 JSON"


def _mock_strategy_config(description: str) -> dict:
    """离线规则模式：关键词启发式生成配置（无 LLM 也可用）。"""
    import re

    from stock_plan.strategy.codegen import normalize_config

    desc = description or ""
    raw: dict = {"name": "LLM 生成策略（离线模式）"}
    # 均线："X日均线附近买入" / "5日均线站上20日均线"
    mas = [int(m) for m in re.findall(r"(\d{1,2})\s*日均线", desc)]
    if len(mas) >= 2:
        raw["trend_ma_fast"], raw["trend_ma_slow"] = min(mas[:2]), max(mas[:2])
    elif mas:
        raw["dev_ma"] = mas[0]
        raw["dev_min"], raw["dev_max"] = -5.0, 5.0
    # 持仓天数
    m = re.search(r"持有\s*(\d{1,3})\s*天", desc)
    if m:
        raw["hold_days"] = int(m.group(1))
    # 止盈止损（按 ATR 倍数描述）
    m = re.search(r"止盈[^\d]{0,4}(\d+(?:\.\d+)?)", desc)
    if m:
        raw["atr_m_exit"] = float(m.group(1))
    m = re.search(r"止损[^\d]{0,4}(\d+(?:\.\d+)?)", desc)
    if m:
        raw["atr_n_stop"] = float(m.group(1))
    # RSI 区间
    m = re.search(r"RSI[^\d]{0,6}(\d{1,3})", desc, re.IGNORECASE)
    if m:
        raw["rsi_max"] = min(float(m.group(1)), 100)
    # 放量
    if "放量" in desc:
        raw["vol_surge_min"] = 1.5
        raw["vol_surge_bonus"] = 10.0
    # 流动性
    m = re.search(r"成交额[^\d]{0,6}(\d+(?:\.\d+)?)", desc)
    if m or "流动性" in desc or "冷门" in desc:
        raw["liquidity_min"] = float(m.group(1)) if m else 0.5
    # MACD
    if "MACD" in desc.upper() or "金叉" in desc:
        raw["macd_golden"] = True
    if "零轴" in desc:
        raw["macd_above_zero"] = True
    # 突破
    if "新高" in desc or "突破" in desc:
        raw["require_breakout"] = True

    config, unknown = normalize_config(raw)

    # 未支持能力识别（供用户日后补充新参数）
    unsupported = []
    future_map = [
        ("美股隔夜行情", ["美股", "纳指", "标普", "道琼斯", "隔夜外盘"]),
        ("股指期货交割日", ["股指交割", "期指交割", "交割日"]),
        ("期权交割日", ["期权交割"]),
        ("龙虎榜/资金流数据", ["龙虎榜", "资金流", "北向资金"]),
        ("涨停板情绪周期", ["连板", "涨停潮", "情绪周期"]),
    ]
    for name, kws in future_map:
        if any(k in desc for k in kws):
            unsupported.append({
                "name": name,
                "description": f"描述中提到「{name}」相关的选股依据，当前系统暂无该数据。",
                "suggestion": f"需新增「{name}」数据源与因子列后，再以参数形式加入词表。",
            })
    if unknown:
        unsupported.append({
            "name": "未识别参数",
            "description": f"以下参数不在系统词表中，已忽略：{', '.join(unknown)}",
            "suggestion": "可从描述中删去，或日后把该能力加入系统词表。",
        })
    return {
        "config": config,
        "unknown": unknown,
        "unsupported": unsupported,
        "proposal_code": "",
        "reason": "离线关键词匹配生成；配置 .env 启用真实 LLM 后效果更佳。",
        "mode": "mock",
    }



def explain_signal(signal: dict, client: Optional[LLMClient] = None) -> str:
    """解释单个选股信号。

    参数：
        signal: 信号 dict（含 code/name/score/entry_price/exit_price/stop_loss/reason 等）。
        client: LLM 客户端。

    返回：
        解释文本。
    """
    c = client or get_client()
    buy = signal.get("entry_price", signal.get("buy_price", 0))
    sell = signal.get("exit_price", signal.get("sell_price", 0))
    user = (
        f"股票：{signal.get('code')} {signal.get('name', '')}\n"
        f"综合评分：{signal.get('score', 0)}\n"
        f"建议买入价：{buy}\n"
        f"目标卖出价：{sell}\n"
        f"止损价：{signal.get('stop_loss', 0)}\n"
        f"选股理由：{signal.get('reason', '')}"
    )
    return c.chat(_SYSTEM_SIGNAL, user)


def review_backtest(
    metrics: dict,
    report: dict,
    config: dict,
    client: Optional[LLMClient] = None,
) -> str:
    """复盘回测结果并给出参数优化建议。

    参数：
        metrics: 回测指标 dict（total_return/max_drawdown/win_rate/profit_loss/sharpe 等）。
        report: 回测报告 dict（含 exit_reason_attribution 等）。
        config: 策略配置 dict（m/n/hold/rebalance_freq/market_timing 等）。
        client: LLM 客户端。

    返回：
        复盘文本。mock 模式下返回规则化复盘建议。
    """
    c = client or get_client()
    if c.mock:
        return _mock_review(metrics, report, config)

    user = (
        "回测指标：\n"
        f"- 总收益：{metrics.get('total_return', 0)}%\n"
        f"- 最大回撤：{metrics.get('max_drawdown', 0)}%\n"
        f"- 胜率：{metrics.get('win_rate', 0)}%\n"
        f"- 盈亏比：{metrics.get('profit_loss', 0)}\n"
        f"- 交易笔数：{metrics.get('trade_count', 0)}\n"
        f"- 夏普比率：{metrics.get('sharpe', 0)}\n"
        "退出原因归因：\n"
        f"{report.get('exit_reason_attribution', {})}\n"
        "当前策略参数：\n"
        f"{config}"
    )
    return c.chat(_SYSTEM_REVIEW, user)


def _mock_review(metrics: dict, report: dict, config: dict) -> str:
    """mock 模式：基于规则生成复盘建议（不调用外部 API）。"""
    total = metrics.get("total_return", 0)
    dd = metrics.get("max_drawdown", 0)
    win = metrics.get("win_rate", 0)
    pl = metrics.get("profit_loss", 0)
    sharpe = metrics.get("sharpe", 0)

    lines = ["【未配置 LLM API Key，当前为离线规则复盘】", ""]

    # 1. 总体评价
    if total > 0:
        lines.append(f"✅ 总体评价：策略样本外总收益 {total:.2f}%，实现正收益。")
    else:
        lines.append(f"⚠️ 总体评价：策略总收益 {total:.2f}%，处于亏损状态，需重点优化。")
    if abs(dd) > 20:
        lines.append(f"⚠️ 最大回撤 {dd:.2f}% 偏大，风险控制需加强。")
    elif abs(dd) > 10:
        lines.append(f"ℹ️ 最大回撤 {dd:.2f}%，处于中等水平。")
    else:
        lines.append(f"✅ 最大回撤 {dd:.2f}%，回撤控制良好。")
    if win >= 50:
        lines.append(f"✅ 胜率 {win:.2f}% 较高。")
    else:
        lines.append(f"ℹ️ 胜率 {win:.2f}% 偏低，但若盈亏比 {pl:.2f} 大于 1 仍可盈利。")
    if sharpe > 1:
        lines.append(f"✅ 夏普比率 {sharpe:.2f}，风险调整后收益优秀。")
    elif sharpe > 0:
        lines.append(f"ℹ️ 夏普比率 {sharpe:.2f}，风险调整后收益一般。")
    else:
        lines.append(f"⚠️ 夏普比率 {sharpe:.2f}，风险调整后收益为负。")

    # 2. 退出原因归因
    lines.append("")
    lines.append("📊 退出原因归因：")
    attr = report.get("exit_reason_attribution", {})
    if attr:
        for reason, info in attr.items():
            avg = info.get("avg_return", 0)
            cnt = info.get("count", 0)
            mark = "✅" if avg > 0 else "⚠️"
            lines.append(f"  {mark} {reason}：{cnt} 笔，平均收益 {avg:.2f}%")
    else:
        lines.append("  （无退出归因数据）")

    # 3. 参数优化建议
    lines.append("")
    lines.append("🔧 参数优化建议：")
    m = config.get("m", 3.5)
    n = config.get("n", 3.5)
    hold = config.get("hold", 30)
    if total < 0:
        lines.append(f"  - 当前 m={m}/n={n}/hold={hold} 组合亏损，建议：")
        lines.append("    · 若止损单过多（平均收益为负），可适当放宽 n（如 3.5→4.0）减少误杀；")
        lines.append("    · 若止盈单过少，可降低 m（如 3.5→3.0）让利润更快落袋；")
        lines.append("    · 尝试缩短 hold（如 30→20）减少持仓期内的回撤暴露。")
    else:
        lines.append(f"  - 当前 m={m}/n={n}/hold={hold} 组合盈利，可保持或微调：")
        lines.append("    · 在邻域内做小步扫描（m/n ±0.5，hold ±10）寻找更优组合；")
        lines.append("    · 关注回撤，若回撤偏大可考虑开启大盘择时（market_timing）。")
    if n and m and n >= m:
        lines.append("  - ⚠️ 止损倍数 n 不小于止盈倍数 m，盈亏结构可能失衡，建议 n < m。")

    # 4. 风险提示
    lines.append("")
    lines.append("🚨 风险提示：")
    lines.append("  - 回测基于历史数据，未来收益不保证；样本外表现可能显著弱于样本内。")
    lines.append("  - 建议用 Walk-Forward 滚动验证确认参数稳健性，避免过拟合。")
    lines.append("  - 实盘前先在模拟交易模块小仓位验证。")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 四大师价值投资分析（巴菲特 / 芒格 / 段永平 / 李录）
# ---------------------------------------------------------------------------

_SYSTEM_FOUR_MASTERS = (
    "你是价值投资研究助手，需从四位大师的视角对给定个股做对抗式分析：\n"
    "1. 巴菲特（护城河与长期盈利）：关注 ROE、毛利率、品牌与竞争壁垒；\n"
    "2. 芒格（多元思维与质量）：关注商业模式的可理解性、管理层理性、行业格局；\n"
    "3. 段永平（本分与生意本质）：关注是否『看得懂的生意』、差异化与用户导向；\n"
    "4. 李录（长期确定性）：关注行业空间、公司治理、长期复利能力。\n"
    "对每位大师输出：1-5 星评分 + 一句核心观点 + 一条否决清单检查（列出可能触发一票否决的问题）。\n"
    "最后给出：整体结论（通过 / 灰色地带 / 不通过）与分层建议（买入区间观察、跟踪要点）。\n"
    "输出为 Markdown，总长不超过 600 字。"
)


def four_masters_analysis(stock: dict, client: Optional[LLMClient] = None) -> str:
    """从巴菲特/芒格/段永平/李录四个视角分析个股基本面。

    参数：
        stock: 个股数据 dict，建议包含 code/name/value_score/growth_score/
               quality_score/fund_score 等字段（缺失时降级处理）。
        client: LLM 客户端（默认全局单例）。

    返回：
        Markdown 格式的四大师分析报告。
    """
    c = client or get_client()
    if c.mock:
        return _mock_four_masters(stock)
    facts = {
        "code": stock.get("code", ""),
        "name": stock.get("name", ""),
        "value_score": stock.get("value_score"),
        "growth_score": stock.get("growth_score"),
        "quality_score": stock.get("quality_score"),
        "fund_score": stock.get("fund_score"),
        "roe": stock.get("roe"),
        "pe": stock.get("pe"),
    }
    facts = {k: v for k, v in facts.items() if v is not None}
    user = f"请分析以下个股：{facts}"
    return _strip_code_fence(c.chat(_SYSTEM_FOUR_MASTERS, user, temperature=0.4))


def _strip_code_fence(text: str) -> str:
    """剥离模型返回中误带的 markdown 代码围栏，保证正常渲染。"""
    t = text.strip()
    if t.startswith("```"):
        lines = t.split("\n")
        lines = lines[1:]  # 去掉首行 ```markdown
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        t = "\n".join(lines)
    return t.strip()


def _stars(score, threshold: float = 80.0) -> str:
    """按百分制分数生成 1-5 星标记。"""
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "☆（数据缺失）"
    if s >= threshold * 0.9:
        return "★★★★★"
    if s >= threshold * 0.75:
        return "★★★★"
    if s >= threshold * 0.6:
        return "★★★"
    if s >= threshold * 0.45:
        return "★★"
    return "★"


def _mock_four_masters(stock: dict) -> str:
    """离线规则版四大师分析：按价值/成长/质量分数打星并给出结论。"""
    name = stock.get("name") or stock.get("code") or "该股"
    v, g, q = stock.get("value_score"), stock.get("growth_score"), stock.get("quality_score")
    avg = 0.0
    n = 0
    for x in (v, g, q):
        try:
            avg += float(x)
            n += 1
        except (TypeError, ValueError):
            pass
    avg = avg / n if n else 0.0

    lines = [
        "【LLM 离线规则模式】以下为基于基本面分数的规则化分析，配置 .env 后可获得更深入解读。",
        "",
        f"### 📊 {name} 四大师视角速评",
        "",
        f"**巴菲特（护城河）** {_stars(q, 80)}  核心观点："
        + ("质量分较高，具备一定长期盈利基础。" if _num(q) >= 60 else "质量维度偏弱，护城河证据不足。"),
        f"  否决检查：{'未发现 ROE/盈利能力硬伤' if _num(q) >= 50 else '⚠️ 盈利能力可能触发一票否决'}",
        "",
        f"**芒格（多元思维）** {_stars(g, 80)}  核心观点："
        + ("成长性尚可，商业模式值得跟踪。" if _num(g) >= 60 else "成长动能不足，需确认行业空间。"),
        f"  否决检查：{'无结构性恶化信号' if _num(g) >= 50 else '⚠️ 增长停滞需警惕'}",
        "",
        f"**段永平（生意本质）** {_stars(q, 75)}  核心观点："
        + ("生意可理解性较好，聚焦主业。" if _num(q) >= 60 else "生意本质需进一步研究，暂列观察。"),
        "",
        f"**李录（长期确定性）** {_stars(avg, 80)}  核心观点："
        + ("综合分数达标，具备长期跟踪价值。" if avg >= 60 else "长期确定性不足，建议小仓观察或放弃。"),
        f"  否决检查：{'估值未明显透支' if _num(v) >= 50 else '⚠️ 估值偏高可能否决买入'}",
        "",
        "---",
        "",
    ]

    if avg >= 70:
        concl = "**✅ 通过**：可在合理估值区间分批建立观察仓，跟踪季度基本面。"
    elif avg >= 50:
        concl = "**🟡 灰色地带**：列入观察名单，等待更好价格或基本面改善信号。"
    else:
        concl = "**❌ 不通过**：暂不纳入长线组合。"
    lines.append(f"**整体结论**：{concl}")
    lines.append("")
    lines.append("**分层建议**：先跟踪 1-2 个财报周期，验证质量与成长分数的持续性，再决定仓位。")
    lines.append("")
    lines.append("> ⚠️ 免责声明：本框架为长线价值投资视角，与盘前短线选股定位不同，仅供参考，不构成投资建议。")
    return "\n".join(lines)


def _num(x) -> float:
    """安全转 float，失败返回 0。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0