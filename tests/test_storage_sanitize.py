# -*- coding: utf-8 -*-
"""行情清洗测试：零值占位行必须被剔除（2026-10-15 端到端验收发现）。

数据源（新浪）偶尔会给出 `open=high=low=0`、`volume=0`、`close=沿用前收` 的停牌占位行
（实测全库 3/5890 只，均在 2024-11-06）。这类行会让 ATR 的 TR 变成 |0-前收|，
把 ATR 放大 1.5~1.8 倍并持续近一个月，从而把止盈/止损价算歪。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from stock_plan.data.storage import Storage, _sanitize_bars
from stock_plan.factors.technical import compute_technical


def _bars_with_zero_row() -> pd.DataFrame:
    n = 30
    dates = pd.bdate_range("2024-10-01", periods=n + 1)
    close = np.linspace(20.0, 21.0, n + 1)
    df = pd.DataFrame({
        "date": dates, "open": close, "high": close * 1.01,
        "low": close * 0.99, "close": close,
        "volume": 1_000_000.0, "amount": 2e7, "turnover": 0.01,
    })
    # 第 15 行变成零值占位行（close 沿用前收）
    i = 15
    df.loc[i, ["open", "high", "low"]] = 0.0
    df.loc[i, "volume"] = 0.0
    df.loc[i, "amount"] = 0.0
    df.loc[i, "turnover"] = 0.0
    df.loc[i, "close"] = df.loc[i - 1, "close"]
    return df


def test_sanitize_drops_zero_price_rows() -> None:
    df = _bars_with_zero_row()
    assert len(df) == 31
    out = _sanitize_bars(df)
    assert len(out) == 30, "含零值的那一行应被剔除"
    assert (out[["open", "high", "low"]] > 0).all().all()


def test_sanitize_keeps_clean_frame_untouched() -> None:
    df = _bars_with_zero_row().drop(index=15).reset_index(drop=True)
    out = _sanitize_bars(df)
    assert len(out) == len(df)
    assert out["close"].equals(df["close"])


def test_sanitize_handles_empty_and_missing_columns() -> None:
    assert _sanitize_bars(pd.DataFrame()).empty
    assert _sanitize_bars(None) is None
    # 缺 open/high/low 时不应报错
    bare = pd.DataFrame({"date": pd.bdate_range("2024-10-01", periods=3), "close": [1.0, 2, 3]})
    assert len(_sanitize_bars(bare)) == 3


def test_zero_row_inflates_atr_so_sanitizing_matters() -> None:
    """量化危害：不剔除时 ATR 被显著放大 —— 这正是要清洗的理由。"""
    dirty_src = _bars_with_zero_row()
    clean_src = _sanitize_bars(dirty_src)

    dirty_atr = compute_technical(dirty_src)["atr14"].max()
    clean_atr = compute_technical(clean_src)["atr14"].max()

    assert dirty_atr > clean_atr * 1.5, (
        f"零值行本应显著放大 ATR（脏 {dirty_atr:.3f} vs 干净 {clean_atr:.3f}）"
    )


def test_load_bars_applies_sanitization(tmp_path) -> None:
    """端到端：经 Storage 读出来的行情不应含零值行。"""
    st = Storage(bars_dir=tmp_path, db_path=tmp_path / "meta.db")
    df = _bars_with_zero_row()
    st.save_bars("600000", df)
    loaded = st.load_bars("600000")
    assert len(loaded) == 30
    assert (loaded[["open", "high", "low"]] > 0).all().all()