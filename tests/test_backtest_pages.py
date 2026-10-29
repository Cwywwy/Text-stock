"""Phase 16 页面集成测试：共享 prepared 缓存、进度、缓存命中提示、清除按钮。

这里不跑真实回测/真实行情：`Storage`、`prepare_backtest`、引擎主循环、指标计算
全部替换成计数器假实现，只验证页面编排层（缓存键、命中、进度、展示参数不重算）。

为什么用 `streamlit.testing.v1.AppTest`：页面函数里有 `st.status` / `st.progress` /
`st.session_state` 等运行时副作用，裸调用拿不到元素；AppTest 在同一进程里用真实
Streamlit 运行时执行脚本，既能断言元素（status/caption/按钮），又能让模块级缓存
（`@st.cache_data` / `@st.cache_resource`）跨两次 `run()` 真正命中。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from stock_plan.ui import backtest_cache as btc
from stock_plan.ui.views import backtest as backtest_view
from stock_plan.ui.views import compare as compare_view
from stock_plan.ui.views import today as today_view
from stock_plan.ui.views import visual_builder as builder_view


# ---------------------------------------------------------------- 假实现

@dataclass(frozen=True)
class _FakePrepared:
    """与引擎 PreparedBacktest 同形的假对象（frozen dataclass 才能被 replace 浅拷贝）。"""

    factor_tables: dict
    stock_list: pd.DataFrame
    fund_map: dict
    bars_map: dict
    config: object


_FAKE_STOCK_LIST = pd.DataFrame({"code": ["000001", "600000"]})
# bars_map 必须非空：load_shared_prepared 遇到空行情会直接返回 (None, ...)，
# 那样页面会走「无数据」分支，测试就测不到缓存层了。
_FAKE_BARS = {"000001": pd.DataFrame({"date": pd.to_datetime(["2025-01-02"])})}


class _FakeStorage:
    """避免页面测试真的去读 data/processed/bars。"""

    def load_market_maps(self, **kwargs):
        del kwargs
        return _FAKE_STOCK_LIST.copy(), {k: v.copy() for k, v in _FAKE_BARS.items()}, {}

    def fundamentals_snapshot_date(self):
        return date(2025, 1, 1)


class _Runtime:
    """记录假实现被调用的次数：prepare 应为 1（共享缓存），run 随缓存命中增长。"""

    def __init__(self) -> None:
        self.prepare = 0
        self.run = 0
        self.metrics = 0
        self.bar_maps_seen: list[dict] = []


def _fake_result() -> SimpleNamespace:
    index = pd.to_datetime(["2025-01-02", "2025-01-03"])
    return SimpleNamespace(
        equity_curve=pd.Series([100_000.0, 101_000.0], index=index),
        trades=pd.DataFrame(),
        benchmark=pd.Series([1.0, 1.02], index=index),
    )


def _install_runtime(monkeypatch) -> _Runtime:
    """把页面依赖的「重活」全部换掉，只保留真实缓存层。"""
    calls = _Runtime()
    stock_list = _FAKE_STOCK_LIST.copy()

    def fake_prepare(*args, **kwargs):
        del args, kwargs
        calls.prepare += 1
        return _FakePrepared({}, stock_list, {}, {k: v.copy() for k, v in _FAKE_BARS.items()}, None)

    def fake_run(strategy, config, prepared):
        del strategy, config
        calls.run += 1
        calls.bar_maps_seen.append(prepared.bars_map)
        return _fake_result()

    def fake_metrics(curve, trades):
        del curve, trades
        calls.metrics += 1
        return {
            "total_return": 3.0,
            "annual_return": 2.0,
            "max_drawdown": -5.0,
            "sharpe": 1.1,
            "win_rate": 55.0,
            "profit_loss": 1.5,
            "trade_count": 4,
        }

    monkeypatch.setattr(btc, "Storage", _FakeStorage)
    monkeypatch.setattr(btc, "_prepare_prepared", fake_prepare)
    monkeypatch.setattr(btc, "_run_backtest_with_prepared", fake_run)
    # 策略构造也换掉：测试用的「甲策略/乙策略」不在注册表里，走真 store 会 KeyError
    monkeypatch.setattr(
        btc, "_create_strategy",
        lambda name, kind="registry", params_json="": SimpleNamespace(name=name, params={}),
    )
    monkeypatch.setattr(btc, "calc_metrics", fake_metrics)
    monkeypatch.setattr(btc, "market_data_version", lambda *a, **k: "test-version")
    monkeypatch.setattr(btc, "universe_limit", lambda: None)
    # 假回测是毫秒级：把「<0.5s 视为缓存命中」的阈值降到 0，才能用文案区分首次/二次
    monkeypatch.setattr(btc, "CACHE_HIT_SECONDS", 0.0)
    btc.clear_ui_caches()
    # 会话命中标记也清掉，避免上一个用例的键串到本用例
    try:
        st.session_state.pop(btc._HIT_STATE_KEY, None)
    except Exception:  # noqa: BLE001 — 某些运行时下 session_state 不可用，忽略即可
        pass
    return calls


def _spy_progress(monkeypatch) -> list[str]:
    """记录 st.progress 的文案（进度条跑完会被 empty()，最终元素树里看不到）。"""
    texts: list[str] = []
    real = st.progress

    def spy(value=0.0, *, text=None, **kwargs):
        if text:
            texts.append(str(text))
        return real(value, text=text, **kwargs)

    monkeypatch.setattr(st, "progress", spy)
    return texts


def _patch_compare_chrome(monkeypatch) -> None:
    monkeypatch.setattr(compare_view, "board_filter_ui", lambda *a, **k: ([], True))
    monkeypatch.setattr(compare_view, "page_glossary", lambda *a, **k: None)
    monkeypatch.setattr(compare_view, "render_chart", lambda *a, **k: None)
    monkeypatch.setattr(compare_view, "store", _fake_store())


def _patch_backtest_chrome(monkeypatch) -> None:
    monkeypatch.setattr(backtest_view, "board_filter_ui", lambda *a, **k: ([], True))
    monkeypatch.setattr(backtest_view, "page_glossary", lambda *a, **k: None)
    monkeypatch.setattr(backtest_view, "render_chart", lambda *a, **k: None)
    monkeypatch.setattr(backtest_view, "backtest_hero_fig", lambda *a, **k: None)
    monkeypatch.setattr(backtest_view, "make_report", lambda *a, **k: {})
    monkeypatch.setattr(backtest_view, "review_backtest", lambda *a, **k: "")
    from stock_plan.strategy import store as store_module

    monkeypatch.setattr(store_module, "strategy_options", lambda: ["趋势跟随策略"])
    monkeypatch.setattr(store_module, "strip_display_name", lambda name: name)
    monkeypatch.setattr(store_module, "load_strategy", lambda name: {})
    monkeypatch.setattr(store_module, "display_name", lambda name: name)


def _patch_builder_chrome(monkeypatch) -> None:
    monkeypatch.setattr(builder_view, "board_filter_ui", lambda *a, **k: ([], True))
    monkeypatch.setattr(builder_view, "page_glossary", lambda *a, **k: None)
    monkeypatch.setattr(builder_view, "render_chart", lambda *a, **k: None)
    monkeypatch.setattr(builder_view, "backtest_hero_fig", lambda *a, **k: None)
    monkeypatch.setattr(builder_view, "render_ai_strategy_section", lambda *a, **k: None)


def _fake_store() -> SimpleNamespace:
    return SimpleNamespace(
        strategy_options=lambda: ["趋势跟随策略"],
        strip_display_name=lambda name: name,
        display_name=lambda name: name,
        load_strategy=lambda name: {},
        list_strategies=lambda: [],
    )


def _parse_elapsed(note: str) -> float:
    match = re.search(r"耗时 ([\d.]+)s", note)
    assert match, f"缓存文案缺少耗时：{note}"
    return float(match.group(1))


def _button_by_label(at: AppTest, label_part: str):
    for button in at.button:
        if label_part in button.label:
            return button
    raise AssertionError(f"未找到按钮 {label_part!r}，现有：{[b.label for b in at.button]}")


# ---------------------------------------------------------------- 对比页

def test_comparison_progress_and_cache_hit(monkeypatch):
    """7 策略循环可见进度；同参数第二次全部命中结果缓存（<2s）。"""
    calls = _install_runtime(monkeypatch)
    texts = _spy_progress(monkeypatch)
    monkeypatch.setattr(
        compare_view,
        "_comparison_specs",
        lambda: {"甲策略": ("registry", "甲策略"), "乙策略": ("registry", "乙策略")},
    )
    monkeypatch.setattr(compare_view, "_strategy_sig", lambda label: "")

    script = (
        "import streamlit as st\n"
        "from stock_plan.ui.views.compare import _run_comparison\n"
        'results, note = _run_comparison("2024-01-01", "2025-01-01", 100000.0, "weekly", True, 30, "[]", True)\n'
        'st.caption(note)\n'
        'st.write(f"count={len(results)}")\n'
    )
    at = AppTest.from_string(script, default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.prepare == 1, "共享 prepared 应当只构建一次"
    assert calls.run == 2, "首次运行每个策略各算一次"
    assert "本次为实时计算" in at.caption[0].value
    assert any("正在跑 1/2" in text for text in texts), f"进度文案缺失：{texts}"

    # 第二次同参数：结果全部命中，不重算
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.run == 2, "第二次运行不应重算"
    note = at.caption[0].value
    assert "本次为缓存结果" in note
    assert _parse_elapsed(note) < 2.0

    # 清除缓存后重算：prepared 与结果都 miss
    btc.clear_ui_caches()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.run == 4
    assert calls.prepare == 2


def test_compare_page_clear_button_and_progress_status(monkeypatch):
    """整页冒烟：清除缓存按钮就位；点击运行后能渲染结果、状态折叠为耗时。"""
    calls = _install_runtime(monkeypatch)
    _spy_progress(monkeypatch)
    _patch_compare_chrome(monkeypatch)
    monkeypatch.setattr(compare_view, "_strategy_sig", lambda label: "")

    at = AppTest.from_string(
        "from stock_plan.ui.views import compare\ncompare.render()\n",
        default_timeout=120,
    )
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    clear = at.button(key="cmp_clear_cache")
    assert clear.label == "🧹 清除缓存"

    at.button(key="cmp_run").click().run()
    assert not at.exception, [e.value for e in at.exception]
    status = at.status[0]
    assert status.label.startswith("对比完成")
    assert status.state == "complete"
    assert any("本次为实时计算" in c.value for c in at.caption)
    assert calls.run == 7, "内置 7 策略各跑一次"

    # 再点一次：结果缓存全命中
    at.button(key="cmp_run").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.run == 7
    assert any("本次为缓存结果" in c.value for c in at.caption)


# ---------------------------------------------------------------- 回测页

def test_backtest_page_display_toggle_does_not_recompute(monkeypatch):
    """切「标注买卖点」只是展示参数：不应触发回测，也不应造成缓存 miss。"""
    calls = _install_runtime(monkeypatch)
    _patch_backtest_chrome(monkeypatch)

    at = AppTest.from_string(
        "from stock_plan.ui.views import backtest\nbacktest.render()\n",
        default_timeout=120,
    )
    at.run()
    assert not at.exception, [e.value for e in at.exception]

    _button_by_label(at, "运行回测").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.run == 1
    assert any("本次为实时计算" in c.value for c in at.caption)

    # 只改展示参数：重跑脚本不应重新回测
    at.toggle(key="backtest_show_trades").set_value(True).run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.run == 1, "展示参数变化不应触发回测"

    # 同参数再点运行：命中缓存
    _button_by_label(at, "运行回测").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.run == 1
    assert any("本次为缓存结果" in c.value for c in at.caption)


# ---------------------------------------------------------------- 拼装页

def test_builder_name_change_is_cache_hit(monkeypatch):
    """策略名称只是展示/提交用：改名不应触发重算。"""
    calls = _install_runtime(monkeypatch)

    script = (
        "import streamlit as st\n"
        "from stock_plan.ui.views.visual_builder import _run_custom_backtest\n"
        'cfg = {"name": "甲", "params": {"hold_days": 30}}\n'
        'm1, r1, n1 = _run_custom_backtest(cfg, "2024-01-01", "2025-01-01", 100000.0, "weekly", True, [], True)\n'
        'cfg2 = dict(cfg); cfg2["name"] = "乙"\n'
        'm2, r2, n2 = _run_custom_backtest(cfg2, "2024-01-01", "2025-01-01", 100000.0, "weekly", True, [], True)\n'
        'st.caption(n1)\n'
        'st.caption(n2)\n'
    )
    at = AppTest.from_string(script, default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.run == 1, "改名不应重算"
    assert "本次为实时计算" in at.caption[0].value
    assert "本次为缓存结果" in at.caption[1].value


def test_builder_page_smoke(monkeypatch):
    calls = _install_runtime(monkeypatch)
    _patch_builder_chrome(monkeypatch)

    at = AppTest.from_string(
        "from stock_plan.ui.views import visual_builder\nvisual_builder.render()\n",
        default_timeout=120,
    )
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.button(key="vb_clear_cache").label == "🧹 清除缓存"

    _button_by_label(at, "运行回测").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert calls.run == 1
    assert any("本次为实时计算" in c.value for c in at.caption)


# ---------------------------------------------------------------- prepared 只读约定

def test_shared_prepared_survives_callers_mutation(monkeypatch):
    """R4：入口浅拷贝，调用方（或引擎）改容器不能污染共享缓存对象。"""
    calls = _install_runtime(monkeypatch)

    def mutating_run(strategy, config, prepared):
        del strategy, config
        calls.run += 1
        prepared.bars_map["999999"] = pd.DataFrame()
        prepared.fund_map["999999"] = {}
        return _fake_result()

    monkeypatch.setattr(btc, "_run_backtest_with_prepared", mutating_run)
    args = ("2024-01-01", "2025-01-01", 180, "[]", True, False, None, "test-version")
    btc.run_strategy_cached(*args, "甲策略", "registry", "", "", json.dumps(
        btc.config_to_dict(_fake_config())
    ))
    assert calls.run == 1
    prepared, _stock_list, _fund_map = btc.load_shared_prepared(*args)
    assert "999999" not in prepared.bars_map, "调用方不得污染共享 prepared"
    assert "999999" not in prepared.fund_map
    # 第二次命中结果缓存，不会再看 prepared
    btc.run_strategy_cached(*args, "甲策略", "registry", "", "", json.dumps(
        btc.config_to_dict(_fake_config())
    ))
    assert calls.run == 1


def _fake_config():
    from stock_plan.backtest.engine import BacktestConfig

    return BacktestConfig(start=date(2024, 1, 1), end=date(2025, 1, 1))


# ---------------------------------------------------------------- 今日信号页共享 prepared

def _install_today_runtime(monkeypatch, *, cloud: bool = False) -> SimpleNamespace:
    """今日信号页：换掉行情加载/因子准备/策略构造/生成器，只验证 prepared 接线。"""
    counts = SimpleNamespace(storage=0, prepare=0)
    stock_list = _FAKE_STOCK_LIST.copy()

    class _CountingStorage:
        """记录行情加载次数：多周期并存时应当只有 1 次。"""

        def load_market_maps(self, **kwargs):
            del kwargs
            counts.storage += 1
            return stock_list.copy(), {k: v.copy() for k, v in _FAKE_BARS.items()}, {}

        def fundamentals_snapshot_date(self):
            return date(2025, 1, 1)

    def fake_prepare(*args, **kwargs):
        del args, kwargs
        counts.prepare += 1
        return _FakePrepared(
            {}, stock_list.copy(), {}, {k: v.copy() for k, v in _FAKE_BARS.items()}, None
        )

    monkeypatch.setattr(btc, "Storage", _CountingStorage)
    monkeypatch.setattr(btc, "_prepare_prepared", fake_prepare)
    monkeypatch.setattr(btc, "market_data_version", lambda *a, **k: "test-version")
    monkeypatch.setattr(btc, "universe_limit", lambda: None)
    btc.clear_ui_caches()
    try:
        st.session_state.pop(btc._HIT_STATE_KEY, None)
    except Exception:  # noqa: BLE001 — 某些运行时下 session_state 不可用，忽略即可
        pass
    from stock_plan.data import snapshot as snapshot_module

    monkeypatch.setattr(snapshot_module, "is_cloud", lambda: cloud)
    monkeypatch.setattr(
        today_view,
        "_get_strategy",
        lambda name, params=None: SimpleNamespace(name=name, params=params or {}),
    )
    today_view._cached_signals_and_report.clear()
    return counts


def _capture_today_generation(monkeypatch) -> list[dict]:
    """拦截生成器调用，记录它实际收到的关键字参数（含 prepared）。"""
    seen: list[dict] = []

    def fake_generate(**kwargs):
        seen.append(kwargs)
        return [], None

    monkeypatch.setattr(today_view, "generate_signals_with_report", fake_generate)
    return seen


def test_today_multi_period_shares_one_prepared(monkeypatch):
    """多周期并存：三个周期各调一次缓存函数，但行情与因子准备只做一次。"""
    counts = _install_today_runtime(monkeypatch)
    seen = _capture_today_generation(monkeypatch)

    for hold_days in (10, 20, 30):
        signals, report = today_view._cached_signals_and_report(
            "趋势跟随策略",
            5,
            json.dumps({"hold_days": hold_days}),
            "{}",
            "[]",
            True,
            "[]",
        )
        assert signals == [] and report is None

    assert len(seen) == 3, "三个周期应各生成一次信号"
    assert counts.storage == 1, "行情只应加载一次（旧实现是三次）"
    assert counts.prepare == 1, "prepared 只应构建一次（旧实现是三次）"
    prepareds = [kwargs["prepared"] for kwargs in seen]
    assert all(prepared is not None for prepared in prepareds), "prepared 应透传给生成器"
    assert prepareds[0] is prepareds[1] is prepareds[2], "三个周期应共享同一份 prepared"


def test_today_prepared_loader_matches_generator_window(monkeypatch):
    """prepared 口径必须与 generator 内部加载一致：今天、回看 180 天、lead_days=0。"""
    _install_today_runtime(monkeypatch)
    real_loader = btc.load_shared_prepared
    captured: dict = {}

    def spy(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return real_loader(*args, **kwargs)

    monkeypatch.setattr(btc, "load_shared_prepared", spy)
    seen = _capture_today_generation(monkeypatch)
    boards_json = json.dumps(["主板"], ensure_ascii=False)

    today_view._cached_signals_and_report("趋势跟随策略", 5, "{}", "{}", boards_json, True, "[]")

    end = date.today()
    start = end - timedelta(days=today_view.SIGNAL_LOOKBACK_DAYS)
    assert captured["args"] == (
        start.isoformat(),
        end.isoformat(),
        0,
        boards_json,
        True,
        False,
        None,
        "test-version",
    )
    assert seen[0]["prepared"] is not None
    assert seen[0]["boards"] == ["主板"], "板块口径应原样传给生成器"


def test_today_prepared_failure_falls_back_to_old_path(monkeypatch):
    """取不到 prepared（无数据）或准备层抛错时，生成器收到 None 并照旧出信号。"""
    _install_today_runtime(monkeypatch)
    seen = _capture_today_generation(monkeypatch)

    monkeypatch.setattr(
        btc,
        "load_shared_prepared",
        lambda *args, **kwargs: (None, _FAKE_STOCK_LIST.copy(), {}),
    )
    assert today_view._cached_signals_and_report(
        "趋势跟随策略", 5, '{"hold_days": 10}', "{}"
    ) == ([], None)
    assert seen[0]["prepared"] is None, "prepared 为空时应回退生成器内部加载"

    def boom(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("准备层故障")

    monkeypatch.setattr(btc, "load_shared_prepared", boom)
    assert today_view._cached_signals_and_report(
        "趋势跟随策略", 5, '{"hold_days": 20}', "{}"
    ) == ([], None)
    assert seen[1]["prepared"] is None, "准备层失败不应把异常抛给页面"


def test_today_cloud_path_does_not_load_prepared(monkeypatch):
    """云端发布信号分支必须保持原样：不加载行情、不构建 prepared、不调生成器。"""
    counts = _install_today_runtime(monkeypatch, cloud=True)
    from stock_plan.strategy import store as store_module

    monkeypatch.setattr(store_module, "load_strategy", lambda name: None)
    seen = _capture_today_generation(monkeypatch)

    assert today_view._cached_signals_and_report("策略", 5, "{}", "{}") == ([], None)
    assert counts.storage == 0, "云端分支不应加载行情"
    assert counts.prepare == 0, "云端分支不应构建 prepared"
    assert seen == [], "云端分支不应调用生成器"
