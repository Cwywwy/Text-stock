# -*- coding: utf-8 -*-
"""分类数据模块 —— 个股 ↔ 行业/概念 的映射，以及按时点落盘的快照。

为什么这样设计（三条都是**实测**结论，不是猜的，见
`.github/plans/2026-10-15-5-new-classification-emotion-flow-and-weekly-t.md`）：

1. **东财（push2.eastmoney.com）在本网络不可达**（ConnectionError / RemoteDisconnected），
   而它正是"东财行业/概念"的唯一来源。所以行业改用两个都可达的口径：
   - **申万三级**：有层级（一级/二级/三级）、有「纳入时间」（可做近似时点判断）
   - **baostock 证监会行业**：84 类，项目本来就依赖 baostock（退市股那条线），零新依赖
2. **A 股"概念成分股"没有可用来源**：东财被墙、akshare 根本没有同花顺成分接口、
   富途只有 3 个硬编码美股概念。因此概念只做到**板块级**（列表/指数行情/资金流），
   **无法**把概念标签挂到个股。这不是少做，是数据不存在 —— 写在这里避免后人再去翻一遍。
3. **抓取成本实测**：申万三级成分 0.47s × 335 ≈ 2.6 分钟、baostock 98 秒、
   板块指数行情 0.35s × 465 ≈ 2.8 分钟，而日频的"资金流 + 涨停池"只要约 12 秒。
   所以：**慢变量（成分/行业）按月存，快变量（资金流/情绪）按日存**
   （后者见 `market_snapshot.py`）。

时点性（**很重要**，别拿它做历史回测）：
- 申万成分带「纳入时间」，可做"纳入时间 ≤ t"的**近似**时点；但**没有剔除时间**，
  退市股与调出成分无法还原 → 只是近似，不是精确时点。
- baostock 的 `updateDate` 全表只有**一个**取值（实测 2026-10-12），是纯当前快照、**没有任何历史**。
- 结论：这些分类**只用于当下判断**；要用在回测上，必须从现在起按月积累快照，
  且积累到足够月份之前不得用于历史回测（会构成前视偏差）。

落盘结构（只增不改，天然可回溯）::

    data/processed/classify/2026-10/
        industry_sw.parquet    # 申万：code,name,sw_l1,sw_l2,sw_l3,incl_date
        industry_csr.parquet   # baostock 证监会：code,name,csr_industry
        concept.parquet        # 同花顺概念：board,board_code
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CLASSIFY_DIR = PROJECT_ROOT / "data" / "processed" / "classify"

# 申万三级成分的逐个调用间隔（秒）。默认 0：实测 335 个连续调用没有触发限流。
SW_CONS_DELAY = 0.0
# 单个三级行业最多等多久（秒）—— 某个接口卡住时不要整体挂死
SW_TIMEOUT_HINT = 45

INDUSTRY_SW = "industry_sw.parquet"
INDUSTRY_CSR = "industry_csr.parquet"
CONCEPT = "concept.parquet"


# ---------------------------------------------------------------- 工具


def norm_code(raw) -> str:
    """把各种写法的代码归一成 6 位数字串。

    三个来源的写法各不相同，不统一就没法跟 `stock_list.code` 对上：
    - baostock:   ``sh.600000`` / ``sz.000001``
    - 申万成分:   ``600313.SH`` / ``000713.SZ``
    - 同花顺/本项目: ``600000``
    """
    s = str(raw).strip()
    if not s:
        return ""
    s = s.replace("sh.", "").replace("sz.", "").replace("bj.", "")
    if "." in s:
        s = s.split(".")[0]
    return s.zfill(6) if s.isdigit() and len(s) < 6 else s


def current_month() -> str:
    """当前快照月份（YYYY-MM）。按月快照的键。"""
    return date.today().strftime("%Y-%m")


def _month_dir(month: str | None = None) -> Path:
    m = month or current_month()
    datetime.strptime(m, "%Y-%m")  # 格式不对就抛错，别静默写到奇怪的目录
    return CLASSIFY_DIR / m


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> Path:
    """原子写 parquet。

    为什么不用 `df.to_parquet(path)` 直接写：应用可能在运行并同时读这份快照，
    直接覆盖会让读者读到半个文件。先写临时文件再 `os.replace`（原子改名）。
    与 `data/storage.py` 的 `save_bars` 是同一套约定。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------- 申万行业


class _HttpTimeout:
    """给 akshare 的 requests 调用**兜底超时**。

    为什么需要：akshare 内部 `requests.get(...)` 不传 timeout，上游一卡住就永久阻塞。
    实测 2026-10-27 全量抓申万成分时，进程跑了 13 分钟却只用掉 12.6 秒 CPU ——
    完全是在等网络，任务永远结束不了。月频任务无人值守，必须自己兜底。

    做法：临时替换 `requests.sessions.Session.request`，只在调用方**没传** timeout 时
    补一个默认值（`setdefault`），不影响显式指定超时的调用。退出时原样还原。
    """

    def __init__(self, seconds: float = 30.0):
        self.seconds = seconds
        self._orig = None

    def __enter__(self):
        import requests

        orig = requests.sessions.Session.request
        sec = self.seconds

        def patched(self_, *args, **kwargs):
            kwargs.setdefault("timeout", sec)
            return orig(self_, *args, **kwargs)

        self._orig = orig
        requests.sessions.Session.request = patched
        return self

    def __exit__(self, *exc_info):
        import requests

        if self._orig is not None:
            requests.sessions.Session.request = self._orig
        return False


def _retry(fn, *args, attempts: int = 3, delay: float = 2.0, what: str = "", **kwargs):
    """调用并重试瞬时失败。

    为什么需要：申万这几个表是靠**抓取网页**实现的（akshare 的 index_sw 模块），
    页面偶尔返回不含 `level1Items` 的版本，akshare 内部直接抛
    `AttributeError: 'NoneType' object has no attribute 'find_all'`。
    实测这是**瞬时**的 —— 2026-10-27 第一次全量抓取就撞上，隔一分钟重跑三个函数
    全部正常（各 1.4 秒）。月频任务一个月只跑一次，一次抖动就丢掉整月快照，
    代价太高，所以必须重试；但**必须重试后仍失败就抛错**，不能静默返回空表
    （那会写出一份"看起来正常"的残缺快照，比失败更难发现）。
    """
    import time

    last: Exception | None = None
    for i in range(attempts):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i < attempts - 1:
                time.sleep(delay)
    raise RuntimeError(
        f"{what or getattr(fn, '__name__', '调用')}连续 {attempts} 次失败："
        f"{type(last).__name__}: {last}"
    ) from last


def sw_hierarchy() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """取申万一级/二级/三级行业表（各 31 / 131 / 335 行）。

    为什么三级都要：成分接口只提供「申万3级」，但用户看行业时通常想看一级/二级
    （粒度太细的三级行业只有 2~8 只股票，没有统计意义）。层级靠
    `上级行业` 字段逐个回溯：三级→二级（third_info.上级行业）、二级→一级
    （second_info.上级行业）。
    """
    import akshare as ak

    with _HttpTimeout(45):
        first = _retry(ak.sw_index_first_info, what="申万一级行业表")
        second = _retry(ak.sw_index_second_info, what="申万二级行业表")
        third = _retry(ak.sw_index_third_info, what="申万三级行业表")
    return first, second, third


def fetch_industry_sw(limit: int | None = None, quiet: bool = False) -> pd.DataFrame:
    """抓申万三级成分 → 每只股票一行 `code, name, sw_l1, sw_l2, sw_l3, incl_date`。

    参数：
        limit: 只抓前 N 个三级行业（调试用；None = 全量 335 个）。
        quiet: 关掉进度输出。

    实测：单个三级行业约 0.47s，全量约 2.6 分钟。
    """
    import akshare as ak

    _first, second, third = sw_hierarchy()

    # 三级名 → 二级名（third_info 的「上级行业」就是二级名）
    l3_to_l2: dict[str, str] = dict(
        zip(third["行业名称"].astype(str), third["上级行业"].astype(str))
    )
    # 二级名 → 一级名（second_info 的「上级行业」就是一级名）
    l2_to_l1: dict[str, str] = dict(
        zip(second["行业名称"].astype(str), second["上级行业"].astype(str))
    )

    codes = [str(c) for c in third["行业代码"].tolist()]
    if limit:
        codes = codes[:limit]

    rows: list[dict] = []
    for i, sw3_code in enumerate(codes, 1):
        try:
            # 每个行业单独套超时：某个接口卡住时只损失这一个行业，不会拖死整轮
            with _HttpTimeout(SW_TIMEOUT_HINT):
                cons = _retry(
                    ak.sw_index_third_cons,
                    symbol=sw3_code,
                    attempts=2,
                    delay=1.0,
                    what=f"申万成分 {sw3_code}",
                )
        except Exception as exc:  # noqa: BLE001
            # 单个行业失败不该毁掉整次抓取 —— 记下来，下一次快照会重抓
            print(f"[warn] 申万成分 {sw3_code} 失败：{type(exc).__name__}: {exc}")
            continue
        if cons is None or cons.empty:
            continue
        for _, r in cons.iterrows():
            sw3 = str(r.get("申万3级") or "").strip()
            sw2 = l3_to_l2.get(sw3, "")
            sw1 = l2_to_l1.get(sw2, "")
            rows.append(
                {
                    "code": norm_code(r.get("股票代码")),
                    "name": str(r.get("股票简称") or "").strip(),
                    "sw_l1": sw1,
                    "sw_l2": sw2,
                    "sw_l3": sw3,
                    "incl_date": str(r.get("纳入时间") or "").strip(),
                }
            )
        if not quiet and (i % 25 == 0 or i == len(codes)):
            print(f"  申万成分 {i}/{len(codes)} 已抓，累计 {len(rows)} 行")
        if SW_CONS_DELAY:
            time.sleep(SW_CONS_DELAY)

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 同一只股票若出现在多个三级行业（理论上不该发生），保留行数最多的那条
    df = df.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)
    return df


def fetch_industry_sw_official(quiet: bool = False) -> pd.DataFrame:
    """用申万官网接口拼「股票 → 申万一级/二级」映射（乐咕乐股挂掉时的降级路径）。

    为什么需要这条路径：主源 `sw_index_*_info` / `sw_index_third_cons` 都是对
    **乐咕乐股网页**做 HTML 解析，实测整站会 504（2026-10-27 22:55 起连续三次
    504，`sw_index_first_info` 抛 `AttributeError: 'NoneType' object has no
    attribute 'find_all'`）。一次要跑 6 分钟的月度抓取不能只押在一个抓网页的源上。

    代价（实测，必须写清）：官网接口
    - `index_realtime_sw('一级行业')` 给 **31** 个名录、`('二级行业')` 给 **124**
      个（不是 131）、`('三级行业')` **直接失败**（0 行 → Length mismatch）；
    - `index_component_sw(code)` 给单个指数成分，且带「计入日期」。
    → 降级路径只能产出**一级/二级**，`sw_l3` 留空。调用方据此标记缺口，
    绝不用二级冒充三级（那会让"三级行业筛选"静默失效）。

    实测耗时：名录 2 次约 5 秒，成分 31+124 次约 2 分钟。
    """
    import akshare as ak

    roster: dict[str, tuple[str, int]] = {}  # 指数代码 → (名称, 层级)
    for lvl, sym in ((1, "一级行业"), (2, "二级行业")):
        df = _retry(ak.index_realtime_sw, symbol=sym, what=f"申万{lvl}级行业名录")
        for _, r in df.iterrows():
            ic = str(r.get("指数代码") or "").strip()
            nm = str(r.get("指数名称") or "").strip()
            if ic and nm:
                roster[ic] = (nm, lvl)
    if not quiet:
        print(f"  官网名录：一级/二级共 {len(roster)} 个指数")

    raw_rows: list[dict] = []
    for i, (ic, (nm, lvl)) in enumerate(roster.items(), 1):
        try:
            with _HttpTimeout(SW_TIMEOUT_HINT):
                cons = _retry(
                    ak.index_component_sw,
                    symbol=ic,
                    attempts=2,
                    delay=1.0,
                    what=f"申万官网成分 {ic} {nm}",
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[warn] 官网成分 {ic} {nm} 失败：{type(exc).__name__}: {exc}")
            continue
        if cons is None or cons.empty:
            continue
        for _, r in cons.iterrows():
            raw_rows.append(
                {
                    "code": norm_code(r.get("证券代码")),
                    "name": str(r.get("证券名称") or "").strip(),
                    "level": lvl,
                    "industry": nm,
                    "incl_date": str(r.get("计入日期") or "").strip()[:10],
                }
            )
        if not quiet and (i % 25 == 0 or i == len(roster)):
            print(f"  官网成分 {i}/{len(roster)}，累计 {len(raw_rows)} 条")
        if SW_CONS_DELAY:
            time.sleep(SW_CONS_DELAY)

    if not raw_rows:
        return pd.DataFrame(columns=["code", "name", "sw_l1", "sw_l2", "sw_l3", "incl_date"])
    raw = pd.DataFrame(raw_rows).drop_duplicates(subset=["code", "level"])

    # 申万分级分类本身是「划分 + 嵌套」：每只股票恰好属于一个二级、一个一级。
    # 用同一个 code 的 level 1/2 两行拼成一行（不靠行业名做 join，避免改名错配）。
    l1 = raw[raw["level"] == 1].set_index("code")["industry"]
    l2 = raw[raw["level"] == 2].set_index("code")
    base = raw.drop_duplicates(subset=["code"]).set_index("code")
    out = pd.DataFrame({"code": list(base.index)})
    out["name"] = out["code"].map(base["name"]).fillna("")
    out["sw_l1"] = out["code"].map(l1).fillna("")
    out["sw_l2"] = out["code"].map(l2["industry"]).fillna("")
    out["sw_l3"] = ""
    out["incl_date"] = out["code"].map(l2["incl_date"]).fillna("")
    if not quiet:
        n1 = int((out["sw_l1"] != "").sum())
        n2 = int((out["sw_l2"] != "").sum())
        print(
            f"  官网降级结果：{len(out)} 只股票，一级覆盖 {n1}、二级覆盖 {n2}"
            f"（三级留空——官网无三级名录接口）"
        )
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- 证监会行业


def fetch_industry_csr(quiet: bool = False) -> pd.DataFrame:
    """抓 baostock 证监会行业分类 → `code, name, csr_industry`（84 类）。

    为什么还要这一份：申万口径没覆盖到的股票（例如较新的票、或申万缺数据的），
    证监会行业能兜底；两份并存还能互相校验（同一个 code 两个口径是否矛盾）。

    实测耗时约 98 秒；`industry` 为空的 334 行正好是**退市股**（baostock 只给在市股）。
    baostock **惰性导入**：应用与云端不必安装它（与 `data/delisted.py` 同一约定）。
    """
    import socket

    import baostock as bs

    rows: list[dict] = []
    ok = False
    # baostock 走裸 socket 且不设超时：上游卡住会永久阻塞。
    # 设全局默认超时，结束后还原（避免影响同进程里别的网络调用）。
    _prev_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(60)
    try:
        lg = bs.login()
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败：{lg.error_msg}")
        rs = bs.query_stock_industry()
        fields = list(rs.fields)
        while rs.error_code == "0" and rs.next():
            rec = dict(zip(fields, rs.get_row_data()))
            rows.append(
                {
                    "code": norm_code(rec.get("code", "")),
                    "name": str(rec.get("code_name") or "").strip(),
                    "csr_industry": str(rec.get("industry") or "").strip(),
                    "csr_update": str(rec.get("updateDate") or "").strip(),
                }
            )
        ok = True
    finally:
        try:
            bs.logout()
        except Exception:  # noqa: BLE001
            pass
        socket.setdefaulttimeout(_prev_timeout)
    if not ok and not rows:
        return pd.DataFrame(columns=["code", "name", "csr_industry", "csr_update"])
    df = pd.DataFrame(rows)
    if not quiet:
        filled = int((df["csr_industry"] != "").sum()) if not df.empty else 0
        print(f"  证监会行业 {len(df)} 行，其中有行业 {filled} 行")
    return df


# ---------------------------------------------------------------- 概念（仅板块级）


def fetch_concept_list(quiet: bool = False) -> pd.DataFrame:
    """抓同花顺概念板块**列表** → `board, board_code`（375 个）。

    ⚠️ 注意这里**没有成分股**，也拿不到：东财被墙、akshare 无同花顺成分接口、
    富途只有 3 个美股概念。所以概念只能做板块级分析（指数行情 + 资金流），
    不能用于"按概念筛选个股"。实证见计划书 §2.3。
    """
    import akshare as ak

    df = ak.stock_board_concept_name_ths()
    out = df.rename(columns={"name": "board", "code": "board_code"})[["board", "board_code"]]
    if not quiet:
        print(f"  同花顺概念板块 {len(out)} 个（注：只有列表，没有成分股）")
    return out


# ---------------------------------------------------------------- 落盘/读取


def save_month(month: str | None = None, frames: dict[str, pd.DataFrame] | None = None) -> list[Path]:
    """把当月快照写入 `data/processed/classify/{YYYY-MM}/`，返回写入的文件列表。"""
    d = _month_dir(month)
    written: list[Path] = []
    for fname, df in (frames or {}).items():
        if df is None or df.empty:
            continue
        written.append(_atomic_write_parquet(df, d / fname))
    return written


def load_month(month: str | None = None) -> dict[str, pd.DataFrame]:
    """读当月快照；不存在就返回空 dict（调用方自己决定要不要现抓）。"""
    d = _month_dir(month)
    out: dict[str, pd.DataFrame] = {}
    for fname in (INDUSTRY_SW, INDUSTRY_CSR, CONCEPT):
        p = d / fname
        if p.exists():
            out[fname] = pd.read_parquet(p)
    return out


def available_months() -> list[str]:
    """已积累的月份（升序）。用于"从哪个月开始有数据"。"""
    if not CLASSIFY_DIR.exists():
        return []
    return sorted(
        p.name for p in CLASSIFY_DIR.iterdir() if p.is_dir() and p.name.count("-") == 1
    )


def latest_month() -> str | None:
    ms = available_months()
    return ms[-1] if ms else None


def industry_table(month: str | None = None) -> pd.DataFrame:
    """把申万与证监会两份行业合并成一张宽表（以申万为主，证监会兜底）。

    返回列：`code, name, sw_l1, sw_l2, sw_l3, csr_industry, incl_date`。
    同名股票以**申万**为准，申万缺的用证监会。
    """
    frames = load_month(month)
    sw = frames.get(INDUSTRY_SW, pd.DataFrame())
    csr = frames.get(INDUSTRY_CSR, pd.DataFrame())
    if sw.empty and csr.empty:
        return pd.DataFrame(
            columns=["code", "name", "sw_l1", "sw_l2", "sw_l3", "csr_industry", "incl_date"]
        )
    if sw.empty:
        out = csr.copy()
        for c in ("sw_l1", "sw_l2", "sw_l3", "incl_date"):
            out[c] = ""
    elif csr.empty:
        out = sw.copy()
        out["csr_industry"] = ""
    else:
        out = sw.merge(
            csr[["code", "csr_industry"]], on="code", how="left"
        )
        out["csr_industry"] = out["csr_industry"].fillna("")
    return out[["code", "name", "sw_l1", "sw_l2", "sw_l3", "csr_industry", "incl_date"]]


def industry_of(code: str, month: str | None = None) -> dict:
    """单只股票的行业信息（个股详情/图表展示用）。查不到返回空字段，不抛异常。"""
    code = norm_code(code)
    table = industry_table(month)
    if table.empty:
        return {"code": code, "sw_l1": "", "sw_l2": "", "sw_l3": "", "csr_industry": ""}
    hit = table[table["code"] == code]
    if hit.empty:
        return {"code": code, "sw_l1": "", "sw_l2": "", "sw_l3": "", "csr_industry": ""}
    r = hit.iloc[0]
    return {
        "code": code,
        "sw_l1": str(r.get("sw_l1") or ""),
        "sw_l2": str(r.get("sw_l2") or ""),
        "sw_l3": str(r.get("sw_l3") or ""),
        "csr_industry": str(r.get("csr_industry") or ""),
    }


def industry_options(level: str = "sw_l1", month: str | None = None) -> list[str]:
    """行业名列表（UI 多选用）。level ∈ {"sw_l1","sw_l2","sw_l3","csr_industry"}。"""
    table = industry_table(month)
    if table.empty or level not in table.columns:
        return []
    vals = [str(v) for v in table[level].tolist() if str(v).strip()]
    # 按该行业下的股票数从多到少排，常用的排在前面
    counts = pd.Series(vals).value_counts()
    return list(counts.index)


def filter_codes_by_industries(
    codes: list[str], industries: list[str] | None, level: str = "sw_l1",
    month: str | None = None,
) -> list[str]:
    """按行业筛选代码。industries 为空/None 表示不过滤（与 `factors.board` 的口径一致）。"""
    if not industries:
        return list(codes)
    table = industry_table(month)
    if table.empty or level not in table.columns:
        # 没有分类数据时**不要**静默返回空列表（那会让选股结果莫名其妙变成 0 只）
        return list(codes)
    keep = set(table.loc[table[level].isin(industries), "code"].astype(str))
    if not keep:
        return list(codes)
    return [c for c in codes if norm_code(c) in keep]


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    用法::

        python -m stock_plan.data.classification --industry   # 只抓申万（约 2.6 分钟）
        python -m stock_plan.data.classification --csr        # 只抓证监会（约 98 秒）
        python -m stock_plan.data.classification --concept    # 只抓概念列表（约 3.5 秒）
        python -m stock_plan.data.classification --all        # 全部（约 6 分钟）
        python -m stock_plan.data.classification --all --limit 20   # 调试：只抓 20 个三级行业
    """
    import argparse

    ap = argparse.ArgumentParser(description="抓取行业/概念分类并落成按月快照")
    ap.add_argument("--month", default=None, help="快照月份 YYYY-MM（默认当月）")
    ap.add_argument("--industry", action="store_true", help="抓申万三级成分")
    ap.add_argument("--csr", action="store_true", help="抓 baostock 证监会行业")
    ap.add_argument("--concept", action="store_true", help="抓同花顺概念列表")
    ap.add_argument("--all", action="store_true", help="上面三项全抓")
    ap.add_argument("--limit", type=int, default=None, help="申万只抓前 N 个三级行业（调试）")
    args = ap.parse_args(argv)

    do_all = args.all or not (args.industry or args.csr or args.concept)
    month = args.month or current_month()
    print(f"快照月份：{month} → {_month_dir(month)}")

    frames: dict[str, pd.DataFrame] = {}
    if do_all or args.industry:
        print("[1/3] 申万三级成分（约 2.6 分钟）…")
        t0 = time.time()
        try:
            frames[INDUSTRY_SW] = fetch_industry_sw(limit=args.limit)
        except Exception as exc:  # noqa: BLE001
            # 主源是网页抓取，整站 504 时必然失败；降级到官网接口而不是整轮报废
            print(f"[warn] 乐咕乐股申万源不可用：{type(exc).__name__}: {exc}")
            if args.limit:
                raise
            print("       降级到申万官网接口（只有一级/二级，三级留空）…")
            frames[INDUSTRY_SW] = fetch_industry_sw_official()
        if frames[INDUSTRY_SW].empty:
            raise RuntimeError("申万行业两条源都没抓到数据，拒绝写入空快照")
        print(f"      完成，{len(frames[INDUSTRY_SW])} 只股票，耗时 {time.time() - t0:.1f}s")
    if do_all or args.csr:
        print("[2/3] baostock 证监会行业（约 98 秒）…")
        t0 = time.time()
        frames[INDUSTRY_CSR] = fetch_industry_csr()
        print(f"      完成，{len(frames[INDUSTRY_CSR])} 行，耗时 {time.time() - t0:.1f}s")
    if do_all or args.concept:
        print("[3/3] 同花顺概念板块列表…")
        t0 = time.time()
        frames[CONCEPT] = fetch_concept_list()
        print(f"      完成，{len(frames[CONCEPT])} 个，耗时 {time.time() - t0:.1f}s")

    written = save_month(month, frames)
    for p in written:
        print(f"  写入 {p}  ({p.stat().st_size / 1024:.1f} KB)")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
