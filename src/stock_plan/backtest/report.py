"""回测报告模块 — 生成可视化所需的结构化报告。

把回测结果整理成 UI 可直接渲染的结构：
- equity_curve       资金曲线（Plotly 折线图数据）
- monthly_returns    月度收益
- drawdown_periods   回撤区间
- trade_distribution 交易盈亏分布
- exit_attribution   退出原因归因（止盈/止损/超时）
- monthly_heatmap    月度收益热力图（年×月）
- drawdown_annotations 回撤标注（峰值/谷值）
"""
from __future__ import annotations

import pandas as pd


def make_report(result) -> dict:
    """生成结构化报告。

    参数：
        result: BacktestResult（含 equity_curve / trades / metrics）。

    返回：
        dict，含 equity_curve / monthly_returns / drawdown_periods / trade_distribution。
    """
    equity = result.equity_curve
    trades = result.trades

    # 确保索引为 DatetimeIndex（回测引擎可能返回 date 对象索引）
    if not equity.empty and not isinstance(equity.index, pd.DatetimeIndex):
        equity = equity.copy()
        equity.index = pd.to_datetime(equity.index)

    # 资金曲线：转成 {date: value} 列表，便于 Plotly 渲染
    equity_curve = (
            [{"date": str(d)[:10], "value": round(float(v), 2)} for d, v in equity.items()]
        if not equity.empty
        else []
    )

    # 月度收益：按自然月分组计算收益率
    monthly_returns = []
    if not equity.empty:
        monthly = equity.resample("ME").last()
        monthly_ret = monthly.pct_change() * 100
        for d, v in monthly_ret.items():
            if pd.notna(v):
                monthly_returns.append({"month": str(d.date())[:7], "return": round(float(v), 2)})

    # 回撤区间：找出资金从高点回落到回升的区间
    drawdown_periods = []
    if not equity.empty:
        running_max = equity.cummax()
        drawdown = (equity - running_max) / running_max * 100
        in_drawdown = drawdown < 0
        # 找连续回撤段
        start = None
        for d, flag in in_drawdown.items():
            if flag and start is None:
                start = d
            elif not flag and start is not None:
                seg = drawdown.loc[start:d]
                drawdown_periods.append(
                    {
                                        "start": str(start)[:10],
                                        "end": str(d)[:10],
                        "max_drawdown": round(float(seg.min()), 2),
                    }
                )
                start = None
        if start is not None:
            seg = drawdown.loc[start:]
            drawdown_periods.append(
                {
                                    "start": str(start)[:10],
                                    "end": str(equity.index[-1])[:10],
                    "max_drawdown": round(float(seg.min()), 2),
                }
            )

    # 交易盈亏分布：按 pnl 分桶统计
    trade_distribution = []
    if trades is not None and not trades.empty:
        bins = [-1e9, -5000, -2000, 0, 2000, 5000, 1e9]
        labels = ["<-5000", "-5000~-2000", "-2000~0", "0~2000", "2000~5000", ">5000"]
        counts = pd.cut(trades["pnl"], bins=bins, labels=labels).value_counts()
        for label in labels:
            trade_distribution.append({"range": label, "count": int(counts.get(label, 0))})

    return {
        "equity_curve": equity_curve,
        "monthly_returns": monthly_returns,
        "drawdown_periods": drawdown_periods,
        "trade_distribution": trade_distribution,
        "exit_attribution": exit_reason_attribution(trades),
        "monthly_heatmap": monthly_heatmap(equity),
        "drawdown_annotations": drawdown_annotations(equity),
    }


def exit_reason_attribution(trades: pd.DataFrame) -> list[dict]:
    """退出原因归因：按止盈/止损/超时分组统计笔数与平均收益。

    参数：
        trades: 交易明细 DataFrame（含 reason/pnl_pct 列）。

    返回：
        list[dict]，每项含 reason/count/avg_pnl_pct/win_rate。
    """
    if trades is None or trades.empty or "reason" not in trades.columns:
        return []
    out = []
    for reason, grp in trades.groupby("reason"):
        out.append(
            {
                "reason": reason,
                "count": int(len(grp)),
                "avg_pnl_pct": round(float(grp["pnl_pct"].mean()), 2),
                "win_rate": round(float((grp["pnl_pct"] > 0).mean() * 100), 2),
            }
        )
    return out


def monthly_heatmap(equity: pd.Series) -> list[dict]:
    """月度收益热力图数据：按 年×月 矩阵统计收益率。

    参数：
        equity: 资金曲线（DatetimeIndex）。

    返回：
        list[dict]，每项含 year/month/return。
    """
    if equity.empty:
        return []
    monthly = equity.resample("ME").last()
    monthly_ret = monthly.pct_change() * 100
    out = []
    for d, v in monthly_ret.items():
        if pd.notna(v):
            out.append(
                {
                    "year": d.year,
                    "month": d.month,
                    "return": round(float(v), 2),
                }
            )
    return out


def drawdown_annotations(equity: pd.Series) -> list[dict]:
    """回撤标注数据：每个回撤段的峰值/谷值日期与幅度（用于资金曲线标注）。

    参数：
        equity: 资金曲线（DatetimeIndex）。

    返回：
        list[dict]，每项含 peak_date/trough_date/drawdown。
    """
    if equity.empty:
        return []
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100
    out = []
    peak_date = equity.index[0]
    peak_val = equity.iloc[0]
    trough_date, trough_val = None, None
    for d, v in equity.items():
        if v >= peak_val:
            # 创新高：结算上一个回撤段
            if trough_date is not None and trough_val is not None:
                dd = (trough_val - peak_val) / peak_val * 100
                if dd < -1:  # 只记录超过 1% 的回撤
                    out.append(
                        {
                            "peak_date": str(peak_date)[:10],
                            "trough_date": str(trough_date)[:10],
                            "drawdown": round(float(dd), 2),
                        }
                    )
            peak_date, peak_val = d, v
            trough_date, trough_val = None, None
        else:
            if trough_val is None or v < trough_val:
                trough_date, trough_val = d, v
    # 收尾
    if trough_date is not None and trough_val is not None:
        dd = (trough_val - peak_val) / peak_val * 100
        if dd < -1:
            out.append(
                {
                    "peak_date": str(peak_date)[:10],
                    "trough_date": str(trough_date)[:10],
                    "drawdown": round(float(dd), 2),
                }
            )
    return out


if __name__ == "__main__":
    # 简单自测
    from datetime import date, timedelta

    from stock_plan.backtest.engine import BacktestConfig, run_backtest
    from stock_plan.data.storage import Storage
    from stock_plan.strategy.builtin import TrendFollowingStrategy

    storage = Storage()
    stock_list = storage.load_stock_list()
    bars_map = {}
    for code in stock_list["code"].astype(str).tolist():
        if storage.cache_exists(code):
            bars_map[code] = storage.load_bars(code)
    fund_map = {}
    for code in bars_map:
        fin = storage.load_fundamentals(code)
        if fin:
            fund_map[code] = fin

    end = date.today()
    start = end - timedelta(days=365)
    config = BacktestConfig(start=start, end=end)
    result = run_backtest(TrendFollowingStrategy(), config, bars_map, fund_map, stock_list)
    report = make_report(result)
    print("资金曲线点数:", len(report["equity_curve"]))
    print("月度收益:", report["monthly_returns"][-3:])
    print("回撤区间数:", len(report["drawdown_periods"]))
    print("交易分布:", report["trade_distribution"])