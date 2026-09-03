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


# 参数中文说明（策略管理页展示用）
PARAM_DESC: dict[str, str] = {
    "ma_period": "主均线周期（用于判断趋势）",
    "mom_period": "动量回看周期（近 N 日涨幅）",
    "atr_k_entry": "买入价 = 收盘价 + k×ATR（突破买入幅度）",
    "atr_m_exit": "卖出价 = 买入价 + m×ATR（止盈幅度）",
    "atr_n_stop": "止损价 = 买入价 - n×ATR（止损幅度）",
    "hold_days": "期望持仓天数（超时卖出）",
    "w_tech": "技术面权重",
    "w_fund": "基本面权重",
    "dev_min": "偏离 ma20 下限（均值回归加分区间下界）",
    "dev_exclude": "偏离 ma20 排除线（低于视为落刀）",
    "value_min": "估值分下限（低于视为高估，排除）",
    "quality_min": "质量分下限（生意模式差则排除）",
    "dim_min": "三维度下限（任一低于视为确定性不足）",
}
