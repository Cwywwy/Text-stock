"""均线收敛因子（PCF / VCF / PVCF / ACF / TRCF）。

来源
----
策略选型调研 §3.4 候选 1（`.github/plans/2026-10-29-2-new-strategy-research-and-selection.md`
与 `docs/策略选型调研-2026-10-29.md`）。研报口径：

    factor = −log(1 + std(MA_1, MA_5, MA_10, MA_20, MA_60, MA_120))

其中 `MA_1` 就是原始价格（1 日均线 = 收盘价）。均线越收敛 → std 越小 → factor 越接近 0；
均线越发散 → std 越大 → factor 越负。所以 **数值越大 = 越收敛**。

五变体
------
- PCF：收盘价均线收敛；
- VCF：成交量均线收敛；
- ACF：成交额均线收敛；
- TRCF：换手率均线收敛；
- PVCF：PCF 与 VCF 的**截面 z-score 相加**（研报合成口径，见 `compose_pvcf()`）。

量纲问题（计划 §2.6，必须知情）
-------------------------------
`std` 的单位是「元」（或手/元/小数），所以原始 PCF/VCF/ACF/TRCF **跨股票不可比**：
同样 2% 的均线离散度，5 元股的 std≈0.1、1300 元股的 std≈26，得到的 factor 分别是
−0.095 与 −3.3。直接比大小等于偷偷引入「价格水平/成交量水平」暴露。

因此本模块同时提供：
- `convergence_series()`：文档原式（主口径，可复现研报）；
- `relative_convergence_series()`：无量纲诊断口径 `−log(1 + std(MA)/mean(MA))`，
  输出为诊断列 `conv_pcf_cv`，只进报告、不进打分。

**策略打分时必须先做截面标准化（z-score 或百分位）再用**，绝不直接比较原始列大小；
`compose_pvcf()` 与 `cross_section_zscore()` 已实现截面标准化，可直接复用。

预热期
------
MA_120 需要 120 根 K 线；不足时结果为 NaN（不静默填 0，避免把「没有历史」当成
「完美收敛」）。调用方决定降级策略（策略侧统一按中性处理）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# 研报给定的五个均线窗口（不含 MA_1）
CONVERGENCE_WINDOWS: tuple[int, ...] = (5, 10, 20, 60, 120)

# 实际参与计算的均线窗口：MA_1 就是原始序列本身
MA_WINDOWS: tuple[int, ...] = (1,) + CONVERGENCE_WINDOWS

# 四列原始收敛因子 + 一列截面合成
CONV_RAW_COLUMNS: tuple[str, ...] = ("conv_pcf", "conv_vcf", "conv_acf", "conv_trcf")
CONV_COLUMNS: tuple[str, ...] = CONV_RAW_COLUMNS + ("conv_pvcf",)

# 诊断列（无量纲口径，只进报告，不进因子行/打分）
CONV_DIAG_COLUMNS: tuple[str, ...] = ("conv_pcf_cv",)

# 收敛因子 → 原始序列列名
_SOURCE_MAP: dict[str, str] = {
    "conv_pcf": "close",
    "conv_vcf": "volume",
    "conv_acf": "amount",
    "conv_trcf": "turnover",
}


def ma_stack(series: pd.Series, windows: tuple[int, ...] = MA_WINDOWS) -> pd.DataFrame:
    """把同一序列的多个窗口均线拼成 DataFrame（列名 `ma{w}`）。

    MA_1 用 `rolling(1).mean()` 得到，等价于原始序列本身。
    """
    s = pd.to_numeric(series, errors="coerce").astype(float)
    return pd.DataFrame(
        {f"ma{w}": s.rolling(int(w), min_periods=int(w)).mean() for w in windows}
    )


def convergence_series(
    series: pd.Series, windows: tuple[int, ...] = MA_WINDOWS, ddof: int = 1
) -> pd.Series:
    """文档原式：`−log(1 + std(MA_1..MA_120))`。

    参数：
        series: 同一口径的原始序列（收盘价 / 成交量 / 成交额 / 换手率）。
        windows: 均线窗口，默认 1/5/10/20/60/120。
        ddof: 标准差自由度，默认 1（与 pandas 默认一致）。

    返回：
        float Series（与输入同索引）。前 119 根为 NaN（MA_120 未成形）。
        注意必须 `skipna=False`：6 条均线里只要有一条未成形，收敛度就无定义，
        不能让 pandas 用「已有的 5 条」偷偷算出一个偏小的 std。
    """
    ma = ma_stack(series, windows)
    std = ma.std(axis=1, ddof=ddof, skipna=False)
    return -np.log1p(std)


def relative_convergence_series(
    series: pd.Series, windows: tuple[int, ...] = MA_WINDOWS, ddof: int = 1
) -> pd.Series:
    """无量纲诊断口径：`−log(1 + std(MA) / |mean(MA)|)`。

    用于证明/证伪「原式量纲问题」（计划 §2.6）：若诊断口径与主口径的 A/B 结论
    方向一致，说明结论来自收敛结构而非价格水平。
    """
    ma = ma_stack(series, windows)
    std = ma.std(axis=1, ddof=ddof, skipna=False)
    mean = ma.mean(axis=1).abs()
    ratio = std / mean.where(mean > 0)
    return -np.log1p(ratio)


def convergence_columns(bars: pd.DataFrame) -> pd.DataFrame:
    """日线全序列 → 四个原始收敛列 + 一个诊断列（与输入同索引）。

    参数：
        bars: 含 close/volume/amount/turnover 的日线 DataFrame（按日期升序）。

    返回：
        DataFrame，列 `conv_pcf / conv_vcf / conv_acf / conv_trcf / conv_pcf_cv`。
        缺列（如某些数据源没有 turnover）时对应列整列为 NaN，不抛错。
    """
    out = pd.DataFrame(index=bars.index)
    for column, source in _SOURCE_MAP.items():
        if source in bars.columns:
            out[column] = convergence_series(bars[source]).to_numpy()
        else:
            out[column] = np.nan
    if "close" in bars.columns:
        out["conv_pcf_cv"] = relative_convergence_series(bars["close"]).to_numpy()
    else:
        out["conv_pcf_cv"] = np.nan
    return out


def add_conv_columns(df: pd.DataFrame, bars: pd.DataFrame) -> pd.DataFrame:
    """把四个原始收敛列追加到既有日线/因子 DataFrame（供引擎预计算复用）。

    参数：
        df:   目标 DataFrame（行序与 bars 一致，通常已含因子列）。
        bars: 原始日线（与 df 同源，用于取四类源序列）。

    返回：
        df 的副本，追加 conv_pcf / conv_vcf / conv_acf / conv_trcf 四列。
    """
    out = df.copy()
    conv = convergence_columns(bars)
    for column in CONV_RAW_COLUMNS:
        out[column] = conv[column].to_numpy()
    return out


def cross_section_zscore(df: pd.DataFrame, column: str) -> pd.Series:
    """单列截面 z-score（ddof=0；标准差为 0 或全空时返回 0）。"""
    x = pd.to_numeric(df[column], errors="coerce").astype(float)
    std = x.std(ddof=0)
    if not np.isfinite(std) or std == 0:
        return pd.Series(0.0, index=df.index)
    return ((x - x.mean()) / std).fillna(0.0)


def compose_pvcf(df: pd.DataFrame) -> pd.Series:
    """在**单日截面**上计算 PVCF = z(PCF) + z(VCF)（研报合成口径）。

    参数：
        df: 每只股票一行的因子 DataFrame，需含 conv_pcf / conv_vcf。

    返回：
        float Series（与 df 同索引）。缺任一变体时整列 NaN；单只股票或列无方差时
        z-score 为 0，因此 PVCF 为 0（中性），不放大噪声。
    """
    if "conv_pcf" not in df.columns or "conv_vcf" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    if df.empty:
        return pd.Series(dtype=float, index=df.index)
    z = cross_section_zscore(df, "conv_pcf") + cross_section_zscore(df, "conv_vcf")
    return z.fillna(0.0)


def conv_series_for(symbol: str, bars: pd.DataFrame) -> dict[str, pd.Series]:
    """便捷函数：返回一只股票四个原始收敛列（Series，与 bars 行序对齐）。

    供单元测试与临时分析使用；生产路径直接用 `convergence_columns` / `add_conv_columns`。
    """
    conv = convergence_columns(bars)
    return {column: conv[column] for column in CONV_RAW_COLUMNS}


if __name__ == "__main__":
    # 简单自测：用已缓存数据打印一只股票的收敛序列
    from stock_plan.data.storage import Storage

    storage = Storage()
    bars = storage.load_bars("600000")
    if bars.empty:
        print("无缓存数据，请先运行 fetch_all")
    else:
        cols = convergence_columns(bars)
        print(pd.concat([bars[["date"]], cols], axis=1).tail(5).to_string(index=False))
