# -*- coding: utf-8 -*-
"""动量因子模块 — 均线相对价格比（研报口径），修正 A 股裸涨幅动量方向问题。

用途：
- 生成 `ma_ratio_{20,60,120,240} = MA_L / close` 四列；
- 供普适因子行（`build_factor_rows`）与回测引擎两条路径共用；
- 供 `MomentumStrategy` 的 `momentum_mode` 切换使用。

所属模块：factors（纯计算层，无策略语义、无外部依赖）。

方向约定（**必须按这个方向用**）：
- `ma_ratio_L` 越小 = 价格越在均线之上 = 动量越强；越接近 1 或大于 1 = 价格在均线附近/之下 = 动量弱。
- 研报实测：A 股裸涨幅动量（M1/M3/M6/M12/M24）IC 全为负，改用 `MA_L / close` 后单调性转正；
  但「单调性转正」不等于「能赚钱」，需本项目回测验证（见 `.github/plans/2026-10-29-4-...` Phase 8）。

Revision History:
    2026-10-29  (phase 8) add MA/close momentum ratio columns (20/60/120/240)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 研报口径的四个回看窗口（MA_20 / 60 / 120 / 240）
MA_RATIO_WINDOWS: tuple[int, ...] = (20, 60, 120, 240)

# 固定列名，方便 parity 测试与两路径对齐
MA_RATIO_COLUMNS: tuple[str, ...] = tuple(f"ma_ratio_{_w}" for _w in MA_RATIO_WINDOWS)


def ma_ratio_columns(
    frame: pd.DataFrame, windows: tuple[int, ...] = MA_RATIO_WINDOWS
) -> pd.DataFrame:
    """计算 `MA_L / close` 四列（与输入同索引）。

    参数：
        frame:  含 `close` 列的日线或因子 DataFrame（按日期升序）。
        windows: 回看窗口，默认 `(20, 60, 120, 240)`。

    返回：
        DataFrame，列 `ma_ratio_20 / ma_ratio_60 / ma_ratio_120 / ma_ratio_240`。
        窗口不足（前 L-1 行）为 NaN，不填充；`close <= 0` 时该行为 NaN。

    方向：**越小越强**（价格高于均线），详见模块 docstring。
    """
    if "close" not in frame.columns:
        return pd.DataFrame(
            {f"ma_ratio_{_w}": np.nan for _w in windows}, index=frame.index
        )
    close = pd.to_numeric(frame["close"], errors="coerce").astype(float)
    safe_close = close.where(close > 0)
    out = pd.DataFrame(index=frame.index)
    for window in windows:
        ma = close.rolling(window, min_periods=window).mean()
        out[f"ma_ratio_{window}"] = ma / safe_close
    return out


def add_ma_ratio_columns(
    frame: pd.DataFrame, windows: tuple[int, ...] = MA_RATIO_WINDOWS
) -> pd.DataFrame:
    """把四个 `ma_ratio_*` 列追加到既有 DataFrame（回测引擎预计算用）。

    参数：
        frame:   含 `close` 列的 DataFrame（不会被原地修改）。
        windows: 回看窗口。

    返回：
        frame 的副本，追加 `ma_ratio_{L}` 四列。
    """
    out = frame.copy()
    ratios = ma_ratio_columns(frame, windows)
    for column in ratios.columns:
        out[column] = ratios[column].to_numpy()
    return out


def ma_ratio_strength(
    frame: pd.DataFrame, window: int = 60
) -> pd.Series:
    """动量强度（越大越强）：`1 - ma_ratio_L`。

    与 `mom_ret` 同量纲（都是相对涨幅的小数），便于复用「-20%~+30% 映射 0~30 分」的映射。
    """
    column = f"ma_ratio_{window}"
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index)
    ratio = pd.to_numeric(frame[column], errors="coerce").astype(float)
    return 1.0 - ratio


def cross_section_percentile(series: pd.Series) -> pd.Series:
    """单列截面百分位（0~1，NaN 保持 NaN）。

    用途：`ma_ratio_blend` 需要把四个不同窗口的强度合成，先各自转百分位再平均，
    避免量纲/窗口差异互相压制。
    """
    numeric = pd.to_numeric(series, errors="coerce").astype(float)
    return numeric.rank(pct=True, method="average", na_option="keep")
