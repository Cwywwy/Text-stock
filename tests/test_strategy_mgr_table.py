# -*- coding: utf-8 -*-
"""策略管理页参数表的类型安全测试（2026-10-15）。

背景：`值` 列原先混装数字（权重/买卖价参数）与字符串（规则的"启用/关闭"），
得到 dtype=object。Streamlit 内部的 `pyarrow.Table.from_pandas` 会抛
`ArrowInvalid: Could not convert '启用' ...`，而 **Streamlit 会吞掉这个异常** ——
页面上表格不显示，`at.exception` 也是空的（AppTest 保存原始对象、不经过 Arrow 序列化）。
因此必须直接断言"这张表能被 Arrow 序列化"，才能在测试里拦住它。
"""
from __future__ import annotations

import pytest

from stock_plan.ui.views.strategy_mgr import _config_rows


CFG_WITH_RULES = {
    "weights": {"trend_score": 0.6, "fund_score": 0.4},
    "rules": {"macd_golden": True, "macd_above_zero": False, "require_breakout": True},
    "params": {"atr_k_entry": 0.0, "atr_m_exit": 3.5},
}


def test_config_rows_are_arrow_serialisable() -> None:
    """带规则的策略（真实数据都会有规则）必须能渲染成表。"""
    pa = pytest.importorskip("pyarrow")
    import pandas as pd

    rows = _config_rows(CFG_WITH_RULES)
    df = pd.DataFrame(rows)
    # 修前这里会抛 ArrowInvalid
    pa.Table.from_pandas(df)
    assert df["值"].map(type).nunique() == 1, "值列必须是同一种类型"
    assert df["值"].dtype == object or df["值"].dtype.kind in "OUS"


def test_config_rows_cover_all_three_categories() -> None:
    rows = _config_rows(CFG_WITH_RULES)
    cats = {r["类别"] for r in rows}
    assert cats == {"权重", "规则", "买卖价参数"}
    assert len(rows) == 2 + 3 + 2


def test_config_rows_render_rule_states_as_text() -> None:
    rows = _config_rows(CFG_WITH_RULES)
    by_param = {r["参数"]: r["值"] for r in rows}
    assert by_param["macd_golden"] == "启用"
    assert by_param["macd_above_zero"] == "关闭"
    # 数字也统一成字符串
    assert by_param["trend_score"] == "0.6"
    assert by_param["atr_m_exit"] == "3.5"


def test_config_rows_handle_empty_and_missing_sections() -> None:
    assert _config_rows({}) == []
    rows = _config_rows({"weights": {"a": 1.0}})
    assert rows == [{"类别": "权重", "参数": "a", "值": "1.0"}]


def test_config_rows_arrow_ok_for_every_category_alone() -> None:
    """任何单一类别也不应出问题（防止将来又把数字塞回同一列）。"""
    pa = pytest.importorskip("pyarrow")
    import pandas as pd

    for section in ("weights", "rules", "params"):
        pa.Table.from_pandas(pd.DataFrame(_config_rows({section: {"x": 1.0}})))
