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