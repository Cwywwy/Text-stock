"""可视化策略拼装页面 — 选择因子/权重/阈值组合成自定义策略并回测。

流程：
1. 配置策略（名称、因子权重、规则阈值、买卖参数）
2. 运行回测验证
3. 保存策略到 session_state（供今日信号页使用）

Revision History:
    2026-09-04  switch to windowed load_market_maps to avoid cloud OOM
    2026-09-04  require public acknowledgement before shared strategy submission
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import streamlit as st

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.data.storage import Storage
from stock_plan.strategy.custom import CustomStrategy, build_custom_factor_rows
from stock_plan.ui.widgets import board_filter_ui, equity_curve_fig, page_glossary

# 本页名词速览（R2 亲民化）
BUILDER_GLOSSARY = {
    "因子": "用来给股票打分的「考察角度」，比如趋势分（走势强不强）、基本面分（公司好不好）。",
    "权重": "每个考察角度的重要程度。比如趋势 0.6、基本面 0.4，意思是六成看走势、四成看公司。",
    "量比": "今天成交量 ÷ 最近 5 天平均成交量。大于 1 说明今天比平时活跃，1.5 以上叫「放量」。",
    "成交额": "当天一共交易了多少钱（股数×价格）。成交额越大，说明这只股票越「好买好卖」（流动性好）。",
    "流动性": "想买的时候买得到、想卖的时候卖得掉。成交额太低的股票容易「卖不出去」，建议设置下限。",
    "RSI": "衡量股票最近是否「涨得太猛」（>70 超买，可能要回调）或「跌得太狠」(<30 超卖) 的温度计。",
    "ATR": "这支股票平时一天大概波动多少钱。买卖价、止损价都按它的倍数来设，波动大的股票止损就设宽一点。",
}


@st.cache_data(ttl=600, show_spinner="正在运行自定义策略回测…")
def _cached_custom_backtest(
    config_json: str, start: str, end: str, initial_cash: float,
    rebalance_freq: str, market_timing: bool,
    boards_json: str = "[]", exclude_st: bool = True,
):
    """自定义策略回测（带缓存）。"""
    import json

    from stock_plan.data.snapshot import CLOUD_MAX_CODES, is_cloud

    config = json.loads(config_json)
    # 窗口 + 预筛选加载（指标预热 180 个日历日，云端限制股票数量）
    boards = json.loads(boards_json)
    stock_list, bars_map, fund_map = Storage().load_market_maps(
        start=start, end=end, lead_days=180,
        boards=boards, exclude_st=exclude_st,
        max_codes=CLOUD_MAX_CODES if is_cloud() else None,
    )

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
    page_glossary(BUILDER_GLOSSARY)

    # 选股范围（R5：板块筛选 + 剔除 ST）
    st.subheader("0. 选股范围")
    boards, exclude_st = board_filter_ui("vb")

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
            help="术语叫「均线」。大白话：这支股票最近 N 天的平均成本。快线 > 慢线 = 最近买的人普遍在赚钱，趋势向上。可自由组合，如 ma5>ma7、ma20>ma60。选择「关闭」则不启用趋势条件。",
        )
    with col_t2:
        trend_slow = st.selectbox("趋势条件 慢线", ma_options, index=5)
    with col_d1:
        dev_ma = st.selectbox(
            "偏离基准均线", [5, 7, 10, 20, 30, 60], index=3,
            help="当前股价比 N 天平均成本高/低多少百分比。偏低（负数）= 回调到位，偏高太多 = 追高风险大。",
        )
    col3, col4, col5 = st.columns(3)
    with col3:
        dev_min = st.slider("偏离下限（%）", -20, 0, -5,
                            help="股价比平均成本低超过这个数就减分/排除。比如 -5 = 比 20 天平均成本低 5% 以内最佳。")
    with col4:
        dev_max = st.slider("偏离上限（%）", 0, 20, 5,
                            help="股价比平均成本高超过这个数说明涨太多（追高），会扣分。")
    with col5:
        vol_ratio_max = st.slider("量比上限（防过热）", 1.0, 5.0, 3.0, 0.1,
                                  help="量比 = 今天成交量 ÷ 近5天平均量。超过上限说明炒得太热（可能是一日行情），会扣分。")

    # ---- 量价配置（R1 需求：流动性筛选 + 放量异动加分）----
    st.markdown("**量价配置（成交量相关）**")
    col_liq, col_vs, col_vb = st.columns(3)
    with col_liq:
        liquidity_min = st.slider(
            "流动性下限：近20日平均成交额（亿元）", 0.5, 50.0, 0.5, 0.5,
            help="成交额 = 每天成交的总金额。太低的股票「买得到卖不掉」。低于这个金额的直接排除。0.5亿 = 5000万，是系统的默认底线。",
        )
    with col_vs:
        use_surge = st.checkbox("启用「放量异动」加分", value=False,
                                help="放量 = 今天的成交量明显比平时大（比如 1.5 倍以上），常说明有资金关注。开启后，放量的股票会加分排到前面。")
        vol_surge_min = st.slider("放量阈值（量比达到几倍算放量）", 1.0, 5.0, 1.5, 0.1,
                                  disabled=not use_surge,
                                  help="量比达到这个倍数就认定为「放量」。1.5 = 成交量是平时的 1.5 倍以上。")
    with col_vb:
        vol_surge_bonus = st.slider("放量加分数值", 5.0, 30.0, 10.0, 1.0,
                                    disabled=not use_surge,
                                    help="被认定为放量的股票，总分额外加多少分。加得越多，放量股票排得越靠前。")

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
            help="动量 = 最近 20 天总共涨/跌了百分之几。-30 约等于不启用；设置如 0 表示只买近20日不跌的股票。",
        )
    with col_brk:
        require_breakout = st.checkbox("要求接近/创20日新高", value=False,
                                       help="只买股价接近或创出最近 20 天新高的股票（「突破」买法），没接近新高的排除。")

    st.markdown("**买卖参数（ATR 倍数）**")
    st.caption("💡 ATR = 这支股票平时一天大概波动多少钱（元）。下面的倍数就是「按几天的波动幅度」来设买卖价和止损价。")
    col8, col9, col10, col_k = st.columns(4)
    with col8:
        k = st.number_input("买入 ATR 倍数（收盘价+k×ATR）", 0.0, 3.0, 0.0, 0.1,
                            help="目标买入价 = 最新收盘价 + k×ATR。0 = 按收盘价买；0.5 = 比现价高半个日常波动才买（突破确认）。")
    with col9:
        m = st.number_input("止盈 ATR 倍数（涨多少卖）", 1.0, 10.0, 3.5, 0.5,
                            help="卖出价 = 买入价 + m×ATR。3.5 ≈ 涨了 3 天半的日常波动就落袋为安。")
    with col10:
        n = st.number_input("止损 ATR 倍数（跌多少认赔）", 1.0, 10.0, 3.5, 0.5,
                            help="止损价 = 买入价 − n×ATR。跌到这里无条件卖出，防止大亏。")
    with col_k:
        hold = st.number_input("最大持仓天数", 5, 120, 30, step=5,
                               help="到期无论盈亏都换股，保证资金效率。短线 10 天内，中线 20~30 天。")

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
            # 量价配置（R1）
            "liquidity_min": float(liquidity_min),
            "vol_surge_min": float(vol_surge_min) if use_surge else 0.0,
            "vol_surge_bonus": float(vol_surge_bonus),
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
                json.dumps(boards, ensure_ascii=False), exclude_st,
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

            # 收益曲线（R3 需求：累计收益率 % + 回撤区域标注）
            st.subheader("📈 收益曲线")
            st.caption("蓝线 = 累计收益率（从第一天算起赚/亏百分之几）；红色阴影 = 中途回撤的深度。")
            fig_ret = equity_curve_fig(result.equity_curve)
            if fig_ret is not None:
                st.plotly_chart(fig_ret, use_container_width=True)

            if result.trades is not None and not result.trades.empty:
                st.dataframe(result.trades.tail(20), use_container_width=True, hide_index=True)

    with col16:
        st.warning(
            "公开策略提示：本系统为共享试用环境。策略名称、参数和规则将对所有用户公开，"
            "其他用户可查看和使用。请勿填写个人或敏感信息。"
        )
        confirmed = st.checkbox(
            "我已知悉该策略将公开给所有用户，且不包含个人或敏感信息。",
            key="builder_public_strategy_confirm",
        )
        if st.button("💾 提交公开策略", type="secondary", disabled=not confirmed):
            from stock_plan.strategy import store
            from stock_plan.strategy.publication import submit_public_strategy

            try:
                record = submit_public_strategy(name.strip(), config, source="builder")
                store.save_strategy(record["name"], config, source="builder")
            except (RuntimeError, ValueError) as error:
                st.error(f"提交失败：{error}")
            else:
                st.session_state["custom_strategy_config"] = config
                st.session_state["strategy_params"] = config["params"]
                st.success(
                    f"✅ 已提交「{record['name']}」。等待开发者本机完成全 A 股计算并发布后，"
                    "将在下一个交易日早上 9:00 生效。"
                )