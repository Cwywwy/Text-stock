"""RRG 相对旋转图（行业层，任务 C1）。

来源
----
策略选型调研 §3.2 候选 1（`docs/策略选型调研-2026-10-29.md`）。研报口径是用 RRG 做
**行业 ETF 轮动**：在「转强」象限里挑行业，持有到象限变化。本模块只做 **C1 行业层证伪**：
先把行业指数和 RRG 算对、按预注册判定线看有没有超额；没有超额就不做到个股层（C2）。

口径（用户 2026-10-29 拍板，不可自行改动）
----------------------------------------
1. 行业指数：申万一级行业，按**等权**聚合成分股日收益（缺行情的成分当日剔除并计入
   `n_components`；当日有效成分 < `min_components` 时该行业该日指数为 NaN，不静默补 0）；
2. `RS = 行业指数 / 基准 × 100`；
3. `RS-Ratio = (100 × RS / RS.shift(220)).rolling(20).mean()`；
4. `RS-Momentum = (100 × RS-Ratio / RS-Ratio.shift(60)).rolling(20).mean()`；
5. 中枢 `(100, 100)`；四象限：
   - `>=100 & >=100` → 领先；`>=100 & <100` → 转弱；
   - `<100 & <100` → 落后；`<100 & >=100` → 转强（`>=100` 统一归「上方」，注释见代码）；
6. 选行业：只在「转强」里按到 `(100,100)` 的欧氏距离降序取前 `top_n`（A 方案）；
7. 预热不足**显式抛 `ValueError`**，绝不静默返回空表：
   - 少于 239 行 → `RS-Ratio 需 239 行，实际 N 行`；
   - 少于 318 行 → `完整 RRG 需 318 行，实际 N 行`。

已知限制（必须写进报告，不得隐瞒）
--------------------------------
- 行业分类快照只有 2026-10 一个月，用它回溯 2022–2026 属**前视偏差**（个股可能已换行业）；
  `incl_date` 是纳入时间、没有剔除时间，无法还原历史成分；
- 北交所（`920xxx`）无申万行业，天然不在池内，覆盖面要在报告披露；
- RRG 预热 318 行 ≈ 1.3 年，实际有效样本只剩约 3.5 年，统计显著性弱，不做参数寻优。

修订记录：
2026-10-29  (phase 9) 新建：申万一级等权指数聚合、RRG 计算与「转强」选行业（C1 证伪用）
"""
from __future__ import annotations

import warnings
from collections.abc import Mapping

import numpy as np
import pandas as pd

# 北交所代码前缀：申万行业表里没有这些票，聚合时排除
EXCLUDED_CODE_PREFIXES: tuple[str, ...] = ("920",)

# 四象限中文名（UI / 报告直接展示，不要改成英文）
QUADRANT_LEADING = "领先"
QUADRANT_WEAKENING = "转弱"
QUADRANT_LAGGING = "落后"
QUADRANT_IMPROVING = "转强"

QUADRANT_NAMES: tuple[str, ...] = (
    QUADRANT_LEADING,
    QUADRANT_WEAKENING,
    QUADRANT_LAGGING,
    QUADRANT_IMPROVING,
)

# 预热行数口径（用户拍板：RS-Ratio 239 行、完整 RRG 318 行）
RR_RATIO_MIN_ROWS = 239
RRG_MIN_ROWS = 318


def _as_industry_series(industry_map: Mapping[str, str] | pd.Series | pd.DataFrame) -> pd.Series:
    """把行业映射统一成 `Series[index=code, value=industry]`（去掉空值）。"""
    if isinstance(industry_map, pd.DataFrame):
        if "code" not in industry_map.columns or "industry" not in industry_map.columns:
            raise ValueError("industry_map 为 DataFrame 时必须含 code / industry 两列")
        s = industry_map.set_index("code")["industry"]
    elif isinstance(industry_map, pd.Series):
        s = industry_map
    elif isinstance(industry_map, Mapping):
        s = pd.Series(dict(industry_map))
    else:  # pragma: no cover - 类型错误直接抛，便于调用方早发现
        raise TypeError(f"industry_map 类型不支持：{type(industry_map)!r}")
    s = s.dropna().astype(str)
    s = s[s.str.strip() != ""]
    s.index = s.index.astype(str)
    return s


def build_industry_index(
    bars_map: dict[str, pd.DataFrame],
    industry_map: Mapping[str, str] | pd.Series | pd.DataFrame,
    level: str = "sw_l1",
    min_components: int = 3,
) -> pd.DataFrame:
    """把个股日线按行业**等权**聚合成行业指数（长表）。

    聚合口径：先算每只股票的日收益率 `close.pct_change()`，再对同一行业当日**有行情的
    成分**取算术平均，链式累乘成指数（首日 = 1.0）。缺行情的成分当日自动剔除；
    有效成分少于 `min_components` 的日子该行业指数为 NaN（不填 0、不退化常数）。

    参数：
        bars_map: {code: 日线 DataFrame}（需含 date / close）。
        industry_map: 代码 → 行业名（dict / Series / 含 code、industry 列的 DataFrame）。
        level: 仅用于报错与文档说明（调用方传的是申万一级的字段名）。
        min_components: 每日最少有效成分数。

    返回：
        长表 DataFrame，列 `date, industry, index, n_components`，按 date / industry 排序。
    """
    mapping = _as_industry_series(industry_map)
    closes: list[pd.Series] = []
    for code, bars in (bars_map or {}).items():
        code = str(code)
        if code.startswith(EXCLUDED_CODE_PREFIXES):
            continue
        if code not in mapping.index or bars is None or len(bars) < 2:
            continue
        if "date" not in bars.columns or "close" not in bars.columns:
            continue
        s = bars.set_index(pd.to_datetime(bars["date"]))["close"].astype(float).sort_index()
        s = s[~s.index.duplicated(keep="last")]
        if len(s) < 2:
            continue
        closes.append(s.rename(code))

    if not closes:
        raise ValueError("没有可用于行业聚合的股票（检查 industry_map 与 bars_map 是否对得上）")

    wide = pd.concat(closes, axis=1).sort_index()
    returns = wide.pct_change()
    codes = pd.Index(wide.columns.astype(str))
    industries = mapping.reindex(codes)

    index_series: dict[str, pd.Series] = {}
    counts: dict[str, pd.Series] = {}
    for industry, group in industries.groupby(industries, sort=True):
        members = [c for c in group.index if c in returns.columns]
        if not members:
            continue
        sub = returns[members]
        n = sub.notna().sum(axis=1)
        mean_ret = sub.mean(axis=1, skipna=True)
        valid = n >= int(min_components)
        # 首日没有收益率信息，不能用「有效成分数 = 0」判死：改成看当天**有几只票有行情**，
        # 够门槛才把指数定在 1.0（与 regime.equal_weight_index 的首日口径一致）；
        # n_components 首日也按当天有行情的成分数记录，避免报告里出现「0 个成分却有权重」。
        if len(valid):
            present = wide[members].notna().sum(axis=1)
            valid.iloc[0] = bool(present.iloc[0] >= int(min_components))
            n = n.copy()
            n.iloc[0] = int(present.iloc[0])
        idx = (1.0 + mean_ret.fillna(0.0)).cumprod()
        idx[~valid] = np.nan
        index_series[str(industry)] = idx
        counts[str(industry)] = n

    if not index_series:
        raise ValueError(f"行业聚合后为空（level={level}）")

    idx_wide = pd.DataFrame(index_series).sort_index()
    n_wide = pd.DataFrame(counts).reindex(idx_wide.index)
    long = (
        idx_wide.rename_axis("date")
        .reset_index()
        .melt(id_vars="date", var_name="industry", value_name="index")
        .dropna(subset=["index"])
        .sort_values(["date", "industry"])
        .reset_index(drop=True)
    )
    n_long = (
        n_wide.rename_axis("date")
        .reset_index()
        .melt(id_vars="date", var_name="industry", value_name="n_components")
    )
    long = long.merge(n_long, on=["date", "industry"], how="left")
    return long


def industry_index_wide(panel: pd.DataFrame) -> pd.DataFrame:
    """把长表（date/industry/index）转成宽表：index=日期，columns=行业。"""
    if panel is None or panel.empty:
        return pd.DataFrame()
    return panel.pivot_table(index="date", columns="industry", values="index", aggfunc="last").sort_index()


def build_equal_weight_benchmark(panel_wide: pd.DataFrame) -> pd.Series:
    """由 31 个行业指数构造「行业等权基准」（每日行业收益等权平均，首日归一 1.0）。

    注意这是**行业等权**，不是全市场个股等权：小行业与大行业权重相同。
    """
    if panel_wide is None or panel_wide.empty:
        return pd.Series(dtype=float)
    rets = panel_wide.pct_change()
    avg = rets.mean(axis=1, skipna=True)
    if len(avg):
        # 首日没有收益率，基准固定在 1.0（否则曲线会少一天，与轮动资金曲线对不齐）
        avg.iloc[0] = 0.0
    if avg.empty:
        return pd.Series(dtype=float)
    return (1.0 + avg.fillna(0.0)).cumprod()


def compute_rrg(
    panel_wide: pd.DataFrame,
    benchmark: pd.Series,
    ratio_lb: int = 220,
    mom_lb: int = 60,
    smooth: int = 20,
) -> pd.DataFrame:
    """计算 RRG 指标（长表，逐日逐行业）。

    公式严格按用户拍板口径：

        RS         = index / benchmark × 100
        RS-Ratio   = (100 × RS / RS.shift(220)).rolling(20).mean()
        RS-Momentum= (100 × RS-Ratio / RS-Ratio.shift(60)).rolling(20).mean()

    预热不足抛 `ValueError`（不静默返回空表）：
        - `< 239` 行：`RS-Ratio 需 239 行，实际 N 行`
        - `< 318` 行：`完整 RRG 需 318 行，实际 N 行`

    返回：
        长表 DataFrame，列 `date, industry, rs, rs_ratio, rs_momentum, quadrant`；
        `quadrant` 在指标未成形时为 NaN。
    """
    if panel_wide is None or panel_wide.empty:
        raise ValueError("行业指数为空，无法计算 RRG")
    panel = panel_wide.sort_index().copy()
    rows = len(panel)
    # 预热门槛由回看窗口推导：默认 220/60/20 → RS-Ratio 需 239 行、完整 RRG 需 318 行
    # （与用户拍板口径一致）；单测若缩小窗口，门槛随之缩小，避免测试被迫造 400 行数据。
    need_ratio = max(int(ratio_lb) + int(smooth) - 1, 1)
    need_full = max(int(ratio_lb) + int(mom_lb) + 2 * int(smooth) - 2, need_ratio)
    if rows < need_ratio:
        raise ValueError(f"RS-Ratio 需 {need_ratio} 行，实际 {rows} 行")

    bench = benchmark.reindex(panel.index).astype(float)
    if bench.notna().sum() < 2:
        raise ValueError("基准序列与行业指数没有重叠日期")

    rs = panel.div(bench, axis=0) * 100.0
    rs_ratio = (100.0 * rs / rs.shift(int(ratio_lb))).rolling(int(smooth)).mean()

    if rows < need_full:
        raise ValueError(f"完整 RRG 需 {need_full} 行，实际 {rows} 行")

    rs_momentum = (
        100.0 * rs_ratio / rs_ratio.shift(int(mom_lb))
    ).rolling(int(smooth)).mean()

    quadrant = pd.DataFrame(np.nan, index=panel.index, columns=panel.columns, dtype=object)
    above_x = rs_ratio >= 100.0  # 边界恰好 100 统一归「上方」（用户口径）
    above_y = rs_momentum >= 100.0
    ready = rs_ratio.notna() & rs_momentum.notna()
    quadrant[ready & above_x & above_y] = QUADRANT_LEADING
    quadrant[ready & above_x & ~above_y] = QUADRANT_WEAKENING
    quadrant[ready & ~above_x & ~above_y] = QUADRANT_LAGGING
    quadrant[ready & ~above_x & above_y] = QUADRANT_IMPROVING

    frames = []
    for name, frame in (
        ("rs", rs),
        ("rs_ratio", rs_ratio),
        ("rs_momentum", rs_momentum),
        ("quadrant", quadrant),
    ):
        # pandas 3.0 的 stack() 会丢掉 NaN 行：四个 frame 各自堆叠后按 (date, industry)
        # 对齐拼接，预热期缺的值自然落成 NaN，不需要 dropna=False。
        frames.append(frame.stack().rename(name))
    out = pd.concat(frames, axis=1)
    out.index = out.index.set_names(["date", "industry"])
    out = out.reset_index()
    out = out[out["rs"].notna()]
    return out.sort_values(["date", "industry"]).reset_index(drop=True)


def rrg_ready(rrg: pd.DataFrame) -> pd.DataFrame:
    """筛出 RS-Ratio 与 RS-Momentum 都已成形（非 NaN）的行。"""
    if rrg is None or rrg.empty:
        return pd.DataFrame(columns=["date", "industry", "rs", "rs_ratio", "rs_momentum", "quadrant"])
    return rrg[rrg["rs_ratio"].notna() & rrg["rs_momentum"].notna()].copy()


def select_industries(rrg_day: pd.DataFrame, top_n: int = 6) -> list[str]:
    """在单日 RRG 里挑「转强」象限的行业（A 方案：距中枢欧氏距离最远优先）。

    不足 `top_n` 个时按实际数量返回，并 `warnings.warn` 提示（不静默补位）。
    """
    if rrg_day is None or rrg_day.empty:
        return []
    frame = rrg_day[rrg_day["quadrant"] == QUADRANT_IMPROVING].copy()
    if frame.empty:
        return []
    frame = frame.dropna(subset=["rs_ratio", "rs_momentum"])
    if frame.empty:
        return []
    dist = np.sqrt((frame["rs_ratio"] - 100.0) ** 2 + (frame["rs_momentum"] - 100.0) ** 2)
    frame = frame.assign(distance=dist).sort_values("distance", ascending=False)
    picked = [str(x) for x in frame["industry"].head(int(top_n)).tolist()]
    if len(picked) < int(top_n):
        warnings.warn(
            f"「转强」象限只有 {len(picked)} 个行业，少于 top_n={top_n}，按实际数量持有",
            stacklevel=2,
        )
    return picked
