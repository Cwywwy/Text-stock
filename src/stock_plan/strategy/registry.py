# -*- coding: utf-8 -*-
"""策略注册表 — 全部内置策略的统一入口。

今日信号 / 策略对比 / 策略管理 / 回测页均从这里取策略列表，
新增策略只需在本文件注册即可全站生效。
"""
from __future__ import annotations

from stock_plan.strategy.base import Strategy
from stock_plan.strategy.breakout import BreakoutStrategy
from stock_plan.strategy.builtin import TrendFollowingStrategy
from stock_plan.strategy.meanreversion import MeanReversionStrategy
from stock_plan.strategy.masters import DuanYongpingStrategy, LiLuStrategy
from stock_plan.strategy.momentum import MomentumStrategy
from stock_plan.strategy.value import ValueInvestingStrategy

# 策略名 → 策略类（顺序即 UI 下拉框顺序）
STRATEGIES: dict[str, type[Strategy]] = {
    TrendFollowingStrategy.name: TrendFollowingStrategy,
    MomentumStrategy.name: MomentumStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    BreakoutStrategy.name: BreakoutStrategy,
    ValueInvestingStrategy.name: ValueInvestingStrategy,
    DuanYongpingStrategy.name: DuanYongpingStrategy,
    LiLuStrategy.name: LiLuStrategy,
}


def create_strategy(name: str, params: dict | None = None) -> Strategy:
    """按名称实例化策略（应用可选参数覆盖）。"""
    cls = STRATEGIES[name]
    strategy = cls()
    if params:
        strategy.params = {**strategy.params, **params}
    return strategy


# 参数中文说明（策略管理页展示用）— 大白话优先，括号保留术语（R2 亲民化）
PARAM_DESC: dict[str, str] = {
    "ma_period": "主均线周期：看最近几天的平均成本来判断走势（均线 MA）",
    "mom_period": "动量回看周期：看最近几天的总涨跌幅来判断「势头」（动量）",
    "atr_k_entry": "买入价 = 收盘价 + k×ATR。ATR=这股票平均一天波动多少钱，k=0 就是当天价格直接买",
    "atr_m_exit": "止盈：涨了 m 个「日常波动幅度」就卖出落袋（ATR 倍数）",
    "atr_n_stop": "止损：跌了 n 个「日常波动幅度」就认赔卖出，防止大亏（ATR 倍数）",
    "hold_days": "期望持仓天数：拿这么久就换股，到期无论盈亏都卖出，保证资金效率",
    "w_tech": "技术面权重：走势强弱（K线、均线）在总分里占几成",
    "w_fund": "基本面权重：公司好坏（赚钱能力、成长性）在总分里占几成",
    "dev_min": "偏离下限：股价比平均成本低超过这个百分比，视为趋势转弱、扣分（%）",
    "dev_exclude": "偏离排除线：股价比平均成本低过这条线（正在落刀），直接排除（%）",
    "value_min": "估值分下限：公司「贵不贵」的分数，低于它说明太贵（如市盈率过高），排除",
    "quality_min": "质量分下限：生意模式好不好（赚钱是否稳定轻松），太差则排除",
    "dim_min": "三维度下限：估值/成长/质量三项里任何一项低于它，都视为确定性不足、排除",
}
