# -*- coding: utf-8 -*-
"""Phase 20 复筛矩阵的双层判定（候选层 + 择时层）。

用途
----
读取 `scripts/measure_factor_ab.py` 产出的全市场矩阵 JSON 与
`scripts/measure_rrg_batch.py` 产出的 RRG JSON，按计划书
`.github/plans/2026-10-29-7-new-strategy-rescreen-and-timing-tiers.md` §4.2
的预注册阈值给出判定，并生成中文 Markdown 报告。

判定规则（跑数前固定，本脚本不调参）：
- 候选层：W1 上按 C-1/C-2 从同族候选中选出 1 个进 W2；W2 上按 C-1/C-2/C-3 给最终结论。
- 择时层：W2 上同一候选的 `weak`/`tier` 与 `off` 比较，按 M-1/M-2/M-3 判「帮忙/帮倒忙/无差别」。
- W2 只跑一次；本脚本只读结果，不重跑。
- 若某窗口不足 3 个自然年度，C-3 / M-3 的「≥3 个年度里 ≥2 年为正」按「可用年度里全数为正」执行，
  并在报告里显式标注（W2 只有 2025 下半年 + 2026 前 10 个月两个自然年度）。

作者：Cwywwy
所属模块：scripts（离线分析脚本，不参与运行时）

修订记录：
- 2026-10-29 (phase 20) 新建：实现候选层/择时层判定与报告生成。
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

C_ANNUAL_EXCESS_MIN = 2.0  # C-1 年化超额下限（pp）
C_DD_TOLERANCE = 3.0  # C-2 允许的最差回撤退化（pp）
M_DD_IMPROVE_MIN = 3.0  # M-1 回撤改善下限（pp）
M_ANNUAL_DROP_MAX = 2.0  # M-2 年化下降上限（pp）

CANDIDATE_FAMILIES: tuple[str, ...] = (
    "收敛-pcf",
    "收敛-vcf",
    "收敛-pvcf",
    "收敛-acf",
    "收敛-trcf",
    "动量-ret20",
    "动量-ma_ratio",
    "动量-ma_ratio_blend",
)
CONVERGENCE_FAMILIES = {f for f in CANDIDATE_FAMILIES if f.startswith("收敛-")}

# 计划书 §4.2：W1 阶段「同族候选只能选 1 个进 W2」。收敛 5 变体属同一族，
# 动量 3 模式属同一族，RRG 单独一组，避免把多重检验当成多个发现。
FAMILY_GROUPS: dict[str, tuple[str, ...]] = {
    "收敛因子": tuple(f for f in CANDIDATE_FAMILIES if f.startswith("收敛-")),
    "动量方向": tuple(f for f in CANDIDATE_FAMILIES if f.startswith("动量-")),
}


def _normalize_window(text: str) -> str:
    """把 `W1训练期(2022-03~2025-06)` 这类旧标签归一成 `W1训练`。"""
    raw = str(text)
    for key in ("W1训练", "W2验证", "W3全期"):
        if raw.startswith(key):
            return key
    return raw


def _parse_label(label: str) -> tuple[str, str, str, str]:
    """把 `族/策略/窗口/口径` 拆成四元组；旧版两段式标签按「基线 arm」处理。"""
    parts = str(label).split("/")
    if len(parts) == 4:
        return parts[0], parts[1], parts[2], parts[3]
    if len(parts) == 2:
        return "基线", parts[1], _normalize_window(parts[0]), "off"
    raise ValueError(f"无法解析 arm 标签：{label}")


def _load_runs(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    runs = payload.get("runs")
    if runs is None:
        raise ValueError(f"{path} 缺少 runs 字段")
    normalized: list[dict[str, Any]] = []
    for run in runs:
        family, strategy, window, timing = _parse_label(run["label"])
        normalized.append({**run, "family": family, "strategy": strategy, "window": window, "timing": timing})
    return normalized


def _index(runs: list[dict[str, Any]]) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    return {(r["family"], r["strategy"], r["window"], r["timing"]): r for r in runs}


def _annual_excess(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, float]:
    """逐自然年超额（百分点）：候选同年收益 − 基线同年收益。"""
    base_annual = base.get("annual") or {}
    cand_annual = cand.get("annual") or {}
    out: dict[str, float] = {}
    for year in sorted(set(base_annual) & set(cand_annual)):
        try:
            b = float(base_annual[year]["total_return"])
            c = float(cand_annual[year]["total_return"])
        except (KeyError, TypeError, ValueError):
            continue
        out[str(year)] = round(c - b, 2)
    return out


def _judge_candidate(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """候选层判定：C-1 年化超额、C-2 回撤不劣化、C-3 分年度为正。"""
    b = base["metrics"]
    c = cand["metrics"]
    annual_excess = _annual_excess(base, cand)
    years = len(annual_excess)
    positive = sum(1 for value in annual_excess.values() if value > 0)
    # 可用年度不足 3 个时按「全数为正」执行（报告显式标注）
    need_positive = 2 if years >= 3 else years
    c1 = round(float(c["annual_return"]) - float(b["annual_return"]), 2)
    dd_delta = round(float(c["max_drawdown"]) - float(b["max_drawdown"]), 2)  # >0 表示回撤更浅
    return {
        "annual_excess_pp": c1,
        "dd_delta_pp": dd_delta,
        "annual_excess": annual_excess,
        "years": years,
        "positive_years": positive,
        "need_positive_years": need_positive,
        "pass_c1": c1 >= C_ANNUAL_EXCESS_MIN,
        "pass_c2": dd_delta >= -C_DD_TOLERANCE,
        "pass_c3": years > 0 and positive >= need_positive,
    }


def _judge_timing(base: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    """择时层判定：M-1 回撤改善、M-2 年化不明显变差、M-3 分年度更好。"""
    b = base["metrics"]
    c = cand["metrics"]
    annual_excess = _annual_excess(base, cand)
    years = len(annual_excess)
    better = sum(1 for value in annual_excess.values() if value > 0)
    need_better = 2 if years >= 3 else years
    dd_improve = round(float(c["max_drawdown"]) - float(b["max_drawdown"]), 2)  # >0 表示回撤更浅
    annual_delta = round(float(c["annual_return"]) - float(b["annual_return"]), 2)
    return {
        "dd_improve_pp": dd_improve,
        "annual_delta_pp": annual_delta,
        "annual_excess": annual_excess,
        "years": years,
        "better_years": better,
        "need_better_years": need_better,
        "pass_m1": dd_improve >= M_DD_IMPROVE_MIN,
        "pass_m2": annual_delta >= -M_ANNUAL_DROP_MAX,
        "pass_m3": years > 0 and better >= need_better,
    }


def _verdict_timing(result: dict[str, Any]) -> str:
    if result["pass_m1"] and result["pass_m2"] and result["pass_m3"]:
        return "帮忙"
    if result["dd_improve_pp"] < 0 or result["annual_delta_pp"] < -M_ANNUAL_DROP_MAX:
        return "帮倒忙"
    return "无差别"


def _fmt(value: float | int | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int,)):
        return str(value)
    return f"{float(value):.{digits}f}"


def _monthly_returns(run: dict[str, Any]) -> dict[str, float]:
    """把明细回放的资金曲线换算成「月收益率（%）」，键为 YYYY-MM。"""
    points = run.get("equity") or []
    if not points:
        return {}
    import pandas as pd

    index = pd.to_datetime([p["date"] for p in points])
    series = pd.Series([float(p["value"]) for p in points], index=index)
    monthly = series.resample("ME").last().pct_change() * 100.0
    return {str(idx)[:7]: float(value) for idx, value in monthly.items() if pd.notna(value)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 20 双层判定")
    parser.add_argument("--matrix", required=True, help="144 股票 arm 的矩阵 JSON")
    parser.add_argument("--rrg", default="", help="RRG 批量 JSON（6 arm）")
    parser.add_argument("--detail", default="", help="明细回放 JSON（含资金曲线与降仓失败记录）")
    parser.add_argument("--fingerprint", default="", help="数据指纹 JSON")
    parser.add_argument("--out-md", required=True, help="判定报告 Markdown 输出路径")
    parser.add_argument("--out-json", default="", help="判定明细 JSON 输出路径")
    args = parser.parse_args(argv)

    runs = _load_runs(Path(args.matrix))
    index = _index(runs)
    windows = sorted({r["window"] for r in runs})
    strategies = sorted({r["strategy"] for r in runs if r["family"] == "基线"})

    # ---------- 候选层：W1 选候选（同族只选 1 个） ----------
    selection_rows: list[dict[str, Any]] = []
    rows_by_family: dict[str, list[dict[str, Any]]] = {f: [] for f in CANDIDATE_FAMILIES}
    for family in CANDIDATE_FAMILIES:
        for strategy in strategies:
            base = index.get(("基线", strategy, "W1训练", "off"))
            cand = index.get((family, strategy, "W1训练", "off"))
            if base is None or cand is None:
                continue
            judge = _judge_candidate(base, cand)
            rows_by_family[family].append(
                {"family": family, "strategy": strategy, "cand": cand, "judge": judge, "selected": False}
            )
    for family, rows in rows_by_family.items():
        selection_rows.extend(rows)

    selected: dict[str, dict[str, Any]] = {}
    for group, families in FAMILY_GROUPS.items():
        candidates = [
            row
            for family in families
            for row in rows_by_family.get(family, [])
            if row["judge"]["pass_c1"] and row["judge"]["pass_c2"]
        ]
        if candidates:
            best = max(candidates, key=lambda r: r["judge"]["annual_excess_pp"])
            best["selected"] = True
            selected[group] = best

    # ---------- 候选层：W2 最终判定 ----------
    final_rows: list[dict[str, Any]] = []
    for group, best in selected.items():
        family = best["family"]
        strategy = best["strategy"]
        base_w2 = index.get(("基线", strategy, "W2验证", "off"))
        cand_w2 = index.get((family, strategy, "W2验证", "off"))
        if base_w2 is None or cand_w2 is None:
            continue
        judge = _judge_candidate(base_w2, cand_w2)
        judge["pass"] = judge["pass_c1"] and judge["pass_c2"] and judge["pass_c3"]
        final_rows.append(
            {
                "group": group,
                "family": family,
                "strategy": strategy,
                "w1": best["judge"],
                "w2": judge,
                "base": base_w2,
                "cand": cand_w2,
            }
        )

    # ---------- 择时层：W2 ----------
    timing_rows: list[dict[str, Any]] = []
    for row in final_rows:
        family, strategy = row["family"], row["strategy"]
        base_off = index.get((family, strategy, "W2验证", "off"))
        if base_off is None:
            continue
        for timing in ("weak", "tier"):
            cand = index.get((family, strategy, "W2验证", timing))
            if cand is None:
                continue
            judge = _judge_timing(base_off, cand)
            timing_rows.append(
                {
                    "family": family,
                    "strategy": strategy,
                    "timing": timing,
                    "judge": judge,
                    "verdict": _verdict_timing(judge),
                    "off": base_off,
                    "cand": cand,
                }
            )
    # 基线策略自身也看择时层（作为「参考」而非候选结论）
    baseline_timing: list[dict[str, Any]] = []
    for strategy in strategies:
        base_off = index.get(("基线", strategy, "W2验证", "off"))
        if base_off is None:
            continue
        for timing in ("weak", "tier"):
            cand = index.get(("基线", strategy, "W2验证", timing))
            if cand is None:
                continue
            judge = _judge_timing(base_off, cand)
            baseline_timing.append(
                {"strategy": strategy, "timing": timing, "judge": judge, "verdict": _verdict_timing(judge)}
            )

    # 全矩阵信息性：所有候选族的 W2 三档，不论是否入选，都给判定（不进正式结论）
    all_candidate_timing: list[dict[str, Any]] = []
    for family in CANDIDATE_FAMILIES:
        for strategy in strategies:
            base_off = index.get((family, strategy, "W2验证", "off"))
            if base_off is None:
                continue
            for timing in ("weak", "tier"):
                cand = index.get((family, strategy, "W2验证", timing))
                if cand is None:
                    continue
                judge = _judge_timing(base_off, cand)
                all_candidate_timing.append(
                    {
                        "family": family,
                        "strategy": strategy,
                        "timing": timing,
                        "judge": judge,
                        "verdict": _verdict_timing(judge),
                    }
                )

    # ---------- RRG ----------
    rrg_rows: list[dict[str, Any]] = []
    if args.rrg and Path(args.rrg).exists():
        rrg_payload = json.loads(Path(args.rrg).read_text(encoding="utf-8"))
        rrg_runs = rrg_payload.get("runs") or []
        rrg_index = {(r["window"], r["timing_tier"]): r for r in rrg_runs}
        for window in ("W1训练", "W2验证", "W3全期"):
            off = rrg_index.get((window, "off"))
            tier = rrg_index.get((window, "dual_ma"))
            if off is None:
                continue
            bench = off.get("bench_metrics") or {}
            row: dict[str, Any] = {
                "window": window,
                "off": off,
                "bench": bench,
                "excess_pp": round(
                    float(off["metrics"]["annual_return"]) - float(bench.get("annual_return", 0.0)), 2
                ),
                "dd_vs_bench_pp": round(
                    float(off["metrics"]["max_drawdown"]) - float(bench.get("max_drawdown", 0.0)), 2
                ),
            }
            if tier is not None:
                judge = _judge_timing(off, tier)
                row["timing"] = judge
                row["timing_verdict"] = _verdict_timing(judge)
                row["tier"] = tier
            rrg_rows.append(row)

    # ---------- 明细回放（可选）：月度极值 + 未能降仓 + 与矩阵一致性 ----------
    detail_groups: list[dict[str, Any]] = []
    detail_mismatch: list[str] = []
    if args.detail and Path(args.detail).exists():
        detail_payload = json.loads(Path(args.detail).read_text(encoding="utf-8"))
        detail_runs = detail_payload.get("runs") or []
        grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        for run in detail_runs:
            parts = run["label"].split("/")
            if len(parts) < 4:
                continue
            timing = parts[-1]
            grouped.setdefault((parts[0], parts[1]), {})[timing] = run
        for (family, strategy), by_timing in grouped.items():
            off = by_timing.get("off")
            if off is None:
                continue
            # 与矩阵同 label 的指标对拍，确认「加记录器不改变回测结果」
            matrix_run = index.get((family, strategy, "W2验证", "off"))
            if matrix_run is not None:
                for key in ("total_return", "annual_return", "max_drawdown", "sharpe", "win_rate", "trade_count"):
                    if off["metrics"].get(key) != matrix_run["metrics"].get(key):
                        detail_mismatch.append(f"{family}/{strategy}/off 的 {key} 不一致")
            row: dict[str, Any] = {"family": family, "strategy": strategy, "pairs": []}
            for timing in ("weak", "tier"):
                alt = by_timing.get(timing)
                if alt is None:
                    continue
                monthly_off = _monthly_returns(off)
                monthly_alt = _monthly_returns(alt)
                diffs = {
                    month: round(monthly_alt[month] - monthly_off[month], 2)
                    for month in sorted(set(monthly_off) & set(monthly_alt))
                }
                missed = sorted(diffs.items(), key=lambda kv: kv[1])[:5]
                avoided = sorted(diffs.items(), key=lambda kv: kv[1], reverse=True)[:5]
                failures = (alt.get("timing_failures") or {}).get("count", 0)
                row["pairs"].append(
                    {
                        "timing": timing,
                        "monthly_diff": diffs,
                        "missed": missed,
                        "avoided": avoided,
                        "failures": failures,
                        "failures_examples": (alt.get("timing_failures") or {}).get("examples", [])[:5],
                    }
                )
            detail_groups.append(row)

    # ---------- Markdown ----------
    lines: list[str] = []
    lines.append("# Phase 20 复筛矩阵双层判定报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- 矩阵 JSON：`{args.matrix}`（{len(runs)} 个 arm）")
    if args.rrg:
        lines.append(f"- RRG JSON：`{args.rrg}`")
    lines.append("- 判定阈值：C-1 年化超额 ≥ +2.0pp；C-2 回撤不劣于基线 3pp；C-3 分年度 ≥3 年里 ≥2 年为正；"
                 "M-1 回撤改善 ≥ 3pp；M-2 年化下降 ≤ 2pp；M-3 ≥3 年里 ≥2 年更好")
    lines.append("- 窗口：W1 训练（2022-03-07~2025-06-30，选候选）；W2 验证（2025-07-01~2026-10-28，判定）；"
                 "W3 全期（信息性）")
    lines.append("")

    lines.append("## 1. 候选层 W1：同族选 1 个进 W2")
    lines.append("")
    lines.append("| 族 | 策略 | 年化超额 pp | 回撤差 pp | 年度为正 | C-1 | C-2 | 入选 |")
    lines.append("| --- | --- | ---: | ---: | ---: | :--: | :--: | :--: |")
    for row in selection_rows:
        j = row["judge"]
        lines.append(
            f"| {row['family']} | {row['strategy']} | {_fmt(j['annual_excess_pp'])} | {_fmt(j['dd_delta_pp'])} | "
            f"{j['positive_years']}/{j['years']} | {'✅' if j['pass_c1'] else '❌'} | "
            f"{'✅' if j['pass_c2'] else '❌'} | {'**是**' if row['selected'] else ''} |"
        )
    lines.append("")

    lines.append("## 2. 候选层 W2：最终判定（每族只用 W1 选出的那一个）")
    lines.append("")
    if not final_rows:
        lines.append("**没有任何候选通过 W1 选拔，本阶段候选层全部不通过。**")
    else:
        lines.append("| 族 | 策略 | 基线年化 | 候选中位年化 | 超额 pp | 回撤差 pp | 年度为正 | C-1 | C-2 | C-3 | 结论 |")
        lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | :--: | :--: | :--: | :--: |")
        for row in final_rows:
            j = row["w2"]
            lines.append(
                f"| {row['family']} | {row['strategy']} | {_fmt(row['base']['metrics']['annual_return'])} | "
                f"{_fmt(row['cand']['metrics']['annual_return'])} | {_fmt(j['annual_excess_pp'])} | "
                f"{_fmt(j['dd_delta_pp'])} | {j['positive_years']}/{j['years']} | "
                f"{'✅' if j['pass_c1'] else '❌'} | {'✅' if j['pass_c2'] else '❌'} | "
                f"{'✅' if j['pass_c3'] else '❌'} | {'**通过**' if j['pass'] else '不通过'} |"
            )
    lines.append("")

    lines.append("## 3. 择时层 W2：三档 vs 恒满仓（选中的候选）")
    lines.append("")
    if not timing_rows:
        lines.append("无候选进入择时层判定。")
    else:
        lines.append("| 族 | 策略 | 口径 | 回撤改善 pp | 年化差 pp | 更好年度 | M-1 | M-2 | M-3 | 结论 |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: | :--: | :--: | :--: | :--: |")
        for row in timing_rows:
            j = row["judge"]
            lines.append(
                f"| {row['family']} | {row['strategy']} | {row['timing']} | {_fmt(j['dd_improve_pp'])} | "
                f"{_fmt(j['annual_delta_pp'])} | {j['better_years']}/{j['years']} | "
                f"{'✅' if j['pass_m1'] else '❌'} | {'✅' if j['pass_m2'] else '❌'} | "
                f"{'✅' if j['pass_m3'] else '❌'} | **{row['verdict']}** |"
            )
    lines.append("")

    lines.append("## 4. 择时层参考：7 个基线策略（不参与候选结论）")
    lines.append("")
    lines.append("| 策略 | 口径 | 回撤改善 pp | 年化差 pp | 更好年度 | 结论 |")
    lines.append("| --- | --- | ---: | ---: | ---: | :--: |")
    for row in baseline_timing:
        j = row["judge"]
        lines.append(
            f"| {row['strategy']} | {row['timing']} | {_fmt(j['dd_improve_pp'])} | {_fmt(j['annual_delta_pp'])} | "
            f"{j['better_years']}/{j['years']} | **{row['verdict']}** |"
        )
    lines.append("")

    lines.append("## 5. RRG 行业轮动（6 arm：3 窗口 × off/dual_ma）")
    lines.append("")
    if not rrg_rows:
        lines.append("未提供 RRG 批量结果。")
    else:
        lines.append("| 窗口 | 轮动年化 | 基准年化 | 超额 pp | 回撤差 vs 基准 pp | 双均线 vs off | 结论 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | --- | :--: |")
        for row in rrg_rows:
            timing_text = "—"
            verdict = "—"
            if "timing" in row:
                j = row["timing"]
                timing_text = f"回撤 {_fmt(j['dd_improve_pp'])}pp / 年化 {_fmt(j['annual_delta_pp'])}pp"
                verdict = row["timing_verdict"]
            lines.append(
                f"| {row['window']} | {_fmt(row['off']['metrics']['annual_return'])} | "
                f"{_fmt(row['bench'].get('annual_return'))} | {_fmt(row['excess_pp'])} | "
                f"{_fmt(row['dd_vs_bench_pp'])} | {timing_text} | {verdict} |"
            )
    lines.append("")

    lines.append("## 6. 换手与成本（W2，选中候选 + 基线）")
    lines.append("")
    lines.append("| 族 | 策略 | 口径 | 年化收益 | 最大回撤 | 单边年换手 | 笔数 | 基准收益 |")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |")
    detail_arms: list[tuple[str, dict[str, Any]]] = []
    for row in final_rows:
        detail_arms.append((f"{row['family']}（基线）", row["base"]))
        detail_arms.append((f"{row['family']}", row["cand"]))
    for row in timing_rows:
        detail_arms.append((f"{row['family']}·{row['timing']}", row["cand"]))
    for label, run in detail_arms:
        turnover = (run.get("turnover") or {}).get("single_side")
        lines.append(
            f"| {label} | {run['strategy']} | {run['timing']} | {_fmt(run['metrics']['annual_return'])} | "
            f"{_fmt(run['metrics']['max_drawdown'])} | {_fmt(turnover)} | {run['metrics']['trade_count']} | "
            f"{_fmt(run.get('benchmark_return'))} |"
        )
    lines.append("")

    lines.append("## 7. 结论")
    lines.append("")
    passed = [row for row in final_rows if row["w2"]["pass"]]
    passed_text = "、".join(row["family"] for row in passed) if passed else "无"
    helpful = [f"{row['family']}/{row['timing']}" for row in timing_rows if row["verdict"] == "帮忙"]
    harmful = [f"{row['family']}/{row['timing']}" for row in timing_rows if row["verdict"] == "帮倒忙"]
    lines.append(f"- 通过 W2 候选层判定的族：{passed_text}")
    lines.append(f"- 择时层「帮忙」：{'、'.join(helpful) if helpful else '无'}")
    lines.append(f"- 择时层「帮倒忙」：{'、'.join(harmful) if harmful else '无'}")
    lines.append("- 说明：同族只选 1 个——收敛 5 变体（PCF/VCF/PVCF/ACF/TRCF）里取 W1 超额最高者，"
                 "动量 3 模式同样处理，RRG 单独一组；这样不会把同一族的多次尝试当成多个独立发现。")
    lines.append("- 说明：W2 只有 2025 下半年 + 2026 前 10 个月两个自然年度，C-3/M-3 的"
                 "「≥3 个年度里 ≥2 年为正」按「可用年度全数为正」执行（已在判定表中标注年度数）。")
    lines.append("")

    lines.append("## 8. 择时层信息性汇总：全部候选族 × W2（不改变正式结论）")
    lines.append("")
    if not all_candidate_timing:
        lines.append("无数据。")
    else:
        lines.append("| 族 | 策略 | 口径 | 回撤改善 pp | 年化差 pp | 更好地年度 | 结论 |")
        lines.append("| --- | --- | --- | ---: | ---: | ---: | :--: |")
        for row in all_candidate_timing:
            j = row["judge"]
            lines.append(
                f"| {row['family']} | {row['strategy']} | {row['timing']} | {_fmt(j['dd_improve_pp'])} | "
                f"{_fmt(j['annual_delta_pp'])} | {j['better_years']}/{j['years']} | **{row['verdict']}** |"
            )
        helpful = sum(1 for r in all_candidate_timing if r["verdict"] == "帮忙")
        harmful = sum(1 for r in all_candidate_timing if r["verdict"] == "帮倒忙")
        lines.append("")
        lines.append(f"- 合计 {len(all_candidate_timing)} 组：帮忙 {helpful} 组、帮倒忙 {harmful} 组。")
    lines.append("")

    lines.append("## 9. 月度极值：错过的上涨 / 多得的避免亏损（明细回放）")
    lines.append("")
    if not detail_groups:
        lines.append("未提供明细回放 JSON（`--detail`）。")
    else:
        lines.append("每行取「该口径月收益 − off 月收益」的极值月：左 5 为错过最多（择时害处），右 5 为躲开最多（择时好处）。")
        lines.append("")
        for group in detail_groups:
            for pair in group["pairs"]:
                lines.append(f"### {group['family']} / {group['strategy']} / {pair['timing']}")
                lines.append("")
                missed = "、".join(f"{m}（{v:+.2f}pp）" for m, v in pair["missed"])
                avoided = "、".join(f"{m}（{v:+.2f}pp）" for m, v in pair["avoided"])
                lines.append(f"- 错过最多：{missed}")
                lines.append(f"- 躲开最多：{avoided}")
                lines.append("")
        if detail_mismatch:
            lines.append(f"- ⚠️ 明细回放与矩阵指标不一致：{'；'.join(detail_mismatch)}")
            lines.append("")
        else:
            lines.append("- 明细回放与矩阵同 label 的六项指标逐值一致（记录器不改变回测结果）。")
            lines.append("")

    lines.append("## 10. 未能降仓（明细回放）")
    lines.append("")
    lines.append("记录口径：降档日按目标市值降仓后，若持仓市值仍高于目标 2% 即记一条。常见原因："
                 "当日跌停 / 停牌卖不出、当日买入受 T+1 豁免、仓位不足一手、整手取整后残余。"
                 "注意：单日 gap 较大通常意味着多只持仓各自「不足一手可卖」，这是小资金 + 100 股整手约束的"
                 "执行残差，不是规则失效。")
    lines.append("")
    failed = [g for g in detail_groups if any(p["failures"] for p in g["pairs"])]
    if not failed:
        lines.append("无记录（或未提供明细回放）。")
        lines.append("")
    else:
        for group in failed:
            for pair in group["pairs"]:
                if not pair["failures"]:
                    continue
                lines.append(
                    f"- {group['family']} / {group['strategy']} / {pair['timing']}："
                    f"{pair['failures']} 条未能降仓记录"
                )
                for item in pair["failures_examples"]:
                    lines.append(f"  - {item}")
        lines.append("")

    lines.append("## 11. 数据指纹")
    lines.append("")
    fp = None
    if args.fingerprint and Path(args.fingerprint).exists():
        fp = json.loads(Path(args.fingerprint).read_text(encoding="utf-8"))
        for key, value in fp.items():
            lines.append(f"- `{key}`：{value}")
    else:
        lines.append("未提供指纹文件（`--fingerprint`）。")
    lines.append("")

    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[写出] {out_md}")

    if args.out_json:
        payload = {
            "generated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            "thresholds": {
                "c1_annual_excess_pp": C_ANNUAL_EXCESS_MIN,
                "c2_dd_tolerance_pp": C_DD_TOLERANCE,
                "m1_dd_improve_pp": M_DD_IMPROVE_MIN,
                "m2_annual_drop_pp": M_ANNUAL_DROP_MAX,
            },
            "selection_w1": [
                {"family": r["family"], "strategy": r["strategy"], "selected": r["selected"], **r["judge"]}
                for r in selection_rows
            ],
            "final_w2": [
                {
                    "family": r["family"],
                    "strategy": r["strategy"],
                    "w1": r["w1"],
                    "w2": r["w2"],
                    "base": {"label": r["base"]["label"], "metrics": r["base"]["metrics"]},
                    "cand": {"label": r["cand"]["label"], "metrics": r["cand"]["metrics"]},
                }
                for r in final_rows
            ],
            "timing_w2": [
                {
                    "family": r["family"],
                    "strategy": r["strategy"],
                    "timing": r["timing"],
                    "verdict": r["verdict"],
                    **r["judge"],
                }
                for r in timing_rows
            ],
            "baseline_timing": baseline_timing,
            "candidate_timing_all_w2": all_candidate_timing,
            "detail_replay": detail_groups,
            "detail_mismatch": detail_mismatch,
            "fingerprint": fp,
            "rrg": rrg_rows,
        }
        out_json = Path(args.out_json)
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(f"[写出] {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
