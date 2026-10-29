# -*- coding: utf-8 -*-
"""RRG 行业轮动（任务 C1）单元测试。

覆盖：
- 行业等权指数聚合口径（缺行情剔除、有效成分不足置 NaN、北交所排除）；
- 与 `factors/regime.equal_weight_index` 在全市场等权口径下的一致性（1e-9）；
- RRG 预热边界（238/239/317/318 行）与「显式抛 ValueError」；
- RRG 公式的独立循环实现对拍（手算小样本）；
- 四象限判定（含恰好 100 归上方）；
- `select_industries` 只在「转强」里按距离取前 N，不足告警；
- 行业轮动的时点口径（信号只用前一交易日）、成本扣除与指标输出。
"""
from __future__ import annotations

import math
import warnings

import numpy as np
import pandas as pd
import pytest

from stock_plan.backtest.metrics import calc_metrics
from stock_plan.backtest.rotation import run_industry_rotation
from stock_plan.factors.regime import equal_weight_index
from stock_plan.factors.rrg import (
    QUADRANT_IMPROVING,
    QUADRANT_LAGGING,
    QUADRANT_LEADING,
    QUADRANT_WEAKENING,
    RRG_MIN_ROWS,
    RR_RATIO_MIN_ROWS,
    build_equal_weight_benchmark,
    build_industry_index,
    compute_rrg,
    industry_index_wide,
    select_industries,
)


def _dates(n: int, start: str = "2021-01-04") -> pd.DatetimeIndex:
    """生成 n 个连续工作日（不关心是否真实交易日，单测只需要连续日期）。"""
    return pd.bdate_range(start=start, periods=n)


def _bars(close: list[float], start: str = "2021-01-04") -> pd.DataFrame:
    """由收盘价序列构造最小可用日线表。"""
    dates = _dates(len(close), start=start)
    arr = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "date": dates,
            "open": arr,
            "high": arr * 1.01,
            "low": arr * 0.99,
            "close": arr,
            "volume": np.full(len(arr), 1_000_000.0),
            "amount": np.full(len(arr), 1e8),
            "turnover": np.full(len(arr), 0.01),
        }
    )


def _rising(n: int, daily: float = 0.004, start: float = 10.0) -> list[float]:
    """等比上涨价格序列。"""
    return [start * (1.0 + daily) ** i for i in range(n)]


def _panel(industries: dict[str, list[float]], start: str = "2021-01-04") -> pd.DataFrame:
    """把 {行业: 指数序列} 拼成宽表（index=日期）。"""
    return pd.DataFrame(industries, index=_dates(len(next(iter(industries.values()))), start=start))


# ---------------------------------------------------------------- 行业指数聚合


def test_build_industry_index_equal_weight_chain():
    """行业指数 = 成分日收益等权平均后链式累乘，首日归一 1.0。"""
    n = 30
    r1 = 0.01
    r2 = 0.03
    bars_map = {
        "600001": _bars(_rising(n, r1)),
        "600002": _bars(_rising(n, r2)),
        "600003": _bars(_rising(n, r1)),
    }
    mapping = {"600001": "银行", "600002": "银行", "600003": "银行"}
    panel = build_industry_index(bars_map, mapping, min_components=1)
    wide = industry_index_wide(panel)
    assert list(wide.columns) == ["银行"]
    expected = (1.0 + (r1 + r2 + r1) / 3.0) ** np.arange(n)
    assert wide["银行"].iloc[0] == pytest.approx(1.0)
    np.testing.assert_allclose(wide["银行"].to_numpy(), expected, rtol=1e-12)


def test_build_industry_index_drops_sparse_days_and_missing_bars():
    """有效成分 < min_components 的日子置 NaN；缺行情成分不计入当日收益。"""
    n = 6
    bars_map = {
        "600001": _bars([10.0] * n),
        "600002": _bars([10.0] * n),
        "600101": _bars([20.0] * n),
    }
    mapping = {"600001": "银行", "600002": "银行", "600101": "医药"}
    panel = build_industry_index(bars_map, mapping, min_components=3)
    wide = industry_index_wide(panel)
    # 银行只有 2 个成分、医药只有 1 个 → 全部低于门槛，长表为空
    assert panel.empty
    assert wide.empty

    # 门槛降到 2 后，银行重新出现；医药仍不足
    panel2 = build_industry_index(bars_map, mapping, min_components=2)
    assert set(panel2["industry"]) == {"银行"}
    assert (panel2["n_components"] == 2).all()

    # 让其中一个成分当天停牌（缺行）→ 当日有效成分变 1，被剔除成 NaN
    dropped = {k: v.iloc[:-1].copy() for k, v in bars_map.items()}
    panel3 = build_industry_index(dropped, mapping, min_components=2)
    bank = industry_index_wide(panel3)["银行"]
    assert bank.index[-1] < _dates(n)[-1]
    assert pd.isna(bank.iloc[-1]) is False


def test_build_industry_index_excludes_bse_and_unmapped():
    """北交所 920xxx 与无行业映射的股票不进入聚合。"""
    n = 10
    bars_map = {
        "600001": _bars(_rising(n)),
        "920001": _bars(_rising(n)),
        "000002": _bars(_rising(n)),
    }
    mapping = {"600001": "银行", "920001": "银行"}  # 000002 故意不给行业
    panel = build_industry_index(bars_map, mapping, min_components=1)
    assert panel["n_components"].max() == 1
    assert set(panel["industry"]) == {"银行"}


def test_industry_index_matches_regime_equal_weight_index():
    """全市场同一行业、门槛 1 时，必须与 `regime.equal_weight_index` 逐值一致。"""
    n = 40
    bars_map = {
        "600001": _bars(_rising(n, 0.01)),
        "600002": _bars(_rising(n, -0.005)),
        "600003": _bars(_rising(n, 0.003)),
        "600004": _bars(_rising(n, 0.0)),
    }
    mapping = {c: "全市场" for c in bars_map}
    panel = build_industry_index(bars_map, mapping, min_components=1)
    mine = industry_index_wide(panel)["全市场"]
    ref = equal_weight_index(bars_map, min_contributors=1)
    pd.testing.assert_series_equal(mine, ref, check_names=False, rtol=1e-9, atol=1e-12)


# ---------------------------------------------------------------- RRG 预热与公式


def _rrg_inputs(n: int) -> tuple[pd.DataFrame, pd.Series]:
    """构造 n 行、3 个行业的确定性指数面板与等权基准。"""
    industries = {
        "银行": [100.0 * (1.001) ** i for i in range(n)],
        "医药": [100.0 * (1.0 + 0.002 * math.sin(i / 7.0)) ** i for i in range(n)],
        "电子": [100.0 * (1.0005) ** i for i in range(n)],
    }
    panel = _panel(industries)
    return panel, build_equal_weight_benchmark(panel)


def test_compute_rrg_warmup_raises_explicitly():
    """238 行报 RS-Ratio 不足；239 行报完整 RRG 不足；317 行仍报不足；318 行通过。"""
    panel238, bench238 = _rrg_inputs(238)
    with pytest.raises(ValueError, match=f"RS-Ratio 需 {RR_RATIO_MIN_ROWS} 行"):
        compute_rrg(panel238, bench238)

    panel239, bench239 = _rrg_inputs(239)
    with pytest.raises(ValueError, match=f"完整 RRG 需 {RRG_MIN_ROWS} 行"):
        compute_rrg(panel239, bench239)

    panel317, bench317 = _rrg_inputs(317)
    with pytest.raises(ValueError, match=f"完整 RRG 需 {RRG_MIN_ROWS} 行"):
        compute_rrg(panel317, bench317)

    panel318, bench318 = _rrg_inputs(318)
    out = compute_rrg(panel318, bench318)
    assert not out.empty


def test_compute_rrg_first_valid_positions_match_formula():
    """默认参数下 RS-Ratio 首个有值位置 = 239、RS-Momentum = 318（0 基）。"""
    panel, bench = _rrg_inputs(340)
    out = compute_rrg(panel, bench)
    bank = out[out["industry"] == "银行"].sort_values("date").reset_index(drop=True)
    first_ratio = bank["rs_ratio"].first_valid_index()
    first_mom = bank["rs_momentum"].first_valid_index()
    assert first_ratio == 239
    assert first_mom == 318
    assert bank.loc[238, "rs_ratio"] != bank.loc[238, "rs_ratio"]  # NaN


def test_compute_rrg_matches_independent_loop_reference():
    """用显式循环实现独立复算 RS / RS-Ratio / RS-Momentum，逐值对拍。"""
    n = 120
    ratio_lb, mom_lb, smooth = 5, 3, 2
    panel, bench = _rrg_inputs(n)
    out = compute_rrg(panel, bench, ratio_lb=ratio_lb, mom_lb=mom_lb, smooth=smooth)

    cols = list(panel.columns)
    rs_ref = panel.div(bench, axis=0) * 100.0
    ratio_ref = pd.DataFrame(index=panel.index, columns=cols, dtype=float)
    mom_ref = pd.DataFrame(index=panel.index, columns=cols, dtype=float)
    for col in cols:
        rs_vals = rs_ref[col].to_numpy(dtype=float)
        for i in range(len(panel)):
            if i - ratio_lb - (smooth - 1) >= 0:
                window = [100.0 * rs_vals[i - k] / rs_vals[i - k - ratio_lb] for k in range(smooth)]
                ratio_ref.iloc[i, ratio_ref.columns.get_loc(col)] = sum(window) / smooth
    ratio_vals = {c: ratio_ref[c].to_numpy(dtype=float) for c in cols}
    for col in cols:
        vals = ratio_vals[col]
        for i in range(len(panel)):
            if i - mom_lb - (smooth - 1) >= 0 and np.isfinite(vals[i - mom_lb]):
                window = [100.0 * vals[i - k] / vals[i - k - mom_lb] for k in range(smooth)]
                if all(np.isfinite(x) for x in window):
                    mom_ref.iloc[i, mom_ref.columns.get_loc(col)] = sum(window) / smooth

    for col in cols:
        got = out[out["industry"] == col].set_index("date").sort_index()
        pd.testing.assert_series_equal(got["rs"], rs_ref[col].reindex(got.index), check_names=False, rtol=1e-12)
        np.testing.assert_allclose(
            got["rs_ratio"].to_numpy(dtype=float),
            ratio_ref[col].reindex(got.index).to_numpy(dtype=float),
            rtol=1e-12, equal_nan=True,
        )
        np.testing.assert_allclose(
            got["rs_momentum"].to_numpy(dtype=float),
            mom_ref[col].reindex(got.index).to_numpy(dtype=float),
            rtol=1e-12, equal_nan=True,
        )


def test_quadrant_boundaries_treat_exact_hundred_as_above():
    """恰好 100 归「上方」：>100&>100 领先；<100&>100 转强（>=100 才算上方）。"""
    day = pd.DataFrame(
        {
            "industry": ["甲", "乙", "丙", "丁"],
            "rs_ratio": [100.0, 99.5, 100.0, 99.5],
            "rs_momentum": [100.0, 100.0, 99.5, 99.5],
            "quadrant": [QUADRANT_LEADING, QUADRANT_IMPROVING, QUADRANT_WEAKENING, QUADRANT_LAGGING],
        }
    )
    # compute_rrg 内部判定与这里一致：≥100 视为「上方」
    assert select_industries(day, top_n=1) == ["乙"]


# ---------------------------------------------------------------- 选行业


def test_select_industries_only_improving_sorted_by_distance():
    """只在「转强」里挑，按到 (100,100) 的欧氏距离降序。"""
    day = pd.DataFrame(
        {
            "industry": ["甲", "乙", "丙", "丁"],
            "rs_ratio": [100.0, 95.0, 90.0, 99.0],
            "rs_momentum": [99.0, 105.0, 100.0, 102.0],
            "quadrant": [QUADRANT_WEAKENING, QUADRANT_IMPROVING, QUADRANT_LAGGING, QUADRANT_IMPROVING],
        }
    )
    picked = select_industries(day, top_n=2)
    # 乙距离 sqrt(25+25)=7.07；丁距离 sqrt(1+4)=2.24
    assert picked == ["乙", "丁"]


def test_select_industries_warns_when_not_enough_candidates():
    """「转强」不足 top_n 时按实际数量返回并告警，不静默补位。"""
    day = pd.DataFrame(
        {
            "industry": ["甲"],
            "rs_ratio": [95.0],
            "rs_momentum": [105.0],
            "quadrant": [QUADRANT_IMPROVING],
        }
    )
    with pytest.warns(UserWarning, match="少于 top_n"):
        picked = select_industries(day, top_n=6)
    assert picked == ["甲"]


# ---------------------------------------------------------------- 行业轮动回测


def _ready_rrg(panel: pd.DataFrame, quadrants: dict[str, str], ready_from: int = 5) -> pd.DataFrame:
    """构造一份「已成形」的 RRG 长表（跳过预热），用于单独测轮动逻辑。"""
    rows = []
    for i, day in enumerate(panel.index):
        if i < ready_from:
            continue
        for industry in panel.columns:
            rows.append(
                {
                    "date": day,
                    "industry": industry,
                    "rs": 100.0,
                    "rs_ratio": 105.0 if quadrants.get(industry) == QUADRANT_IMPROVING else 95.0,
                    "rs_momentum": 105.0 if quadrants.get(industry) == QUADRANT_IMPROVING else 95.0,
                    "quadrant": quadrants.get(industry, QUADRANT_LAGGING),
                }
            )
    return pd.DataFrame(rows)


def test_rotation_uses_previous_day_signal_and_charges_cost():
    """信号只用前一日：起点当天无信号不持仓；换仓当天按双边成本扣钱。"""
    n = 12
    industries = {
        "甲": [100.0 * (1.01) ** i for i in range(n)],
        "乙": [100.0 * (1.0) ** i for i in range(n)],
        "丙": [100.0 * (1.0) ** i for i in range(n)],
    }
    panel = _panel(industries)
    rrg = _ready_rrg(panel, {"甲": QUADRANT_IMPROVING, "乙": QUADRANT_IMPROVING}, ready_from=2)
    result = run_industry_rotation(
        panel, rrg, start=panel.index[3], rebalance_days=100, top_n=2, cost_bps=10.0
    )
    # 起点当天用「前一日」信号 → 持仓甲、乙
    assert set(result.holdings["industry"]) == {"甲", "乙"}
    first_day = result.holdings["date"].min()
    assert first_day == panel.index[3]
    # 从空仓建到两行业等权：只发生买入，单边 10bp → 总成本 10bp（不是 20bp）
    assert result.total_cost == pytest.approx(0.001, rel=1e-6)
    assert result.metrics["trade_count"] == 2


def test_rotation_benchmark_and_metrics_shape():
    """基准为 31 行业等权（此处 3 个行业），指标键与股票回测一致。"""
    n = 20
    industries = {
        "甲": [100.0 * (1.002) ** i for i in range(n)],
        "乙": [100.0 * (1.001) ** i for i in range(n)],
        "丙": [100.0 * (1.0) ** i for i in range(n)],
    }
    panel = _panel(industries)
    rrg = _ready_rrg(panel, {"甲": QUADRANT_IMPROVING}, ready_from=2)
    result = run_industry_rotation(panel, rrg, start=panel.index[4], rebalance_days=5, top_n=1)
    assert list(result.metrics) == list(calc_metrics(pd.Series([1.0, 1.1], index=panel.index[:2]), pd.DataFrame()))
    bench = build_equal_weight_benchmark(panel)
    assert (result.benchmark > 0).all()
    assert result.benchmark.iloc[0] == pytest.approx(1.0)
    assert str(bench.index[0]) == str(panel.index[0])


def test_rotation_rejects_insufficient_input():
    """行业指数为空 / RRG 无成形行时显式报错，不返回空结果。"""
    with pytest.raises(ValueError):
        run_industry_rotation(pd.DataFrame(), pd.DataFrame())
    panel = _panel({"甲": [1.0, 1.1, 1.2], "乙": [1.0, 1.0, 1.0]})
    with pytest.raises(ValueError):
        run_industry_rotation(panel, pd.DataFrame(columns=["date", "industry", "rs", "rs_ratio", "rs_momentum", "quadrant"]))
