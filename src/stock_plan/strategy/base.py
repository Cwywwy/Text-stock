"""策略基类模块。

策略 = 一套完整的选股规则，包含：
1. filter_universe  硬过滤（剔除 ST/停牌/流动性差的股票）
2. score            打分（技术面 + 基本面加权综合分）
3. entry_price      目标买入价（基于 ATR 波动）
4. exit_price       目标卖出价 / 止损价 / 期望持仓天数

所有具体策略（如趋势策略）都继承本基类，只需实现自己的打分逻辑。
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class Strategy(ABC):
    """策略抽象基类。"""

    # 策略名称（子类必须覆盖）
    name: str = "未命名策略"
    # 策略参数（子类必须覆盖，供 UI 展示与回测使用）
    params: dict = {}

    @abstractmethod
    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        """硬过滤：返回通过筛选的股票代码列表。

        参数：
            stock_list: 全 A 股列表（含 code/name/is_st）。
            bars_map:   {code: 日线 DataFrame}。
        """
        raise NotImplementedError

    @abstractmethod
    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        """打分：返回每只股票 0-100 的综合分。

        参数：
            df_factors: 每只股票一行，含技术分/基本面分等列。
        """
        raise NotImplementedError

    @abstractmethod
    def entry_price(self, row: pd.Series, atr: float) -> float:
        """目标买入价。

        参数：
            row: 该股票最新一行的因子数据（含 close 等）。
            atr: 该股票的 ATR 值。
        """
        raise NotImplementedError

    @abstractmethod
    def exit_price(self, entry: float, atr: float) -> tuple[float, float, int]:
        """返回 (目标卖出价, 止损价, 期望持仓天数)。"""
        raise NotImplementedError