# -*- coding: utf-8 -*-
"""MVP 端到端联调脚本 — 验证"数据 → 因子 → 策略 → 信号 → 回测 → 模拟交易"全链路。

用法：
    uv run python -m stock_plan.mvp_check

输出：
    1. 数据层：缓存股票数 / 财务数
    2. 信号层：今日 Top5 信号
    3. 回测层：绩效指标
    4. 模拟交易：账户快照
"""
from __future__ import annotations

from datetime import date, timedelta

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.data.storage import Storage
from stock_plan.signal.generator import generate_signals
from stock_plan.simulator.paper import PaperTrader
from stock_plan.strategy.builtin import TrendFollowingStrategy


def main() -> None:
    print("=" * 60)
    print("MVP 端到端联调")
    print("=" * 60)

    storage = Storage()

    # ---------- 1. 数据层 ----------
    print("\n[1] 数据层")
    stock_list = storage.load_stock_list()
    bars_count = storage.bars_count()
    print(f"  股票列表: {len(stock_list)} 只")
    print(f"  日线缓存: {bars_count} 只")
    if stock_list.empty or bars_count == 0:
        print("  ❌ 无数据，请先运行 fetch_all.py")
        return

    # ---------- 2. 信号层 ----------
    print("\n[2] 信号层")
    signals = generate_signals(top_n=5)
    print(f"  生成信号: {len(signals)} 条")
    for s in signals:
        print(
            f"    {s.code} {s.name} | 分 {s.score} | "
            f"买 {s.entry_price} 卖 {s.exit_price} 止损 {s.stop_loss} 持 {s.hold_days}天"
        )
    if not signals:
        print("  ⚠️ 无信号（可能缓存数据不足）")

    # ---------- 3. 回测层 ----------
    print("\n[3] 回测层")
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
        # 使用回测验证出的最优配置：每周选股 + 大盘择时 + 30 天持仓
        config = BacktestConfig(
            start=start, end=end, rebalance_freq="weekly", max_hold_days=30, market_timing=True
        )
        result = run_backtest(TrendFollowingStrategy(), config, bars_map, fund_map, stock_list)
    metrics = calc_metrics(result.equity_curve, result.trades)
    if metrics:
        print(f"  交易日数: {len(result.equity_curve)}")
        print(f"  交易笔数: {metrics.get('trade_count', 0)}")
        print(f"  总收益率: {metrics.get('total_return', 0):.2f}%")
        print(f"  年化收益: {metrics.get('annual_return', 0):.2f}%")
        print(f"  最大回撤: {metrics.get('max_drawdown', 0):.2f}%")
        print(f"  夏普比率: {metrics.get('sharpe', 0):.2f}")
        print(f"  胜率: {metrics.get('win_rate', 0):.2f}%")
        print(f"  盈亏比: {metrics.get('profit_loss', 0)}")
    else:
        print("  ⚠️ 回测无结果")

    # ---------- 4. 模拟交易 ----------
    print("\n[4] 模拟交易")
    trader = PaperTrader()
    for s in signals:
        trader.on_signal(s)
    status = trader.status()
    print(f"  持仓数: {status['position_count']}")
    print(f"  总资产: {status['total_asset']:,.2f}")
    print(f"  现金: {status['cash']:,.2f}")
    print(f"  已实现盈亏: {trader.realized_pnl():,.2f}")

    print("\n" + "=" * 60)
    print("✅ MVP 端到端联调完成")
    print("=" * 60)


if __name__ == "__main__":
    main()