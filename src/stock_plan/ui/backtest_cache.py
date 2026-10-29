"""回测缓存层 — 全站共享 prepared 数据与逐策略结果缓存。

为什么需要（Phase 16 性能改造）：
- 策略对比页一次跑 7 个策略，旧实现每个策略各自 `load_market_maps`（5000+ 个
  parquet）并各自构建一遍因子表；回测页、策略拼装页又各存一份
  `@st.cache_data` 的「加载 + 回测」结果，同一区间会被重复加载与重复准备。
- 这里把「加载行情 + 预计算因子表」放进 `@st.cache_resource`（进程级共享、不 pickle），
  把「单个策略回测结果」放进 `@st.cache_data`（可持久化到磁盘）。展示参数
  （图表模式、买卖点开关、当前标签页）**不参与**缓存键，切换展示不会触发重算。

只读约定（R4）：`@st.cache_resource` 返回的 prepared 会被多个页面/策略共享，
调用方在入口用 `clone_prepared()` 做浅拷贝（复制 dict 容器、不复制 numpy/大 DataFrame），
`run_backtest` 不得原地改写传入对象，否则会污染缓存。引擎侧对缺失 code 的惰性补建
只写局部 dict，不回写 prepared（Lead 已与引擎侧确认）。

手动清除（R6）：页面顶部的「🧹 清除缓存」按钮只清本模块两个 target cache，
不动全局 `st.cache_*`；清除后同一参数会重新显示「正在运行」。

Revision History:
    2026-10-29  (phase 16) add shared prepared cache, per-strategy result cache and clear-cache button
    2026-10-29  (phase 16) add walk-forward result cache sharing the same prepared data
    2026-10-29  (phase 16) expose universe limit and strategy hold-days helpers for page integration
"""
from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict, is_dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import streamlit as st

from stock_plan.backtest.engine import BacktestConfig, BacktestResult, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.backtest.walkforward import walk_forward
from stock_plan.data.storage import BARS_DIR, DB_PATH, Storage
from stock_plan.strategy import store
from stock_plan.strategy.registry import STRATEGIES, create_strategy

# 结果缓存有效期：1 小时。数据更新会由 data_version 指纹自动失效，ttl 只是兜底
RESULT_TTL_SECONDS = 3600
# 结果缓存最多保留 64 份（一次对比 7 个策略 × 若干区间/成本组合，够用且不占内存）
RESULT_MAX_ENTRIES = 64
# Walk-Forward 结果比单策略结果重（含逐窗口明细），只留 32 份
WALKFORWARD_MAX_ENTRIES = 32
# prepared 是重对象（全市场因子表上百 MB），只留最近 2 份，防止切换区间时内存翻倍
PREPARED_MAX_ENTRIES = 2
# 耗时低于该阈值即视为命中缓存（跨会话首次命中、会话标记失效时兜底）
CACHE_HIT_SECONDS = 0.5
# 会话标记：记录本会话跑过的口径键，用于显示「本次为缓存结果」
_HIT_STATE_KEY = "_backtest_cache_seen_keys"

# 参与回测缓存键的页面状态（只取口径参数，展示参数一律不进键）
_BACKTEST_STATE_KEYS: tuple[str, ...] = (
    "start",
    "end",
    "initial_cash",
    "rebalance_freq",
    "market_timing",
    "timing_tier",
    "max_hold_days",
    "commission_w",
    "stamp_tax_w",
    "slippage_w",
    "min_commission",
    "include_delisted",
    "boards_json",
    "exclude_st",
    "max_codes",
)

# 纯展示参数：改这些不应触发重算，也不应改变会话命中标记
_DISPLAY_ONLY_KEYS: frozenset[str] = frozenset(
    {
        "hero_mode",
        "show_trades",
        "chart_mode",
        "view_mode",
        "tab",
        "active_tab",
        "table_sort",
        "table_page",
        "llm_review",
    }
)


def universe_limit() -> int | None:
    """当前运行环境的股票数量上限：云端取抽样上限（控内存），本地 None（全市场）。

    三个回测页共用同一口径：prepared 缓存键里的 max_codes 必须与这里一致，
    否则同一次页面运行里会出现「加载按全市场、缓存键按抽样」的错位。
    """
    from stock_plan.data.snapshot import CLOUD_MAX_CODES, is_cloud

    return CLOUD_MAX_CODES if is_cloud() else None


def strategy_hold_days(
    strategy_name: str, strategy_kind: str = "registry", params_json: str = ""
) -> int:
    """读策略自带的 hold_days（0 = 未声明，页面用默认值）。

    为什么页面要先实例化一次：对比页允许「策略自带持仓天数优先于页面滑杆」，
    而真实回测在缓存函数体内才建策略，页面必须先知道这个数才能构造 BacktestConfig。
    实例化失败（自存策略被删等）时退回 0，让页面用默认值而不是整页崩掉。
    """
    try:
        strategy = _create_strategy(strategy_name, strategy_kind, params_json)
    except Exception:  # noqa: BLE001 — 页面兜底：取不到就按页面默认值
        return 0
    return int((getattr(strategy, "params", {}) or {}).get("hold_days") or 0)


def market_data_version(
    bars_dir: Path | str | None = None,
    db_path: Path | str | None = None,
) -> str:
    """行情/财务数据的廉价指纹（目录 mtime + 大小 → sha1 前 16 位）。

    目的：本地数据更新（每日任务增量写 parquet、重建 meta.db、云端快照恢复）后，
    prepared 与结果缓存自动失效，避免回测拿到旧数据却显示成新结果。
    只看顶层目录的 mtime/大小，不遍历 5000+ 文件，开销在毫秒级。

    参数：
        bars_dir: 日线目录，默认用 storage.BARS_DIR（测试可传临时目录）。
        db_path: 元数据库路径，默认用 storage.DB_PATH。

    返回：
        16 位十六进制指纹；路径不存在时对应段记为 "missing"。
    """
    parts: list[str] = []
    for label, path in (
        ("bars", Path(bars_dir) if bars_dir is not None else BARS_DIR),
        ("meta", Path(db_path) if db_path is not None else DB_PATH),
    ):
        try:
            info = path.stat()
        except OSError:
            parts.append(f"{label}:missing")
        else:
            parts.append(f"{label}:{info.st_mtime_ns}:{info.st_size}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    """把日期 / dataclass / 映射转成稳定可 JSON 序列化的结构。"""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(item) for item in value]
    return value


def _digest(payload: Any) -> str:
    """对可序列化负载取稳定 sha1 前 16 位，作为缓存键片段。"""
    text = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _canonical_boards(boards_json: str) -> list[str]:
    """板块筛选是无序集合语义，键里统一排序，避免勾选顺序不同导致缓存 miss。"""
    try:
        boards = json.loads(boards_json) if boards_json else []
    except (TypeError, json.JSONDecodeError):
        return [str(boards_json)]
    if not isinstance(boards, list):
        return [str(boards)]
    return sorted({str(board) for board in boards})


def build_prepared_key(
    *,
    start: str,
    end: str,
    lead_days: int,
    boards_json: str,
    exclude_st: bool,
    include_delisted: bool,
    max_codes: int | None,
    data_version: str,
) -> str:
    """shared prepared 的缓存键（数据口径 + 数据版本，展示参数不参与）。"""
    return _digest(
        {
            "kind": "prepared",
            "start": str(start),
            "end": str(end),
            "lead_days": int(lead_days),
            "boards": _canonical_boards(boards_json),
            "exclude_st": bool(exclude_st),
            "include_delisted": bool(include_delisted),
            "max_codes": max_codes,
            "data_version": data_version,
        }
    )


def build_config_fingerprint(config: BacktestConfig) -> str:
    """BacktestConfig 全部字段的指纹（成本/区间/择时/持仓天数变化都会换键）。"""
    return _digest({"kind": "config", "config": asdict(config)})


def build_strategy_fingerprint(strategy: Any, *, name: str | None = None) -> str:
    """策略指纹：类路径 + 名称 + 参数（自存策略重名时靠 name 区分）。"""
    params = getattr(strategy, "params", None) or {}
    return _digest(
        {
            "kind": "strategy",
            "class": f"{type(strategy).__module__}.{type(strategy).__qualname__}",
            "name": name if name is not None else getattr(strategy, "name", None),
            "params": params,
        }
    )


def _state_value(value: Any) -> Any:
    """把页面状态值转成可哈希的稳定表示。"""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return _digest(value)


def build_request_key(state: Mapping[str, Any]) -> tuple:
    """从页面状态抽取「回测口径」键：只取白名单字段，展示参数被显式忽略。"""
    return tuple(
        (key, _state_value(state.get(key)))
        for key in _BACKTEST_STATE_KEYS
        if key in state
    )


def build_result_key(
    *,
    prepared_key: str,
    strategy_fingerprint: str,
    request_key: tuple,
) -> tuple:
    """逐策略结果缓存键：prepared 数据口径 + 策略指纹 + 回测口径参数。"""
    return (prepared_key, strategy_fingerprint, tuple(request_key))


def config_to_dict(config: BacktestConfig) -> dict:
    """BacktestConfig → 可 JSON 序列化字典（日期转 ISO 字符串）。"""
    data = asdict(config)
    for key in ("start", "end", "fundamental_as_of"):
        if data.get(key) is not None:
            data[key] = data[key].isoformat()
    return data


def config_from_dict(data: Mapping[str, Any]) -> BacktestConfig:
    """字典 → BacktestConfig（与 config_to_dict 互逆）。"""
    payload = dict(data)
    for key in ("start", "end", "fundamental_as_of"):
        if payload.get(key):
            payload[key] = date.fromisoformat(str(payload[key]))
    return BacktestConfig(**payload)


def clone_prepared(prepared: Any) -> Any:
    """浅拷贝 prepared 的容器字段（R4：run_backtest 不得改写共享缓存对象）。

    只复制 dict / list 外壳与 stock_list 的 DataFrame 视图（`deep=False`，共享数据块），
    不复制 numpy 因子矩阵与 bars_map 里的 DataFrame；`_FactorTable` 引擎侧已设为只读。
    """
    if prepared is None:
        return None
    updates: dict[str, Any] = {}
    factor_tables = getattr(prepared, "factor_tables", None)
    if factor_tables is not None:
        updates["factor_tables"] = dict(factor_tables)
    bars_map = getattr(prepared, "bars_map", None)
    if bars_map is not None:
        updates["bars_map"] = dict(bars_map)
    fund_map = getattr(prepared, "fund_map", None)
    if fund_map is not None:
        updates["fund_map"] = dict(fund_map)
    stock_list = getattr(prepared, "stock_list", None)
    if isinstance(stock_list, pd.DataFrame):
        updates["stock_list"] = stock_list.copy(deep=False)
    else:
        updates["stock_list"] = stock_list
    return replace(prepared, **updates)


def prepared_maps(prepared: Any) -> tuple[dict, dict, pd.DataFrame] | None:
    """从 prepared 里取出 (bars_map, fund_map, stock_list)，供 walk_forward 等复用。"""
    if prepared is None:
        return None
    bars_map = getattr(prepared, "bars_map", None)
    fund_map = getattr(prepared, "fund_map", None)
    stock_list = getattr(prepared, "stock_list", None)
    if bars_map is None or fund_map is None or stock_list is None:
        return None
    return bars_map, fund_map, stock_list


def _prepare_prepared(
    bars_map: dict, fund_map: dict, stock_list: pd.DataFrame, config: BacktestConfig
) -> Any:
    """调用引擎准备层（惰性 import：引擎落地前也不会拖垮模块导入，测试可替换）。"""
    from stock_plan.backtest.engine import prepare_backtest

    return prepare_backtest(bars_map, fund_map, stock_list, config)


def _create_strategy(strategy_name: str, strategy_kind: str, params_json: str) -> Any:
    """按名称/类型实例化策略（内置、自存、拼装页自定义三种来源）。"""
    params = json.loads(params_json) if params_json else None
    if strategy_kind == "custom":
        from stock_plan.strategy.custom import CustomStrategy

        return CustomStrategy(json.loads(params_json) if params_json else {})
    if strategy_name in STRATEGIES:
        return create_strategy(strategy_name, params)
    return store.resolve_strategy(strategy_name, params)


def _run_backtest_with_prepared(
    strategy: Any, config: BacktestConfig, prepared: Any
) -> BacktestResult:
    """用（已浅拷贝的）prepared 跑一次回测；引擎永远以传入 config 为准。"""
    maps = prepared_maps(prepared)
    if maps is None:
        return run_backtest(
            strategy,
            config,
            {},
            {},
            pd.DataFrame(columns=["code"]),
            prepared=prepared,
        )
    bars_map, fund_map, stock_list = maps
    return run_backtest(
        strategy,
        config,
        bars_map,
        fund_map,
        stock_list,
        prepared=prepared,
    )


@st.cache_resource(
    show_spinner="正在加载行情并预计算因子表…",
    max_entries=PREPARED_MAX_ENTRIES,
)
def load_shared_prepared(
    start: str,
    end: str,
    lead_days: int,
    boards_json: str,
    exclude_st: bool,
    include_delisted: bool,
    max_codes: int | None,
    data_version: str,
):
    """加载行情并构建跨策略共享的 prepared（进程级缓存，不 pickle）。

    参数全部是标量字符串/布尔/整数，便于 Streamlit 生成缓存键；`data_version`
    只参与缓存键、不参与计算：本地数据一旦更新（mtime/大小变化），旧 prepared 自动失效。

    返回：
        (prepared, stock_list, fund_map)；无行情数据时 prepared 为 None。
    """
    del data_version  # 仅参与缓存键；函数体内不需要，显式说明避免误用
    boards = json.loads(boards_json) if boards_json else None
    storage = Storage()
    stock_list, bars_map, fund_map = storage.load_market_maps(
        start=start,
        end=end,
        lead_days=lead_days,
        boards=boards,
        exclude_st=exclude_st,
        max_codes=max_codes,
        include_delisted=include_delisted,
    )
    if stock_list is None or stock_list.empty or not bars_map:
        return None, stock_list, fund_map
    base_config = BacktestConfig(
        start=date.fromisoformat(start),
        end=date.fromisoformat(end),
        fundamental_as_of=storage.fundamentals_snapshot_date(),
    )
    prepared = _prepare_prepared(bars_map, fund_map, stock_list, base_config)
    return prepared, stock_list, fund_map


@st.cache_data(
    ttl=RESULT_TTL_SECONDS,
    show_spinner=False,
    max_entries=RESULT_MAX_ENTRIES,
)
def run_strategy_cached(
    start: str,
    end: str,
    lead_days: int,
    boards_json: str,
    exclude_st: bool,
    include_delisted: bool,
    max_codes: int | None,
    data_version: str,
    strategy_name: str,
    strategy_kind: str = "registry",
    params_json: str = "",
    strategy_sig: str = "",
    config_json: str = "",
) -> tuple[BacktestResult | None, dict]:
    """单个策略的回测结果缓存（缓存键 = 数据口径 + 策略 + config，见模块说明）。

    参数：
        start/end/lead_days/boards_json/exclude_st/include_delisted/max_codes/
        data_version: 数据口径（与 shared prepared 一致，保证数据更新后失效）。
        strategy_name: 内置策略名 / 自存策略显示名 / 自定义策略名。
        strategy_kind: "registry"（内置+自存，默认）或 "custom"（策略拼装页）。
        params_json: 策略参数覆盖（JSON 字符串，空串表示不覆盖）。
        strategy_sig: 自存策略配置指纹（配置在函数体内从 DB 读，必须进键）。
        config_json: BacktestConfig 的 JSON 形式（见 `config_to_dict`）。

    返回：
        (BacktestResult | None, metrics 字典)；无行情数据时返回 (None, {})。
    """
    del strategy_sig  # 仅参与缓存键（自存策略配置在体内读，键里必须显式包含）
    prepared, stock_list, fund_map = load_shared_prepared(
        start,
        end,
        lead_days,
        boards_json,
        exclude_st,
        include_delisted,
        max_codes,
        data_version,
    )
    if prepared is None or stock_list is None or stock_list.empty:
        return None, {}
    config = config_from_dict(json.loads(config_json))
    strategy = _create_strategy(strategy_name, strategy_kind, params_json)
    result = _run_backtest_with_prepared(strategy, config, clone_prepared(prepared))
    metrics = calc_metrics(result.equity_curve, result.trades)
    return result, metrics


def _strategy_factory(
    strategy_name: str, strategy_kind: str, params_json: str
):
    """返回 walk_forward 需要的「每次新建策略实例」工厂（保持旧语义）。"""

    def factory() -> Any:
        return _create_strategy(strategy_name, strategy_kind, params_json)

    return factory


@st.cache_data(
    ttl=RESULT_TTL_SECONDS,
    show_spinner=False,
    max_entries=WALKFORWARD_MAX_ENTRIES,
)
def run_walkforward_cached(
    start: str,
    end: str,
    lead_days: int,
    boards_json: str,
    exclude_st: bool,
    include_delisted: bool,
    max_codes: int | None,
    data_version: str,
    strategy_name: str,
    strategy_kind: str = "registry",
    params_json: str = "",
    strategy_sig: str = "",
    train_days: int = 180,
    test_days: int = 90,
):
    """Walk-Forward 结果缓存：复用 shared prepared，窗口结果按参数缓存。

    与 `run_strategy_cached` 同一套数据口径与 prepared 缓存；Phase 15b 的
    `walk_forward(..., prepared=...)` 一旦落地会自动改用 prepared（按签名探测，
    不强行依赖，避免与引擎改动互相阻塞）。
    """
    del strategy_sig  # 仅参与缓存键（自存策略配置在体内读，键里必须显式包含）
    prepared, stock_list, fund_map = load_shared_prepared(
        start,
        end,
        lead_days,
        boards_json,
        exclude_st,
        include_delisted,
        max_codes,
        data_version,
    )
    if prepared is None or stock_list is None or stock_list.empty:
        return None
    maps = prepared_maps(prepared)
    if maps is None:
        return None
    bars_map, fund_map_used, stock_list_used = maps
    kwargs: dict[str, Any] = {"train_days": train_days, "test_days": test_days}
    try:
        supports_prepared = "prepared" in inspect.signature(walk_forward).parameters
    except (TypeError, ValueError):  # 内置/包装函数取不到签名时按旧签名调用
        supports_prepared = False
    if supports_prepared:
        kwargs["prepared"] = prepared
    return walk_forward(
        _strategy_factory(strategy_name, strategy_kind, params_json),
        bars_map,
        fund_map_used,
        stock_list_used,
        date.fromisoformat(start),
        date.fromisoformat(end),
        **kwargs,
    )


def mark_cache_hit(state_key: tuple) -> bool:
    """记下本次口径键；返回「本会话此前是否跑过同一口径」（R6 的会话标记）。"""
    seen = st.session_state.setdefault(_HIT_STATE_KEY, set())
    previous = state_key in seen
    seen.add(state_key)
    return previous


def cache_caption(elapsed: float, *, saw_before: bool) -> str:
    """缓存状态文案：会话内跑过或耗时极短 → 本次为缓存结果。"""
    if saw_before or elapsed < CACHE_HIT_SECONDS:
        return f"本次为缓存结果（耗时 {elapsed:.2f}s）"
    return f"本次为实时计算（耗时 {elapsed:.2f}s）"


def clear_ui_caches() -> None:
    """清空本模块的 target cache 与会话命中标记（不碰其他模块/全局缓存）。"""
    load_shared_prepared.clear()
    run_strategy_cached.clear()
    run_walkforward_cached.clear()
    try:
        st.session_state.pop(_HIT_STATE_KEY, None)
    except Exception:  # noqa: BLE001 — 裸跑（无 Streamlit 运行时）时忽略
        pass


def render_clear_cache_button(key_prefix: str) -> None:
    """页面顶部的「清除缓存」按钮：清本模块缓存后重跑页面。"""
    if st.button(
        "🧹 清除缓存",
        key=f"{key_prefix}_clear_cache",
        help="只清除本模块的行情/因子表缓存与策略回测结果缓存，不会删除任何本地数据。",
    ):
        clear_ui_caches()
        st.toast("缓存已清除")
        st.rerun()
