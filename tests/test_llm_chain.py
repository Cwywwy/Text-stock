# -*- coding: utf-8 -*-
"""LLM 自然语言 → 策略配置 → 导出/回测 链路的回归测试。

这些测试守的是审计里发现的真实缺陷，每条都对应一个"曾经坏掉"的行为：

D5 口径混淆：用户说「涨 10% 止盈」，系统词表只有 ATR 倍数，旧实现会把
   百分比数字当倍数填进去（甚至跨逗号抓错数字），等于止盈永远不触发。
D2 失败静默：LLM 返回非 JSON 时 config 为空，页面整块不渲染、连原因都没有。
D6 导出注入：策略名带换行会把生成的脚本变成可执行代码。
D4 同名遮蔽：用户保存的策略与内置同名时，在下拉框里根本不出现。
"""
from __future__ import annotations

import ast

import pytest

from stock_plan.llm.analyzer import _mock_strategy_config
from stock_plan.strategy import store
from stock_plan.strategy.codegen import generate_runnable_script, normalize_config


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """把策略库指到临时文件，避免污染真实 strategies.db。"""
    db = tmp_path / "strategies.db"
    monkeypatch.setattr(store, "STRATEGY_DB", db)
    return db


# ---------------- D5：百分比 vs ATR 倍数 ----------------
def test_mock_percent_takeprofit_not_mixed_into_atr() -> None:
    """「涨 10% 止盈，跌 5% 止损」不能把 5 填进止盈倍数。"""
    r = _mock_strategy_config("涨 10% 止盈，跌 5% 止损")
    p = r["config"]["params"]
    assert p["atr_m_exit"] == 3.5, "百分比口径不应写进 ATR 倍数（3.5 是默认值）"
    assert p["atr_n_stop"] == 3.5, "止损默认值也是 3.5，不应被 5% 覆盖"
    assert p["atr_m_exit"] != 5.0, "旧实现在这里会抓到隔壁的 5"
    names = [u["name"] for u in r["unsupported"]]
    assert "按百分比止盈止损" in names
    text = "".join(u["description"] for u in r["unsupported"])
    assert "10%" in text and "5%" in text, "应把用户说的 10%/5% 原样回显，便于确认"


def test_mock_percent_synonyms_recognized() -> None:
    """「盈利 20%，亏损 8%」也要识别（不能只认"止盈/止损"两个字）。"""
    r = _mock_strategy_config("盈利 20%，亏损 8%")
    text = "".join(u["description"] for u in r["unsupported"])
    assert "20%" in text and "8%" in text


def test_mock_atr_multiple_applied_and_hold_days() -> None:
    """明确说「N 倍 ATR」时才写入倍数；持有天数照常解析。"""
    r = _mock_strategy_config("持有 15 天，止盈 4 倍 ATR，止损 2 倍 ATR")
    p = r["config"]["params"]
    assert p["atr_m_exit"] == 4.0
    assert p["atr_n_stop"] == 2.0
    assert p["hold_days"] == 15
    assert not [u for u in r["unsupported"] if u["name"] == "按百分比止盈止损"]


# ---------------- D2：失败必须能说清原因 ----------------
def test_failure_exposes_error_field() -> None:
    """LLM 返回非 JSON 时，结果要带 error，页面的渲染条件之外还能显示原因。"""
    from stock_plan.llm.analyzer import generate_strategy_config

    class BadClient:
        mock = False
        last_error = ""

        def chat(self, system, user, temperature=0.3):
            self.last_error = "TimeoutError: read timed out"
            return "抱歉，我无法回答"

    res = generate_strategy_config("随便写点", client=BadClient())
    assert res["config"] == {}
    assert res["error"], "失败结果必须带 error 字段"
    assert "TimeoutError" in res["error"], "应带上底层异常，便于定位是网络/额度问题"


# ---------------- D6：导出注入 ----------------
def test_export_script_ignores_injection_in_name() -> None:
    """策略名里的换行 + 代码不能变成生成脚本里的可执行语句。"""
    bad = "a\nimport os\nos.system('calc')"
    cfg, _ = normalize_config({"name": bad, "hold_days": 10})
    assert "\n" not in cfg["name"], "名称必须压成单行"

    script = generate_runnable_script(cfg)
    tree = ast.parse(script)  # 不合法就会抛 SyntaxError
    tops = [type(n).__name__ for n in tree.body]
    assert "Import" not in tops and "Expr" not in tops, f"注入产生了可执行语句：{tops}"


# ---------------- D4：同名策略可见且优先自存 ----------------
def test_saved_strategy_with_builtin_name_is_visible(tmp_db) -> None:
    """与内置同名的已保存策略要出现在下拉框里（带「（自存）」后缀），且能选中。"""
    from stock_plan.strategy.registry import STRATEGIES

    builtin = next(iter(STRATEGIES))
    store.save_strategy(builtin, {"weights": {}, "rules": {}, "params": {"hold_days": 15}})

    opts = store.strategy_options()
    label = store.display_name(builtin)
    assert label in opts, "同名自存策略被过滤掉了，用户会以为保存失败"
    assert opts.count(label) == 1

    # 带后缀 → 明确选中用户自己保存的那条
    saved = store.resolve_strategy(label)
    assert saved.params.get("hold_days") == 15

    # 不带后缀 → 保持旧语义，仍然是内置策略
    builtin_obj = store.resolve_strategy(builtin)
    assert type(builtin_obj) is STRATEGIES[builtin]

    assert store.strip_display_name(label) == builtin
