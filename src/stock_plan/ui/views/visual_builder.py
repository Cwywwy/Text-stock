"""可视化策略拼装页面 — 选择因子/权重/阈值组合成自定义策略并回测。

流程：
1. 配置策略（名称、因子权重、规则阈值、买卖参数）
2. 运行回测验证
3. 保存策略到 session_state（供今日信号页使用）
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import streamlit as st

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.data.storage import Storage
from stock_plan.strategy.custom import CustomStrategy, build_custom_factor_rows


@st.cache_data(ttl=600, show_spinner="正在运行自定义策略回测…")
def _cached_custom_backtest(
    config_json: str, start: str, end: str, initial_cash: float,
    rebalance_freq: str, market_timing: bool,
):
    """自定义策略回测（带缓存）。"""
    config = json.loads(config_json)
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

    strategy = CustomStrategy(config)
    bt_config = BacktestConfig(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        initial_cash=initial_cash,
        rebalance_freq=rebalance_freq,
        market_timing=market_timing,
        max_hold_days=strategy.params["hold_days"],
    )
    result = run_backtest(strategy, bt_config, bars_map, fund_map, stock_list)
    metrics = calc_metrics(result.equity_curve, result.trades)
    return metrics, result


def render():
    st.header("🧩 可视化策略拼装")
    st.caption("选择因子、权重与阈值，组合成自定义策略并回测验证。")

    # ============ 策略配置 ============
    st.subheader("1. 策略配置")
    name = st.text_input("策略名称", "我的自定义策略")

    col1, col2 = st.columns(2)
    with col1:
        w_tech = st.slider("趋势分权重", 0.0, 1.0, 0.6, 0.05)
    with col2:
        w_fund = st.slider("基本面分权重", 0.0, 1.0, 0.4, 0.05)
    w_mom = st.slider("动量加分权重", 0.0, 1.0, 0.0, 0.05)

    st.markdown("**规则阈值**")
    ma_options = ["关闭", 5, 7, 10, 20, 30, 60]
    col_t1, col_t2, col_d1 = st.columns(3)
    with col_t1:
        trend_fast = st.selectbox(
            "趋势条件 快线", ma_options, index=3,
            help="趋势条件：快线 > 慢线。可自由组合，如 ma5>ma7、ma20>ma60。选择「关闭」则不启用趋势条件。",
        )
    with col_t2:
        trend_slow = st.selectbox("趋势条件 慢线", ma_options, index=5)
    with col_d1:
        dev_ma = st.selectbox(
            "偏离基准均线", [5, 7, 10, 20, 30, 60], index=3,
            help="偏离 = (收盘价 / 该均线 - 1)×100%。如仅看 ma20 偏离或 ma10 偏离。",
        )
    col3, col4, col5 = st.columns(3)
    with col3:
        dev_min = st.slider("偏离下限（%）", -20, 0, -5)
    with col4:
        dev_max = st.slider("偏离上限（%）", 0, 20, 5)
    with col5:
        vol_ratio_max = st.slider("量比上限", 1.0, 5.0, 3.0, 0.1)
    col6, col7, col_rsi_min = st.columns(3)
    with col6:
        rsi_min = st.slider("RSI 下限（低于排除）", 0, 50, 0)
    with col7:
        rsi_max = st.slider("RSI 上限", 50, 95, 75)
    with col_rsi_min:
        ma20_gt_ma60 = st.checkbox("兜底：ma20 > ma60", value=False,
                                   help="仅在上方趋势条件关闭时生效。")
    col_mom, col_brk, _fill = st.columns(3)
    with col_mom:
        mom_min = st.slider(
            "近20日动量下限（%，低于排除）", -30, 30, -30, 1,
            help="-30 约等于不启用；设置如 0 表示只买近20日不跌的股票。",
        )
    with col_brk:
        require_breakout = st.checkbox("要求接近/创20日新高", value=False)

    st.markdown("**买卖参数（ATR 倍数）**")
    col8, col9, col10, col_k = st.columns(4)
    with col8:
        k = st.number_input("买入 ATR 倍数（收盘价+k×ATR）", 0.0, 3.0, 0.0, 0.1)
    with col9:
        m = st.number_input("止盈 ATR 倍数", 1.0, 10.0, 3.5, 0.5)
    with col10:
        n = st.number_input("止损 ATR 倍数", 1.0, 10.0, 3.5, 0.5)
    with col_k:
        hold = st.number_input("最大持仓天数", 5, 120, 30, step=5)

    config = {
        "name": name,
        "weights": {"trend_score": w_tech, "fund_score": w_fund, "mom_ret": w_mom},
        "rules": {
            "ma20_gt_ma60": bool(ma20_gt_ma60),
            "trend_ma_fast": 0 if trend_fast == "关闭" else int(trend_fast),
            "trend_ma_slow": 0 if trend_slow == "关闭" else int(trend_slow),
            "dev_ma": int(dev_ma),
            "dev_min": int(dev_min),
            "dev_max": int(dev_max),
            "rsi_min": int(rsi_min),
            "rsi_max": int(rsi_max),
            "vol_ratio_max": float(vol_ratio_max),
            "mom_min": int(mom_min),
            "require_breakout": bool(require_breakout),
        },
        "params": {
            "atr_k_entry": float(k), "atr_m_exit": m, "atr_n_stop": n, "hold_days": int(hold)
        },
    }

    # ============ 回测设置 ============
    st.subheader("2. 回测设置")
    col11, col12, col13, col14 = st.columns(4)
    with col11:
        default_end = date.today()
        default_start = default_end - timedelta(days=365)
        start = st.date_input("开始日期", default_start, key="vb_start")
    with col12:
        end = st.date_input("结束日期", default_end, key="vb_end")
    with col13:
        initial_cash = st.number_input("初始资金", 10_000, 10_000_000, 100_000, step=10_000, key="vb_cash")
    with col14:
        rebalance_freq = st.selectbox("选股频率", ["weekly", "daily"], index=0, key="vb_freq")
    market_timing = st.checkbox("大盘择时（低于 MA20 减半仓）", value=True, key="vb_timing")

    col15, col16 = st.columns(2)
    with col15:
        if st.button("▶️ 运行回测", type="primary"):
            if start >= end:
                st.error("开始日期必须早于结束日期")
                return
            metrics, result = _cached_custom_backtest(
                json.dumps(config, ensure_ascii=False),
                start.isoformat(), end.isoformat(), float(initial_cash),
                rebalance_freq, bool(market_timing),
            )
            if not metrics:
                st.warning("回测无数据。请先运行数据拉取脚本（fetch_all.py）确保有缓存数据。")
                return

            st.subheader("回测结果")
            cols = st.columns(4)
            cols[0].metric("总收益率", f"{metrics.get('total_return', 0):.2f}%")
            cols[1].metric("年化收益率", f"{metrics.get('annual_return', 0):.2f}%")
            cols[2].metric("最大回撤", f"{metrics.get('max_drawdown', 0):.2f}%")
            cols[3].metric("夏普比率", f"{metrics.get('sharpe', 0):.2f}")
            cols = st.columns(4)
            cols[0].metric("胜率", f"{metrics.get('win_rate', 0):.2f}%")
            cols[1].metric("盈亏比", f"{metrics.get('profit_loss', 0) or 0:.2f}")
            cols[2].metric("交易次数", f"{metrics.get('trade_count', 0)}")
            cols[3].metric("平均持仓", f"{metrics.get('avg_hold_days', 0):.1f} 天")

            if result.trades is not None and not result.trades.empty:
                st.dataframe(result.trades.tail(20), use_container_width=True, hide_index=True)

    with col16:
        if st.button("💾 保存策略", type="secondary"):
            st.session_state["custom_strategy_config"] = config
            st.session_state["strategy_params"] = config["params"]
            st.success(f"策略「{name}」已保存，可在今日信号页使用。")