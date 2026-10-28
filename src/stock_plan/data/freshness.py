"""数据新鲜度体检：回答「我现在用的是哪一天的数据」。

为什么单独成模块、且要缓存扫描结果：
- 行情最新交易日的准确值只能扫 `data/processed/bars/*.parquet`（实测 5916 只、32 线程 8 秒）；
  页面每次 rerun 都扫会让「数据更新」页卡住 8 秒，所以把扫描结果写成 marker 文件，
  平时读 marker，需要时（手动点「重新扫描」或更新完成）才重扫。
- 用户最关心的问题不是「抓了多少只」，而是「我看到的信号、回测用的是哪一天的数据」。
  行情、分类、情绪、公告四处的日期口径不同，必须放在一起看，才知道有没有错位。
- 另有一个真实风险：`update_incremental(days=30)` 只补最近 30 个自然日。如果机器
  超过 30 天没开机，增量会「成功」但留下中间一段空洞 —— 这里提供的 `latest_bar_date()`
  就是给增量更新用的缺口检测入口（见 updater.update_incremental 的窗口自动扩张）。

对外接口：
- `scan_bars()` 扫描行情最后交易日（慢，约 8 秒）
- `load_marker()` / `refresh_marker()` 读写缓存
- `latest_bar_date()` 取已知的最新交易日（marker 缺失时自动扫一次）
- `status()` 汇总四处数据日期，供 UI 展示
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from stock_plan.data.clock import now_cn
from stock_plan.data.storage import PROJECT_ROOT

logger = logging.getLogger(__name__)

BARS_DIR = PROJECT_ROOT / "data" / "processed" / "bars"
# marker 放在 bars 目录里，跟着数据一起被快照/备份带走；名字以下划线开头，不会撞股票代码。
# 之所以让各函数都接受 bars_dir：测试与手动补拉会传入临时目录，若 marker 固定指向
# 生产目录，一次单元测试就会去扫 5916 个真实文件（实测 8 秒 × 4 个用）。
MARKER = BARS_DIR / "_freshness.json"


def marker_path(bars_dir: Path | None = None) -> Path:
    """marker 路径：给了 bars_dir 就跟着它走，否则用生产目录。"""
    return (Path(bars_dir) / "_freshness.json") if bars_dir else MARKER


def scan_bars(bars_dir: Path | None = None, workers: int = 32) -> dict:
    """扫描全部行情文件，统计最后交易日分布。

    返回 dict：latest_date / total / current / stale_7 / stale_30 / stale_codes / scanned_at。
    `current` 的定义是「最后交易日等于全市场最新交易日」，因此停牌股、退市股会被算进 stale，
    这是有意的 —— 它们本来就不会有今天的数据，`stale_7` 只是提示"这些票没有新行情"，不等于故障。
    """
    bars_dir = bars_dir or BARS_DIR
    files = sorted(bars_dir.glob("*.parquet")) if bars_dir.exists() else []
    if not files:
        return {
            "latest_date": None,
            "total": 0,
            "current": 0,
            "stale_7": 0,
            "stale_30": 0,
            "stale_codes": [],
            "scanned_at": now_cn().isoformat(timespec="seconds"),
        }

    def one(path: Path) -> tuple[str, pd.Timestamp | None]:
        try:
            df = pd.read_parquet(path, columns=["date"])
            if df.empty:
                return path.stem, None
            return path.stem, pd.to_datetime(df["date"]).max().normalize()
        except Exception as e:  # noqa: BLE001 —— 单个坏文件不该让整体体检失败
            logger.debug("读取 %s 失败：%s", path.name, e)
            return path.stem, None

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(4, workers)) as pool:
        rows = list(pool.map(one, files))

    last = pd.Series({code: ts for code, ts in rows if ts is not None})
    latest = last.max() if not last.empty else None
    if latest is None:
        stale7 = stale30 = 0
        stale_codes: list[str] = []
    else:
        stale7 = int((last < latest - pd.Timedelta(days=7)).sum())
        stale30 = int((last < latest - pd.Timedelta(days=30)).sum())
        # 只留前 20 个代码：marker 不该膨胀成几万行的列表，UI 也只需要几个例子
        stale_codes = [str(c) for c in last[last < latest - pd.Timedelta(days=7)].index[:20]]

    result = {
        "latest_date": str(latest.date()) if latest is not None else None,
        "total": len(files),
        # current 是「最后交易日等于全市场最新交易日」的只数；退市/停牌股不算 —— 它们本来
        # 就不会有今天的数据，用 notna() 会让口径变成"能读出来就算新"，失去提示意义。
        "current": int((last == latest).sum()) if latest is not None else 0,
        "stale_7": stale7,
        "stale_30": stale30,
        "stale_codes": stale_codes,
        "scanned_at": now_cn().isoformat(timespec="seconds"),
        "scan_seconds": round(time.time() - t0, 1),
    }
    logger.info(
        "行情体检：最新 %s，覆盖 %d/%d 只，落后 7 天以上 %d 只（%.1fs）",
        result["latest_date"],
        result["current"],
        result["total"],
        stale7,
        result["scan_seconds"],
    )
    return result


def load_marker(bars_dir: Path | None = None) -> dict | None:
    """读缓存的体检结果；文件损坏就当没有（宁可慢一次，也不让页面报错）。"""
    try:
        marker = marker_path(bars_dir)
        if marker.exists():
            return json.loads(marker.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.debug("体检缓存读取失败：%s", e)
    return None


def save_marker(data: dict, bars_dir: Path | None = None) -> None:
    try:
        marker = marker_path(bars_dir)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.debug("体检缓存写入失败：%s", e)


def refresh_marker(bars_dir: Path | None = None) -> dict:
    """重扫并写缓存（更新任务结束后调用一次，8 秒成本可接受）。"""
    data = scan_bars(bars_dir)
    save_marker(data, bars_dir)
    return data


def latest_bar_date(allow_scan: bool = True, bars_dir: Path | None = None) -> pd.Timestamp | None:
    """已知的全市场最新交易日。

    优先用 marker（它记的是「上次扫到的最新日期」，只会低估不会高估 —— 低估只会让
    增量窗口更大，属于安全方向）。marker 缺失时才扫一次，避免每次更新都花 8 秒。
    """
    marker = load_marker(bars_dir)
    if marker and marker.get("latest_date"):
        try:
            return pd.Timestamp(marker["latest_date"])
        except Exception:  # noqa: BLE001
            pass
    if not allow_scan:
        return None
    scanned = refresh_marker(bars_dir)
    raw = scanned.get("latest_date")
    return pd.Timestamp(raw) if raw else None


def status(expected: pd.Timestamp | None = None, bars_dir: Path | None = None) -> dict:
    """汇总四处数据日期，供「数据更新」页展示。

    四处数据更新机制不同（行情日频、分类月频、情绪日频、公告日频），只看其中一处
    很容易误判「数据是新的」—— 例如行情补到今天但分类快照还是上个月，选行业时就会用旧名单。
    """
    from stock_plan.data import announcements, classification, market_snapshot

    marker = load_marker(bars_dir)
    if marker is None:
        marker = refresh_marker(bars_dir)
    latest = marker.get("latest_date")

    result: dict = {
        "bars_latest": latest,
        "bars_total": marker.get("total", 0),
        "bars_current": marker.get("current", 0),
        "bars_stale_7": marker.get("stale_7", 0),
        "bars_stale_30": marker.get("stale_30", 0),
        "bars_stale_codes": marker.get("stale_codes", []),
        "bars_scanned_at": marker.get("scanned_at"),
        "expected": str(expected.date()) if expected is not None else None,
    }

    if latest and expected is not None:
        result["lag_days"] = int((expected - pd.Timestamp(latest)).days)
    else:
        result["lag_days"] = None

    try:
        months = classification.available_months()
        result["classify_latest"] = months[-1] if months else None
        result["classify_months"] = len(months)
    except Exception as e:  # noqa: BLE001
        logger.debug("分类快照读取失败：%s", e)
        result["classify_latest"] = None
        result["classify_months"] = 0

    try:
        days = market_snapshot.available_days()
        result["market_latest"] = days[-1] if days else None
        result["market_days"] = len(days)
    except Exception as e:  # noqa: BLE001
        logger.debug("情绪快照读取失败：%s", e)
        result["market_latest"] = None
        result["market_days"] = 0

    try:
        stats = announcements.stats()
        result["ann_rows"] = stats.get("rows", 0)
        result["ann_codes"] = stats.get("codes", 0)
        result["ann_latest"] = stats.get("last_date")
    except Exception as e:  # noqa: BLE001
        logger.debug("公告库读取失败：%s", e)
        result["ann_rows"] = 0
        result["ann_codes"] = 0
        result["ann_latest"] = None

    return result


def main() -> int:
    """命令行入口：`python -m stock_plan.data.freshness [--refresh]`。"""
    import argparse

    parser = argparse.ArgumentParser(description="查看当前数据用的是哪一天的")
    parser.add_argument("--refresh", action="store_true", help="强制重扫行情文件（约 8 秒）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    from stock_plan.data.updater import latest_expected_trade_date

    if args.refresh:
        refresh_marker()
    info = status(latest_expected_trade_date())
    if args.json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    print(f"行情最新交易日：{info['bars_latest']}（期望 {info['expected']}，落后 {info['lag_days']} 天）")
    print(f"  覆盖 {info['bars_current']}/{info['bars_total']} 只；落后 7 天以上 {info['bars_stale_7']} 只")
    print(f"分类快照：{info['classify_latest']}（共 {info['classify_months']} 个月）")
    print(f"情绪快照：{info['market_latest']}（共 {info['market_days']} 天）")
    print(f"公告存档：{info['ann_rows']} 条 / {info['ann_codes']} 只，最新 {info['ann_latest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
