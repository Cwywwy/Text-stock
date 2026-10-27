# -*- coding: utf-8 -*-
"""市场情绪与资金流动倾向 —— 从日频快照里提炼可核对的指标。

设计原则（本项目方法论要求"先测量再下结论"）：

1. **不产出单一 0~100 情绪分**。任何阈值都要有历史分布做校准；现在只有 1 天快照，
   硬编阈值就是拍脑袋，还会被当成精确结论。所以这里只产出**原始指标**
   （家数、连板高度、炸板率、净流入分位），等快照攒够约 20 个交易日
   （一个月）再由上层做分位校准。
2. **涨停家数按申万一级聚合**，不按涨停池自带的行业名。实测涨停池的 24 个行业名里
   只有 9 个与同花顺行业名重合（东财叫「一般零售」「休闲食品」，同花顺叫「商业百货」…），
   跨源直接 join 会**静默丢数**。资金流仍按同花顺行业/概念名单独排序，并注明来源，
   不做跨源融合。
3. 三张资金流表的净额**不能互相加总**：实测同一天行业合计 −600.9 亿、
   个股合计 −779.8 亿，覆盖范围不同。只用各自的排序。
"""
from __future__ import annotations

import pandas as pd

from stock_plan.data import market_snapshot as ms

# 情绪摘要里各项的展示顺序
_SECTIONS = ("涨停跌停", "连板结构", "个股广度", "行业资金", "概念资金", "涨停行业")


def _yi(x: float) -> str:
    """把元转成「x.xx 亿」便于阅读；NaN 显示为 —。"""
    if x is None or pd.isna(x):
        return "—"
    return f"{x / 1e8:+.2f} 亿"


def latest_day() -> str | None:
    days = ms.available_days()
    return days[-1] if days else None


def limit_stats(day: str | None = None) -> dict:
    """涨停/跌停/炸板家数、最高连板、连板梯队、封板资金合计。"""
    d = ms.load_day(day)
    up = d.get(ms.LIMIT_UP)
    down = d.get(ms.LIMIT_DOWN)
    zb = d.get(ms.ZHABAN)

    n_up = 0 if up is None else len(up)
    n_down = 0 if down is None else len(down)
    n_zb = 0 if zb is None else len(zb)

    ladder: dict[int, int] = {}
    max_boards = 0
    seal_fund = 0.0
    if up is not None and len(up) and "boards" in up.columns:
        max_boards = int(pd.to_numeric(up["boards"], errors="coerce").fillna(0).max())
        for k, v in up["boards"].value_counts().sort_index().items():
            ladder[int(k)] = int(v)
        if "seal_fund" in up.columns:
            seal_fund = float(pd.to_numeric(up["seal_fund"], errors="coerce").sum())

    # 真实炸板率 = 炸板家数 /（涨停家数 + 炸板家数）。只算涨停池会系统性高估情绪。
    denom = n_up + n_zb
    break_rate = (n_zb / denom) if denom else float("nan")

    return {
        "day": ms.today_str(day) if day else (latest_day() or ""),
        "limit_up": n_up,
        "limit_down": n_down,
        "zhaban": n_zb,
        "break_rate": break_rate,
        "max_boards": max_boards,
        "ladder": ladder,
        "seal_fund": seal_fund,
        "up_down_ratio": (n_up / n_down) if n_down else float("inf") if n_up else float("nan"),
    }


def flow_breadth(day: str | None = None) -> dict:
    """个股层面的广度：上涨家数占比、净流入家数占比、净流入分位中位数。

    为什么看广度而不是只看指数涨跌：指数被权重股绑架，5000 只票里
    真正上涨的比例才是"赚钱效应"。
    """
    d = ms.load_day(day)
    stk = d.get(ms.FUND_STK)
    if stk is None or stk.empty:
        return {"day": ms.today_str(day), "n_stocks": 0, "up_share": float("nan"),
                "inflow_share": float("nan"), "median_net_pct": float("nan")}
    n = len(stk)
    pct = pd.to_numeric(stk.get("pct"), errors="coerce")
    net = pd.to_numeric(stk.get("net"), errors="coerce")
    return {
        "day": ms.today_str(day),
        "n_stocks": n,
        "up_share": float((pct > 0).sum() / n) if pct is not None else float("nan"),
        "inflow_share": float((net > 0).sum() / n) if net is not None else float("nan"),
        # 为什么是中位数净额而不是 net_pct 的中位数：net_pct 是**排名分位**，
        # 它的中位数按构造恒为 ≈0.5（实测 0.5001），与市场冷暖无关，是个空转指标。
        "median_net": float(net.median()) if net is not None else float("nan"),
    }


def fund_flow_rank(day: str | None = None, n: int = 5) -> dict[str, pd.DataFrame]:
    """行业 / 概念 / 个股的资金净流入、净流出排行。

    返回的 `net` 已统一为**元**（见 `market_snapshot._money_series`）；
    `pct` 是涨跌幅（%）。三者口径不同源，不要跨表相加。
    """
    d = ms.load_day(day)
    out: dict[str, pd.DataFrame] = {}

    for key, fname in (("industry", ms.FUND_IND), ("concept", ms.FUND_CON)):
        df = d.get(fname)
        if df is None or df.empty:
            continue
        cols = [c for c in ("board", "pct", "net", "n_stocks") if c in df.columns]
        out[f"{key}_top"] = df.nlargest(n, "net")[cols].reset_index(drop=True)
        out[f"{key}_bottom"] = df.nsmallest(n, "net")[cols].reset_index(drop=True)

    stk = d.get(ms.FUND_STK)
    if stk is not None and not stk.empty:
        cols = [c for c in ("code", "name", "pct", "net") if c in stk.columns]
        out["stock_top"] = stk.nlargest(n, "net")[cols].reset_index(drop=True)
        out["stock_bottom"] = stk.nsmallest(n, "net")[cols].reset_index(drop=True)

    return out


def limit_by_sw_l1(day: str | None = None) -> tuple[pd.DataFrame, str]:
    """按申万一级行业统计涨停家数。

    返回 ``(DataFrame[sw_l1, limit_up], 数据来源标签)``。
    月度分类快照缺失时**降级**为涨停池自带的东财行业名（标签会写明是降级），
    这样 UI 不会因为没跑月频任务就空白，但用户能看清口径不同。
    """
    d = ms.load_day(day)
    up = d.get(ms.LIMIT_UP)
    empty = pd.DataFrame(columns=["sw_l1", "limit_up"])
    if up is None or up.empty:
        return empty, "无数据"

    from stock_plan.data import classification

    try:
        table = classification.industry_table()
    except Exception:  # noqa: BLE001
        table = pd.DataFrame()
    if table is None or table.empty or "sw_l1" not in table.columns:
        if "industry" in up.columns:
            vc = up["industry"].value_counts()
            return (
                pd.DataFrame({"sw_l1": vc.index, "limit_up": vc.values}),
                "东财行业（降级：缺少申万月度快照）",
            )
        return empty, "无数据"

    l1 = dict(zip(table["code"].astype(str), table["sw_l1"].astype(str)))
    mapped = up["code"].astype(str).map(l1).dropna()
    vc = mapped.value_counts()
    return (
        pd.DataFrame({"sw_l1": vc.index, "limit_up": vc.values}),
        "申万一级",
    )


def emotion_summary(day: str | None = None) -> dict:
    """把上面所有指标汇成一个 dict（UI 和 LLM 都用它，避免各处重复拼装）。"""
    day = day or latest_day()
    ls = limit_stats(day)
    fb = flow_breadth(day)
    flow = fund_flow_rank(day, n=3)
    l1, l1_src = limit_by_sw_l1(day)
    return {
        "day": ls["day"],
        "limit": ls,
        "breadth": fb,
        "flow": flow,
        "limit_by_l1": l1,
        "limit_by_l1_source": l1_src,
    }


def _tail(df: pd.DataFrame, col: str = "net", n: int = 3) -> str:
    if df is None or df.empty:
        return "—"
    parts = []
    for _, r in df.head(n).iterrows():
        parts.append(f"{r['board']} {_yi(r[col])}")
    return " / ".join(parts)


def emotion_text(day: str | None = None) -> str:
    """把情绪摘要渲染成一段中文，供 UI 展示与 LLM 读。"""
    s = emotion_summary(day)
    ls, fb, flow = s["limit"], s["breadth"], s["flow"]
    lines = [f"【{s['day']} 市场情绪与资金】"]

    lines.append(
        f"涨停 {ls['limit_up']} 家 / 跌停 {ls['limit_down']} 家 / 炸板 {ls['zhaban']} 家"
        f" → 炸板率 {ls['break_rate'] * 100:.1f}%" if not pd.isna(ls["break_rate"])
        else f"涨停 {ls['limit_up']} 家 / 跌停 {ls['limit_down']} 家"
    )
    if ls["ladder"]:
        ladder = " ".join(f"{k}板×{v}" for k, v in sorted(ls["ladder"].items()))
        lines.append(f"最高 {ls['max_boards']} 板；连板梯队：{ladder}")
    if fb["n_stocks"]:
        lines.append(
            f"个股广度：{fb['n_stocks']} 只中上涨 {fb['up_share'] * 100:.1f}%、"
            f"主力净流入 {fb['inflow_share'] * 100:.1f}%"
        )
    if "industry_top" in flow:
        lines.append(f"行业净流入前三：{_tail(flow['industry_top'])}")
        lines.append(f"行业净流出前三：{_tail(flow['industry_bottom'])}")
    if "concept_top" in flow:
        lines.append(f"概念净流入前三：{_tail(flow['concept_top'])}")
    if not s["limit_by_l1"].empty:
        top = s["limit_by_l1"].head(5)
        parts = " / ".join(f"{r['sw_l1']} {int(r['limit_up'])}" for _, r in top.iterrows())
        lines.append(f"涨停集中行业（{s['limit_by_l1_source']}）：{parts}")
    lines.append(
        "注：家数与资金来自不同数据源，只做各自排序，不可跨源相加。"
        "单一情绪分需 ≥20 个交易日分布校准，暂不产出。"
    )
    return "\n".join(lines)
