# -*- coding: utf-8 -*-
"""策略类型画像（Archetype）— 两段式的第一段「先选类型」。

为什么要这一层
--------------
调研文档 §6 的结论：让 LLM 直接写「一整套参数」，它经常写出系统里根本取不到的字段，
然后被 `custom.py` 的 `if "列" in df.columns` 守卫**静默跳过**——用户看到的是「策略跑完了」，
实际上少算了好几条规则。两段式的第一段就是先限定类型的**可用字段白名单**，第二段 LLM
只能在白名单内发挥；越界直接拒绝并给中文原因，缺字段则该条规则禁用并打 `degraded` 标记。

三层默认值（优先级从低到高）
--------------------------
1. 全局默认值 `GLOBAL_DEFAULTS`（跨类型一致的买卖参数口径）；
2. 类型默认值 `Archetype.defaults`；
3. 用户/LLM 显式给的值（只有落在白名单内才生效）。

缺字段处理
----------
- `Archetype.requires`：该类型赖以成立的核心字段；缺了 → 类型不可用（`degraded` 说明原因）；
- `RULE_REQUIRES`：每条规则的依赖列；缺列 → 该条规则禁用（置哨兵值）+ `degraded` 记录，
  **不再静默跳过**。

修订记录：
2026-10-29  (phase 11) 新建：6 类型画像、字段白名单、三层默认值与缺字段降级
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 全局默认值：所有类型共享的兜底口径（调研 §6.3 第二层）
# ---------------------------------------------------------------------------
GLOBAL_DEFAULTS: dict[str, dict[str, Any]] = {
    "weights": {"trend_score": 0.6, "fund_score": 0.4, "mom_ret": 0.0},
    "rules": {},
    # 买卖参数：调研 §6.3 给出的全局层是 atr_m_exit=3.0 / atr_n_stop=3.0 / top_n=5。
    # top_n 属于引擎 `BacktestConfig`（默认 5），不在自定义策略配置里，故这里只放前三项中的 ATR。
    "params": {"atr_k_entry": 0.0, "atr_m_exit": 3.0, "atr_n_stop": 3.0, "hold_days": 30},
}

SECTION_ZH = {"weights": "权重", "rules": "规则", "params": "买卖参数"}

# 规则 → 依赖的因子列。缺列即禁用该规则并记录 degraded（`dev_ma`/`trend_ma_*` 是动态取列）。
RULE_REQUIRES: dict[str, tuple[str, ...]] = {
    "ma20_gt_ma60": ("ma20", "ma60"),
    "dev_ma": ("close",),
    "dev_min": ("close",),
    "dev_max": ("close",),
    "rsi_min": ("rsi14",),
    "rsi_max": ("rsi14",),
    "vol_ratio_max": ("vol_ratio",),
    "mom_min": ("mom_ret",),
    "require_breakout": ("high20_ratio",),
    "liquidity_min": ("avg_amount20",),
    "vol_surge_min": ("vol_ratio",),
    "vol_surge_bonus": ("vol_ratio",),
    "macd_golden": ("macd", "macd_signal"),
    "macd_above_zero": ("macd",),
    "mv_pct": ("total_mv",),
}

# 禁用某条规则时写入的哨兵值（custom.py 认得这些哨兵，等于「不启用」）
DISABLED_RULE_VALUES: dict[str, Any] = {
    "ma20_gt_ma60": False,
    "trend_ma_fast": 0,
    "trend_ma_slow": 0,
    "dev_min": -50.0,
    "dev_max": 50.0,
    "rsi_min": 0.0,
    "rsi_max": 100.0,
    "vol_ratio_max": 20.0,
    "mom_min": -100.0,
    "require_breakout": False,
    "liquidity_min": 0.0,
    "vol_surge_min": 0.0,
    "vol_surge_bonus": 10.0,
    "macd_golden": False,
    "macd_above_zero": False,
    "mv_pct": 0.0,
}

RULE_LABELS: dict[str, str] = {
    "ma20_gt_ma60": "中期趋势向上",
    "trend_ma_fast": "趋势快线周期",
    "trend_ma_slow": "趋势慢线周期",
    "dev_ma": "偏离基准线周期",
    "dev_min": "偏离下限",
    "dev_max": "偏离上限",
    "rsi_min": "RSI 下限",
    "rsi_max": "RSI 上限",
    "vol_ratio_max": "量比上限",
    "mom_min": "动量下限",
    "require_breakout": "要求突破新高",
    "liquidity_min": "流动性下限",
    "vol_surge_min": "放量阈值",
    "vol_surge_bonus": "放量加分数值",
    "macd_golden": "MACD 金叉",
    "macd_above_zero": "MACD 零轴上方",
    "mv_pct": "小市值分位",
}


@dataclass(frozen=True)
class Archetype:
    """一种策略类型的完整画像（白名单 + 默认值 + 骨架说明）。"""

    key: str
    label: str
    tagline: str
    requires: tuple[str, ...]
    allowed_weights: tuple[str, ...]
    allowed_rules: tuple[str, ...]
    allowed_params: tuple[str, ...]
    defaults: dict[str, dict[str, Any]] = field(default_factory=dict)
    skeleton: str = ""
    status: str = "ready"  # ready | pending_data
    note: str = ""

    @property
    def is_ready(self) -> bool:
        """该类型当前能否用于回测（事件驱动类因数据不足为 False）。"""
        return self.status == "ready"

    def allows(self, section: str, key: str) -> bool:
        """字段是否在该类型的白名单内。"""
        allowed = {
            "weights": self.allowed_weights,
            "rules": self.allowed_rules,
            "params": self.allowed_params,
        }.get(section, ())
        return key in allowed


_PARAMS_ALL = ("atr_k_entry", "atr_m_exit", "atr_n_stop", "hold_days")

ARCHETYPES: dict[str, Archetype] = {
    "trend": Archetype(
        key="trend",
        label="趋势跟随",
        tagline="均线多头 + 趋势强度打分：只做中期趋势向上的股票。",
        requires=("close", "ma20", "ma60"),
        allowed_weights=("trend_score", "fund_score"),
        allowed_rules=(
            "ma20_gt_ma60",
            "trend_ma_fast",
            "trend_ma_slow",
            "dev_ma",
            "rsi_max",
            "vol_ratio_max",
            "require_breakout",
            "macd_golden",
            "macd_above_zero",
        ),
        allowed_params=_PARAMS_ALL,
        defaults={
            "weights": {"trend_score": 0.6, "fund_score": 0.4},
            "rules": {"ma20_gt_ma60": True, "trend_ma_fast": 10, "trend_ma_slow": 30, "rsi_max": 75.0},
            "params": {"atr_m_exit": 3.5, "atr_n_stop": 3.5, "hold_days": 30},
        },
        skeleton="hard: ma20 > ma60；score = 0.6×trend_score + 0.4×fund_score − 扣分",
    ),
    "momentum": Archetype(
        key="momentum",
        label="动量轮动",
        tagline="买近期最强的一批，按动量强度排序并定期换股。",
        requires=("close", "mom_ret"),
        allowed_weights=("trend_score", "fund_score", "mom_ret"),
        allowed_rules=(
            "mom_min",
            "ma20_gt_ma60",
            "trend_ma_fast",
            "trend_ma_slow",
            "rsi_max",
            "vol_ratio_max",
        ),
        allowed_params=_PARAMS_ALL,
        defaults={
            "weights": {"trend_score": 0.5, "fund_score": 0.2, "mom_ret": 0.3},
            "rules": {"mom_min": 0.0, "vol_ratio_max": 3.5},
            "params": {"atr_m_exit": 4.0, "atr_n_stop": 2.5, "hold_days": 20},
        },
        skeleton="score = 0.5×trend + 0.2×fund + 0.3×动量分位；mom_min 过滤下跌股",
    ),
    "mean_reversion": Archetype(
        key="mean_reversion",
        label="均值回归",
        tagline="股价偏离均线过远、RSI 超卖时买入，等回归。",
        requires=("close", "ma20", "rsi14"),
        allowed_weights=("trend_score", "fund_score"),
        allowed_rules=("dev_ma", "dev_min", "dev_max", "rsi_min", "rsi_max", "vol_ratio_max"),
        allowed_params=_PARAMS_ALL,
        defaults={
            "weights": {"trend_score": 0.4, "fund_score": 0.6},
            "rules": {"dev_ma": 20, "dev_min": -10.0, "dev_max": 3.0, "rsi_min": 20.0, "rsi_max": 60.0},
            "params": {"atr_m_exit": 2.5, "atr_n_stop": 2.0, "hold_days": 15},
        },
        skeleton="只在 dev 区间内选股；score = 0.4×trend + 0.6×fund − 偏离/RSI 扣分",
    ),
    "multi_factor": Archetype(
        key="multi_factor",
        label="多因子",
        tagline="技术面 + 基本面 + 动量按权重合成，追求均衡。",
        requires=("trend_score", "fund_score"),
        allowed_weights=("trend_score", "fund_score", "mom_ret"),
        allowed_rules=(
            "ma20_gt_ma60",
            "dev_ma",
            "dev_min",
            "dev_max",
            "rsi_max",
            "vol_ratio_max",
            "liquidity_min",
            "mom_min",
        ),
        allowed_params=_PARAMS_ALL,
        defaults={
            "weights": {"trend_score": 0.4, "fund_score": 0.4, "mom_ret": 0.2},
            "rules": {"ma20_gt_ma60": True, "liquidity_min": 0.5, "vol_ratio_max": 3.0},
            "params": {"atr_m_exit": 3.5, "atr_n_stop": 3.5, "hold_days": 30},
        },
        skeleton="score = 0.4×trend + 0.4×fund + 0.2×动量分位 − 扣分",
    ),
    "small_cap": Archetype(
        key="small_cap",
        label="小市值",
        tagline="只在总市值最小的分位里选股（需要总股本字段，见任务 E）。",
        requires=("total_mv",),
        allowed_weights=("trend_score", "fund_score"),
        allowed_rules=("mv_pct", "liquidity_min", "ma20_gt_ma60", "vol_ratio_max"),
        allowed_params=_PARAMS_ALL,
        defaults={
            "weights": {"trend_score": 0.5, "fund_score": 0.5},
            "rules": {"mv_pct": 0.2, "liquidity_min": 0.5},
            "params": {"atr_m_exit": 3.0, "atr_n_stop": 3.0, "hold_days": 20},
        },
        skeleton="先按总市值升序取前 mv_pct 分位，再按打分选股；缺 total_mv 则本规则降级",
    ),
    "event": Archetype(
        key="event",
        label="事件驱动",
        tagline="涨停/打板、业绩预告等事件触发（本轮数据不足，仅占位）。",
        requires=("limitup_history", "disclosure_date"),
        allowed_weights=(),
        allowed_rules=(),
        allowed_params=(),
        defaults={},
        skeleton="事件触发买入 → 次日/数日内卖出；需要历史涨停池与真实披露日",
        status="pending_data",
        note="缺历史涨停池（`data/processed/market/` 只有 2026-10-27/28 两天）与真实财报披露日，本轮仅占位。",
    ),
}


def get_archetype(key: str | None) -> Archetype:
    """按 key 取类型画像；未知 key 抛中文 ValueError（不静默回退到某个类型）。"""
    if not key:
        raise ValueError("未指定策略类型")
    arch = ARCHETYPES.get(str(key))
    if arch is None:
        allowed = "、".join(f"{item.key}={item.label}" for item in ARCHETYPES.values())
        raise ValueError(f"未知策略类型「{key}」，可选：{allowed}")
    return arch


def rule_columns(rule: str, rules: dict[str, Any]) -> tuple[str, ...]:
    """某条规则依赖的因子列（`dev_ma`/`trend_ma_*` 动态取均线列）。"""
    if rule in ("trend_ma_fast", "trend_ma_slow"):
        period = int(rules.get(rule, 0) or 0)
        return (f"ma{period}",) if period > 0 else ()
    if rule == "dev_ma":
        period = int(rules.get("dev_ma", 0) or 0)
        return (f"ma{period}",) if period > 0 else ()
    return RULE_REQUIRES.get(rule, ())


def degraded_record(rule: str, missing: tuple[str, ...], *, something: str = "") -> dict[str, str]:
    """构造一条降级记录（供报告与 UI 展示）。"""
    label = RULE_LABELS.get(rule, rule)
    detail = something or f"缺少字段 {'、'.join(missing)}"
    return {
        "rule": rule,
        "missing": "、".join(missing),
        "message": f"{detail}：规则「{label}」未计算",
    }


def resolve_config(
    archetype_key: str,
    clean_config: dict,
    *,
    provided: set[str] | None = None,
    available_columns: set[str] | None = None,
    extra_unknown: list[str] | None = None,
) -> tuple[dict, list[str], list[dict]]:
    """把已清洗的配置按类型白名单与三层默认值收敛。

    参数：
        archetype_key: 类型 key。
        clean_config: `codegen.normalize_config()` 的输出（已含全部词表键）。
        provided: 用户/LLM **显式给出**的键集合（用来区分「显式给值」与「清洗补的默认值」）。
        available_columns: 当前可用的因子列；None 表示未知（跳过列检查，运行期再降级）。
        extra_unknown: 清洗阶段已发现的未识别键（会并入 issues）。

    返回：
        (config, issues, degraded)：config 含 `archetype` / `degraded` /
        `disabled_rules`；issues 是越界字段的中文说明列表；degraded 是规则级降级记录。
    """
    arch = get_archetype(archetype_key)
    provided = set(provided or ())
    issues: list[str] = [f"未识别字段：{key}" for key in (extra_unknown or [])]
    degraded: list[dict] = []

    config: dict = {
        "name": clean_config.get("name") or "未命名策略",
        "archetype": arch.key,
        "weights": {},
        "rules": {},
        "params": {},
    }

    if not arch.is_ready:
        # 事件驱动等占位类型：全部走类型默认值，并明确告知不可用，绝不产出可回测配置。
        for section in ("weights", "rules", "params"):
            config[section] = dict(GLOBAL_DEFAULTS.get(section, {}))
            config[section].update(arch.defaults.get(section, {}))
        degraded.append(
            {
                "rule": "archetype",
                "missing": "",
                "message": f"类型「{arch.label}」数据不足（{arch.note}），本轮不可用于回测",
            }
        )
        config["degraded"] = degraded
        config["disabled_rules"] = sorted(arch.allowed_rules)
        return config, issues, degraded

    # ---- 三层默认值 + 白名单 ----
    for section in ("weights", "rules", "params"):
        clean = dict(clean_config.get(section) or {})
        merged: dict[str, Any] = {}
        merged.update(GLOBAL_DEFAULTS.get(section, {}))
        merged.update(arch.defaults.get(section, {}))
        for key, value in clean.items():
            if arch.allows(section, key):
                if key in provided or key not in merged:
                    merged[key] = value
                continue
            if key in provided:
                issues.append(
                    f"{SECTION_ZH[section]}字段「{key}」不属于「{arch.label}」的可用范围，已忽略并回落默认值"
                )
            merged.setdefault(key, value)
        config[section] = merged

    # ---- 缺字段 → 禁用规则 + degraded（不静默）----
    disabled: list[str] = []
    if available_columns is not None:
        cols = set(available_columns)
        missing_required = [name for name in arch.requires if name not in cols]
        if missing_required:
            degraded.append(
                {
                    "rule": "archetype",
                    "missing": "、".join(missing_required),
                    "message": (
                        f"缺少字段 {'、'.join(missing_required)}：类型「{arch.label}」的核心口径不完整，"
                        "相关规则已禁用"
                    ),
                }
            )
        rules = config["rules"]
        for rule in arch.allowed_rules:
            value = rules.get(rule)
            # 只处理「已启用」的规则：哨兵值本身就是未启用（dev_min/dev_max 恒启用，不做跳过）
            if value == DISABLED_RULE_VALUES.get(rule) and rule not in ("dev_min", "dev_max"):
                continue
            missing = tuple(col for col in rule_columns(rule, rules) if col not in cols)
            if missing:
                rules[rule] = DISABLED_RULE_VALUES.get(rule, value)
                disabled.append(rule)
                degraded.append(degraded_record(rule, missing))

    config["degraded"] = degraded
    config["disabled_rules"] = sorted(disabled)
    return config, issues, degraded


def strategy_degraded(strategy: Any) -> tuple[str, ...]:
    """从策略实例读取降级说明（供 `SignalReport.degraded` 使用）。"""
    messages = getattr(strategy, "degraded_messages", None)
    if callable(messages):
        try:
            return tuple(str(item) for item in messages())
        except Exception:  # noqa: BLE001 - 报告路径不能因降级信息崩掉
            return ()
    raw = getattr(strategy, "degraded", None) or ()
    return tuple(str(item) for item in raw)
