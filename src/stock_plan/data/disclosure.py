# -*- coding: utf-8 -*-
"""财报披露日（B 档字段补齐）：真实公告日 + 法定截止日兜底。

文件：src/stock_plan/data/disclosure.py
作者：Cwywwy
用途：为财务因子提供「截至某日已经公开」的财报口径，替代 `fundamentals.updated_at`
模块：data

为什么需要它：`meta.db` 的 `fundamentals.updated_at` 是**抓取时刻**（26 字符微秒 ISO 时间戳），
不是公告披露日；直接用它做时点过滤等于给回测开了未来函数——2024 年报可能 2025-04-28 才公告，
但数据在 2025-01 就被「当作已知」。本模块解决三件事：

1. **真实披露日**：akshare `stock_yysj_em`（东方财富「预约披露时间」）给出每只股票的
   首次预约日、历次变更日、实际披露日 → 我们取「最晚一次变更日 / 实际披露日」中较晚者为
   `announce_date`（保守：宁可晚一天，不提前）。
2. **法定截止日兜底**：接口缺失时按交易所规则给截止日——一季报 04-30、半年报 08-31、
   三季报 10-31、年报次年 04-30。**截止日 ≥ 真实公告日**，所以用它当「已知日」不会引入未来函数，
   只是把信息可用时间推晚了点（保守方向，可接受）。
3. **时点查询**：`as_of_disclosure(code, date)` 返回截至 date 最近一期已公开的财报日。

落盘（只增不改，upsert 覆盖同键）::

    data/db/meta.db -> report_disclosure(code, report_period, announce_date, actual_date, source, updated_at)

Revision History:
    2026-10-29  新建：披露日抓取、法定截止日兜底、时点查询
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from stock_plan.data.clock import stamp_cn, today_cn
from stock_plan.data.storage import DB_PATH

DISCLOSURE_COLUMNS = [
    "code",
    "report_period",
    "announce_date",
    "actual_date",
    "source",
    "updated_at",
]

# 财报报告期（'YYYYMMDD'）→ 法定最晚披露日（月-日）；年报跨年
_QUARTER_DEADLINE = {
    "0331": ("04-30", 0),  # 一季报：当年 4 月 30 日
    "0630": ("08-31", 0),  # 半年报：当年 8 月 31 日
    "0930": ("10-31", 0),  # 三季报：当年 10 月 31 日
    "1231": ("04-30", 1),  # 年报：次年 4 月 30 日
}

# 回补的默认区间：本项目日线从 2021-09-06 开始，取 2021 年报 ~ 2026 三季报
DEFAULT_START_PERIOD = "20211231"
DEFAULT_END_PERIOD = "20260930"


def norm_code(raw) -> str:
    """归一成 6 位数字代码（去前缀/后缀）。"""
    text = str(raw).strip().lower()
    for prefix in ("sh.", "sz.", "bj."):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if "." in text:
        text = text.split(".")[0]
    if not text.isdigit():
        raise ValueError(f"非法股票代码：{raw!r}")
    return text.zfill(6)


def report_periods(start: str = DEFAULT_START_PERIOD, end: str = DEFAULT_END_PERIOD) -> list[str]:
    """枚举报告期（每季度末，'YYYYMMDD'，含端点）。"""
    periods: list[str] = []
    year = int(str(start)[:4])
    while True:
        for month_day in ("0331", "0630", "0930", "1231"):
            period = f"{year}{month_day}"
            if str(start) <= period <= str(end):
                periods.append(period)
        year += 1
        if f"{year}0331" > str(end):
            break
    return periods


def statutory_deadline(period: str) -> str:
    """报告期 → 法定最晚披露日（'YYYY-MM-DD'）。"""
    period = str(period).strip()
    if len(period) != 8 or not period.isdigit():
        raise ValueError(f"非法报告期：{period!r}（应为 YYYYMMDD）")
    month_day = period[4:]
    if month_day not in _QUARTER_DEADLINE:
        raise ValueError(f"非法报告期：{period!r}（季度末必须是 0331/0630/0930/1231）")
    deadline_md, year_offset = _QUARTER_DEADLINE[month_day]
    year = int(period[:4]) + year_offset
    return f"{year}-{deadline_md}"


def _parse_day(value) -> str:
    """把各种日期写法归一为 'YYYY-MM-DD'；无法解析返回空串。"""
    if value is None or value is pd.NaT:
        return ""
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text or text in {"-", "--", "NaT", "nan", "None", "0000-00-00"}:
        return ""
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _latest(*days: str) -> str:
    """取最晚的非空日期（保守口径：信息可用时间取更晚者）。"""
    valid = [day for day in days if day]
    return max(valid) if valid else ""


# ---------------------------------------------------------------- 抓取


def fetch_disclosure(period: str, timeout: float | None = None) -> pd.DataFrame:
    """抓一期（报告期）全市场披露日。

    参数：
        period: 'YYYYMMDD'，季度末日期。
        timeout: 预留（akshare 内部走 requests，超时不可直接注入）。

    返回：`code, report_period, announce_date, actual_date, source, updated_at`。
        `announce_date` = max(实际披露日, 最后一次预约变更日)；
        `actual_date` = 实际披露日（可能为空）。
    异常：akshare 拉取失败时抛 RuntimeError，由调用方决定跳过还是中止。
    """
    period = str(period).strip()
    statutory_deadline(period)  # 顺带校验报告期合法性
    try:
        import akshare as ak

        df = ak.stock_yysj_em(symbol="沪深A股", date=period)
    except Exception as exc:  # noqa: BLE001 - 网络/接口异常统一转成可读错误
        raise RuntimeError(f"{period} 披露日拉取失败：{type(exc).__name__}: {exc}") from exc
    if df is None or df.empty:
        return pd.DataFrame(columns=DISCLOSURE_COLUMNS)

    rename = {
        "股票代码": "code",
        "首次预约时间": "first_date",
        "一次变更日期": "change1",
        "二次变更日期": "change2",
        "三次变更日期": "change3",
        "实际披露时间": "actual_date",
    }
    frame = df.rename(columns=rename)
    if "code" not in frame.columns:
        raise RuntimeError(f"{period} 披露日返回缺少股票代码列：{list(df.columns)[:8]}")
    frame = frame[frame["code"].notna()].copy()
    frame["code"] = frame["code"].map(norm_code)
    for column in ("first_date", "change1", "change2", "change3", "actual_date"):
        if column in frame.columns:
            frame[column] = frame[column].map(_parse_day)
        else:
            frame[column] = ""
    frame["report_period"] = period
    frame["announce_date"] = frame.apply(
        lambda row: _latest(
            row["first_date"], row["change1"], row["change2"], row["change3"], row["actual_date"]
        ),
        axis=1,
    )
    frame["source"] = "akshare_yysj"
    frame["updated_at"] = stamp_cn()
    frame = frame[frame["announce_date"] != ""]
    return frame[DISCLOSURE_COLUMNS].reset_index(drop=True)


# ---------------------------------------------------------------- 落盘 / 读取


def _conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS report_disclosure (
            code TEXT NOT NULL,
            report_period TEXT NOT NULL,
            announce_date TEXT,
            actual_date TEXT,
            source TEXT,
            updated_at TEXT,
            PRIMARY KEY (code, report_period)
        )
        """
    )
    return conn


def save_disclosure(df: pd.DataFrame, db_path: Path | str | None = None) -> int:
    """按 (code, report_period) upsert，返回写入行数。"""
    if df is None or df.empty:
        return 0
    rows = [
        (
            str(row["code"]),
            str(row["report_period"]),
            str(row["announce_date"]),
            str(row.get("actual_date") or ""),
            str(row.get("source") or "akshare_yysj"),
            str(row.get("updated_at") or stamp_cn()),
        )
        for _, row in df.iterrows()
    ]
    with _conn(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO report_disclosure "
            "(code, report_period, announce_date, actual_date, source, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def load_disclosure(
    period: str | None = None,
    codes: list[str] | None = None,
    db_path: Path | str | None = None,
) -> pd.DataFrame:
    """读披露日（可按报告期/代码过滤）；无记录返回空表。"""
    sql = "SELECT code, report_period, announce_date, actual_date, source, updated_at FROM report_disclosure"
    clauses: list[str] = []
    params: list[str] = []
    if period:
        clauses.append("report_period = ?")
        params.append(str(period))
    if codes:
        placeholders = ",".join("?" for _ in codes)
        clauses.append(f"code IN ({placeholders})")
        params.extend(str(code) for code in codes)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY code, report_period"
    with _conn(db_path) as conn:
        try:
            frame = pd.read_sql_query(sql, conn, params=params)
        except pd.errors.DatabaseError:
            return pd.DataFrame(columns=DISCLOSURE_COLUMNS)
    if frame.empty:
        return pd.DataFrame(columns=DISCLOSURE_COLUMNS)
    return frame


def stored_periods(db_path: Path | str | None = None) -> list[str]:
    """已入库的报告期（升序）。"""
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT report_period FROM report_disclosure ORDER BY report_period"
        ).fetchall()
    return [str(row[0]) for row in rows]


def coverage(db_path: Path | str | None = None) -> dict:
    """覆盖率概况（供回补脚本与报告使用）。"""
    with _conn(db_path) as conn:
        total, codes, periods = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT code), COUNT(DISTINCT report_period) FROM report_disclosure"
        ).fetchone()
        with_actual = conn.execute(
            "SELECT COUNT(*) FROM report_disclosure WHERE actual_date IS NOT NULL AND actual_date != ''"
        ).fetchone()[0]
    return {
        "rows": int(total or 0),
        "codes": int(codes or 0),
        "periods": int(periods or 0),
        "with_actual_date": int(with_actual or 0),
    }


def refresh_disclosure(
    periods: list[str] | None = None,
    *,
    dry_run: bool = False,
    skip_existing: bool = True,
    sleep_sec: float = 1.0,
    db_path: Path | str | None = None,
    verbose: bool = True,
) -> dict:
    """批量回补披露日（默认回补 2021Q4~2026Q3，跳过已入库报告期）。"""
    import time

    todo = periods or report_periods()
    if skip_existing:
        done = set(stored_periods(db_path))
        todo = [period for period in todo if period not in done]

    stats = {"requested": len(todo), "saved": 0, "rows": 0, "failed": [], "skipped": []}
    if dry_run:
        stats["skipped"] = todo
        return stats

    for index, period in enumerate(todo):
        try:
            frame = fetch_disclosure(period)
        except RuntimeError as exc:
            stats["failed"].append({"period": period, "error": str(exc)})
            if verbose:
                print(f"  [{period}] 失败：{exc}")
            continue
        if frame.empty:
            stats["skipped"].append(period)
            if verbose:
                print(f"  [{period}] 无数据")
            continue
        rows = save_disclosure(frame, db_path)
        stats["saved"] += 1
        stats["rows"] += rows
        if verbose:
            print(f"  [{period}] 写入 {rows} 行（{frame['code'].nunique()} 只）")
        if index + 1 < len(todo) and sleep_sec > 0:
            time.sleep(sleep_sec)
    if verbose:
        print(f"披露日回补：成功 {stats['saved']} 期 / {stats['rows']} 行，失败 {len(stats['failed'])} 期")
    return stats


# ---------------------------------------------------------------- 时点查询


def as_of_disclosure(
    code: str,
    date,
    *,
    db_path: Path | str | None = None,
) -> dict:
    """截至 date 最近一期已公开财报的披露情况。

    返回 dict：
        value: 'YYYY-MM-DD' 或 None —— 可用的「信息已知日」
        report_period: 'YYYYMMDD' 或 None
        source: 'announce'（真实/预约披露日）| 'statutory'（法定截止日兜底）| None
        degraded: 中文说明（None 表示未降级）

    口径：先找 announce_date ≤ date 的真实记录；没有则用「法定截止日 ≤ date」的最近报告期兜底；
    仍没有则返回 None + degraded 说明。
    """
    code = norm_code(code)
    target = _parse_day(date)
    if not target:
        return {"value": None, "report_period": None, "source": None,
                "degraded": f"非法日期：{date!r}"}

    frame = load_disclosure(codes=[code], db_path=db_path)
    if not frame.empty:
        known = frame[frame["announce_date"].map(_parse_day) <= target]
        if not known.empty:
            latest = known.sort_values(["announce_date", "report_period"]).iloc[-1]
            return {
                "value": _parse_day(latest["announce_date"]),
                "report_period": str(latest["report_period"]),
                "source": "announce",
                "degraded": None,
            }

    usable = [period for period in report_periods() if statutory_deadline(period) <= target]
    if usable:
        period = max(usable)
        return {
            "value": statutory_deadline(period),
            "report_period": period,
            "source": "statutory",
            "degraded": f"{code} 缺真实披露日，用 {period} 法定截止日 {statutory_deadline(period)} 兜底",
        }
    return {
        "value": None,
        "report_period": None,
        "source": None,
        "degraded": f"{code} 在 {target} 之前无任何可确认的财报披露日",
    }


def _main(argv: list[str] | None = None) -> int:
    """命令行：回补披露日（幂等，可 --dry-run）。"""
    import argparse
    import json

    parser = argparse.ArgumentParser(description="回补财报披露日")
    parser.add_argument("--dry-run", action="store_true", help="只列出待回补报告期")
    parser.add_argument("--period", action="append", help="指定报告期（可多次），默认全部")
    parser.add_argument("--force", action="store_true", help="已入库报告期也重抓")
    parser.add_argument("--sleep", type=float, default=1.0, help="每期间隔秒数")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出统计")
    args = parser.parse_args(argv)

    periods = [p for p in (args.period or [])] or None
    stats = refresh_disclosure(
        periods,
        dry_run=args.dry_run,
        skip_existing=not args.force,
        sleep_sec=args.sleep,
        verbose=not args.json,
    )
    if args.json:
        print(json.dumps({**stats, "coverage": coverage()}, ensure_ascii=False, default=str))
    return 0 if not stats["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
