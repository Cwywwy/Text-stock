# -*- coding: utf-8 -*-
"""RRG 行业轮动批量口径复筛（Phase 20）。

用途
----
Phase 20 的复筛矩阵里，RRG 不能走 `scripts/measure_factor_ab.py`（那条链路是
「个股打分 → top_n 选股」，而 RRG 是「行业轮动 → 按行业等权持有」）。本脚本把
行业指数 / RRG 只算一次，然后在 W1/W2/W3 三个窗口 × off/dual_ma 两个择时口径上
各跑一次轮动回测，汇总成一个 JSON，交给 `scripts/judge_rescreen.py` 做择时层判定。

作者：Cwywwy
所属模块：scripts（离线测量脚本，不参与运行时）

修订记录：
- 2026-10-29 (phase 20) 新建：把 RRG 的 6 个 arm（3 窗口 × 2 择时口径）汇总成一份 JSON。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_plan.backtest.metrics import calc_annual_metrics, calc_metrics
from stock_plan.backtest.rotation import run_industry_rotation
from stock_plan.data.classification import industry_table, latest_month
from stock_plan.data.storage import Storage
from stock_plan.factors.rrg import (
    EXCLUDED_CODE_PREFIXES,
    build_equal_weight_benchmark,
    build_industry_index,
    compute_rrg,
    industry_index_wide,
    rrg_ready,
)

ROOT = Path(__file__).resolve().parents[1]

# 三个窗口与计划书 §4.1 一致；W2 只用于最终判定，不做二次调参
WINDOWS: dict[str, tuple[str, str]] = {
    "W1训练": ("2022-03-07", "2025-06-30"),
    "W2验证": ("2025-07-01", "2026-10-28"),
    "W3全期": ("2022-03-07", "2026-10-28"),
}
TIMINGS: tuple[str, ...] = ("off", "dual_ma")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RRG 行业轮动批量口径复筛")
    parser.add_argument("--out", default="", help="汇总 JSON 输出路径")
    parser.add_argument("--top-n", type=int, default=6, help="每次调仓持有行业数（默认 6）")
    parser.add_argument("--rebalance-days", type=int, default=20, help="调仓间隔交易日（默认 20）")
    parser.add_argument("--cost-bps", type=float, default=10.0, help="单边成本（基点，默认 10）")
    parser.add_argument("--max-codes", type=int, default=0, help="最多加载股票数（0 = 全市场）")
    parser.add_argument("--timings", default="off,dual_ma", help="逗号分隔的择时口径")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    t0 = time.time()
    stamp = datetime.now().strftime("%Y-%m-%d-%H-%M")
    out_path = Path(args.out) if args.out else ROOT / f"tests/reports/{stamp}.rescreen-rrg.json"
    timings = tuple(item.strip() for item in str(args.timings).split(",") if item.strip())

    print("[1/3] 加载日线 ...", flush=True)
    storage = Storage()
    _, bars_map, _ = storage.load_market_maps(
        start=None, end=max(end for _, end in WINDOWS.values()), max_codes=args.max_codes or None
    )
    print(f"      股票 {len(bars_map)} 只，耗时 {time.time() - t0:.1f}s", flush=True)

    print("[2/3] 聚合行业等权指数 + RRG ...", flush=True)
    table = industry_table(latest_month())
    if table.empty:
        print("没有行业分类数据，无法复筛 RRG", flush=True)
        return 2
    mapping = table.set_index("code")["sw_l1"]
    mapping = mapping[mapping.astype(str).str.strip() != ""]
    panel = build_industry_index(bars_map, mapping, min_components=3)
    panel_wide = industry_index_wide(panel)
    benchmark = build_equal_weight_benchmark(panel_wide)
    rrg = compute_rrg(panel_wide, benchmark)
    ready = rrg_ready(rrg)
    first_ready = pd.Timestamp(ready["date"].min())
    print(f"      行业 {panel_wide.shape[1]} 个，首个成形日 {first_ready.date()}", flush=True)

    runs: list[dict] = []
    for window, (start, end) in WINDOWS.items():
        for timing in timings:
            print(f"[3/3] {window} / {timing} ...", flush=True)
            rotation = run_industry_rotation(
                panel_wide,
                rrg,
                start=start,
                end=end,
                rebalance_days=args.rebalance_days,
                top_n=args.top_n,
                cost_bps=args.cost_bps,
                timing_tier=timing,
            )
            metrics = calc_metrics(rotation.equity_curve, rotation.trades)
            annual = calc_annual_metrics(rotation.equity_curve, rotation.trades)
            bench_metrics = calc_metrics(rotation.benchmark, pd.DataFrame())
            annual_bench = calc_annual_metrics(rotation.benchmark, pd.DataFrame())
            years = max((rotation.end - rotation.start).days / 365.0, 1 / 365.0)
            implied = (
                rotation.total_cost / (2.0 * args.cost_bps / 10000.0) if args.cost_bps > 0 else float("nan")
            )
            pick_counts = (
                rotation.holdings.groupby("date")["industry"].nunique().astype(int)
                if not rotation.holdings.empty
                else pd.Series(dtype=int)
            )
            runs.append(
                {
                    "label": f"RRG-C1/{window}/{timing}",
                    "family": "RRG-C1",
                    "window": window,
                    "timing_tier": timing,
                    "start": str(rotation.start),
                    "end": str(rotation.end),
                    "metrics": metrics,
                    "annual": annual,
                    "benchmark_return": round(
                        float(rotation.benchmark.iloc[-1] / rotation.benchmark.iloc[0] - 1.0) * 100.0, 2
                    ),
                    "bench_metrics": bench_metrics,
                    "annual_bench": annual_bench,
                    "turnover_annual": round(implied / years, 2),
                    "total_cost": round(rotation.total_cost, 6),
                    "rebalances": int(rotation.rebalances),
                    "holdings_stats": {
                        "avg_picks": round(float(pick_counts.mean()), 2) if len(pick_counts) else 0.0,
                        "min_picks": int(pick_counts.min()) if len(pick_counts) else 0,
                        "max_picks": int(pick_counts.max()) if len(pick_counts) else 0,
                        "empty_rebalances": int((pick_counts == 0).sum()) if len(pick_counts) else 0,
                    },
                }
            )

    payload = {
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "start_load": None,
            "codes": len(bars_map),
            "industries": int(panel_wide.shape[1]),
            "excluded_prefix": list(EXCLUDED_CODE_PREFIXES),
            "first_ready": str(first_ready.date()),
            "top_n": args.top_n,
            "rebalance_days": args.rebalance_days,
            "cost_bps": args.cost_bps,
            "timings": list(timings),
            "elapsed_sec": round(time.time() - t0, 1),
        },
        "runs": runs,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[写出] {out_path}（{len(runs)} 个 arm，耗时 {time.time() - t0:.1f}s）", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
