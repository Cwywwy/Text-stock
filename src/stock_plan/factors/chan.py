"""缠论（缠中说禅）技术分析 —— 机械可计算部分（暂时需求 6 后半）。

## 已实现（规则明确、可逐步人工验证）

1. **K 线包含处理**：相邻 K 线存在包含关系时按方向合并（向上取高高，向下取低低）
2. **分型**：合并后连续三根 K 线中，中间那根的高点与低点**都是**最高 → 顶分型；
   都是最低 → 底分型
3. **笔**：连接相邻的顶分型与底分型，要求两者在合并序列中的间距 ≥ `min_gap`（默认 4，
   即一笔至少含 5 根合并 K 线）
4. **笔中枢**：连续三笔的价格区间重叠部分 `[ZD, ZG]`（`ZG = min(三笔的高点)`，
   `ZD = max(三笔的低点)`，要求 `ZG > ZD`）
5. **三买**：价格向上离开中枢后回抽不跌回中枢（回抽低点 > `ZG`）→ 机械可判定

## 明确**未实现**（这些容易实现得似是而非，故不做）

- **线段**：笔的再上一层结构，需要特征序列与缺口处理
- **走势类型**：趋势 / 盘整的严格定义（依赖线段）
- **背驰**：需要比较同级别走势的力度。实践中常用 MACD 面积近似，但那属于启发式，
  没有统一定义；本模块不提供
- **一买 / 二买**：依赖背驰判定，因此同样不提供

所以本模块的定位是**结构识别的辅助特征**，不是一套完整的缠论交易系统。
把"识别出中枢"等同于"可以交易"是常见误用 —— 中枢只描述价格在某个区间内反复，
方向的确认仍在离开中枢的那一笔上。另外「笔中枢」是常见简化（严格定义用的是线段中枢），
本实现采用笔中枢，因为它可逐步验证。

用法：
    from stock_plan.factors.chan import chan_analysis
    result = chan_analysis(bars)      # bars 需含 date/high/low
    result["pivots"]                  # 中枢列表
    result["third_buys"]              # 三买列表
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# 一笔至少需要的合并 K 线根数（含两端分型）。4 表示两个分型中心至少隔 4 根。
DEFAULT_MIN_GAP = 4

TOP = "top"
BOTTOM = "bottom"
UP = "up"
DOWN = "down"


@dataclass
class MergedBar:
    """包含处理后的合并 K 线。"""

    high: float
    low: float
    start: int          # 对应的原始 K 线起始位置
    end: int            # 对应的原始 K 线结束位置
    direction: int      # 合并时采用的方向：1 向上 / -1 向下

    @property
    def bars_merged(self) -> int:
        return self.end - self.start + 1


@dataclass
class Fractal:
    """分型：合并序列中的顶或底。"""

    idx: int            # 在合并序列中的位置
    kind: str           # top / bottom
    high: float
    low: float
    date: object = None


@dataclass
class Stroke:
    """笔：连接一个顶分型与一个底分型。"""

    start_idx: int
    end_idx: int
    direction: str      # up（底→顶）/ down（顶→底）
    high: float
    low: float
    start_date: object = None
    end_date: object = None

    def describe(self) -> str:
        arrow = "↑" if self.direction == UP else "↓"
        return f"{arrow} {self.start_date} → {self.end_date}  {self.low:.2f}~{self.high:.2f}"


@dataclass
class Pivot:
    """中枢：连续三笔的重叠区间 [zd, zg]。"""

    start_stroke: int
    end_stroke: int
    zg: float
    zd: float
    start_date: object = None
    end_date: object = None

    @property
    def height(self) -> float:
        return self.zg - self.zd

    def describe(self) -> str:
        return (
            f"中枢 {self.start_date} → {self.end_date}  "
            f"区间 [{self.zd:.2f}, {self.zg:.2f}]（高度 {self.height:.2f}）"
        )


@dataclass
class ThirdBuy:
    """三买：向上离开中枢后回抽不跌回中枢。"""

    stroke_index: int
    price: float
    date: object
    above: float        # 相对中枢上沿高出多少

    def describe(self) -> str:
        return f"三买 @ {self.date} 价格 {self.price:.2f}（高出中枢上沿 {self.above:.2f}）"


@dataclass
class ChanResult:
    """缠论结构识别结果。"""

    merged: list[MergedBar] = field(default_factory=list)
    fractals: list[Fractal] = field(default_factory=list)
    strokes: list[Stroke] = field(default_factory=list)
    pivots: list[Pivot] = field(default_factory=list)
    third_buys: list[ThirdBuy] = field(default_factory=list)
    explain: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "merged_count": len(self.merged),
            "fractal_count": len(self.fractals),
            "stroke_count": len(self.strokes),
            "pivot_count": len(self.pivots),
            "third_buy_count": len(self.third_buys),
            "latest_strokes": [s.describe() for s in self.strokes[-3:]],
            "latest_pivot": self.pivots[-1].describe() if self.pivots else None,
            "explain": list(self.explain),
        }


def merge_bars(df: pd.DataFrame) -> list[MergedBar]:
    """K 线包含处理。

    相邻两根存在包含关系（一根的高低点完全覆盖另一根）时合并：
    按当前方向，向上取「高高」，向下取「低低」。

    方向由最近两根**无包含关系**的合并 K 线确定；起始方向默认向上，
    并在第一次出现明确的高低点关系时被纠正。
    """
    if df is None or df.empty:
        return []

    highs = df["high"].astype(float).tolist()
    lows = df["low"].astype(float).tolist()
    out: list[MergedBar] = []
    direction = 1  # 起始假设向上，遇到明确方向会被覆盖

    for i, (h, l) in enumerate(zip(highs, lows)):
        if not out:
            out.append(MergedBar(high=h, low=l, start=i, end=i, direction=direction))
            continue

        last = out[-1]
        contains = (h <= last.high and l >= last.low) or (h >= last.high and l <= last.low)
        if not contains:
            if h > last.high and l > last.low:
                direction = 1
            elif h < last.high and l < last.low:
                direction = -1
            out.append(MergedBar(high=h, low=l, start=i, end=i, direction=direction))
            continue

        # 有包含关系：按方向合并到上一根
        if direction == 1:
            last.high = max(last.high, h)
            last.low = max(last.low, l)
        else:
            last.high = min(last.high, h)
            last.low = min(last.low, l)
        last.end = i

    return out


def find_fractals(merged: list[MergedBar], dates: list | None = None) -> list[Fractal]:
    """在合并序列上找分型。

    顶分型：中间那根的**高点与低点都是最高**；底分型：**都是最低**。
    （只看高点会漏掉"高点高但低点不高"的伪分型，所以两个条件都要满足。）
    """
    out: list[Fractal] = []
    for i in range(1, len(merged) - 1):
        a, b, c = merged[i - 1], merged[i], merged[i + 1]
        if b.high > a.high and b.high > c.high and b.low > a.low and b.low > c.low:
            out.append(Fractal(i, TOP, b.high, b.low, dates[i] if dates else None))
        elif b.high < a.high and b.high < c.high and b.low < a.low and b.low < c.low:
            out.append(Fractal(i, BOTTOM, b.high, b.low, dates[i] if dates else None))
    return out


def _more_extreme(f: Fractal, ref: Fractal) -> bool:
    """同类型分型里 f 是否比 ref 更极端（顶取更高、底取更低）。"""
    if f.kind == TOP:
        return f.high >= ref.high
    return f.low <= ref.low


def _make_stroke(start: Fractal, end: Fractal) -> Stroke:
    return Stroke(
        start_idx=start.idx,
        end_idx=end.idx,
        direction=UP if start.kind == BOTTOM else DOWN,
        high=max(start.high, end.high),
        low=min(start.low, end.low),
        start_date=start.date,
        end_date=end.date,
    )


def find_strokes(
    fractals: list[Fractal],
    min_gap: int = DEFAULT_MIN_GAP,
) -> list[Stroke]:
    """把分型连成笔。

    规则：
    - 笔的两端必须是**异类型**分型（顶 ↔ 底），因此笔天然交替
    - 两端在合并序列中的间距必须 ≥ `min_gap`
    - 出现同类型分型时取更极端的那个；间距不足的异类型分型被忽略

    实现上先**归约**出一个交替且单调的转折点序列，再由它生成笔。这样做有两个必要性：

    1. **笔的延伸**：出现更高的顶时，应把前一笔的端点延伸过去，而不是早早固定在
       较低的顶上；
    2. **笔必须首尾相接**：每笔的终点就是下一笔的起点。若一发现异类型分型就立即
       固定成笔，同类型分型插在中间时会产生断开的两笔（实测用例捕获到过这个问题）。
    """
    out: list[Fractal] = []  # 归约后的转折点序列（交替）
    for f in fractals:
        if not out:
            out.append(f)
            continue
        last = out[-1]
        if f.kind == last.kind:
            if _more_extreme(f, last):
                out[-1] = f  # 同类型延伸
            continue

        if f.idx - last.idx >= min_gap:
            out.append(f)
            continue

        # 异类型但间距不足：它很可能只是同一段行情里的小波动。
        # 若它比"倒数第二个"（与它同类型）更极端，就替换掉，并把 last 丢掉，
        # 使波形回到光滑的交替序列。
        if len(out) >= 2 and _more_extreme(f, out[-2]):
            out[-2] = f
            out.pop()

    return [_make_stroke(out[i], out[i + 1]) for i in range(len(out) - 1)]


def find_pivots(strokes: list[Stroke], merge_overlapping: bool = True) -> list[Pivot]:
    """找笔中枢：连续三笔价格区间有重叠。

    `ZG = min(三笔高点)`，`ZD = max(三笔低点)`；仅当 `ZG > ZD` 时构成中枢。

    每个"连续三笔"都是一个候选窗口，因此同一段行情会产生大量重叠的候选中枢
    （实测 26 笔 → 22 个候选，几乎全是冗余）。`merge_overlapping=True` 时按缠论的
    **中枢延伸** 语义把它们并起来：后续候选若与已有中枢区间仍有重叠，就归入同一中枢
    并延长其结束位置（区间保持最初三笔确定的 `[zd, zg]` 不变）；一旦不再重叠，
    说明价格离开了该中枢，开始新的中枢。
    """
    candidates: list[Pivot] = []
    for i in range(len(strokes) - 2):
        s1, s2, s3 = strokes[i], strokes[i + 1], strokes[i + 2]
        zg = min(s1.high, s2.high, s3.high)
        zd = max(s1.low, s2.low, s3.low)
        if zg > zd:
            candidates.append(
                Pivot(
                    start_stroke=i,
                    end_stroke=i + 2,
                    zg=zg,
                    zd=zd,
                    start_date=s1.start_date,
                    end_date=s3.end_date,
                )
            )

    if not merge_overlapping or not candidates:
        return candidates

    merged: list[Pivot] = []
    for pv in candidates:
        if not merged:
            merged.append(pv)
            continue
        last = merged[-1]
        # 与已有中枢仍有重叠 → 中枢延伸：区间不变，只把结束位置往后延
        if pv.zd <= last.zg and pv.zg >= last.zd:
            last.end_stroke = max(last.end_stroke, pv.end_stroke)
            last.end_date = pv.end_date
        else:
            merged.append(pv)
    return merged


def find_third_buys(strokes: list[Stroke], pivots: list[Pivot]) -> list[ThirdBuy]:
    """三买：价格向上离开中枢后，回抽的低点仍在中枢上沿之上。

    对每个中枢，向后找第一个满足条件的「下笔」：它自身低点 > `ZG`，
    且它之前的那个高点也 > `ZG`（说明确实是向上离开的）。

    相邻中枢常共享笔（中枢延伸），会对同一笔重复报出三买，因此按笔去重。
    """
    out: list[ThirdBuy] = []
    seen: set[int] = set()
    for pv in pivots:
        for j in range(pv.end_stroke + 1, len(strokes)):
            s = strokes[j]
            if s.direction != DOWN:
                continue
            prev = strokes[j - 1] if j - 1 >= 0 else None
            left_up = (s.high > pv.zg) and (prev is not None and prev.high > pv.zg)
            if left_up and s.low > pv.zg:
                if j not in seen:
                    seen.add(j)
                    out.append(
                        ThirdBuy(
                            stroke_index=j,
                            price=s.low,
                            date=s.end_date,
                            above=s.low - pv.zg,
                        )
                    )
                break
            # 已经跌回中枢之内，说明这次离开失败了
            if s.low <= pv.zg:
                break
    return out


def chan_analysis(
    bars: pd.DataFrame,
    min_gap: int = DEFAULT_MIN_GAP,
    tail: int = 400,
) -> ChanResult:
    """对一份日线做缠论结构识别（取最近 `tail` 根 K 线以控制计算量）。"""
    result = ChanResult()
    if bars is None or bars.empty or not {"high", "low"} <= set(bars.columns):
        result.explain.append("数据不足或缺少 high/low 列，无法做缠论分析")
        return result

    df = bars.tail(tail).reset_index(drop=True)
    merged = merge_bars(df)
    result.merged = merged
    if len(merged) < 5:
        result.explain.append("合并后 K 线不足 5 根，无法形成分型")
        return result

    # 合并 K 线不直接对应原始行，取每根合并 K 线最后一根原始 K 线的日期作为其日期
    dates = df["date"].tolist() if "date" in df.columns else None
    merged_dates = [dates[m.end] if dates else None for m in merged]

    result.fractals = find_fractals(merged, merged_dates)
    result.strokes = find_strokes(result.fractals, min_gap=min_gap)
    result.pivots = find_pivots(result.strokes)
    result.third_buys = find_third_buys(result.strokes, result.pivots)

    result.explain.append(
        f"{len(df)} 根 K 线经包含处理后为 {len(merged)} 根，"
        f"识别出 {len(result.fractals)} 个分型、{len(result.strokes)} 笔、"
        f"{len(result.pivots)} 个中枢、{len(result.third_buys)} 个三买"
    )
    if result.pivots:
        result.explain.append("最近" + result.pivots[-1].describe())
    if result.third_buys:
        result.explain.append("最近" + result.third_buys[-1].describe())
    result.explain.append(
        "注意：本模块只做结构识别（包含处理/分型/笔/中枢/三买），"
        "不含线段、走势类型与背驰，因此不提供一买/二买。"
    )
    return result


if __name__ == "__main__":
    # 简单自测：一段人工折线行情，看结构是否被正确识别
    import numpy as np

    points = [10.0, 12.0, 10.2, 11.8, 10.4, 12.4, 11.0]
    closes: list[float] = []
    for a, b in zip(points, points[1:]):
        closes.extend(np.linspace(a, b, 6, endpoint=False))
    closes.append(points[-1])
    prices = np.array(closes)
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2026-01-01", periods=len(prices)),
            "high": prices + 0.05,
            "low": prices - 0.05,
            "close": prices,
        }
    )
    res = chan_analysis(df)
    for line in res.to_dict()["explain"]:
        print(line)
    print("分型:", [(f.idx, f.kind) for f in res.fractals][:12])
    for s in res.strokes:
        print("  笔:", s.describe())
    for p in res.pivots:
        print("  ", p.describe())
    for t in res.third_buys:
        print("  ", t.describe())
