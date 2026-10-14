"""退市股票数据层 —— 消除回测的幸存者偏差（暂时需求 5c）。

为什么需要：
    stock_list 来自新浪实时行情，只含**当前在市**的股票。历史上退市、暂停上市、
    被吸收合并的公司从未进入样本，回测收益因此被系统性高估。

数据源（2026-10-15 实测，详见 .github/plans/2026-10-15-1-new-delisted-stock-history-db.md）：
    - **证券基础表**：baostock `query_stock_basic()`，一次调用返回全部 8999 个证券的
      `code / code_name / ipoDate / outDate / type / status`，其中 status=0（已退市）
      1195 条。**时点股票池由 [ipoDate, outDate] 区间本地推导**，不需要按日期爬取。
    - 退市股历史行情：新浪源已不再提供（600001/000003/600002/000024 全部
      JSONDecodeError），必须走 baostock `query_history_k_data_plus(adjustflag=2)`
    - 深交所退市名单：akshare `stock_info_sz_delist`（208 行），用于交叉校验；
      上交所的 query.sse.com.cn 在本网络环境不可达，改由基础表的 outDate 提供

为什么放弃"按月爬 query_all_stock"的初版方案：
    初版用 `query_all_stock(day=...)` 逐月抓 61 次。实测两个致命问题——
    (1) 长会话会退化：第 21 个月起陆续返回空/截断（2023-06-30 只返回 1288 只）；
    (2) baostock 没有超时参数，退化后会**无限挂起**。
    改用一次 `query_stock_basic()` + 本地区间推导后，两个问题同时消失，
    而且推导结果与爬取结果完全吻合（2021-12-31：都是 4603 只）。

设计要点：
    - baostock **惰性导入**：Streamlit 应用与云端不需要它也能正常运行，
      因此 requirements.txt 无需新增依赖
    - 所有 baostock 调用都设 socket 全局超时，避免无限阻塞
    - 接口错误码必须显式抛出：初版的取数循环在 error_code != 0 时静默返回空列表，
      把"请求失败"伪装成"当天没有数据"，导致退化被埋了 24 个月才发现
    - 时点池存 data/processed/universe/{YYYY-MM-DD}.parquet（只存代码，约 60KB/个）
    - 退市股行情与在市股共用 data/processed/bars/{code}.parquet，回测读路径不用改；
      元数据另存 SQLite 的 delisted_stocks 表

用法：
    uv run python -m stock_plan.data.delisted --basic        # 只刷新证券基础表
    uv run python -m stock_plan.data.delisted --universe     # 生成时点股票池快照
    uv run python -m stock_plan.data.delisted --delisted     # 补齐退市股历史行情
    uv run python -m stock_plan.data.delisted --all
    uv run python -m stock_plan.data.delisted --delisted --limit 10   # 试跑
"""
from __future__ import annotations

import argparse
import socket
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from stock_plan.data.storage import PROJECT_ROOT, Storage

UNIVERSE_DIR = PROJECT_ROOT / "data" / "processed" / "universe"
BASIC_PATH = PROJECT_ROOT / "data" / "processed" / "stock_basic.parquet"

# baostock 没有超时参数：退化时会无限阻塞，靠全局 socket 超时兜底
_SOCKET_TIMEOUT = 45

# 各交易所的 A 股代码前缀。
# 必须**按交易所分别**判断：上证指数是 `sh.000001`，只看数字前缀会被误判成深市
# A 股，而它落库后还会与平安银行 `sz.000001` 的 6 位代码直接撞车。
_A_SHARE_PREFIXES = {
    "sh": ("600", "601", "603", "605", "688", "689"),              # 主板 / 科创板
    "sz": ("000", "001", "002", "003", "300", "301"),              # 主板 / 创业板
    "bj": ("430", "830", "831", "832", "833", "834", "835", "836",  # 北交所
           "837", "838", "839", "870", "871", "872", "873", "920"),
}

# baostock 日线字段；turn 是换手率（百分数），tradestatus=1 为正常交易
_BAR_FIELDS = "date,open,high,low,close,volume,amount,turn,tradestatus"


def to_bs_code(code: str) -> str:
    """6 位代码 → baostock 格式（sh.600000 / sz.000001 / bj.920000）。

    前缀规则与 fetcher._to_sina_symbol 保持一致：0/1/2/3 → sz，4/8/9 → bj，其余 → sh。
    """
    code = str(code).strip()[-6:]
    if code.startswith(("0", "1", "2", "3")):
        return f"sz.{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj.{code}"
    return f"sh.{code}"


def is_a_share(bs_code: str) -> bool:
    """判断 baostock 返回的证券是否属于沪深/北交所 A 股。

    滤掉指数、基金与 B 股。上证指数（sh.000001）、沪深300（sh.000300）这类如果
    漏进来，不仅会虚增股票池，还会与深市 A 股的 6 位代码撞车。
    """
    if "." not in bs_code:
        return False
    exchange, digits = bs_code.split(".", 1)
    prefixes = _A_SHARE_PREFIXES.get(exchange)
    return bool(prefixes) and len(digits) == 6 and digits.startswith(prefixes)


def _drain(rs, limit: int = 100_000) -> list[list[str]]:
    """把 baostock 的 ResultData 取完；接口报错必须显式抛出。

    初版在这里静默返回空列表，把"请求失败"伪装成"没有数据"—— 结果长会话退化被
    埋了 24 个月才被发现。
    """
    out: list[list[str]] = []
    while rs.next():
        out.append(rs.get_row_data())
        if len(out) >= limit:
            break
    if rs.error_code != "0":
        raise RuntimeError(f"baostock 查询失败：error_code={rs.error_code} msg={rs.error_msg}")
    return out


class DelistedFetcher:
    """baostock 数据获取。用一个上下文保持会话，避免每只股票都登录一次。

    用法：
        with DelistedFetcher() as f:
            basic = f.stock_basic()
            bars = f.bars("000003", "1991-01-01", "2002-06-14")
    """

    def __init__(self) -> None:
        self._bs = None

    def __enter__(self) -> "DelistedFetcher":
        import baostock as bs  # 惰性导入：应用与云端无需安装 baostock

        socket.setdefaulttimeout(_SOCKET_TIMEOUT)
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败：{lg.error_msg}")
        self._bs = bs
        return self

    def __exit__(self, *exc) -> bool:
        if self._bs is not None:
            try:
                self._bs.logout()
            except Exception:
                pass
            self._bs = None
        return False

    # ---------- 证券基础表（一次拿全，时点池由它推导） ----------

    def stock_basic(self) -> pd.DataFrame:
        """全部证券的基础信息：code / code_name / ipoDate / outDate / type / status。

        status：1=上市，0=退市；type：1=股票，2=指数（其余为基金/可转债等）。
        """
        rs = self._bs.query_stock_basic()
        fields = list(rs.fields)
        rows = _drain(rs)
        if not rows:
            return pd.DataFrame(columns=fields)
        df = pd.DataFrame(rows, columns=fields)
        for col in ("ipoDate", "outDate"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
        return df

    # ---------- 退市股行情 ----------

    def bars(self, code: str, start: str, end: str) -> pd.DataFrame:
        """取单只股票的日线（前复权），字段与 Storage.BAR_COLUMNS 对齐。

        停牌行（tradestatus != 1）会被剔除：baostock 对停牌日返回空价格，
        留着会污染均线/ATR 等因子。
        """
        rs = self._bs.query_history_k_data_plus(
            to_bs_code(code), _BAR_FIELDS,
            start_date=start, end_date=end, frequency="d", adjustflag="2",
        )
        rows = _drain(rs)
        empty = pd.DataFrame(columns=["date", "open", "high", "low", "close",
                                      "volume", "amount", "turnover"])
        if not rows:
            return empty

        raw = pd.DataFrame(rows, columns=_BAR_FIELDS.split(","))
        raw = raw[raw["tradestatus"].astype(str) == "1"].copy()
        raw = raw.replace("", pd.NA).dropna(subset=["open", "high", "low", "close"])
        if raw.empty:
            return empty

        out = pd.DataFrame(
            {
                "date": pd.to_datetime(raw["date"]),
                "open": raw["open"].astype(float),
                "high": raw["high"].astype(float),
                "low": raw["low"].astype(float),
                "close": raw["close"].astype(float),
                "volume": pd.to_numeric(raw["volume"], errors="coerce").fillna(0.0),
                "amount": pd.to_numeric(raw["amount"], errors="coerce").fillna(0.0),
                # baostock 的 turn 是百分数（0.53 表示 0.53%），
                # 而新浪口径的 turnover 是小数（0.0053），这里统一成小数
                "turnover": pd.to_numeric(raw["turn"], errors="coerce").fillna(0.0) / 100.0,
            }
        )
        return out.sort_values("date").reset_index(drop=True)


# ---------- 证券基础表的本地缓存 ----------

def refresh_stock_basic(force: bool = False) -> pd.DataFrame:
    """刷新并缓存证券基础表（默认当天已刷新则直接读缓存）。"""
    if not force and BASIC_PATH.exists():
        age_hours = (time.time() - BASIC_PATH.stat().st_mtime) / 3600
        if age_hours < 24:
            return pd.read_parquet(BASIC_PATH)
    with DelistedFetcher() as f:
        basic = f.stock_basic()
    if basic.empty:
        raise RuntimeError("证券基础表为空，检查 baostock 连接")
    BASIC_PATH.parent.mkdir(parents=True, exist_ok=True)
    basic.to_parquet(BASIC_PATH, index=False)
    return basic


def load_stock_basic(refresh: bool = False) -> pd.DataFrame:
    """读取证券基础表（必要时先刷新）。"""
    return refresh_stock_basic(force=refresh)


def a_share_table(basic: pd.DataFrame | None = None) -> pd.DataFrame:
    """从基础表里筛出 A 股股票（type=1 且代码前缀属于沪深北 A 股）。"""
    if basic is None:
        basic = load_stock_basic()
    if basic.empty:
        return basic
    mask = basic["code"].map(is_a_share)
    if "type" in basic.columns:
        mask &= basic["type"].astype(str) == "1"
    out = basic.loc[mask].copy()
    out["code6"] = out["code"].str.split(".").str[-1]
    # 防御性转换：parquet 往返会保留 datetime，但手工构造或换数据源时可能是字符串
    for col in ("ipoDate", "outDate"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce")
    return out.reset_index(drop=True)


def universe_on(day: str, table: pd.DataFrame | None = None) -> list[str]:
    """某历史日期的 A 股在市代码列表（由 [ipoDate, outDate] 区间推导）。

    这是消除幸存者偏差的核心：给出的是**当天真实存在**的股票，而不是"今天还在市"。
    """
    if table is None:
        table = a_share_table()
    if table.empty:
        return []
    if "code6" not in table.columns:  # 容错：允许直接传原始基础表
        table = table.copy()
        table["code6"] = table["code"].str.split(".").str[-1]
    ts = pd.Timestamp(day)
    ipo = table["ipoDate"]
    out = table["outDate"]
    alive = (ipo <= ts) & (out.isna() | (out >= ts))
    return sorted(table.loc[alive, "code6"].astype(str).tolist())


def delisted_a_shares(table: pd.DataFrame | None = None) -> pd.DataFrame:
    """已退市的 A 股（status=0 且有退市日期），字段：code6/name/ipoDate/outDate/exchange。

    允许直接传原始基础表：此时先按 A 股股票过滤，指数/基金/B 股的"退市"记录
    （例如 sh.000817 这类已终止的指数）不会被混进来。
    """
    if table is None:
        table = a_share_table()
    elif "code6" not in table.columns:
        table = a_share_table(table)
    if table.empty:
        return pd.DataFrame(columns=["code6", "name", "ipoDate", "outDate", "exchange"])
    out = table[table["status"].astype(str) == "0"].copy()
    out = out[out["outDate"].notna()]
    out["name"] = out["code_name"]
    out["exchange"] = out["code"].str.split(".").str[0].str.upper()
    return out[["code6", "name", "ipoDate", "outDate", "exchange"]].reset_index(drop=True)


# ---------- 退市名单（深交所，用于交叉校验） ----------

def sz_delisted_list() -> list[dict]:
    """深交所退市股票名单（含终止上市日期），来自 akshare。"""
    import akshare as ak

    try:
        df = ak.stock_info_sz_delist()
    except Exception:
        return []
    if df is None or df.empty:
        return []
    out = []
    for _, r in df.iterrows():
        code = str(r.get("证券代码", "")).strip()[-6:]
        if len(code) != 6:
            continue
        out.append(
            {
                "code": code,
                "name": str(r.get("证券简称", "") or ""),
                "exchange": "SZ",
                "list_date": str(r.get("上市日期", "") or ""),
                "delist_date": str(r.get("终止上市日期", "") or ""),
                "source": "akshare:stock_info_sz_delist",
            }
        )
    return out


# ---------- 时点股票池落盘 ----------

def _universe_path(day: str) -> Path:
    return UNIVERSE_DIR / f"{day}.parquet"


def save_universe(day: str, codes: list[str]) -> Path:
    """保存某日的时点股票池（只存代码，文件极小）。"""
    UNIVERSE_DIR.mkdir(parents=True, exist_ok=True)
    path = _universe_path(day)
    pd.DataFrame({"code": sorted(set(codes))}).to_parquet(path, index=False)
    return path


def load_universe(day: str) -> list[str]:
    """读取某日的时点股票池；不存在返回空列表。"""
    path = _universe_path(day)
    if not path.exists():
        return []
    return pd.read_parquet(path)["code"].astype(str).tolist()


def month_ends(start: date, end: date) -> list[str]:
    """生成 [start, end] 区间内每个自然月末的日期字符串（含 end）。"""
    days = pd.date_range(start=start, end=end, freq="ME")
    out = [d.date().isoformat() for d in days]
    if not out or out[-1] < end.isoformat():
        out.append(end.isoformat())
    return out


def _existing_in_month(year: int, month: int) -> bool:
    """该自然月是否已有落盘的时点池。"""
    return any(UNIVERSE_DIR.glob(f"{year:04d}-{month:02d}-*.parquet"))


def update_universes(years: int = 5, force: bool = False) -> dict:
    """按月生成时点股票池快照。

    本地推导，**不产生按月 API 请求**：先取一次证券基础表，再由 [ipoDate, outDate]
    推算每个历史日期的在市集合。因此不再受"非交易日返回空"和"长会话退化"影响。
    """
    end = date.today()
    start = end - timedelta(days=365 * years)
    days = month_ends(start, end)

    table = a_share_table(load_stock_basic())
    stats = {"total": len(days), "saved": 0, "skipped": 0, "empty": 0,
             "a_share_total": int(len(table)), "errors": []}
    t0 = time.time()
    for day in days:
        ts = pd.Timestamp(day)
        if not force and _existing_in_month(ts.year, ts.month):
            stats["skipped"] += 1
            continue
        codes = universe_on(day, table)
        if not codes:
            stats["empty"] += 1
            continue
        save_universe(day, codes)
        stats["saved"] += 1
        print(f"  {day}: {len(codes)} 只", flush=True)
    stats["elapsed"] = round(time.time() - t0, 1)
    return stats


# ---------- 退市股行情补齐 ----------

def _days_after(day: str, days: int) -> str:
    """在日期字符串上加若干天，用于判断"取到的首日是否明显晚于上市日"。"""
    try:
        return str((pd.Timestamp(day) + pd.Timedelta(days=days)).date())
    except Exception:
        return day


def update_delisted_bars(limit: int = 0, force: bool = False) -> dict:
    """补齐退市股的完整历史行情，并记录元数据。

    取数区间是每只股票的 **上市日 → 退市日**，而不是"最近 N 年"：多数退市股在
    2000 年代就已摘牌，用最近 5 年去取会全部落空（实测 5 只里 3 只为空）。
    baostock 每次请求都返回整段历史，按生命周期取并不更贵。

    跳过条件用 **delisted_stocks 元数据**而不是 `cache_exists`：
    parquet 文件存在只能说明"取过"，不能说明"取对了" —— 用错区间取过的记录
    （例如按最近 5 年取，实际只覆盖 2021-10 之后）会让文件永远存在、永远被跳过，
    错数据就此固化。这里改为检查"记录的首个交易日是否明显晚于上市日"，
    是则自动重取（自愈）。

    退市股历史是**一次性补齐**的静态数据，落地后不再依赖 baostock 在线。
    """
    storage = Storage()
    table = delisted_a_shares()
    rows = table.sort_values("outDate", ascending=False).to_dict("records")
    if limit:
        rows = rows[:limit]

    existing = storage.load_delisted()
    recorded: dict[str, dict] = {}
    if not existing.empty:
        for r in existing.to_dict("records"):
            recorded[str(r["code"])] = r

    today = date.today().isoformat()
    stats = {"total": len(rows), "saved": 0, "empty": 0, "failed": 0,
             "skipped": 0, "refetched": 0, "errors": [], "elapsed": 0.0}
    t0 = time.time()

    with DelistedFetcher() as f:
        for i, meta in enumerate(rows, 1):
            code = str(meta["code6"])
            ipo = meta.get("ipoDate")
            out = meta.get("outDate")
            # 按生命周期取：缺上市日就退到 1990（A 股最早的开市年份）
            start_str = "1990-01-01" if pd.isna(ipo) else str(pd.Timestamp(ipo).date())
            end_str = today if pd.isna(out) else str(pd.Timestamp(out).date())

            rec = recorded.get(code)
            if not force and rec is not None:
                first_bar = str(rec.get("first_bar") or "")
                # 首个交易日明显晚于上市日 ⇒ 之前用错区间取过，重取
                incomplete = bool(first_bar) and first_bar > _days_after(start_str, 30)
                if not incomplete:
                    stats["skipped"] += 1
                    continue
                stats["refetched"] += 1

            try:
                bars = f.bars(code, start_str, end_str)
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append((code, str(e)[:120]))
                continue
            if bars.empty:
                stats["empty"] += 1
                continue
            storage.save_bars(code, bars)
            storage.save_delisted([{
                "code": code,
                "name": str(meta.get("name") or ""),
                "exchange": str(meta.get("exchange") or ""),
                "list_date": "" if pd.isna(ipo) else str(pd.Timestamp(ipo).date()),
                "delist_date": "" if pd.isna(out) else str(pd.Timestamp(out).date()),
                "first_bar": str(bars["date"].min().date()),
                "last_bar": str(bars["date"].max().date()),
                "bars_count": len(bars),
                "source": "baostock:query_history_k_data_plus(adjustflag=2)",
            }])
            stats["saved"] += 1
            if i % 20 == 0 or i == len(rows):
                print(f"  {i}/{len(rows)}：已补齐 {stats['saved']}，跳过 {stats['skipped']}，"
                      f"重取 {stats['refetched']}，空 {stats['empty']}，失败 {stats['failed']}",
                      flush=True)

    stats["errors"] = stats["errors"][:20]
    stats["elapsed"] = round(time.time() - t0, 1)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="退市股数据补齐（消除幸存者偏差）")
    parser.add_argument("--basic", action="store_true", help="只刷新证券基础表")
    parser.add_argument("--universe", action="store_true", help="只生成时点股票池")
    parser.add_argument("--delisted", action="store_true", help="只补退市股行情")
    parser.add_argument("--all", action="store_true", help="全部执行")
    parser.add_argument("--years", type=int, default=5, help="回溯年数（默认 5）")
    parser.add_argument("--limit", type=int, default=0, help="试跑：只处理前 N 只退市股")
    parser.add_argument("--force", action="store_true", help="重算已存在的产物")
    args = parser.parse_args()

    explicit = args.basic or args.universe or args.delisted or args.all
    do_basic = args.basic or args.all or not explicit
    do_universe = args.universe or args.all or not explicit
    do_delisted = args.delisted or args.all

    if do_basic:
        print("[1/3] 刷新证券基础表（一次调用拿到全部证券的 ipoDate/outDate/status）...")
        basic = refresh_stock_basic(force=args.force)
        table = a_share_table(basic)
        delisted = delisted_a_shares(table)
        print(f"  全部证券 {len(basic)} 个；A 股股票 {len(table)} 只；"
              f"其中已退市 {len(delisted)} 只"
              f"（SH {(delisted['exchange'] == 'SH').sum()} / "
              f"SZ {(delisted['exchange'] == 'SZ').sum()} / "
              f"BJ {(delisted['exchange'] == 'BJ').sum()}）")

    if do_universe:
        print(f"\n[2/3] 生成时点股票池（最近 {args.years} 年，按月，本地推导）...")
        u = update_universes(years=args.years, force=args.force)
        print(f"  完成：新增 {u['saved']}，已存在跳过 {u['skipped']}，"
              f"无数据 {u['empty']}，耗时 {u['elapsed']}s")

    if do_delisted:
        print("\n[3/3] 补齐退市股历史行情（按每只股票的上市日→退市日取全历史）...")
        d = update_delisted_bars(limit=args.limit, force=args.force)
        print(f"  完成：落地 {d['saved']} 只，已缓存跳过 {d['skipped']}，"
              f"空数据 {d['empty']}，失败 {d['failed']}，共 {d['total']} 只，"
              f"耗时 {d['elapsed']}s")
        for code, err in d["errors"][:5]:
            print(f"    {code}: {err}")


if __name__ == "__main__":
    main()
