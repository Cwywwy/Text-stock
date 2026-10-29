"""数据存储模块 — 本地缓存层。

设计：
- 日线行情 → Parquet 文件（data/processed/bars/{code}.parquet），列式存储、读取快
- 财务指标 → SQLite 数据库（data/db/meta.db），键值式存储，便于按代码查询
- 股票列表 → SQLite 数据库（data/db/meta.db）的 stock_list 表

这样做的原因：
- Parquet 比 CSV 体积小、读取快，适合存大量日线数据
- SQLite 单文件、无需安装数据库服务，适合存元数据

Revision History:
    2026-09-04  add load_market_maps windowed loader for backtest/signal
    2026-10-15  在 load_bars 里剔除"零值占位行"（见 _sanitize_bars）
    2026-10-29  (phase 12) add share-capital/disclosure accessors for B-tier fields
    2026-10-29  (phase 15) parallelise load_market_maps reads; push date/price filters into pyarrow
"""
from __future__ import annotations

import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

# 项目根目录（src/stock_plan/data/storage.py 向上 3 级 = 项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# load_market_maps 的 parquet 读取并发度。
# 为什么是 8：本机 8 物理核 / 16 逻辑核。单个 parquet 的耗时主要是
# pyarrow 解码（在 C++ 侧释放 GIL）+ Python 侧 DataFrame 装配，并发 8
# 已是实测拐点：再往上加线程会与 pyarrow 自身的线程池抢核，不再变快。
# 保持有界（而不是按文件数开线程）是为了兼容云端 2 GB 轻量容器。
_BARS_LOAD_WORKERS = 8


def _sanitize_bars(df: pd.DataFrame) -> pd.DataFrame:
    """剔除**不可能的行情行**：`open`/`high`/`low` 出现 0 或负值。

    为什么需要（2026-10-15 端到端验收发现）：
    数据源本身偶尔会给出停牌占位行。实测新浪 `stock_zh_a_daily` 对 3 只科创板股票
    （688089 / 688143 / 688173，全库 3/5890）在 2024-11-06 返回：

        date        open  high  low   close  volume  amount  turnover
        2024-11-06  0.00  0.00  0.00  前收   0.0     0.0     0.0

    （已直接重新拉取同区间确认：新浪返回里同样带这一行，不是本项目管线造的。）

    危害在于 ATR：`TR = max(high-low, |high-前收|, |low-前收|)`，
    当 `low = 0` 时 TR 变成 `|0 - 前收|`（≈20，而正常约 1）。这个尖峰进入 ATR 的 EMA
    （alpha=1/14）后会把 ATR **放大 1.5~1.8 倍并持续近一个月** —— 实测 688143 峰值
    3.812 vs 干净的 2.178。乘上默认的 `m=n=3.5`，止损位被拉远约 5.7 元
    （对 25 元的股票≈23%），单笔风险敞口被严重误算。图上也表现为掉到 0 的尖刺。

    真实停牌日本就没有 K 线，因此**剔除**是语义正确的做法（不是填值）。
    缓存文件保留原始行以便追溯，这里只在**读取时**过滤。
    """
    if df is None or df.empty:
        return df
    price_cols = [c for c in ("open", "high", "low") if c in df.columns]
    if not price_cols:
        return df
    bad = (df[price_cols] <= 0).any(axis=1)
    if not bad.any():
        return df
    return df.loc[~bad].reset_index(drop=True)


def _read_bar_window_frame(
    path: Path,
    lo: pd.Timestamp | None,
    hi: pd.Timestamp | None,
) -> pd.DataFrame | None:
    """读取单个日线 parquet，裁剪到 [lo, hi] 并剔除非法价位行。

    为什么不直接 `load_bars` + pandas 过滤（Phase 15 实测结论）：`load_bars`
    每只股票都要做一次 `pd.to_datetime` 和 pandas 级布尔过滤，这些是
    Python/GIL 绑定的工作，放进线程池也并行不起来（8 线程实测只快约 8%）。
    这里把「日期窗口过滤」和「剔除 open/high/low ≤ 0 的占位行」下推到
    pyarrow 计算层（C++ 执行、释放 GIL），线程池才有真实并行度。

    语义与 `_sanitize_bars(load_bars(code))` 再按 pandas 过滤窗口**逐值等价**：
    - 窗口：pandas 里 `date >= lo` 会丢掉 NaT，这里 timestamp 比较后 `Table.filter`
      同样丢弃 null 掩码行；
    - 占位行：pandas 里 `open/high/low <= 0`（NaN 比较为 False → 保留），
      这里 `pc.less_equal` + `fill_null(False)`，NaN 不会被误删；
    - 输出列顺序/数值/index 与串行实现一致（index 统一 reset）。

    返回 None 表示「窗口内没有任何行」，对应串行实现里跳过该 code。
    """
    import numpy as np
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    table = pq.read_table(path)
    date_type = table["date"].type if "date" in table.column_names else None
    if date_type is None or not pa.types.is_timestamp(date_type) or date_type.tz is not None:
        # 非标准 schema（正常管线不会出现）：回退 pandas 路径，语义与 load_bars 一致
        frame = table.to_pandas()
        frame["date"] = pd.to_datetime(frame["date"])
        frame = _sanitize_bars(frame)
        if lo is not None:
            frame = frame[frame["date"] >= lo]
        if hi is not None:
            frame = frame[frame["date"] <= hi]
        if frame.empty:
            return None
        return frame.reset_index(drop=True)

    date_idx = table.column_names.index("date")
    # 统一升到 timestamp[ns] 再比较：毫秒/微秒/纳秒列都能与 ns 标量对齐，也无精度损失
    dates_ns = pc.cast(table["date"], pa.timestamp("ns"))
    mask = None
    if lo is not None:
        mask = pc.greater_equal(
            dates_ns, pa.scalar(np.datetime64(lo.value, "ns"), type=pa.timestamp("ns"))
        )
    if hi is not None:
        hi_mask = pc.less_equal(
            dates_ns, pa.scalar(np.datetime64(hi.value, "ns"), type=pa.timestamp("ns"))
        )
        mask = hi_mask if mask is None else pc.and_(mask, hi_mask)

    bad = None
    for name in ("open", "high", "low"):
        if name in table.column_names:
            col = table[name]
            if not pa.types.is_floating(col.type):
                col = pc.cast(col, pa.float64())
            is_bad = pc.fill_null(pc.less_equal(col, pa.scalar(0.0)), False)
            bad = is_bad if bad is None else pc.or_(bad, is_bad)
    if bad is not None:
        keep = pc.invert(bad)
        mask = keep if mask is None else pc.and_(mask, keep)

    if mask is not None:
        table = table.filter(mask)
        # 把日期列还原成文件原本的时间单位（如 timestamp[ms]），保证 dtype 一致
        table = table.set_column(date_idx, "date", pc.cast(table["date"], date_type))
    frame = table.to_pandas()
    if frame.empty:
        return None
    return frame.reset_index(drop=True)


BARS_DIR = PROJECT_ROOT / "data" / "processed" / "bars"
DB_PATH = PROJECT_ROOT / "data" / "db" / "meta.db"
# 股本/市值快照与历史表目录（B 档字段；实现在 data/share_capital.py）
SHARES_DIR = PROJECT_ROOT / "data" / "processed" / "shares"

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
            # 退市股元数据（暂时需求 5c）：用于消除回测的幸存者偏差
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS delisted_stocks (
                    code TEXT PRIMARY KEY,
                    name TEXT,
                    exchange TEXT,
                    list_date TEXT,
                    delist_date TEXT,
                    first_bar TEXT,
                    last_bar TEXT,
                    bars_count INTEGER,
                    source TEXT,
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
        """保存日线行情到 Parquet。df 需含 BAR_COLUMNS 字段。

        先写临时文件再 os.replace 原子替换：增量更新/全历史重建会在 Streamlit
        运行期间改写同一个文件，直接覆盖会让并发读取方读到写了一半的 parquet。
        """
        if df is None or df.empty:
            return
        df = df[BAR_COLUMNS].copy()
        df["date"] = pd.to_datetime(df["date"])
        target = self._bars_path(code)
        tmp = target.with_suffix(target.suffix + ".tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, target)  # 同目录内替换，POSIX/NTFS 上均为原子操作

    def load_bars(self, code: str) -> pd.DataFrame:
        """读取日线行情。若不存在返回空 DataFrame。

        读取时会**剔除不可能的行情行**（open/high/low 出现 0 或负值），见 `_sanitize_bars`。
        这是所有行情消费方的唯一入口，因此在此处清洗可覆盖回测、信号、图表等全部路径。
        """
        path = self._bars_path(code)
        if not path.exists():
            return pd.DataFrame(columns=BAR_COLUMNS)
        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"])
        return _sanitize_bars(df)

    def cache_exists(self, code: str) -> bool:
        """判断该股票日线缓存是否存在。"""
        return self._bars_path(code).exists()

    def last_date(self, code: str) -> pd.Timestamp | None:
        """只读取 date 列，返回该股票缓存的最新交易日。

        比 load_bars 整文件读取便宜：增量更新的幂等预筛要对全市场 5000+ 只股票
        逐个判断"是否已是最新"，整文件读会白白解析十余列数据。
        """
        path = self._bars_path(code)
        if not path.exists():
            return None
        try:
            col = pd.read_parquet(path, columns=["date"])
        except Exception:
            return None
        if col.empty:
            return None
        return pd.Timestamp(pd.to_datetime(col["date"]).max())

    def bars_count(self) -> int:
        """已缓存日线的股票数量。"""
        return len(list(self.bars_dir.glob("*.parquet")))

    # ---------- 回测/信号统一加载（内存受限环境友好） ----------
    def delisted_listing(
        self, start: pd.Timestamp | str | None = None, end: pd.Timestamp | str | None = None
    ) -> pd.DataFrame:
        """已退市股票构成的"股票列表"（字段与 stock_list 对齐）。

        只保留与 [start, end] 有重叠的退市股 —— 也就是在该回测窗口内还交易过的。
        这样回测样本才会包含"跌到退市的那批"，而不是只有今天还活着的股票
        （即消除幸存者偏差）。

        注意 is_st 一律给 0：退市股本来就必须留在样本里，不能因为 ST 规则被剔掉。
        """
        cols = ["code", "name", "industry", "list_date", "is_st"]
        empty = pd.DataFrame(columns=cols)
        d = self.load_delisted()
        if d.empty:
            return empty

        first = pd.to_datetime(d["first_bar"], errors="coerce")
        last = pd.to_datetime(d["last_bar"], errors="coerce")
        keep = pd.Series(True, index=d.index)
        if start is not None:
            lo = pd.Timestamp(start)
            keep &= last.isna() | (last >= lo)
        if end is not None:
            hi = pd.Timestamp(end)
            keep &= first.isna() | (first <= hi)
        if not keep.any():
            return empty

        out = d.loc[keep, ["code", "name", "list_date"]].copy()
        out["industry"] = ""
        out["is_st"] = 0
        return out[cols].reset_index(drop=True)

    def _load_bars_for_window(
        self,
        code: str,
        lo: pd.Timestamp | None,
        hi: pd.Timestamp | None,
    ) -> tuple[str, pd.DataFrame | None]:
        """读取单只股票的日线并裁剪到 [lo, hi]，供 load_market_maps 线程池调用。

        **线程安全约束**：只读 `self.bars_dir`；每个线程各自用
        `pyarrow.parquet.read_table` 打开/关闭自己的文件句柄，不共享任何可变
        状态（SQLite 连接、缓存对象都不在这里用）。pyarrow 的 C++ 读取实现
        本身是线程安全的，因此 8 个线程并发读不同文件不会互相干扰。

        返回 `(code, None)` 表示「无缓存 / 空数据 / 窗口内无行」，与串行实现里
        `continue` 跳过该 code 的语义一一对应；读取/清洗逻辑见
        `_read_bar_window_frame`。
        """
        path = self._bars_path(code)
        if not path.exists():
            return code, None
        frame = _read_bar_window_frame(path, lo, hi)
        if frame is None:
            return code, None
        return code, frame

    def load_market_maps(
        self,
        start: pd.Timestamp | str | None = None,
        end: pd.Timestamp | str | None = None,
        lead_days: int = 0,
        boards: list[str] | None = None,
        exclude_st: bool = True,
        max_codes: int | None = None,
        include_delisted: bool = False,
    ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, dict]]:
        """按需装配回测/信号数据（stock_list, bars_map, fund_map）。

        相比全量加载，这里做三件事控制内存占用：
        - 日期窗口：只加载 [start - lead_days, end] 区间的日线（lead_days 供指标预热）；
        - 预筛选：加载前先按板块/ST 过滤代码，避免读入用不到的股票数据；
        - 数量上限：max_codes 生效时对代码等间隔抽样（云端轻量模式使用）。

        start/end 为 None 时加载全历史，行为与旧版全量加载一致。

        并发读取（Phase 15）：逐 code 的 parquet 读取走有界线程池（见
        `_BARS_LOAD_WORKERS`），单文件的窗口过滤/价位清洗下推到 pyarrow 计算层，
        但对外语义与串行实现完全相同 ——
        - `bars_map` 的键顺序严格按 `codes` 顺序，内容逐值相同（与
          `load_bars` + pandas 窗口过滤的结果一致，测试里逐值对拍）；
        - 无缓存文件的 code 照旧跳过（文件不存在即跳过），不报错；
        - 文件损坏照旧抛出原始异常（**不**改成静默跳过，避免把数据损坏藏起来）；
        - 每个线程只读自己的文件、不共享可变对象，pyarrow 单文件读取线程安全。

        include_delisted=True 时把**已退市股票**也纳入样本（暂时需求 5c）。默认关闭，
        因为这会显著改变回测结果（原先的系统性高估被去掉），历史调参结论需要重跑。
        前置条件：回测引擎必须能处理"持仓期间行情终结"，该修复已完成。
        """
        stock_list = self.load_stock_list()
        if stock_list.empty:
            return stock_list, {}, {}

        # 预筛选语义与 factors.board.filter_universe_ui 一致：先过滤代码再加载
        codes = stock_list["code"].astype(str).tolist()
        if exclude_st and "is_st" in stock_list.columns:
            codes = stock_list.loc[stock_list["is_st"] != 1, "code"].astype(str).tolist()
        from stock_plan.factors.board import filter_codes_by_boards

        codes = filter_codes_by_boards(codes, boards)

        extra_list = pd.DataFrame()
        if include_delisted:
            extra_list = self.delisted_listing(start=start, end=end)
            if not extra_list.empty:
                extra_codes = filter_codes_by_boards(
                    extra_list["code"].astype(str).tolist(), boards
                )
                codes = sorted(set(codes) | set(extra_codes))

        if max_codes is not None and len(codes) > max_codes:
            step = -(-len(codes) // max_codes)  # 向上取整，等间隔抽样保留板块分布
            codes = codes[::step]

        lo = pd.Timestamp(start) - pd.Timedelta(days=lead_days) if start is not None else None
        hi = pd.Timestamp(end) if end is not None else None
        bars_map: dict[str, pd.DataFrame] = {}
        if codes:
            # 逐文件读取是 I/O + 解码密集、且每只股票互不依赖，用有界线程池并发；
            # 结果仍按 codes 顺序写入 bars_map（dict 插入顺序 = 串行实现完全一致）。
            workers = min(_BARS_LOAD_WORKERS, len(codes))
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="bars-load"
            ) as pool:
                futures = [
                    pool.submit(self._load_bars_for_window, code, lo, hi) for code in codes
                ]
                try:
                    for future in futures:
                        code, df = future.result()
                        if df is not None:
                            bars_map[code] = df
                except BaseException:
                    # 串行实现的语义是「第一个坏文件直接抛异常」，这里保持：
                    # 按 codes 顺序取结果，谁的读取失败就在谁的次序位置抛出；
                    # 绝不把坏文件静默跳过（那会把数据损坏藏起来）。
                    for fut in futures:
                        fut.cancel()
                    raise

        # 退市股要出现在 stock_list 里，策略的 filter_universe 才会把它们当候选
        if not extra_list.empty:
            present = extra_list[extra_list["code"].astype(str).isin(bars_map)]
            if not present.empty:
                stock_list = pd.concat([stock_list, present], ignore_index=True)

        fund_map = {c: f for c in bars_map if (f := self.load_fundamentals(c)) is not None}
        return stock_list, bars_map, fund_map

    # ---------- B 档字段：股本 / 市值 / 披露日（Phase 12） ----------
    def load_shares(
        self,
        kind: str = "snapshot",
        month: str | None = None,
        out_dir: Path | str | None = None,
    ) -> pd.DataFrame:
        """读股本数据。`kind`：`snapshot`（当月快照）/ `float` / `total`（历史长表）。

        缺文件时返回空 DataFrame（不抛错），由 data/fields.py 统一转成降级说明。
        """
        from stock_plan.data import share_capital as shares

        base = Path(out_dir) if out_dir else SHARES_DIR
        if kind == "snapshot":
            return shares.load_snapshot(base, month=month)
        if kind in {"float", "total"}:
            return shares.load_history(kind, base)
        raise ValueError(f"未知股本数据类别：{kind!r}（应为 snapshot/float/total）")

    def load_total_mv(self, code: str | None = None) -> pd.DataFrame:
        """总市值历史长表（`date, code, total_shares, total_mv, source`，总股本为外推口径）。"""
        frame = self.load_shares("total")
        if code and not frame.empty:
            frame = frame[frame["code"].astype(str) == str(code)]
        return frame.reset_index(drop=True)

    def load_float_mv(self, code: str | None = None) -> pd.DataFrame:
        """流通市值历史长表（`date, code, close, float_shares, float_mv`，由成交额÷换手率反推）。"""
        frame = self.load_shares("float")
        if code and not frame.empty:
            frame = frame[frame["code"].astype(str) == str(code)]
        return frame.reset_index(drop=True)

    def load_disclosure(
        self,
        period: str | None = None,
        codes: list[str] | None = None,
    ) -> pd.DataFrame:
        """读财报披露日（`report_disclosure` 表）；无记录返回空表。"""
        from stock_plan.data import disclosure

        return disclosure.load_disclosure(period, codes, db_path=self.db_path)

    # ---------- 退市股元数据（暂时需求 5c） ----------
    def save_delisted(self, rows: list[dict]) -> int:
        """写入/更新退市股元数据。返回写入条数。

        字段：code, name, exchange, list_date, delist_date, first_bar, last_bar,
              bars_count, source（缺失的键写空值）
        """
        if not rows:
            return 0
        now = pd.Timestamp.now().isoformat()
        payload = []
        for r in rows:
            payload.append(
                (
                    str(r.get("code", "")),
                    str(r.get("name") or ""),
                    str(r.get("exchange") or ""),
                    str(r.get("list_date") or ""),
                    str(r.get("delist_date") or ""),
                    str(r.get("first_bar") or ""),
                    str(r.get("last_bar") or ""),
                    int(r.get("bars_count") or 0),
                    str(r.get("source") or ""),
                    now,
                )
            )
        with self._conn() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO delisted_stocks
                (code, name, exchange, list_date, delist_date,
                 first_bar, last_bar, bars_count, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        return len(payload)

    def load_delisted(self) -> pd.DataFrame:
        """读取退市股元数据表。"""
        with self._conn() as conn:
            return pd.read_sql_query("SELECT * FROM delisted_stocks", conn)

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

    def last_close(self, code: str) -> float | None:
        """取该股票缓存日线的最后一根收盘价；无缓存返回 None。"""
        try:
            bars = self.load_bars(code)
        except Exception:
            return None
        if bars is None or bars.empty:
            return None
        close = bars["close"].iloc[-1]
        return None if pd.isna(close) else float(close)

    def refresh_valuation(self, code: str, price: float | None = None) -> bool:
        """用最新收盘价重算 pe/pb 并回写，不重新抓财务数据。

        为什么需要单独一个方法（2026-10-28）：
        `fetcher.get_fundamentals()` 只有传了 `latest_price` 才会算 pe/pb，而
        `fetch_all.py` / `updater.py` 两处调用都**没传**，于是 `fundamentals` 表里
        pe/pb 两列**全表为 NULL**（实测 5553 行全空），估值分恒等于 50，价值类策略的
        "便宜"这一维一直是死的。这里用缓存里已有的日线收盘价 × 已入库的 eps/bvps
        重算，不需要再跑一遍网络抓取（5553 只重抓要几十分钟，重算只要几十秒）。

        注意：**不更新 updated_at**。eps/bvps 仍是那次快照抓到的值，
        `updated_at` 表示"财务数据的抓取日"，用它当 PIT 分界（见
        `fundamentals_snapshot_date()`）；pe/pb 只是同一份 eps/bvps 乘以当前价，
        改这个时间戳会让"快照日"漂移到今天，反而破坏前视修正。

        返回：是否写入了有效值（eps/bvps 缺失或价格缺失时返回 False）。
        """
        row = self.load_fundamentals(code)
        if not row:
            return False
        if price is None:
            price = self.last_close(code)
        if price is None or price <= 0:
            return False
        eps = row.get("eps")
        bvps = row.get("bvps")
        pe = (price / eps) if eps not in (None, 0) else None
        pb = (price / bvps) if bvps not in (None, 0) else None
        if pe is None and pb is None:
            return False
        with self._conn() as conn:
            conn.execute(
                "UPDATE fundamentals SET pe = ?, pb = ? WHERE code = ?",
                (pe, pb, code),
            )
        return True

    def fundamentals_snapshot_date(self):
        """财务指标快照的抓取日期（全表 updated_at 的最大日期），取不到返回 None。

        为什么需要这个：`fundamentals` 表每个 code **只有 1 行**、**没有报告期列**，
        `updated_at` 只是抓取时间戳（实测全表集中在同一天）。所以回测里拿到的财务数据
        其实是"今天才抓到的那一份" —— 在快照日**之前**的任何一天用它，都是未来数据。
        回测据此把快照日之前的财务因子中性化（见 `backtest/engine.py` 的
        `fundamental_as_of`），把"前视收益"从结果里摘出去。
        """
        from datetime import date as _date

        with self._conn() as conn:
            row = conn.execute("SELECT MAX(updated_at) FROM fundamentals").fetchone()
        raw = (row[0] if row else None) or ""
        raw = str(raw)[:10]
        try:
            return _date.fromisoformat(raw)
        except ValueError:
            return None

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