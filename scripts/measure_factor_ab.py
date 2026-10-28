"""A/B 测量脚手架（2026-10-29，策略刷新计划 Phase 6）。

为什么必须用子进程
------------------
`measure_engine_defect_impact.py` 已踩过坑：主进程 import 过模块后，改源码/换参数
再重跑会拿到内存里的旧代码对象，得到**假结果**。凡是"改一行再对比"的度量，都必须
拉起新进程。本脚本因此只做两件事：主进程发 spec、收 `RESULT {json}`；真正的回测
全部在子进程里跑。

用法
----
    # 7 策略 × 3 窗口基线（Phase 6，不改任何策略行为）
    .venv/Scripts/python.exe scripts/measure_factor_ab.py --baseline --jobs 3

    # 通用 A/B：spec 为 JSON 列表，每项一个 arm
    .venv/Scripts/python.exe scripts/measure_factor_ab.py --spec .github/plans/specs/phase7.json --jobs 3

    # 单跑一个 worker（调试用）
    .venv/Scripts/python.exe scripts/measure_factor_ab.py --worker --strategy 趋势跟随策略 \
        --start 2022-03-07 --end 2026-10-28 --lead-days 400 --max-codes 800

spec 每项字段
-------------
    label       必填，报告里的口径名（如 "趋势跟随/conv0.2"）
    strategy    必填，注册表里的策略名
    params      可选，策略参数覆盖（如 {"conv_weight": 0.2}）
    config      可选，BacktestConfig 字段覆盖（如 {"top_n": 3, "market_timing": true}）
    start/end   可选，缺省用 --start/--end
    lead_days   可选，缺省用 --lead-days（指标预热所需的历史长度，日历年）
    max_codes   可选，缺省用 --max-codes

样本一致性
----------
`--max-codes` 用 `codes[::step]` 等间隔抽样（storage 内部实现），顺序由股票列表决定，
**同一参数下每次抽样完全相同**。因此同一批 arm 必须传同一个 max_codes，否则不可比。
报告里必须写明样本规模（计划 §3.1 第 4 条：同策略、同区间、同配置）。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv/Scripts/python.exe"
SCRIPT = Path(__file__).resolve()
PYTHONPATH = str(ROOT / "src")


# 三个基线窗口（计划 §1 / §2.8：预热期决定区间，不能一律从 2021-10 起跑）
# full 的起点由数据决定：日线从 2021-09-06 开始，内置策略硬过滤要求 ≥60 根 K 线。
# 实测 2021-12-06 时点只有 59 根 → 候选池为空、资金曲线 0 点（7 个 arm 全部空结果）；
# 2021-12-07 是第 60 个交易日，故 full 从该日起，并加载 400 天预热历史。
WINDOWS = {
    "full": {"start": "2021-12-07", "end": "2026-10-28", "lead_days": 400},
    "win_a": {"start": "2022-03-07", "end": "2026-10-28", "lead_days": 400},
    "win_b": {"start": "2022-09-01", "end": "2026-10-28", "lead_days": 400},
}

STRATEGIES = [
    "趋势跟随策略",
    "动量策略",
    "均值回归策略",
    "突破策略",
    "价值投资策略",
    "段永平-高质量策略",
    "李录-长期确定性策略",
]


# --------------------------------------------------------------------------- worker
def worker(args: argparse.Namespace) -> int:
    """子进程入口：加载真实数据 → 回测 → 打印一行 RESULT json。"""
    sys.path.insert(0, PYTHONPATH)
    from stock_plan.backtest.engine import BacktestConfig, run_backtest
    from stock_plan.backtest.metrics import calc_annual_metrics, calc_metrics, calc_turnover
    from stock_plan.data.storage import Storage
    from stock_plan.strategy.registry import create_strategy

    t0 = time.time()
    storage = Storage()
    stock_list, bars_map, fund_map = storage.load_market_maps(
        start=args.start,
        end=args.end,
        lead_days=args.lead_days,
        max_codes=args.max_codes,
    )
    if not bars_map:
        print("ERROR 没有可用行情，检查 --start/--end/--max-codes")
        return 2

    config_kwargs = json.loads(args.config) if args.config else {}
    cfg = BacktestConfig(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        **config_kwargs,
    )
    params = json.loads(args.params) if args.params else {}
    strategy = create_strategy(args.strategy, params)
    res = run_backtest(strategy, cfg, bars_map, fund_map, stock_list)

    # 空结果必须显式失败：候选池为空时引擎会返回空资金曲线，若只写 None 进报告，
    # 会被误读成"跑完了但没收益"，让后续 A/B 对比失去参照（实测 2021-12-06 起跑即如此）。
    if res.equity_curve.empty:
        print(
            "ERROR 回测资金曲线为空：候选池为空（检查 --start 是否早于数据可用起点，"
            "前置策略要求 ≥60 根 K 线）",
            file=sys.stderr,
        )
        return 3

    metrics = calc_metrics(res.equity_curve, res.trades)
    annual = calc_annual_metrics(res.equity_curve, res.trades)
    turnover = calc_turnover(res.equity_curve, res.trades)
    benchmark_return = None
    if res.benchmark is not None and not res.benchmark.empty:
        benchmark_return = round(
            float(res.benchmark.iloc[-1] / res.benchmark.iloc[0] - 1) * 100, 2
        )

    payload = {
        "label": args.label,
        "strategy": args.strategy,
        "params": params,
        "config": config_kwargs,
        "start": args.start,
        "end": args.end,
        "lead_days": args.lead_days,
        "max_codes": args.max_codes,
        "codes": len(bars_map),
        "equity_points": int(len(res.equity_curve)),
        "metrics": metrics,
        "annual": annual,
        "turnover": turnover,
        "benchmark_return": benchmark_return,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    print("RESULT " + json.dumps(payload, ensure_ascii=False))
    return 0


# ----------------------------------------------------------------------------- main
def _run_arm(arm: dict, defaults: dict) -> dict | None:
    """跑一个 arm（子进程），返回解析后的结果；失败返回 None 并打印原因。"""
    label = arm.get("label") or arm["strategy"]
    cmd = [
        str(PY),
        str(SCRIPT),
        "--worker",
        "--strategy",
        arm["strategy"],
        "--start",
        arm.get("start") or defaults["start"],
        "--end",
        arm.get("end") or defaults["end"],
        "--lead-days",
        str(arm.get("lead_days", defaults.get("lead_days", 0))),
        "--max-codes",
        str(arm.get("max_codes", defaults.get("max_codes"))),
        "--label",
        label,
    ]
    if arm.get("params"):
        cmd += ["--params", json.dumps(arm["params"], ensure_ascii=False)]
    if arm.get("config"):
        cmd += ["--config", json.dumps(arm["config"], ensure_ascii=False)]

    t0 = time.time()
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=env, cwd=str(ROOT), timeout=7200,
    )
    if proc.returncode != 0:
        print(f"[失败] {label}（{time.time() - t0:.0f}s）\n{(proc.stderr or '')[-1200:]}")
        return None
    for line in (proc.stdout or "").splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    print(f"[失败] {label}：没有解析到 RESULT\n{(proc.stdout or '')[-600:]}")
    return None


def _write_markdown(runs: list[dict], path: Path, title: str, defaults: dict) -> None:
    lines = [f"# {title}", "", f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    lines += [
        f"- 样本：等间隔抽样 max_codes={defaults.get('max_codes')}（各 arm 同一样本）",
        "- 配置：BacktestConfig 默认（top_n=5, max_hold_days=20, daily, market_timing=False, "
        "fundamental_as_of=None, min_commission=5.0）",
        "",
        "## 总表",
        "",
        "| 口径 | 策略 | 区间 | 总收益% | 年化% | 最大回撤% | 夏普 | 单边年换手 | 交易笔数 | 胜率% | 基准收益% |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in runs:
        m = r["metrics"]
        to = r["turnover"]
        # 年化单边换手 = 买入成交额 / 平均总资产 / 年数
        days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
        years = max(days / 365.0, 1 / 365.0)
        annual_single = to.get("single_side", 0) / years
        lines.append(
            f"| {r['label']} | {r['strategy']} | {r['start']}~{r['end']} | "
            f"{m.get('total_return')} | {m.get('annual_return')} | {m.get('max_drawdown')} | "
            f"{m.get('sharpe')} | {annual_single:.2f} | {m.get('trade_count')} | "
            f"{m.get('win_rate')} | {r.get('benchmark_return')} |"
        )

    lines += ["", "## 分年度（每口径四列：收益% / 回撤% / 笔数 / 单边换手）", ""]
    for r in runs:
        lines.append(f"### {r['label']}（{r['start']}~{r['end']}）")
        lines.append("")
        lines.append("| 年份 | 收益% | 最大回撤% | 交易笔数 | 胜率% | 单边换手 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        annual = r.get("annual", {})
        turnover_annual = r.get("turnover", {}).get("annual", {})
        for year in sorted(annual):
            a = annual[year]
            t = turnover_annual.get(year, {})
            lines.append(
                f"| {year} | {a.get('total_return')} | {a.get('max_drawdown')} | "
                f"{a.get('trade_count')} | {a.get('win_rate')} | {t.get('single_side')} |"
            )
        lines.append("")
    lines.append("> 换手口径见 `backtest/metrics.py::calc_turnover`：单边换手 = 买入成交额 ÷ 期间平均总资产；"
                 "年度换手 = 当年买入成交额 ÷ 当年平均总资产。")
    lines.append("")
    lines.append("> 首年/末年可能是不完整年度，跨年持仓按 `exit_date` 归属交易。")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[写出] {path}")


def baseline_spec() -> list[dict]:
    spec = []
    for window_name, w in WINDOWS.items():
        for strategy in STRATEGIES:
            spec.append(
                {
                    "label": f"{strategy}/{window_name}",
                    "strategy": strategy,
                    "start": w["start"],
                    "end": w["end"],
                    "lead_days": w["lead_days"],
                }
            )
    return spec


def main() -> int:
    ap = argparse.ArgumentParser(description="A/B 测量脚手架（子进程 worker + RESULT json）")
    ap.add_argument("--worker", action="store_true", help="子进程模式（内部使用）")
    ap.add_argument("--strategy")
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--lead-days", type=int, default=0)
    ap.add_argument("--max-codes", type=int, default=800)
    ap.add_argument("--params", default="")
    ap.add_argument("--config", default="")
    ap.add_argument("--label", default="")

    ap.add_argument("--baseline", action="store_true", help="跑 7 策略 × 3 窗口基线")
    ap.add_argument("--spec", help="通用 spec JSON 文件路径")
    ap.add_argument("--jobs", type=int, default=3, help="并行子进程数")
    ap.add_argument("--out", help="结果 JSON 输出路径")
    ap.add_argument("--md", help="报告 Markdown 输出路径")
    ap.add_argument("--title", default="策略 A/B 测量报告")
    args = ap.parse_args()

    if args.worker:
        if not args.strategy or not args.start or not args.end:
            print("worker 需要 --strategy/--start/--end")
            return 2
        return worker(args)

    defaults = {
        "start": WINDOWS["win_a"]["start"],
        "end": WINDOWS["win_a"]["end"],
        "lead_days": 400,
        "max_codes": args.max_codes,
    }
    if args.baseline:
        spec = baseline_spec()
        title = "策略刷新 Phase 6 基线（7 策略 × 3 窗口）"
        stamp = datetime.now().strftime("%Y-%m-%d-%H-%M")
        args.out = args.out or str(ROOT / f"tests/reports/{stamp}.report-1.json")
        args.md = args.md or str(ROOT / f"tests/reports/{stamp}.report-1.md")
    elif args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8-sig"))
        title = args.title
        args.out = args.out or str(ROOT / "tests/reports/ab-latest.json")
        args.md = args.md or str(ROOT / "tests/reports/ab-latest.md")
    else:
        ap.error("需要 --baseline 或 --spec")

    print(f"共 {len(spec)} 个 arm，max_codes={defaults['max_codes']}，jobs={args.jobs}")
    results: list[dict | None] = [None] * len(spec)

    def _task(i_arm):
        i, arm = i_arm
        t0 = time.time()
        r = _run_arm(arm, defaults)
        print(f"[{i + 1}/{len(spec)}] {arm.get('label') or arm['strategy']} "
              f"{'完成' if r else '失败'}（{time.time() - t0:.0f}s）")
        return i, r

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        for i, r in ex.map(_task, list(enumerate(spec))):
            results[i] = r

    runs = [r for r in results if r]
    if not runs:
        print("全部失败，未写出结果")
        return 1

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "defaults": defaults,
        "windows": WINDOWS,
        "runs": runs,
        "failed": len(results) - len(runs),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[写出] {out}")

    md = Path(args.md)
    _write_markdown(runs, md, title, defaults)
    return 0 if len(runs) == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
