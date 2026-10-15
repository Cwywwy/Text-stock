# -*- coding: utf-8 -*-
"""项目自检（acceptance）——一条命令复验本项目已验证过的关键结论。

为什么需要它：本项目在 2026-10-15 的审计中发现了多个"改完之后收益变了多少"看不出来、
但会实质影响结论的问题（僵尸候选占位、大盘代理取平均、持仓静默丢弃、回测与实盘因子列
不一致、数据源零值行污染 ATR）。这些结论当时是靠一次性脚本验证的；本脚本把它们固化下来，
以后每次改动只要跑一遍就能确认没有被推翻。

用法：
    python scripts/acceptance.py            # 常规自检（约 30 秒）
    python scripts/acceptance.py --full     # 额外做快照打包往返校验（多约 1 分钟、占额外磁盘）
    python scripts/acceptance.py --no-pytest  # 跳过测试套件（只想查数据/环境时）

退出码：0 = 全部通过；1 = 有检查未通过。

Revision History:
    2026-10-15  首次创建：把审计与验收结论固化为可重复自检。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

LOG_DIR = PROJECT_ROOT / "data" / "logs"
REPORT = LOG_DIR / "acceptance.txt"

_results: list[tuple[str, bool | None, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _results.append((name, bool(ok), detail))
    print(f"  {'✅' if ok else '❌'} {name}" + (f" — {detail}" if detail else ""), flush=True)
    return bool(ok)


def note(name: str, detail: str = "") -> None:
    """记为"跳过"：不计入通过数，也不导致退出码非 0。"""
    _results.append((name, None, detail))
    print(f"  ⏭️ {name}" + (f" — {detail}" if detail else ""), flush=True)


# --------------------------------------------------------------------------
# 1) 环境
# --------------------------------------------------------------------------
def check_env() -> None:
    print("\n[1] 环境")
    v = sys.version_info
    check("Python >= 3.11", v >= (3, 11), f"{v.major}.{v.minor}.{v.micro}")
    check("以项目 .venv 运行", ".venv" in sys.executable.lower(), sys.executable)

    missing = []
    for mod in ("pandas", "numpy", "streamlit", "plotly", "pyarrow", "akshare"):
        try:
            __import__(mod)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{mod}({type(e).__name__})")
    check("关键依赖可导入", not missing, "缺失: " + ", ".join(missing) if missing else "6/6")

    # py_mini_racer 是 fetcher.py 的模块级依赖（云端由 akshare 在 Linux 上带入）
    try:
        import py_mini_racer  # noqa: F401

        pmr = "可导入"
    except Exception as e:  # noqa: BLE001
        pmr = f"不可导入（{type(e).__name__}）—— 在线拉取会失败"
    check("py_mini_racer 可导入（新浪日线需要）", pmr == "可导入", pmr)


# --------------------------------------------------------------------------
# 2) 测试套件
# --------------------------------------------------------------------------
def check_pytest() -> None:
    print("\n[2] 测试套件")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--no-header"],
            cwd=str(PROJECT_ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900,
        )
        tail = (r.stdout or "").strip().splitlines()[-1] if r.stdout else ""
        check("pytest 全部通过", r.returncode == 0, tail[:120])
        # 关键的三个"防回归"文件必须存在
        for f in ("test_factor_parity.py", "test_storage_sanitize.py",
                  "test_backtest.py", "test_strategy_store.py"):
            check(f"存在 tests/{f}", (PROJECT_ROOT / "tests" / f).exists())
    except Exception as e:  # noqa: BLE001
        check("pytest 全部通过", False, f"{type(e).__name__}: {str(e)[:120]}")


# --------------------------------------------------------------------------
# 3) 12 页渲染
# --------------------------------------------------------------------------
PAGES = [
    ("今日信号", "today"), ("持仓诊断", "portfolio"), ("模拟交易", "paper"),
    ("回测结果", "backtest"), ("策略对比", "compare"), ("策略拼装", "visual_builder"),
    ("策略管理", "strategy_mgr"), ("四大师研究", "four_masters"), ("LLM 智能分析", "llm"),
    ("新闻舆情", "news"), ("新手指南", "guide"), ("数据更新", "update_center"),
]
_WRAP = """# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, r"{src}")
from stock_plan.ui.views import {mod} as _m
_m.render()
"""


def check_pages(timeout: int = 300) -> None:
    print("\n[3] 12 页渲染")
    try:
        from streamlit.testing.v1 import AppTest
    except Exception as e:  # noqa: BLE001
        check("AppTest 可用", False, str(e)[:120])
        return

    tmp = Path(tempfile.mkdtemp(prefix="accept_"))
    bad: list[str] = []
    log_errors: list[str] = []
    for name, mod in PAGES:
        f = tmp / f"{mod}.py"
        f.write_text(_WRAP.format(src=str(PROJECT_ROOT / "src"), mod=mod), encoding="utf-8")
        try:
            at = AppTest.from_file(str(f), default_timeout=timeout)
            at.run()
            errs = [str(e.value)[:120] for e in at.exception]
            if errs:
                bad.append(f"{name}: {errs[0]}")
        except Exception as e:  # noqa: BLE001
            bad.append(f"{name}: {type(e).__name__} {str(e)[:100]}")
    check(f"{len(PAGES)} 页全部无异常", not bad,
          "；".join(bad[:3]) if bad else f"{len(PAGES)}/{len(PAGES)}")

    # ⚠️ 已知局限（2026-10-15 实测）：**AppTest 看不出"表格内部 Arrow 转换失败"这类问题**。
    #    背景：`st.dataframe` 收到值列混合（数字 + "启用/关闭"）时，Streamlit 内部会抛
    #    `ArrowInvalid: Could not convert '启用' ...`，但异常被吞掉、`at.exception` 为空。
    #    我试过两种探测，都**不具判别力**（在故意改回混合类型的代码上仍报通过）：
    #      1) 对 `at.dataframe` 元素试 `pa.Table.from_pandas` —— 走到元素时该列已被强制成
    #         pandas 的 `string` 扩展类型，序列化会成功；
    #      2) 捕获 logging 记录 / 重定向 stderr —— 该错误两处都不出现。
    #    因此这类问题改由 `tests/test_strategy_mgr_table.py` 直接断言"行构造函数输出同一
    #    种 dtype"来拦（该用例在改回混合类型时 3 failed，有判别力）。


# --------------------------------------------------------------------------
# 4) 数据完整性
# --------------------------------------------------------------------------
def check_data(per_stock_limit: int = 600) -> None:
    print("\n[4] 数据完整性")
    import numpy as np
    import pandas as pd

    from stock_plan.data.storage import Storage

    st = Storage()
    bars_dir = PROJECT_ROOT / "data" / "processed" / "bars"
    files = sorted(bars_dir.glob("*.parquet"))
    check("行情缓存非空", len(files) > 100, f"{len(files)} 只")

    # 经 load_bars（唯一读入口）出来必须无零值行 —— 这是 2026-10-15 的修复
    zero_via_cache = zero_via_loader = 0
    for f in files[:per_stock_limit]:
        d = pd.read_parquet(f)
        if len(d) and (d[["open", "high", "low"]] <= 0).any().any():
            zero_via_cache += 1
            if (st.load_bars(f.stem)[["open", "high", "low"]] <= 0).any().any():
                zero_via_loader += 1
    check("零值占位行不会流到消费方（load_bars 已清洗）", zero_via_loader == 0,
          f"抽样 {min(per_stock_limit, len(files))} 只：缓存含 {zero_via_cache} 只，"
          f"读出仍含 {zero_via_loader} 只")

    # 逐只体检
    dup = nonmono = badohlc = 0
    n = 0
    for f in files[:per_stock_limit]:
        d = pd.read_parquet(f)
        if d.empty:
            continue
        n += 1
        dates = pd.to_datetime(d["date"])
        if dates.duplicated().any():
            dup += 1
        elif not dates.is_monotonic_increasing:
            nonmono += 1
        lo, hi = d["low"], d["high"]
        pos = d[d[["open", "high", "low"]] > 0]
        if len(pos) and ((pos["open"] < pos["low"]) | (pos["open"] > pos["high"])
                         | (pos["close"] < pos["low"]) | (pos["close"] > pos["high"])).any():
            badohlc += 1
    check("日期唯一且递增", dup == 0 and nonmono == 0,
          f"抽查 {n} 只：重复 {dup}、未递增 {nonmono}")
    check("OHLC 自洽（低价≤开/收≤高价）", badohlc == 0, f"不自洽 {badohlc} 只")

    d = st.load_delisted()
    check("退市股元数据已补齐", len(d) >= 300, f"{len(d)} 只 / 目标 339")
    uni = list((PROJECT_ROOT / "data" / "processed" / "universe").glob("*.parquet"))
    check("时点股票池已生成", len(uni) >= 50, f"{len(uni)} 个月")


# --------------------------------------------------------------------------
# 5) 快照打包往返（--full）
# --------------------------------------------------------------------------
def check_snapshot_roundtrip() -> None:
    print("\n[5] 快照打包往返")
    import hashlib
    import shutil

    from stock_plan.data.snapshot import BARS_DIR, build_snapshot, validate_manifest

    out = Path(tempfile.mkdtemp(prefix="accept_snap_"))
    try:
        t0 = time.time()
        manifest = build_snapshot(BARS_DIR, out)
        check("打包成功", manifest["bars_count"] > 100,
              f"{manifest['bars_count']} 只 / {manifest['zip_bytes']/1048576:.1f} MB / "
              f"{len(manifest['parts'])} 卷 / {time.time()-t0:.0f}s")
        check("manifest 校验通过", not validate_manifest(manifest),
              str(validate_manifest(manifest)[:2]))

        ok_parts = 0
        for p in manifest["parts"]:
            f = out / p["file"]
            if f.exists() and f.stat().st_size == p["bytes"] and \
                    hashlib.sha256(f.read_bytes()).hexdigest() == p["sha256"]:
                ok_parts += 1
        check("逐卷 sha256/字节数一致", ok_parts == len(manifest["parts"]),
              f"{ok_parts}/{len(manifest['parts'])}")

        merged = out / "_merged.zip"
        with merged.open("wb") as fp:
            for p in manifest["parts"]:
                fp.write((out / p["file"]).read_bytes())
        got = hashlib.sha256(merged.read_bytes()).hexdigest()
        # 这一步等价于 restore_from_url 下载后的最后一道校验
        check("按序拼接重现 zip_sha256（恢复路径的最后一道校验）",
              got == manifest["zip_sha256"])
        with zipfile.ZipFile(merged) as zf:
            names = zf.namelist()
            bad = zf.testzip()
        check("zip 可读且无损坏", bad is None and len(names) == manifest["bars_count"],
              f"{len(names)} 条目")
    except Exception as e:  # noqa: BLE001
        check("快照往返", False, f"{type(e).__name__}: {str(e)[:150]}")
    finally:
        shutil.rmtree(out, ignore_errors=True)


# --------------------------------------------------------------------------
# 6) 策略存储 schema
# --------------------------------------------------------------------------
def check_strategy_store() -> None:
    print("\n[6] 策略存储")
    from stock_plan.strategy import store

    try:
        with store._conn() as conn:  # noqa: SLF001 - 自检需要看真实表结构
            cols = {r[1] for r in conn.execute("PRAGMA table_info(strategies)")}
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        check("strategies 表含 tags（策略分组）", "tags" in cols)
        check("strategies 表含 owner（分客户）", "owner" in cols)
        check("settings 表存在（存当前客户）", "settings" in tables)
        check("当前客户可读取", isinstance(store.current_owner(), str),
              store.current_owner())
    except Exception as e:  # noqa: BLE001
        check("策略库结构", False, f"{type(e).__name__}: {str(e)[:120]}")


# --------------------------------------------------------------------------
# 7) 定时任务定义
# --------------------------------------------------------------------------
def check_scheduled_tasks() -> None:
    print("\n[7] 定时任务定义")
    tasks = PROJECT_ROOT / "scripts" / "tasks"
    xmls = sorted(tasks.glob("*.xml"))
    check("任务 XML 齐备", len(xmls) == 3, f"{len(xmls)} 个")

    job = PROJECT_ROOT / "scripts" / "daily_job.ps1"
    job_text = job.read_text(encoding="utf-8") if job.exists() else ""
    need = {
        "Daily_Publish.xml": "-SkipPublish",
        "Daily_Update.xml": "-SkipPublish",
        "Monthly_Rebuild.xml": "-Rebuild",
    }
    for f in xmls:
        raw = f.read_bytes()
        utf16 = raw[:2] == b"\xff\xfe"
        text = raw.decode("utf-16") if utf16 else raw.decode("utf-8", "replace")
        cmd_ok = "daily_job.ps1" in text
        check(f"{f.name} 为 UTF-16 且指向 daily_job.ps1", utf16 and cmd_ok)

    for param in ("SkipPublish", "Rebuild", "Workers"):
        check(f"daily_job.ps1 声明了 -{param}",
              re.search(rf"\${param}\b", job_text) is not None)


# --------------------------------------------------------------------------
# 8) 敏感文件
# --------------------------------------------------------------------------
def check_secrets() -> None:
    print("\n[8] 敏感文件")
    secrets = PROJECT_ROOT / ".streamlit" / "secrets.toml"
    check("secrets.toml 存在于本机（不入库）", secrets.exists())

    def git(*args: str) -> tuple[int, str]:
        r = subprocess.run(["git", *args], cwd=str(PROJECT_ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "").strip()

    rc, tracked = git("ls-files", "--", ".streamlit/secrets.toml")
    check("未被 git 跟踪", not tracked, tracked or "未跟踪")
    rc, _ = git("check-ignore", "-q", "--", ".streamlit/secrets.toml")
    check("已被 .gitignore 忽略", rc == 0)
    rc, hist = git("log", "--all", "--oneline", "--", ".streamlit/secrets.toml")
    check("全历史中从未出现过", not hist, hist[:80] or "无记录")


# --------------------------------------------------------------------------
# 9) 应用健康
# --------------------------------------------------------------------------
def check_app_health(url: str = "http://127.0.0.1:8501/_stcore/health") -> None:
    """应用健康检查。**应用没运行时记为"跳过"、不算失败** —— 它只是可选的在线检查，
    不该让离线环境的自检整体失败（原先实现把它记成失败、但提示文字写着"非失败项"，
    说法与行为不一致，2026-10-15 修正）。
    """
    print("\n[9] 应用健康（可选，未运行则跳过）")
    import urllib.error
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "acceptance"})
        with urllib.request.urlopen(req, timeout=5) as r:
            body = r.read().decode("utf-8", "replace").strip()
        check("Streamlit 健康检查 200", r.status == 200, f"{r.status} {body}")
    except Exception as e:  # noqa: BLE001
        note("Streamlit 健康检查 200", f"未运行或不可达（{type(e).__name__}）于 {url} → 跳过")


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="项目自检")
    ap.add_argument("--full", action="store_true", help="额外做快照打包往返校验")
    ap.add_argument("--no-pytest", action="store_true", help="跳过测试套件")
    ap.add_argument("--app-url", default="http://127.0.0.1:8501/_stcore/health",
                    help="应用健康检查地址；不可达时该项记为跳过（不影响退出码）")
    args = ap.parse_args()

    print("=" * 72)
    print(f"项目自检 {datetime.now():%Y-%m-%d %H:%M:%S}   项目根 {PROJECT_ROOT}")
    print("=" * 72)

    check_env()
    if not args.no_pytest:
        check_pytest()
    check_pages()
    check_data()
    if args.full:
        check_snapshot_roundtrip()
    check_strategy_store()
    check_scheduled_tasks()
    check_secrets()
    check_app_health(args.app_url)

    # ok 为 None 表示"跳过"（例如应用没在运行）——不计入通过数，也不导致退出码非 0
    passed = sum(1 for _, ok, _ in _results if ok is True)
    skipped = [n for n, ok, _ in _results if ok is None]
    total = len(_results)
    failed = [n for n, ok, _ in _results if ok is False]
    print("\n" + "=" * 72)
    line = f"结果：{passed}/{total - len(skipped)} 通过"
    if skipped:
        line += f"；{len(skipped)} 项跳过（{', '.join(skipped)}）"
    line += f"；未通过：{failed}" if failed else "；全部通过 ✅"
    print(line)
    print("=" * 72)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        f"项目自检 {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        + "\n".join(
            f"[{'SKIP' if ok is None else ('PASS' if ok else 'FAIL')}] {n} {d}"
            for n, ok, d in _results
        )
        + f"\n\n合计 {passed}/{total - len(skipped)} 通过"
        + (f"，{len(skipped)} 项跳过" if skipped else "")
        + "\n",
        encoding="utf-8",
    )
    print(f"报告已写入 {REPORT}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
