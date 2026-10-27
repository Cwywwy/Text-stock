"""基本面因子计算模块。

输入：单只股票的财务指标 dict（fetcher.get_fundamentals 返回）。
输出：三个标准化分数（0-100），供策略打分使用。

分数说明：
- value_score   估值分：PE/PB 越低越便宜，分数越高
- growth_score  成长分：营收/净利润增速越高，分数越高
- quality_score 质量分：ROE 高、毛利率高、负债率低，分数越高

打分采用分段线性方式（简单易懂，适合入门），后续可替换为百分位排名。
"""
from __future__ import annotations

from typing import Any


def _score_pe(pe: float | None) -> float:
    """市盈率打分：越低越好。None（缺失）给中性分 50。"""
    if pe is None or pe <= 0:  # 亏损或缺失给中性分
        return 50.0
    if pe <= 15:
        return 90.0
    if pe <= 30:
        return 70.0
    if pe <= 50:
        return 50.0
    return 30.0


def _score_pb(pb: float | None) -> float:
    """市净率打分：越低越好。None 给中性分 50。"""
    if pb is None or pb <= 0:
        return 50.0
    if pb <= 1.5:
        return 90.0
    if pb <= 3:
        return 70.0
    if pb <= 5:
        return 50.0
    return 30.0


def _score_growth(growth: float | None) -> float:
    """增长率打分：越高越好。None 给中性分 50。"""
    if growth is None:
        return 50.0
    if growth >= 30:
        return 90.0
    if growth >= 10:
        return 70.0
    if growth >= 0:
        return 50.0
    return 30.0


def _score_roe(roe: float | None) -> float:
    """ROE 打分：越高越好。None 给中性分 50。"""
    if roe is None:
        return 50.0
    if roe >= 15:
        return 90.0
    if roe >= 10:
        return 70.0
    if roe >= 5:
        return 50.0
    return 30.0


def _score_margin(margin: float | None) -> float:
    """毛利率打分：越高越好。None 给中性分 50。"""
    if margin is None:
        return 50.0
    if margin >= 40:
        return 90.0
    if margin >= 25:
        return 70.0
    if margin >= 10:
        return 50.0
    return 30.0


def _score_debt(debt: float | None) -> float:
    """资产负债率打分：越低越好。None 给中性分 50。"""
    if debt is None:
        return 50.0
    if debt <= 30:
        return 90.0
    if debt <= 50:
        return 70.0
    if debt <= 70:
        return 50.0
    return 30.0


def compute_fundamental(fin: dict[str, Any]) -> dict[str, float]:
    """计算基本面分数。

    参数：
        fin: fetcher.get_fundamentals 返回的财务指标 dict，
             含 roe/revenue_growth/net_profit_growth/gross_margin/debt_ratio/eps/bvps/pe/pb。

    返回：
        dict，含 value_score / growth_score / quality_score 三个 0-100 分数。
    """
    # 估值分 = PE 分与 PB 分的平均
    value_score = (_score_pe(fin.get("pe")) + _score_pb(fin.get("pb"))) / 2

    # 成长分 = 营收增速分与净利润增速分的平均
    growth_score = (
        _score_growth(fin.get("revenue_growth"))
        + _score_growth(fin.get("net_profit_growth"))
    ) / 2

    # 质量分 = ROE 分、毛利率分、负债率分的平均
    quality_score = (
        _score_roe(fin.get("roe"))
        + _score_margin(fin.get("gross_margin"))
        + _score_debt(fin.get("debt_ratio"))
    ) / 3

    return {
        "value_score": round(value_score, 1),
        "growth_score": round(growth_score, 1),
        "quality_score": round(quality_score, 1),
    }


# 中性基本面分：三档都取 50（打分区间 0-100 的中点）。
# 2026-10-28 新增：回测把快照日**之前**的财务因子中性化时传空 dict，用这组值
# 表示"这一天没有可用的财务信息"，与"财务数据缺失"（compute_fundamental 会给 30 分）
# 区分开 —— 30 分是**利空**，50 分才是**无信息**，两者混用会让选股系统性偏向有数据的股票。
NEUTRAL_FUNDAMENTAL: dict[str, float] = {
    "value_score": 50.0,
    "growth_score": 50.0,
    "quality_score": 50.0,
}


def compute_fundamental_or_neutral(fin: dict[str, Any] | None) -> dict[str, float]:
    """把财务指标 dict 转成三档分数；空数据返回中性 50 分。

    为什么需要这个包装：`fundamental_composite` 与各策略读的是 **value_score /
    growth_score / quality_score** 这三个**算好的分数键**，而 `Storage.load_fundamentals`
    返回的是 **roe/revenue_growth/... 原始指标**。历史上两条路径都直接拿原始 dict 去读
    分数键，缺失时一律回退 50 —— 于是财务因子在实际运行中恒等于 50（2026-10-28 实测
    578 只股票 fund_score 全部为 50.0，唯一取值），"技术 0.6 + 基本面 0.4"里的基本面
    权重实际不起作用。本函数负责做这一步转换，两条路径（今日信号 / 回测）共用。
    """
    if not fin:
        return dict(NEUTRAL_FUNDAMENTAL)
    # 兼容"已经是算好的分数 dict"的调用方（历史数据/测试桩）：三个分数键齐全且
    # 没有任何原始指标时原样返回。否则会把 {'value_score': 90} 这类输入重新按缺失
    # 指标算成 50 —— 静默抹掉分数，是最难查的一类回归。
    score_keys = ("value_score", "growth_score", "quality_score")
    raw_keys = ("roe", "revenue_growth", "net_profit_growth", "gross_margin",
                "debt_ratio", "pe", "pb")
    if all(k in fin for k in score_keys) and not any(k in fin for k in raw_keys):
        return {k: float(fin[k]) for k in score_keys}
    return compute_fundamental(fin)


if __name__ == "__main__":
    # 简单自测：用浦发银行财务数据计算基本面分数
    from stock_plan.data.storage import Storage

    storage = Storage()
    fin = storage.load_fundamentals("600000")
    if fin is None:
        print("无财务缓存，请先运行 fetch_all")
    else:
        print("原始财务:", {k: v for k, v in fin.items() if k != "updated_at"})
        print("基本面分数:", compute_fundamental(fin))