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
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from stock_plan.data.fetcher import DataFetcher
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
    today_str = date.today().isoformat()
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
        rng = pd.bdate_range(end=date.today() + timedelta(days=365), periods=730)
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
    now = now or datetime.now()
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

def update_incremental(
    days: int = 30,
    workers: int = 8,
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
    start = date.today() - timedelta(days=days)
    end = date.today()

    # 股票列表：优先在线刷新（顺带捕获新股），失败退回本地
    try:
        stock_list = fetcher.get_stock_list()
        storage.save_stock_list(stock_list)
    except Exception as e:
        logger.warning("在线股票列表拉取失败，使用本地列表: %s", e)
        stock_list = storage.load_stock_list()

    all_codes = stock_list["code"].astype(str).tolist() if not stock_list.empty else []
    cached = [c for c in all_codes if storage.cache_exists(c)]
    uncached = [c for c in all_codes if not storage.cache_exists(c)]

    # 幂等预筛：本地已到期望交易日的直接跳过（只读本地文件，不联网）
    todo = []
    for c in cached:
        last = _last_date(storage, c)
        if not force and last is not None and last >= expected:
            continue
        todo.append(c)

    stats = {
        "expected_date": str(expected.date()),
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

        with ThreadPoolExecutor(max_workers=workers) as pool:
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
    _log_run("incremental", stats)
    return stats


def fetch_uncached(
    codes: list[str],
    years: int = 5,
    workers: int = 8,
    storage: Storage | None = None,
    fetcher: DataFetcher | None = None,
    progress: ProgressFn | None = None,
) -> dict:
    """全量拉取指定未缓存股票（近 years 年日线 + 财务），供手动补新股票用。"""
    storage = storage or Storage()
    fetcher = fetcher or DataFetcher()
    t0 = time.time()
    start = date.today() - timedelta(days=365 * years)
    end = date.today()

    stats = {"total": len(codes), "updated": 0, "failed": 0, "errors": [], "elapsed": 0.0}
    _notify(progress, done=0, total=len(codes), ok=0, failed=0, current="")

    lock = threading.Lock()
    done = ok = failed = 0

    def fetch_one(code: str) -> tuple[str, bool, str]:
        try:
            bars = fetcher.get_daily_bars(code, start, end)
            storage.save_bars(code, bars)
            try:
                storage.save_fundamentals(code, fetcher.get_fundamentals(code))
            except Exception:
                pass  # 财务失败不影响日线入库
            return code, True, ""
        except Exception as e:
            return code, False, str(e)

    with ThreadPoolExecutor(max_workers=workers) as pool:
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


# ---------- 内部工具 ----------

def _last_date(storage: Storage, code: str) -> pd.Timestamp | None:
    """读取某只股票本地缓存的最新日期（只读 date 列附近，快速判断）。"""
    try:
        df = storage.load_bars(code)
        if df.empty:
            return None
        return pd.Timestamp(df["date"].max())
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
            f"{datetime.now().isoformat(timespec='seconds')} [{kind}] "
            f"updated={stats.get('updated', 0)} failed={stats.get('failed', 0)} "
            f"uncached={len(stats.get('uncached', []))} elapsed={stats.get('elapsed', 0)}s"
            + (f" errors={stats['errors'][:3]}" if stats.get("errors") else "")
        )
        with (LOG_DIR / "update.log").open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
