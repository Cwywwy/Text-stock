"""量化各条引擎缺陷修复对"历史回测结果"的影响（2026-10-28）。

用户要求：改动回测/因子/数据层时，**必须说明它是否改变历史回测结果**。
做法：真实本地行情，同一策略、同一区间、同一样本，逐个翻一个开关跑一次。

为什么必须用子进程：上一版在主进程里改 engine.py 源码再重跑，结果三档完全相同
（24.95%）——那是**假结果**，因为模块早已 import、代码对象在内存里缓存，改文件
对当前进程无效。凡是"改源码再对比"的度量都必须拉起新进程。

设计说明（避免误读）：
- 每条只翻一个开关，其余保持新版，差值是**该条缺陷的单独贡献**；
- 单次 A/B 的收益差不是严谨的"效应量"——持仓路径会变，同一策略收益的标准差本
  身就有十几 pp（见计划书 §7.2）。这里只回答"是否改变、大概多少、方向如何"。
- 候选池用 max_codes 抽样控制耗时；抽样本身也改变结果，故各档用同一样本。

用法：`.venv/Scripts/python.exe scripts/measure_engine_defect_impact.py`
注意：其中两档会**临时改写** `engine.py`（每档跑完在 finally 里还原）。跑之前确认
没有别的进程在改这个文件；若中途被打断，用 `git checkout -- src/stock_plan/backtest/engine.py` 兜底。
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "src/stock_plan/backtest/engine.py"
PY = ROOT / ".venv/Scripts/python.exe"
START, END = "2025-01-02", "2025-09-30"
MAX_CODES = 600

_POOL_GOOD = """    _pool_cutoff = pd.Timestamp(config.start)
    pool_bars = {c: b[b["date"] <= _pool_cutoff] for c, b in bars_map.items()}"""
_POOL_BAD = """    pool_bars = dict(bars_map)"""

_LIMIT_GOOD = """                if close <= down:
                    continue"""
_LIMIT_BAD = """                if close >= _up:
                    continue"""

# 2026-10-28 第二轮：审计 #6，策略 exit_price 返回的期望持仓天数原先被丢弃
_HOLD_GOOD = """            hit_max = hold_days >= pos.get("max_hold_days", config.max_hold_days)"""
_HOLD_BAD = """            hit_max = hold_days >= config.max_hold_days"""


# ----------------------------------------------------------------------------- worker
def worker(strategy_name: str, no_min_commission: bool, no_fund_asof: bool) -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from stock_plan.backtest.engine import BacktestConfig, run_backtest
    from stock_plan.backtest.metrics import calc_metrics
    from stock_plan.data.storage import Storage
    from stock_plan.strategy.registry import STRATEGIES

    st = Storage()
    stock_list, bars_map, fund_map = st.load_market_maps(
        start=START, end=END, lead_days=180, max_codes=MAX_CODES
    )
    cfg = BacktestConfig(
        start=date.fromisoformat(START),
        end=date.fromisoformat(END),
        min_commission=0.0 if no_min_commission else 5.0,
        fundamental_as_of=None if no_fund_asof else st.fundamentals_snapshot_date(),
    )
    res = run_backtest(STRATEGIES[strategy_name](), cfg, bars_map, fund_map, stock_list)
    m = calc_metrics(res.equity_curve, res.trades)
    print("RESULT " + json.dumps({
        "codes": len(bars_map),
        "total_return": round(float(m["total_return"]), 2),
        "trade_count": int(m["trade_count"]),
        "max_drawdown": round(float(m["max_drawdown"]), 2),
        "win_rate": round(float(m["win_rate"]), 2),
        "equity_points": int(len(res.equity_curve)),
    }, ensure_ascii=False))
    return 0


# ------------------------------------------------------------------------------- main
def _run_subprocess(label, strategy, patch=None, no_min_commission=False,
                    no_fund_asof=False):
    original = ENGINE.read_text(encoding="utf-8")
    try:
        if patch is not None:
            good, bad = patch
            if good not in original:
                print(f"[跳过] {label}：源码锚点已变化")
                return None
            ENGINE.write_text(original.replace(good, bad, 1), encoding="utf-8")
        cmd = [str(PY), str(Path(__file__).resolve()), "--worker",
               "--strategy", strategy]
        if no_min_commission:
            cmd.append("--no-min-commission")
        if no_fund_asof:
            cmd.append("--no-fund-asof")
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", cwd=str(ROOT), timeout=900)
    finally:
        if patch is not None:
            ENGINE.write_text(original, encoding="utf-8")
    if proc.returncode != 0:
        print(f"[失败] {label}\n{proc.stderr[-1500:]}")
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("RESULT "):
            return json.loads(line[len("RESULT "):])
    print(f"[失败] {label}：没有解析到结果\n{proc.stdout[-800:]}")
    return None


def main() -> int:
    print(f"样本区间 {START} ~ {END}，最多 {MAX_CODES} 只，真实本地行情\n")

    rows = []
    # 均值回归策略自带 hold_days=15，比引擎默认上限 20 短，是最能体现 #6 的策略；
    # 其余内置策略 hint（20/30/60/90）≥ 20，默认配置下取小后不变。
    all_variants = [
        ("佣金下限 5 元去掉", dict(no_min_commission=True)),
        ("财务快照不做前视修正", dict(no_fund_asof=True)),
        ("候选池不截到开始日", dict(patch=(_POOL_GOOD, _POOL_BAD))),
        ("涨跌停判回旧方向", dict(patch=(_LIMIT_GOOD, _LIMIT_BAD))),
    ]
    hold_variant = ("策略持仓天数被忽略（#6）", dict(patch=(_HOLD_GOOD, _HOLD_BAD)))
    for strategy in ("趋势跟随策略", "价值投资策略", "均值回归策略"):
        base = _run_subprocess(f"{strategy} 新版基线", strategy)
        if base is None:
            return 1
        print(f"【{strategy}】新版基线：总收益 {base['total_return']:+.2f}%、"
              f"{base['trade_count']} 笔、回撤 {base['max_drawdown']:.2f}%、"
              f"胜率 {base['win_rate']:.2f}%（样本 {base['codes']} 只）")
        rows.append((strategy, "新版（全部修复）", base))

        variants = all_variants + [hold_variant] if strategy == "均值回归策略" else all_variants
        for label, kwargs in variants:
            m = _run_subprocess(label, strategy, **kwargs)
            if m is None:
                continue
            rows.append((strategy, label, m))
            delta = m["total_return"] - base["total_return"]
            changed = "改变" if (delta or m["trade_count"] != base["trade_count"]) else "未变"
            print(f"  {label}：{m['total_return']:+.2f}%（{delta:+.2f}pp）、"
                  f"{m['trade_count']} 笔 → {changed}")
        print()

    print("汇总（同一样本、同一区间，仅翻一个开关）：")
    table = pd.DataFrame(
        [(s, label, m["total_return"], m["trade_count"], m["max_drawdown"],
          m["win_rate"]) for s, label, m in rows],
        columns=["策略", "口径", "总收益%", "笔数", "最大回撤%", "胜率%"],
    )
    print(table.to_string(index=False))
    print("\n说明：五条修复都会改变历史结果的**计算口径**，故此前的历史调参结论需按新版重跑；")
    print("但表中差值不能当作'某缺陷的收益贡献'——路径变化带来的噪声同量级。")
    return 0


if __name__ == "__main__":
    if "--worker" in sys.argv:
        sys.exit(worker(
            sys.argv[sys.argv.index("--strategy") + 1],
            "--no-min-commission" in sys.argv,
            "--no-fund-asof" in sys.argv,
        ))
    sys.exit(main())
