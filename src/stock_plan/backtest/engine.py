# -*- coding: utf-8 -*-
"""回测引擎模块 — 用历史数据验证策略表现。

核心逻辑（逐日模拟）：
1. 每天收盘后，用截至当天的数据对候选股票打分，选出 Top N 买入
2. 每天检查持仓：达到止盈 / 止损 / 最大持仓天数 → 卖出
3. 记录每笔交易与每日资金曲线

交易规则（A 股）：
- T+1：买入后次日才能卖出
- 手续费：买入万三，卖出万三 + 印花税千一
- 滑点：成交价按千一偏移（买入加价、卖出减价）
- 涨跌停：涨停无法买入、跌停无法卖出（简化判断）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from stock_plan.factors.technical import compute_technical
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.scorer import fundamental_composite


@dataclass
class BacktestConfig:
    """回测配置。"""

    start: date                 # 回测开始日期
    end: date                   # 回测结束日期
    initial_cash: float = 100_000  # 初始资金
    commission: float = 0.0003     # 手续费（万三，买卖都收）
    stamp_tax: float = 0.001       # 印花税（千一，仅卖出）
    slippage: float = 0.001        # 滑点（千一）
    top_n: int = 5                 # 每天最多买入几只
    max_hold_days: int = 20        # 最大持仓天数
    rebalance_freq: str = "daily"  # 选股频率："daily" 每天 / "weekly" 每周
    market_timing: bool = False    # 大盘择时：大盘低于 MA20 时仓位减半
    enable_t1: bool = True         # 是否启用 T+1
    enable_price_limit: bool = True  # 是否启用涨跌停限制


@dataclass
class BacktestResult:
    """回测结果。"""

    equity_curve: pd.Series   # 资金曲线（日期索引）
    trades: pd.DataFrame      # 每笔交易明细
    metrics: dict = field(default_factory=dict)  # 绩效指标


def _price_limit(code: str, bars: pd.DataFrame, idx: int) -> tuple[float, float]:
    """计算某日涨跌停价（简化：主板 ±10%，创业板/科创板 ±20%，北交所 ±30%）。

    参数：
        code: 股票代码（用于判断板块）。
        bars: 该股票日线。
        idx:  当日索引位置。

    返回：
        (涨停价, 跌停价)。
    """
    prev_close = bars["close"].iloc[idx - 1] if idx > 0 else bars["close"].iloc[idx]
    if code.startswith(("300", "301", "688")):
        limit = 0.20
    elif code.startswith(("4", "8", "9")):
        limit = 0.30
    else:
        limit = 0.10
    return round(prev_close * (1 + limit), 2), round(prev_close * (1 - limit), 2)


def run_backtest(
    strategy: Strategy,
    config: BacktestConfig,
    bars_map: dict[str, pd.DataFrame],
    fund_map: dict[str, dict],
    stock_list: pd.DataFrame,
) -> BacktestResult:
    """执行回测。

    参数：
        strategy:  策略实例。
        config:    回测配置。
        bars_map:  {code: 日线 DataFrame}。
        fund_map:  {code: 财务指标 dict}。
        stock_list: 全 A 股列表（用于硬过滤）。

    返回：
        BacktestResult（资金曲线 + 交易明细 + 指标）。
    """
    # 候选股票：硬过滤
    codes = strategy.filter_universe(stock_list, bars_map)
    if not codes:
        return BacktestResult(pd.Series(dtype=float), pd.DataFrame())

    # 对齐交易日：取所有股票日期的并集，限制在回测区间内
    all_dates = set()
    for code in codes:
        bars = bars_map[code]
        mask = (bars["date"] >= pd.Timestamp(config.start)) & (
            bars["date"] <= pd.Timestamp(config.end)
        )
        all_dates.update(bars.loc[mask, "date"].dt.date)
    trading_days = sorted(all_dates)
    if not trading_days:
        return BacktestResult(pd.Series(dtype=float), pd.DataFrame())

    # 为每只股票建立 日期→行号 索引，便于按日取数据
    date_index = {code: bars_map[code].set_index("date") for code in codes}

    # 预计算每只股票的完整技术因子（滚动计算无未来函数，可一次性算完再按日查表）
    # 相比逐日对每只股票重新 compute_technical，可大幅提升回测速度
    factor_index = {}
    for code in codes:
        f = compute_technical(bars_map[code]).set_index("date")
        # 近 20 日最高收盘价（供突破策略使用，rolling 窗口止于当日，无未来函数）
        f["high20"] = f["close"].rolling(20).max()
        # 自定义均线列（供自定义策略均线自由组合使用）
        for _p in (5, 7, 10, 30):
            f[f"ma{_p}"] = f["close"].rolling(_p).mean()
        factor_index[code] = f
    # 每只股票的日期数组（numpy datetime64），配合指针做单调递增查找
    code_dates = {code: factor_index[code].index.values for code in codes}
    code_ptr = {code: -1 for code in codes}

    # 大盘代理：所有候选股票收盘价的等权平均（用于大盘择时）
    market_close: pd.Series | None = None
    market_ma20: pd.Series | None = None
    if config.market_timing:
        closes = pd.DataFrame({code: date_index[code]["close"] for code in codes})
        market_close = closes.mean(axis=1).sort_index()
        market_ma20 = market_close.rolling(20).mean()

    cash = config.initial_cash
    positions: dict[str, dict] = {}  # code -> {shares, entry_price, entry_date, stop, target}
    trades: list[dict] = []
    equity_curve: dict[date, float] = {}

    # 每周选股：记录上一周买入日，仅在新的一周首个交易日重新选股
    last_rebalance_week: tuple[int, int] | None = None

    for day in trading_days:
        ts = pd.Timestamp(day)
        ts_val = np.datetime64(day)
        # ---------- 1. 检查持仓：止盈 / 止损 / 持仓天数 ----------
        for code in list(positions.keys()):
            pos = positions[code]
            bars = date_index[code]
            if ts not in bars.index:
                continue
            row = bars.loc[ts]
            close = float(row["close"])
            # 涨跌停时无法卖出（简化）
            if config.enable_price_limit:
                up, down = _price_limit(code, bars_map[code], bars.index.get_loc(ts))
                if close >= up:
                    continue  # 涨停，卖不出
            # 止盈 / 止损
            hit_exit = close >= pos["target"]
            hit_stop = close <= pos["stop"]
            # 持仓天数超限
            hold_days = (day - pos["entry_date"]).days
            hit_max = hold_days >= config.max_hold_days
            if hit_exit or hit_stop or hit_max:
                # 卖出（含滑点与费用）
                sell_price = close * (1 - config.slippage)
                proceeds = pos["shares"] * sell_price
                fee = proceeds * (config.commission + config.stamp_tax)
                cash += proceeds - fee
                trades.append(
                    {
                        "code": code,
                        "entry_date": pos["entry_date"],
                        "exit_date": day,
                        "entry_price": pos["entry_price"],
                        "exit_price": round(sell_price, 2),
                        "shares": pos["shares"],
                        "pnl": round(proceeds - fee - pos["cost"], 2),
                        "pnl_pct": round((proceeds - fee - pos["cost"]) / pos["cost"] * 100, 2),
                        "reason": "止盈" if hit_exit else ("止损" if hit_stop else "超时"),
                    }
                )
                del positions[code]

        # ---------- 2. 生成当日信号并买入 ----------
        # 用截至当天的数据计算因子（避免未来函数）
        # 每周选股：仅在新的一周首个交易日重新选股
        if config.rebalance_freq == "weekly":
            iso = day.isocalendar()
            week_key = (iso.year, iso.week)
            if week_key == last_rebalance_week:
                # 本周已选过，跳过买入（持仓仍按每日止盈止损管理）
                market_value = 0.0
                for code, pos in positions.items():
                    bars = date_index[code]
                    if ts in bars.index:
                        market_value += pos["shares"] * float(bars.loc[ts, "close"])
                equity_curve[day] = cash + market_value
                continue
            last_rebalance_week = week_key

        candidates = []
        for code in codes:
            dates = code_dates[code]
            p = code_ptr[code]
            # 单调推进指针：找到最后一个日期 <= 当天的行
            while p + 1 < len(dates) and dates[p + 1] <= ts_val:
                p += 1
            code_ptr[code] = p
            if p < 59:  # 至少 60 根 K 线历史
                continue
            last = factor_index[code].iloc[p]
            if last["volume"] == 0:
                continue
            fund = fund_map.get(code, {})
            # 近 20 日动量（供动量策略使用）
            mom_ret = 0.0
            if p >= 20:
                mom_ret = last["close"] / factor_index[code].iloc[p - 20]["close"] - 1
            # 近 20 日新高偏离（供突破策略使用）
            high20 = last.get("high20")
            if pd.notna(high20) and high20 > 0:
                high20_ratio = last["close"] / high20 - 1
            else:
                high20_ratio = 0.0
            candidates.append(
                {
                    "code": code,
                    "close": last["close"],
                    "atr14": last["atr14"],
                    "ma20": last["ma20"],
                    "ma60": last["ma60"],
                    "rsi14": last["rsi14"],
                    "vol_ratio": last["vol_ratio"],
                    "mom_ret": mom_ret,
                    "high20_ratio": high20_ratio,
                    "value_score": fund.get("value_score", 50.0),
                    "growth_score": fund.get("growth_score", 50.0),
                    "quality_score": fund.get("quality_score", 50.0),
                    "trend_score": last["trend_score"],
                    "fund_score": fundamental_composite(fund),
                    **{
                        f"ma{_p}": float(last.get(f"ma{_p}"))
                        for _p in (5, 7, 10, 30)
                    },
                }
            )
        if candidates:
            df = pd.DataFrame(candidates)
            df["score"] = strategy.score(df)
            df = df.sort_values("score", ascending=False)
            # 买入 Top N（跳过已持仓与涨停）
            for _, row in df.head(config.top_n).iterrows():
                code = row["code"]
                if code in positions:
                    continue
                bars = date_index[code]
                if ts not in bars.index:
                    continue
                close = float(bars.loc[ts, "close"])
                if config.enable_price_limit:
                    up, _ = _price_limit(code, bars_map[code], bars.index.get_loc(ts))
                    if close >= up:
                        continue  # 涨停，买不进
                atr = row["atr14"]
                if pd.isna(atr) or atr <= 0:
                    continue
                entry = strategy.entry_price(row, atr)
                target, stop, hold_days = strategy.exit_price(entry, atr)
                # 用可用资金买入（最多 1/5 仓位）
                budget = cash / max(config.top_n, 1)
                # 大盘择时：大盘低于 MA20 时仓位减半
                if config.market_timing and market_close is not None and market_ma20 is not None:
                    if ts in market_ma20.index and not pd.isna(market_ma20.loc[ts]):
                        if market_close.loc[ts] < market_ma20.loc[ts]:
                            budget *= 0.5
                buy_price = entry * (1 + config.slippage)
                shares = int(budget / buy_price / 100) * 100  # 按 100 股一手取整
                if shares <= 0:
                    continue
                cost = shares * buy_price
                fee = cost * config.commission
                if cost + fee > cash:
                    continue
                cash -= cost + fee
                positions[code] = {
                    "shares": shares,
                    "entry_price": entry,
                    "entry_date": day,
                    "stop": stop,
                    "target": target,
                    "cost": cost + fee,
                }

        # ---------- 3. 记录当日总资产 ----------
        market_value = 0.0
        for code, pos in positions.items():
            bars = date_index[code]
            if ts in bars.index:
                market_value += pos["shares"] * float(bars.loc[ts, "close"])
        equity_curve[day] = cash + market_value

    # 收尾：强制平仓剩余持仓（按最后一天收盘价）
    last_day = trading_days[-1]
    for code, pos in positions.items():
        bars = date_index[code]
        ts = pd.Timestamp(last_day)
        if ts in bars.index:
            close = float(bars.loc[ts, "close"])
            sell_price = close * (1 - config.slippage)
            proceeds = pos["shares"] * sell_price
            fee = proceeds * (config.commission + config.stamp_tax)
            cash += proceeds - fee
            trades.append(
                {
                    "code": code,
                    "entry_date": pos["entry_date"],
                    "exit_date": last_day,
                    "entry_price": pos["entry_price"],
                    "exit_price": round(sell_price, 2),
                    "shares": pos["shares"],
                    "pnl": round(proceeds - fee - pos["cost"], 2),
                    "pnl_pct": round((proceeds - fee - pos["cost"]) / pos["cost"] * 100, 2),
                    "reason": "回测结束平仓",
                }
            )

    equity = pd.Series(equity_curve).sort_index()
    # 统一为 DatetimeIndex，便于后续 resample
    equity.index = pd.to_datetime(equity.index)
    trades_df = pd.DataFrame(trades)
    return BacktestResult(equity_curve=equity, trades=trades_df)


if __name__ == "__main__":
    # 简单自测：用已缓存数据跑一段回测
    from datetime import timedelta

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
    strat = TrendFollowingStrategy()
    result = run_backtest(strat, config, bars_map, fund_map, stock_list)
    print("交易日:", len(result.equity_curve), "交易笔数:", len(result.trades))
    if len(result.trades):
        print(result.trades.head())