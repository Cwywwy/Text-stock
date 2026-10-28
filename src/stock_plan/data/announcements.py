# -*- coding: utf-8 -*-
"""公告抓取与存档：持仓 + 自选，日频。

为什么要独立一个 sqlite（`data/processed/announcements.db`）：
- `meta.db` 是行情/基本面的主库，应用里每页都在读它；公告是"只增不改"的日志，
  塞进去会让主库膨胀、还多一份写锁风险。
- `artifacts/` 下已有 strategies.db / paper.db 的先例，按用途分库是本项目既定做法。

**去重靠主键** `(code, ann_date, title)` + `INSERT OR IGNORE`：同一份公告每天重复抓
不会产生重复行，所以定时任务可以放心天天跑、也可以对任意区间重跑。

**为什么按股票抓，而不是按日期抓全市场**：用户明确范围是"持仓/自选"。全市场公告
一天几千条，绝大部分与用户无关；且全市场接口要翻页、更容易被限流。按股票抓实测
约 1.0 秒/只，20 只关注票 = 20 秒，完全可接受。

关注清单 = **模拟盘持仓**（自动读 `paper.db` 的 positions 表）+ **手动自选**（watch 表）。
"""
from __future__ import annotations

import sqlite3
from datetime import timedelta
from pathlib import Path

import pandas as pd

from stock_plan.data.clock import stamp_cn, today_cn

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ANN_DB = PROJECT_ROOT / "data" / "processed" / "announcements.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS announcements (
    code       TEXT NOT NULL,
    ann_date   TEXT NOT NULL,
    title      TEXT NOT NULL,
    ann_type   TEXT,
    url        TEXT,
    fetched_at TEXT,
    PRIMARY KEY (code, ann_date, title)
);
CREATE TABLE IF NOT EXISTS watch (
    code     TEXT PRIMARY KEY,
    added_at TEXT,
    note     TEXT
);
CREATE INDEX IF NOT EXISTS idx_ann_date ON announcements(ann_date);
"""


def norm_code(raw) -> str:
    """归一成 6 位代码；非 6 位数字的输入直接拒绝（避免把垃圾写进关注表）。"""
    s = str(raw).strip().replace("sh.", "").replace("sz.", "").replace("bj.", "")
    if "." in s:
        s = s.split(".")[0]
    if not s.isdigit():
        raise ValueError(f"非法股票代码：{raw!r}")
    return s.zfill(6)


def _conn() -> sqlite3.Connection:
    ANN_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ANN_DB)
    conn.executescript(_SCHEMA)
    return conn


def _close(conn: sqlite3.Connection) -> None:
    """显式关闭。

    项目里 `store._conn()` 用 `with sqlite3.connect(...)` 只提交事务、不关连接
    （已记录为小隐患）。新代码不再沿用这个写法。
    """
    try:
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- 关注清单


def holdings_codes() -> list[str]:
    """模拟盘当前持仓的代码。读不到（没跑过模拟盘 / 文件被占）就返回空。"""
    from stock_plan.simulator.paper import PAPER_DB

    p = Path(PAPER_DB)
    if not p.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        try:
            rows = conn.execute("SELECT code FROM positions").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return sorted({norm_code(r[0]) for r in rows if r[0]})


def watch_codes() -> list[str]:
    conn = _conn()
    try:
        rows = conn.execute("SELECT code FROM watch ORDER BY added_at").fetchall()
    finally:
        _close(conn)
    return [r[0] for r in rows]


def tracked_codes() -> list[str]:
    """持仓 ∪ 自选，去重排序。日频任务抓这个集合。"""
    return sorted(set(holdings_codes()) | set(watch_codes()))


def add_watch(code: str, note: str = "") -> str:
    c = norm_code(code)
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO watch(code, added_at, note) VALUES (?, ?, ?)",
            (c, stamp_cn("%Y-%m-%dT%H:%M:%S"), note),
        )
    finally:
        _close(conn)
    return c


def remove_watch(code: str) -> bool:
    c = norm_code(code)
    conn = _conn()
    try:
        cur = conn.execute("DELETE FROM watch WHERE code = ?", (c,))
        deleted = cur.rowcount > 0
    finally:
        _close(conn)
    return deleted


# ---------------------------------------------------------------- 抓取/存档


def fetch_one(code: str, days: int = 7) -> pd.DataFrame:
    """抓单只股票的公告（东财个股公告，实测约 1.0 秒/只）。"""
    import akshare as ak

    end = today_cn()
    begin = end - timedelta(days=max(1, int(days)))
    df = ak.stock_individual_notice_report(
        security=norm_code(code),
        symbol="全部",
        begin_date=begin.strftime("%Y%m%d"),
        end_date=end.strftime("%Y%m%d"),
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["code", "ann_date", "title", "ann_type", "url"])
    df = df.rename(
        columns={"代码": "code", "公告标题": "title", "公告类型": "ann_type",
                 "公告日期": "ann_date", "网址": "url"}
    )
    keep = [c for c in ("code", "ann_date", "title", "ann_type", "url") if c in df.columns]
    df = df[keep].copy()
    df["code"] = df["code"].map(norm_code)
    df["ann_date"] = df["ann_date"].astype(str).str[:10]
    df["title"] = df["title"].astype(str).str.strip()
    return df


def save(df: pd.DataFrame) -> int:
    """写入并存档，返回**新增行数**（重复公告被主键挡掉，不算新增）。"""
    if df is None or df.empty:
        return 0
    now = stamp_cn("%Y-%m-%dT%H:%M:%S")
    rows = [
        (str(r["code"]), str(r["ann_date"]), str(r["title"]),
         str(r.get("ann_type", "") or ""), str(r.get("url", "") or ""), now)
        for _, r in df.iterrows()
        if str(r.get("title", "")).strip()
    ]
    if not rows:
        return 0
    conn = _conn()
    try:
        before = conn.total_changes
        conn.executemany(
            "INSERT OR IGNORE INTO announcements"
            "(code, ann_date, title, ann_type, url, fetched_at) VALUES (?,?,?,?,?,?)",
            rows,
        )
        added = conn.total_changes - before
    finally:
        _close(conn)
    return added


def capture(codes: list[str] | None = None, days: int = 7, quiet: bool = False) -> dict:
    """抓取关注清单（默认）的公告并存档。

    单只失败不中断整轮 —— 记进 errors，下次跑会补上（主键去重，重抓无副作用）。
    """
    targets = [norm_code(c) for c in codes] if codes else tracked_codes()
    result = {"codes": len(targets), "rows": 0, "added": 0, "errors": []}
    for i, c in enumerate(targets, 1):
        try:
            df = fetch_one(c, days=days)
        except Exception as exc:  # noqa: BLE001
            result["errors"].append(f"{c}: {type(exc).__name__}: {str(exc)[:80]}")
            if not quiet:
                print(f"  [{i}/{len(targets)}] {c} 失败：{type(exc).__name__}")
            continue
        added = save(df)
        result["rows"] += len(df)
        result["added"] += added
        if not quiet:
            print(f"  [{i}/{len(targets)}] {c}: {len(df)} 条（新增 {added}）")
    if not quiet:
        print(
            f"完成：{result['codes']} 只、抓到 {result['rows']} 条、新增 {result['added']} 条"
            + (f"、失败 {len(result['errors'])} 只" if result["errors"] else "")
        )
    return result


# ---------------------------------------------------------------- 读取（给 UI / LLM）


def load(code: str | None = None, since: str | None = None, limit: int = 200) -> pd.DataFrame:
    """按日期倒序读存档。`since` 支持 'YYYY-MM-DD'，用于周频统计。"""
    cols = ["code", "ann_date", "title", "ann_type", "url"]
    sql = f"SELECT {', '.join(cols)} FROM announcements WHERE 1=1"
    params: list = []
    if code:
        sql += " AND code = ?"
        params.append(norm_code(code))
    if since:
        sql += " AND ann_date >= ?"
        params.append(str(since)[:10])
    sql += " ORDER BY ann_date DESC, code LIMIT ?"
    params.append(int(limit))
    conn = _conn()
    try:
        df = pd.read_sql_query(sql, conn, params=params)
    finally:
        _close(conn)
    return df


def stats() -> dict:
    """存档概况：总条数、覆盖股票数、日期范围、最近一次抓取时间。"""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT code), MIN(ann_date), MAX(ann_date), MAX(fetched_at)"
            " FROM announcements"
        ).fetchone()
    finally:
        _close(conn)
    return {
        "rows": row[0] or 0,
        "codes": row[1] or 0,
        "first_date": row[2] or "",
        "last_date": row[3] or "",
        "last_fetched": row[4] or "",
    }


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    用法::

        python -m stock_plan.data.announcements                 # 抓关注清单（默认近 7 天）
        python -m stock_plan.data.announcements --days 30
        python -m stock_plan.data.announcements --codes 600000,000001
        python -m stock_plan.data.announcements --add 600000 --note 底仓
        python -m stock_plan.data.announcements --watch         # 列出关注清单
        python -m stock_plan.data.announcements --show          # 看存档概况
    """
    import argparse

    ap = argparse.ArgumentParser(description="公告抓取与存档（持仓 + 自选）")
    ap.add_argument("--days", type=int, default=7, help="回溯天数（默认 7）")
    ap.add_argument("--codes", default=None, help="只抓这些代码，逗号分隔")
    ap.add_argument("--add", default=None, help="加入自选")
    ap.add_argument("--note", default="", help="加入自选时的备注")
    ap.add_argument("--remove", default=None, help="从自选移除")
    ap.add_argument("--watch", action="store_true", help="列出关注清单")
    ap.add_argument("--show", action="store_true", help="查看存档概况")
    args = ap.parse_args(argv)

    if args.add:
        print(f"已加入自选：{add_watch(args.add, args.note)}")
        return 0
    if args.remove:
        print("已移除" if remove_watch(args.remove) else "不在自选里")
        return 0
    if args.watch:
        hold = set(holdings_codes())
        for c in watch_codes():
            print(f"{c}  自选{'（持仓中）' if c in hold else ''}")
        extra = sorted(hold - set(watch_codes()))
        for c in extra:
            print(f"{c}  持仓（未加入自选）")
        if not watch_codes() and not extra:
            print("关注清单为空：用 --add 加自选，或先在模拟交易里建仓")
        return 0
    if args.show:
        print(stats())
        df = load(limit=20)
        print(df.to_string(index=False) if not df.empty else "（暂无存档）")
        return 0

    codes = [c.strip() for c in args.codes.split(",")] if args.codes else None
    r = capture(codes=codes, days=args.days)
    return 0 if not r["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
