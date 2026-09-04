"""定时数据更新入口 — 供 Windows 计划任务 / Linux cron / UI 子进程调用。

用法：
    python -m stock_plan.data.update_daily            # 增量更新（幂等，可一天跑多次）
    python -m stock_plan.data.update_daily --force    # 强制重拉最近 30 天
    python -m stock_plan.data.update_daily --days 60  # 补拉近 60 天
    python -m stock_plan.data.update_daily --uncached 301266 301267   # 补拉指定未缓存股票（近 5 年）

说明：
- 每次运行先判断交易日历，自动幂等：数据已是最新时几乎秒退
- 日志写入 data/logs/update.log
- 传 --progress 时实时进度与最终结果写入该 JSON 文件（UI 轮询用）
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="每日增量数据更新")
    parser.add_argument("--days", type=int, default=30, help="每只股票补拉近 N 个自然日（默认 30）")
    parser.add_argument("--force", action="store_true", help="强制重拉（忽略本地已是最新）")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数（默认 8）")
    parser.add_argument(
        "--uncached",
        nargs="*",
        default=None,
        metavar="CODE",
        help="只补拉这些未缓存股票代码（近 5 年全量），不传则执行常规增量更新",
    )
    args = parser.parse_args()

    from stock_plan.data.updater import (
        _write_progress,
        fetch_uncached,
        make_progress_writer,
        update_incremental,
    )

    mode = "uncached" if args.uncached is not None else "incremental"
    progress = make_progress_writer(mode)

    print("开始增量数据更新 ..." if mode == "incremental" else f"开始补拉未缓存股票 {args.uncached} ...")
    error: str | None = None
    stats: dict | None = None
    try:
        if mode == "incremental":
            stats = update_incremental(days=args.days, workers=args.workers, force=args.force, progress=progress)
        else:
            stats = fetch_uncached(codes=args.uncached, progress=progress)
    except Exception as e:  # 确保异常也落到进度文件，UI 能显示失败原因
        error = str(e)
        raise
    finally:
        _write_progress({"mode": mode, "running": False, "error": error, "result": stats})

    if stats is None:
        return
    if mode == "incremental":
        print(f"期望最新交易日: {stats['expected_date']}")
        print(f"已缓存 {stats['total_cached']} 只 | 已是最新跳过 {stats['current']} 只")
        print(f"本次更新 {stats['updated']} 只，失败 {stats['failed']} 只，耗时 {stats['elapsed']}s")
        if stats["uncached"]:
            print(f"注意：发现 {len(stats['uncached'])} 只未缓存股票（定时任务不自动拉取），"
                  f"可在左侧「数据更新」页单独补拉")
    else:
        print(f"补拉 {stats['total']} 只：成功 {stats['updated']} 只，"
              f"失败 {stats['failed']} 只，耗时 {stats['elapsed']}s")
    if stats["errors"]:
        print("失败示例（最多 3 条）:")
        for code, err in stats["errors"][:3]:
            print(f"  {code}: {err}")

    # 有失败时返回非 0，方便计划任务日志排查（不视为致命）
    raise SystemExit(0 if stats["failed"] == 0 else 2)


if __name__ == "__main__":
    main()
