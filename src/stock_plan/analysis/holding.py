# -*- coding: utf-8 -*-
"""持仓诊断引擎。

功能：
1. diagnose_holding：输入 股票代码 + 买入日期 + 买入价 + 所选策略，
   对照策略止损/止盈/持仓天数给出「继续持有 / 做T降成本 / 减仓 / 清仓」结论；
2. t_trade_advisor：ATR 日内区间 + MA5/MA10 支撑 + 近期压力位 + 策略止盈止损，
   给出做T参考价位与操作步骤；
3. single_stock_backtest：单只股票按所选策略重新回测（参考用）。

说明：本模块输出的是规则对照结果，不构成投资建议。
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from stock_plan.factors.technical import compute_technical
from stock_plan.strategy import store

ACTIONS = {
    "hold": ("✅ 继续持有", "green"),
    "t_trade": ("🔄 可做T降成本", "blue"),
    "reduce": ("⚠️ 建议减仓", "orange"),
    "exit": ("🛑 建议清仓", "red"),
}


def _load_bars(code: str, storage) -> tuple[pd.DataFrame | None, str]:
    """优先读本地缓存，无缓存则在线拉取近 2 年日线；失败返回 (None, 原因)。"""
    if storage is not None and storage.cache_exists(code):
        return storage.load_bars(code), "本地缓存"
    from stock_plan.data.fetcher import DataFetcher

    try:
        fetcher = DataFetcher()
        end = date.today()
        df = fetcher.get_daily_bars(code, end - timedelta(days=730), end)
        if df is None or df.empty:
            return None, "未获取到行情数据（可能代码有误、停牌或新股上市时间过短）"
        return df, "在线拉取（新浪源）"
    except Exception as e:  # noqa: BLE001 - 拉取失败需给出友好提示
        return None, f"在线拉取失败：{e}"


def _prepare_technical(bars: pd.DataFrame) -> pd.DataFrame:
    t = bars.copy()
    t["date"] = pd.to_datetime(t["date"])
    t = t.sort_values("date").reset_index(drop=True)
    return compute_technical(t)


def _resolve_strategy(strategy_name: str, params: dict | None = None):
    """按名称实例化策略（内置 + 已保存策略统一走 store）。"""
    return store.resolve_strategy(strategy_name, params)


def t_trade_advisor(
    close: float, atr: float, ma5: float, ma10: float,
    high20: float, take_profit: float | None, stop_loss: float | None,
) -> dict:
    """综合做T价位：ATR 日内区间 + 均线支撑 + 近期压力 + 策略止盈止损。"""
    candidates_low = [v for v in (ma10, close - 0.8 * atr) if v and v == v]
    buy_low = max(candidates_low)
    candidates_high = [v for v in (high20, close + 0.8 * atr) if v and v == v]
    sell_high = min(candidates_high)
    # 现实保护：高抛至少要有 0.3×ATR 空间，低吸至少低于现价 0.3×ATR
    sell_high = max(sell_high, close + 0.3 * atr)
    buy_low = min(buy_low, close - 0.3 * atr)

    steps = [
        f"低吸参考价 ≈ {buy_low:.2f}（MA10 支撑 {ma10:.2f} 与 现价-0.8×ATR 取高者）",
        f"高抛参考价 ≈ {sell_high:.2f}（近20日高点 {high20:.2f} 与 现价+0.8×ATR 取低者）",
    ]
    if stop_loss:
        steps.append(f"做T底仓保护：若低吸后跌破止损价 {stop_loss:.2f}，直接止损不再接回")
    if take_profit:
        steps.append(f"若当日直接冲到止盈区 {take_profit:.2f} 附近，优先卖出现有持仓而非追买")

    # 现价离哪个价更近，就先做哪一边
    if (sell_high - close) < (close - buy_low):
        mode = "先卖后买（反T）"
        steps.insert(0, "现价更靠近高抛价：先卖出一部分锁定差价，回落后再买回同等数量")
    else:
        mode = "先买后卖（正T）"
        steps.insert(0, "现价更靠近低吸价：先买入一部分摊低成本，反弹到高抛价再卖出同等数量")

    return {"mode": mode, "buy_low": buy_low, "sell_high": sell_high, "steps": steps}


def diagnose_holding(
    code: str, buy_date: str, buy_price: float,
    strategy_name: str, params: dict | None = None, storage=None,
) -> dict:
    """持仓诊断主入口。返回 dict，含 ok/msg/结论/价位/做T建议等。"""
    result = {
        "ok": False, "code": code, "buy_date": buy_date, "buy_price": buy_price,
        "strategy_name": strategy_name, "msg": "", "source": "",
    }
    bars, source = _load_bars(code, storage)
    result["source"] = source
    if bars is None or len(bars) < 30:
        result["msg"] = source if bars is None else "历史数据不足 30 个交易日，无法诊断（可能为次新股）"
        return result

    try:
        strategy = _resolve_strategy(strategy_name, params)
    except Exception as e:  # noqa: BLE001
        result["msg"] = f"策略加载失败：{e}"
        return result

    t = _prepare_technical(bars)
    buy_ts = pd.Timestamp(buy_date)
    held = t[t["date"] >= buy_ts]
    note = ""
    if held.empty:
        if buy_ts > t["date"].iloc[-1]:
            # 当日盘前/盘中刚买入，行情尚未覆盖到今天：以最新交易日近似为买点
            held = t.tail(1)
            note = f"买入日期 {buy_date} 晚于最新行情日，已用最新交易日数据近似诊断"
        else:
            result["msg"] = "买入日期之后没有行情数据（可能非交易日、日期填错或数据未覆盖到该日期）"
            return result

    buy_row = held.iloc[0]
    cur = t.iloc[-1]
    cur_close = float(cur["close"])
    profit_pct = (cur_close - buy_price) / buy_price * 100 if buy_price else 0.0
    hold_days = int((cur["date"] - buy_row["date"]).days)

    # 策略买卖价：以当前 ATR 复现策略口径，再折算到用户真实买价
    atr_buy = float(buy_row["atr14"]) if pd.notna(buy_row["atr14"]) else 0.0
    atr_cur = float(cur["atr14"]) if pd.notna(cur["atr14"]) else atr_buy
    entry_ref = float(strategy.entry_price(cur, atr_cur)) if atr_cur else cur_close
    exit_ref, stop_ref, hold_ref = strategy.exit_price(entry_ref, atr_cur) if atr_cur else (None, None, None)
    # 相对用户真实买价的止盈/止损
    tp_user = buy_price * (1 + (exit_ref - entry_ref) / entry_ref) if (exit_ref and entry_ref) else None
    sl_user = buy_price * (1 + (stop_ref - entry_ref) / entry_ref) if (stop_ref and entry_ref) else None

    ma5 = float(cur["ma5"]) if pd.notna(cur["ma5"]) else cur_close
    ma10 = float(cur["ma10"]) if pd.notna(cur["ma10"]) else cur_close
    ma20 = float(cur["ma20"]) if pd.notna(cur["ma20"]) else cur_close
    high20 = float(t["close"].iloc[-20:].max())

    # ---------- 规则对照，给出结论 ----------
    reasons: list[str] = []
    if sl_user and cur_close <= sl_user:
        action = "exit"
        reasons.append(f"现价 {cur_close:.2f} 已跌破策略止损位 {sl_user:.2f}，纪律优先，止损离场")
    elif profit_pct >= 15 and cur_close < ma10:
        action = "reduce"
        reasons.append(f"浮盈 {profit_pct:.1f}% 且已跌破 MA10（{ma10:.2f}），趋势转弱，建议先落袋一部分")
    elif hold_ref and hold_days >= hold_ref and cur_close < ma20:
        action = "reduce"
        reasons.append(
            f"已持有 {hold_days} 天，超过策略建议的 {hold_ref} 天，且现价低于 MA20（{ma20:.2f}），"
            "持仓时间到期 + 趋势不配合，建议换股或减仓"
        )
    elif hold_ref and hold_days >= hold_ref:
        action = "t_trade"
        reasons.append(
            f"已持有 {hold_days} 天，达到策略建议的 {hold_ref} 天上限，但趋势未走坏（现价在 MA20 上方）；"
            "可继续持有并通过做T降低成本，或到期换股"
        )
    elif cur_close < ma20 and ma5 < ma10:
        action = "reduce"
        reasons.append(f"现价 {cur_close:.2f} 跌破 MA20（{ma20:.2f}）且 MA5 < MA10，短期趋势走坏")
    else:
        action = "hold" if profit_pct >= 0 else "t_trade"
        if action == "hold":
            reasons.append(
                f"浮盈 {profit_pct:+.1f}%，趋势完好（现价 {cur_close:.2f} ≥ MA20 {ma20:.2f}），"
                f"距策略止损位 {sl_user:.2f} 尚有空间，继续持有"
            )
        else:
            reasons.append(
                f"小幅浮亏 {profit_pct:+.1f}%，但未破止损位 {sl_user:.2f}、趋势未完全走坏；"
                "可做T降成本，不宜盲目补仓"
            )

    t_trade = t_trade_advisor(cur_close, atr_cur, ma5, ma10, high20, tp_user, sl_user)

    result.update(
        ok=True,
        note=note,
        current={"date": str(cur["date"])[:10], "close": cur_close, "profit_pct": profit_pct, "hold_days": hold_days},
        levels={"entry_ref": entry_ref, "take_profit": tp_user, "stop_loss": sl_user,
                "hold_days_ref": hold_ref, "atr": atr_cur},
        technical={"ma5": ma5, "ma10": ma10, "ma20": ma20, "high20": high20},
        action=action,
        action_text=ACTIONS[action][0],
        reasons=reasons,
        t_trade=t_trade,
        bars=t,  # 供 UI 画图
    )
    return result


def single_stock_backtest(
    code: str, strategy_name: str, params: dict | None = None, storage=None,
    days: int = 365,
) -> dict:
    """单只股票按所选策略回测近一年，返回指标与资金曲线（参考用）。"""
    from stock_plan.backtest.engine import BacktestConfig, run_backtest
    from stock_plan.backtest.metrics import calc_metrics

    bars, source = _load_bars(code, storage)
    if bars is None or len(bars) < 60:
        return {"ok": False, "msg": source or "历史数据不足，无法回测"}
    t = _prepare_technical(bars)
    end = pd.Timestamp(t["date"].iloc[-1]).date()
    start = end - timedelta(days=days)

    try:
        strategy = _resolve_strategy(strategy_name, params)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"策略加载失败：{e}"}

    fund = {}
    if storage is not None:
        fin = storage.load_fundamentals(code)
        if fin:
            fund = fin
    stock_list = pd.DataFrame({"code": [code], "name": [code], "is_st": [0]})
    config = BacktestConfig(start=start, end=end)
    try:
        result = run_backtest(strategy, config, {code: t}, {code: fund}, stock_list)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"回测运行失败：{e}"}
    metrics = calc_metrics(result.equity_curve, result.trades)
    return {"ok": True, "metrics": metrics, "equity": result.equity_curve}
