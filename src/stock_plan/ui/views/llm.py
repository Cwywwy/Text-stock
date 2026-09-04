"""LLM 智能分析页面 — 信号解释 / 消息面分析 / 策略生成。

未配置 API Key 时自动降级为离线规则模式（mock），UI 仍可正常使用。

Revision History:
    2026-09-04  require public acknowledgement before shared strategy submission
"""
from __future__ import annotations

import streamlit as st

from stock_plan.llm.analyzer import analyze_news, explain_signal, generate_strategy_config
from stock_plan.llm.client import get_client
from stock_plan.strategy import store
from stock_plan.strategy.codegen import (
    PARAM_SPEC,
    RULE_SPEC,
    WEIGHT_SPEC,
    generate_annotated_code,
    generate_runnable_script,
)
from stock_plan.ui.widgets import page_glossary

LLM_GLOSSARY = {
    "LLM（大语言模型）": "像 ChatGPT 那样的 AI，能读懂新闻和信号并用自然语言给你解释。",
    "API Key": "调用 AI 服务的「钥匙」，在本页配置。没配也能用——系统会自动切换成离线规则模式。",
    "信号解释": "AI 用大白话说明「为什么选这只股票、买卖价怎么定」，帮助你理解而不是盲从。",
    "离线规则模式": "没配 AI 时的备用方案：用固定模板生成说明，不需要联网。",
    "结构化参数": "AI 输出的不是随意的代码，而是系统认识的标准参数，能直接用于选股和回测。",
    "代码提案": "当现有参数不够表达你的想法时，AI 会给出一段参考代码存档（不参与实际选股），等你审核后可让开发者内置成新参数。",
}


def render():
    st.header("🤖 LLM 智能分析")
    st.caption("用大模型解释选股信号、分析消息面、生成策略。未配置 API Key 时自动降级为离线规则模式。")
    page_glossary(LLM_GLOSSARY)

    client = get_client()
    if client.mock:
        st.info(
            "当前为**离线规则模式**：未检测到 LLM 配置。请在项目根 `.env` 填写 "
            "`LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 后重启应用，启用真实 LLM 分析。"
        )
    else:
        st.success(f"🟢 {client.status_text}")

    tab1, tab2, tab3 = st.tabs(["信号解释", "消息面分析", "策略生成"])

    # ============ Tab 1: 信号解释 ============
    with tab1:
        st.subheader("解释今日选股信号")
        signals = st.session_state.get("today_signals", [])
        if not signals:
            st.info("请先到「今日信号」页生成信号，再回到这里解释。")
        else:
            options = {f"{s.get('code')} {s.get('name', '')}": s for s in signals}
            sel = st.selectbox("选择信号", list(options.keys()))
            if st.button("💬 解释该信号", type="primary"):
                with st.spinner("正在分析…"):
                    text = explain_signal(options[sel])
                st.markdown(text)

    # ============ Tab 2: 消息面分析 ============
    with tab2:
        st.subheader("个股消息面分析")
        code = st.text_input("股票代码", placeholder="如 600519")
        if st.button("📰 分析消息面"):
            if not code.strip():
                st.warning("请输入股票代码")
                return
            with st.spinner("正在拉取并分析新闻…"):
                from stock_plan.data.fetcher import DataFetcher

                fetcher = DataFetcher()
                try:
                    news = fetcher.get_news(code.strip())
                except Exception as e:
                    st.error(f"新闻拉取失败：{e}")
                    return
                text = analyze_news(code.strip(), "", news or [])
            st.markdown(text)

    # ============ Tab 3: 策略生成 ============
    with tab3:
        st.subheader("用自然语言生成策略")
        st.caption(
            "流程：描述想法 → AI 给出参数建议（对照当前默认值）→ 你确认后保存为正式策略，"
            "今日信号/回测/策略对比页立即可用。现有参数表达不了的想法，AI 会给出代码提案与补充建议。"
        )
        desc = st.text_area(
            "描述你的投资偏好",
            placeholder="例如：我喜欢趋势向上的股票，5日均线上穿30日均线时买入，"
                        "涨 10% 止盈，跌 5% 止损，最多持有 20 天；只做流动性好的主板票。",
            height=120,
        )
        if st.button("✨ 生成策略建议", type="primary"):
            if not desc.strip():
                st.warning("请先描述你的投资偏好")
                return
            with st.spinner("正在生成策略…"):
                result = generate_strategy_config(desc)
            st.session_state["llm_strategy_result"] = result

        result = st.session_state.get("llm_strategy_result")
        if result and result.get("config"):
            _render_strategy_result(result)


def _render_strategy_result(result: dict):
    """渲染 LLM 策略建议：参数对照 + 反馈清单 + 代码提案 + 保存 + 导出。"""
    import json

    config = result["config"]
    st.markdown("---")
    mode_txt = "真实 LLM" if result.get("mode") == "llm" else "离线规则模式"
    st.subheader(f"📋 策略建议：{config.get('name', '未命名')}（{mode_txt}）")
    if result.get("reason"):
        st.caption(f"AI 取值逻辑：{result['reason']}")

    # ---- 参数对照表（当前默认 → 建议值）----
    rows = []
    for key, (cn, _desc, default, _t) in WEIGHT_SPEC.items():
        rows.append(("权重", key, cn, default, config["weights"].get(key, default)))
    for key, (cn, _desc, default, _t) in RULE_SPEC.items():
        rows.append(("规则", key, cn, default, config["rules"].get(key, default)))
    for key, (cn, _desc, default, _t) in PARAM_SPEC.items():
        rows.append(("买卖价参数", key, cn, default, config["params"].get(key, default)))
    table = [
        {
            "类别": cat,
            "参数": f"{cn}（{key}）",
            "当前默认": _show(default),
            "AI 建议值": _show(sug),
            "变化": "保持" if default == sug else "调整",
        }
        for cat, key, cn, default, sug in rows
    ]
    st.dataframe(table, use_container_width=True, hide_index=True)

    # ---- 未支持参数反馈清单 ----
    unsupported = result.get("unsupported") or []
    unknown = result.get("unknown") or []
    if unsupported or unknown:
        with st.expander("⚠️ AI 反馈：现有参数无法完全表达的部分（建议补充项）", expanded=True):
            for u in unsupported:
                st.markdown(f"- **{u.get('name', '未知')}**：{u.get('description', '')}")
                st.markdown(f"  - 💡 建议：{u.get('suggestion', '')}")
            if unknown:
                st.markdown(f"- **未识别参数**（已忽略）：{', '.join(unknown)}")

    # ---- 主区：确认保存 | 侧区：代码备注 + 导出 ----
    col_main, col_code = st.columns([3, 2])
    with col_main:
        name = st.text_input(
            "策略名称（将公开给所有用户）",
            value=config.get("name", "LLM 生成策略"),
            key="llm_strategy_name",
        )
        st.warning(
            "公开策略提示：本系统为共享试用环境。策略名称、参数和规则将对所有用户公开，"
            "其他用户可查看和使用。请勿填写个人或敏感信息。"
        )
        confirmed = st.checkbox(
            "我已知悉该策略将公开给所有用户，且不包含个人或敏感信息。",
            key="llm_public_strategy_confirm",
        )
        if st.button("💾 提交公开策略", type="primary", disabled=not confirmed):
            from stock_plan.strategy.publication import submit_public_strategy

            try:
                record = submit_public_strategy(
                    name.strip(), config, source="llm",
                    proposal_code=result.get("proposal_code", ""),
                    unsupported=unsupported, notes=result.get("reason", ""),
                )
                store.save_strategy(
                    record["name"], config, source="llm",
                    proposal_code=result.get("proposal_code", ""),
                    unsupported=unsupported, notes=result.get("reason", ""),
                )
            except (RuntimeError, ValueError) as error:
                st.error(f"提交失败：{error}")
            else:
                st.success(
                    f"✅ 已提交「{record['name']}」。等待开发者本机完成全 A 股计算并发布后，"
                    "将在下一个交易日早上 9:00 生效。"
                )

    with col_code:
        annotated = generate_annotated_code(config)
        st.markdown("**📎 策略代码备注**")
        with st.expander("查看代码（自动根据参数生成）"):
            st.code(annotated, language="python")
        if result.get("proposal_code"):
            with st.expander("🧪 自定义代码提案（AI 原文，未参与选股，仅供审核）"):
                st.code(result["proposal_code"], language="python")
        # 导出框：注释版 / 脚本版切换
        export_kind = st.radio(
            "导出格式",
            ["参数注释版（留档）", "可运行回测脚本"],
            horizontal=True,
            key="llm_export_kind",
        )
        code_out = (
            annotated if export_kind.startswith("参数注释版")
            else generate_runnable_script(config)
        )
        st.download_button(
            "⬇️ 导出代码",
            data=code_out,
            file_name=(
                f"{config.get('name', 'strategy')}_参数注释.py"
                if export_kind.startswith("参数注释版")
                else f"{config.get('name', 'strategy')}_回测脚本.py"
            ),
            mime="text/x-python",
        )

    # 原始配置 JSON（粘回拼装页可用）
    with st.expander("原始配置 JSON（高级）"):
        st.code(json.dumps(config, ensure_ascii=False, indent=2), language="json")


def _show(v) -> str:
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)