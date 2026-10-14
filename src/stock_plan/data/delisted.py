"""退市股票数据层 —— 消除回测的幸存者偏差（暂时需求 5c）。

为什么需要：
    stock_list 来自新浪实时行情，只含**当前在市**的股票。历史上退市、暂停上市、
    被吸收合并的公司从未进入样本，回测收益因此被系统性高估。

数据源（2026-10-15 实测，详见 .github/plans/2026-10-15-1-new-delisted-stock-history-db.md）：
    - 深交所退市名单：akshare stock_info_sz_delist 可用（208 行，含终止上市日期）
    - 上交所退市名单：query.sse.com.cn 在本网络环境不可达（与东财同因），
      改用 baostock 的**时点差集**推算（历史某日在市集合 − 今日在市集合）
    - 退市股历史行情：新浪源已不再提供（600001/000003/600002/000024 全部
      JSONDecodeError），必须走 baostock
    - 时点股票池：baostock query_all_stock(day=...)，返回该历史日期真实的在市清单

设计要点：
    - baostock **惰性导入**：Streamlit 应用与云端不需要它也能正常运行，只有执行
      退市股数据补齐时才导入，因此 requirements.txt 无需新增依赖
    - 退市股行情与在市股共用 data/processed/bars/{code}.parquet，回测读路径不用改；
      元数据另存 SQLite 的 delisted_stocks 表
    - 时点股票池存 data/processed/universe/{YYYY-MM-DD}.parquet（只有代码，文件极小）

用法：
    uv run python -m stock_plan.data.delisted --universe    # 只补时点股票池
    uv run python -m stock_plan.data.delisted --delisted    # 只补退市股行情
    uv run python -m stock_plan.data.delisted --all
    uv run python -m stock_plan.data.delisted --limit 10    # 试跑：只处理 10 只
"""
from __future__ import annotations

import argparse
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from stock_plan.data.storage import PROJECT_ROOT, Storage

UNIVERSE_DIR = PROJECT_ROOT / "data" / "processed" / "universe"

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

    滤掉指数、基金与 B 股。上证指数（sh.000001）、沪深300（sh.000300）这类
    如果漏进来，不仅会虚增股票池，还会与深市 A 股的 6 位代码撞车。
    """
    if "." not in bs_code:
        return False
    exchange, digits = bs_code.split(".", 1)
    prefixes = _A_SHARE_PREFIXES.get(exchange)
    return bool(prefixes) and len(digits) == 6 and digits.startswith(prefixes)


def _rows(rs) -> list[list[str]]:
    """把 baostock 的 ResultData 取完。"""
    out: list[list[str]] = []
    while rs.error_code == "0" and rs.next():
        out.append(rs.get_row_data())
    return out


class DelistedFetcher:
    """退市股数据获取。用一个上下文保持 baostock 会话，避免每只股票都登录一次。

    用法：
        with DelistedFetcher() as f:
            codes = f.universe("2024-04-25")
            bars = f.bars("000003", "1991-01-01", "2002-06-14")
    """

    def __init__(self) -> None:
        self._bs = None

    def __enter__(self) -> "DelistedFetcher":
        import baostock as bs  # 惰性导入：应用与云端无需安装 baostock

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

    # ---------- 时点股票池 ----------

    def universe(self, day: str) -> list[str]:
        """返回某历史日期 baostock 认定的 A 股在市代码列表（6 位）。

        这是消除幸存者偏差的关键：它给出的是**当天真实存在**的股票，
        而不是"今天还在市"的那批。
        """
        rs = self._bs.query_all_stock(day=day)
        codes = []
        for row in _rows(rs):
            bs_code = row[0]
            if is_a_share(bs_code):
                codes.append(bs_code.split(".")[-1])
        return sorted(set(codes))

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
        rows = _rows(rs)
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


# ---------- 退市名单 ----------

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
    """该自然月是否已有落盘的时点池（文件名是实际交易日，所以按月判断）。"""
    return any(UNIVERSE_DIR.glob(f"{year:04d}-{month:02d}-*.parquet"))


def _last_trading_day_with_data(f: DelistedFetcher, day: str, back: int = 10):
    """从 day 往前找最近一个能取到数据的日期。

    自然月末经常落在周末或法定节假日，baostock 的 query_all_stock 对非交易日
    返回空 —— 实测最近 2 年的 25 个月末里有 9 个是空的，所以必须回退。
    返回 (实际日期, 代码列表)。
    """
    cursor = pd.Timestamp(day)
    for _ in range(back):
        actual = cursor.date().isoformat()
        codes = f.universe(actual)
        if codes:
            return actual, codes
        cursor -= pd.Timedelta(days=1)
    return day, []


def update_universes(years: int = 5, force: bool = False) -> dict:
    """补齐时点股票池（按月采样，默认最近 N 年）。

    按月而不是按交易日：一个月一次已经能反映"当天市场上有哪些股票"，
    而按交易日会需要 1200+ 次请求。
    """
    end = date.today()
    start = end - timedelta(days=365 * years)
    days = month_ends(start, end)

    stats = {"total": len(days), "saved": 0, "skipped": 0, "empty": 0, "errors": []}
    t0 = time.time()
    with DelistedFetcher() as f:
        for day in days:
            ts = pd.Timestamp(day)
            if not force and _existing_in_month(ts.year, ts.month):
                stats["skipped"] += 1
                continue
            # 月末常是周末/节假日，回退到最近有数据的交易日再落盘
            actual, codes = _last_trading_day_with_data(f, day)
            if not codes:
                stats["empty"] += 1
                print(f"  {day}: 回退 10 天仍无数据，跳过", flush=True)
                continue
            save_universe(actual, codes)
            stats["saved"] += 1
            print(f"  {day} → {actual}: {len(codes)} 只", flush=True)
    stats["elapsed"] = round(time.time() - t0, 1)
    return stats


def delisted_codes_from_universes(today_codes: set[str] | None = None) -> dict[str, set[str]]:
    """用"历史时点池的并集 − 今日在市"推算退市代码，并按交易所分组。

    上交所没有可用的退市名单接口，这是绕开它的办法：某只股票在历史某天存在、
    今天却不在市，就说明它在这期间退市了。
    """
    storage = Storage()
    if today_codes is None:
        listing = storage.load_stock_list()
        today_codes = set(listing["code"].astype(str)) if not listing.empty else set()

    seen: set[str] = set()
    for path in sorted(UNIVERSE_DIR.glob("*.parquet")):
        seen |= set(pd.read_parquet(path)["code"].astype(str))

    gone = seen - today_codes
    groups: dict[str, set[str]] = {"SH": set(), "SZ": set(), "BJ": set()}
    for code in gone:
        if code.startswith(("4", "8", "9")):
            groups["BJ"].add(code)
        elif code.startswith(("0", "1", "2", "3")):
            groups["SZ"].add(code)
        else:
            groups["SH"].add(code)
    return groups


def update_delisted_bars(years: int = 5, limit: int = 0, force: bool = False) -> dict:
    """补齐退市股的完整历史行情，并记录元数据。

    退市股的历史是**一次性补齐**的静态数据，落地后不再依赖 baostock 在线。
    """
    storage = Storage()
    groups = delisted_codes_from_universes()
    sh_sz_names = {r["code"]: r for r in sz_delisted_list()}

    codes = sorted(groups["SH"] | groups["SZ"] | groups["BJ"])
    if limit:
        codes = codes[:limit]

    end = date.today()
    start = end - timedelta(days=365 * years)
    stats = {"total": len(codes), "saved": 0, "skipped": 0, "empty": 0,
             "failed": 0, "errors": [], "elapsed": 0.0}
    t0 = time.time()

    with DelistedFetcher() as f:
        for i, code in enumerate(codes, 1):
            try:
                bars = f.bars(code, start.isoformat(), end.isoformat())
            except Exception as e:
                stats["failed"] += 1
                stats["errors"].append((code, str(e)[:120]))
                continue
            if bars.empty:
                stats["empty"] += 1
                continue
            storage.save_bars(code, bars)
            meta = sh_sz_names.get(code, {})
            storage.save_delisted([{
                "code": code,
                "name": meta.get("name", ""),
                "exchange": meta.get("exchange") or (
                    "SH" if code.startswith("6") else
                    "BJ" if code.startswith(("4", "8", "9")) else "SZ"
                ),
                "list_date": meta.get("list_date", ""),
                "delist_date": meta.get("delist_date", ""),
                "first_bar": str(bars["date"].min().date()),
                "last_bar": str(bars["date"].max().date()),
                "bars_count": len(bars),
                "source": "baostock:query_history_k_data_plus(adjustflag=2)",
            }])
            stats["saved"] += 1
            if i % 20 == 0 or i == len(codes):
                print(f"  {i}/{len(codes)} 已补齐 {stats['saved']} 只…", flush=True)

    stats["errors"] = stats["errors"][:20]
    stats["elapsed"] = round(time.time() - t0, 1)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="退市股数据补齐（消除幸存者偏差）")
    parser.add_argument("--universe", action="store_true", help="只补时点股票池")
    parser.add_argument("--delisted", action="store_true", help="只补退市股行情")
    parser.add_argument("--all", action="store_true", help="两者都补")
    parser.add_argument("--years", type=int, default=5, help="回溯年数（默认 5）")
    parser.add_argument("--limit", type=int, default=0, help="试跑：只处理前 N 只退市股")
    parser.add_argument("--force", action="store_true", help="重算已存在的时点池")
    args = parser.parse_args()

    do_universe = args.universe or args.all or not (args.universe or args.delisted or args.all)
    do_delisted = args.delisted or args.all

    if do_universe:
        print(f"[1/2] 补齐时点股票池（最近 {args.years} 年，按月采样）...")
        s = update_universes(years=args.years, force=args.force)
        print(f"  完成：新增 {s['saved']}，已存在跳过 {s['skipped']}，"
              f"无数据 {s['empty']}，失败 {len(s['errors'])}，耗时 {s['elapsed']}s")
        for day, err in s["errors"][:5]:
            print(f"    {day}: {err}")

    if do_delisted:
        print(f"\n[2/2] 补齐退市股历史行情 ...")
        d = update_delisted_bars(years=args.years, limit=args.limit, force=args.force)
        print(f"  完成：落地 {d['saved']} 只，空数据 {d['empty']}，失败 {d['failed']}，"
              f"共 {d['total']} 只，耗时 {d['elapsed']}s")
        for code, err in d["errors"][:5]:
            print(f"    {code}: {err}")


if __name__ == "__main__":
    main()
