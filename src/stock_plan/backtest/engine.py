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
from stock_plan.factors.fundamental import compute_fundamental_or_neutral
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
    enable_t1: bool = True         # 是否启用 T+1（注：当前引擎未按此开关分支，见文档）
    enable_price_limit: bool = True  # 是否启用涨跌停限制
    # 单笔佣金下限（元）。券商普遍 5 元起收：小额单按万三算只有几毛钱，
    # 不设下限会把成本算少一个数量级（买 1 万元：万三=3 元 < 5 元）。
    min_commission: float = 5.0
    # 财务指标快照的抓取日期。给定后，快照日之前的交易日财务因子中性化，
    # 消除"用今天抓到的财务数据回测去年行情"的前视偏差（详见 run_backtest 内注释）。
    fundamental_as_of: date | None = None


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
    # 候选股票：硬过滤。
    # ⚠️ 必须**先把行情截到回测开始日**再交给策略过滤（2026-10-28 修正）。
    # 原因：`filter_universe` 内部会做 `tail(20)` / `iloc[-1]` 之类的"近期形态"判断，
    # 而调用方传进来的 bars 覆盖到**回测结束日**。于是在同一起始日下、只改结束日，
    # 股票就会从"在池中"变成"被剔除"——候选池用到了未来数据（实测可复现，见审计 D13）。
    # 截到 start 之后，池子只由"当时能看到的历史"决定，与结束日无关。
    _pool_cutoff = pd.Timestamp(config.start)
    pool_bars = {c: b[b["date"] <= _pool_cutoff] for c, b in bars_map.items()}
    codes = strategy.filter_universe(stock_list, pool_bars)
    if not codes:
        return BacktestResult(pd.Series(dtype=float), pd.DataFrame())

    # 财务快照前视修正的阈值（None = 不做修正，保持旧行为，供老测试/对比用）
    _fund_as_of = config.fundamental_as_of

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
        # 近 20 日平均成交额（供流动性筛选使用）。必须用 **rolling** 而不是
        # `bars["amount"].tail(20)` —— 后者取的是整条序列末尾，在逐日回测里就是未来数据。
        if "amount" in f.columns:
            f["avg_amount20"] = f["amount"].rolling(20).mean()
        # 自定义均线列（供自定义策略均线自由组合使用）
        for _p in (5, 7, 10, 30):
            f[f"ma{_p}"] = f["close"].rolling(_p).mean()
        factor_index[code] = f
    # 每只股票的日期数组（numpy datetime64），配合指针做单调递增查找
    code_dates = {code: factor_index[code].index.values for code in codes}
    code_ptr = {code: -1 for code in codes}

    # 大盘代理：**等权收益指数**（用于大盘择时）
    # 原实现是 closes.mean(axis=1) —— 对原始股价直接取平均，有两个方法学问题：
    #   1. 被高价股主导（茅台 1300 元与 3 元股在均值里权重相同），实际等价于"高价股指数"；
    #   2. 某只股票缺当日 K 线时会从均值里消失，指数水平**跳空** —— 于是
    #      "大盘跌破 MA20"可能由成分变动而非行情触发，仓位被莫名减半。
    # 改为先算每只股票的日收益率、等权平均后累乘，与价格水平和成分变动都无关。
    market_close: pd.Series | None = None
    market_ma20: pd.Series | None = None
    if config.market_timing:
        from stock_plan.factors.regime import MIN_CONTRIBUTORS, equal_weight_index

        # 候选池小于阈值时按候选数放宽，避免"股票太少 → 指数恒为 1.0 →
        # 大盘择时被静默禁用"这种看不出来的行为
        market_close = equal_weight_index(
            {code: bars_map[code] for code in codes},
            min_contributors=max(1, min(MIN_CONTRIBUTORS, len(codes))),
        )
        market_ma20 = market_close.rolling(20).mean() if len(market_close) else None

    cash = config.initial_cash
    positions: dict[str, dict] = {}  # code -> {shares, entry_price, entry_date, stop, target}
    trades: list[dict] = []
    equity_curve: dict[date, float] = {}

    # 每周选股：记录上一周买入日，仅在新的一周首个交易日重新选股
    last_rebalance_week: tuple[int, int] | None = None

    # 每只股票最后一条 K 线的日期与收盘价。用于区分两种情况：
    #   - **临时停牌**：后面还有 K 线（last_bar 晚于今天）→ 继续持有，等复牌
    #   - **退市/长期停牌（行情彻底结束）**：last_bar 早于今天 → 必须强制平仓，
    #     否则该持仓会在估值里被计成 0、在收尾时被静默丢弃（成本也不回现金）
    last_bar: dict[str, tuple[pd.Timestamp | None, float | None]] = {}
    for _code in codes:
        _b = date_index[_code]
        if len(_b):
            last_bar[_code] = (_b.index[-1], float(_b["close"].iloc[-1]))
        else:
            last_bar[_code] = (None, None)

    def _close(code: str, pos: dict, when, close: float, reason: str) -> float:
        """按给定价格平仓并记录交易，返回平仓后的现金。"""
        sell_price = close * (1 - config.slippage)
        proceeds = pos["shares"] * sell_price
        # 佣金有单笔下限（券商 5 元起收），印花税按成交额单独算、无下限。
        commission_fee = max(proceeds * config.commission, config.min_commission)
        fee = commission_fee + proceeds * config.stamp_tax
        trades.append(
            {
                "code": code,
                "entry_date": pos["entry_date"],
                "exit_date": when,
                "entry_price": pos["entry_price"],
                "exit_price": round(sell_price, 2),
                "shares": pos["shares"],
                "pnl": round(proceeds - fee - pos["cost"], 2),
                "pnl_pct": round((proceeds - fee - pos["cost"]) / pos["cost"] * 100, 2),
                "reason": reason,
            }
        )
        return cash + proceeds - fee

    def _market_value(ts: pd.Timestamp) -> float:
        """持仓市值。当天没有 K 线的股票用**最后一条可用收盘价**估值。

        原实现对这种股票计 0，等于把停牌/退市期间的持仓价值凭空抹掉，
        资金曲线会出现虚假暴跌。
        """
        value = 0.0
        for code, pos in positions.items():
            bars = date_index[code]
            if ts in bars.index:
                value += pos["shares"] * float(bars.loc[ts, "close"])
            else:
                _, end_close = last_bar.get(code, (None, None))
                if end_close is not None:
                    value += pos["shares"] * end_close
        return value

    for day in trading_days:
        ts = pd.Timestamp(day)
        ts_val = np.datetime64(day)
        # ---------- 1. 检查持仓：止盈 / 止损 / 持仓天数 / 行情终结 ----------
        for code in list(positions.keys()):
            pos = positions[code]
            bars = date_index[code]
            if ts not in bars.index:
                # 当天没有行情：判断是"临时停牌"还是"行情彻底结束"
                end_ts, end_close = last_bar.get(code, (None, None))
                if end_ts is not None and end_close is not None and end_ts < ts:
                    # 退市或长期停牌：按最后一个可成交价强制平仓并归因。
                    # 不这样做的话，这个持仓会永远留在 positions 里 —— 估值时被计 0、
                    # 收尾时被静默丢弃，既不记交易也不把成本退回现金。
                    cash = _close(code, pos, end_ts.date(), end_close, "退市/停牌")
                    del positions[code]
                continue
            row = bars.loc[ts]
            close = float(row["close"])
            # 涨跌停对**买卖方向**的限制是相反的，原实现判反了（2026-10-28 修正）：
            # 跌停日卖不出去（挂单排不上），涨停日**可以**正常卖出（只是可能卖得便宜）；
            # 原代码写成 `close >= up: 禁止卖出`，于是"涨停拖到次日才止盈、跌停当天照常止损"，
            # 与 docstring 自己写的"跌停无法卖出"矛盾，且对收益方向是系统性偏差。
            if config.enable_price_limit:
                _up, down = _price_limit(code, bars_map[code], bars.index.get_loc(ts))
                if close <= down:
                    continue  # 跌停卖不出，明日再试
            # 止盈 / 止损
            hit_exit = close >= pos["target"]
            hit_stop = close <= pos["stop"]
            # 持仓天数超限
            hold_days = (day - pos["entry_date"]).days
            hit_max = hold_days >= config.max_hold_days
            if hit_exit or hit_stop or hit_max:
                cash = _close(
                    code, pos, day, close,
                    "止盈" if hit_exit else ("止损" if hit_stop else "超时"),
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
                equity_curve[day] = cash + _market_value(ts)
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
            # 当天必须**有行情**才能作为新候选：停牌与已退市的股票买不进。
            # 不加这一条的话，已退市股票的指针会停在最后一根 K 线上、带着过期数据
            # 一直参与打分并占住 top_n 名额 —— 买入环节虽然会跳过它们（当天无 K 线），
            # 但那天就少买了一只，等于被"僵尸候选"挤掉仓位。
            if dates[p] != ts_val:
                continue
            last = factor_index[code].iloc[p]
            if last["volume"] == 0:
                continue
            fund = fund_map.get(code, {})
            # 财务因子前视修正（2026-10-28）：`fundamentals` 表每个 code 只有 1 行、
            # 没有报告期，`updated_at` 只是抓取日（实测全表同一批）。若在快照日**之前**
            # 的交易日就用这份数据打分，等于提前看到了未来才公布的财务指标。所以
            # 快照日之前把财务因子一律中性化（空 dict → 各因子取中位 50）。
            if _fund_as_of is not None and ts.date() < _fund_as_of:
                fund = {}
            # 原始财务指标 → 三个分数（空 dict 得中性 50 分）。
            # 2026-10-28 修正：此前直接把原始 dict 交给 fundamental_composite，
            # 它读的是分数键、读不到就回退 50，导致财务因子在回测里恒为 50。
            _fund_scores = compute_fundamental_or_neutral(fund)
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
                    # 以下三列原先只在「今日信号」路径里有，导致自定义策略的
                    # 流动性下限与 MACD 条件在**回测**中被静默跳过（不一致是双向的）。
                    # 现与 build_factor_rows 对齐，并由 tests/test_factor_parity.py 锁死。
                    "avg_amount20": float(last.get("avg_amount20"))
                    if pd.notna(last.get("avg_amount20"))
                    else float("nan"),
                    "macd": last["macd"],
                    "macd_signal": last["macd_signal"],
                    "value_score": _fund_scores["value_score"],
                    "growth_score": _fund_scores["growth_score"],
                    "quality_score": _fund_scores["quality_score"],
                    "trend_score": last["trend_score"],
                    "fund_score": fundamental_composite(_fund_scores),
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
                # ⚠️ **同柱决策 + 同柱成交**（2026-10-15 审计发现，方向已实测）
                # ------------------------------------------------------------------
                # 这里用的是 `ts` 当天的收盘价，而候选打分用的因子行也是 `ts` 当天的
                # （`ma20`/`rsi14`/`trend_score` 都含 `ts` 的收盘）。也就是说：
                # **用当天收盘的信息，按当天收盘价成交** —— 现实中你不可能做到。
                #
                # 实盘路径（`signal/generator.py` + `simulator/paper.py`）是对的：
                # 盘前用**昨收**出信号、按**今日收盘**成交。因此回测与模拟盘/实盘的
                # 成交口径不同，**两者的收益不能直接对比**。
                #
                # 实测方向（路径无关，1008 笔真实交易）：
                #   次日开盘 / 当日收盘 − 1 的均值 = **−0.103%**（中位数 0.000%，
                #   为正比例仅 34.2%）→ 若改成"次日开盘成交"，买入价平均**更便宜**。
                # 因此当前的同柱成交约定对**买入价**是偏保守的，并没有虚高收益
                # （另外还叠加了 config.slippage 的滑点）。
                #
                # 尚未测量：**决策侧**的未来信息值多少（即"知道当日收盘"对选股有多大帮助）。
                # 要测需把因子行滞后一根（决策只用 ≤ T-1 的数据、成交用 T 的收盘），
                # 但这会改变选股本身，总收益对比又会落入路径噪声 —— 详见
                # `.github/plans/2026-10-15-4-audit-path-dependence-and-lookahead.md`。
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
                # 佣金单笔下限（券商 5 元起收）；买入无印花税。
                fee = max(cost * config.commission, config.min_commission)
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
        equity_curve[day] = cash + _market_value(ts)

    # 收尾：强制平仓剩余持仓。
    # 注意不能只处理"最后一天有 K 线"的股票 —— 停牌/退市的股票在最后一天必然没有
    # K 线，原实现会把它们静默丢弃（不记交易、成本也不回现金）。
    last_day = trading_days[-1]
    ts_last = pd.Timestamp(last_day)
    for code, pos in list(positions.items()):
        bars = date_index[code]
        if ts_last in bars.index:
            cash = _close(code, pos, last_day, float(bars.loc[ts_last, "close"]),
                          "回测结束平仓")
        else:
            end_ts, end_close = last_bar.get(code, (None, None))
            if end_ts is None or end_close is None:
                continue  # 该股从来没有行情，理论上不会持仓到这里
            cash = _close(code, pos, end_ts.date(), end_close, "回测结束平仓（行情已终结）")
        del positions[code]

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