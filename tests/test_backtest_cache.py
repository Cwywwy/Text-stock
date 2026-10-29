"""回测缓存层测试 — 缓存键、prepared 只读约定、缓存命中与手动清除。

测试策略：
- 键函数是纯函数，直接断言「哪些参数换键、哪些不换键」；
- 缓存行为用 monkeypatch 替换 Storage / 引擎准备层 / 回测执行，统计调用次数，
  验证同参二次命中、展示参数不触发重算、清除后重新计算；
- 用轻量 FakePrepared 模拟引擎契约（factor_tables/stock_list/fund_map/bars_map/config），
  这样引擎 Phase 15 的 prepare_backtest 尚未落地时测试也能独立通过。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date

import pandas as pd
import pytest

from stock_plan.backtest.engine import BacktestConfig, BacktestResult
from stock_plan.ui import backtest_cache as bck


@dataclass(frozen=True)
class FakePrepared:
    """模拟引擎的 PreparedBacktest（字段与契约同名）。"""

    factor_tables: dict = field(default_factory=dict)
    stock_list: pd.DataFrame = field(default_factory=pd.DataFrame)
    fund_map: dict = field(default_factory=dict)
    bars_map: dict = field(default_factory=dict)
    config: BacktestConfig | None = None


@pytest.fixture(autouse=True)
def _clean_caches():
    """每个用例前后清空本模块缓存，避免用例间互相污染。"""
    bck.clear_ui_caches()
    yield
    bck.clear_ui_caches()


@pytest.fixture
def fake_env(monkeypatch):
    """替换 Storage、引擎准备层、回测执行与指标计算，返回调用计数。"""
    counts = {"prepare": 0, "run": 0}

    class FakeStorage:
        def load_market_maps(self, **kwargs):
            stock_list = pd.DataFrame({"code": ["000001", "600000"], "name": ["A", "B"]})
            bars_map = {
                "000001": pd.DataFrame(
                    {"date": [pd.Timestamp("2025-01-02")], "close": [10.0]}
                )
            }
            fund_map = {"000001": {"pe": 10.0}}
            return stock_list, bars_map, fund_map

        def fundamentals_snapshot_date(self):
            return date(2025, 1, 2)

    def fake_prepare(bars_map, fund_map, stock_list, config):
        counts["prepare"] += 1
        return FakePrepared(
            factor_tables={"000001": object()},
            stock_list=stock_list,
            fund_map=dict(fund_map),
            bars_map=dict(bars_map),
            config=config,
        )

    def fake_run(strategy, config, prepared):
        counts["run"] += 1
        equity = pd.DataFrame(
            {"date": [pd.Timestamp("2025-01-02")], "equity": [100_000.0]}
        )
        return BacktestResult(
            equity_curve=equity,
            trades=[],
            metrics={"total_return": 0.0},
            benchmark=None,
        )

    monkeypatch.setattr(bck, "Storage", FakeStorage)
    monkeypatch.setattr(bck, "_prepare_prepared", fake_prepare)
    monkeypatch.setattr(bck, "_run_backtest_with_prepared", fake_run)
    monkeypatch.setattr(bck, "calc_metrics", lambda equity, trades: {"n_trades": len(trades)})
    return counts


BASE_ARGS = dict(
    start="2021-12-07",
    end="2026-10-28",
    lead_days=180,
    boards_json='["主板", "创业板"]',
    exclude_st=True,
    include_delisted=False,
    max_codes=None,
    data_version="dv-1",
)
CONFIG = BacktestConfig(start=date(2021, 12, 7), end=date(2026, 10, 28), market_timing=True)
CONFIG_JSON = json.dumps(bck.config_to_dict(CONFIG), ensure_ascii=False)


def _run(**overrides):
    """调用 run_strategy_cached 的简写：默认内置趋势策略 + 同一份 config。"""
    kwargs = dict(BASE_ARGS)
    kwargs.update(
        dict(
            strategy_name="趋势跟随策略",
            strategy_kind="registry",
            params_json="",
            strategy_sig="",
            config_json=CONFIG_JSON,
        )
    )
    kwargs.update(overrides)
    return bck.run_strategy_cached(**kwargs)


# ---------------------------------------------------------------- 数据版本指纹


def test_market_data_version_tracks_file_changes(tmp_path):
    bars = tmp_path / "bars"
    bars.mkdir()
    db = tmp_path / "meta.db"
    first = bck.market_data_version(bars, db)
    (bars / "000001.parquet").write_bytes(b"x")
    db.write_bytes(b"y")
    assert bck.market_data_version(bars, db) != first


def test_market_data_version_handles_missing_paths(tmp_path):
    assert isinstance(bck.market_data_version(tmp_path / "missing", tmp_path / "no.db"), str)


# ---------------------------------------------------------------- 键函数


def test_prepared_key_ignores_board_order():
    key_a = bck.build_prepared_key(**BASE_ARGS)
    key_b = bck.build_prepared_key(**{**BASE_ARGS, "boards_json": '["创业板", "主板"]'})
    assert key_a == key_b


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("start", "2022-01-01"),
        ("end", "2026-01-01"),
        ("lead_days", 400),
        ("boards_json", '["主板"]'),
        ("exclude_st", False),
        ("include_delisted", True),
        ("max_codes", 200),
        ("data_version", "dv-2"),
    ],
)
def test_prepared_key_sensitive_to_scope_params(field_name, value):
    assert bck.build_prepared_key(**{**BASE_ARGS, field_name: value}) != bck.build_prepared_key(
        **BASE_ARGS
    )


def test_config_fingerprint_tracks_every_field():
    base = bck.build_config_fingerprint(CONFIG)
    assert base == bck.build_config_fingerprint(replace(CONFIG))
    for changed in (
        replace(CONFIG, commission=0.0005),
        replace(CONFIG, stamp_tax=0.002),
        replace(CONFIG, market_timing=False),
        replace(CONFIG, max_hold_days=10),
        replace(CONFIG, start=date(2022, 1, 1)),
    ):
        assert bck.build_config_fingerprint(changed) != base


def test_strategy_fingerprint_tracks_params_and_name():
    class Base:
        params = {"hold_days": 20}

    class Other:
        params = {"hold_days": 20, "top_n": 3}

    assert bck.build_strategy_fingerprint(Base()) == bck.build_strategy_fingerprint(Base())
    assert bck.build_strategy_fingerprint(Base()) != bck.build_strategy_fingerprint(Other())
    assert bck.build_strategy_fingerprint(Base(), name="甲") != bck.build_strategy_fingerprint(
        Base(), name="乙"
    )


def test_request_key_ignores_display_only_state():
    state = {
        "start": "2021-12-07",
        "end": "2026-10-28",
        "initial_cash": 100_000.0,
        "hero_mode": "收益率",
        "show_trades": True,
        "active_tab": "概览",
    }
    first = bck.build_request_key(state)
    second = bck.build_request_key(
        {**state, "hero_mode": "金额", "show_trades": False, "active_tab": "交易明细"}
    )
    assert first == second
    assert bck.build_request_key({**state, "initial_cash": 50_000.0}) != first


def test_result_key_changes_with_each_component():
    base = bck.build_result_key(
        prepared_key="p1", strategy_fingerprint="s1", request_key=(("start", "x"),)
    )
    assert base != bck.build_result_key(
        prepared_key="p2", strategy_fingerprint="s1", request_key=(("start", "x"),)
    )
    assert base != bck.build_result_key(
        prepared_key="p1", strategy_fingerprint="s2", request_key=(("start", "x"),)
    )
    assert base != bck.build_result_key(
        prepared_key="p1", strategy_fingerprint="s1", request_key=(("start", "y"),)
    )


def test_config_dict_roundtrip():
    restored = bck.config_from_dict(json.loads(CONFIG_JSON))
    assert restored == CONFIG


# ---------------------------------------------------------------- prepared 只读约定


def test_clone_prepared_copies_containers_not_data():
    prepared = FakePrepared(
        factor_tables={"000001": object()},
        stock_list=pd.DataFrame({"code": ["000001"]}),
        fund_map={"000001": {"pe": 1.0}},
        bars_map={"000001": pd.DataFrame({"close": [1.0]})},
        config=CONFIG,
    )
    clone = bck.clone_prepared(prepared)
    assert clone is not prepared
    assert clone.config == prepared.config
    # 容器外壳是新的
    for field_name in ("factor_tables", "fund_map", "bars_map", "stock_list"):
        assert getattr(clone, field_name) is not getattr(prepared, field_name), field_name
    # 改副本容器不影响原对象（R4 隔离）
    clone.bars_map["999999"] = pd.DataFrame()
    clone.fund_map["999999"] = {}
    assert "999999" not in prepared.bars_map
    assert "999999" not in prepared.fund_map
    # DataFrame 共享数据块（不复制大数组）
    assert clone.stock_list.equals(prepared.stock_list)


def test_prepared_maps_returns_triplet():
    prepared = FakePrepared(bars_map={"a": 1}, fund_map={"a": 2}, stock_list=pd.DataFrame())
    maps = bck.prepared_maps(prepared)
    assert maps is not None
    assert maps[0] == {"a": 1} and maps[1] == {"a": 2}
    assert bck.prepared_maps(None) is None


def test_cache_caption_marks_fast_or_seen():
    assert "缓存结果" in bck.cache_caption(0.1, saw_before=False)
    assert "缓存结果" in bck.cache_caption(9.9, saw_before=True)
    assert "实时计算" in bck.cache_caption(9.9, saw_before=False)


# ---------------------------------------------------------------- 缓存命中与清除


def test_shared_prepared_is_cached(fake_env):
    first = bck.load_shared_prepared(**BASE_ARGS)
    second = bck.load_shared_prepared(**BASE_ARGS)
    assert fake_env["prepare"] == 1
    assert first[0] is second[0]


def test_shared_prepared_invalidated_by_data_version(fake_env):
    bck.load_shared_prepared(**BASE_ARGS)
    bck.load_shared_prepared(**{**BASE_ARGS, "data_version": "dv-2"})
    assert fake_env["prepare"] == 2


def test_strategy_result_cached_and_reused(fake_env):
    first, metrics = _run()
    second, metrics2 = _run()
    assert fake_env["run"] == 1
    assert metrics == metrics2
    assert first is not None


def test_strategy_result_ignores_display_params_recomputes_on_config(fake_env):
    """展示参数不进缓存键：同口径重复调用命中；改回测口径（config_json）重算。"""
    _run()
    _run()
    assert fake_env["run"] == 1
    other_config = json.dumps(
        bck.config_to_dict(replace(CONFIG, initial_cash=50_000.0)), ensure_ascii=False
    )
    _run(config_json=other_config)
    assert fake_env["run"] == 2


def test_clear_ui_caches_forces_recompute(fake_env):
    _run()
    assert fake_env["run"] == 1
    bck.clear_ui_caches()
    _run()
    assert fake_env["run"] == 2
    bck.load_shared_prepared(**BASE_ARGS)
    assert fake_env["prepare"] == 2


def test_walkforward_cached_reused_and_cleared(fake_env, monkeypatch):
    counts = {"walkforward": 0}

    def fake_walk_forward(factory, bars_map, fund_map, stock_list, start, end, train_days=180, test_days=90):
        counts["walkforward"] += 1
        return {"trains": [], "oos_equity": pd.Series(dtype=float)}

    monkeypatch.setattr(bck, "walk_forward", fake_walk_forward)
    args = dict(
        BASE_ARGS,
        strategy_name="趋势跟随策略",
        train_days=180,
        test_days=90,
    )
    first = bck.run_walkforward_cached(**args)
    second = bck.run_walkforward_cached(**args)
    assert counts["walkforward"] == 1
    assert first is not None and second is not None
    # 窗口参数变化 → 重新计算
    bck.run_walkforward_cached(**{**args, "train_days": 120})
    assert counts["walkforward"] == 2
    # 清除缓存 → 重新计算（且复用同一份 prepared，不重复 prepare）
    bck.clear_ui_caches()
    bck.run_walkforward_cached(**args)
    assert counts["walkforward"] == 3
    assert fake_env["prepare"] == 2


def test_mark_cache_hit_session_state():
    assert bck.mark_cache_hit(("k",)) is False
    assert bck.mark_cache_hit(("k",)) is True
    bck.clear_ui_caches()
    assert bck.mark_cache_hit(("k",)) is False


def test_missing_data_returns_none(fake_env, monkeypatch):
    def empty_prepare(bars_map, fund_map, stock_list, config):
        fake_env["prepare"] += 1
        return None

    monkeypatch.setattr(bck, "_prepare_prepared", empty_prepare)
    result, metrics = _run()
    assert result is None and metrics == {}


# ---------------------------------------------------------------- 策略实例化


def test_create_strategy_builtin_and_saved(monkeypatch):
    calls = {}

    def fake_builtin(name, params):
        calls["builtin"] = (name, params)
        return object()

    def fake_saved(name, params):
        calls["saved"] = (name, params)
        return object()

    monkeypatch.setattr(bck, "create_strategy", fake_builtin)
    monkeypatch.setattr(bck.store, "resolve_strategy", fake_saved)
    bck._create_strategy("趋势跟随策略", "registry", '{"top_n": 3}')
    assert calls["builtin"] == ("趋势跟随策略", {"top_n": 3})
    bck._create_strategy("我的策略（自存）", "registry", "")
    assert calls["saved"] == ("我的策略（自存）", None)


def test_create_strategy_custom_uses_custom_class(monkeypatch):
    created = {}

    class FakeCustom:
        params = {"hold_days": 15}

        def __init__(self, config):
            created["config"] = config

    monkeypatch.setattr("stock_plan.strategy.custom.CustomStrategy", FakeCustom)
    strategy = bck._create_strategy(
        "自定义", "custom", json.dumps({"params": {"hold_days": 15}})
    )
    assert created["config"]["params"]["hold_days"] == 15
    assert strategy.params["hold_days"] == 15


# ---------------------------------------------------------------- 页面按钮冒烟


def test_clear_cache_button_clears_and_reruns(fake_env):
    """AppTest 冒烟：按钮文案为简体中文，点击后 target cache 被清空并重跑页面。"""
    from streamlit.testing.v1 import AppTest

    script = (
        "import streamlit as st\n"
        "from stock_plan.ui.backtest_cache import render_clear_cache_button\n"
        "render_clear_cache_button('smoke')\n"
    )
    _run()
    assert fake_env["run"] == 1

    at = AppTest.from_string(script, default_timeout=30).run()
    assert not at.exception
    assert [button.label for button in at.button] == ["🧹 清除缓存"]
    assert at.button[0].key == "smoke_clear_cache"

    at.button[0].click().run()
    assert not at.exception
    # 清除后同一参数必须重算（缓存确实没命中）
    _run()
    assert fake_env["run"] == 2
