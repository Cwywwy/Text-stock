"""数据存储模块 — 本地缓存层。

设计：
- 日线行情 → Parquet 文件（data/processed/bars/{code}.parquet），列式存储、读取快
- 财务指标 → SQLite 数据库（data/db/meta.db），键值式存储，便于按代码查询
- 股票列表 → SQLite 数据库（data/db/meta.db）的 stock_list 表

这样做的原因：
- Parquet 比 CSV 体积小、读取快，适合存大量日线数据
- SQLite 单文件、无需安装数据库服务，适合存元数据
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

# 项目根目录（src/stock_plan/data/storage.py 向上 3 级 = 项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BARS_DIR = PROJECT_ROOT / "data" / "processed" / "bars"
DB_PATH = PROJECT_ROOT / "data" / "db" / "meta.db"

# 日线标准字段（与 fetcher.get_daily_bars 返回一致）
BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "turnover"]


class Storage:
    """本地缓存：日线存 Parquet，财务/列表存 SQLite。"""

    def __init__(self, bars_dir: Path | str | None = None, db_path: Path | str | None = None):
        self.bars_dir = Path(bars_dir) if bars_dir else BARS_DIR
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.bars_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ---------- 数据库初始化 ----------
    def _init_db(self) -> None:
        """建表（若不存在）。"""
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_list (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    industry TEXT,
                    list_date TEXT,
                    is_st INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fundamentals (
                    code TEXT PRIMARY KEY,
                    roe REAL,
                    revenue_growth REAL,
                    net_profit_growth REAL,
                    gross_margin REAL,
                    debt_ratio REAL,
                    eps REAL,
                    bvps REAL,
                    pe REAL,
                    pb REAL,
                    updated_at TEXT
                )
                """
            )

    def _conn(self) -> sqlite3.Connection:
        """打开数据库连接（每次调用新建，避免线程/进程共享问题）。"""
        return sqlite3.connect(self.db_path)

    # ---------- 日线行情（Parquet） ----------
    def _bars_path(self, code: str) -> Path:
        return self.bars_dir / f"{code}.parquet"

    def save_bars(self, code: str, df: pd.DataFrame) -> None:
        """保存日线行情到 Parquet。df 需含 BAR_COLUMNS 字段。"""
        if df is None or df.empty:
            return
        df = df[BAR_COLUMNS].copy()
        df["date"] = pd.to_datetime(df["date"])
        df.to_parquet(self._bars_path(code), index=False)

    def load_bars(self, code: str) -> pd.DataFrame:
        """读取日线行情。若不存在返回空 DataFrame。"""
        path = self._bars_path(code)
        if not path.exists():
            return pd.DataFrame(columns=BAR_COLUMNS)
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        return df

    def cache_exists(self, code: str) -> bool:
        """判断该股票日线缓存是否存在。"""
        return self._bars_path(code).exists()

    def bars_count(self) -> int:
        """已缓存日线的股票数量。"""
        return len(list(self.bars_dir.glob("*.parquet")))

    # ---------- 财务指标（SQLite） ----------
    def save_fundamentals(self, code: str, data: dict) -> None:
        """保存财务指标。data 为 fetcher.get_fundamentals 返回的 dict。"""
        keys = [
            "roe", "revenue_growth", "net_profit_growth", "gross_margin",
            "debt_ratio", "eps", "bvps", "pe", "pb",
        ]
        values = {k: data.get(k) for k in keys}
        values["code"] = code
        values["updated_at"] = pd.Timestamp.now().isoformat()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO fundamentals
                (code, roe, revenue_growth, net_profit_growth, gross_margin,
                 debt_ratio, eps, bvps, pe, pb, updated_at)
                VALUES (:code, :roe, :revenue_growth, :net_profit_growth, :gross_margin,
                        :debt_ratio, :eps, :bvps, :pe, :pb, :updated_at)
                """,
                values,
            )

    def load_fundamentals(self, code: str) -> dict | None:
        """读取财务指标。不存在返回 None。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM fundamentals WHERE code = ?", (code,)
            ).fetchone()
        if row is None:
            return None
        cols = [d[0] for d in conn.execute("SELECT * FROM fundamentals").description]
        return dict(zip(cols, row))

    # ---------- 股票列表（SQLite） ----------
    def save_stock_list(self, df: pd.DataFrame) -> None:
        """保存全 A 股列表。df 需含 code/name/industry/list_date/is_st 字段。"""
        with self._conn() as conn:
            conn.execute("DELETE FROM stock_list")
            conn.executemany(
                """
                INSERT OR REPLACE INTO stock_list (code, name, industry, list_date, is_st)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(r.code),
                        str(r.name),
                        str(r.industry) if pd.notna(r.industry) else "",
                        str(r.list_date) if pd.notna(r.list_date) else "",
                        1 if r.is_st else 0,
                    )
                    for r in df.itertuples(index=False)
                ],
            )

    def load_stock_list(self) -> pd.DataFrame:
        """读取全 A 股列表。"""
        with self._conn() as conn:
            df = pd.read_sql_query("SELECT * FROM stock_list", conn)
        return df


if __name__ == "__main__":
    # 简单自测：写入并读回（使用临时目录，避免覆盖真实缓存数据）
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(bars_dir=tmp, db_path=tmp + "/meta.db")
        test_df = pd.DataFrame(
            {
                "date": pd.to_datetime(["2025-01-02", "2025-01-03"]),
                "open": [9.55, 9.38],
                "high": [9.66, 9.47],
                "low": [9.31, 9.29],
                "close": [9.39, 9.32],
                "volume": [78907129.0, 50693944.0],
                "amount": [803904040.0, 511576494.0],
                "turnover": [0.002688, 0.001727],
            }
        )
        storage.save_bars("600000", test_df)
        loaded = storage.load_bars("600000")
        print("日线缓存存在:", storage.cache_exists("600000"))
        print("读回条数:", len(loaded))
        print(loaded.head(2).to_string())

        storage.save_fundamentals("600000", {"roe": 3.96, "eps": 0.89, "bvps": 22.63})
        fin = storage.load_fundamentals("600000")
        print("财务指标:", fin)