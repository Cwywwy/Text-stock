"""缠论结构识别测试（暂时需求 6 后半）。

刻意用**人工折线行情**与**手工构造的分型**：每个用例的预期结果都能手算出来，
因此算法错在哪一步会直接暴露，而不是"跑通了就算对"。

覆盖：K 线包含处理（向上/向下两个方向）、分型（含伪分型拒绝）、笔（交替、最小间距、
笔的延伸）、笔中枢（含不重叠与共享笔）、三买（含失败情形与前置条件）、以及边界。

注意：折线行情要在最后多留一段，否则**末端的转折点没有右邻居、成不了分型**
（第一版测试就是因此少了最后一笔笔而误判"三买没被识别"）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stock_plan.factors.chan import (
    BOTTOM,
    DEFAULT_MIN_GAP,
    DOWN,
    TOP,
    UP,
    Fractal,
    Pivot,
    Stroke,
    chan_analysis,
    find_fractals,
    find_pivots,
    find_strokes,
    find_third_buys,
    merge_bars,
)


def _bars_from_hl(pairs: list[tuple[float, float]]) -> pd.DataFrame:
    """直接用 (high, low) 列表构造日线，便于手算包含处理。"""
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2026-01-01", periods=len(pairs)),
            "high": [h for h, _ in pairs],
            "low": [l for _, l in pairs],
            "close": [(h + l) / 2 for h, l in pairs],
        }
    )


def _zigzag(points: list[float], seg_len: int = 6, width: float = 0.05) -> pd.DataFrame:
    """按给定转折点生成折线行情：每段 seg_len 根 K 线，high/low 随收盘价平移。

    段内单调 → 不触发包含处理，因此分型位置可以手算。
    `np.linspace(..., endpoint=False)` 使转折点只落在下一段的起点上，不会重复。
    """
    closes: list[float] = []
    for a, b in zip(points, points[1:]):
        closes.extend(np.linspace(a, b, seg_len, endpoint=False))
    closes.append(points[-1])
    prices = np.array(closes)
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2026-01-01", periods=len(prices)),
            "high": prices + width,
            "low": prices - width,
            "close": prices,
        }
    )


# ---------- K 线包含处理 ----------
def test_merge_bars_up_direction() -> None:
    """向上方向：有包含关系时取「高高」。

    (9,7) (10,8) (11,7) (12,9)：第 3 根包含第 2 根 → 合并为 (11,8)
    手算结果：(9,7) (11,8) (12,9)
    """
    merged = merge_bars(_bars_from_hl([(9, 7), (10, 8), (11, 7), (12, 9)]))
    assert [(m.high, m.low) for m in merged] == [(9, 7), (11, 8), (12, 9)]
    assert merged[1].bars_merged == 2, "第 2、3 根应合并为一根"


def test_merge_bars_down_direction() -> None:
    """向下方向：有包含关系时取「低低」。

    (12,10) (11,9) (11.5,8.5) (10,8)：第 3 根包含第 2 根 → 合并为 (11,8.5)
    手算结果：(12,10) (11,8.5) (10,8)
    """
    merged = merge_bars(_bars_from_hl([(12, 10), (11, 9), (11.5, 8.5), (10, 8)]))
    assert [(m.high, m.low) for m in merged] == [(12, 10), (11, 8.5), (10, 8)]


def test_merge_bars_no_containment() -> None:
    merged = merge_bars(_bars_from_hl([(9, 7), (10, 8), (11, 9)]))
    assert len(merged) == 3
    assert all(m.bars_merged == 1 for m in merged)


def test_merge_bars_empty() -> None:
    assert merge_bars(pd.DataFrame()) == []
    assert merge_bars(None) == []


# ---------- 分型 ----------
def test_find_fractals_top_and_bottom() -> None:
    """顶分型要求中间那根的**高点与低点都是最高**，底分型反之。"""
    top = merge_bars(_bars_from_hl([(9, 7), (11, 8), (10, 7.5)]))
    fr = find_fractals(top)
    assert len(fr) == 1 and fr[0].kind == TOP and fr[0].idx == 1

    bottom = merge_bars(_bars_from_hl([(10, 8), (9, 7), (10.5, 7.5)]))
    fr = find_fractals(bottom)
    assert len(fr) == 1 and fr[0].kind == BOTTOM and fr[0].idx == 1


def test_pseudo_fractal_is_rejected() -> None:
    """只"高点更高"但低点不高 → 不是顶分型（只看高点会误判）。"""
    merged = merge_bars(_bars_from_hl([(9, 7), (11, 6.5), (10, 7.5)]))
    assert [f for f in find_fractals(merged) if f.kind == TOP] == []


# ---------- 笔 ----------
def test_strokes_on_hand_made_zigzag() -> None:
    """转折点 10→12→10.2→11.8→10.4→12.4→11.0（每段 6 根）。

    内部 5 个转折点各成一个分型，并连成 4 笔、方向交替。
    """
    result = chan_analysis(_zigzag([10.0, 12.0, 10.2, 11.8, 10.4, 12.4, 11.0]))

    assert [(f.idx, f.kind) for f in result.fractals] == [
        (6, TOP), (12, BOTTOM), (18, TOP), (24, BOTTOM), (30, TOP)
    ]
    assert len(result.strokes) == 4
    assert [s.direction for s in result.strokes] == [DOWN, UP, DOWN, UP]
    assert [(s.start_idx, s.end_idx) for s in result.strokes] == [
        (6, 12), (12, 18), (18, 24), (24, 30)
    ]
    # 浮点比较必须用 approx
    assert result.strokes[0].high == pytest.approx(12.05)
    assert result.strokes[0].low == pytest.approx(10.15)


def test_strokes_respect_min_gap() -> None:
    """分型间距小于 min_gap 时不能成笔 —— 直接验证规则本身。"""
    fractals = [
        Fractal(1, BOTTOM, high=9.0, low=8.0),
        Fractal(3, TOP, high=10.0, low=9.0),      # 间距 2 < 4
    ]
    assert find_strokes(fractals, min_gap=DEFAULT_MIN_GAP) == []
    # 放低要求后同样两个分型可以成笔，说明拦截确实来自间距规则
    strokes = find_strokes(fractals, min_gap=2)
    assert len(strokes) == 1 and strokes[0].direction == UP


def test_find_strokes_extends_to_more_extreme_fractal() -> None:
    """出现同类型分型时取更极端的作为端点（笔的延伸），不被中间小波动打断。"""
    fractals = [
        Fractal(1, BOTTOM, high=10.0, low=9.0),
        Fractal(5, TOP, high=12.0, low=11.0),
        Fractal(9, TOP, high=13.0, low=12.0),      # 更高的顶 → 前一笔端点应延伸过去
        Fractal(15, BOTTOM, high=9.5, low=8.5),
    ]
    strokes = find_strokes(fractals, min_gap=4)
    assert len(strokes) == 2
    assert strokes[0].high == 13.0, "第一笔的顶应被更新为更高的那个"
    assert strokes[0].start_idx == 1 and strokes[0].end_idx == 9


def test_strokes_are_connected() -> None:
    """笔必须首尾相接：每笔的终点就是下一笔的起点。

    这条是缠论的基本要求。第一版实现"一发现异类型分型就立即固定成笔"，
    在同类型分型插在中间时会生成断开的两笔（如 UP(1→5) 与 DOWN(9→15)，
    中间 5→9 悬空），本用例锁死这个行为。
    """
    fractals = [
        Fractal(1, BOTTOM, high=10.0, low=9.0),
        Fractal(5, TOP, high=12.0, low=11.0),
        Fractal(9, TOP, high=13.0, low=12.0),
        Fractal(15, BOTTOM, high=9.5, low=8.5),
    ]
    strokes = find_strokes(fractals, min_gap=4)
    for prev, nxt in zip(strokes, strokes[1:]):
        assert prev.end_idx == nxt.start_idx, (
            f"笔不连续：前一笔止于 {prev.end_idx}，后一笔起于 {nxt.start_idx}"
        )


def test_real_zigzag_strokes_are_connected() -> None:
    """端到端也要保证连续性（折线行情产生的多笔）。"""
    result = chan_analysis(_zigzag([10.0, 12.0, 10.2, 11.8, 10.4, 12.4, 11.0]))
    assert len(result.strokes) >= 3
    for prev, nxt in zip(result.strokes, result.strokes[1:]):
        assert prev.end_idx == nxt.start_idx


def test_find_strokes_requires_alternating_types() -> None:
    """两个同类型分型之间不能成笔。"""
    fractals = [
        Fractal(1, TOP, high=10.0, low=9.0),
        Fractal(9, TOP, high=11.0, low=10.0),
    ]
    assert find_strokes(fractals, min_gap=4) == []


# ---------- 中枢 ----------
def test_pivot_range_matches_hand_calculation() -> None:
    """中枢区间 = 连续三笔的重叠：ZG = min(三笔高点)，ZD = max(三笔低点)。

    折线 10→12→10.2→11.8→10.4→12.4→11.0 的 4 笔为
      ↓ 12.05~10.15、↑ 11.85~10.15、↓ 11.85~10.35、↑ 12.45~10.35
    手算：前三笔 ZG = min(12.05,11.85,11.85) = 11.85
          ZD = max(10.15,10.15,10.35) = 10.35  → [10.35, 11.85]

    两个候选窗口（笔 0~2 与 1~3）区间相同 → 按中枢延伸并为一个。
    """
    result = chan_analysis(_zigzag([10.0, 12.0, 10.2, 11.8, 10.4, 12.4, 11.0]))
    assert len(result.pivots) == 1, "重叠的候选中枢应被合并为一个"
    pv = result.pivots[0]
    assert pv.zd == pytest.approx(10.35, abs=0.01)
    assert pv.zg == pytest.approx(11.85, abs=0.01)
    assert pv.height > 0
    assert pv.end_stroke == 3, "中枢应延伸到最后一笔"


def test_pivot_overlap_merge_can_be_disabled() -> None:
    """关掉合并时应看到全部候选窗口（用于诊断/对照）。"""
    strokes = [
        Stroke(0, 1, UP, 12.0, 10.0),
        Stroke(1, 2, DOWN, 11.8, 10.2),
        Stroke(2, 3, UP, 11.9, 10.3),
        Stroke(3, 4, DOWN, 11.7, 10.1),
    ]
    assert len(find_pivots(strokes, merge_overlapping=False)) == 2
    assert len(find_pivots(strokes, merge_overlapping=True)) == 1


def test_no_pivot_when_strokes_do_not_overlap() -> None:
    """三笔完全没有重叠区间时不构成中枢。"""
    strokes = [
        Stroke(0, 1, UP, high=10.0, low=8.0),
        Stroke(1, 2, DOWN, high=8.0, low=6.0),
        Stroke(2, 3, UP, high=6.0, low=4.0),
    ]
    assert find_pivots(strokes) == []


def test_pivot_extension_keeps_original_range() -> None:
    """中枢延伸时区间保持不变，只延长结束位置（缠论语义）。

    strokes 0~3 = UP(12.0,10.0) DOWN(11.8,10.2) UP(11.9,10.3) DOWN(11.7,10.1)
    第一个窗口 [0,1,2] → ZG=11.8, ZD=10.3
    第二个窗口 [1,2,3] → ZG=11.7, ZD=10.3，与前者重叠 → 并入，区间仍是 (10.3, 11.8)
    """
    strokes = [
        Stroke(0, 1, UP, 12.0, 10.0),
        Stroke(1, 2, DOWN, 11.8, 10.2),
        Stroke(2, 3, UP, 11.9, 10.3),
        Stroke(3, 4, DOWN, 11.7, 10.1),
    ]
    pivots = find_pivots(strokes)
    assert len(pivots) == 1
    assert (round(pivots[0].zd, 2), round(pivots[0].zg, 2)) == (10.3, 11.8)
    assert pivots[0].end_stroke == 3


def test_pivot_new_one_when_price_leaves_range() -> None:
    """价格离开原中枢区间后应开始新的中枢，不应被无限并入。

    第一个窗口 [0,1,2] → ZG=min(12.0,11.8,11.9)=11.8, ZD=max(10.0,10.2,10.3)=10.3
    第二个窗口 [3,4,5] → ZG=min(20.0,19.5,19.8)=19.5, ZD=max(18.0,18.5,18.6)=18.6
    两者不重叠 → 两个独立中枢。
    """
    strokes = [
        Stroke(0, 1, UP, 12.0, 10.0),
        Stroke(1, 2, DOWN, 11.8, 10.2),
        Stroke(2, 3, UP, 11.9, 10.3),
        # 下面三笔整体上移，与 [10.3, 11.8] 不再重叠
        Stroke(3, 4, UP, 20.0, 18.0),
        Stroke(4, 5, DOWN, 19.5, 18.5),
        Stroke(5, 6, UP, 19.8, 18.6),
    ]
    pivots = find_pivots(strokes)
    assert len(pivots) == 2
    assert pivots[0].zg == pytest.approx(11.8)
    assert pivots[1].zd == pytest.approx(18.6)
    assert pivots[1].zg == pytest.approx(19.5)


# ---------- 三买 ----------
def _strokes_with_pivot():
    """4 笔构成一个中枢 [0,1,2]，随后向上离开、再回抽不下中枢。"""
    return [
        Stroke(0, 1, UP, 12.0, 10.0),
        Stroke(1, 2, DOWN, 11.8, 10.2),
        Stroke(2, 3, UP, 11.9, 10.3),
        Stroke(3, 4, UP, 13.05, 10.4),      # 向上离开中枢
        Stroke(4, 5, DOWN, 13.05, 12.15),   # 回抽，低点 12.15 > ZG 11.8
    ]


def test_third_buy_detected() -> None:
    """向上离开中枢后回抽不跌回中枢 → 三买。"""
    strokes = _strokes_with_pivot()
    pivots = find_pivots(strokes)
    assert pivots, "前提：这组笔应构成中枢"
    buys = find_third_buys(strokes, pivots)
    assert buys, "应识别出三买"
    assert buys[0].price == pytest.approx(12.15)
    assert buys[0].above > 0
    # 同一个事件被多个重叠中枢看到，也只应报一次
    assert [b.stroke_index for b in buys] == sorted({b.stroke_index for b in buys})


def test_no_third_buy_when_price_falls_back_into_pivot() -> None:
    """回抽跌回中枢之内 → 不是三买。"""
    strokes = _strokes_with_pivot()
    strokes[-1] = Stroke(4, 5, DOWN, 13.05, 11.0)   # 低点 11.0 < ZG 11.8
    assert find_third_buys(strokes, find_pivots(strokes)) == []


def test_third_buy_requires_leaving_stroke_above_pivot() -> None:
    """仅靠"下笔低点 > ZG"不够，还必须确认之前的上涨笔确实越过了中枢上沿。"""
    pv = Pivot(0, 2, zg=11.0, zd=10.0)
    ok = [
        Stroke(0, 1, UP, 12.0, 10.0),
        Stroke(1, 2, DOWN, 11.8, 10.2),
        Stroke(2, 3, UP, 11.9, 10.3),
        Stroke(3, 4, UP, 12.0, 10.4),      # 越过了 ZG=11
        Stroke(4, 5, DOWN, 12.0, 11.5),
    ]
    assert len(find_third_buys(ok, [pv])) == 1

    bad = list(ok)
    bad[3] = Stroke(3, 4, UP, 10.9, 10.4)  # 该笔高点 10.9 < ZG=11，没真正离开
    assert find_third_buys(bad, [pv]) == []


def test_third_buy_on_hand_made_zigzag() -> None:
    """端到端：折线行情里也要能识别出三买。

    折线 10→12→10.2→11.8→10.4→13.0→12.2→13.5：
    多留最后一段，否则回抽的底点位于末端、没有右邻居而成不了分型
    （第一版测试就是因此误判"三买没被识别"）。
    """
    result = chan_analysis(_zigzag([10.0, 12.0, 10.2, 11.8, 10.4, 13.0, 12.2, 13.5]))
    assert result.third_buys, "应识别出一个三买"
    assert result.third_buys[0].price == pytest.approx(12.15, abs=0.01)


# ---------- 边界 ----------
def test_chan_analysis_handles_insufficient_data() -> None:
    empty = chan_analysis(pd.DataFrame())
    assert "数据不足" in empty.explain[0]

    tiny = chan_analysis(_zigzag([10.0, 10.5], seg_len=2))
    assert tiny.strokes == [] and tiny.pivots == []


def test_chan_analysis_requires_high_low_columns() -> None:
    bad = pd.DataFrame({"date": pd.bdate_range("2026-01-01", periods=10), "close": range(10)})
    assert "数据不足" in chan_analysis(bad).explain[0]


def test_chan_result_is_serialisable() -> None:
    """结果要能直接给 UI/LLM 用。"""
    d = chan_analysis(_zigzag([10.0, 12.0, 10.2, 11.8, 10.4, 12.4, 11.0])).to_dict()
    assert d["stroke_count"] == 4
    # 两个候选窗口区间相同，按中枢延伸并为一个
    assert d["pivot_count"] == 1
    assert isinstance(d["explain"], list) and d["explain"]
    assert d["latest_pivot"] and "中枢" in d["latest_pivot"]
    assert any("不提供一买/二买" in e for e in d["explain"]), "必须明确声明未实现的范围"
