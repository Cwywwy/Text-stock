"""周内做 T + 底仓模型的日线回测。

## 为什么单独写一个模拟器，而不是扩展现有 `BacktestEngine`

现有引擎的持仓以 `code` 为唯一键（`backtest/engine.py:154`），`_close()` 一律
**全额平仓**（`:173-191`），退出价在入场时就被冻结（`:359-360`），而策略层只有
`filter_universe/prepare/on_bar_*` 四个方法、没有逐 bar 的持仓回调钩子。
也就是说：**同一只股票上"底仓 + T 仓两笔独立仓位"在现有引擎里表达不出来**。

硬改引擎要动到刚通过 153 项测试、且刚做完独立审计的成交/平仓路径，风险不对称
（修一个功能顺手改坏回测基线）。所以这里实现一个**只做这件事**的独立模拟器：
它不改变既有引擎的任何历史结果，同时能把"底仓赚多少、做 T 贡献多少"分解清楚。

## 模型口径（全部写死在这里，避免 UI 上说不清）

- **底仓**：回测首日按等权买入，之后**不动**（不参与 T 逻辑）。这是"持有不动"的对照组。
- **T 仓**：只做**正 T**（先买后卖）。当日跌幅 ≥ `entry_drop` 时，用底仓市值 ×
  `t_pct` 的金额加买一笔；满足止盈/止损/超期/周五三个条件之一时卖出这笔。
  反 T（先卖底仓再买回）需要允许底仓被临时卖出，会污染"底仓不动"的对照口径，未实现。
- **T+1**：当日买入的 T 仓当日不可卖（`allow_same_day_sell=True` 只给测试用）。
- **成交价**：按**当日收盘价**成交，买加滑点、卖减滑点。日线上这是最小假设，
  但必须承认：真实做 T 要盯日内最低点，日线模拟**必然低估**买到的机会、也
  **无法**还原日内路径。所以本模块的结论只能回答"按收盘跌幅触发、尾盘成交"这一
  种做法的效果，不能推广到"盘中抄底"。
- **成本**：佣金（默认万三，有最低 5 元）、印花税（默认千一，仅卖出）、滑点（默认千一）。
  这三项必须显式传，别用引擎的默认值去想当然（本项目实测：印花税历史上调过，
  用千一是偏悲观的）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from stock_plan.backtest.metrics import calc_metrics

LOT = 100  # A 股一手 100 股，买入必须是整手


@dataclass
class TConfig:
    """做 T 回测参数。默认值按"谨慎、成本偏高"取。"""

    initial_cash: float = 100_000.0
    base_pct: float = 0.6        # 底仓占初始资金的比例（剩下的是 T 仓与现金缓冲）
    t_pct: float = 0.3           # 单次 T 仓金额 = 该股底仓市值 × t_pct
    entry_drop: float = 0.03     # 当日跌幅达到该值才买 T（正数，3 = 3%）
    exit_gain: float = 0.02      # T 仓浮盈达到该值就卖（正数，2 = 2%）
    stop_loss: float = 0.03      # T 仓浮亏达到该值就卖（正数，3 = 3%）
    max_hold_days: int = 5       # T 仓最长持有交易日数（"周内"的量化）
    close_on_friday: bool = True  # 周五收盘强制平掉 T 仓，不跨周
    commission: float = 0.0003   # 佣金，买卖都收
    min_commission: float = 5.0  # 单笔最低佣金（券商普遍 5 元，回测常被忽略）
    stamp_tax: float = 0.001     # 印花税，仅卖出
    slippage: float = 0.001      # 滑点


@dataclass
class TBacktestResult:
    """做 T 回测结果。

    equity:          总资产曲线（底仓 + T 仓 + 现金）
    base_only_equity: 同一批底仓"买了就不动"的对照曲线（扣同样的买入成本）
    t_trades:        T 仓交易明细（每笔一个来回）
    base_trades:     底仓建仓记录
    metrics:         总资产口径的绩效指标
    compare:         底仓 vs 底仓+做T 的对照与分解
    """

    equity: pd.Series
    base_only_equity: pd.Series
    t_trades: pd.DataFrame
    base_trades: pd.DataFrame
    metrics: dict = field(default_factory=dict)
    compare: dict[str, Any] = field(default_factory=dict)


def _buy_fee(amount: float, cfg: TConfig) -> float:
    """买入费用：佣金（有最低 5 元）。买入不收印花税。"""
    return max(amount * cfg.commission, cfg.min_commission)


def _sell_fee(amount: float, cfg: TConfig) -> float:
    """卖出费用：佣金（有最低 5 元）+ 印花税。"""
    return max(amount * cfg.commission, cfg.min_commission) + amount * cfg.stamp_tax


def _lots(budget: float, price: float) -> int:
    """按整手计算能买多少股；预算不足一手返回 0。"""
    if price <= 0 or budget <= 0:
        return 0
    return int(budget // (price * LOT)) * LOT


def _is_friday(ts: pd.Timestamp) -> bool:
    return pd.Timestamp(ts).weekday() == 4


def run_t_backtest(
    codes: list[str],
    bars_map: dict[str, pd.DataFrame],
    cfg: TConfig | None = None,
) -> TBacktestResult:
    """在给定日线上跑"底仓 + 正 T"。

    参数：
        codes:    参与回测的代码（底仓按它们等权建仓）。
        bars_map: code → 日线 DataFrame，需含 date/close 列，按日期升序。
        cfg:      参数；None 用默认值。

    返回：`TBacktestResult`。

    丢弃规则（与既有引擎保持一致，避免两套口径）：当日没有 bar 的代码不交易，
    停牌/退市当日只按最后已知收盘价估值。
    """
    cfg = cfg or TConfig()
    codes = [str(c) for c in codes]

    # 交易日历取所有股票日期的并集：某只股票停牌不影响别的股票当天能做 T
    all_dates = sorted(
        {pd.Timestamp(d) for c in codes if c in bars_map for d in bars_map[c]["date"]}
    )
    if not all_dates:
        empty = pd.Series(dtype=float)
        return TBacktestResult(empty, empty, pd.DataFrame(), pd.DataFrame(), {}, {})

    # 预先把每只股票做成 {日期: (close, open)} 的字典，循环里 O(1) 取价
    price: dict[str, dict[pd.Timestamp, float]] = {}
    for c in codes:
        df = bars_map.get(c)
        if df is None or df.empty:
            continue
        price[c] = dict(zip(pd.to_datetime(df["date"]), df["close"].astype(float)))

    cash = cfg.initial_cash
    base_shares: dict[str, int] = {}
    base_cost: dict[str, float] = {}          # 底仓买入总成本（含费用），用于算底仓收益
    t_pos: dict[str, dict] = {}               # code → {shares, cost, entry_date, entry_i}
    last_close: dict[str, float] = {}
    base_trades: list[dict] = []
    t_trades: list[dict] = []

    # ---------- 首日建底仓（等权） ----------
    first = all_dates[0]
    first_codes = [c for c in codes if first in price.get(c, {})]
    if first_codes and cfg.base_pct > 0:
        per = cfg.initial_cash * cfg.base_pct / len(first_codes)
        for c in first_codes:
            px = price[c][first] * (1 + cfg.slippage)
            shares = _lots(per, px)
            if shares <= 0:
                continue
            amount = shares * px
            fee = _buy_fee(amount, cfg)
            if amount + fee > cash:
                continue
            cash -= amount + fee
            base_shares[c] = shares
            base_cost[c] = amount + fee
            last_close[c] = price[c][first]
            base_trades.append(
                {
                    "code": c,
                    "entry_date": first,
                    "price": px,
                    "shares": shares,
                    "amount": amount,
                    "fee": fee,
                }
            )

    for c in price:
        if c in last_close:
            continue
        # 首日没 bar 的股票：用它能取到的第一天补记最后收盘价，仅用于估值
        d0 = min(price[c])
        last_close[c] = price[c][d0]

    # ---------- 逐日推进 ----------
    equity_points: list[tuple[pd.Timestamp, float]] = []
    for i, day in enumerate(all_dates):
        # 先更新当日收盘价（当日无 bar 的沿用上次已知价，用于估值）
        for c, px in price.items():
            if day in px:
                last_close[c] = px[day]

        for c in base_shares:  # 只对已有底仓的股票做 T
            px_today = price.get(c, {}).get(day)
            if px_today is None:
                continue
            px_prev = price[c].get(all_dates[i - 1]) if i > 0 else None
            pct = (px_today / px_prev - 1.0) if px_prev else 0.0

            pos = t_pos.get(c)
            if pos is not None:
                held = i - pos["entry_i"]
                pnl_pct = px_today / pos["entry_price"] - 1.0
                # T+1 由**循环结构**保证：仓位只可能在收盘时建（买入块在下面且以
                # continue 收尾），而卖出块只对已存在的仓位执行，所以最早也要次日
                # 收盘才能卖。这里不再加 `held >= 1` 这类冗余判断——实测删掉它
                # 所有测试照样通过，说明它守卫不了任何东西（空转守卫比没守卫更糟）。
                reason = ""
                if pnl_pct >= cfg.exit_gain:
                    reason = "止盈"
                elif pnl_pct <= -cfg.stop_loss:
                    reason = "止损"
                elif held >= cfg.max_hold_days:
                    reason = "超期"
                elif cfg.close_on_friday and _is_friday(day):
                    reason = "周五强平"
                if reason:
                    sell_px = px_today * (1 - cfg.slippage)
                    amount = pos["shares"] * sell_px
                    fee = _sell_fee(amount, cfg)
                    cash += amount - fee
                    cost = pos["cost"]
                    pnl = amount - fee - cost
                    t_trades.append(
                        {
                            "code": c,
                            "entry_date": pos["entry_date"],
                            "exit_date": day,
                            "entry_price": pos["entry_price"],
                            "exit_price": sell_px,
                            "shares": pos["shares"],
                            "cost": cost,
                            "proceeds": amount - fee,
                            "pnl": pnl,
                            "pnl_pct": pnl / cost * 100 if cost else 0.0,
                            "hold_days": held,
                            "exit_reason": reason,
                        }
                    )
                    t_pos.pop(c, None)
                continue  # 同日不重复开仓

            # 正 T 买入：只在底仓存在、且当日跌幅够深时
            if cfg.t_pct > 0 and pct <= -cfg.entry_drop:
                base_val = base_shares[c] * px_today
                budget = min(cash, base_val * cfg.t_pct)
                buy_px = px_today * (1 + cfg.slippage)
                shares = _lots(budget, buy_px)
                if shares <= 0:
                    continue
                amount = shares * buy_px
                fee = _buy_fee(amount, cfg)
                if amount + fee > cash:
                    continue
                cash -= amount + fee
                t_pos[c] = {
                    "shares": shares,
                    "cost": amount + fee,
                    "entry_price": buy_px,
                    "entry_date": day,
                    "entry_i": i,
                }

        # 收盘估值
        mv = sum(s * last_close.get(c, 0.0) for c, s in base_shares.items())
        mv += sum(p["shares"] * last_close.get(c, 0.0) for c, p in t_pos.items())
        equity_points.append((day, cash + mv))

    # ---------- 末日平掉 T 仓（还原成"只剩底仓"，好与对照比较） ----------
    last_day = all_dates[-1]
    for c, pos in list(t_pos.items()):
        px = last_close.get(c, pos["entry_price"])
        sell_px = px * (1 - cfg.slippage)
        amount = pos["shares"] * sell_px
        fee = _sell_fee(amount, cfg)
        cash += amount - fee
        pnl = amount - fee - pos["cost"]
        t_trades.append(
            {
                "code": c,
                "entry_date": pos["entry_date"],
                "exit_date": last_day,
                "entry_price": pos["entry_price"],
                "exit_price": sell_px,
                "shares": pos["shares"],
                "cost": pos["cost"],
                "proceeds": amount - fee,
                "pnl": pnl,
                "pnl_pct": pnl / pos["cost"] * 100 if pos["cost"] else 0.0,
                "hold_days": all_dates.index(last_day) - pos["entry_i"],
                "exit_reason": "末日平仓",
            }
        )
        t_pos.pop(c, None)
    if equity_points:
        equity_points[-1] = (last_day, cash + sum(s * last_close.get(c, 0.0) for c, s in base_shares.items()))

    equity = pd.Series(
        [v for _, v in equity_points], index=pd.DatetimeIndex([d for d, _ in equity_points])
    )

    # ---------- 对照组：同样的底仓，买了不动（已扣建仓费用） ----------
    base_invested = sum(base_cost.values())
    base_ret = pd.Series(
        [
            (cfg.initial_cash - base_invested)
            + sum(s * last_close_lookup(price, c, d) for c, s in base_shares.items())
            for d in all_dates
        ],
        index=pd.DatetimeIndex(all_dates),
    )

    t_df = pd.DataFrame(t_trades)
    b_df = pd.DataFrame(base_trades)
    metrics = calc_metrics(equity, t_df)
    metrics = dict(metrics)
    metrics.update({"trade_count": len(t_df), "base_code_count": len(base_shares)})

    base_return = (base_ret.iloc[-1] / cfg.initial_cash - 1) * 100
    total_return = (equity.iloc[-1] / cfg.initial_cash - 1) * 100
    compare = {
        "total_return": total_return,
        "base_only_return": base_return,
        "t_contribution": total_return - base_return,
        "t_trades": len(t_df),
        "t_win_rate": (t_df["pnl"] > 0).mean() * 100 if len(t_df) else 0.0,
        "t_total_pnl": float(t_df["pnl"].sum()) if len(t_df) else 0.0,
        "t_avg_pnl_pct": float(t_df["pnl_pct"].mean()) if len(t_df) else 0.0,
        "t_avg_hold_days": float(t_df["hold_days"].mean()) if len(t_df) else 0.0,
        "t_pnl_per_trade_vs_roundtrip_cost": (
            float(t_df["pnl"].mean()) if len(t_df) else 0.0
        ),
        "base_code_count": len(base_shares),
        "requested_code_count": len(first_codes),
        # 因资金不足/一整手都买不起而未建底仓的股票数：不显示出来的话，
        # 用户会以为"我选了 20 只"就真的按 20 只等权持有，结论会被整体误导。
        "skipped_code_count": max(len(first_codes) - len(base_shares), 0),
        "days": len(all_dates),
        "t_total_cost": float(
            (t_df["cost"] - t_df["shares"] * t_df["entry_price"]).sum()
            + (t_df["shares"] * t_df["exit_price"] - t_df["proceeds"]).sum()
        )
        if len(t_df)
        else 0.0,
    }
    return TBacktestResult(
        equity=equity,
        base_only_equity=base_ret,
        t_trades=t_df,
        base_trades=b_df,
        metrics=metrics,
        compare=compare,
    )


def last_close_lookup(price: dict[str, dict[pd.Timestamp, float]], code: str, day: pd.Timestamp) -> float:
    """取某日（或之前最近一个有行情的日子）的收盘价，用于收盘估值。

    为什么不能直接用当天的价：停牌/退市时当天没有 bar，直接 KeyError 会中断回测；
    而"用未来最近的价"是前视，只能用历史最近价。
    """
    series = price.get(code)
    if not series:
        return 0.0
    if day in series:
        return float(series[day])
    past = [d for d in series if d <= day]
    return float(series[max(past)]) if past else 0.0


def run_t_backtest_for_codes(
    codes: list[str],
    storage,
    start: str | None = None,
    end: str | None = None,
    cfg: TConfig | None = None,
) -> TBacktestResult:
    """从本地行情缓存读日线并跑做 T 回测（UI / CLI 用的便捷入口）。"""
    bars_map: dict[str, pd.DataFrame] = {}
    for c in codes:
        try:
            df = storage.load_bars(str(c))
        except Exception:  # noqa: BLE001
            continue
        if df is None or df.empty:
            continue
        if start is not None:
            df = df[df["date"] >= pd.Timestamp(start)]
        if end is not None:
            df = df[df["date"] <= pd.Timestamp(end)]
        if not df.empty:
            bars_map[str(c)] = df.reset_index(drop=True)
    return run_t_backtest(codes, bars_map, cfg)
