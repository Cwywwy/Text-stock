# -*- coding: utf-8 -*-
"""策略代码生成器 — 参数配置 ↔ 代码 的双向桥梁。

职责：
1. normalize_config：把 LLM 输出（可能扁平/含未知键）清洗成 CustomStrategy
   合法配置；无法识别的键原样返回（供"未支持参数反馈清单"展示）。
2. generate_annotated_code：配置 → 带中文注释的参数快照代码（备注/留档用）。
3. generate_runnable_script：配置 → 可独立运行的回测脚本（pip install -e . 后
   直接 python 运行即可复现回测）。

规则词表（RULE_SPEC / PARAM_SPEC / WEIGHT_SPEC）同时作为 LLM 的输出约束提示，
保证 LLM 优先输出系统能直接落地的结构化参数。
"""
from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# 词表：key → (中文名, 大白话说明, 默认值, 类型)
# ---------------------------------------------------------------------------
WEIGHT_SPEC: dict[str, tuple[str, str, float, type]] = {
    "trend_score": ("技术面权重", "走势强弱（均线、K线形态）在总分里占几成", 0.6, float),
    "fund_score": ("基本面权重", "公司好坏（赚钱能力、成长性）在总分里占几成", 0.4, float),
    "mom_ret": ("动量加分权重", "最近 20 日涨得越猛加分越多；0 = 不启用", 0.0, float),
}

RULE_SPEC: dict[str, tuple[str, str, object, type]] = {
    "ma20_gt_ma60": ("中期趋势向上", "20日均线在60日均线上方才保留（未配置自定义均线对时生效）", True, bool),
    "trend_ma_fast": ("趋势快线周期", "自选快线（如 5 日均线）；0 = 不启用，与慢线组成「快线>慢线」条件", 0, int),
    "trend_ma_slow": ("趋势慢线周期", "自选慢线（如 30 日均线）；要求快线 > 慢线才算趋势向上", 0, int),
    "dev_ma": ("偏离基准线周期", "用哪条均线衡量「股价是否涨太多/跌太深」（如 20）", 20, int),
    "dev_min": ("偏离下限", "股价比基准线低超过这个百分比视为趋势转弱、重罚（%）", -5, float),
    "dev_max": ("偏离上限", "股价比基准线高超过这个百分比视为追高陷阱、排除（%）", 5, float),
    "rsi_min": ("RSI 下限", "超卖下限：低于它说明跌得过头（0 = 不启用）", 0, float),
    "rsi_max": ("RSI 上限", "超买上限：高于它说明涨得过头、扣分（默认 75）", 75, float),
    "vol_ratio_max": ("量比上限", "量比（今天成交量是平时几倍）超过它视为过热、扣分", 3.0, float),
    "mom_min": ("动量下限", "近 20 日涨幅低于这个百分比直接排除（%）；-100 = 不启用", -100, float),
    "require_breakout": ("要求突破新高", "要求收盘价接近或创 20 日新高才算突破确认", False, bool),
    "liquidity_min": ("流动性下限", "近 20 日平均成交额低于它（亿元）说明太冷门，直接排除；0 = 不启用", 0.0, float),
    "vol_surge_min": ("放量阈值", "量比达到几倍算「放量异动」；0 = 不启用", 0.0, float),
    "vol_surge_bonus": ("放量加分数值", "触发放量异动时额外加多少分", 10.0, float),
    "macd_golden": ("MACD 金叉", "要求 MACD 快线在信号线上方（多头动能）；仅作过滤不满足排除", False, bool),
    "macd_above_zero": ("MACD 零轴上方", "要求 MACD 在零轴上方（中期多头市场）", False, bool),
}

PARAM_SPEC: dict[str, tuple[str, str, float, type]] = {
    "atr_k_entry": ("买入价偏移", "买入价 = 收盘价 + k×ATR（ATR=平均一天波动幅度）；0 = 当天价格直接买", 0.0, float),
    "atr_m_exit": ("止盈 ATR 倍数", "涨了 m 个「日常波动幅度」就卖出落袋", 3.5, float),
    "atr_n_stop": ("止损 ATR 倍数", "跌了 n 个「日常波动幅度」就认赔卖出", 3.5, float),
    "hold_days": ("持仓天数", "拿这么久就换股，到期无论盈亏都卖出", 30, int),
}

_MA_CHOICES = (0, 5, 7, 10, 20, 30, 60)


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def normalize_config(raw: dict) -> tuple[dict, list[str]]:
    """把 LLM 输出清洗为合法 CustomStrategy 配置。

    返回 (config, unknown_keys)：unknown_keys 是无法识别的键名列表，
    供 UI 展示"未支持参数反馈"。
    """
    if not isinstance(raw, dict):
        return {}, ["<整体输出不是 JSON 对象>"]

    # LLM 可能把键平铺在顶层，先按前缀归位
    rules_raw = {**raw.get("rules", {})}
    params_raw = {**raw.get("params", {})}
    weights_raw = {**raw.get("weights", {})}
    for k, v in raw.items():
        if k in ("name", "rules", "params", "weights", "description"):
            continue
        if k in WEIGHT_SPEC:
            weights_raw[k] = v
        elif k in RULE_SPEC:
            rules_raw[k] = v
        elif k in PARAM_SPEC:
            params_raw[k] = v
        else:
            rules_raw.setdefault(k, v)

    unknown: list[str] = []
    config: dict = {"name": str(raw.get("name") or "LLM 生成策略")[:30]}

    # ---- weights ----
    weights: dict = {}
    for key, (_cn, _desc, default, typ) in WEIGHT_SPEC.items():
        v = weights_raw.pop(key, default)
        try:
            weights[key] = _clip(float(v), 0.0, 2.0)
        except (TypeError, ValueError):
            weights[key] = default
    config["weights"] = weights

    # ---- rules ----
    rules: dict = {}
    for key, (_cn, _desc, default, typ) in RULE_SPEC.items():
        v = rules_raw.pop(key, None)
        if v is None:
            rules[key] = default
            continue
        try:
            if typ is bool:
                rules[key] = bool(v) if not isinstance(v, str) else str(v).lower() in ("1", "true", "yes", "on", "是")
            elif typ is int:
                rules[key] = int(float(v))
            else:
                rules[key] = float(v)
        except (TypeError, ValueError):
            rules[key] = default
    # 均线周期合法性
    if rules["trend_ma_fast"] not in _MA_CHOICES:
        rules["trend_ma_fast"] = 0
    if rules["trend_ma_slow"] not in _MA_CHOICES:
        rules["trend_ma_slow"] = 0
    if rules["dev_ma"] not in _MA_CHOICES or rules["dev_ma"] == 0:
        rules["dev_ma"] = 20
    # 快慢线必须成对出现
    if bool(rules["trend_ma_fast"]) != bool(rules["trend_ma_slow"]):
        rules["trend_ma_fast"] = rules["trend_ma_slow"] = 0
    # 数值区间钳制
    rules["rsi_min"] = _clip(float(rules["rsi_min"]), 0, 100)
    rules["rsi_max"] = _clip(float(rules["rsi_max"]), 0, 100)
    if rules["rsi_min"] > rules["rsi_max"]:
        rules["rsi_min"], rules["rsi_max"] = rules["rsi_max"], rules["rsi_min"]
    rules["dev_min"] = _clip(float(rules["dev_min"]), -50, 0)
    rules["dev_max"] = _clip(float(rules["dev_max"]), 0, 50)
    rules["vol_ratio_max"] = _clip(float(rules["vol_ratio_max"]), 1.0, 20.0)
    rules["mom_min"] = _clip(float(rules["mom_min"]), -100, 100)
    rules["liquidity_min"] = _clip(float(rules["liquidity_min"]), 0, 1000)
    rules["vol_surge_min"] = _clip(float(rules["vol_surge_min"]), 0, 20)
    rules["vol_surge_bonus"] = _clip(float(rules["vol_surge_bonus"]), 0, 100)
    unknown.extend(rules_raw.keys())
    config["rules"] = rules

    # ---- params ----
    params: dict = {}
    for key, (_cn, _desc, default, typ) in PARAM_SPEC.items():
        v = params_raw.pop(key, default)
        try:
            params[key] = float(v) if typ is float else int(float(v))
        except (TypeError, ValueError):
            params[key] = default
    params["atr_k_entry"] = _clip(params["atr_k_entry"], -5.0, 10.0)
    params["atr_m_exit"] = _clip(params["atr_m_exit"], 0.5, 20.0)
    params["atr_n_stop"] = _clip(params["atr_n_stop"], 0.5, 20.0)
    params["hold_days"] = int(_clip(params["hold_days"], 1, 250))
    unknown.extend(params_raw.keys())
    config["params"] = params

    return config, sorted(set(unknown))


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return f"{v:g}"
    return repr(v)


def generate_annotated_code(config: dict) -> str:
    """配置 → 带中文参数注释的代码快照（备注/留档用，可粘回策略拼装）。"""
    config = config or {}
    lines: list[str] = []
    lines.append('# -*- coding: utf-8 -*-')
    lines.append(f'# ============================================================')
    lines.append(f'# 策略名称：{config.get("name", "未命名策略")}')
    lines.append(f'# 导出时间：{date.today().isoformat()}')
    lines.append(f'# 用途：参数快照备注。可在「策略拼装」页按此逐项还原，')
    lines.append(f'#       或配合 stock_plan 库以 CustomStrategy(config) 加载。')
    lines.append(f'# ============================================================')
    lines.append('CONFIG = {')
    lines.append(f'    "name": {config.get("name", "未命名策略")!r},')
    lines.append('    "weights": {')
    for key, (cn, desc, _d, _t) in WEIGHT_SPEC.items():
        v = config.get("weights", {}).get(key, _d)
        lines.append(f'        {key!r}: {_fmt(v)},  # {cn}：{desc}')
    lines.append('    },')
    lines.append('    "rules": {')
    for key, (cn, desc, _d, _t) in RULE_SPEC.items():
        v = config.get("rules", {}).get(key, _d)
        lines.append(f'        {key!r}: {_fmt(v)},  # {cn}：{desc}')
    lines.append('    },')
    lines.append('    "params": {')
    for key, (cn, desc, _d, _t) in PARAM_SPEC.items():
        v = config.get("params", {}).get(key, _d)
        lines.append(f'        {key!r}: {_fmt(v)},  # {cn}：{desc}')
    lines.append('    },')
    lines.append('}')
    return "\n".join(lines)


def generate_runnable_script(config: dict) -> str:
    """配置 → 可独立运行的回测脚本。"""
    config = config or {}
    p = config.get("params", {})
    body = generate_annotated_code(config)
    script = f'''{body}

# ============================================================
# 可独立运行：先安装本库（pip install -e .），然后：
#     python 该文件.py
# 依赖 data/ 目录中已缓存的历史数据（系统内"一键拉取"产生）。
# ============================================================
from datetime import date, timedelta

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.data.storage import Storage
from stock_plan.strategy.custom import CustomStrategy


def main() -> None:
    strategy = CustomStrategy(dict(CONFIG))
    storage = Storage()
    stock_list = storage.load_stock_list()
    codes = stock_list["code"].astype(str).tolist()
    bars_map = {{c: storage.load_bars(c) for c in codes if storage.cache_exists(c)}}
    fund_map = {{c: (storage.load_fundamentals(c) or {{}}) for c in bars_map}}
    end = date.today()
    cfg = BacktestConfig(
        start=end - timedelta(days=365),
        end=end,
        initial_cash=100_000.0,
        rebalance_freq="weekly",
        market_timing=True,
        max_hold_days={int(p.get("hold_days", 30))},
    )
    result = run_backtest(strategy, cfg, bars_map, fund_map, stock_list)
    metrics = calc_metrics(result.equity_curve, result.trades)
    print(f"策略：{{CONFIG['name']}}")
    for k in ("total_return", "annual_return", "max_drawdown", "sharpe", "win_rate", "trade_count"):
        print(f"  {{k}}: {{metrics.get(k, 0)}}")


if __name__ == "__main__":
    main()
'''
    return script
