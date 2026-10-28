"""自定义策略 — 由可视化策略拼装器（Visual Builder）动态生成。

用户通过 UI 选择因子、权重、阈值，组合成一个可回测的自定义策略。
配置结构（dict）：
{
    "name": "我的策略",
    "weights": {"trend_score": 0.5, "fund_score": 0.3, "rsi14": 0.2},
    "rules": {
        "ma20_gt_ma60": true,          # 中期趋势向上（旧版规则，trend_ma_* 未配置时生效）
        "trend_ma_fast": 0,            # 趋势条件快线周期（如 5；0=未配置）
        "trend_ma_slow": 0,            # 趋势条件慢线周期（如 7；条件为 快线>慢线）
        "dev_ma": 20,                  # 偏离基准均线周期（如仅看 ma20 偏离）
        "rsi_min": 0, "rsi_max": 75,   # RSI 区间
        "dev_min": -5, "dev_max": 5,   # 偏离基准均线区间（%）
        "vol_ratio_max": 3.0,          # 量比上限
        "mom_min": -100,               # 近20日动量下限（%，低于排除；-100 不启用）
        "require_breakout": false,     # 要求接近/创20日新高（high20_ratio >= -2%）
        # ---- 量价配置（R1 需求）----
        "liquidity_min": 0.5,          # 流动性下限：近20日平均成交额（亿元），低于排除；0=不启用
        "vol_surge_min": 0.0,          # 放量异动阈值：量比达到该值视为放量；0=不启用
        "vol_surge_bonus": 10.0,       # 放量异动加分数值（vol_ratio >= vol_surge_min 时加分）
        "macd_golden": False,          # MACD 金叉：快线在信号线上方（不满足排除）
        "macd_above_zero": False,      # MACD 零轴上方（中期多头市场）
    },
    "params": {"atr_k_entry": 0.0, "atr_m_exit": 3.5, "atr_n_stop": 3.5, "hold_days": 30},
    # ---- 两段式「先选类型 → LLM 发挥」新增字段（Phase 11）----
    "archetype": "trend",              # 策略类型 key（见 strategy/archetypes.py；缺省表示旧式无类型配置）
    "degraded": [],                    # 缺字段导致的规则降级记录，报告里逐条展示
    "disabled_rules": [],              # 被类型层禁用的规则名（不再参与打分）
    "mv_pct": 0.0,                     # 小市值分位：只在总市值最小的该比例里选股；0 = 不启用
}

修订记录：
2026-10-29  (phase 11) 规则缺列不再静默跳过：置哨兵+degraded；新增 mv_pct 小市值分位规则
"""
from __future__ import annotations

import pandas as pd

from stock_plan.factors.filter import filter_universe
from stock_plan.strategy.archetypes import degraded_record, rule_columns
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.builtin import build_factor_rows
from stock_plan.strategy.scorer import composite_score, fundamental_composite

# 可选因子及其方向（higher_better / lower_better）
FACTOR_OPTIONS = {
    "trend_score": "趋势分（越高越好）",
    "fund_score": "基本面分（越高越好）",
    "rsi14": "RSI14（中性区间最佳）",
    "vol_ratio": "量比（越低越好）",
    "mom_ret": "动量（越高越好）",
}


class CustomStrategy(Strategy):
    """可视化拼装的自定义策略。"""

    name = "自定义策略"

    def __init__(self, config: dict):
        self.config = config
        self.name = config.get("name", "自定义策略")
        self.params = {
            "atr_k_entry": config.get("params", {}).get("atr_k_entry", 0.0),
            "atr_m_exit": config.get("params", {}).get("atr_m_exit", 3.5),
            "atr_n_stop": config.get("params", {}).get("atr_n_stop", 3.5),
            "hold_days": config.get("params", {}).get("hold_days", 30),
        }
        # 类型与降级状态：类型层已判定的降级记录原样带进来，运行期再补「当时缺列」的降级
        self.archetype = str(config.get("archetype") or "")
        self._disabled_rules = set(config.get("disabled_rules") or ())
        self.degraded_records: list[dict] = [
            dict(item) for item in (config.get("degraded") or []) if isinstance(item, dict)
        ]
        self._degraded_rules = {str(item.get("rule", "")) for item in self.degraded_records}

    # ---- 降级：缺列不再静默跳过，而是禁用该规则并在报告里留痕 ----
    def _mark_missing(self, rule: str, missing: tuple[str, ...]) -> None:
        """记录一次缺字段降级（同一规则只记一次，避免逐行刷屏）。"""
        if rule in self._degraded_rules:
            return
        self._degraded_rules.add(rule)
        self.degraded_records.append(degraded_record(rule, missing))

    def _rule_enabled(self, rule: str, columns: set[str]) -> bool:
        """规则当前是否可算：类型层禁用 → False；运行期缺依赖列 → 记 degraded 并 False。"""
        if rule in self._disabled_rules:
            return False
        rules = self.config.get("rules", {})
        missing = tuple(col for col in rule_columns(rule, rules) if col and col not in columns)
        if missing:
            self._mark_missing(rule, missing)
            return False
        return True

    def degraded_messages(self) -> tuple[str, ...]:
        """给报告用的降级说明（中文，一条规则一行）。"""
        return tuple(
            str(item.get("message", "")).strip()
            for item in self.degraded_records
            if str(item.get("message", "")).strip()
        )

    def filter_universe(
        self, stock_list: pd.DataFrame, bars_map: dict[str, pd.DataFrame]
    ) -> list[str]:
        return filter_universe(stock_list, bars_map)

    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        weights = self.config.get("weights", {"trend_score": 0.6, "fund_score": 0.4})
        w_tech = weights.get("trend_score", 0.6)
        w_fund = weights.get("fund_score", 0.4)
        score = composite_score(
            df_factors["trend_score"],
            df_factors["fund_score"],
            weights=(w_tech, w_fund, 0.0),
        )

        rules = self.config.get("rules", {})
        columns = set(df_factors.columns)
        # 趋势条件：优先用自定义均线对（快线>慢线，周期可任选 5/7/10/20/30/60），
        # 未配置时回退旧版 ma20 > ma60
        fast = int(rules.get("trend_ma_fast", 0) or 0)
        slow = int(rules.get("trend_ma_slow", 0) or 0)
        if (
            fast
            and slow
            and self._rule_enabled("trend_ma_fast", columns)
            and self._rule_enabled("trend_ma_slow", columns)
        ):
            score -= (~(df_factors[f"ma{fast}"] > df_factors[f"ma{slow}"])) * 100
        elif rules.get("ma20_gt_ma60", True) and self._rule_enabled("ma20_gt_ma60", columns):
            score -= (~(df_factors["ma20"] > df_factors["ma60"])) * 100
        # 偏离区间：基准均线可选（dev_ma，如仅看 ma20 偏离或 ma10 偏离）
        dev_ma = int(rules.get("dev_ma", 20) or 0)
        if dev_ma and self._rule_enabled("dev_ma", columns) and "close" in columns:
            dev = (df_factors["close"] / df_factors[f"ma{dev_ma}"] - 1) * 100
            score -= (dev > rules.get("dev_max", 5)) * 100
            score -= (dev < rules.get("dev_min", -5)) * 50
        # RSI 区间
        if self._rule_enabled("rsi_max", columns):
            score -= (df_factors["rsi14"] > rules.get("rsi_max", 75)) * 20
            score -= (df_factors["rsi14"] < rules.get("rsi_min", 0)) * 20
        # 量比上限
        if self._rule_enabled("vol_ratio_max", columns):
            score -= (df_factors["vol_ratio"] > rules.get("vol_ratio_max", 3.0)) * 30
        # 动量下限（低于排除）
        mom_min = rules.get("mom_min", -100)
        if mom_min > -100 and self._rule_enabled("mom_min", columns):
            score -= (df_factors["mom_ret"] * 100 < mom_min) * 100
        # 要求接近/创 20 日新高（突破确认）
        if rules.get("require_breakout", False) and self._rule_enabled("require_breakout", columns):
            score -= (df_factors["high20_ratio"] < -0.02) * 100
        # ---- 量价配置（R1 需求）----
        # 流动性下限：近 20 日平均成交额（亿元），低于排除（0 = 不启用）
        liquidity_min = float(rules.get("liquidity_min", 0) or 0)
        if liquidity_min > 0 and self._rule_enabled("liquidity_min", columns):
            score -= (df_factors["avg_amount20"] < liquidity_min * 1e8) * 100
        # 放量异动加分：量比达到阈值时加分（0 = 不启用）
        vol_surge_min = float(rules.get("vol_surge_min", 0) or 0)
        vol_surge_bonus = float(rules.get("vol_surge_bonus", 10) or 10)
        if vol_surge_min > 0 and self._rule_enabled("vol_surge_min", columns):
            score += (df_factors["vol_ratio"] >= vol_surge_min) * vol_surge_bonus
        # ---- 小市值分位（Phase 11 新增；需要 total_mv，见任务 E）----
        # 总市值最小的一批才保留：rank(pct) 越小市值越小，超过 mv_pct 分位的直接排除。
        mv_pct = float(rules.get("mv_pct", 0) or 0)
        if mv_pct > 0 and self._rule_enabled("mv_pct", columns):
            total_mv = pd.to_numeric(df_factors["total_mv"], errors="coerce")
            if total_mv.notna().any():
                rank = total_mv.rank(pct=True, method="average")
                score -= ((rank > mv_pct) & total_mv.notna()) * 100
            else:
                self._mark_missing("mv_pct", ("total_mv",))
        # ---- MACD 条件（V4 补充需求）----
        if rules.get("macd_golden", False) and self._rule_enabled("macd_golden", columns):
            score -= (~(df_factors["macd"] > df_factors["macd_signal"])) * 100
        if rules.get("macd_above_zero", False) and self._rule_enabled("macd_above_zero", columns):
            score -= (df_factors["macd"] <= 0) * 100
        # 动量加分
        if "mom_ret" in columns:
            w_mom = weights.get("mom_ret", 0.0)
            if w_mom > 0:
                score += df_factors["mom_ret"].rank(pct=True) * w_mom * 100

        return score

    def entry_price(self, row: pd.Series, atr: float) -> float:
        k = self.params["atr_k_entry"]
        return round(row["close"] + k * atr, 2)

    def exit_price(self, entry: float, atr: float) -> tuple[float, float, int]:
        m = self.params["atr_m_exit"]
        n = self.params["atr_n_stop"]
        exit_price = round(entry + m * atr, 2)
        stop_loss = round(entry - n * atr, 2)
        return exit_price, stop_loss, self.params["hold_days"]


def build_custom_factor_rows(
    codes: list[str], bars_map: dict[str, pd.DataFrame], fund_map: dict[str, dict]
) -> pd.DataFrame:
    """（已冗余，保留仅为兼容）为自定义策略构建因子行。

    2026-10-15：`build_factor_rows` 已经补齐了 `mom_ret` 与 `ma5/7/10/30`
    （见该函数里的说明），因此本函数现在只是转发。原先它是唯一补齐这些列的地方，
    但**从未被调用**（`visual_builder.py` 只导入过名字，未使用；该无用导入已删除），
    结果自定义策略的动量/偏离规则在实盘被静默跳过。

    保留函数本身是为了不破坏可能的外部引用；新代码请直接用 `build_factor_rows`。
    """
    return build_factor_rows(codes, bars_map, fund_map)