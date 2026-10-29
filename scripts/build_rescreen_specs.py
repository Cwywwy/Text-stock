"""生成 Phase 20 全市场复筛矩阵的 spec（计划 §4.1）。

矩阵规模（预注册，跑数前固定）：

| 组 | arm 数 |
| --- | ---: |
| 基线（趋势跟随 / 突破 / 动量 × 3 窗口 × 3 择时口径） | 27 |
| 收敛（5 变体 × 趋势跟随 + 突破 × 3 窗口 × 3 择时口径） | 90 |
| 动量（3 模式 × 动量策略 × 3 窗口 × 3 择时口径） | 27 |
| RRG（1 组 × 3 窗口 × 3 择时口径，行业层独立 runner） | 9 |
| **合计** | **153** |

用法：
    .\\.venv\\Scripts\\python.exe scripts\\build_rescreen_specs.py
    .\\.venv\\Scripts\\python.exe scripts\\build_rescreen_specs.py --out .github\\plans\\specs\\xxx.json

输出：
    - 股票引擎 spec（144 arm，供 `scripts/measure_factor_ab.py --spec`）
    - RRG 运行清单（9 条命令，供 `scripts/measure_rrg_industry.py` 逐条跑）

修订记录：
2026-10-29  (phase 20) 新建：按计划 §4.1 生成 153 arm 复筛矩阵
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 窗口（预注册，不寻优）
WINDOWS: dict[str, tuple[str, str]] = {
    "W1训练": ("2022-03-07", "2025-06-30"),
    "W2验证": ("2025-07-01", "2026-10-28"),
    "W3全期": ("2022-03-07", "2026-10-28"),
}

# 三种择时口径：off = 恒满仓；weak = 旧弱择时（只对新买入减半）；tier = 双均线三档
TIMINGS: dict[str, dict] = {
    "off": {"market_timing": False, "timing_tier": "off"},
    "weak": {"market_timing": True, "timing_tier": "off"},
    "tier": {"market_timing": False, "timing_tier": "dual_ma"},
}

LEAD_DAYS = 400
MAX_CODES = 0  # 0 = 全市场，不做抽样

BASELINE_STRATEGIES = ("趋势跟随策略", "突破策略", "动量策略")
CONV_STRATEGIES = ("趋势跟随策略", "突破策略")
CONV_VARIANTS = ("pcf", "vcf", "pvcf", "acf", "trcf")
CONV_WEIGHT = 0.2
MOMENTUM_STRATEGY = "动量策略"
MOMENTUM_MODES = ("ret20", "ma_ratio", "ma_ratio_blend")

RRG_ARGS = {"top_n": 6, "rebalance_days": 20, "cost_bps": 10.0}


def _arm(label: str, strategy: str, window: str, timing: str, params: dict) -> dict:
    """拼一个 arm；config 固定三口径之一，params 传策略参数。"""
    start, end = WINDOWS[window]
    return {
        "label": f"{label}/{strategy}/{window}/{timing}",
        "strategy": strategy,
        "params": params,
        "config": dict(TIMINGS[timing]),
        "start": start,
        "end": end,
        "lead_days": LEAD_DAYS,
        "max_codes": MAX_CODES,
    }


def build_stock_arms() -> list[dict]:
    """股票引擎的 144 个 arm（基线 27 + 收敛 90 + 动量 27）。"""
    arms: list[dict] = []
    for strategy in BASELINE_STRATEGIES:
        for window in WINDOWS:
            for timing in TIMINGS:
                arms.append(_arm("基线", strategy, window, timing, {}))

    for variant in CONV_VARIANTS:
        for strategy in CONV_STRATEGIES:
            for window in WINDOWS:
                for timing in TIMINGS:
                    arms.append(
                        _arm(
                            f"收敛-{variant}",
                            strategy,
                            window,
                            timing,
                            {"conv_weight": CONV_WEIGHT, "conv_variant": variant},
                        )
                    )

    for mode in MOMENTUM_MODES:
        for window in WINDOWS:
            for timing in TIMINGS:
                arms.append(
                    _arm(f"动量-{mode}", MOMENTUM_STRATEGY, window, timing, {"momentum_mode": mode})
                )
    return arms


def build_rrg_runs(spec_path: Path) -> list[dict]:
    """RRG 的 9 条独立运行（行业层 runner，不进股票引擎 spec）。"""
    runs: list[dict] = []
    for window, (start, end) in WINDOWS.items():
        for timing in TIMINGS:
            stem = f"{start}.{window}.{timing}"
            runs.append(
                {
                    "label": f"RRG-C1/{window}/{timing}",
                    "start": start,
                    "end": end,
                    "timing_tier": TIMINGS[timing]["timing_tier"],
                    "out": f"tests/reports/2026-10-29.rescreen-rrg.{window}.{timing}.json",
                    "md": f"tests/reports/2026-10-29.rescreen-rrg.{window}.{timing}.md",
                    "command": (
                        ".\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\measure_rrg_industry.py "
                        f"--start {start} --end {end} --top-n {RRG_ARGS['top_n']} "
                        f"--rebalance-days {RRG_ARGS['rebalance_days']} --cost-bps {RRG_ARGS['cost_bps']} "
                        f"--timing-tier {TIMINGS[timing]['timing_tier']} "
                        f"--out tests\\\\reports\\\\2026-10-29.rescreen-rrg.{window}.{timing}.json "
                        f"--md tests\\\\reports\\\\2026-10-29.rescreen-rrg.{window}.{timing}.md"
                    ),
                    "_stem": stem,
                }
            )
    return runs


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """命令行参数。"""
    ap = argparse.ArgumentParser(description="生成 Phase 20 复筛矩阵 spec")
    ap.add_argument(
        "--out",
        default=str(ROOT / ".github/plans/specs/2026-10-29.rescreen-matrix.spec.json"),
        help="股票引擎 spec 输出路径",
    )
    ap.add_argument(
        "--rrg-out",
        default=str(ROOT / ".github/plans/specs/2026-10-29.rescreen-rrg.runs.json"),
        help="RRG 运行清单输出路径",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """生成 spec 并打印规模摘要。"""
    args = parse_args(argv)
    arms = build_stock_arms()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(arms, ensure_ascii=False, indent=1), encoding="utf-8")

    rrg_runs = build_rrg_runs(out)
    rrg_out = Path(args.rrg_out)
    rrg_out.write_text(json.dumps(rrg_runs, ensure_ascii=False, indent=1), encoding="utf-8")

    baseline = sum(1 for a in arms if a["label"].startswith("基线/"))
    conv = sum(1 for a in arms if a["label"].startswith("收敛-"))
    mom = sum(1 for a in arms if a["label"].startswith("动量-"))
    print(f"股票引擎 arm：基线 {baseline} + 收敛 {conv} + 动量 {mom} = {len(arms)}")
    print(f"RRG 独立运行：{len(rrg_runs)} 条")
    print(f"合计 {len(arms) + len(rrg_runs)} arm（计划 §4.1 目标 153）")
    print(f"[写出] {out}")
    print(f"[写出] {rrg_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
