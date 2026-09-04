"""发布数据快照到 GitHub data-snapshot 分支（本机权威源）。

用法：
    python scripts/publish_snapshot.py            # 打包 + force push
    python scripts/publish_snapshot.py --build-only  # 只打包不推送（测试用）
    python scripts/publish_snapshot.py --pending-only  # 仅有待发布策略时才发布

供 Windows 计划任务（Snapshot_2130）与本机手动发布共用。

Revision History:
    2026-09-04  publish pending shared strategies with market snapshot
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from stock_plan.data.snapshot import BARS_DIR, publish  # noqa: E402
from stock_plan.strategy.publication import prepare_pending_publications  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="发布行情数据快照")
    parser.add_argument("--build-only", action="store_true", help="只打包到 data/logs/snapshot_build，不推送")
    parser.add_argument("--pending-only", action="store_true", help="仅在有待发布公共策略时执行")
    args = parser.parse_args()

    n = len(list(BARS_DIR.glob("*.parquet")))
    if n == 0:
        print(f"错误：{BARS_DIR} 无数据，先运行数据更新")
        raise SystemExit(1)
    print(f"本地 bars {n} 只，开始打包 ...")
    if args.build_only:
        from stock_plan.data.snapshot import build_snapshot
        from stock_plan.data.updater import LOG_DIR

        build_dir = LOG_DIR / "snapshot_build"
        m = build_snapshot(BARS_DIR, build_dir)
        print(f"打包完成：{m['zip_bytes'] / 1048576:.0f} MB，{len(m['parts'])} 卷 → {build_dir}")
        return

    publication_files, published_count = prepare_pending_publications()
    if args.pending_only and published_count == 0:
        print("没有待发布公共策略，跳过快照发布")
        return
    result = publish(extra_files=publication_files)
    print(f"快照已推送：bars={result['bars_count']}，{result['zip_bytes'] / 1048576:.0f} MB，"
          f"{result['parts']} 卷，已发布策略={published_count} → 分支 {result['branch']}")


if __name__ == "__main__":
    main()
