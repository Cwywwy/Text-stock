"""反向验证脚本：逐个把引擎修复改回"坏版本"，确认对应测试真的会失败。

为什么必须做：一个报不出来的检查等于没有检查（甚至更糟——它给人虚假的安全感）。
本项目已有前例：5 个守卫被实测证明无判别力后删除。所以每条新加的防回归测试都要
证明它对自己的缺陷敏感。

用法：`.venv/Scripts/python.exe scripts/reverse_check_engine_fixes.py`
约定：全部通过时退出码 0；有任何一项"改坏了却仍通过"则退出码 1。
注意：脚本会**临时改写**被测源文件（每项结束后在 finally 里还原），不要在别人正在
改同一份文件时并发跑。改动期间若被打断，用 `git checkout -- <文件>` 兜底。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "src/stock_plan/backtest/engine.py"
WF = ROOT / "src/stock_plan/backtest/walkforward.py"
PY = ROOT / ".venv/Scripts/python.exe"
FUNDAMENTAL = ROOT / "src/stock_plan/factors/fundamental.py"

CASES = [
    (
        "涨跌停方向判反（跌停日反而成交）",
        ENGINE,
        """            if config.enable_price_limit:
                _up, down = _price_limit(code, bars_map[code], bars.index.get_loc(ts))
                if close <= down:
                    continue  # 跌停卖不出，明日再试""",
        """            if config.enable_price_limit:
                _up, down = _price_limit(code, bars_map[code], bars.index.get_loc(ts))
                if close >= _up:
                    continue  # 坏版本：涨停卖不出""",
        "tests/test_engine_defects.py::test_跌停日卖不出_顺延到次日",
    ),
    (
        "缺少单笔最低佣金",
        ENGINE,
        """                cost = shares * buy_price
                # 佣金单笔下限（券商 5 元起收）；买入无印花税。
                fee = max(cost * config.commission, config.min_commission)""",
        """                cost = shares * buy_price
                fee = cost * config.commission""",
        "tests/test_engine_defects.py::test_单笔佣金有下限_小额单每边多收五元",
    ),
    (
        "候选池用到结束日数据（前视）",
        ENGINE,
        """    _pool_cutoff = pd.Timestamp(config.start)
    pool_bars = {c: b[b["date"] <= _pool_cutoff] for c, b in bars_map.items()}
    codes = strategy.filter_universe(stock_list, pool_bars)""",
        """    pool_bars = dict(bars_map)
    codes = strategy.filter_universe(stock_list, pool_bars)""",
        "tests/test_engine_defects.py::test_候选池与结束日无关",
    ),
    (
        "财务因子快照日之前未中性化（前视）",
        ENGINE,
        """            if _fund_as_of is not None and ts.date() < _fund_as_of:
                fund = {}""",
        """            if False:
                fund = {}""",
        "tests/test_engine_defects.py::test_财务快照日之前因子中性化",
    ),
    (
        "Walk-Forward 丢配置（资金/成本被重置）",
        WF,
        """            cfg = replace(
                base_config,
                start=train_start,
                end=train_end,
                max_hold_days=params.get("hold_days", base_config.max_hold_days),
            )""",
        """            cfg = BacktestConfig(
                start=train_start, end=train_end,
                rebalance_freq=base_config.rebalance_freq,
                market_timing=base_config.market_timing,
                max_hold_days=params.get("hold_days", base_config.max_hold_days),
            )""",
        "tests/test_engine_defects.py::test_walkforward_透传资金与成本配置",
    ),
    (
        "财务因子换算被跳过（原始指标直接当分数用 → 恒 50）",
        FUNDAMENTAL,
        """        return {k: float(fin[k]) for k in score_keys}
    return compute_fundamental(fin)""",
        """        return {k: float(fin[k]) for k in score_keys}
    return fin""",
        "tests/test_fundamental_factor.py::test_原始财务指标换算成有区分度的分数",
    ),
    (
        "无数据时给 30 分（把'无信息'当成'利空'）",
        FUNDAMENTAL,
        """NEUTRAL_FUNDAMENTAL: dict[str, float] = {
    "value_score": 50.0,
    "growth_score": 50.0,
    "quality_score": 50.0,
}""",
        """NEUTRAL_FUNDAMENTAL: dict[str, float] = {
    "value_score": 30.0,
    "growth_score": 30.0,
    "quality_score": 30.0,
}""",
        "tests/test_fundamental_factor.py::test_无数据是中性50而非利空30",
    ),
]


def main() -> int:
    failures = []
    for label, path, good, bad, test in CASES:
        original = path.read_text(encoding="utf-8")
        if good not in original:
            print(f"[跳过] {label}：源码里找不到待改片段（可能已被重构，需更新脚本）")
            failures.append(label)
            continue
        try:
            path.write_text(original.replace(good, bad, 1), encoding="utf-8")
            proc = subprocess.run(
                [str(PY), "-m", "pytest", test, "-q"],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        finally:
            path.write_text(original, encoding="utf-8")
        verdict = "改坏后如期失败 ✅" if proc.returncode != 0 else "改坏后仍然通过 ❌（空转守卫）"
        print(f"[{ 'OK ' if proc.returncode != 0 else 'BAD' }] {label} → {verdict}")
        if proc.returncode == 0:
            failures.append(label)

    print()
    if failures:
        print(f"反向验证未通过 {len(failures)} 项：{failures}")
        return 1
    print(f"反向验证全部通过：{len(CASES)}/{len(CASES)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
