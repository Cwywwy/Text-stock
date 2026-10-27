"""反向验证：把 pe/pb 相关的三处修复改回坏版本，确认对应测试真的会失败。

为什么必须做：报不出来的检查等于没有检查。本项目已有 5 个空转守卫被实测删除的前例，
所以每条新加的防回归测试都要证明它对自己的缺陷敏感。

用法：`.venv/Scripts/python.exe scripts/reverse_check_valuation.py`
约定：全部如期失败 → 退出码 0；有任何一项"改坏了却仍通过" → 退出码 1。
注意：脚本**临时改写**被测源文件（每项结束后 finally 还原）。改动期间若被打断，
用 `git checkout -- <文件>` 兜底。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = ROOT / ".venv/Scripts/python.exe"
STORAGE = ROOT / "src/stock_plan/data/storage.py"
UPDATER = ROOT / "src/stock_plan/data/updater.py"

CASES = [
    (
        "回填 pe/pb 时顺手改了 updated_at（破坏 PIT 快照日）",
        STORAGE,
        """            conn.execute(
                "UPDATE fundamentals SET pe = ?, pb = ? WHERE code = ?",
                (pe, pb, code),
            )""",
        """            import pandas as _pd

            conn.execute(
                "UPDATE fundamentals SET pe = ?, pb = ?, updated_at = ? WHERE code = ?",
                (pe, pb, _pd.Timestamp.now().isoformat(), code),
            )""",
        "tests/test_valuation_backfill.py::test_refresh_valuation_不修改快照日期",
    ),
    (
        "补拉未缓存股时漏传 latest_price（pe/pb 又变 NULL）",
        UPDATER,
        """                # 传最新收盘价才能算出 pe/pb（2026-10-28 修正，原先不传导致全表 NULL）
                latest_price = (
                    float(bars["close"].iloc[-1])
                    if bars is not None and not bars.empty
                    else None
                )
                storage.save_fundamentals(
                    code, fetcher.get_fundamentals(code, latest_price=latest_price)
                )""",
        """                storage.save_fundamentals(code, fetcher.get_fundamentals(code))""",
        "tests/test_valuation_backfill.py::test_补拉未缓存股时把最新收盘价传给财务接口",
    ),
    (
        "缺 eps/bvps 时不再返回 False（写进去一堆 None 还报成功）",
        STORAGE,
        """        if pe is None and pb is None:
            return False""",
        """        if False:
            return False""",
        "tests/test_valuation_backfill.py::test_refresh_valuation_缺eps和bvps返回False",
    ),
    (
        "last_close 取成第一根（不是最后一根）",
        STORAGE,
        """        close = bars["close"].iloc[-1]""",
        """        close = bars["close"].iloc[0]""",
        "tests/test_valuation_backfill.py::test_last_close_取缓存最后一根且无缓存返回None",
    ),
]


def main() -> int:
    results = []
    for label, path, good, bad, test_id in CASES:
        original = path.read_text(encoding="utf-8")
        if good not in original:
            results.append((label, test_id, "跳过：源码片段已变化"))
            continue
        try:
            path.write_text(original.replace(good, bad, 1), encoding="utf-8")
            proc = subprocess.run(
                [str(PY), "-m", "pytest", test_id, "-q", "--no-header"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            ok = proc.returncode != 0  # 改坏了 → 测试必须失败
            results.append((label, test_id, "如期失败" if ok else "仍然通过（守卫无效）"))
        finally:
            path.write_text(original, encoding="utf-8")

    print("\n反向验证结果：")
    bad_count = 0
    for label, test_id, verdict in results:
        print(f"  [{verdict}] {label}\n      测试：{test_id}")
        if verdict != "如期失败":
            bad_count += 1

    if bad_count:
        print(f"\n有 {bad_count} 项改回坏版本后测试仍通过 → 这些守卫没有判别力")
        return 1
    print(f"\n反向验证全部通过：{len(results)}/{len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
