"""反向验证：把「增量更新提速」的每处改动改回坏版本，确认对应测试真的会失败。

为什么要有这个脚本：proj 的规矩是「每个防回归检查都要反向验证」——
能通过的测试不代表能拦住回归。只有把实现改坏、看到它确实红，才算有判别力。

用法：
    python scripts/reverse_check_update_speed.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HTTP_POOL = ROOT / "src" / "stock_plan" / "data" / "http_pool.py"
UPDATER = ROOT / "src" / "stock_plan" / "data" / "updater.py"
PY = ROOT / ".venv" / "Scripts" / "python.exe"

# (说明, 目标文件, 原文, 坏版本, 应当失败的测试)
CASES = [
    (
        "线程内不再复用 Session（每次新建）",
        HTTP_POOL,
        '    if session is None or getattr(_LOCAL, "timeout", None) != timeout:',
        "    if True:  # 坏版本：每次新建连接，keep-alive 失效",
        "tests/test_update_speed.py::test_同一线程内复用同一个session",
    ),
    (
        "退出 with 后不还原 requests.get/post",
        HTTP_POOL,
        "        requests.get = original_get\n        requests.post = original_post",
        "        pass  # 坏版本：不还原全局 requests",
        "tests/test_update_speed.py::test_keepalive块退出后还原requests",
    ),
    (
        "不注入默认超时",
        HTTP_POOL,
        '        if kwargs.get("timeout") is None:\n            kwargs["timeout"] = timeout',
        "        pass  # 坏版本：没有超时",
        "tests/test_update_speed.py::test_默认超时注入且不覆盖显式超时",
    ),
    (
        "股票列表每次都联网刷新",
        UPDATER,
        '        if marker.exists() and marker.read_text(encoding="utf-8").strip() == today:',
        "        if False:  # 坏版本：不读标记，每次都刷 70 页",
        "tests/test_update_speed.py::test_当天已刷新则不再联网",
    ),
    (
        "预筛漏掉落后股票（只保留本地无日期者）",
        UPDATER,
        "        todo = [\n            c for c, last in zip(cached, last_dates) if last is None or last < expected\n        ]",
        "        todo = [c for c, last in zip(cached, last_dates) if last is None]",
        "tests/test_update_speed.py::test_预筛并行结果与逐只判断一致",
    ),
]


def run_case(desc: str, path: Path, old: str, bad: str, test: str) -> bool:
    original = path.read_text(encoding="utf-8")
    if old not in original:
        print(f"[skip] {desc}：源码里没找到待替换片段")
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
