# -*- coding: utf-8 -*-
"""股本 / 市值字段：总股本、流通股本、总市值、流通市值（调研 B 档字段补齐）。

文件：src/stock_plan/data/share_capital.py
作者：Cwywwy
用途：为「总市值 / 流通市值 / 小市值分位」提供股本数据，并落盘可回溯的快照与历史
模块：data

为什么需要它：日线表只有 `amount` 与 `turnover`，此前只能算
`流通市值 = 成交额 ÷ 换手率`；**总市值（总股本）完全没有来源**，导致小市值类策略无法实现。

来源与口径（2026-10-29 实测）：
- 当期快照：东财 `push2.eastmoney.com/api/qt/clist/get`（`f2` 最新价 / `f20` 总市值 / `f21` 流通市值），
  一次分页请求覆盖沪深京 5900+ 只；akshare `stock_zh_a_spot_em()` 作为同源备选（本机偶发
  `RemoteDisconnected`，故做成「akshare 优先、直连兜底」）。
- **总股本 = 总市值 ÷ 最新价，流通股本 = 流通市值 ÷ 最新价**。不直接用接口的 `f84/f85`：
  实测存在负值（688137 为 −49187697）与量纲异常，不可信。
- 历史流通股本：由「流通市值 = 成交额 ÷ 换手率」**精确反推**
  `float_shares_t = amount_t ÷ (turnover_t × close_t)`，逐日无近似。
- 历史总股本：`total_shares_t = float_shares_t × (当期总股本 ÷ 当期流通股本)`，`source="proxy"`。
  定增/回购/解禁等股本变动无法回溯，**只能用于市值分档，不可用于精确市值**（已写入风险 R6）。

落盘（只增不改，天然可回溯）::

    data/processed/shares/YYYY-MM.parquet         # 当月快照
    data/processed/shares/history_float.parquet   # 逐日流通股本/流通市值（精确反推）
    data/processed/shares/history_total.parquet   # 逐日总股本/总市值（比例外推，source=proxy）

Revision History:
    2026-10-29  新建：股本/市值快照抓取、历史反推、落盘与读取
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pandas as pd

from stock_plan.data.clock import stamp_cn, today_cn

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SHARES_DIR = PROJECT_ROOT / "data" / "processed" / "shares"

SNAPSHOT_COLUMNS = [
    "code",
    "total_shares",
    "float_shares",
    "total_mv",
    "float_mv",
    "close",
    "as_of",
    "source",
]
HISTORY_FLOAT_COLUMNS = ["date", "code", "close", "float_shares", "float_mv"]
HISTORY_TOTAL_COLUMNS = ["date", "code", "total_shares", "total_mv", "source"]

# 东财 clist 接口：沪深京全市场（与 akshare `stock_zh_a_spot_em` 同一后端）。
# 2026-10-29 实测：本机 **https 被网络层重置**（RemoteDisconnected），http 正常，
# 因此这里用 http 作主通道，akshare 作备选。
_EM_URL = "http://push2.eastmoney.com/api/qt/clist/get"
_EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
_EM_FIELDS = "f2,f12,f14,f20,f21"
_EM_PAGE_SIZE = 100
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


# ---------------------------------------------------------------- 工具


def norm_code(raw) -> str:
    """归一成 6 位数字代码（去掉 sh./sz./bj. 前缀与后缀）。"""
    text = str(raw).strip().lower()
    for prefix in ("sh.", "sz.", "bj."):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if "." in text:
        text = text.split(".")[0]
    if not text.isdigit():
        raise ValueError(f"非法股票代码：{raw!r}")
    return text.zfill(6)


def _atomic_write_parquet(path: Path, df: pd.DataFrame) -> None:
    """原子写 parquet（先写临时文件再替换，避免中断留半个文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


def snapshot_path(as_of: str | None = None, out_dir: Path | str | None = None) -> Path:
    """当月快照文件路径（`as_of` 取 'YYYY-MM-DD' 的月份；默认今天）。"""
    base = Path(out_dir) if out_dir else SHARES_DIR
    month = (as_of or today_cn().isoformat())[:7]
    return base / f"{month}.parquet"


# ---------------------------------------------------------------- 抓取


def _snapshot_from_akshare() -> pd.DataFrame:
    """akshare 快照（同源备选）：代码/最新价/总市值/流通市值。"""
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        raise RuntimeError("akshare 快照为空")
    renamed = df.rename(
        columns={"代码": "code", "最新价": "close", "总市值": "total_mv", "流通市值": "float_mv"}
    )
    keep = [c for c in ("code", "close", "total_mv", "float_mv") if c in renamed.columns]
    if len(keep) < 4:
        raise RuntimeError(f"akshare 快照缺列：{list(df.columns)[:8]}")
    return renamed[keep].copy()


def _snapshot_from_eastmoney(timeout: float = 20.0) -> pd.DataFrame:
    """直连东财 clist 接口（分页），返回 code/close/total_mv/float_mv。"""
    import requests

    rows: list[dict] = []
    page = 1
    total = None
    while True:
        params = {
            "pn": page,
            "pz": _EM_PAGE_SIZE,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": _EM_FS,
            "fields": _EM_FIELDS,
        }
        resp = requests.get(
            _EM_URL, params=params, timeout=timeout, headers={"User-Agent": _UA}
        )
        resp.raise_for_status()
        payload = resp.json().get("data") or {}
        if total is None:
            total = int(payload.get("total") or 0)
        diff = payload.get("diff") or []
        if not diff:
            break
        for item in diff:
            code = str(item.get("f12") or "")
            if not code.isdigit():
                continue
            rows.append(
                {
                    "code": code.zfill(6),
                    "close": item.get("f2"),
                    "total_mv": item.get("f20"),
                    "float_mv": item.get("f21"),
                }
            )
        if total and len(rows) >= total:
            break
        page += 1
        if page > 200:  # 防御：接口异常时不要无限翻页
            break
    if not rows:
        raise RuntimeError("东财快照为空")
    return pd.DataFrame(rows)


def _normalise_snapshot(raw: pd.DataFrame, source: str) -> pd.DataFrame:
    """统一成快照口径：数值化 → 过滤无效 → 反推股本。"""
    df = raw.copy()
    df["code"] = df["code"].map(norm_code)
    for column in ("close", "total_mv", "float_mv"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df[df["close"] > 0].copy()
    df = df[df["total_mv"] > 0].copy()
    df = df.drop_duplicates(subset="code", keep="last")
    df["total_shares"] = df["total_mv"] / df["close"]
    df["float_shares"] = df["float_mv"] / df["close"]
    df["as_of"] = stamp_cn("%Y-%m-%d")
    df["source"] = source
    return df[SNAPSHOT_COLUMNS].reset_index(drop=True)


def fetch_share_snapshot(timeout: float = 20.0) -> pd.DataFrame:
    """抓全市场股本/市值快照（东财 http 直连优先，失败则退回 akshare）。"""
    try:
        raw = _snapshot_from_eastmoney(timeout=timeout)
        return _normalise_snapshot(raw, "eastmoney")
    except Exception as exc:  # noqa: BLE001 - 同源接口不稳，必须能兜底
        print(f"  东财直连失败（{type(exc).__name__}），改用 akshare")
        raw = _snapshot_from_akshare()
        return _normalise_snapshot(raw, "akshare")


# ---------------------------------------------------------------- 落盘 / 读取


def save_snapshot(df: pd.DataFrame, out_dir: Path | str | None = None) -> Path:
    """保存当月快照（同月重复抓取按 code 覆盖为最新值），返回文件路径。"""
    if df is None or df.empty:
        raise ValueError("快照为空，不写文件")
    frame = df.copy()
    frame["code"] = frame["code"].map(norm_code)
    frame = frame.drop_duplicates(subset="code", keep="last")
    path = snapshot_path(frame["as_of"].iloc[0] if "as_of" in frame else None, out_dir)
    _atomic_write_parquet(path, frame)
    return path


def _snapshot_files(base: Path) -> list[Path]:
    """只挑「YYYY-MM.parquet」形式的快照，避开同目录的 history_*.parquet。"""
    if not base.exists():
        return []
    return sorted(path for path in base.glob("*.parquet") if len(path.stem) == 7 and path.stem[4] == "-")


def load_snapshot(
    out_dir: Path | str | None = None, month: str | None = None
) -> pd.DataFrame:
    """读快照：默认最新月份；文件不存在返回空 DataFrame（不抛错）。"""
    base = Path(out_dir) if out_dir else SHARES_DIR
    if month:
        path = base / f"{month[:7]}.parquet"
    else:
        files = _snapshot_files(base)
        if not files:
            return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
        path = files[-1]
    if not path.exists():
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)
    return pd.read_parquet(path)


def available_months(out_dir: Path | str | None = None) -> list[str]:
    """已落盘的快照月份（'YYYY-MM'，升序）。"""
    base = Path(out_dir) if out_dir else SHARES_DIR
    return [path.stem for path in _snapshot_files(base)]


# ---------------------------------------------------------------- 历史反推


def derive_float_shares_history(
    bars_map: dict[str, pd.DataFrame],
    snapshot: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """逐日反推流通股本：`float_shares = amount ÷ (turnover × close)`。

    参数：
        bars_map: 代码 → 日线（需 date/close/amount/turnover 列）。
        snapshot: 只用来限定股票范围（None 表示全部）。

    返回：长表 `date, code, close, float_shares, float_mv`；换手率 ≤0 或价格 ≤0 的行丢弃。
    """
    keep_codes = set(snapshot["code"]) if snapshot is not None and not snapshot.empty else None
    frames: list[pd.DataFrame] = []
    for code, bars in bars_map.items():
        if keep_codes is not None and code not in keep_codes:
            continue
        if bars is None or bars.empty:
            continue
        need = {"date", "close", "amount", "turnover"}
        if not need.issubset(bars.columns):
            continue
        frame = bars.loc[:, ["date", "close", "amount", "turnover"]].copy()
        for column in ("close", "amount", "turnover"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        valid = (frame["close"] > 0) & (frame["turnover"] > 0) & (frame["amount"] > 0)
        frame = frame[valid].copy()
        if frame.empty:
            continue
        frame["float_mv"] = frame["amount"] / frame["turnover"]
        frame["float_shares"] = frame["float_mv"] / frame["close"]
        frame["code"] = code
        frames.append(frame[HISTORY_FLOAT_COLUMNS])
    if not frames:
        return pd.DataFrame(columns=HISTORY_FLOAT_COLUMNS)
    out = pd.concat(frames, ignore_index=True)
    return out.sort_values(["code", "date"]).reset_index(drop=True)


def derive_total_shares_history(
    history_float: pd.DataFrame, snapshot: pd.DataFrame
) -> pd.DataFrame:
    """用当期「总股本 ÷ 流通股本」比例外推历史总股本（`source="proxy"`）。"""
    if history_float is None or history_float.empty:
        return pd.DataFrame(columns=HISTORY_TOTAL_COLUMNS)
    if snapshot is None or snapshot.empty:
        return pd.DataFrame(columns=HISTORY_TOTAL_COLUMNS)
    ratio = snapshot.loc[:, ["code", "float_shares", "total_shares"]].copy()
    ratio = ratio[ratio["float_shares"] > 0]
    ratio["ratio"] = ratio["total_shares"] / ratio["float_shares"]
    ratio = ratio.drop_duplicates(subset="code", keep="last")

    out = history_float.merge(ratio[["code", "ratio"]], on="code", how="inner")
    out["total_shares"] = out["float_shares"] * out["ratio"]
    out["total_mv"] = out["total_shares"] * out["close"]
    out["source"] = "proxy"
    return out[HISTORY_TOTAL_COLUMNS].sort_values(["code", "date"]).reset_index(drop=True)


def save_history(history_float: pd.DataFrame, history_total: pd.DataFrame,
                 out_dir: Path | str | None = None) -> tuple[Path, Path]:
    """落盘两张历史长表，返回 (float 路径, total 路径)。"""
    base = Path(out_dir) if out_dir else SHARES_DIR
    float_path = base / "history_float.parquet"
    total_path = base / "history_total.parquet"
    _atomic_write_parquet(float_path, history_float)
    _atomic_write_parquet(total_path, history_total)
    return float_path, total_path


def load_history(kind: str = "float", out_dir: Path | str | None = None) -> pd.DataFrame:
    """读历史长表：kind ∈ {'float', 'total'}；缺失返回空表。"""
    base = Path(out_dir) if out_dir else SHARES_DIR
    path = base / f"history_{kind}.parquet"
    if not path.exists():
        columns = HISTORY_FLOAT_COLUMNS if kind == "float" else HISTORY_TOTAL_COLUMNS
        return pd.DataFrame(columns=columns)
    return pd.read_parquet(path)


def coverage(snapshot: pd.DataFrame, history_float: pd.DataFrame | None = None) -> dict:
    """覆盖率概况（供回补脚本与报告使用）。"""
    codes = int(snapshot["code"].nunique()) if snapshot is not None and not snapshot.empty else 0
    result = {
        "snapshot_codes": codes,
        "snapshot_as_of": str(snapshot["as_of"].iloc[0]) if codes else "",
        "snapshot_source": str(snapshot["source"].iloc[0]) if codes else "",
    }
    if history_float is not None and not history_float.empty:
        result.update(
            {
                "history_rows": int(len(history_float)),
                "history_codes": int(history_float["code"].nunique()),
                "history_first": str(history_float["date"].min())[:10],
                "history_last": str(history_float["date"].max())[:10],
            }
        )
    return result


def _main(argv: list[str] | None = None) -> int:
    """命令行：抓一次快照并落盘（历史长表由 scripts 回补脚本生成）。"""
    import argparse

    parser = argparse.ArgumentParser(description="抓全市场股本/市值快照")
    parser.add_argument("--dry-run", action="store_true", help="只抓不落盘")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出统计")
    args = parser.parse_args(argv)

    started = datetime.now()
    snapshot = fetch_share_snapshot()
    if not args.dry_run:
        path = save_snapshot(snapshot)
    stats = coverage(snapshot)
    stats["elapsed_sec"] = round((datetime.now() - started).total_seconds(), 1)
    if args.json:
        print(json.dumps(stats, ensure_ascii=False))
    else:
        print(f"快照：{stats['snapshot_codes']} 只，口径 {stats['snapshot_source']}，日期 {stats['snapshot_as_of']}")
        if not args.dry_run:
            print(f"已落盘：{path}")
    return 0 if stats["snapshot_codes"] else 1


if __name__ == "__main__":
    raise SystemExit(_main())
