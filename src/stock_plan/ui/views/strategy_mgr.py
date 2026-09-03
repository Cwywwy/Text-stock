# -*- coding: utf-8 -*-
"""策略管理页面 — 查看全部策略讲解、调节参数。

每个策略附完整讲解（核心思想/入选条件/买卖规则/适用行情/风险），
参数按策略分别存入 session_state["strategy_params_by_strategy"]，
今日信号页与回测页读取对应策略的参数（趋势策略兼容旧 strategy_params）。
"""
from __future__ import annotations

import streamlit as st

from stock_plan.strategy.registry import PARAM_DESC, STRATEGIES
from stock_plan.ui.widgets import page_glossary

STRATEGY_GLOSSARY = {
    "参数": "策略里可以调的「旋钮」。比如止损倍数调小 = 卖得更果断；持仓天数调短 = 换股更勤快。",
    "ATR 倍数": "按「几天的日常波动」来设买卖价和止损。ATR 是这支股票平均一天波动多少钱。",
    "权重": "多个打分角度的重要程度，加起来通常为 1。比如趋势 0.6 + 基本面 0.4。",
    "回测验证": "改完参数别急着用，先去「回测结果」页跑历史数据，确认没有越改越差。",
}


def _param_widget(key: str, value):
    """按参数类型渲染输入控件，返回新值。"""
    desc = PARAM_DESC.get(key, "")
    label = f"{key}（{desc}）" if desc else key
    if key.startswith("w_"):
        return st.slider(label, 0.0, 1.0, float(value), 0.05, key=f"ui_{key}")
    if key in ("hold_days", "ma_period", "mom_period"):
        return st.number_input(
            label, min_value=1, max_value=120, value=int(value), key=f"ui_{key}"
        )
    if key in ("dev_min", "dev_exclude"):
        return st.number_input(
            label, min_value=-0.30, max_value=0.0, value=float(value), step=0.01,
            key=f"ui_{key}",
        )
    if key == "value_min":
        return st.number_input(
            label, min_value=0.0, max_value=100.0, value=float(value), step=5.0,
            key=f"ui_{key}",
        )
    return st.number_input(
        label, min_value=0.0, max_value=10.0, value=float(value), step=0.1,
        key=f"ui_{key}",
    )


def render():
    st.header("⚙️ 策略管理")
    st.caption("查看各策略的完整讲解并调整参数。参数按策略分别保存，应用到今日信号与回测。")
    page_glossary(STRATEGY_GLOSSARY)

    name = st.selectbox("选择策略", list(STRATEGIES.keys()))
    strategy = STRATEGIES[name]()

    # 策略讲解
    st.markdown(strategy.description)

    st.markdown("#### 参数调整")
    st.caption("修改后点击「保存参数」生效。仅对当前所选策略生效。")

    by_name = st.session_state.get("strategy_params_by_strategy", {})
    saved = by_name.get(name)
    if saved is None and name == "趋势跟随策略":
        saved = st.session_state.get("strategy_params", {})  # 兼容旧版
    params = {**strategy.params, **(saved or {})}

    new_params = {}
    keys = list(params.keys())
    half = (len(keys) + 1) // 2
    col1, col2 = st.columns(2)
    for i, key in enumerate(keys):
        with (col1 if i < half else col2):
            new_params[key] = _param_widget(key, params[key])

    if st.button("💾 保存参数", type="primary"):
        by_name = st.session_state.get("strategy_params_by_strategy", {})
        by_name[name] = new_params
        st.session_state["strategy_params_by_strategy"] = by_name
        if name == "趋势跟随策略":
            st.session_state["strategy_params"] = new_params  # 兼容旧版读取
        st.success(f"「{name}」参数已保存，将应用到今日信号与回测。")

    # 当前参数表
    st.markdown("#### 当前参数")
    st.dataframe(
        [
            {"参数": k, "含义": PARAM_DESC.get(k, ""), "当前值": new_params.get(k, params[k])}
            for k in params
        ],
        use_container_width=True,
        hide_index=True,
    )
