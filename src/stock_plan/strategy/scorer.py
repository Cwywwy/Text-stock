"""打分模型模块 — 把技术面/基本面/题材面合成一个 0-100 综合分。

MVP 阶段：只合成技术面 + 基本面，题材面权重为 0（等 V1 接入题材数据）。
权重可调：默认技术 0.6 / 基本面 0.4。
"""
from __future__ import annotations

import pandas as pd


def composite_score(
    tech: pd.Series,
    fund: pd.Series,
    theme: pd.Series | None = None,
    weights: tuple[float, float, float] = (0.6, 0.4, 0.0),
) -> pd.Series:
    """加权综合分（0-100）。

    参数：
        tech:   技术分（如 trend_score，0-100）。
        fund:   基本面综合分（value/growth/quality 的平均，0-100）。
        theme:  题材分（0-100），MVP 阶段传 None。
        weights: (技术权重, 基本面权重, 题材权重)，三者之和应为 1。

    返回：
        与输入同索引的综合分 Series（0-100）。
    """
    w_tech, w_fund, w_theme = weights
    score = tech * w_tech + fund * w_fund
    if theme is not None:
        score = score + theme * w_theme
    return score.clip(0, 100)


def fundamental_composite(fund: dict[str, float]) -> float:
    """把基本面三个分数合成一个综合分（简单平均）。

    参数：
        fund: compute_fundamental 返回的 dict（value_score/growth_score/quality_score）。

    返回：
        0-100 的基本面综合分。
    """
    values = [fund.get("value_score", 50), fund.get("growth_score", 50), fund.get("quality_score", 50)]
    return sum(values) / len(values)


if __name__ == "__main__":
    # 简单自测
    tech = pd.Series([80.0, 60.0, 40.0])
    fund = pd.Series([60.0, 70.0, 50.0])
    print("综合分:", composite_score(tech, fund).tolist())
    print("基本面综合:", fundamental_composite({"value_score": 50, "growth_score": 40, "quality_score": 63.3}))