"""用缓存日线的最新收盘价回填 fundamentals 表的 pe/pb（2026-10-28）。

背景（实测）
------------
`pe`/`pb` 两列在 `fundamentals` 表里**全表 5553 行为 NULL**。根因不是数据源没有，
而是 `fetcher.get_fundamentals()` 只有传了 `latest_price` 才算 pe/pb，而 `fetch_all.py`
与 `updater.py` 两处调用都没传 → 两列永远是 None → 估值分恒 50 → 价值类策略的
"便宜"这一维一直没起作用（详见 CHANGELOG 2026-10-28）。

本脚本不重抓财务数据（5553 只重抓要几十分钟），只用已有 eps/bvps × 缓存最新收盘价
在本地重算，几十秒完成。幂等，可反复跑。

用法：
    python scripts/fill_pe_pb.py            # 回填并打印统计
    python scripts/fill_pe_pb.py --dry-run  # 只统计覆盖率，不写库
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stock_plan.data.storage import Storage  # noqa: E402


def coverage(storage: Storage) -> dict:
    """统计 pe/pb 的填充率与取值范围。"""
    with storage._conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM fundamentals").fetchone()[0]
        pe_n = conn.execute(
            "SELECT COUNT(*) FROM fundamentals WHERE pe IS NOT NULL"
        ).fetchone()[0]
        pb_n = conn.execute(
            "SELECT COUNT(*) FROM fundamentals WHERE pb IS NOT NULL"
        ).fetchone()[0]
        both_n = conn.execute(
            "SELECT COUNT(*) FROM fundamentals "
            "WHERE pe IS NOT NULL AND pb IS NOT NULL"
        ).fetchone()[0]
        rng = conn.execute(
            "SELECT MIN(pe), MAX(pe), MIN(pb), MAX(pb) FROM fundamentals "
            "WHERE pe IS NOT NULL AND pb IS NOT NULL"
        ).fetchone()
    return {
        "total": total,
        "pe": pe_n,
        "pb": pb_n,
        "both": both_n,
        "pe_range": (rng[0], rng[1]),
        "pb_range": (rng[2], rng[3]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="用最新收盘价回填 pe/pb")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    args = parser.parse_args()

    storage = Storage()
    before = coverage(storage)
    print(f"回填前：{before['total']} 行，pe 有值 {before['pe']}、pb 有值 {before['pb']}、"
          f"两者都有 {before['both']}")
    if before["total"] == 0:
        print("fundamentals 表为空，先抓财务数据")
        return 1
    if args.dry_run:
        print("--dry-run：不写库")
        return 0

    with storage._conn() as conn:
        codes = [r[0] for r in conn.execute("SELECT code FROM fundamentals").fetchall()]

    t0 = time.time()
    filled = no_price = no_base = 0
    for i, code in enumerate(codes, 1):
        if storage.refresh_valuation(code):
            filled += 1
        else:
            row = storage.load_fundamentals(code) or {}
            if (row.get("eps") in (None, 0)) and (row.get("bvps") in (None, 0)):
                no_base += 1
            else:
                no_price += 1
        if i % 1000 == 0:
            print(f"  … {i}/{len(codes)}，已回填 {filled}")

    after = coverage(storage)
    print(f"\n完成，耗时 {time.time() - t0:.1f}s")
    print(f"回填成功 {filled} 只；缺价格 {no_price} 只；缺 eps/bvps {no_base} 只")
    print(f"回填后：pe 有值 {after['pe']}（{after['pe'] / after['total']:.1%}）、"
          f"pb 有值 {after['pb']}（{after['pb'] / after['total']:.1%}）、"
          f"两者都有 {after['both']}（{after['both'] / after['total']:.1%}）")
    print(f"pe 范围 {after['pe_range']}，pb 范围 {after['pb_range']}")
    print("\n提示：pe/pb 由「最新收盘价 ÷ 快照里的 eps/bvps」得到，随行情变化；"
          "每天更新行情后重跑本脚本即可保持最新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
