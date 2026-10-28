"""把备份库里的历史行合并回新库，保证「从 0 重建」没有丢掉任何历史。

为什么需要这一步：
1. 新的全市场列表取新浪 spot，**停牌股不在列表里** → 有 4 只只存在于备份库；
2. 重拉窗口是「近 5 年」，而备份库某些股票的首日更早（旧库首日 2021-09-06，新库窗口从
   2021-10-28 起）→ 35 只股票比备份少 1~13 行。
两条都会让回测悄悄少一段历史，所以按「同一日期以新数据为准、日期并集」把备份行合回来。

用法：
    python scripts/merge_backup_history.py                 # 自动选最新的 bars_backup_*
    python scripts/merge_backup_history.py --backup data/processed/bars_backup_20261028_0615
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

DEFAULT_NEW = Path("data/processed/bars")
PROCESSED = Path("data/processed")


def newest_backup(processed: Path = PROCESSED) -> Path | None:
    """取最新的 bars_backup_* 目录；没有就返回 None（调用方负责报错）。"""
    candidates = sorted(p for p in processed.glob("bars_backup_*") if p.is_dir())
    return candidates[-1] if candidates else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把备份库历史行并回新库（停牌股整份回填）")
    parser.add_argument("--new", default=str(DEFAULT_NEW), help="新行情目录")
    parser.add_argument("--backup", default="", help="备份目录；留空自动选最新 bars_backup_*")
    args = parser.parse_args(argv)

    new_dir = Path(args.new)
    old_dir = Path(args.backup) if args.backup else newest_backup(new_dir.parent)
    if old_dir is None or not old_dir.is_dir():
        print("找不到备份目录，终止（不做任何写入）")
        return 2
    if not new_dir.is_dir():
        print(f"新行情目录不存在：{new_dir}")
        return 2

    t0 = time.time()
    restored_only_old = merged = added_rows = 0
    for old_path in sorted(old_dir.glob("*.parquet")):
        code = old_path.stem
        new_path = new_dir / f"{code}.parquet"
        old_df = pd.read_parquet(old_path)
        if not new_path.exists():
            # 停牌股/抓取失败股：整份回填，等于保留旧历史
            old_df.to_parquet(new_path, index=False)
            restored_only_old += 1
            added_rows += len(old_df)
            continue
        if len(old_df) == 0:
            continue
        new_df = pd.read_parquet(new_path)
        if old_df["date"].min() >= new_df["date"].min() and old_df["date"].max() <= new_df["date"].max():
            # 旧数据完全落在新窗口内，无需合并（正常情况）
            continue
        combined = (
            pd.concat([old_df, new_df], ignore_index=True)
            .drop_duplicates(subset="date", keep="last")  # 重复日期保留新抓的那行
            .sort_values("date")
            .reset_index(drop=True)
        )
        delta = len(combined) - len(new_df)
        if delta <= 0:
            continue
        combined.to_parquet(new_path, index=False)
        merged += 1
        added_rows += delta

    total_rows = sum(len(pd.read_parquet(f, columns=["date"])) for f in new_dir.glob("*.parquet"))
    print(f"备份目录：{old_dir}")
    print(f"整份回填（仅备份有）：{restored_only_old} 只")
    print(f"合并历史行：{merged} 只，补回 {added_rows} 行")
    print(f"耗时 {time.time() - t0:.1f} 秒")
    print(f"新库文件数：{len(list(new_dir.glob('*.parquet')))}，总行数：{total_rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
