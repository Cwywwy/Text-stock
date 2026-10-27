"""反向验证：把「数据日期体检」的每处改动改回坏版本，确认对应测试真的会失败。

为什么要做：项目规矩是「每个防回归检查都要反向验证」——能通过的测试不代表能拦住回归，
只有把实现改坏、看到对应测试确实红，才算有判别力。空转的守卫比没有守卫更糟。

用法：
    python scripts/reverse_check_freshness.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRESHNESS = ROOT / "src" / "stock_plan" / "data" / "freshness.py"
UPDATER = ROOT / "src" / "stock_plan" / "data" / "updater.py"
PY = ROOT / ".venv" / "Scripts" / "python.exe"

CASES = [
    (
        "current 口径退回「能读出来就算新」",
        FRESHNESS,
        '        "current": int((last == latest).sum()) if latest is not None else 0,',
        '        "current": int(last.notna().sum()) if latest is not None else 0,',
        "tests/test_freshness.py::test_扫描行情能算出最新交易日与落后只数",
    ),
    (
        "有缓存也重新扫盘（marker 失效）",
        FRESHNESS,
        '    marker = load_marker(bars_dir)\n    if marker and marker.get("latest_date"):',
        '    marker = None\n    if marker and marker.get("latest_date"):',
        "tests/test_freshness.py::test_marker可读写并可脱离扫描复用",
    ),
    (
        "去掉缺口保护（落后也不扩窗口）",
        UPDATER,
        "            if gap > days:",
        "            if False:  # 坏版本：数据落后也不扩窗口",
        "tests/test_freshness.py::test_数据落后时增量窗口自动扩大",
    ),
    (
        "无条件扩窗口（没落后也扩）",
        UPDATER,
        "            if gap > days:",
        "            if gap > 0:  # 坏版本：总扩窗口",
        "tests/test_freshness.py::test_数据不落后时窗口保持默认",
    ),
    (
        "汇总里丢掉分类快照日期",
        FRESHNESS,
        '        result["classify_latest"] = months[-1] if months else None',
        '        result["classify_latest"] = None',
        "tests/test_freshness.py::test_汇总包含四类数据日期",
    ),
]


def run_case(desc: str, path: Path, old: str, bad: str, test: str) -> bool:
    original = path.read_text(encoding="utf-8")
    if old not in original:
        print(f"[skip] {desc}：源码里没找到待替换片段")
        return False
    if original.count(old) > 1:
        print(f"[skip] {desc}：待替换片段出现 {original.count(old)} 次，替换位置不唯一")
        return False
    path.write_text(original.replace(old, bad, 1), encoding="utf-8")
    try:
        proc = subprocess.run(
            [str(PY), "-m", "pytest", test, "-q", "--no-header"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        failed = proc.returncode != 0
        print(f"[{'OK' if failed else '无判别力'}] {desc} → 测试{'失败（符合预期）' if failed else '仍然通过'}")
        return failed
    finally:
        path.write_text(original, encoding="utf-8")


def main() -> int:
    results = [run_case(*case) for case in CASES]
    ok = sum(results)
    print(f"\n反向验证：{ok}/{len(CASES)} 有判别力")
    if ok != len(CASES):
        print("存在无判别力的检查，请修正或删除该检查并记录局限")
    return 0 if ok == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
