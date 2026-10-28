"""LLM「一句话生成策略参数」区块 —— 内嵌在「策略拼装」页里复用。

为什么把它从「LLM 智能分析」页搬到这里：
    LLM 生成的产物是**一套参数 + 权重 + 规则**，而策略拼装页正是调这些参数的地方。
    分在两个页面时，用户拿到建议还得手工把二三十个滑杆再调一遍，等于 AI 白算。
    搬进来之后可以直接「把建议值套用到下面的表单」，再跑回测验证。

为什么做成独立模块而不是直接写在 visual_builder.py 里：
    「LLM 智能分析」页仍保留信号解释/消息面分析两个 tab，将来若再需要这个区块
    可以从任何页面 import，不必复制一份 200 行的渲染代码。

Revision History:
    2026-10-28  新建：从 ui/views/llm.py 的「策略生成」tab 抽出，改为内嵌区块
"""
from __future__ import annotations

import json
from typing import Callable

import streamlit as st

from stock_plan.llm.analyzer import generate_strategy_config
from stock_plan.llm.client import get_client
from stock_plan.strategy import store
from stock_plan.strategy.codegen import (
    PARAM_SPEC,
    RULE_SPEC,
    WEIGHT_SPEC,
    generate_annotated_code,
    generate_runnable_script,
)


def render_ai_strategy_section(
    key_prefix: str = "ai",
    apply_config: Callable[[dict], None] | None = None,
) -> None:
    """渲染「用一句话生成策略参数」区块。

    Args:
        key_prefix: Streamlit widget key 前缀（同一页面出现多次时避免 key 冲突）。
        apply_config: 点「套用到下面的表单」时的回调。传 None 则不显示该按钮
            （纯展示场景，例如将来的只读导出页）。
    """
    with st.expander("🤖 用一句话生成策略参数（AI，可选）", expanded=False):
        if apply_config is None:
            st.caption(
                "描述你的想法 → AI 给出参数建议（对照当前默认值）→ 你审核后保存为正式策略。"
            )
        else:
            st.caption(
                "描述你的想法 → AI 给出参数建议（对照当前默认值）→ 一键套用到下面的表单 → "
                "跑回测验证。现有参数表达不了的想法，AI 会给代码提案与补充建议。"
            )

        client = get_client()
        if client.mock:
            st.info(
                "当前为**离线规则模式**（未配置 LLM Key）：建议值由关键词规则生成，能用但不算聪明。"
                "配好 `.env` 里的 `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` 后重启即为真实模型。"
            )
        else:
            st.caption(f"🟢 {client.status_text}")

        desc = st.text_area(
            "描述你的投资偏好",
            key=f"{key_prefix}_desc",
            placeholder="例如：我喜欢趋势向上的股票，5日均线上穿30日均线时买入，"
                        "涨 10% 止盈，跌 5% 止损，最多持有 20 天；只做流动性好的主板票。",
            height=100,
        )
        if st.button("✨ 生成参数建议", type="primary", key=f"{key_prefix}_gen"):
            if not desc.strip():
                st.warning("请先描述你的投资偏好")
            else:
                with st.spinner("正在生成策略建议…"):
                    st.session_state[f"{key_prefix}_result"] = generate_strategy_config(desc)

        result = st.session_state.get(f"{key_prefix}_result")
        if not result:
            return
        if not result.get("config"):
            # 失败时必须给原因：原先只判 `result.get("config")`，失败时整块不渲染，
            # 页面一片空白，用户只能反复点按钮，也看不出是"没配 key"还是"网络不通"。
            st.error(
                f"没能生成策略：{result.get('error') or result.get('reason') or '未知原因'}"
            )
            if result.get("reason") and result.get("reason") != result.get("error"):
                st.caption(f"补充说明：{result['reason']}")
            return

        _render_result(result, key_prefix=key_prefix, apply_config=apply_config)


def _render_result(
    result: dict,
    key_prefix: str,
    apply_config: Callable[[dict], None] | None,
) -> None:
    config = result["config"]
    mode_txt = "真实 LLM" if result.get("mode") == "llm" else "离线规则模式"
    st.markdown("---")
    st.markdown(
        f"**📋 建议：{config.get('name', '未命名')}**（{mode_txt}）"
    )
    if result.get("reason"):
        st.caption(f"AI 取值逻辑：{result['reason']}")

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
    st.dataframe(table, width="stretch", hide_index=True)

    unsupported = result.get("unsupported") or []
    unknown = result.get("unknown") or []
    if unsupported or unknown:
        with st.expander("⚠️ 现有参数无法完全表达的部分（建议补充项）", expanded=True):
            for u in unsupported:
                st.markdown(f"- **{u.get('name', '未知')}**：{u.get('description', '')}")
                st.markdown(f"  - 💡 建议：{u.get('suggestion', '')}")
            if unknown:
                st.markdown(f"- **未识别参数**（已忽略）：{', '.join(unknown)}")

    if apply_config is not None:
        if st.button("⬇️ 把建议值套用到下面的表单", type="primary", key=f"{key_prefix}_apply"):
            apply_config(config)
            st.session_state[f"{key_prefix}_applied"] = True
            st.rerun()

    _render_save_and_export(result, config, unsupported, key_prefix=key_prefix)


def _render_save_and_export(result: dict, config: dict, unsupported: list, key_prefix: str) -> None:
    """保存（先本地后公开）+ 代码备注 + 导出。"""
    annotated = generate_annotated_code(config)
    code_out = annotated  # 默认导出参数注释版，切换见下方 radio

    with st.expander("💾 保存为正式策略", expanded=False):
        name = st.text_input(
            "策略名称（将公开给所有用户）",
            value=config.get("name", "LLM 生成策略"),
            key=f"{key_prefix}_name",
        )
        st.warning(
            "公开策略提示：本系统为共享试用环境。策略名称、参数和规则将对所有用户公开，"
            "其他用户可查看和使用。请勿填写个人或敏感信息。"
        )
        confirmed = st.checkbox(
            "我已知悉该策略将公开给所有用户，且不包含个人或敏感信息。",
            key=f"{key_prefix}_confirm",
        )
        if st.button(
            "保存策略（并尝试提交公开）",
            type="primary",
            disabled=not confirmed,
            key=f"{key_prefix}_save",
        ):
            from stock_plan.strategy.publication import submit_public_strategy

            # 先落本地，再尝试公开提交：保存是本地能力，不该被 token/网络卡住。
            payload = dict(
                name=name.strip(),
                config=config,
                source="llm",
                proposal_code=result.get("proposal_code", ""),
                unsupported=unsupported,
                notes=result.get("reason", ""),
            )
            try:
                store.save_strategy(**payload)
            except (RuntimeError, ValueError) as error:
                st.error(f"本地保存失败：{error}")
            else:
                st.success(f"✅ 已保存「{name.strip()}」。今日信号 / 回测 / 策略对比页立即可用。")
                try:
                    record = submit_public_strategy(**payload)
                except (RuntimeError, ValueError) as error:
                    st.warning(f"公开提交未成功（本地已保存，不影响使用）：{error}")
                else:
                    st.info(
                        f"☁️ 已提交公开「{record['name']}」。等开发者本机完成全 A 股计算并发布后，"
                        "将在下一个交易日早上 9:00 生效。"
                    )

    with st.expander("📎 代码与导出", expanded=False):
        st.markdown("**参数注释版**")
        st.code(annotated, language="python")
        if result.get("proposal_code"):
            st.markdown("**🧪 自定义代码提案（AI 原文，未参与选股，仅供审核）**")
            st.code(result["proposal_code"], language="python")
        export_kind = st.radio(
            "导出格式",
            ["参数注释版（留档）", "可运行回测脚本"],
            horizontal=True,
            key=f"{key_prefix}_export_kind",
        )
        if export_kind.startswith("可运行"):
            code_out = generate_runnable_script(config)
        st.download_button(
            "⬇️ 导出代码",
            data=code_out,
            file_name=(
                f"{config.get('name', 'strategy')}_参数注释.py"
                if export_kind.startswith("参数注释版")
                else f"{config.get('name', 'strategy')}_回测脚本.py"
            ),
            mime="text/x-python",
            key=f"{key_prefix}_download",
        )
        st.markdown("**原始配置 JSON**（高级）")
        st.code(json.dumps(config, ensure_ascii=False, indent=2), language="json")


def _show(v) -> str:
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)
