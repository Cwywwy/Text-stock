# -*- coding: utf-8 -*-
"""引擎性能改造逐值对拍测试（Phase 14）。

为什么需要：Phase 14 把回测主循环的「pandas 逐行 Series 取值」换成「numpy 矩阵行 +
整数列号」取值。本文件内嵌一份**改造前实现的参照拷贝**（`_legacy_run_backtest`，从
改造前 engine.py 逐行复制，只改函数名并加 `captured` 钩子），在同一份合成数据上分别
跑新旧实现，逐候选、逐列、逐交易、逐净值点对拍，确保一个数字都不变（计划书 R1）。

覆盖的边界语义：
- 停牌：`volume == 0` 的交易日不进候选（600005 有连续停牌段、600009 末日停牌）；
- 缺可选列：无 `amount` 列时 `avg_amount20` 回落 NaN（600004）；
- 预热不足：不足 60 根 K 线的股票不进候选（600006 只有 40 根，600011 窗口内次新）；
- NaN 因子：末段 close 为 NaN、中期 amount 为 NaN 的股票（600008 / 600005）；
- 涨跌停：主板 ±10%、创业板/科创板 ±20%，涨停不可买、跌停不可卖；
- 多 code / 数据提前结束（600007）/ ST（600010）/ 缺日线股票；
- 7 个内置策略的合成数据与真实数据小窗口回归入口。

Revision History:
    2026-10-29  (phase 14) add legacy-vs-numpy engine parity tests, synthetic edge cases and seven-strategy entry
    2026-10-29  (phase 15) extend parity tests to prepared reuse: read-only containers, lazy build, sub-window
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import pytest

from stock_plan.backtest.engine import (
    _FACTOR_COLUMNS,
    BacktestConfig,
    BacktestResult,
    PreparedBacktest,
    _FactorTable,
    _build_factor_table,
    _price_limit,
    equal_weight_benchmark,
    prepare_backtest,
    run_backtest,
)
from stock_plan.factors.convergence import (
    CONV_RAW_COLUMNS,
    add_conv_columns,
    compose_pvcf,
)
from stock_plan.factors.fundamental import compute_fundamental_or_neutral
from stock_plan.factors.momentum import MA_RATIO_COLUMNS, add_ma_ratio_columns
from stock_plan.factors.regime import MIN_CONTRIBUTORS
from stock_plan.factors.technical import compute_technical
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.registry import STRATEGIES, create_strategy
from stock_plan.strategy.scorer import fundamental_composite

STRATEGY_NAMES = list(STRATEGIES.keys())


# ---------------------------------------------------------------- 合成数据


def _make_bars(
    *,
    n: int = 160,
    seed: int = 0,
    start_price: float = 10.0,
    drift: float = 0.001,
    vol: float = 0.01,
    start: str = "2024-01-02",
    with_amount: bool = True,
    zero_volume_rows: tuple[int, ...] = (),
    nan_amount_rows: tuple[int, ...] = (),
    nan_close_rows: tuple[int, ...] = (),
    limit_moves: tuple[tuple[int, float], ...] = (),
) -> pd.DataFrame:
    """构造一只股票的合成日线（bdate 日历 + 真实涨跌停缺口 + 各类缺失值）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start, periods=n)
    close = start_price * np.exp(np.cumsum(drift + rng.normal(0.0, vol, n)))
    for row, move in limit_moves:
        # 涨跌停按 A 股口径四舍五入到分，确保引擎 `prev_close*(1±limit)` 精确命中
        close[row] = round(float(close[row - 1]) * (1.0 + move), 2)
    if nan_close_rows:
        close[list(nan_close_rows)] = np.nan
    open_ = close * (1.0 + rng.normal(0.0, 0.002, n))
    high = np.fmax(open_, close) * 1.004
    low = np.fmin(open_, close) * 0.996
    volume = rng.integers(2_000_000, 6_000_000, n).astype(float)
    amount = rng.uniform(2e8, 5e8, n)
    turnover = rng.uniform(0.005, 0.02, n)
    bars = pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "amount": amount,
            "turnover": turnover,
        }
    )
    for row in zero_volume_rows:
        bars.loc[row, "volume"] = 0.0
    for row in nan_amount_rows:
        bars.loc[row, "amount"] = np.nan
    if not with_amount:
        bars = bars.drop(columns=["amount"])
    return bars


def _distressed_universe() -> tuple[pd.DataFrame, dict[str, pd.DataFrame], dict[str, dict]]:
    """构造覆盖全部边界语义的合成股票池（12 只 code + 1 只无日线 ST）。"""
    bars_map: dict[str, pd.DataFrame] = {}
    bars_map["600000"] = _make_bars(seed=1, drift=0.006, start_price=10.0)  # 稳步上涨
    bars_map["600001"] = _make_bars(seed=2, drift=-0.003, start_price=20.0)  # 阴跌
    bars_map["600002"] = _make_bars(
        seed=3,
        drift=0.002,
        start_price=8.0,
        limit_moves=((120, 0.10), (121, 0.10), (140, -0.10)),
    )
    bars_map["300002"] = _make_bars(
        seed=4,
        drift=0.004,
        start_price=15.0,
        limit_moves=((110, 0.20), (130, -0.20)),
    )
    bars_map["688003"] = _make_bars(
        seed=5,
        drift=0.003,
        start_price=30.0,
        limit_moves=((115, 0.20),),
    )
    # 缺 amount 列：引擎候选里的 avg_amount20 必须回落 NaN
    bars_map["600004"] = _make_bars(seed=6, drift=0.001, start_price=12.0, with_amount=False)
    # 中期停牌（volume=0）+ 中期成交额 NaN（距离窗口起点足够远的 rolling 窗口）
    bars_map["600005"] = _make_bars(
        seed=7,
        drift=0.002,
        start_price=9.0,
        zero_volume_rows=tuple(range(100, 106)),
        nan_amount_rows=(60, 61, 62),
    )
    # 预热不足：只有 40 根 K 线
    bars_map["600006"] = _make_bars(n=40, seed=8, start_price=11.0)
    # 数据提前结束（退市语义）：120 根后没有行情
    bars_map["600007"] = _make_bars(n=120, seed=9, start_price=14.0)
    # 末段 close 为 NaN：末尾三根因子全 NaN
    bars_map["600008"] = _make_bars(
        seed=10, drift=0.001, start_price=18.0, nan_close_rows=(150, 151, 152)
    )
    # 末日停牌：filter_universe 的 suspended 分支
    bars_map["600009"] = _make_bars(
        seed=11, drift=0.002, start_price=13.0, zero_volume_rows=(159,)
    )
    # 窗口内次新股：窗口开始时不足 60 根，候选循环要靠 p<59 过滤
    bars_map["600011"] = _make_bars(n=90, seed=12, start_price=16.0, start="2024-04-15")

    codes = [
        "600000",
        "600001",
        "600002",
        "300002",
        "688003",
        "600004",
        "600005",
        "600006",
        "600007",
        "600008",
        "600009",
        "600011",
        "600010",
    ]
    names = {code: f"测试{code}" for code in codes}
    names["600009"] = "测试末停牌"
    names["600010"] = "测试ST股"
    is_st = [1 if code == "600010" else 0 for code in codes]
    board = []
    for code in codes:
        if code.startswith("300"):
            board.append("创业板")
        elif code.startswith("688"):
            board.append("科创板")
        else:
            board.append("主板")
    stock_list = pd.DataFrame(
        {"code": codes, "name": [names[code] for code in codes], "is_st": is_st, "board": board}
    )

    fund_map: dict[str, dict] = {
        # 直接给三项分数：走 compute_fundamental_or_neutral 的「原样 float」分支
        "600000": {"value_score": 66.0, "growth_score": 55.0, "quality_score": 77.0},
        "600001": {"value_score": 40.0, "growth_score": 61.0, "quality_score": 52.0},
        "600002": {"value_score": 88.0, "growth_score": 33.0, "quality_score": 45.0},
        # 原始指标：走 compute_fundamental 的分段线性打分分支
        "300002": {
            "pe": 18.0,
            "pb": 1.4,
            "roe": 0.2,
            "revenue_growth": 0.3,
            "net_profit_growth": 0.35,
            "gross_margin": 0.45,
            "debt_ratio": 0.25,
        },
    }
    return stock_list, bars_map, fund_map


def _window_bounds(bars_map: dict[str, pd.DataFrame]) -> tuple[date, date]:
    """窗口取主股票 81~160 根之间：pool_bars 有 81 根（≥MIN_BARS）且覆盖全部边界行。"""
    dates = bars_map["600000"]["date"]
    return dates.iloc[80].date(), dates.iloc[159].date()


def _daily_config(
    bars_map: dict[str, pd.DataFrame],
    *,
    fundamental_as_of: date | None = None,
) -> BacktestConfig:
    start, end = _window_bounds(bars_map)
    return BacktestConfig(
        start=start,
        end=end,
        top_n=3,
        max_hold_days=10,
        rebalance_freq="daily",
        fundamental_as_of=fundamental_as_of,
    )


# ---------------------------------------------------------------- 探针策略


class _AllCodesStrategy(Strategy):
    """不筛股票、按收盘价打分的探针：让所有边界数据都进入候选循环。"""

    name = "全池探针"
    params: dict = {}

    def filter_universe(self, stock_list, bars_map):  # noqa: ANN001, ANN201
        return list(bars_map.keys())

    def score(self, df_factors):  # noqa: ANN001, ANN201
        return df_factors["close"].astype(float)

    def entry_price(self, row, atr):  # noqa: ANN001, ANN201
        return round(float(row["close"]), 2)

    def exit_price(self, entry, atr):  # noqa: ANN001, ANN201
        return entry * 1.2, entry * 0.9, 5


class _EmptyStrategy(Strategy):
    """空池探针：验证「无候选」时新旧实现返回一致的空结果。"""

    name = "空池探针"
    params: dict = {}

    def filter_universe(self, stock_list, bars_map):  # noqa: ANN001, ANN201
        return []

    def score(self, df_factors):  # noqa: ANN001, ANN201
        raise AssertionError("空股票池不应进入打分")

    def entry_price(self, row, atr):  # noqa: ANN001, ANN201
        raise AssertionError("空股票池不应买入")

    def exit_price(self, entry, atr):  # noqa: ANN001, ANN201
        raise AssertionError("空股票池不应买入")


class _CaptureWrapper(Strategy):
    """包装策略并把每天送给 score() 的候选表拷贝一份，供逐候选逐列对拍。"""

    name = "捕获探针"
    params: dict = {}

    def __init__(self, inner: Strategy):
        self._inner = inner
        self.captured: list[pd.DataFrame] = []

    def filter_universe(self, stock_list, bars_map):  # noqa: ANN001, ANN201
        return self._inner.filter_universe(stock_list, bars_map)

    def score(self, df_factors):  # noqa: ANN001, ANN201
        self.captured.append(df_factors.copy(deep=True))
        return self._inner.score(df_factors)

    def entry_price(self, row, atr):  # noqa: ANN001, ANN201
        return self._inner.entry_price(row, atr)

    def exit_price(self, entry, atr):  # noqa: ANN001, ANN201
        return self._inner.exit_price(entry, atr)


# ---------------------------------------------------------------- 对拍工具


def _assert_candidate_frames_match(
    new_frames: list[pd.DataFrame], legacy_frames: list[pd.DataFrame]
) -> None:
    """逐候选日、逐列对拍候选表（序列/类型/数值）。"""
    assert len(new_frames) == len(legacy_frames), (
        f"候选交易日数量不一致：新实现 {len(new_frames)} 天，旧实现 {len(legacy_frames)} 天"
    )
    for day_no, (new_df, legacy_df) in enumerate(zip(new_frames, legacy_frames)):
        assert list(new_df.columns) == list(legacy_df.columns), (
            f"第 {day_no} 个候选日列顺序不一致：新实现 {list(new_df.columns)}，"
            f"旧实现 {list(legacy_df.columns)}"
        )
        assert new_df.index.tolist() == legacy_df.index.tolist(), "候选表行索引不一致"
        assert new_df["code"].tolist() == legacy_df["code"].tolist(), "候选股票顺序不一致"
        for col in new_df.columns:
            if col == "code":
                continue
            assert new_df[col].dtype == legacy_df[col].dtype, (
                f"第 {day_no} 个候选日列 {col} dtype 不一致："
                f"{new_df[col].dtype} != {legacy_df[col].dtype}"
            )
            np.testing.assert_allclose(
                new_df[col].to_numpy(dtype=np.float64),
                legacy_df[col].to_numpy(dtype=np.float64),
                rtol=1e-12,
                atol=0.0,
                equal_nan=True,
                err_msg=f"第 {day_no} 个候选日列 {col} 数值不一致",
            )


def _assert_results_match(new: BacktestResult, legacy: BacktestResult, label: str = "") -> None:
    """对拍完整回测结果：净值曲线、交易明细、基准。"""
    prefix = f"[{label}] " if label else ""
    assert (new.benchmark is None) == (legacy.benchmark is None), f"{prefix}基准有无不一致"
    pd.testing.assert_series_equal(
        new.equity_curve,
        legacy.equity_curve,
        check_exact=False,
        rtol=1e-12,
        atol=0.0,
        check_dtype=False,
        check_names=False,
        obj=f"{prefix}净值曲线",
    )
    assert list(new.trades.columns) == list(legacy.trades.columns), f"{prefix}交易明细列不一致"
    assert len(new.trades) == len(legacy.trades), (
        f"{prefix}交易笔数不一致：新实现 {len(new.trades)}，旧实现 {len(legacy.trades)}"
    )
    if len(new.trades):
        pd.testing.assert_frame_equal(
            new.trades,
            legacy.trades,
            check_exact=False,
            rtol=1e-12,
            atol=0.0,
            check_dtype=False,
            obj=f"{prefix}交易明细",
        )
    if legacy.benchmark is not None:
        pd.testing.assert_series_equal(
            new.benchmark,
            legacy.benchmark,
            check_exact=False,
            rtol=1e-12,
            atol=0.0,
            check_dtype=False,
            check_names=False,
            obj=f"{prefix}基准曲线",
        )


def _run_both(
    strategy_name: str | None,
    config: BacktestConfig,
    stock_list: pd.DataFrame,
    bars_map: dict[str, pd.DataFrame],
    fund_map: dict[str, dict],
    *,
    probe: Strategy | None = None,
    capture: bool = False,
) -> tuple[BacktestResult, BacktestResult, list[pd.DataFrame], list[pd.DataFrame]]:
    """分别用新实现与旧参照实现跑同一次回测，返回两份结果与（可选）候选表。"""
    if probe is not None:
        def _make():
            return _CaptureWrapper(probe) if capture else probe
    else:
        assert strategy_name is not None
        def _make():
            return _CaptureWrapper(create_strategy(strategy_name)) if capture else create_strategy(strategy_name)

    new_strategy = _make()
    legacy_strategy = _make()
    new_result = run_backtest(new_strategy, config, bars_map, fund_map, stock_list)
    legacy_captured: list[pd.DataFrame] | None = [] if capture else None
    legacy_result = _legacy_run_backtest(
        legacy_strategy, config, bars_map, fund_map, stock_list, legacy_captured
    )
    new_captured = getattr(new_strategy, "captured", [])
    return new_result, legacy_result, new_captured, legacy_captured or []


# 改造前 run_backtest 的参照实现：从改造前 engine.py 逐行复制，
# 仅改函数名 + 追加 captured 钩子（其中 if config.market_timing 分支为原实现的局部 import）。
def _legacy_run_backtest(
    strategy,
    config,
    bars_map,
    fund_map,
    stock_list,
    captured: list[pd.DataFrame] | None = None,
) -> BacktestResult:
    """执行回测。

    参数：
        strategy:  策略实例。
        config:    回测配置。
        bars_map:  {code: 日线 DataFrame}。
        fund_map:  {code: 财务指标 dict}。
        stock_list: 全 A 股列表（用于硬过滤）。

    返回：
        BacktestResult（资金曲线 + 交易明细 + 指标）。
    """
    # 候选股票：硬过滤。
    # ⚠️ 必须**先把行情截到回测开始日**再交给策略过滤（2026-10-28 修正）。
    # 原因：`filter_universe` 内部会做 `tail(20)` / `iloc[-1]` 之类的"近期形态"判断，
    # 而调用方传进来的 bars 覆盖到**回测结束日**。于是在同一起始日下、只改结束日，
    # 股票就会从"在池中"变成"被剔除"——候选池用到了未来数据（实测可复现，见审计 D13）。
    # 截到 start 之后，池子只由"当时能看到的历史"决定，与结束日无关。
    _pool_cutoff = pd.Timestamp(config.start)
    pool_bars = {c: b[b["date"] <= _pool_cutoff] for c, b in bars_map.items()}
    codes = strategy.filter_universe(stock_list, pool_bars)
    if not codes:
        return BacktestResult(pd.Series(dtype=float), pd.DataFrame())

    # 财务快照前视修正的阈值（None = 不做修正，保持旧行为，供老测试/对比用）
    _fund_as_of = config.fundamental_as_of

    # 对齐交易日：取所有股票日期的并集，限制在回测区间内
    all_dates = set()
    for code in codes:
        bars = bars_map[code]
        mask = (bars["date"] >= pd.Timestamp(config.start)) & (
            bars["date"] <= pd.Timestamp(config.end)
        )
        all_dates.update(bars.loc[mask, "date"].dt.date)
    trading_days = sorted(all_dates)
    if not trading_days:
        return BacktestResult(pd.Series(dtype=float), pd.DataFrame())

    # 基准：同期等权持有全部候选股，用于区分「策略能力」与「大盘/池子本身涨跌」。
    benchmark = equal_weight_benchmark(codes, bars_map, config.start, config.end)

    # 为每只股票建立 日期→行号 索引，便于按日取数据
    date_index = {code: bars_map[code].set_index("date") for code in codes}

    # 预计算每只股票的完整技术因子（滚动计算无未来函数，可一次性算完再按日查表）
    # 相比逐日对每只股票重新 compute_technical，可大幅提升回测速度
    factor_index = {}
    for code in codes:
        f = compute_technical(bars_map[code]).set_index("date")
        # 近 20 日最高收盘价（供突破策略使用，rolling 窗口止于当日，无未来函数）
        f["high20"] = f["close"].rolling(20).max()
        # 近 20 日平均成交额（供流动性筛选使用）。必须用 **rolling** 而不是
        # `bars["amount"].tail(20)` —— 后者取的是整条序列末尾，在逐日回测里就是未来数据。
        if "amount" in f.columns:
            f["avg_amount20"] = f["amount"].rolling(20).mean()
        # 自定义均线列（供自定义策略均线自由组合使用）
        for _p in (5, 7, 10, 30):
            f[f"ma{_p}"] = f["close"].rolling(_p).mean()
        # 均线收敛因子（2026-10-29）：滚动计算天然无未来函数，整列预计算、按日取行。
        # PVCF 依赖「当日截面」，只能在候选集拼成 DataFrame 后算（见下方 compose_pvcf）。
        f = add_conv_columns(f, bars_map[code])
        # 动量方向修正（2026-10-29）：MA_L/close 四列，同样整列滚动预计算。
        f = add_ma_ratio_columns(f)
        factor_index[code] = f
    # 每只股票的日期数组（numpy datetime64），配合指针做单调递增查找
    code_dates = {code: factor_index[code].index.values for code in codes}
    code_ptr = {code: -1 for code in codes}

    # 大盘代理：**等权收益指数**（用于大盘择时）
    # 原实现是 closes.mean(axis=1) —— 对原始股价直接取平均，有两个方法学问题：
    #   1. 被高价股主导（茅台 1300 元与 3 元股在均值里权重相同），实际等价于"高价股指数"；
    #   2. 某只股票缺当日 K 线时会从均值里消失，指数水平**跳空** —— 于是
    #      "大盘跌破 MA20"可能由成分变动而非行情触发，仓位被莫名减半。
    # 改为先算每只股票的日收益率、等权平均后累乘，与价格水平和成分变动都无关。
    market_close: pd.Series | None = None
    market_ma20: pd.Series | None = None
    if config.market_timing:
        from stock_plan.factors.regime import MIN_CONTRIBUTORS, equal_weight_index

        # 候选池小于阈值时按候选数放宽，避免"股票太少 → 指数恒为 1.0 →
        # 大盘择时被静默禁用"这种看不出来的行为
        market_close = equal_weight_index(
            {code: bars_map[code] for code in codes},
            min_contributors=max(1, min(MIN_CONTRIBUTORS, len(codes))),
        )
        market_ma20 = market_close.rolling(20).mean() if len(market_close) else None

    cash = config.initial_cash
    positions: dict[str, dict] = {}  # code -> {shares, entry_price, entry_date, stop, target}
    trades: list[dict] = []
    equity_curve: dict[date, float] = {}

    # 每周选股：记录上一周买入日，仅在新的一周首个交易日重新选股
    last_rebalance_week: tuple[int, int] | None = None

    # 每只股票最后一条 K 线的日期与收盘价。用于区分两种情况：
    #   - **临时停牌**：后面还有 K 线（last_bar 晚于今天）→ 继续持有，等复牌
    #   - **退市/长期停牌（行情彻底结束）**：last_bar 早于今天 → 必须强制平仓，
    #     否则该持仓会在估值里被计成 0、在收尾时被静默丢弃（成本也不回现金）
    last_bar: dict[str, tuple[pd.Timestamp | None, float | None]] = {}
    for _code in codes:
        _b = date_index[_code]
        if len(_b):
            last_bar[_code] = (_b.index[-1], float(_b["close"].iloc[-1]))
        else:
            last_bar[_code] = (None, None)

    def _close(code: str, pos: dict, when, close: float, reason: str) -> float:
        """按给定价格平仓并记录交易，返回平仓后的现金。"""
        sell_price = close * (1 - config.slippage)
        proceeds = pos["shares"] * sell_price
        # 佣金有单笔下限（券商 5 元起收），印花税按成交额单独算、无下限。
        commission_fee = max(proceeds * config.commission, config.min_commission)
        fee = commission_fee + proceeds * config.stamp_tax
        trades.append(
            {
                "code": code,
                "entry_date": pos["entry_date"],
                "exit_date": when,
                "entry_price": pos["entry_price"],
                "exit_price": round(sell_price, 2),
                "shares": pos["shares"],
                # 实际持仓自然日数（原实现只把它当局部变量，流水里查不到；
                # 审计要核对"持有几天"必须先有这一列）
                "hold_days": (when - pos["entry_date"]).days,
                "pnl": round(proceeds - fee - pos["cost"], 2),
                "pnl_pct": round((proceeds - fee - pos["cost"]) / pos["cost"] * 100, 2),
                "reason": reason,
            }
        )
        return cash + proceeds - fee

    def _market_value(ts: pd.Timestamp) -> float:
        """持仓市值。当天没有 K 线的股票用**最后一条可用收盘价**估值。

        原实现对这种股票计 0，等于把停牌/退市期间的持仓价值凭空抹掉，
        资金曲线会出现虚假暴跌。
        """
        value = 0.0
        for code, pos in positions.items():
            bars = date_index[code]
            if ts in bars.index:
                value += pos["shares"] * float(bars.loc[ts, "close"])
            else:
                _, end_close = last_bar.get(code, (None, None))
                if end_close is not None:
                    value += pos["shares"] * end_close
        return value

    for day in trading_days:
        ts = pd.Timestamp(day)
        ts_val = np.datetime64(day)
        # ---------- 1. 检查持仓：止盈 / 止损 / 持仓天数 / 行情终结 ----------
        for code in list(positions.keys()):
            pos = positions[code]
            bars = date_index[code]
            if ts not in bars.index:
                # 当天没有行情：判断是"临时停牌"还是"行情彻底结束"
                end_ts, end_close = last_bar.get(code, (None, None))
                if end_ts is not None and end_close is not None and end_ts < ts:
                    # 退市或长期停牌：按最后一个可成交价强制平仓并归因。
                    # 不这样做的话，这个持仓会永远留在 positions 里 —— 估值时被计 0、
                    # 收尾时被静默丢弃，既不记交易也不把成本退回现金。
                    cash = _close(code, pos, end_ts.date(), end_close, "退市/停牌")
                    del positions[code]
                continue
            row = bars.loc[ts]
            close = float(row["close"])
            # 涨跌停对**买卖方向**的限制是相反的，原实现判反了（2026-10-28 修正）：
            # 跌停日卖不出去（挂单排不上），涨停日**可以**正常卖出（只是可能卖得便宜）；
            # 原代码写成 `close >= up: 禁止卖出`，于是"涨停拖到次日才止盈、跌停当天照常止损"，
            # 与 docstring 自己写的"跌停无法卖出"矛盾，且对收益方向是系统性偏差。
            if config.enable_price_limit:
                _up, down = _price_limit(code, bars_map[code], bars.index.get_loc(ts))
                if close <= down:
                    continue  # 跌停卖不出，明日再试
            # 止盈 / 止损
            hit_exit = close >= pos["target"]
            hit_stop = close <= pos["stop"]
            # 持仓天数超限（每笔仓位自己的上限：策略 hint 与 config 取小）
            hold_days = (day - pos["entry_date"]).days
            hit_max = hold_days >= pos.get("max_hold_days", config.max_hold_days)
            if hit_exit or hit_stop or hit_max:
                cash = _close(
                    code, pos, day, close,
                    "止盈" if hit_exit else ("止损" if hit_stop else "超时"),
                )
                del positions[code]

        # ---------- 2. 生成当日信号并买入 ----------
        # 用截至当天的数据计算因子（避免未来函数）
        # 每周选股：仅在新的一周首个交易日重新选股
        if config.rebalance_freq == "weekly":
            iso = day.isocalendar()
            week_key = (iso.year, iso.week)
            if week_key == last_rebalance_week:
                # 本周已选过，跳过买入（持仓仍按每日止盈止损管理）
                equity_curve[day] = cash + _market_value(ts)
                continue
            last_rebalance_week = week_key

        candidates = []
        for code in codes:
            dates = code_dates[code]
            p = code_ptr[code]
            # 单调推进指针：找到最后一个日期 <= 当天的行
            while p + 1 < len(dates) and dates[p + 1] <= ts_val:
                p += 1
            code_ptr[code] = p
            if p < 59:  # 至少 60 根 K 线历史
                continue
            # 当天必须**有行情**才能作为新候选：停牌与已退市的股票买不进。
            # 不加这一条的话，已退市股票的指针会停在最后一根 K 线上、带着过期数据
            # 一直参与打分并占住 top_n 名额 —— 买入环节虽然会跳过它们（当天无 K 线），
            # 但那天就少买了一只，等于被"僵尸候选"挤掉仓位。
            if dates[p] != ts_val:
                continue
            last = factor_index[code].iloc[p]
            if last["volume"] == 0:
                continue
            fund = fund_map.get(code, {})
            # 财务因子前视修正（2026-10-28）：`fundamentals` 表每个 code 只有 1 行、
            # 没有报告期，`updated_at` 只是抓取日（实测全表同一批）。若在快照日**之前**
            # 的交易日就用这份数据打分，等于提前看到了未来才公布的财务指标。所以
            # 快照日之前把财务因子一律中性化（空 dict → 各因子取中位 50）。
            if _fund_as_of is not None and ts.date() < _fund_as_of:
                fund = {}
            # 原始财务指标 → 三个分数（空 dict 得中性 50 分）。
            # 2026-10-28 修正：此前直接把原始 dict 交给 fundamental_composite，
            # 它读的是分数键、读不到就回退 50，导致财务因子在回测里恒为 50。
            _fund_scores = compute_fundamental_or_neutral(fund)
            # 近 20 日动量（供动量策略使用）
            mom_ret = 0.0
            if p >= 20:
                mom_ret = last["close"] / factor_index[code].iloc[p - 20]["close"] - 1
            # 近 20 日新高偏离（供突破策略使用）
            high20 = last.get("high20")
            if pd.notna(high20) and high20 > 0:
                high20_ratio = last["close"] / high20 - 1
            else:
                high20_ratio = 0.0
            candidates.append(
                {
                    "code": code,
                    "close": last["close"],
                    "atr14": last["atr14"],
                    "ma20": last["ma20"],
                    "ma60": last["ma60"],
                    "rsi14": last["rsi14"],
                    "vol_ratio": last["vol_ratio"],
                    "mom_ret": mom_ret,
                    "high20_ratio": high20_ratio,
                    # 以下三列原先只在「今日信号」路径里有，导致自定义策略的
                    # 流动性下限与 MACD 条件在**回测**中被静默跳过（不一致是双向的）。
                    # 现与 build_factor_rows 对齐，并由 tests/test_factor_parity.py 锁死。
                    "avg_amount20": float(last.get("avg_amount20"))
                    if pd.notna(last.get("avg_amount20"))
                    else float("nan"),
                    "macd": last["macd"],
                    "macd_signal": last["macd_signal"],
                    "value_score": _fund_scores["value_score"],
                    "growth_score": _fund_scores["growth_score"],
                    "quality_score": _fund_scores["quality_score"],
                    "trend_score": last["trend_score"],
                    "fund_score": fundamental_composite(_fund_scores),
                    **{
                        f"ma{_p}": float(last.get(f"ma{_p}"))
                        for _p in (5, 7, 10, 30)
                    },
                    # 均线收敛因子（2026-10-29）：整列已在 factor_index 里预计算，
                    # 这里按「当日行」取值，与 build_factor_rows 的末行口径一致；
                    # 无未来函数（rolling 只回看）。PVCF 依赖当日截面，下面统一合成。
                    **{
                        column: (float(last[column]) if pd.notna(last.get(column)) else float("nan"))
                        for column in CONV_RAW_COLUMNS
                    },
                    # 动量方向修正（2026-10-29）：MA_L/close，越小越强（研报口径）。
                    **{
                        column: (
                            float(last[column]) if pd.notna(last.get(column)) else float("nan")
                        )
                        for column in MA_RATIO_COLUMNS
                    },
                }
            )
        if candidates:
            df = pd.DataFrame(candidates)
            # 截面合成列：PCF、VCF 各自 z-score 后再相加（研报口径，见 compose_pvcf）
            df["conv_pvcf"] = compose_pvcf(df)
            if captured is not None:
                captured.append(df.copy(deep=True))
            df["score"] = strategy.score(df)
            df = df.sort_values("score", ascending=False)
            # 买入 Top N（跳过已持仓与涨停）
            for _, row in df.head(config.top_n).iterrows():
                code = row["code"]
                if code in positions:
                    continue
                bars = date_index[code]
                if ts not in bars.index:
                    continue
                # ⚠️ **同柱决策 + 同柱成交**（2026-10-15 审计发现，方向已实测）
                # ------------------------------------------------------------------
                # 这里用的是 `ts` 当天的收盘价，而候选打分用的因子行也是 `ts` 当天的
                # （`ma20`/`rsi14`/`trend_score` 都含 `ts` 的收盘）。也就是说：
                # **用当天收盘的信息，按当天收盘价成交** —— 现实中你不可能做到。
                #
                # 实盘路径（`signal/generator.py` + `simulator/paper.py`）是对的：
                # 盘前用**昨收**出信号、按**今日收盘**成交。因此回测与模拟盘/实盘的
                # 成交口径不同，**两者的收益不能直接对比**。
                #
                # 实测方向（路径无关，1008 笔真实交易）：
                #   次日开盘 / 当日收盘 − 1 的均值 = **−0.103%**（中位数 0.000%，
                #   为正比例仅 34.2%）→ 若改成"次日开盘成交"，买入价平均**更便宜**。
                # 因此当前的同柱成交约定对**买入价**是偏保守的，并没有虚高收益
                # （另外还叠加了 config.slippage 的滑点）。
                #
                # 尚未测量：**决策侧**的未来信息值多少（即"知道当日收盘"对选股有多大帮助）。
                # 要测需把因子行滞后一根（决策只用 ≤ T-1 的数据、成交用 T 的收盘），
                # 但这会改变选股本身，总收益对比又会落入路径噪声 —— 详见
                # `.github/plans/2026-10-15-4-audit-path-dependence-and-lookahead.md`。
                close = float(bars.loc[ts, "close"])
                if config.enable_price_limit:
                    up, _ = _price_limit(code, bars_map[code], bars.index.get_loc(ts))
                    if close >= up:
                        continue  # 涨停，买不进
                atr = row["atr14"]
                if pd.isna(atr) or atr <= 0:
                    continue
                entry = strategy.entry_price(row, atr)
                target, stop, hold_hint = strategy.exit_price(entry, atr)
                # 策略自带的期望持仓天数原先被直接丢弃（审计 #6），这里把它当**上限**用：
                # 与 config.max_hold_days 取小。config 仍是硬上限，所以内置策略
                # （突破 20 / 趋势 30 / 大师 90 天）在默认 config=20 下行为不变；
                # 只有"用户策略设 15 天、页面传 30 天"这种情形才会提前离场。
                if hold_hint and int(hold_hint) > 0:
                    pos_hold_days = min(config.max_hold_days, int(hold_hint))
                else:
                    pos_hold_days = config.max_hold_days
                # 仓位：等分**剩余现金**。注意这**不是**"固定 1/top_n 总资产"——首日会
                # 投到 65%~74%（top_n=5/2 实测），因为每买一只现金就少一份（审计 #7
                # 指出原注释"最多 1/5 仓位"与实际不符，这里按实际行为改写注释）。
                budget = cash / max(config.top_n, 1)
                equity_now = cash + _market_value(ts)
                # 大盘择时：大盘低于 MA20 时仓位减半
                if config.market_timing and market_close is not None and market_ma20 is not None:
                    if ts in market_ma20.index and not pd.isna(market_ma20.loc[ts]):
                        if market_close.loc[ts] < market_ma20.loc[ts]:
                            budget *= 0.5
                # 可选仓位约束（默认 None = 不启用，历史结果逐位不变）
                if config.max_position_pct is not None:
                    budget = min(budget, equity_now * config.max_position_pct)
                if config.max_total_position_pct is not None:
                    room = equity_now * config.max_total_position_pct - _market_value(ts)
                    budget = min(budget, max(room, 0.0))
                buy_price = entry * (1 + config.slippage)
                shares = int(budget / buy_price / 100) * 100  # 按 100 股一手取整
                # 可选成交量约束：最多吃掉当日成交量的 max_volume_pct（0.1 = 10%）
                if config.max_volume_pct is not None:
                    day_volume = float(bars.loc[ts, "volume"])
                    shares = min(shares, int(day_volume * config.max_volume_pct / 100) * 100)
                if shares <= 0:
                    continue
                cost = shares * buy_price
                # 佣金单笔下限（券商 5 元起收）；买入无印花税。
                fee = max(cost * config.commission, config.min_commission)
                if cost + fee > cash:
                    continue
                cash -= cost + fee
                positions[code] = {
                    "shares": shares,
                    # 记**实际成交价**（含滑点）。原实现买入记策略报价、卖出记含滑点
                    # 的成交价，一进一出口径不一致（审计 #9），逐笔盈亏看起来偏高。
                    "entry_price": round(buy_price, 2),
                    "entry_date": day,
                    "stop": stop,
                    "target": target,
                    "cost": cost + fee,
                    "max_hold_days": pos_hold_days,
                }

        # ---------- 3. 记录当日总资产 ----------
        equity_curve[day] = cash + _market_value(ts)

    # 收尾：强制平仓剩余持仓。
    # 注意不能只处理"最后一天有 K 线"的股票 —— 停牌/退市的股票在最后一天必然没有
    # K 线，原实现会把它们静默丢弃（不记交易、成本也不回现金）。
    last_day = trading_days[-1]
    ts_last = pd.Timestamp(last_day)
    for code, pos in list(positions.items()):
        bars = date_index[code]
        if ts_last in bars.index:
            cash = _close(code, pos, last_day, float(bars.loc[ts_last, "close"]),
                          "回测结束平仓")
        else:
            end_ts, end_close = last_bar.get(code, (None, None))
            if end_ts is None or end_close is None:
                continue  # 该股从来没有行情，理论上不会持仓到这里
            cash = _close(code, pos, end_ts.date(), end_close, "回测结束平仓（行情已终结）")
        del positions[code]

    equity = pd.Series(equity_curve).sort_index()
    # 统一为 DatetimeIndex，便于后续 resample
    equity.index = pd.to_datetime(equity.index)
    trades_df = pd.DataFrame(trades)
    return BacktestResult(equity_curve=equity, trades=trades_df, benchmark=benchmark)


# ---------------------------------------------------------------- 单元测试


def test_factor_table_layout_and_missing_amount() -> None:
    """_FactorTable 的列集、dtype、日期 int64 与缺 amount 时的 NaN 语义。"""
    bars = _make_bars(n=80, seed=21)
    table = _build_factor_table(bars)
    assert isinstance(table, _FactorTable)
    assert table.columns == _FACTOR_COLUMNS
    assert table.data.shape == (len(bars), len(_FACTOR_COLUMNS))
    assert table.data.dtype == np.float64
    assert table.data.flags.writeable is False, "因子矩阵必须只读（PreparedBacktest 复用约定）"
    assert table.dates.dtype == np.int64
    assert table.dates.flags.writeable is False
    expected_dates = pd.DatetimeIndex(bars["date"]).values.astype("datetime64[ns]").astype(np.int64)
    np.testing.assert_array_equal(table.dates, expected_dates)

    np.testing.assert_allclose(
        table.data[:, table.col_index["close"]],
        bars["close"].to_numpy(dtype=np.float64),
        rtol=0.0,
        atol=0.0,
    )
    expected_avg = pd.Series(bars["amount"]).rolling(20).mean().to_numpy(dtype=np.float64)
    np.testing.assert_allclose(
        table.data[:, table.col_index["avg_amount20"]],
        expected_avg,
        rtol=1e-12,
        atol=0.0,
        equal_nan=True,
    )
    expected_high20 = pd.Series(bars["close"]).rolling(20).max().to_numpy(dtype=np.float64)
    np.testing.assert_allclose(
        table.data[:, table.col_index["high20"]],
        expected_high20,
        rtol=1e-12,
        atol=0.0,
        equal_nan=True,
    )

    no_amount = _build_factor_table(_make_bars(n=80, seed=22, with_amount=False))
    assert no_amount.columns == _FACTOR_COLUMNS
    assert np.isnan(no_amount.data[:, no_amount.col_index["avg_amount20"]]).all()


# ---------------------------------------------------------------- 对拍测试


def test_empty_universe_matches_legacy() -> None:
    """无候选股票时，新旧实现都返回空结果（不进入因子构建）。"""
    stock_list, bars_map, fund_map = _distressed_universe()
    config = _daily_config(bars_map)
    new_result, legacy_result, _, _ = _run_both(
        None, config, stock_list, bars_map, fund_map, probe=_EmptyStrategy()
    )
    _assert_results_match(new_result, legacy_result, "空股票池")


def test_candidate_rows_match_legacy() -> None:
    """边界合成数据上，逐候选日逐列对拍新实现与旧参照实现。"""
    stock_list, bars_map, fund_map = _distressed_universe()
    config = _daily_config(bars_map)
    new_result, legacy_result, new_captured, legacy_captured = _run_both(
        None, config, stock_list, bars_map, fund_map, probe=_AllCodesStrategy(), capture=True
    )
    assert new_captured, "探针应至少产生一个候选日"
    _assert_candidate_frames_match(new_captured, legacy_captured)
    _assert_results_match(new_result, legacy_result, "边界合成数据")


@pytest.mark.parametrize(
    "config_kind",
    ["weekly_timing_caps", "fundamental_as_of"],
    ids=["weekly_择时_仓位上限", "财务快照中性化"],
)
def test_boundary_configs_match_legacy(config_kind: str) -> None:
    """周频 + 择时 + 仓位约束 / 财务快照日 两条分支都与旧实现逐值一致。"""
    stock_list, bars_map, fund_map = _distressed_universe()
    if config_kind == "weekly_timing_caps":
        start, end = _window_bounds(bars_map)
        config = BacktestConfig(
            start=start,
            end=end,
            top_n=3,
            max_hold_days=10,
            rebalance_freq="weekly",
            market_timing=True,
            max_position_pct=0.25,
            max_total_position_pct=0.6,
            max_volume_pct=0.3,
        )
    else:
        as_of = bars_map["600000"]["date"].iloc[100].date()
        config = _daily_config(bars_map, fundamental_as_of=as_of)
    new_result, legacy_result, new_captured, legacy_captured = _run_both(
        None, config, stock_list, bars_map, fund_map, probe=_AllCodesStrategy(), capture=True
    )
    assert new_captured, "探针应至少产生一个候选日"
    _assert_candidate_frames_match(new_captured, legacy_captured)
    _assert_results_match(new_result, legacy_result, config_kind)


@pytest.mark.parametrize("strategy_name", STRATEGY_NAMES, ids=STRATEGY_NAMES)
def test_builtin_strategies_match_legacy(strategy_name: str) -> None:
    """7 个内置策略在合成数据上与旧实现逐值一致（含候选表）。"""
    stock_list, bars_map, fund_map = _distressed_universe()
    config = _daily_config(bars_map)
    new_result, legacy_result, new_captured, legacy_captured = _run_both(
        strategy_name, config, stock_list, bars_map, fund_map, capture=True
    )
    _assert_candidate_frames_match(new_captured, legacy_captured)
    _assert_results_match(new_result, legacy_result, strategy_name)


# ---------------------------------------------------------------- 真实数据入口


@pytest.fixture(scope="module")
def real_market():
    """真实行情小窗口（12 只 × 2024 全年）；本机无缓存时跳过（不阻塞 CI/部署环境）。"""
    try:
        from stock_plan.data.storage import Storage

        storage = Storage()
        start = date(2024, 1, 2)
        end = date(2024, 12, 31)
        stock_list, bars_map, fund_map = storage.load_market_maps(
            start=start, end=end, lead_days=180, max_codes=12
        )
    except Exception as exc:  # noqa: BLE001 - 数据缓存缺失属于环境问题，跳过而非失败
        pytest.skip(f"本地真实行情缓存不可用，跳过真实数据对拍：{exc!r}")
    if not bars_map:
        pytest.skip("本地真实行情为空，跳过真实数据对拍")
    return stock_list, bars_map, fund_map, start, end


@pytest.mark.parametrize("strategy_name", STRATEGY_NAMES, ids=STRATEGY_NAMES)
def test_real_market_small_window_matches_legacy(real_market, strategy_name: str) -> None:
    """真实数据（12 只 × 2024 全年）上，7 个内置策略新旧实现逐值一致。"""
    stock_list, bars_map, fund_map, start, end = real_market
    config = BacktestConfig(start=start, end=end, top_n=3, max_hold_days=10)
    new_result, legacy_result, new_captured, legacy_captured = _run_both(
        strategy_name, config, stock_list, bars_map, fund_map, capture=True
    )
    _assert_candidate_frames_match(new_captured, legacy_captured)
    _assert_results_match(new_result, legacy_result, f"真实数据/{strategy_name}")


# ---------------------------------------------------------------- Phase 15：准备层复用


def _prepared_universe() -> tuple[
    pd.DataFrame, dict[str, pd.DataFrame], dict[str, dict], BacktestConfig, PreparedBacktest
]:
    """合成边界股票池 + 整段行情一次 prepare 的只读快照。"""
    stock_list, bars_map, fund_map = _distressed_universe()
    config = _daily_config(bars_map)
    prepared = prepare_backtest(bars_map, fund_map, stock_list, config)
    return stock_list, bars_map, fund_map, config, prepared


def test_prepare_backtest_readonly_and_shallow_copy() -> None:
    """PreparedBacktest 冻结、容器只读且为浅拷贝：外部增删 key 不影响已制备快照。"""
    stock_list, bars_map, fund_map, config, prepared = _prepared_universe()

    assert isinstance(prepared, PreparedBacktest)
    assert prepared.config is config
    assert type(prepared.bars_map).__name__ == "mappingproxy"
    assert type(prepared.fund_map).__name__ == "mappingproxy"
    assert type(prepared.factor_tables).__name__ == "mappingproxy"
    # 浅拷贝：容器是新的，内部元素不复制（同一 DataFrame / dict / _FactorTable 对象）。
    assert prepared.bars_map is not bars_map
    assert prepared.bars_map["600000"] is bars_map["600000"]
    assert prepared.fund_map["600000"] is fund_map["600000"]
    assert isinstance(prepared.factor_tables["600000"], _FactorTable)
    assert prepared.factor_tables["600000"].data.flags.writeable is False
    assert set(prepared.factor_tables) == set(bars_map)

    # 外部 dict 增删 key 不影响已制备快照（构造时已浅拷贝容器）。
    trimmed_bars = dict(bars_map)
    trimmed_bars.pop("600011")
    trimmed_prepared = prepare_backtest(trimmed_bars, fund_map, stock_list, config)
    assert "600011" not in trimmed_prepared.bars_map
    assert "600011" in prepared.bars_map

    with pytest.raises(TypeError):
        prepared.bars_map["600000"] = bars_map["600000"]
    with pytest.raises(TypeError):
        prepared.fund_map["600000"] = {}
    with pytest.raises(TypeError):
        prepared.factor_tables["600000"] = prepared.factor_tables["600000"]
    with pytest.raises(FrozenInstanceError):
        prepared.config = config


@pytest.mark.parametrize("strategy_name", STRATEGY_NAMES, ids=STRATEGY_NAMES)
def test_prepared_reuse_matches_plain(strategy_name: str) -> None:
    """7 个内置策略：prepared 复用与每次重建因子表的结果逐值一致。"""
    stock_list, bars_map, fund_map, config, prepared = _prepared_universe()
    plain_result = run_backtest(
        create_strategy(strategy_name), config, bars_map, fund_map, stock_list
    )
    prepared_result = run_backtest(
        create_strategy(strategy_name), config, None, None, None, prepared=prepared
    )
    _assert_results_match(plain_result, prepared_result, f"prepared/{strategy_name}")


def test_prepared_ignores_caller_data_snapshot() -> None:
    """传 prepared 时内部以 prepared 的数据快照为准，忽略调用方传入的 bars/fund/stock_list。"""
    stock_list, bars_map, fund_map, config, prepared = _prepared_universe()
    expected = run_backtest(create_strategy("趋势跟随策略"), config, bars_map, fund_map, stock_list)
    overridden = run_backtest(
        create_strategy("趋势跟随策略"),
        config,
        {},  # 故意传空行情
        {},  # 故意传空财务
        pd.DataFrame({"code": [], "name": [], "is_st": [], "board": []}),
        prepared=prepared,
    )
    _assert_results_match(expected, overridden, "prepared 覆盖调用方数据")


def test_prepared_reuse_works_for_sub_window() -> None:
    """因子表按整段行情预计算，子窗口回测（walk-forward 场景）仍与不复用逐值一致。"""
    stock_list, bars_map, fund_map, config, prepared = _prepared_universe()
    dates = bars_map["600000"]["date"]
    sub_config = replace(
        config,
        start=dates.iloc[90].date(),
        end=dates.iloc[140].date(),
        rebalance_freq="weekly",
        market_timing=True,
    )
    plain_result = run_backtest(
        create_strategy("趋势跟随策略"), sub_config, bars_map, fund_map, stock_list
    )
    prepared_result = run_backtest(
        create_strategy("趋势跟随策略"), sub_config, None, None, None, prepared=prepared
    )
    _assert_results_match(plain_result, prepared_result, "prepared 子窗口")


def test_prepared_missing_code_is_lazily_built_without_mutation() -> None:
    """prepared 缺某 code 时惰性补建，结果不变且不修改 prepared 本身。"""
    stock_list, bars_map, fund_map, config, prepared = _prepared_universe()
    tables = {code: table for code, table in prepared.factor_tables.items() if code != "600000"}
    trimmed = PreparedBacktest(
        factor_tables=tables,
        stock_list=prepared.stock_list,
        fund_map=prepared.fund_map,
        bars_map=prepared.bars_map,
        config=prepared.config,
    )
    assert "600000" not in trimmed.factor_tables

    expected = run_backtest(
        create_strategy("趋势跟随策略"), config, bars_map, fund_map, stock_list
    )
    result = run_backtest(
        create_strategy("趋势跟随策略"), config, None, None, None, prepared=trimmed
    )
    _assert_results_match(expected, result, "惰性补建缺 code")

    # 惰性补建只写入本次调用内部的字典，不得回写 prepared 的只读视图。
    assert "600000" not in trimmed.factor_tables
    assert len(trimmed.factor_tables) == len(tables)

