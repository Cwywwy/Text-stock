# -*- coding: utf-8 -*-
"""字段消费接口：股本 / 市值 / 披露日的统一取值（缺数据即降级，不抛错）。

文件：src/stock_plan/data/fields.py
作者：Cwywwy
用途：让策略、信号、UI 用同一套 `.get()` 风格拿 B 档字段，缺数据时返回降级说明
模块：data

为什么单独一层：调研 B 档里的总股本、披露日都是「补不齐就降级」的字段。如果各处直接读
parquet，缺文件/缺股票时会抛错或静默返回 NaN，前者让盘前任务整体失败，后者让用户不知道
「今天哪条规则少了输入」。统一接口后：

- 每个取值函数返回 dict：`value`（可能为 None）、`source`（数据来源，可追溯）、`degraded`（中文说明）；
- 缺数据一律返回 `value=None, degraded="..."`，**不抛异常**；
- `source` 区分精度：`history_float`（成交额÷换手率÷收盘价，精确）、`snapshot`（当期快照）、
  `proxy`（总股本按当期比例外推）、`announce`（真实披露日）、`statutory`（法定截止日兜底）。

Revision History:
    2026-10-29  新建：股本/市值/披露日取值接口与降级说明
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd

from stock_plan.data import disclosure as _disclosure
from stock_plan.data import share_capital as _shares


def _out_dir(out_dir: Path | str | None) -> Path:
    return Path(out_dir) if out_dir else _shares.SHARES_DIR


@lru_cache(maxsize=8)
def _load_frame(kind: str, out_dir: str) -> pd.DataFrame:
    """缓存读取（同一进程内多次取值不重复读盘）；文件缺失返回空表。"""
    base = Path(out_dir)
    if kind == "snapshot":
        return _shares.load_snapshot(base)
    return _shares.load_history(kind, base)


def clear_cache() -> None:
    """清空读取缓存（回补脚本落盘新数据后调用）。"""
    _load_frame.cache_clear()


def _pick(frame: pd.DataFrame, code: str, date) -> pd.Series | None:
    """在长表里取 code 截至 date 的最近一行；取不到返回 None。"""
    if frame is None or frame.empty:
        return None
    target = _disclosure._parse_day(date)  # 复用同一套日期归一（含 'YYYY-MM-DD' 校验）
    if not target:
        return None
    subset = frame[frame["code"].astype(str) == str(code)]
    if subset.empty:
        return None
    subset = subset.copy()
    subset["_day"] = subset["date"].map(_disclosure._parse_day)
    subset = subset[(subset["_day"] != "") & (subset["_day"] <= target)]
    if subset.empty:
        return None
    return subset.sort_values("_day").iloc[-1]


def _missing(field: str, code: str, date) -> dict:
    return {
        "value": None,
        "source": None,
        "degraded": f"{field}：{code} 在 {date} 无可用数据（缺历史表或未回补）",
    }


def _bad_date(field: str, date) -> dict | None:
    """日期无法解析时直接降级（不拿快照兜底，否则「截至某日」的语义就失效了）。"""
    if _disclosure._parse_day(date):
        return None
    return {"value": None, "source": None, "degraded": f"{field}：非法日期 {date!r}，无法按时点取值"}


# ---------------------------------------------------------------- 股本


def as_of_float_shares(code: str, date, *, out_dir: Path | str | None = None) -> dict:
    """流通股本（优先历史精确反推，退回当期快照）。"""
    bad = _bad_date("流通股本", date)
    if bad:
        return bad
    base = _out_dir(out_dir)
    row = _pick(_load_frame("float", str(base)), code, date)
    if row is not None:
        value = float(row["float_shares"])
        if value > 0:
            return {"value": value, "source": "history_float", "degraded": None}
    snapshot = _load_frame("snapshot", str(base))
    if not snapshot.empty:
        hit = snapshot[snapshot["code"].astype(str) == str(code)]
        if not hit.empty and float(hit["float_shares"].iloc[-1]) > 0:
            return {
                "value": float(hit["float_shares"].iloc[-1]),
                "source": "snapshot",
                "degraded": f"流通股本：{code} 缺 {date} 历史，退回最新快照（{hit['as_of'].iloc[-1]}）",
            }
    return _missing("流通股本", code, date)


def as_of_total_shares(code: str, date, *, out_dir: Path | str | None = None) -> dict:
    """总股本（历史为当期比例外推，`source="proxy"`）。"""
    bad = _bad_date("总股本", date)
    if bad:
        return bad
    base = _out_dir(out_dir)
    row = _pick(_load_frame("total", str(base)), code, date)
    if row is not None:
        value = float(row["total_shares"])
        if value > 0:
            return {
                "value": value,
                "source": "proxy",
                "degraded": f"总股本：{code} 按当期比例外推（定增/回购不可回溯，仅可用于分档）",
            }
    snapshot = _load_frame("snapshot", str(base))
    if not snapshot.empty:
        hit = snapshot[snapshot["code"].astype(str) == str(code)]
        if not hit.empty and float(hit["total_shares"].iloc[-1]) > 0:
            return {
                "value": float(hit["total_shares"].iloc[-1]),
                "source": "snapshot",
                "degraded": f"总股本：{code} 缺 {date} 历史，退回最新快照（{hit['as_of'].iloc[-1]}）",
            }
    return _missing("总股本", code, date)


def as_of_total_mv(code: str, date, close: float | None,
                   *, out_dir: Path | str | None = None) -> dict:
    """总市值 = 总股本 × 当日收盘价（股本为外推口径时同样标 proxy）。"""
    if close is None or not pd.notna(close) or float(close) <= 0:
        return {"value": None, "source": None,
                "degraded": f"总市值：{code} 在 {date} 缺有效收盘价"}
    shares = as_of_total_shares(code, date, out_dir=out_dir)
    if shares["value"] is None:
        return {"value": None, "source": None, "degraded": shares["degraded"]}
    scale = 1e8 if shares["value"] < 1e6 else 1.0  # 快照单位为股；若上游已换算成亿股则兼容
    value = float(shares["value"]) * float(close) * scale
    return {
        "value": value,
        "source": "proxy" if shares["source"] in {"proxy", "history_float"} else shares["source"],
        "degraded": shares["degraded"],
    }


def as_of_float_mv(
    code: str,
    date,
    close: float | None = None,
    amount: float | None = None,
    turnover: float | None = None,
    *,
    out_dir: Path | str | None = None,
) -> dict:
    """流通市值：优先当日 `成交额 ÷ 换手率`（精确），否则退回股本 × 收盘价。"""
    if amount is not None and turnover is not None and pd.notna(amount) and pd.notna(turnover):
        if float(turnover) > 0 and float(amount) > 0:
            return {
                "value": float(amount) / float(turnover),
                "source": "amount_turnover",
                "degraded": None,
            }
    if close is None or not pd.notna(close) or float(close) <= 0:
        return _missing("流通市值", code, date)
    shares = as_of_float_shares(code, date, out_dir=out_dir)
    if shares["value"] is None:
        return {"value": None, "source": None, "degraded": shares["degraded"]}
    return {
        "value": float(shares["value"]) * float(close),
        "source": "shares_close",
        "degraded": shares["degraded"],
    }


# ---------------------------------------------------------------- 披露日


def as_of_disclosure(code: str, date, *, db_path: Path | str | None = None) -> dict:
    """截至 date 最近一期已公开财报的披露日（真实公告日优先，法定截止日兜底）。"""
    result = _disclosure.as_of_disclosure(code, date, db_path=db_path)
    return {
        "value": result["value"],
        "source": result["source"],
        "report_period": result["report_period"],
        "degraded": result["degraded"],
    }


def field_snapshot(
    codes: list[str],
    date,
    *,
    bars_map: dict[str, pd.DataFrame] | None = None,
    out_dir: Path | str | None = None,
    db_path: Path | str | None = None,
) -> pd.DataFrame:
    """批量取一份字段表（供报告/诊断用）：code + 股本 + 市值 + 披露日 + 降级原因。

    参数：
        codes: 股票代码列表。
        date: 时点（'YYYY-MM-DD' 或 date/Timestamp）。
        bars_map: 可选，含当日 close/amount/turnover 时用于算流通市值。
    """
    rows: list[dict] = []
    for code in codes:
        close = amount = turnover = None
        frame = (bars_map or {}).get(str(code))
        if frame is not None and not frame.empty:
            day = _disclosure._parse_day(date)
            hit = frame[frame["date"].map(_disclosure._parse_day) == day]
            source = hit if not hit.empty else frame.iloc[[-1]]
            row = source.iloc[-1]
            close = float(row["close"]) if "close" in row else None
            amount = float(row["amount"]) if "amount" in row else None
            turnover = float(row["turnover"]) if "turnover" in row else None
        total_mv = as_of_total_mv(code, date, close, out_dir=out_dir)
        float_mv = as_of_float_mv(code, date, close, amount, turnover, out_dir=out_dir)
        disc = as_of_disclosure(code, date, db_path=db_path)
        rows.append(
            {
                "code": str(code),
                "close": close,
                "float_mv": float_mv["value"],
                "float_mv_source": float_mv["source"],
                "total_mv": total_mv["value"],
                "total_mv_source": total_mv["source"],
                "disclosure_date": disc["value"],
                "disclosure_source": disc["source"],
                "report_period": disc.get("report_period"),
                "degraded": "; ".join(
                    text for text in (total_mv["degraded"], float_mv["degraded"], disc["degraded"]) if text
                ),
            }
        )
    return pd.DataFrame(rows)
