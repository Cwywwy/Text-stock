"""一键数据拉取脚本 — 全 A 股日线 + 财务指标落盘。

用法：
    uv run python -m stock_plan.data.fetch_all            # 全量拉取（断点续传）
    uv run python -m stock_plan.data.fetch_all --limit 20 # 只拉前 20 只（测试用）
    uv run python -m stock_plan.data.fetch_all --years 3  # 只拉近 3 年日线

说明：
- 已缓存的股票自动跳过（断点续传），中断后重跑即可继续
- 每只股票：日线存 Parquet，财务指标存 SQLite
- 全 A 股约 5500 只，全量拉取耗时较长，建议后台运行
- 多线程并发拉取（默认 8 线程），大幅提升速度
"""
from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from stock_plan.data.fetcher import DataFetcher
from stock_plan.data.storage import Storage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="全 A 股数据拉取")
    parser.add_argument("--limit", type=int, default=0, help="只拉前 N 只（0=全部，测试用）")
    parser.add_argument("--years", type=int, default=5, help="拉取近 N 年日线（默认 5）")
    parser.add_argument("--workers", type=int, default=8, help="并发线程数（默认 8）")
    parser.add_argument(
        "--force", action="store_true", help="强制重新拉取（默认跳过已有缓存）"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    fetcher = DataFetcher()
    storage = Storage()

    # 1. 拉取全 A 股列表并落盘
    print("拉取全 A 股列表 ...")
    stock_list = fetcher.get_stock_list()
    storage.save_stock_list(stock_list)
    print(f"股票总数: {len(stock_list)}")

    # 2. 遍历拉取日线 + 财务
    start = date.today() - timedelta(days=365 * args.years)
    end = date.today()

    codes = stock_list["code"].tolist()
    if args.limit > 0:
        codes = codes[: args.limit]

    # 断点续传：过滤掉已缓存的股票
    todo = [c for c in codes if not (storage.cache_exists(c) and not args.force)]
    skipped = len(codes) - len(todo)
    print(f"待拉取 {len(todo)} 只（已缓存跳过 {skipped} 只）")

    ok, failed = 0, 0
    lock = threading.Lock()
    t0 = time.time()

    def fetch_one(code: str) -> tuple[str, bool, str]:
        """拉取单只股票，返回 (code, 是否成功, 错误信息)。"""
        try:
            bars = fetcher.get_daily_bars(code, start, end)
            storage.save_bars(code, bars)
            fin = fetcher.get_fundamentals(code)
            storage.save_fundamentals(code, fin)
            return code, True, ""
        except Exception as e:  # 单只失败不影响整体
            return code, False, str(e)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_one, c): c for c in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            code, success, err = fut.result()
            if success:
                with lock:
                    ok += 1
            else:
                with lock:
                    failed += 1
                print(f"  [{i}/{len(todo)}] {code} 失败: {err}")
            # 每 100 只打印一次进度
            if i % 100 == 0 or i == len(todo):
                elapsed = time.time() - t0
                print(
                    f"  进度 {i}/{len(todo)} | 成功 {ok} 失败 {failed} "
                    f"| 已用 {elapsed:.0f}s"
                )

    print(f"\n完成！成功 {ok} 失败 {failed} 跳过 {skipped}，总耗时 {time.time() - t0:.0f}s")
    print(f"已缓存日线股票数: {storage.bars_count()}")


if __name__ == "__main__":
    main()
