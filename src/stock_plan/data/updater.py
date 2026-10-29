"""每日增量数据更新引擎。

设计：
- 只更新"已缓存"股票的最近 N 个自然日日线（默认 30 天），合并去重后落盘，
  不动历史数据，也不拉财务（财务变化慢，全量脚本按月补即可）
- 未缓存股票（新上市/此前拉取失败）不在定时任务里拉取，只返回代码清单，
  由 UI 提示用户点击单独拉取（全量 5 年，耗时较长）
- 幂等：某只股票本地最新日期已达到"期望最新交易日"时直接跳过，不发起网络请求，
  因此同一任务一天重复跑 4 次没有副作用，周末/节假日自动空转
- 交易日历用 akshare 的 A 股交易日历（含法定节假日），本地按天缓存一份 CSV

用法（UI / 计划任务共用）：
    from stock_plan.data.updater import update_incremental
    stats = update_incremental()
"""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from stock_plan.data.clock import now_cn, to_cn, today_cn
from stock_plan.data.fetcher import DataFetcher
from stock_plan.data.http_pool import keepalive_requests
from stock_plan.data.storage import PROJECT_ROOT, Storage

logger = logging.getLogger(__name__)

# 交易日历缓存文件（按天失效，一天只联网拉一次）
CALENDAR_PATH = PROJECT_ROOT / "data" / "processed" / "trade_calendar.csv"
LOG_DIR = PROJECT_ROOT / "data" / "logs"
# 进度文件：更新进程把实时进度写到这里，UI 每 3 秒轮询（子进程崩溃也不丢状态）
PROGRESS_PATH = LOG_DIR / "update_progress.json"

# 进度回调类型：progress(done, total, ok, failed, current)
ProgressFn = type(lambda **kw: None)


# ---------- 交易日历 ----------

def get_trade_calendar(refresh: bool = False) -> pd.DatetimeIndex:
    """获取 A 股交易日历（升序 DatetimeIndex）。

    优先读本地缓存（当天有效），缓存不存在/过期/刷新时从 akshare 拉取；
    拉取失败则回退用"周一到周五"近似，保证定时任务不至于中断。
    """
    today_str = today_cn().isoformat()
    if not refresh and CALENDAR_PATH.exists():
        try:
            head = CALENDAR_PATH.read_text(encoding="utf-8").splitlines()[0]
            if head.strip() == today_str:
                df = pd.read_csv(CALENDAR_PATH, skiprows=1)
                return pd.DatetimeIndex(pd.to_datetime(df["trade_date"]))
        except Exception:
            pass

    dates = None
    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        dates = pd.DatetimeIndex(pd.to_datetime(df["trade_date"].astype(str)))
    except Exception as e:
        logger.warning("交易日历拉取失败，回退为周末近似: %s", e)

    if dates is None or dates.empty:
        # 回退：近 2 年的周一到周五（仅用于兜底判断，误差只有法定节假日）
        rng = pd.bdate_range(end=today_cn() + timedelta(days=365), periods=730)
        dates = pd.DatetimeIndex(rng)

    try:
        CALENDAR_PATH.parent.mkdir(parents=True, exist_ok=True)
        out = pd.DataFrame({"trade_date": dates.strftime("%Y-%m-%d")})
        with CALENDAR_PATH.open("w", encoding="utf-8") as f:
            f.write(today_str + "\n")
            out.to_csv(f, index=False)
    except Exception:
        pass
    return dates


def latest_expected_trade_date(now: datetime | None = None) -> pd.Timestamp:
    """当前时点"本地数据应该更新到哪一天"。

    规则：当天是交易日且已过 16:00 → 当天；否则 → 上一个交易日。
    （16:00 前当天收盘数据尚未生成，0:00/8:00 的定时任务对应的是前一交易日数据）
    """
    now = to_cn(now)
    trades = get_trade_calendar()
    today = pd.Timestamp(now.date())
    past = trades[trades <= today]
    if past.empty:
        return today
    if past[-1] == today and (now.hour, now.minute) >= (16, 0):
        return today
    if past[-1] == today:
        # 当天是交易日但未过 16:00 → 前一交易日
        return past[-2] if len(past) >= 2 else past[-1]
    return past[-1]  # 今天不是交易日 → 上一个交易日


# ---------- 合并写入 ----------

def merge_bars(storage: Storage, code: str, new_bars: pd.DataFrame) -> int:
    """把新日线与本地缓存合并（按日期去重、新数据优先）后整体重写，返回合并后行数。"""
    if new_bars is None or new_bars.empty:
        return 0
    old = storage.load_bars(code)
    combined = pd.concat([old, new_bars], ignore_index=True)
    combined["date"] = pd.to_datetime(combined["date"])
    combined = (
        combined.drop_duplicates(subset="date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )
    storage.save_bars(code, combined)
    return len(combined)


# ---------- 增量更新 ----------

def _list_marker(storage: Storage) -> Path:
    """股票列表刷新标记放在该 Storage 自己的 db 目录里（测试/临时库互不干扰）。"""
    return storage.db_path.parent / "stock_list_date.txt"


def _refresh_stock_list(fetcher: DataFetcher, storage: Storage) -> pd.DataFrame:
    """取全市场股票列表：当天已刷新过就读本地，否则在线刷新并记标记。

    为什么加这个（2026-10-28 实测）：在线列表走新浪 spot 接口要翻 70 页、约 20 秒，
    而它一天只会变一次（新股上市）。原先每跑一次增量更新都重刷，全市场 5550 只
    的净抓取时间才 5~9 分钟，这 20 秒白花得可惜；更糟的是列表接口失败会让整轮
    更新直接退回本地并打印告警。所以「当天刷过就跳过」。
    """
    today = today_cn().isoformat()
    marker = _list_marker(storage)
    try:
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == today:
            cached_list = storage.load_stock_list()
            if not cached_list.empty:
                return cached_list
    except Exception:  # noqa: BLE001 - 标记文件损坏就当没刷过
        pass

    try:
        stock_list = fetcher.get_stock_list()
        storage.save_stock_list(stock_list)
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(today, encoding="utf-8")
        except Exception:  # noqa: BLE001 - 写不了标记不影响本次结果
            pass
        return stock_list
    except Exception as e:  # noqa: BLE001
        logger.warning("在线股票列表拉取失败，使用本地列表: %s", e)
        return storage.load_stock_list()


def refresh_aux_fields(
    storage: Storage | None = None,
    *,
    include_shares: bool = True,
    include_disclosure: bool = True,
    force_disclosure: bool = False,
) -> dict:
    """刷新 B 档辅助字段（股本/市值快照 + 财报披露日），返回统计 dict。

    设计取舍：
    - 股本快照一天一变，抓一次约 3~8 秒，每次增量更新都刷；
    - 披露日按报告期抓，一期 1 次请求，**只在临近法定截止日的 30 天窗口内补**，
      平时不打接口（否则每天白跑 1~2 次且命中率极低）；
    - 两类字段互不影响：一类失败只在自己的键下记 error，不抛给调用方。
    """
    storage = storage or Storage()
    result: dict = {}

    if include_shares:
        from stock_plan.data.share_capital import fetch_share_snapshot, save_snapshot

        try:
            snapshot = fetch_share_snapshot()
            path = save_snapshot(snapshot)
            result["shares"] = {
                "codes": int(snapshot["code"].nunique()),
                "as_of": str(snapshot["as_of"].iloc[0]),
                "path": str(path),
            }
        except Exception as e:  # noqa: BLE001 —— 辅助字段失败不该中断主流程
            result["shares"] = {"error": f"{type(e).__name__}: {e}"[:200]}

    if include_disclosure:
        from stock_plan.data.disclosure import (
            refresh_disclosure,
            report_periods,
            statutory_deadline,
            stored_periods,
        )

        try:
            today = today_cn()
            done = set(stored_periods(storage.db_path))
            due: list[str] = []
            for period in report_periods()[-2:]:
                if period in done and not force_disclosure:
                    continue
                deadline = pd.Timestamp(statutory_deadline(period)).date()
                if force_disclosure or 0 <= (deadline - today).days <= 30:
                    due.append(period)
            if due:
                result["disclosure"] = refresh_disclosure(
                    due,
                    skip_existing=not force_disclosure,
                    db_path=storage.db_path,
                    verbose=False,
                )
            else:
                result["disclosure"] = {"due": []}
        except Exception as e:  # noqa: BLE001
            result["disclosure"] = {"error": f"{type(e).__name__}: {e}"[:200]}

    return result


def update_incremental(
    days: int = 30,
    workers: int = 32,
    force: bool = False,
    storage: Storage | None = None,
    fetcher: DataFetcher | None = None,
    progress: ProgressFn | None = None,
) -> dict:
    """增量更新全部已缓存股票的日线。

    - days: 每只股票补拉最近多少个自然日
    - 已是最新（本地最后日期 >= 期望最新交易日）的股票直接跳过，不发网络请求
    - 返回统计 dict：updated / current / failed / errors / uncached / expected_date 等
    """
    storage = storage or Storage()
    fetcher = fetcher or DataFetcher()
    t0 = time.time()

    expected = latest_expected_trade_date()
    start = today_cn() - timedelta(days=days)
    end = today_cn()

    # 缺口保护（2026-10-28）：`days=30` 的固定窗口只适用于「一直有在用」的机器。
    # 如果本机上次更新在 30 天以前，从今天往回只抓 30 天，旧数据和新抓取之间就会
    # 留一整段空洞 —— 任务显示"成功"，回测却凭空少了几十天。所以先问一次本地最新
    # 交易日，落后超过窗口就把窗口撑大到覆盖缺口（多抓几天互相重叠，合并时天然去重）。
    data_lag_days: int | None = None
    try:
        from stock_plan.data.freshness import latest_bar_date

        local_latest = latest_bar_date(bars_dir=getattr(storage, "bars_dir", None))
        if local_latest is not None:
            gap = (end - local_latest.date()).days
            data_lag_days = int(gap)
            if gap > days:
                days = min(gap + 5, 365 * 5)
                start = end - timedelta(days=days)
                logger.warning(
                    "本地行情最新 %s，已落后 %d 天，增量窗口自动扩到 %d 天",
                    local_latest.date(),
                    gap,
                    days,
                )
    except Exception as e:  # noqa: BLE001 —— 体检失败不该挡住抓取
        logger.debug("数据新鲜度检查失败，按原窗口执行：%s", e)

    # 股票列表：优先在线刷新（顺带捕获新股），失败退回本地。
    # 2026-10-28 提速：全市场列表走新浪 spot 接口要翻 70 页、实测约 20 秒，
    # 而列表一天只会变一次 —— 所以当天已经刷过就直接读本地，省掉这 20 秒。
    stock_list = _refresh_stock_list(fetcher, storage)

    all_codes = stock_list["code"].astype(str).tolist() if not stock_list.empty else []
    # 单次遍历分桶：原先 cached / uncached 各调一次 cache_exists，等于每只股票 stat 两次
    cached: list[str] = []
    uncached: list[str] = []
    for c in all_codes:
        (cached if storage.cache_exists(c) else uncached).append(c)

    # 幂等预筛：本地已到期望交易日的直接跳过（只读本地文件，不联网）。
    # 2026-10-28 提速：每只读一次 parquet 约 5ms，5550 只串行就是 28 秒 ——
    # pyarrow 读文件会释放 GIL，所以丢进线程池并行，实测降到 2~3 秒。
    if force:
        todo = list(cached)
    else:
        with ThreadPoolExecutor(max_workers=min(32, max(4, workers))) as pool:
            last_dates = list(pool.map(lambda c: _last_date(storage, c), cached))
        todo = [
            c for c, last in zip(cached, last_dates) if last is None or last < expected
        ]

    stats = {
        "expected_date": str(expected.date()),
        "window_days": days,
        "data_lag_days": data_lag_days,
        "total_cached": len(cached),
        "todo": len(todo),
        "updated": 0,
        "current": len(cached) - len(todo),
        "failed": 0,
        "errors": [],
        "uncached": uncached,
        "elapsed": 0.0,
    }
    _notify(progress, done=0, total=len(todo), ok=0, failed=0, current="")

    if todo:
        lock = threading.Lock()
        done = ok = failed = 0

        def fetch_one(code: str) -> tuple[str, bool, str]:
            try:
                bars = fetcher.get_daily_bars(code, start, end)
                merge_bars(storage, code, bars)
                return code, True, ""
            except Exception as e:
                return code, False, str(e)

        # keep-alive：每只股票 3 个新浪请求，复用连接把 TLS 握手从「每请求一次」
        # 降到「每线程一次」，实测 16 线程 8.78 → 15.6 只/秒（详见 http_pool 模块注释）
        with keepalive_requests(timeout=30), ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(fetch_one, c): c for c in todo}
            for fut in as_completed(futures):
                code, success, err = fut.result()
                with lock:
                    done += 1
                    if success:
                        ok += 1
                    else:
                        failed += 1
                        stats["errors"].append((code, err))
                    _notify(
                        progress,
                        done=done,
                        total=len(todo),
                        ok=ok,
                        failed=failed,
                        current=code,
                    )

        stats["updated"] = ok
        stats["failed"] = failed
        stats["errors"] = stats["errors"][:50]  # 只保留前 50 条避免过大

    stats["elapsed"] = round(time.time() - t0, 1)
    # B 档字段（Phase 12）：股本/市值快照每次更新刷一次（成本 <10 秒）；披露日只在临近
    # 法定截止日时才补。任何失败都不影响日线结果，只在 stats 里留痕。
    try:
        stats["aux_fields"] = refresh_aux_fields(storage=storage)
    except Exception as e:  # noqa: BLE001 —— 辅助字段失败不该让日线更新报错
        logger.warning("B 档辅助字段刷新失败（不影响日线）：%s", e)
        stats["aux_fields"] = {"error": str(e)[:200]}
    _log_run("incremental", stats)
    # 更新完把体检缓存刷掉，否则页面上的「行情最新交易日」要等到手动重扫才会变新
    try:
        from stock_plan.data.freshness import refresh_marker

        refresh_marker(getattr(storage, "bars_dir", None))
    except Exception as e:  # noqa: BLE001
        logger.debug("体检缓存刷新失败：%s", e)
    return stats


def fetch_uncached(
    codes: list[str],
    years: int = 5,
    workers: int = 16,
    storage: Storage | None = None,
    fetcher: DataFetcher | None = None,
    progress: ProgressFn | None = None,
) -> dict:
    """全量拉取指定未缓存股票（近 years 年日线 + 财务），供手动补新股票用。"""
    storage = storage or Storage()
    fetcher = fetcher or DataFetcher()
    t0 = time.time()
    start = today_cn() - timedelta(days=365 * years)
    end = today_cn()

    stats = {"total": len(codes), "updated": 0, "failed": 0, "errors": [], "elapsed": 0.0}
    _notify(progress, done=0, total=len(codes), ok=0, failed=0, current="")

    lock = threading.Lock()
    done = ok = failed = 0

    def fetch_one(code: str) -> tuple[str, bool, str]:
        try:
            bars = fetcher.get_daily_bars(code, start, end)
            storage.save_bars(code, bars)
            try:
                # 传最新收盘价才能算出 pe/pb（2026-10-28 修正，原先不传导致全表 NULL）
                latest_price = (
                    float(bars["close"].iloc[-1])
                    if bars is not None and not bars.empty
                    else None
                )
                storage.save_fundamentals(
                    code, fetcher.get_fundamentals(code, latest_price=latest_price)
                )
            except Exception:
                pass  # 财务失败不影响日线入库
            return code, True, ""
        except Exception as e:
            return code, False, str(e)

    with keepalive_requests(timeout=30), ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, success, err = fut.result()
            with lock:
                done += 1
                if success:
                    ok += 1
                else:
                    failed += 1
                    stats["errors"].append((code, err))
                _notify(progress, done=done, total=len(codes), ok=ok, failed=failed, current=code)

    stats["updated"] = ok
    stats["failed"] = failed
    stats["errors"] = stats["errors"][:50]
    stats["elapsed"] = round(time.time() - t0, 1)
    _log_run("uncached", stats)
    return stats


# ---------- 全历史重建（修正前复权回溯） ----------

def rebuild_history(
    years: int = 5,
    workers: int = 16,
    storage: Storage | None = None,
    fetcher: DataFetcher | None = None,
    progress: ProgressFn | None = None,
) -> dict:
    """重拉全部已缓存股票的完整历史日线并整体覆盖。

    为什么需要这个：
    前复权价是**回溯性**的 —— 每次分红/送转后，新浪返回的整段历史价格都会按新因子
    改写。而"增量补最近 N 天再合并"只会覆盖最近那段，更早的行仍停留在旧因子上，
    结果是历史序列在除权日出现人为跳空，均线/ATR/涨跌幅/回测全线失真。
    （实测：间隔约 6 周后，000001 全历史偏移 2.19%、601318 偏移 1.74%。）

    为什么这个操作不贵：
    新浪日线接口每次请求都返回**整份历史文件**（akshare 再按日期裁剪），
    所以"重拉 5 年"与"补 30 天"的网络成本几乎相同，全量重建是很划算的。

    建议：每次发现持仓/候选股除权后，或每月跑一次。
    """
    storage = storage or Storage()
    fetcher = fetcher or DataFetcher()
    t0 = time.time()

    start = today_cn() - timedelta(days=365 * years)
    end = today_cn()
    codes = sorted(p.stem for p in storage.bars_dir.glob("*.parquet"))

    stats = {
        "mode": "rebuild",
        "total": len(codes),
        "updated": 0,
        "failed": 0,
        "errors": [],
        "elapsed": 0.0,
    }
    _notify(progress, done=0, total=len(codes), ok=0, failed=0, current="")

    lock = threading.Lock()
    done = ok = failed = 0

    def fetch_one(code: str) -> tuple[str, bool, str]:
        try:
            bars = fetcher.get_daily_bars(code, start, end)
            if bars is None or bars.empty:
                return code, False, "返回空数据"
            storage.save_bars(code, bars)  # save_bars 为整体覆盖写，正是重建所需
            return code, True, ""
        except Exception as e:
            return code, False, str(e)

    with keepalive_requests(timeout=30), ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, success, err = fut.result()
            with lock:
                done += 1
                if success:
                    ok += 1
                else:
                    failed += 1
                    stats["errors"].append((code, err))
                _notify(
                    progress,
                    done=done,
                    total=len(codes),
                    ok=ok,
                    failed=failed,
                    current=code,
                )

    stats["updated"] = ok
    stats["failed"] = failed
    stats["errors"] = stats["errors"][:50]
    stats["elapsed"] = round(time.time() - t0, 1)
    _log_run("rebuild", stats)
    return stats


# ---------- 内部工具 ----------

def _last_date(storage: Storage, code: str) -> pd.Timestamp | None:
    """读取某只股票本地缓存的最新日期（只读 date 列，避免整文件解析）。"""
    try:
        return storage.last_date(code)
    except Exception:
        return None


def _notify(progress: ProgressFn | None, **kw) -> None:
    if progress:
        try:
            progress(**kw)
        except Exception:
            pass


def _write_progress(payload: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        PROGRESS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def make_progress_writer(mode: str) -> ProgressFn:
    """生成把实时进度落到 JSON 文件的回调。

    更新任务在独立子进程中运行（akshare 的 py_mini_racer 在某些环境下会
    致命崩溃拖垮宿主进程），UI 通过轮询该文件获取进度与结果。
    """
    def _cb(**kw) -> None:
        _write_progress({"mode": mode, "running": True, **kw})
    return _cb


def _log_run(kind: str, stats: dict) -> None:
    """把每次运行摘要追加到 data/logs/update.log，方便排查定时任务是否正常。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        line = (
            f"{now_cn().isoformat(timespec='seconds')} [{kind}] "
            f"updated={stats.get('updated', 0)} failed={stats.get('failed', 0)} "
            f"uncached={len(stats.get('uncached', []))} elapsed={stats.get('elapsed', 0)}s"
            + (f" errors={stats['errors'][:3]}" if stats.get("errors") else "")
        )
        with (LOG_DIR / "update.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
