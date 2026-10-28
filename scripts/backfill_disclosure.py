# -*- coding: utf-8 -*-
"""回补财报披露日（B 档字段）。

文件：scripts/backfill_disclosure.py
作者：Cwywwy
用途：按报告期抓「预约披露时间 / 实际披露时间」写入 meta.db，供时点过滤使用
模块：scripts（数据回补）

用法::

    .\\.venv\\Scripts\\python.exe scripts/backfill_disclosure.py --dry-run
    .\\.venv\\Scripts\\python.exe scripts/backfill_disclosure.py                  # 补全部缺失报告期
    .\\.venv\\Scripts\\python.exe scripts/backfill_disclosure.py --period 20251231 --force

幂等性：按 (code, report_period) upsert，重复跑不重复写；
限速：每期 1 次请求，默认间隔 1 秒（`--sleep 0` 可关）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stock_plan.data.disclosure import (  # noqa: E402
    coverage,
    refresh_disclosure,
    report_periods,
    stored_periods,
)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：回补披露日并打印覆盖率。"""
    parser = argparse.ArgumentParser(description="回补财报披露日")
    parser.add_argument("--dry-run", action="store_true", help="只列出待回补报告期")
    parser.add_argument("--period", action="append", help="指定报告期（可多次）")
    parser.add_argument("--force", action="store_true", help="已入库报告期也重抓")
    parser.add_argument("--sleep", type=float, default=1.0, help="每期间隔秒数")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出统计")
    args = parser.parse_args(argv)

    periods = args.period or None
    if args.dry_run:
        done = set(stored_periods())
        todo = [p for p in (periods or report_periods()) if p not in done or args.force]
        payload = {"todo": todo, "stored": sorted(done), "coverage": coverage()}
        print(json.dumps(payload, ensure_ascii=False) if args.json else
              f"待回补 {len(todo)} 期：{', '.join(todo) or '无'}\n已入库：{', '.join(sorted(done)) or '无'}")
        return 0

    stats = refresh_disclosure(
        periods,
        skip_existing=not args.force,
        sleep_sec=args.sleep,
        verbose=not args.json,
    )
    stats["coverage"] = coverage()
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, default=str))
    else:
        cov = stats["coverage"]
        print(
            f"完成：成功 {stats['saved']} 期 / {stats['rows']} 行；"
            f"累计 {cov['rows']} 行、{cov['codes']} 只、{cov['periods']} 期"
            f"（含实际披露日 {cov['with_actual_date']} 行）"
        )
    return 0 if not stats["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
