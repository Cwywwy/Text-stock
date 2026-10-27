"""反向验证：把新增数据层的三处"防回归"逻辑故意改回坏版本，确认测试真的会报错。

为什么必须做：本项目的硬约定是"报不出来的守卫要删掉并记录局限"。
空转的守卫比没有守卫更糟，因为它会让人误以为这条约束有人看着。
做法：改文件 → 跑对应测试（期望 FAILED）→ 无论成败都还原 → 报告结果。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / ".venv" / "Scripts" / "python.exe"

CASES = [
    (
        "炸板率分母",
        ROOT / "src" / "stock_plan" / "factors" / "emotion.py",
        "break_rate = (n_zb / denom) if denom else float(\"nan\")",
        "break_rate = (n_zb / n_up) if n_up else float(\"nan\")",
        "tests/test_market_data.py::test_炸板率分母同时含涨停与炸板",
    ),
    (
        "广度用真实净额中位数",
        ROOT / "src" / "stock_plan" / "factors" / "emotion.py",
        '"median_net": float(net.median()) if net is not None else float("nan"),',
        '"median_net": 0.5001,',
        "tests/test_market_data.py::test_个股广度用中位数净额而不是排名分位",
    ),
    (
        "缺分类数据时不静默清空",
        ROOT / "src" / "stock_plan" / "data" / "classification.py",
        "    if table.empty or level not in table.columns:\n        # 没有分类数据时**不要**静默返回空列表（那会让选股结果莫名其妙变成 0 只）\n        return list(codes)",
        "    if table.empty or level not in table.columns:\n        return []",
        "tests/test_market_data.py::test_没有分类数据时按行业筛选不把池子清空",
    ),
]


def run(test_id: str) -> int:
    proc = subprocess.run(
        [str(PY), "-m", "pytest", test_id, "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode


def main() -> int:
    failures: list[str] = []
    for name, path, good, bad, test_id in CASES:
        original = path.read_text(encoding="utf-8")
        if good not in original:
            failures.append(f"{name}: 找不到待替换的原始片段")
            continue
        try:
            path.write_text(original.replace(good, bad, 1), encoding="utf-8", newline="\n")
            code = run(test_id)
            ok = code != 0
            print(f"[{'OK' if ok else 'BAD'}] {name}: 改回坏版本后测试 {'FAILED（有判别力）' if ok else '仍然通过（=空转守卫）'}")
            if not ok:
                failures.append(f"{name}: 守卫没有判别力")
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")

    # 还原后再跑一遍全量，确认文件真的回退了
    final = subprocess.run(
        [str(PY), "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print("还原后全量:", final.stdout.strip().splitlines()[-1] if final.stdout.strip() else final.returncode)

    if failures:
        print("存在空转守卫：", "; ".join(failures))
        return 1
    print("反向验证 3/3 通过：三处守卫确实能报错")
    return 0


if __name__ == "__main__":
    sys.exit(main())
