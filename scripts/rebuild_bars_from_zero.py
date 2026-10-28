"""从 0 重建全部行情数据（维护脚本，可重复使用）。

背景：用户要求「重新做一次数据清洗，从 0 开始重新下载全部股票数据，保证数据最新、
至少不会缺 30 天以上」。做法不是复用增量更新（它只补最近 N 天），而是：
  1. 先把旧 data/processed/bars 改名备份（可回滚，见 INSTRUCTION §8 的 4 步）；
  2. 用当天最新的全市场股票列表（含北交所 920xxx）；
  3. 对每只重拉近 N 年完整日线（新浪每次返回整份历史，重拉 5 年与补 30 天成本几乎相同），
     顺带重算财务与 pe/pb；
  4. 每只整体覆盖写 parquet，因此不存在"新旧因子拼接"的跳空问题；
  5. 跑完再执行 scripts/merge_backup_history.py 把备份库里更早的历史行并回。

为什么不用 `updater.rebuild_history()`：它只遍历"已缓存的 parquet"，目录一空就什么都不做，
容易让人误以为重建成功。

用法：
    python scripts/rebuild_bars_from_zero.py              # 近 5 年、32 线程
    python scripts/rebuild_bars_from_zero.py --years 10 --workers 24
"""

from __future__ import annotations

import argparse
import sys
import time

from stock_plan.data.fetcher import DataFetcher
from stock_plan.data.storage import Storage
from stock_plan.data.updater import _refresh_stock_list, fetch_uncached


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 0 重建全部行情（调用方负责先备份/清空 bars）")
    parser.add_argument("--years", type=int, default=5, help="回溯年数（默认 5）")
    parser.add_argument("--workers", type=int, default=32, help="并发线程（默认 32，实测饱和点）")
    args = parser.parse_args(argv)

    t0 = time.time()
    storage = Storage()
    fetcher = DataFetcher()

    stock_list = _refresh_stock_list(fetcher, storage)
    codes = sorted({str(c).zfill(6) for c in stock_list["code"].tolist()})
    print(f"股票列表 {len(codes)} 只（含北交所 920xxx）", flush=True)

    _last = [0.0]

    def progress(done: int, total: int, ok: int, failed: int, current: str) -> None:
        now = time.time()
        if done % 200 == 0 or now - _last[0] > 20 or done == total:
            _last[0] = now
            rate = done / max(now - t0, 1)
            eta = (total - done) / rate / 60 if rate > 0 else 0.0
            print(
                f"[{done}/{total}] ok={ok} failed={failed} {rate:.1f} 只/s "
                f"ETA {eta:.1f} 分钟 current={current}",
                flush=True,
            )

    stats = fetch_uncached(
        codes,
        years=args.years,
        workers=args.workers,
        storage=storage,
        fetcher=fetcher,
        progress=progress,
    )
    print("完成：", {k: v for k, v in stats.items() if k != "errors"}, flush=True)
    if stats["errors"]:
        print("失败样例：", stats["errors"][:20], flush=True)
    print(f"总耗时 {time.time() - t0:.1f} 秒", flush=True)
    print("下一步：python scripts/merge_backup_history.py（把更早的历史行并回）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
