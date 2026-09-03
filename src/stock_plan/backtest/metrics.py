"""回测指标计算模块。

输入：资金曲线（equity_curve）与交易明细（trades）。
输出：绩效指标 dict，供 UI 展示与策略对比。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def calc_metrics(equity: pd.Series, trades: pd.DataFrame) -> dict:
    """计算回测绩效指标。

    参数：
        equity: 资金曲线（日期索引，值为总资产）。
        trades: 交易明细（含 pnl/pnl_pct/entry_date/exit_date 列）。

    返回：
        dict，含以下指标：
        - total_return    总收益率（%）
        - annual_return   年化收益率（%）
        - max_drawdown    最大回撤（%）
        - sharpe          夏普比率（年化）
        - win_rate        胜率（%）
        - profit_loss     盈亏比（平均盈利/平均亏损）
        - trade_count     交易次数
        - avg_hold_days   平均持仓天数
    """
    if equity is None or equity.empty:
        return {}

    # 总收益率与年化收益率
    total_return = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    years = max((equity.index[-1] - equity.index[0]).days / 365.0, 1 / 365.0)
    annual_return = ((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1) * 100

    # 最大回撤
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    max_drawdown = drawdown.min()

    # 夏普比率（用日收益率，年化 ×√252）
    daily_ret = equity.pct_change().dropna()
    if daily_ret.std() > 0:
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252)
    else:
        sharpe = 0.0

    # 交易统计
    if trades is not None and not trades.empty:
        wins = trades[trades["pnl"] > 0]
        losses = trades[trades["pnl"] <= 0]
        win_rate = len(wins) / len(trades) * 100
        avg_win = wins["pnl"].mean() if not wins.empty else 0
        avg_loss = abs(losses["pnl"].mean()) if not losses.empty else 0
        profit_loss = avg_win / avg_loss if avg_loss > 0 else float("inf")
        hold_days = (pd.to_datetime(trades["exit_date"]) - pd.to_datetime(trades["entry_date"])).dt.days
        avg_hold_days = hold_days.mean()
        trade_count = len(trades)
    else:
        win_rate = profit_loss = avg_hold_days = 0.0
        trade_count = 0

    return {
        "total_return": round(total_return, 2),
        "annual_return": round(annual_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "sharpe": round(sharpe, 2),
        "win_rate": round(win_rate, 2),
        "profit_loss": round(profit_loss, 2) if profit_loss != float("inf") else None,
        "trade_count": trade_count,
        "avg_hold_days": round(avg_hold_days, 1),
    }


if __name__ == "__main__":
    # 简单自测
    idx = pd.date_range("2025-01-01", periods=100, freq="D")
    equity = pd.Series(np.linspace(100000, 120000, 100), index=idx)
    trades = pd.DataFrame(
        {
            "entry_date": ["2025-01-05", "2025-01-10", "2025-01-15"],
            "exit_date": ["2025-01-12", "2025-01-20", "2025-01-25"],
            "pnl": [1000, -500, 2000],
        }
    )
    print(calc_metrics(equity, trades))