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


def _config_rows(cfg: dict) -> list[dict]:
    """把策略配置摊平成「类别/参数/值」三列，供 st.dataframe 展示。

    ⚠️ `值` 必须**统一成字符串**（2026-10-15 修）：权重与买卖价参数是数字，而规则的
    取值是"启用/关闭"，混在一起会得到 dtype=object 的列，Streamlit 内部的
    `pyarrow.Table.from_pandas` 会抛
    `ArrowInvalid: Could not convert '启用' with type str: tried to convert to double`
    —— 而 **Streamlit 会吞掉这个异常**，于是页面上表格直接不显示、`at.exception` 也看不到
    （AppTest 保存的是原始对象，不经过 Arrow 序列化，因此这个坑只有真实渲染路径才暴露）。
    """
    rows: list[dict] = []
    for k, v in (cfg.get("weights") or {}).items():
        rows.append({"类别": "权重", "参数": k, "值": str(v)})
    for k, v in (cfg.get("rules") or {}).items():
        rows.append({"类别": "规则", "参数": k, "值": "启用" if v else "关闭"})
    for k, v in (cfg.get("params") or {}).items():
        rows.append({"类别": "买卖价参数", "参数": k, "值": str(v)})
    return rows


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
        width="stretch",
        hide_index=True,
    )

    # ============ 已保存策略（LLM 生成 / 拼装页保存） ============
    from stock_plan.strategy import store

    def _flash(msg: str) -> None:
        """把提示存进 session_state 再 rerun —— 直接 st.success 会被 rerun 丢掉。"""
        st.session_state["_strategy_flash"] = msg

    all_records = store.list_strategies()
    if all_records:
        st.markdown("---")
        st.markdown("### 📁 已保存策略")
        st.caption("在「策略拼装」页手工拼装或让 AI 生成后保存的正式策略，今日信号/回测/对比页可直接选用。")

        flash = st.session_state.pop("_strategy_flash", None)
        if flash:
            st.success(flash)

        # ---- 当前客户（暂时需求 4：分客户保存）----
        st.markdown("**当前客户（策略归属）**")
        oc1, oc2, oc3 = st.columns([3, 1, 2])
        with oc1:
            owner_input = st.text_input(
                "当前客户", value=store.current_owner(), key="owner_current",
                label_visibility="collapsed",
                help=(
                    "策略按「客户」归属隔离查看与批量删除：列表只显示当前客户的策略，"
                    "批量删除也只作用于当前客户，不会误删别人的。"
                    "⚠️ 这只是**便于分类的标签**，不是权限控制 —— 换个名字就能看到全部。"
                ),
            )
        with oc2:
            if st.button("切换", key="owner_switch"):
                store.set_current_owner(owner_input)
                _flash(f"已切换到「{store.current_owner()}」。")
                st.rerun()
        with oc3:
            owners = store.all_owners()
            st.caption("已出现的客户：" + "、".join(owners))
        cur_owner = store.current_owner()

        # ---- 分组标签筛选（暂时需求 4：策略分批），在当前客户范围内 ----
        scoped = store.list_strategies(owner=cur_owner)
        tags = sorted({t.strip() for r in scoped.values()
                       for t in (r.get("tags") or "").split(",") if t.strip()})
        picked_tag = st.selectbox(
            "按标签筛选", ["（全部）"] + tags, key="saved_tag_filter",
            help="标签在下方「批量管理」里为策略填写，用于把策略分批管理。",
        )
        saved_records = (
            scoped if picked_tag == "（全部）"
            else {k: v for k, v in scoped.items() if picked_tag in (v.get("tags") or "").split(",")}
        )
        st.caption(
            f"客户「{cur_owner}」下共 {len(scoped)} 个策略"
            + (f"，其中标签「{picked_tag}」的 {len(saved_records)} 个。" if picked_tag != "（全部）" else "。")
        )

        if not saved_records:
            st.info(f"客户「{cur_owner}」下没有符合条件的策略。可在上方切换客户。")
        else:
            saved_name = st.selectbox(
                "选择已保存策略", list(saved_records.keys()), key="saved_strategy_pick"
            )
            rec = saved_records[saved_name]
            st.markdown(
                f"**来源**：{rec['source']}　|　**创建时间**：{rec['created_at']}"
                + (f"　|　**标签**：{rec['tags']}" if rec.get("tags") else "")
                + f"　|　**归属**：{rec.get('owner') or 'local'}"
            )
            if rec.get("notes"):
                st.caption(f"AI 取值逻辑：{rec['notes']}")
            cfg = rec["config"]
            rows = _config_rows(cfg)
            st.dataframe(rows, width="stretch", hide_index=True)
            unsupported = rec.get("unsupported") or []
            if unsupported:
                with st.expander("⚠️ 保存时的 AI 反馈（未支持参数建议）"):
                    for u in unsupported:
                        st.markdown(f"- **{u.get('name', '未知')}**：{u.get('description', '')}（建议：{u.get('suggestion', '')}）")
            if rec.get("proposal_code"):
                with st.expander("🧪 自定义代码提案（未参与选股，仅供审核）"):
                    st.code(rec["proposal_code"], language="python")
            if st.button("🗑️ 删除该策略", key="del_saved_strategy"):
                store.delete_strategy(saved_name)
                _flash(f"已删除「{saved_name}」。")
                st.rerun()

        # ---- 批量管理：总览 + 标签 + 批量删除（暂时需求 4）----
        with st.expander(
            f"🧹 批量管理（客户「{cur_owner}」共 {len(scoped)} 个；全库 {len(all_records)} 个）",
            expanded=False,
        ):
            all_recs = scoped
            # 先给下面那个"批量删除"按钮的依赖项兜底默认值 ——
            # 当前客户下没有策略时，下面的 multiselect 不会被执行；
            # 若不预置，`disabled=not (victims and confirm)` 会抛
            # "cannot access local variable 'victims'"（切换到新客户名就会遇到）
            victims: list = []
            confirm = False
            if not all_recs:
                st.caption("当前客户下没有策略。")
            else:
                st.dataframe(
                    [
                        {
                            "名称": r["name"],
                            "归属": r.get("owner") or "local",
                            "来源": r["source"],
                            "标签": r.get("tags") or "—",
                            "创建": str(r.get("created_at") or "")[:19],
                        }
                        for r in all_recs.values()
                    ],
                    width="stretch",
                    hide_index=True,
                )

                st.markdown("**为单个策略设置标签**")
                tcol1, tcol2, tcol3 = st.columns([2, 2, 1])
                with tcol1:
                    tag_target = st.selectbox(
                        "策略", list(all_recs.keys()), key="tag_edit_target"
                    )
                with tcol2:
                    tag_value = st.text_input(
                        "标签（多个用逗号分隔）",
                        value=all_recs[tag_target].get("tags") or "",
                        key="tag_edit_value",
                        help="例如：短线, 波段。用于把策略分批管理。",
                    )
                with tcol3:
                    st.caption("")
                    if st.button("💾 保存标签", key="tag_save"):
                        store.set_tags(tag_target, tag_value)
                        _flash(f"「{tag_target}」标签已更新。")
                        st.rerun()

                st.markdown("**批量删除多余策略**")
                st.caption(
                    f"只列出并只删除客户「{cur_owner}」的策略 —— 不会误删其他客户的。"
                )
                victims = st.multiselect(
                    "选择要删除的策略（可多选）", list(all_recs.keys()), key="bulk_delete_pick"
                )
                confirm = st.checkbox(
                    f"我确认删除这 {len(victims)} 个策略（不可撤销）",
                    key="bulk_delete_confirm",
                    disabled=not victims,
            )
            if st.button("🗑️ 批量删除", key="bulk_delete", disabled=not (victims and confirm)):
                # 限定在当前客户，store 层还会再校验一次归属
                n = store.delete_strategies(victims, owner=cur_owner)
                _flash(f"已删除 {n} 个策略。")
                st.rerun()
    else:
        st.markdown("---")
        st.caption("还没有已保存策略。可到「策略拼装」页用一句话让 AI 生成，或手工拼装后保存。")
