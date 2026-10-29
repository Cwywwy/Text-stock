# -*- coding: utf-8 -*-
"""大盘择时档位（双均线三档）— 由大盘等权指数推导目标仓位比例。

================================================================================
文件    : src/stock_plan/factors/timing.py
作者    : Cwywwy
用途    : 为回测引擎与行业轮动回测提供「双均线三档」仓位档位序列。
          规则固定、不可调参（预注册口径，见
          `.github/plans/2026-10-29-7-new-strategy-rescreen-and-timing-tiers.md` §3.1）：

              close >= MA20                                   → 100%
              close <  MA20，且不在下一行条件内                → 50%
              close <  MA60 且 MA20[t] < MA20[t-5]（向下）     → 0%

          预热期（MA60 未成形或不足 20 个交易日）一律按 100% 处理，不做静默假设。
          档位只用**收盘后**数据计算，且**次日生效**（`exposure_series` 内部 shift(1)），
          避免"用当日收盘信息在当日开盘就调仓"的未来函数。
所属模块: factors（因子与市场状态）
================================================================================

修订记录：
- 2026-10-29 (phase 19) 新建：三档档位序列、次日生效、预热按满仓。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 档位口径（字符串是回测入口的合法取值；真实档位用下面的浮点暴露比例）
TIMING_TIERS: tuple[str, ...] = ("off", "dual_ma")
DEFAULT_TIMING_TIER = "off"

# 目标仓位比例：满仓 / 半仓 / 清仓
EXPOSURE_FULL = 1.0
EXPOSURE_HALF = 0.5
EXPOSURE_EMPTY = 0.0

MA_SHORT = 20
MA_LONG = 60
SLOPE_LAG = 5  # MA20 斜率回看 5 个交易日


def validate_timing_tier(tier: str) -> str:
    """校验档位名称；非法值抛中文 ValueError（不静默回退）。"""
    if tier not in TIMING_TIERS:
        raise ValueError(f"未知的择时档位：{tier!r}；可选值：{', '.join(TIMING_TIERS)}")
    return tier


def tier_series(index: pd.Series, *, short: int = MA_SHORT, long: int = MA_LONG,
                slope_lag: int = SLOPE_LAG) -> pd.Series:
    """按大盘等权指数收盘序列计算**当日**档位（1.0 / 0.5 / 0.0）。

    参数
    ----
    index : 大盘等权收益指数（首日 = 1.0 的净值序列，DatetimeIndex）
    short / long / slope_lag : 短均线、长均线、斜率回看窗口（固定值，仅为可测性保留）

    返回
    ----
    与 ``index`` 同索引的浮点序列；预热期（短均线未成形）填 1.0（满仓），
    长均线未成形时只可能给 1.0 或 0.5，不会因为"MA60 是 NaN"而误判清仓。
    """
    close = index.astype(float)
    ma_short = close.rolling(short).mean()
    ma_long = close.rolling(long).mean()
    # 严格小于才算"向下"（相等不算，避免边界抖动）
    ma_down = ma_short < ma_short.shift(slope_lag)

    tier = pd.Series(np.nan, index=close.index, dtype=float)
    valid = ma_short.notna()
    tier[valid & (close >= ma_short)] = EXPOSURE_FULL
    near_empty = valid & (close < ma_short) & ma_long.notna() & ma_down
    tier[near_empty] = EXPOSURE_EMPTY
    half = valid & (close < ma_short) & ~(ma_long.notna() & ma_down)
    tier[half] = EXPOSURE_HALF
    return tier.fillna(EXPOSURE_FULL)


def exposure_series(index: pd.Series, *, shift: int = 1, **kwargs) -> pd.Series:
    """**次日生效**的目标仓位序列：第 t 日收盘算出的档位，从 t+1 日起执行。

    预热期与首个可判定日之前按满仓处理（``fillna(1.0)``），并在报告中注明。
    """
    return tier_series(index, **kwargs).shift(shift).fillna(EXPOSURE_FULL)


def tier_table(index: pd.Series, **kwargs) -> pd.DataFrame:
    """档位明细表（供报告与单测核对）：含均线、斜率方向、当日档位、次日生效仓位。"""
    close = index.astype(float)
    ma_short = close.rolling(kwargs.get("short", MA_SHORT)).mean()
    ma_long = close.rolling(kwargs.get("long", MA_LONG)).mean()
    ma_down = ma_short < ma_short.shift(kwargs.get("slope_lag", SLOPE_LAG))
    tier = tier_series(index, **kwargs)
    return pd.DataFrame(
        {
            "close": close,
            "ma_short": ma_short,
            "ma_long": ma_long,
            "ma_down": ma_down,
            "tier": tier,
            "exposure": tier.shift(kwargs.get("shift", 1)).fillna(EXPOSURE_FULL),
        }
    )


def tier_counts(tier: pd.Series) -> dict[str, int]:
    """统计各档位的交易日数（报告用）。"""
    counts = tier.value_counts().to_dict()
    return {
        "满仓(100%)": int(counts.get(EXPOSURE_FULL, 0)),
        "半仓(50%)": int(counts.get(EXPOSURE_HALF, 0)),
        "清仓(0%)": int(counts.get(EXPOSURE_EMPTY, 0)),
    }


if __name__ == "__main__":  # pragma: no cover - 手工自测
    import pandas as pd

    rng = np.random.default_rng(0)
    idx = pd.Series(
        np.cumprod(1 + rng.normal(0.0002, 0.01, 400)),
        index=pd.bdate_range("2023-01-02", periods=400),
    )
    print(tier_counts(tier_series(idx)))
    print(tier_table(idx).tail(5))
