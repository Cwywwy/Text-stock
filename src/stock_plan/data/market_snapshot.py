# -*- coding: utf-8 -*-
"""日频市场快照 —— 资金流 + 涨停/跌停池，用于「市场情绪与资金流动倾向」分析。

为什么和 `classification.py` 分开：两者的**变化速度差两个数量级**（实测）。

| 数据 | 每次抓取 | 变化频率 | 结论 |
|---|---|---|---|
| 行业/概念/个股资金流 | 0.33 + 0.28 + 6.86 s | 每天 | **日频** |
| 涨停池 | 0.63 s | 每天 | **日频** |
| 申万成分 / 证监会行业 / 板块指数行情 | 2.6 min / 98 s / 2.8 min | 季度级 | **月频**（见 classification.py） |

所以这里只放"每天真正会变"的东西，整体约 12 秒/天、落盘约 0.3 MB/天。
把慢变量也塞进日频的话，每天要多花 5 分钟去抓一份几乎不变的数据 —— 不值。

数据源实测（2026-10）：
- 同花顺资金流三张表：行业 90 行 / 概念 387 行 / 个股 5217 行 —— **全部可用**
- 东财涨停池 `stock_zt_pool_em`：可用（走 push2ex，是东财唯一还活着的接口），
  含 **连板数 / 封板资金 / 炸板次数 / 所属行业** —— 情绪价值最高的一张表
- 东财的 push2 主接口（资金流排行等）**不可达**，所以资金流一律用同花顺

落盘结构（按日一目录，只增不改）::

    data/processed/market/2026-10-27/
        fund_industry.parquet     # 90 行
        fund_concept.parquet      # 387 行
        fund_stock.parquet        # 5217 行（压成 排名/净额/分位，见下）
        limitup.parquet           # 涨停池
        limitdown.parquet         # 跌停池（拿不到时为空表）
        zhaban.parquet            # 炸板池（算真实炸板率用）
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pandas as pd

from stock_plan.data.clock import today_cn

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MARKET_DIR = PROJECT_ROOT / "data" / "processed" / "market"

FUND_IND = "fund_industry.parquet"
FUND_CON = "fund_concept.parquet"
FUND_STK = "fund_stock.parquet"
LIMIT_UP = "limitup.parquet"
LIMIT_DOWN = "limitdown.parquet"
ZHABAN = "zhaban.parquet"

# 资金流原表里金额是"67.68 亿"这类中文字符串，必须先转成数字才能比较
_UNIT = {"亿": 1e8, "万": 1e4, "": 1.0}


def parse_money(raw) -> float:
    """把 `"2.07亿"` / `"-4913.57万"` / `"3.5"` 统一解析成**元**（float）。

    为什么单独写：同花顺资金流接口把数值和单位拼在一个字符串里，
    直接 `astype(float)` 会全变 NaN；而单位混用（亿/万/无）是这张表的常态。
    """
    if raw is None:
        return float("nan")
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "")
    if not s or s in {"-", "--", "None", "nan"}:
        return float("nan")
    neg = s.startswith("-")
    s = s.lstrip("-")
    unit = 1.0
    for u, mult in _UNIT.items():
        if u and s.endswith(u):
            unit = mult
            s = s[: -len(u)]
            break
    try:
        val = float(s) * unit
    except ValueError:
        return float("nan")
    return -val if neg else val


def parse_pct(raw) -> float:
    """把 `"20.01%"` / `"-3.5%"` / ``1.34`` 统一解析成百分数值（float，单位 %）。

    个股资金流表的「涨跌幅」「换手率」是**带百分号的字符串**，
    直接交给 `parse_money` 会返回 NaN（实测整列全空），所以要单独解析。
    """
    if raw is None:
        return float("nan")
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().replace(",", "")
    if not s or s in {"-", "--", "None", "nan"}:
        return float("nan")
    neg = s.startswith("-")
    s = s.lstrip("-").rstrip("%")
    try:
        val = float(s)
    except ValueError:
        return float("nan")
    return -val if neg else val


def _money_series(s: pd.Series) -> pd.Series:
    """把一列金额统一成**元**。

    实测三张表的单位**不一致**，这是最容易埋雷的地方：

    - 行业/概念资金流：`净额` 是 float，单位 **亿元**（银行 74.32 就是 74.32 亿）
    - 个股资金流：`净额` 是字符串 ``"1798.11万"``，自带单位，得按字符串解析

    所以按 dtype 分支：数值型 ×1e8，字符串型走 `parse_money`。
    不统一的话，同屏比较时个股净额 1.2e9、行业净额 74.32，会差出 7 个数量级。
    """
    if pd.api.types.is_numeric_dtype(s):
        return s.astype(float) * 1e8
    return s.map(parse_money)


def today_str(day: str | date | None = None) -> str:
    if day is None:
        return today_cn().strftime("%Y-%m-%d")
    if isinstance(day, date):
        return day.strftime("%Y-%m-%d")
    return str(day)


def _day_dir(day: str | None = None) -> Path:
    return MARKET_DIR / today_str(day)


def _atomic_write(df: pd.DataFrame, path: Path) -> Path:
    """原子写（同 classification.py 的约定）：应用可能在读，不能写半个文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- 抓取


def fetch_fund_flow(quiet: bool = False) -> dict[str, pd.DataFrame]:
    """抓同花顺三张资金流表：行业 / 概念 / 个股。

    返回的金额列已转成**元**（见 `parse_money`）；个股表额外压成
    「排名 + 净额 + 分位」，因为 5217 行明细每天全存没有必要 ——
    真正要看的是"谁在流入/流出"的排序与相对位置。
    """
    import akshare as ak

    out: dict[str, pd.DataFrame] = {}

    ind = ak.stock_fund_flow_industry(symbol="即时")
    ind = ind.rename(
        columns={"行业": "board", "行业-涨跌幅": "pct", "净额": "net",
                 "流入资金": "inflow", "流出资金": "outflow", "公司家数": "n_stocks"}
    )
    for col in ("net", "inflow", "outflow"):
        ind[col] = _money_series(ind[col])
    ind = ind[["board", "pct", "net", "inflow", "outflow", "n_stocks"]].copy()
    ind = ind.sort_values("net", ascending=False).reset_index(drop=True)
    ind["rank"] = range(1, len(ind) + 1)
    out[FUND_IND] = ind

    con = ak.stock_fund_flow_concept(symbol="即时")
    con = con.rename(
        columns={"行业": "board", "行业-涨跌幅": "pct", "净额": "net",
                 "流入资金": "inflow", "流出资金": "outflow", "公司家数": "n_stocks"}
    )
    for col in ("net", "inflow", "outflow"):
        con[col] = _money_series(con[col])
    con = con[["board", "pct", "net", "inflow", "outflow", "n_stocks"]].copy()
    con = con.sort_values("net", ascending=False).reset_index(drop=True)
    con["rank"] = range(1, len(con) + 1)
    out[FUND_CON] = con

    stk = ak.stock_fund_flow_individual(symbol="即时")
    stk = stk.rename(
        columns={"股票代码": "code", "股票简称": "name", "涨跌幅": "pct",
                 "换手率": "turnover", "净额": "net", "成交额": "amount"}
    )
    stk["net"] = _money_series(stk["net"])
    stk["amount"] = _money_series(stk["amount"])
    stk["pct"] = stk["pct"].map(parse_pct)
    stk["turnover"] = stk["turnover"].map(parse_pct)
    stk["code"] = stk["code"].astype(str).str.zfill(6)
    stk = stk[["code", "name", "pct", "turnover", "net", "amount"]].copy()
    stk = stk.sort_values("net", ascending=False).reset_index(drop=True)
    stk["rank"] = range(1, len(stk) + 1)
    # 分位：0=净流出最多，1=净流入最多。用它而不是绝对金额，跨日可比。
    if len(stk) > 1:
        stk["net_pct"] = stk["net"].rank(pct=True)
    else:
        stk["net_pct"] = 1.0
    out[FUND_STK] = stk

    if not quiet:
        for k, v in out.items():
            print(f"  {k:22s} {len(v)} 行")
    return out


def fetch_limitup(day: str | None = None, quiet: bool = False) -> pd.DataFrame:
    """抓东财涨停池（含连板数/封板资金/炸板次数/所属行业）。

    这是"市场情绪"最直接的温度计：涨停家数、连板高度、炸板率。
    走的是 `push2ex.eastmoney.com` —— 东财主接口 push2 在本网络不可达，
    但这个子域可用（实测 41 行、0.63 秒）。
    """
    import akshare as ak

    d = today_str(day).replace("-", "")
    df = ak.stock_zt_pool_em(date=d)
    df = df.rename(
        columns={"代码": "code", "名称": "name", "涨跌幅": "pct", "最新价": "price",
                 "成交额": "amount", "流通市值": "float_mv", "换手率": "turnover",
                 "封板资金": "seal_fund", "首次封板时间": "first_seal",
                 "最后封板时间": "last_seal", "炸板次数": "break_count",
                 "涨停统计": "lu_stat", "连板数": "boards", "所属行业": "industry"}
    )
    keep = [c for c in ["code", "name", "pct", "price", "amount", "turnover",
                        "seal_fund", "first_seal", "last_seal", "break_count",
                        "lu_stat", "boards", "industry"] if c in df.columns]
    df = df[keep].copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    if not quiet:
        print(f"  涨停池 {len(df)} 行")
    return df


def fetch_limitdown(day: str | None = None, quiet: bool = False) -> pd.DataFrame:
    """抓跌停池。拿不到时返回空表 —— 没有它情绪指标仍可用（只是少一维）。"""
    import akshare as ak

    d = today_str(day).replace("-", "")
    try:
        df = ak.stock_zt_pool_dtgc_em(date=d)
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"  跌停池不可用（{type(exc).__name__}），按空表处理")
        return pd.DataFrame(columns=["code", "name", "pct"])
    df = df.rename(columns={"代码": "code", "名称": "name", "涨跌幅": "pct"})
    df = df[[c for c in ["code", "name", "pct"] if c in df.columns]].copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    if not quiet:
        print(f"  跌停池 {len(df)} 行")
    return df


def fetch_zhaban(day: str | None = None, quiet: bool = False) -> pd.DataFrame:
    """抓炸板池（曾涨停、收盘未封住）。

    为什么需要：涨停池只含**最终封住**的票，光看它会高估情绪。
    真实炸板率 = 炸板家数 / (涨停家数 + 炸板家数)，是情绪最灵敏的领先指标之一
    —— 炸板率突然抬升通常意味着题材接力失败。拿不到时返回空表，
    情绪指标会退化成"只有涨停家数"，不至于整体失败。
    """
    import akshare as ak

    d = today_str(day).replace("-", "")
    try:
        df = ak.stock_zt_pool_zbgc_em(date=d)
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"  炸板池不可用（{type(exc).__name__}），按空表处理")
        return pd.DataFrame(columns=["code", "name", "pct"])
    df = df.rename(columns={"代码": "code", "名称": "name", "涨跌幅": "pct"})
    df = df[[c for c in ["code", "name", "pct"] if c in df.columns]].copy()
    df["code"] = df["code"].astype(str).str.zfill(6)
    if not quiet:
        print(f"  炸板池 {len(df)} 行")
    return df


# ---------------------------------------------------------------- 落盘/读取


def save_day(day: str | None = None, frames: dict[str, pd.DataFrame] | None = None) -> list[Path]:
    d = _day_dir(day)
    written: list[Path] = []
    for fname, df in (frames or {}).items():
        if df is None or df.empty:
            continue
        written.append(_atomic_write(df, d / fname))
    return written


def load_day(day: str | None = None) -> dict[str, pd.DataFrame]:
    d = _day_dir(day)
    out: dict[str, pd.DataFrame] = {}
    for fname in (FUND_IND, FUND_CON, FUND_STK, LIMIT_UP, LIMIT_DOWN, ZHABAN):
        p = d / fname
        if p.exists():
            out[fname] = pd.read_parquet(p)
    return out


def available_days() -> list[str]:
    if not MARKET_DIR.exists():
        return []
    return sorted(
        p.name for p in MARKET_DIR.iterdir() if p.is_dir() and p.name.count("-") == 2
    )


def capture(day: str | None = None, quiet: bool = False) -> list[Path]:
    """一次抓齐当日全部快照并落盘。日频任务调这个（约 12 秒）。"""
    d = today_str(day)
    if not quiet:
        print(f"抓取 {d} 的日频快照…")
    frames: dict[str, pd.DataFrame] = {}
    frames.update(fetch_fund_flow(quiet=quiet))
    frames[LIMIT_UP] = fetch_limitup(d, quiet=quiet)
    frames[LIMIT_DOWN] = fetch_limitdown(d, quiet=quiet)
    frames[ZHABAN] = fetch_zhaban(d, quiet=quiet)
    written = save_day(d, frames)
    if not quiet:
        total = sum(p.stat().st_size for p in written) / 1024
        print(f"已写入 {len(written)} 个文件，共 {total:.0f} KB → {_day_dir(d)}")
    return written


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    用法::

        python -m stock_plan.data.market_snapshot              # 抓今天
        python -m stock_plan.data.market_snapshot --day 2026-10-27
        python -m stock_plan.data.market_snapshot --list       # 看已有哪些天
    """
    import argparse

    ap = argparse.ArgumentParser(description="抓取日频市场快照（资金流 + 涨跌停池）")
    ap.add_argument("--day", default=None, help="日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--list", action="store_true", help="列出已保存的日期")
    args = ap.parse_args(argv)
    if args.list:
        for d in available_days():
            print(d)
        return 0
    written = capture(args.day)
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
