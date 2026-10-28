# -*- coding: utf-8 -*-
"""回补股本/市值历史长表（B 档字段）。

文件：scripts/backfill_share_capital.py
作者：Cwywwy
用途：抓一次全市场股本快照，用「成交额 ÷ 换手率」反推逐日流通股本，再按当期比例外推总股本
模块：scripts（数据回补）

用法::

    .\\.venv\\Scripts\\python.exe scripts/backfill_share_capital.py            # 全量回补
    .\\.venv\\Scripts\\python.exe scripts/backfill_share_capital.py --dry-run  # 只统计不落盘
    .\\.venv\\Scripts\\python.exe scripts/backfill_share_capital.py --max-codes 500 --json

幂等性：同一批行情反复跑结果一致（历史长表整体重写，不追加）；
限速：日线全部读本地，只有 1 次快照网络请求；`--sleep` 保留给后续扩展多期快照。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stock_plan.data.share_capital import (  # noqa: E402
    SHARES_DIR,
    coverage,
    derive_float_shares_history,
    derive_total_shares_history,
    fetch_share_snapshot,
    load_history,
    load_snapshot,
    save_history,
    save_snapshot,
)
from stock_plan.data.storage import Storage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """命令行入口：抓快照 → 反推历史 → 落盘 → 打印覆盖率。"""
    parser = argparse.ArgumentParser(description="回补股本/市值历史长表")
    parser.add_argument("--dry-run", action="store_true", help="只统计不落盘")
    parser.add_argument("--max-codes", type=int, default=None, help="限制股票数（调试用）")
    parser.add_argument("--out-dir", default=None, help="输出目录，默认 data/processed/shares")
    parser.add_argument("--skip-fetch", action="store_true", help="复用已落盘快照，不联网")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出统计")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir) if args.out_dir else SHARES_DIR
    started = time.time()

    if args.skip_fetch:
        snapshot = load_snapshot(out_dir)
        if snapshot.empty:
            print(f"没有可用快照：{out_dir}（去掉 --skip-fetch 先抓一次）")
            return 1
    else:
        print("抓取全市场股本快照……")
        snapshot = fetch_share_snapshot()
        if args.max_codes:
            snapshot = snapshot.head(int(args.max_codes)).reset_index(drop=True)
        if not args.dry_run:
            path = save_snapshot(snapshot, out_dir)
            print(f"  快照已落盘：{path}（{len(snapshot)} 只）")

    print("加载全市场日线……")
    # exclude_st=False：ST 股同样需要股本（策略层自己决定要不要选它们），
    # 否则这 197 只会在历史表里整体缺失，市值分档时被静默当成「无数据」。
    _, bars_map, _ = Storage().load_market_maps(max_codes=args.max_codes, exclude_st=False)
    print(f"  日线 {len(bars_map)} 只")

    history_float = derive_float_shares_history(bars_map, snapshot)
    history_total = derive_total_shares_history(history_float, snapshot)
    stats = coverage(snapshot, history_float)
    stats.update(
        {
            "total_rows": int(len(history_total)),
            "elapsed_sec": round(time.time() - started, 1),
        }
    )

    if args.dry_run:
        print("dry-run：不落盘")
    else:
        float_path, total_path = save_history(history_float, history_total, out_dir)
        stats["float_path"] = str(float_path)
        stats["total_path"] = str(total_path)
        # 幂等自检：重读一遍确认行数一致
        stats["reread_float_rows"] = int(len(load_history("float", out_dir)))
        stats["reread_total_rows"] = int(len(load_history("total", out_dir)))

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, default=str))
    else:
        print(
            f"完成：快照 {stats['snapshot_codes']} 只 / 历史 {stats['history_codes']} 只 "
            f"{stats['history_rows']} 行（{stats['history_first']} ~ {stats['history_last']}），"
            f"耗时 {stats['elapsed_sec']}s"
        )
    return 0 if stats["history_rows"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
