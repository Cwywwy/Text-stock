"""回测指标计算模块。

输入：资金曲线（equity_curve）与交易明细（trades）。
输出：绩效指标 dict，供 UI 展示与策略对比。

Revision History:
    2026-10-29  add annual metrics and turnover helpers for factor A/B measurement
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
        - avg_pnl_pct     单笔平均收益率（%），用于与往返交易成本对比
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
        # 单笔平均收益率：用于判断"策略赚的钱够不够覆盖来回手续费+印花税+滑点"
        avg_pnl_pct = float(trades["pnl_pct"].mean()) if "pnl_pct" in trades.columns else 0.0
    else:
        win_rate = profit_loss = avg_hold_days = avg_pnl_pct = 0.0
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
        "avg_pnl_pct": round(avg_pnl_pct, 2),
    }


def calc_annual_metrics(equity: pd.Series, trades: pd.DataFrame) -> dict[str, dict]:
    """按自然年度拆分绩效指标（分年度表）。

    参数：
        equity: 资金曲线（日期索引，值为总资产）。
        trades: 交易明细（含 exit_date/pnl/pnl_pct 列）。

    返回：
        {年份字符串: 该年度指标 dict}，年度指标复用 calc_metrics：
        total_return / annual_return / max_drawdown / sharpe / win_rate /
        profit_loss / trade_count / avg_hold_days / avg_pnl_pct。
        首年与末年可能是不完整年度（取决于资金曲线起止），报告中需注明。

    说明：
        - 年度收益按「该年资金曲线首末值」计算，不是把日收益简单相加；
        - 交易归属按 **exit_date** 所在年（今年平的仓才算今年的交易），
          跨年持仓在平仓年一次性计入，避免同笔交易重复计数。
    """
    if equity is None or equity.empty:
        return {}

    eq = equity.copy()
    eq.index = pd.to_datetime(eq.index)
    tr = trades.copy() if trades is not None else pd.DataFrame()
    if not tr.empty and "exit_date" in tr.columns:
        tr["exit_date"] = pd.to_datetime(tr["exit_date"])

    out: dict[str, dict] = {}
    for year in sorted(set(eq.index.year)):
        eq_y = eq[eq.index.year == year]
        if tr.empty:
            tr_y = tr
        elif "exit_date" in tr.columns:
            tr_y = tr[tr["exit_date"].dt.year == year]
        else:
            tr_y = tr.iloc[0:0]
        m = calc_metrics(eq_y, tr_y)
        if m:
            out[str(year)] = m
    return out


def calc_turnover(equity: pd.Series, trades: pd.DataFrame) -> dict:
    """计算换手率指标（单边 / 双边 / 分年度）。

    定义（预注册口径，所有 A/B 报告统一使用，避免事后换算法）：
        - 单边换手 = 买入成交额 ÷ 期间平均总资产；
        - 双边换手 = (买入成交额 + 卖出成交额) ÷ 期间平均总资产；
        - 年度换手 = 当年买入(卖出)成交额 ÷ 当年平均总资产。

    成交额按 `entry_price × shares`（买入）与 `exit_price × shares`（卖出）估算，
    entry/exit 价格已含滑点，未含佣金印花税（费率 <0.1%，对换手率量级无影响）。

    参数：
        equity: 资金曲线（日期索引）。
        trades: 交易明细（含 entry_date/exit_date/entry_price/exit_price/shares）。

    返回：
        dict，含 single_side / double_side / buy_amount / sell_amount /
        avg_equity / buy_trades / sell_trades / annual（{年份: 年度换手 dict}）。
        换手率为**倍数**（如 3.5 表示一年买卖了 3.5 倍平均资产）。
    """
    empty = {
        "single_side": 0.0,
        "double_side": 0.0,
        "buy_amount": 0.0,
        "sell_amount": 0.0,
        "avg_equity": 0.0,
        "buy_trades": 0,
        "sell_trades": 0,
        "annual": {},
    }
    if equity is None or equity.empty:
        return empty
    if trades is None or trades.empty:
        eq0 = equity.copy()
        eq0.index = pd.to_datetime(eq0.index)
        return {**empty, "avg_equity": round(float(eq0.mean()), 2)}

    eq = equity.copy()
    eq.index = pd.to_datetime(eq.index)
    tr = trades.copy()
    tr["entry_date"] = pd.to_datetime(tr["entry_date"])
    tr["exit_date"] = pd.to_datetime(tr["exit_date"])
    tr["buy_amount"] = tr["entry_price"].astype(float) * tr["shares"].astype(float)
    tr["sell_amount"] = tr["exit_price"].astype(float) * tr["shares"].astype(float)

    avg_equity = float(eq.mean())
    buy_amount = float(tr["buy_amount"].sum())
    sell_amount = float(tr["sell_amount"].sum())

    def _ratio(amount: float, base: float) -> float:
        return round(amount / base, 4) if base > 0 else 0.0

    annual: dict[str, dict] = {}
    for year in sorted(set(eq.index.year) | set(tr["exit_date"].dt.year)):
        eq_y = eq[eq.index.year == year]
        avg_y = float(eq_y.mean()) if not eq_y.empty else 0.0
        buy_y = float(tr.loc[tr["entry_date"].dt.year == year, "buy_amount"].sum())
        sell_y = float(tr.loc[tr["exit_date"].dt.year == year, "sell_amount"].sum())
        annual[str(year)] = {
            "single_side": _ratio(buy_y, avg_y),
            "double_side": _ratio(buy_y + sell_y, avg_y),
            "buy_amount": round(buy_y, 2),
            "sell_amount": round(sell_y, 2),
            "avg_equity": round(avg_y, 2),
            "buy_trades": int((tr["entry_date"].dt.year == year).sum()),
            "sell_trades": int((tr["exit_date"].dt.year == year).sum()),
        }

    return {
        "single_side": _ratio(buy_amount, avg_equity),
        "double_side": _ratio(buy_amount + sell_amount, avg_equity),
        "buy_amount": round(buy_amount, 2),
        "sell_amount": round(sell_amount, 2),
        "avg_equity": round(avg_equity, 2),
        "buy_trades": int(len(tr)),
        "sell_trades": int(len(tr)),
        "annual": annual,
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