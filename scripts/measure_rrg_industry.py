"""RRG 行业轮动 C1 回测脚本（任务 C1，只做行业层证伪）。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\measure_rrg_industry.py
    .\\.venv\\Scripts\\python.exe scripts\\measure_rrg_industry.py --top-n 6 --rebalance-days 20 `
        --out tests\\reports\\xxx.json --md tests\\reports\\xxx.md

输出：
    控制台摘要 + JSON（含逐日资金曲线/持仓/年度指标）+ Markdown 报告
    （覆盖度、预热与有效区间、四指标、分年度、相对基准超额、§3.5 四条判定、前视偏差披露）。

**本脚本不做参数寻优**：默认口径就是用户拍板口径（top_n=6、20 日调仓、单边 10bp），
只跑一次，避免「多试几组参数挑最好看」的过拟合。

修订记录：
2026-10-29  (phase 9) 新建：RRG 行业轮动 C1 回测与报告生成
2026-10-29 (phase 20) 新增 --timing-tier：复筛矩阵叠三档择时口径
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 允许直接 `python scripts/xxx.py` 运行
    sys.path.insert(0, str(ROOT))

from stock_plan.backtest.metrics import calc_annual_metrics, calc_metrics
from stock_plan.backtest.rotation import quadrant_summary, run_industry_rotation
from stock_plan.data.classification import industry_table, latest_month
from stock_plan.data.storage import Storage
from stock_plan.factors.rrg import (
    EXCLUDED_CODE_PREFIXES,
    build_equal_weight_benchmark,
    build_industry_index,
    compute_rrg,
    industry_index_wide,
    rrg_ready,
    select_industries,
)


def _pct(value) -> str:
    """把数值安全格式化成两位小数字符串（None/NaN 显示为 —）。"""
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _json_safe(value):
    """把 numpy / pandas 标量转成原生类型，避免 json.dumps 报 TypeError。"""
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, (pd.Timestamp, pd.Period)):
        return str(value)
    raise TypeError(f"不可序列化的类型：{type(value)!r}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="RRG 行业轮动 C1 回测")
    ap.add_argument("--start", help="回测起点（默认 RRG 首个成形日）")
    ap.add_argument("--end", default="2026-10-28", help="回测终点")
    ap.add_argument("--top-n", type=int, default=6, help="每次持有行业数")
    ap.add_argument("--rebalance-days", type=int, default=20, help="调仓间隔（交易日）")
    ap.add_argument("--cost-bps", type=float, default=10.0, help="单边调仓成本（基点）")
    ap.add_argument(
        "--timing-tier",
        default="off",
        choices=("off", "dual_ma"),
        help="大盘择时档位（off=默认恒满仓；dual_ma=双均线三档，作用于行业组合总暴露）",
    )
    ap.add_argument("--max-codes", type=int, default=0, help="抽样股票数，0 = 全部")
    ap.add_argument("--out", help="结果 JSON 路径")
    ap.add_argument("--md", help="报告 Markdown 路径")
    ap.add_argument("--title", default="RRG 行业轮动 C1 回测报告")
    return ap.parse_args(argv)


def _judge(rotation, bench_metrics: dict, annual_rotation: dict, annual_bench: dict, annual_turnover: float) -> list[dict]:
    """按计划 §3.5 四条判定线逐条判定（预注册，不事后挑数字）。"""
    excess = float(rotation.metrics.get("annual_return", 0.0)) - float(bench_metrics.get("annual_return", 0.0))
    mdd_diff = float(rotation.metrics.get("max_drawdown", 0.0)) - float(bench_metrics.get("max_drawdown", 0.0))
    positive_years = 0
    for year, row in annual_rotation.items():
        b = annual_bench.get(year)
        if b is None:
            continue
        if float(row.get("total_return", 0.0)) > float(b.get("total_return", 0.0)):
            positive_years += 1
    return [
        {"id": "C1-1", "rule": "相对 31 行业等权基准年化超额 ≥ +2.0pp",
         "value": f"{excess:+.2f}pp", "pass": excess >= 2.0},
        {"id": "C1-2", "rule": "最大回撤不劣于基准 3pp 以上",
         "value": f"{mdd_diff:+.2f}pp", "pass": mdd_diff >= -3.0},
        {"id": "C1-3", "rule": "有效区间内 ≥ 3 个自然年度正超额",
         "value": f"{positive_years} 年", "pass": positive_years >= 3},
        {"id": "C1-4", "rule": "年单边换手 ≤ 12 次",
         "value": f"{annual_turnover:.2f} 次/年", "pass": annual_turnover <= 12.0},
    ]


def _write_markdown(path: Path, meta: dict, panel_wide: pd.DataFrame, rrg: pd.DataFrame, rotation, bench_metrics: dict, annual_rotation: dict, annual_bench: dict, judgment: list[dict]) -> None:
    """生成 Markdown 报告（口径、覆盖度、结果、判定、限制）。"""
    ready = rrg_ready(rrg)
    first_ready = ready["date"].min()
    last = panel_wide.index.max()
    qtable = quadrant_summary(rrg)
    lines: list[str] = []
    lines.append(f"# {meta['title']}")
    lines.append("")
    lines.append(f"- 生成时间：{meta['generated_at']}")
    lines.append(f"- 数据区间：{panel_wide.index.min().date()} ~ {last.date()}")
    lines.append(f"- RRG 首个成形日：**{pd.Timestamp(first_ready).date()}**（预热 {meta['warmup_rows']} 行）")
    lines.append(f"- 回测区间：{rotation.start.date()} ~ {rotation.end.date()}（{len(rotation.equity_curve)} 个交易日）")
    lines.append(f"- 口径：申万一级 **{meta['industries']}** 个行业等权指数；top_n={meta['top_n']}；每 {meta['rebalance_days']} 交易日调仓；单边 {meta['cost_bps']} bp")
    lines.append(f"- 大盘择时档位：`{meta.get('timing_tier', 'off')}`（off=恒满仓；dual_ma=双均线三档作用于组合总暴露）")
    lines.append("")
    lines.append("## 覆盖度")
    lines.append("")
    lines.append(f"- 行业快照：`{meta['month']}`（申万一级）")
    lines.append(f"- 参与聚合股票：**{meta['codes_used']}** 只；无行业/被排除：{meta['codes_excluded']} 只（含北交所 `920xxx` {meta['bj_codes']} 只）")
    lines.append(f"- 成分少于 {meta['min_components']} 只的行业-日：{meta['sparse_days']} 个（置 NaN，不补 0）")
    hs = meta.get("holdings_stats") or {}
    if hs:
        lines.append(
            f"- 调仓持仓行业数：平均 {hs['avg_picks']} / 最少 {hs['min_picks']} / 最多 {hs['max_picks']}；"
            f"不足 top_n 的调仓 {hs['under_top_n']} 次（其中空仓 {hs['empty_rebalances']} 次）"
        )
    lines.append("")
    lines.append("## 总表（行业 ETF 口径，收益不可与股票引擎直接对比）")
    lines.append("")
    lines.append("| 口径 | 总收益% | 年化% | 最大回撤% | 夏普 | 调仓次数 | 年单边换手 | 胜率% |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    m, b = rotation.metrics, bench_metrics
    lines.append(f"| RRG 行业轮动 | {_pct(m.get('total_return'))} | {_pct(m.get('annual_return'))} | {_pct(m.get('max_drawdown'))} | {_pct(m.get('sharpe'))} | {rotation.rebalances} | {meta['annual_turnover']:.2f} | {_pct(m.get('win_rate'))} |")
    lines.append(f"| 31 行业等权基准 | {_pct(b.get('total_return'))} | {_pct(b.get('annual_return'))} | {_pct(b.get('max_drawdown'))} | {_pct(b.get('sharpe'))} | 0 | 0 | — |")
    lines.append("")
    lines.append("## 分年度（收益% / 回撤% / 超额pp）")
    lines.append("")
    lines.append("| 年份 | 轮动收益 | 轮动回撤 | 基准收益 | 基准回撤 | 超额 | 调仓成本% |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for year in sorted(annual_rotation):
        row = annual_rotation[year]
        base = annual_bench.get(year, {})
        excess = float(row.get("total_return", 0.0)) - float(base.get("total_return", 0.0))
        # 当年真实扣掉的调仓成本（由回测循环按年累计，不是用平均换手估算）
        yr_cost = float(rotation.cost_by_year.get(int(year), 0.0)) * 100.0
        lines.append(
            f"| {year} | {_pct(row.get('total_return'))} | {_pct(row.get('max_drawdown'))} | "
            f"{_pct(base.get('total_return'))} | {_pct(base.get('max_drawdown'))} | {excess:+.2f} | {yr_cost:.2f} |"
        )
    lines.append("")
    day_counts = pd.Series(1, index=rotation.equity_curve.index).groupby(rotation.equity_curve.index.year).sum()
    thin_years = [str(y) for y in sorted(annual_rotation) if int(day_counts.get(int(y), 0)) < 20]
    if thin_years:
        lines.append(f"注：{'、'.join(thin_years)} 年不足 20 个交易日（预热尾部残段），该年数字不代表年度表现。")
        lines.append("")
    lines.append("## §3.5 预注册判定（四条全满足才做 C2）")
    lines.append("")
    lines.append("| # | 条件 | 实测 | 通过 |")
    lines.append("| --- | --- | ---: | :---: |")
    for item in judgment:
        lines.append(f"| {item['id']} | {item['rule']} | {item['value']} | {'✅' if item['pass'] else '❌'} |")
    all_pass = all(item["pass"] for item in judgment)
    lines.append("")
    lines.append(f"**结论：{'通过 — 可进入 C2（个股层）' if all_pass else '不通过 — 停止 C2，C1 证伪。'}**")
    lines.append("")
    lines.append("## 象限分布（近 6 个月，每个交易日的平均行业数）")
    lines.append("")
    if not qtable.empty:
        cols = [c for c in ("转强", "领先", "转弱", "落后") if c in qtable.columns]
        recent = qtable.tail(6)
        lines.append("| 月份 | " + " | ".join(cols) + " |")
        lines.append("| --- | " + " | ".join("---:" for _ in cols) + " |")
        for _, row in recent.iterrows():
            lines.append(f"| {row['month']} | " + " | ".join(f"{float(row[c]):.1f}" for c in cols) + " |")
    else:
        lines.append("（无成形行）")
    lines.append("")
    lines.append("## 前视偏差与限制（必读）")
    lines.append("")
    lines.append("1. **行业分类只用 2026-10 单月快照回溯**：`classify/` 只有 2026-10，历史月份未追补，")
    lines.append("   个股历史行业归属不可还原（`incl_date` 是纳入时间、没有剔除时间）。若期间有成分调整，")
    lines.append("   本回测存在**前视偏差**。")
    lines.append("2. **研报口径是买行业 ETF，我们算的是行业等权指数**：ETF 有跟踪误差、溢价与流动性问题，")
    lines.append("   真实可交易收益应低于本报告。")
    warmup_years = meta["warmup_rows"] / 244.0
    lines.append(f"3. {meta['warmup_rows']} 行预热 ≈ {warmup_years:.1f} 年，有效样本不足 4 年，统计显著性弱；本脚本**不做参数寻优**。")
    lines.append("4. 成本按单边 10bp 扣，不含冲击成本与申赎费；A 股行业 ETF 实际往返成本通常高于此。")
    lines.append("5. 「转强」象限在震荡市可能过少（`select_industries` 会告警），此时实际持仓为 0~数个行业，")
    lines.append("   回撤与波动会被放大。")
    lines.append("")
    lines.append("## 可复现命令")
    lines.append("")
    lines.append("```powershell")
    lines.append(".\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\measure_rrg_industry.py --top-n 6 --rebalance-days 20")
    lines.append("```")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """脚本入口：加载数据 → 建行业指数 → 算 RRG → 回测 → 输出报告。"""
    args = _parse_args(argv)
    t0 = time.time()
    stamp = datetime.now().strftime("%Y-%m-%d-%H-%M")
    out_path = Path(args.out) if args.out else ROOT / f"tests/reports/{stamp}.rrg-c1.json"
    md_path = Path(args.md) if args.md else ROOT / f"tests/reports/{stamp}.rrg-c1.md"

    print("[1/4] 加载日线 ...", flush=True)
    storage = Storage()
    stock_list, bars_map, _ = storage.load_market_maps(
        start=None,
        end=args.end,
        max_codes=args.max_codes or None,
    )
    print(f"      股票 {len(bars_map)} 只，耗时 {time.time() - t0:.1f}s", flush=True)

    month = latest_month()
    table = industry_table(month)
    if table.empty:
        print("没有行业分类数据（classify/ 为空），无法做 RRG C1", file=sys.stderr)
        return 2
    mapping = table.set_index("code")["sw_l1"]
    mapping = mapping[mapping.astype(str).str.strip() != ""]

    print("[2/4] 聚合行业等权指数 ...", flush=True)
    panel = build_industry_index(bars_map, mapping, min_components=3)
    panel_wide = industry_index_wide(panel)
    benchmark = build_equal_weight_benchmark(panel_wide)
    industries = list(panel_wide.columns)
    codes_in_bars = set(bars_map)
    codes_with_industry = codes_in_bars & set(mapping.index)
    bj_codes = sum(1 for c in codes_in_bars if c.startswith(EXCLUDED_CODE_PREFIXES))
    sparse_days = int((panel.groupby("industry")["n_components"].apply(lambda s: (s < 3).sum())).sum())
    print(f"      行业 {len(industries)} 个，指数长度 {len(panel_wide)} 行", flush=True)

    print("[3/4] 计算 RRG ...", flush=True)
    rrg = compute_rrg(panel_wide, benchmark)
    ready = rrg_ready(rrg)
    first_ready = pd.Timestamp(ready["date"].min())
    warmup_rows = int((panel_wide.index < first_ready).sum())
    print(f"      首个成形日 {first_ready.date()}（预热 {warmup_rows} 行，成形 {len(ready)} 行）", flush=True)

    print("[4/4] 行业轮动回测 ...", flush=True)
    rotation = run_industry_rotation(
        panel_wide,
        rrg,
        start=args.start,
        end=args.end,
        rebalance_days=args.rebalance_days,
        top_n=args.top_n,
        cost_bps=args.cost_bps,
        timing_tier=args.timing_tier,
    )
    bench_metrics = calc_metrics(rotation.benchmark, pd.DataFrame())
    annual_rotation = calc_annual_metrics(rotation.equity_curve, rotation.trades)
    annual_bench = calc_annual_metrics(rotation.benchmark, pd.DataFrame())
    years = max((rotation.end - rotation.start).days / 365.0, 1 / 365.0)
    # 由总成本反推累计换手（cost = turnover × 2 × bps），避免依赖合成交易的股数
    implied_turnover = rotation.total_cost / (2.0 * args.cost_bps / 10000.0) if args.cost_bps > 0 else float("nan")
    annual_turnover = implied_turnover / years
    judgment = _judge(rotation, bench_metrics, annual_rotation, annual_bench, annual_turnover)

    # 调仓时的持仓行业数分布：用来判断「转强」信号是否长期不足（会导致长期空仓/少仓）
    hold = rotation.holdings
    if not hold.empty:
        per_date = hold.groupby("date")["industry"].nunique()
        picks = per_date.astype(int)
        holdings_stats = {
            "rebalances": int(len(picks)),
            "avg_picks": round(float(picks.mean()), 2),
            "min_picks": int(picks.min()),
            "max_picks": int(picks.max()),
            "empty_rebalances": int((picks == 0).sum()),
            "under_top_n": int((picks < args.top_n).sum()),
        }
    else:
        holdings_stats = {
            "rebalances": 0,
            "avg_picks": 0.0,
            "min_picks": 0,
            "max_picks": 0,
            "empty_rebalances": 0,
            "under_top_n": 0,
        }

    meta = {
        "title": args.title,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "month": month,
        "industries": len(industries),
        "codes_used": len(codes_with_industry),
        "codes_excluded": len(codes_in_bars) - len(codes_with_industry),
        "bj_codes": int(bj_codes),
        "min_components": 3,
        "sparse_days": sparse_days,
        "warmup_rows": warmup_rows,
        "top_n": args.top_n,
        "rebalance_days": args.rebalance_days,
        "cost_bps": args.cost_bps,
        "timing_tier": args.timing_tier,
        "annual_turnover": annual_turnover,
        "holdings_stats": holdings_stats,
        "elapsed_sec": round(time.time() - t0, 1),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": meta,
        "bench_metrics": bench_metrics,
        "rotation_metrics": rotation.metrics,
        "annual_rotation": annual_rotation,
        "annual_bench": annual_bench,
        "judgment": judgment,
        "all_pass": all(item["pass"] for item in judgment),
        "panels": {
            "dates": [d.strftime("%Y-%m-%d") for d in rotation.equity_curve.index],
            "equity": [round(float(v), 6) for v in rotation.equity_curve.values],
            "benchmark": [round(float(v), 6) for v in rotation.benchmark.values],
        },
        "holdings_tail": rotation.holdings.tail(60).assign(
            date=lambda d: d["date"].dt.strftime("%Y-%m-%d")
        ).to_dict(orient="records") if not rotation.holdings.empty else [],
        "last_selection": select_industries(ready[ready["date"] == ready["date"].max()], top_n=args.top_n),
    }
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_safe),
        encoding="utf-8",
    )

    _write_markdown(md_path, meta, panel_wide, rrg, rotation, bench_metrics, annual_rotation, annual_bench, judgment)

    print("")
    print(f"区间 {rotation.start.date()} ~ {rotation.end.date()}  ({len(rotation.equity_curve)} 交易日)")
    print(f"轮动 总收益 {rotation.metrics.get('total_return')}% / 年化 {rotation.metrics.get('annual_return')}% / 回撤 {rotation.metrics.get('max_drawdown')}%")
    print(f"基准 总收益 {bench_metrics.get('total_return')}% / 年化 {bench_metrics.get('annual_return')}% / 回撤 {bench_metrics.get('max_drawdown')}%")
    print(f"年单边换手 {annual_turnover:.2f} 次；调仓 {rotation.rebalances} 次；累计成本 {rotation.total_cost * 100:.2f}%")
    for item in judgment:
        print(f"  {item['id']} {item['rule']}：{item['value']} -> {'PASS' if item['pass'] else 'FAIL'}")
    print(f"结论：{'C1 通过，可做 C2' if payload['all_pass'] else 'C1 不通过，停止 C2'}")
    print(f"[写出] {out_path}")
    print(f"[写出] {md_path}")
    print(f"总耗时 {meta['elapsed_sec']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
