"""市场阶段因子 —— 判断当前股市处于什么阶段（暂时需求 6 的前半）。

回答三个问题：
1. 现在的大盘是**上升 / 下降 / 震荡**？
2. 波动率处于历史什么水平？（高 / 中 / 低）
3. 当前位置在近一年通道的哪个区间？（0~1）

## 为什么先要造一个"正确的大盘代理"

原引擎（`backtest/engine.py`）用 `closes.mean(axis=1)` 当大盘：**对原始股价直接取平均**。
这有两个方法学问题：

- 被高价股主导：贵州茅台 1300 元与 3 元股在均值里权重相同，实际等价于"高价股指数"；
- 成分变动会**跳空**：某只股票当天没有 K 线就从均值里消失，指数水平会突变，
  于是"大盘跌破 MA20"这种判断会被成分变动而非行情触发。

这里改为**等权收益指数**（chained equal-weight return index）：先算每只股票的日收益率，
等权平均后累乘。它与成分股的价格水平无关，也不受成分增减影响。

## 阶段判定规则（刻意写得可解释、可测试）

| 条件 | 判定 |
|---|---|
| `close > MA20 > MA60` 且 MA60 近 20 日上行 | 上升趋势 |
| `close < MA20 < MA60` 且 MA60 近 20 日下行 | 下降趋势 |
| 其余 | 震荡 |

波动率用 20 日年化标准差，并在**全序列内取分位**：>70% 分位为高、<30% 为低。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# 判定用的均线周期
MA_FAST = 20
MA_MID = 60
MA_SLOW = 120
# MA60 的斜率观察窗口（交易日）
SLOPE_WINDOW = 20
# 波动率分位阈值
VOL_HIGH_PCT = 0.70
VOL_LOW_PCT = 0.30
# 等权指数在每个交易日至少要有多少只成分股参与，否则该日不参与
MIN_CONTRIBUTORS = 20

PHASE_UP = "上升趋势"
PHASE_DOWN = "下降趋势"
PHASE_RANGE = "震荡"
PHASE_UNKNOWN = "数据不足"

TREND_UP = "up"
TREND_DOWN = "down"
TREND_RANGE = "range"


@dataclass
class RegimeSnapshot:
    """某一日的市场阶段快照。"""

    phase: str = PHASE_UNKNOWN
    trend: str = TREND_RANGE
    volatility: str = "中"
    close: float = float("nan")
    ma20: float = float("nan")
    ma60: float = float("nan")
    ma120: float = float("nan")
    slope_ma60: float = float("nan")
    vol20_annual: float = float("nan")
    vol_percentile: float = float("nan")
    channel_pos: float = float("nan")
    explain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "trend": self.trend,
            "volatility": self.volatility,
            "close": self.close,
            "ma20": self.ma20,
            "ma60": self.ma60,
            "ma120": self.ma120,
            "slope_ma60": self.slope_ma60,
            "vol20_annual": self.vol20_annual,
            "vol_percentile": self.vol_percentile,
            "channel_pos": self.channel_pos,
            "explain": self.explain,
        }


def equal_weight_index(
    bars_map: dict[str, pd.DataFrame],
    start=None,
    end=None,
    min_contributors: int = MIN_CONTRIBUTORS,
) -> pd.Series:
    """由成分股构造等权收益指数（首日归一为 1.0）。

    参数：
        bars_map: {code: 日线 DataFrame}（需含 date / close）。
        start/end: 可选日期窗口。
        min_contributors: 当日参与计算的最少股票数；不足则该日不计入收益（记为 0），
            避免样本极少的日子把指数拉出假波动。

    返回：
        以日期为索引的指数序列；无可用数据时返回空 Series。
    """
    rets: dict[str, pd.Series] = {}
    for code, df in (bars_map or {}).items():
        if df is None or len(df) < 2 or "close" not in df.columns:
            continue
        s = df.set_index("date")["close"].astype(float).sort_index()
        if start is not None:
            s = s[s.index >= pd.Timestamp(start)]
        if end is not None:
            s = s[s.index <= pd.Timestamp(end)]
        if len(s) < 2:
            continue
        rets[code] = s.pct_change()

    if not rets:
        return pd.Series(dtype=float)

    r = pd.DataFrame(rets).sort_index()
    counts = r.notna().sum(axis=1)
    avg = r.mean(axis=1, skipna=True).fillna(0.0)
    avg[counts < min_contributors] = 0.0  # 成分太少的日子不参与
    return (1.0 + avg).cumprod()


def _volatility_series(index: pd.Series, window: int = 20) -> pd.Series:
    """20 日滚动年化波动率（按 252 个交易日折算）。"""
    return index.pct_change().rolling(window).std() * np.sqrt(252)


def regime_table(index: pd.Series) -> pd.DataFrame:
    """整段序列的逐日阶段表（供画图与"分阶段策略"使用）。

    返回列：close, ma20, ma60, ma120, slope_ma60, vol20_annual, vol_percentile,
            channel_pos, phase, trend, volatility
    """
    if index is None or len(index) < MA_MID + SLOPE_WINDOW:
        return pd.DataFrame()

    close = index.astype(float)
    ma20 = close.rolling(MA_FAST).mean()
    ma60 = close.rolling(MA_MID).mean()
    ma120 = close.rolling(MA_SLOW).mean()
    slope = ma60 - ma60.shift(SLOPE_WINDOW)
    vol = _volatility_series(close)
    vol_pct = vol.rank(pct=True)
    roll_lo = close.rolling(MA_SLOW, min_periods=1).min()
    roll_hi = close.rolling(MA_SLOW, min_periods=1).max()
    span = (roll_hi - roll_lo).replace(0, np.nan)
    channel = ((close - roll_lo) / span).clip(0, 1)

    up = (close > ma20) & (ma20 > ma60) & (slope > 0)
    down = (close < ma20) & (ma20 < ma60) & (slope < 0)

    trend = pd.Series(TREND_RANGE, index=close.index, dtype=object)
    trend[up] = TREND_UP
    trend[down & ~up] = TREND_DOWN

    phase = pd.Series(PHASE_RANGE, index=close.index, dtype=object)
    phase[trend == TREND_UP] = PHASE_UP
    phase[trend == TREND_DOWN] = PHASE_DOWN
    # 均线尚未成形时标注"数据不足"，不要给出误导性的阶段
    phase[ma60.isna() | slope.isna()] = PHASE_UNKNOWN

    vol_label = pd.Series("中", index=close.index, dtype=object)
    vol_label[vol_pct > VOL_HIGH_PCT] = "高"
    vol_label[vol_pct < VOL_LOW_PCT] = "低"
    vol_label[vol.isna()] = "未知"

    return pd.DataFrame(
        {
            "close": close,
            "ma20": ma20,
            "ma60": ma60,
            "ma120": ma120,
            "slope_ma60": slope,
            "vol20_annual": vol,
            "vol_percentile": vol_pct,
            "channel_pos": channel,
            "phase": phase,
            "trend": trend,
            "volatility": vol_label,
        }
    )


def detect_regime(index: pd.Series, as_of=None) -> RegimeSnapshot:
    """判断指定日期（默认最后一天）的市场阶段。

    参数：
        index: 大盘（等权指数或指数收盘价）序列。
        as_of: 截止日期；None 表示用最后一天。

    返回：
        RegimeSnapshot（阶段、趋势、波动率、通道位置 + 可直接展示的解释）。
    """
    table = regime_table(index)
    if table.empty:
        snap = RegimeSnapshot()
        snap.explain.append("历史数据不足（至少需要约 80 个交易日）")
        return snap

    row = table.iloc[-1] if as_of is None else table.loc[:pd.Timestamp(as_of)].iloc[-1]
    snap = RegimeSnapshot(
        phase=str(row["phase"]),
        trend=str(row["trend"]),
        volatility=str(row["volatility"]),
        close=float(row["close"]),
        ma20=float(row["ma20"]) if pd.notna(row["ma20"]) else float("nan"),
        ma60=float(row["ma60"]) if pd.notna(row["ma60"]) else float("nan"),
        ma120=float(row["ma120"]) if pd.notna(row["ma120"]) else float("nan"),
        slope_ma60=float(row["slope_ma60"]) if pd.notna(row["slope_ma60"]) else float("nan"),
        vol20_annual=float(row["vol20_annual"]) if pd.notna(row["vol20_annual"]) else float("nan"),
        vol_percentile=(
            float(row["vol_percentile"]) if pd.notna(row["vol_percentile"]) else float("nan")
        ),
        channel_pos=float(row["channel_pos"]) if pd.notna(row["channel_pos"]) else float("nan"),
    )

    if snap.phase == PHASE_UP:
        snap.explain.append("收盘价在 MA20 上方、MA20 在 MA60 上方、且 MA60 近 20 日在上行 → 上升趋势")
    elif snap.phase == PHASE_DOWN:
        snap.explain.append("收盘价在 MA20 下方、MA20 在 MA60 下方、且 MA60 近 20 日在下行 → 下降趋势")
    elif snap.phase == PHASE_UNKNOWN:
        snap.explain.append("均线尚未成形（数据不足 60 个交易日）")
    else:
        snap.explain.append("均线未形成一致的多头或空头排列 → 震荡")
    if pd.notna(snap.vol20_annual):
        snap.explain.append(
            f"20 日年化波动率 {snap.vol20_annual*100:.1f}%，处于历史 {snap.vol_percentile*100:.0f}% 分位 → 波动{snap.volatility}"
        )
    if pd.notna(snap.channel_pos):
        snap.explain.append(f"在近 {MA_SLOW} 日通道中的位置 {snap.channel_pos*100:.0f}%")
    return snap


# 阶段 → 策略偏好与仓位建议（暂时需求 6 的后半：分阶段策略组合）
_PHASE_PLAYBOOK = {
    PHASE_UP: {
        "prefer": ("动量策略", "趋势跟随策略"),
        "stance": "进攻",
        "base_position": 0.9,
        "note": "趋势向上时顺势做多，止损可以放宽一点，别被正常回撤洗出去。",
    },
    PHASE_RANGE: {
        "prefer": ("均值回归策略", "价值策略"),
        "stance": "均衡",
        "base_position": 0.5,
        "note": "没有明确方向时低吸高抛，仓位别重，避免在区间上沿追高。",
    },
    PHASE_DOWN: {
        "prefer": ("价值策略",),
        "stance": "防守",
        "base_position": 0.2,
        "note": "下跌趋势里现金也是仓位。要买就买低估值、抗跌的，且严格止损。",
    },
    PHASE_UNKNOWN: {
        "prefer": ("价值策略",),
        "stance": "观望",
        "base_position": 0.3,
        "note": "数据不足，先小仓位试探，不要依赖趋势类信号。",
    },
}


def suggest_playbook(snapshot: RegimeSnapshot) -> dict:
    """按阶段给出策略偏好与建议仓位（波动率高时进一步压低仓位）。"""
    plan = dict(_PHASE_PLAYBOOK.get(snapshot.phase, _PHASE_PLAYBOOK[PHASE_UNKNOWN]))
    pos = float(plan["base_position"])
    if snapshot.volatility == "高":
        pos *= 0.7
        plan["note"] += " 当前波动率偏高，仓位再打七折。"
    elif snapshot.volatility == "低":
        pos = min(pos * 1.1, 1.0)
    plan["position"] = round(pos, 2)
    plan["phase"] = snapshot.phase
    plan["volatility"] = snapshot.volatility
    return plan


if __name__ == "__main__":
    # 简单自测：三段人造行情分别应判为上升 / 下降 / 震荡
    n = 240
    dates = pd.bdate_range("2025-01-01", periods=n)
    up = pd.Series(np.linspace(1.0, 1.5, n), index=dates)
    down = pd.Series(np.linspace(1.5, 1.0, n), index=dates)
    # 震荡用**正弦**而不是随机游走：随机游走在末段本身就是有方向的，
    # 判成趋势并不算错（第一版就是这么误判自己用例的）
    t = np.arange(n)
    sine = pd.Series(1.0 + 0.06 * np.sin(2 * np.pi * t / 40), index=dates)
    for name, series in [("单调上涨", up), ("单调下跌", down), ("正弦震荡", sine)]:
        s = detect_regime(series)
        print(f"{name}: {s.phase} / 波动{s.volatility} / 建议仓位 {suggest_playbook(s)['position']}")
        print("   ", s.explain[0])
