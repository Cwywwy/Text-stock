"""回测引擎既有缺陷的回归测试（2026-10-28）。

覆盖审计里 5 条**会改变历史回测结果**的缺陷，每条都对应一个"改回坏版本必定失败"的断言：

1. 涨跌停方向判反：跌停日卖不出、涨停日可以卖（原代码反着判）。
2. 缺少单笔最低佣金：小额单按万三算只有几毛，真实券商 5 元起收。
3. 候选池用到结束日数据：同一起始日、只改结束日，候选池就变了（前视）。
4. 财务因子前视：`fundamentals` 只有一份当前快照，快照日之前必须中性化。
5. Walk-Forward 丢配置：只透传 5 个字段，把 initial_cash/佣金等全部重置回默认。

这些测试都是**行为级**的：只断言结果，不依赖内部实现细节，因此不会因为重构而误报。
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stock_plan.backtest.engine import BacktestConfig, run_backtest
from stock_plan.backtest.walkforward import walk_forward
from stock_plan.strategy.base import Strategy


# ---------------- 构造素材 ----------------

class _FixedStrategy(Strategy):
    """固定打分策略：所有候选都 80 分，方便只观察成交规则本身。"""

    name = "缺陷回归测试策略"
    params = {"atr_k_entry": 1.0, "atr_m_exit": 3.0, "atr_n_stop": 1.5, "hold_days": 10}

    def filter_universe(self, stock_list, bars_map):
        return [c for c in stock_list["code"].astype(str) if c in bars_map]

    def score(self, df_factors):
        return pd.Series([80.0] * len(df_factors), index=df_factors.index)

    def entry_price(self, row, atr):
        return round(row["close"] + 1.0 * atr, 2)

    def exit_price(self, entry, atr):
        return round(entry + 3.0 * atr, 2), round(entry - 1.5 * atr, 2), int(self.params["hold_days"])


class _LongHoldStrategy(_FixedStrategy):
    """期望持仓 200 天：让"涨跌停能否卖出""佣金下限"这类测试只产生 1 笔完整买卖。

    2026-10-28 起引擎会采用策略自带的 hold_days（审计 #6），默认的 10 天会让
    这些测试在 200 天窗口里反复换仓，所以显式拉长。
    """

    params = {**_FixedStrategy.params, "hold_days": 200}


def _bars(closes: list[float], start: str = "2025-01-01") -> pd.DataFrame:
    """按给定收盘价序列构造日线。

    开高低都贴着收盘价，只为让 ATR 有值；引擎要求每只股票至少 60 根 K 线
    才允许买入，所以序列长度都在 120 根以上。
    """
    dates = pd.bdate_range(start, periods=len(closes))
    rows = []
    prev = closes[0]
    for d, c in zip(dates, closes):
        rows.append(
            {
                "date": d,
                "open": round(prev, 2),
                "high": round(max(c, prev) * 1.002, 2),
                "low": round(min(c, prev) * 0.998, 2),
                "close": round(float(c), 2),
                "volume": 1_000_000.0,
                "amount": 10_000_000.0,
                "turnover": 0.01,
            }
        )
        prev = c
    return pd.DataFrame(rows)


def _stock_list(codes: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "code": codes,
            "name": [f"测试{i}" for i in range(len(codes))],
            "industry": "测试行业",
            "is_st": 0,
        }
    )


def _run(bars_map, config, strategy=None):
    return run_backtest(
        strategy or _FixedStrategy(),
        config,
        bars_map,
        {},
        _stock_list(sorted(bars_map)),
    )


# ---------------- 1. 涨跌停方向 ----------------

def test_跌停日卖不出_顺延到次日():
    """跌停当天挂单排不上队（卖不出），止损必须顺延到下一个交易日。

    坏版本（`if close >= up: continue`）在跌停日**照常成交**，此断言失败。
    """
    closes = [10.0] * 120 + [9.0, 9.0, 9.0, 9.0]
    bars = {"000001": _bars(closes)}
    cfg = BacktestConfig(start=bars["000001"]["date"].iloc[0].date(),
                         end=bars["000001"]["date"].iloc[-1].date(), top_n=1,
                         max_hold_days=200)
    res = _run(bars, cfg, _LongHoldStrategy())

    trades = res.trades
    assert len(trades) >= 1
    trade = trades.iloc[0]
    limit_down_day = bars["000001"]["date"].iloc[120].date()
    # 跌停那天不能成交，必须等到次日
    assert trade["exit_date"] > limit_down_day, (
        f"跌停日 {limit_down_day} 竟然成交了：exit_date={trade['exit_date']}"
    )
    assert trade["exit_date"] == bars["000001"]["date"].iloc[121].date()


def test_涨停日可以卖出_止盈不被拖延():
    """涨停日现实中**可以**卖出（只是可能卖低一点），止盈不该被拖到次日。

    坏版本在涨停日 `continue`，止盈被推迟一天，此断言失败。
    """
    closes = [10.0] * 120 + [11.0, 11.0, 11.0]
    bars = {"000001": _bars(closes)}
    cfg = BacktestConfig(start=bars["000001"]["date"].iloc[0].date(),
                         end=bars["000001"]["date"].iloc[-1].date(), top_n=1,
                         max_hold_days=200)
    res = _run(bars, cfg, _LongHoldStrategy())

    trades = res.trades
    assert len(trades) >= 1
    trade = trades.iloc[0]
    limit_up_day = bars["000001"]["date"].iloc[120].date()
    assert trade["exit_date"] == limit_up_day, (
        f"涨停日 {limit_up_day} 没卖出，被拖到了 {trade['exit_date']}"
    )
    assert trade["reason"] == "止盈"


# ---------------- 2. 单笔最低佣金 ----------------

def test_单笔佣金有下限_小额单每边多收五元():
    """佣金设为 0、只留 5 元下限：一次完整买卖应恰好比不设下限少 10 元。

    坏版本没有下限（fee = 成交额 × 佣金率），两档结果完全一样，此断言失败。
    """
    closes = [10.0] * 140
    bars = {"000001": _bars(closes)}
    first = bars["000001"]["date"].iloc[0].date()
    last = bars["000001"]["date"].iloc[-1].date()

    def pnl_with_floor(floor: float) -> float:
        cfg = BacktestConfig(
            start=first, end=last, top_n=1, max_hold_days=200,
            commission=0.0, stamp_tax=0.0, slippage=0.0, min_commission=floor,
        )
        res = _run(bars, cfg, _LongHoldStrategy())
        assert len(res.trades) == 1, "口径前置条件：本次场景应只有 1 笔完整买卖"
        # 用成交的 pnl（已扣买卖两边费用）；资金曲线最后一点是"收尾平仓"之前记的，
        # 不含卖出费用，不能用来做这个比较。
        return float(res.trades.iloc[0]["pnl"])

    no_floor = pnl_with_floor(0.0)
    with_floor = pnl_with_floor(5.0)
    # 买入 5 元 + 卖出 5 元 = 10 元
    assert no_floor - with_floor == pytest.approx(10.0, abs=0.01)


# ---------------- 3. 候选池不得用到结束日数据 ----------------

class _PoolSpy(_FixedStrategy):
    """记录每次 filter_universe 实际收到的候选池，并按"传进来最后一根收盘价"过滤。

    这个过滤器本身就是**旧实现的受害者**：若引擎没把行情截到回测开始日，
    它就会用结束日附近的价格做判断 —— 正是前视的典型形态。
    """

    pools: list[tuple[str, ...]] = []

    def filter_universe(self, stock_list, bars_map):
        codes = [c for c in stock_list["code"].astype(str) if c in bars_map]
        codes = [c for c in codes if float(bars_map[c]["close"].iloc[-1]) > 10.0]
        _PoolSpy.pools.append(tuple(sorted(codes)))
        return codes


def test_候选池与结束日无关():
    """同一起始日、只改结束日，候选池必须完全一致。

    坏版本：候选池由"传进来的整段行情"决定，后面的上涨会让 000002 在长窗口里
    突然进池（实测可复现），两池不等，此断言失败。
    """
    dates = pd.bdate_range("2025-01-01", periods=80)
    flat = [10.0] * 40 + [20.0] * 40  # 000002 在 40 个交易日之后才涨上去
    bars = {
        "000001": _bars([12.0] * 80),          # 一直 > 10，两种窗口都在池
        "000002": _bars(flat),                 # 只有"看未来"才会进池
    }
    start = dates[0].date()
    _PoolSpy.pools.clear()
    _run(bars, BacktestConfig(start=start, end=dates[10].date(), top_n=1), _PoolSpy())
    _run(bars, BacktestConfig(start=start, end=dates[-1].date(), top_n=1), _PoolSpy())

    short_pool, long_pool = _PoolSpy.pools
    assert short_pool == long_pool, f"候选池被结束日影响了：{short_pool} vs {long_pool}"
    assert "000002" not in long_pool, "候选池用到了开始日之后的涨幅（前视）"


# ---------------- 4. 财务因子前视 ----------------

class _FundSpy(_FixedStrategy):
    """记录每次打分时拿到的财务综合分，用于验证快照日之前是否被中性化。"""

    seen: list[dict] = []

    def score(self, df_factors):
        _FundSpy.seen.append(
            {str(r.code): round(float(r.fund_score), 2) for r in df_factors.itertuples()}
        )
        return pd.Series([80.0] * len(df_factors), index=df_factors.index)


def test_财务快照日之前因子中性化():
    """快照日之后的交易日才能用财务分；之前必须是中性 50。

    坏版本没有 `fundamental_as_of` 逻辑，第一天就会带上高分，此断言失败。
    """
    bars = {"000001": _bars([10.0] * 130)}
    stock_list = _stock_list(["000001"])
    fund_map = {"000001": {"value_score": 90.0, "growth_score": 95.0, "quality_score": 85.0}}
    first = bars["000001"]["date"].iloc[0].date()
    last = bars["000001"]["date"].iloc[-1].date()

    _FundSpy.seen.clear()
    cfg_after = BacktestConfig(
        start=first, end=last, top_n=1,
        fundamental_as_of=last + pd.Timedelta(days=1).to_pytimedelta(),
    )
    run_backtest(_FundSpy(), cfg_after, bars, fund_map, stock_list)
    # 快照日晚于所有交易日 → 全程中性
    assert all(v == pytest.approx(50.0) for day in _FundSpy.seen for v in day.values())

    _FundSpy.seen.clear()
    cfg_before = BacktestConfig(
        start=first, end=last, top_n=1,
        fundamental_as_of=first - pd.Timedelta(days=1).to_pytimedelta(),
    )
    run_backtest(_FundSpy(), cfg_before, bars, fund_map, stock_list)
    # 快照日早于所有交易日 → 全程使用真实（非中性）财务分
    assert any(v != pytest.approx(50.0) for day in _FundSpy.seen for v in day.values())


# ---------------- 5. Walk-Forward 必须透传配置 ----------------

def test_walkforward_透传资金与成本配置(monkeypatch):
    """Walk-Forward 各窗口的配置必须继承 base_config（资金/成本等），不能重置成默认值。

    坏版本手工构造 BacktestConfig 只传 5 个字段，initial_cash 掉回 10 万、
    佣金/印花税/滑点回到默认 —— 实测 oos 起点从 100 万变成 99,917，此断言失败。
    """
    captured: list[BacktestConfig] = []

    def fake_run_backtest(strategy, cfg, bars_map, fund_map, stock_list, prepared=None):
        from stock_plan.backtest.engine import BacktestResult

        captured.append(cfg)
        idx = pd.to_datetime(["2025-01-02", "2025-01-03"])
        eq = pd.Series([cfg.initial_cash, cfg.initial_cash], index=idx)
        return BacktestResult(equity_curve=eq, trades=pd.DataFrame())

    monkeypatch.setattr(
        "stock_plan.backtest.walkforward.run_backtest", fake_run_backtest
    )

    bars = {"000001": _bars([10.0] * 60)}
    base = BacktestConfig(
        start=date(2025, 1, 1), end=date(2025, 6, 30),
        initial_cash=1_000_000.0, commission=0.00012, stamp_tax=0.0005,
        slippage=0.002, min_commission=0.0,
    )
    walk_forward(
        _FixedStrategy, bars, {}, _stock_list(["000001"]),
        start=date(2025, 1, 1), end=date(2025, 6, 30),
        train_days=30, test_days=30,
        param_grid=[{"hold_days": 5}],
        base_config=base,
    )

    assert captured, "walk_forward 没有调用 run_backtest"
    for cfg in captured:
        assert cfg.initial_cash == pytest.approx(1_000_000.0)
        assert cfg.commission == pytest.approx(0.00012)
        assert cfg.stamp_tax == pytest.approx(0.0005)
        assert cfg.slippage == pytest.approx(0.002)
        assert cfg.min_commission == pytest.approx(0.0)


# ---------------- 6~10. 审计剩余项（2026-10-28 第二轮） ----------------
#
# 这几条原本被列为「只影响口径、不影响结论」，实测下来至少 #6（持仓天数）会
# 改变单个策略的历史结果，所以同样配了行为级断言，全部默认关闭时零影响。

class _HoldSpy(_FixedStrategy):
    """目标价/止损价都远离现价：只会因「超时」离场，专门验证持仓天数口径。"""

    params = {**_FixedStrategy.params, "hold_days": 10}

    def exit_price(self, entry, atr):
        return round(entry * 2.0, 2), round(entry * 0.5, 2), int(self.params["hold_days"])


def _wavy_bars(n: int = 130, base: float = 10.0) -> pd.DataFrame:
    """小幅震荡、无趋势：既不触发止盈也不触发止损。"""
    return _bars([base + (i % 3) * 0.02 for i in range(n)])


def test_策略自带持仓天数被采用_而不是一律用config上限():
    """策略 exit_price 返回的期望持仓天数必须生效（审计 #6 说它被直接丢弃）。

    坏版本只认 config.max_hold_days（这里设 200 天），持仓会一路拖到 200 天，
    断言 max(hold_days) <= 12 立即失败。
    """
    bars_map = {"000001": _wavy_bars()}
    cfg = BacktestConfig(
        start=date(2025, 1, 1), end=date(2025, 12, 31),
        initial_cash=100_000.0, top_n=1, max_hold_days=200, min_commission=0.0,
    )
    res = _run(bars_map, cfg, _HoldSpy())
    assert not res.trades.empty, "没有成交，测试素材失效"
    assert (res.trades["reason"] == "超时").any()
    # 10 个自然日 ≤ 12（跨一个周末）；坏版本是 200 天
    assert res.trades["hold_days"].max() <= 12


def test_单票仓位上限_默认None时不受影响_开启后每笔不超总资产比例():
    """`max_position_pct` 是新加的可选约束，默认 None 必须与历史行为一致。"""
    bars_map = {"000001": _wavy_bars(), "000002": _wavy_bars(), "000003": _wavy_bars()}
    base = dict(
        start=date(2025, 1, 1), end=date(2025, 12, 31),
        initial_cash=100_000.0, top_n=3, max_hold_days=10, min_commission=0.0,
    )
    # 默认 None：等分现金（每只 ~3.3 万），远超 5%
    unlimited = _run(bars_map, BacktestConfig(**base), _HoldSpy())
    assert (unlimited.trades["shares"] * unlimited.trades["entry_price"]).max() > 10_000

    capped = _run(bars_map, BacktestConfig(**base, max_position_pct=0.05), _HoldSpy())
    cost_per_trade = capped.trades["shares"] * capped.trades["entry_price"]
    assert cost_per_trade.max() <= 100_000 * 0.05 * 1.01


def test_总仓位上限_开启后同日建仓不超过总资产比例():
    """`max_total_position_pct` 限制"持仓市值/总资产"。坏版本没有这道闸，首日会买满 5 只。"""
    codes = [f"00000{i}" for i in range(1, 6)]
    bars_map = {c: _wavy_bars() for c in codes}
    cfg = BacktestConfig(
        start=date(2025, 1, 1), end=date(2025, 12, 31),
        initial_cash=100_000.0, top_n=5, max_hold_days=10,
        min_commission=0.0, max_total_position_pct=0.2,
    )
    res = _run(bars_map, cfg, _HoldSpy())
    assert not res.trades.empty
    first_day = res.trades["entry_date"].min()
    first_round = res.trades[res.trades["entry_date"] == first_day]
    spent = (first_round["shares"] * first_round["entry_price"]).sum()
    assert spent <= 100_000 * 0.2 * 1.02
    # 20% 总仓 + 每笔等分现金 => 首日只买得起 1 只
    assert len(first_round) == 1


def test_单日成交量占比上限_限制可买股数():
    """`max_volume_pct` 防"假设市场无限承接"。合成日线 volume=100 万股，限 0.01% => 100 股。"""
    bars_map = {"000001": _wavy_bars()}
    cfg = BacktestConfig(
        start=date(2025, 1, 1), end=date(2025, 12, 31),
        initial_cash=100_000.0, top_n=1, max_hold_days=10,
        min_commission=0.0, max_volume_pct=0.0001,
    )
    res = _run(bars_map, cfg, _HoldSpy())
    assert not res.trades.empty
    assert res.trades["shares"].max() == 100


def test_买卖两端滑点口径对称_买入价记实际成交价():
    """审计 #9：原实现买入记策略报价、卖出记含滑点成交价，逐笔盈亏偏高。

    现在两端都记实际成交价：买入价 = 报价×(1+滑点)、卖出价 = 收盘×(1-滑点)。
    同一份行情、同一策略，滑点 0.2% 的买入价必须是滑点 0 的 1.002 倍。
    """
    bars_map = {"000001": _wavy_bars()}
    base = dict(
        start=date(2025, 1, 1), end=date(2025, 12, 31),
        initial_cash=100_000.0, top_n=1, max_hold_days=10, min_commission=0.0,
    )
    no_slip = _run(bars_map, BacktestConfig(**base, slippage=0.0), _HoldSpy())
    with_slip = _run(bars_map, BacktestConfig(**base, slippage=0.002), _HoldSpy())
    assert len(no_slip.trades) == len(with_slip.trades), "滑点不该改变成交笔数（本素材只超时离场）"
    ratio = with_slip.trades["entry_price"].iloc[0] / no_slip.trades["entry_price"].iloc[0]
    # 容差必须收紧到 5e-4：最初写 rel=2e-3 时 1.000 也落在区间里，
    # 反向验证实测"改回记策略报价"仍通过（空转守卫），收紧后才有判别力。
    assert ratio == pytest.approx(1.002, abs=5e-4)
    # 两端都吃滑点，总盈亏必然更差
    assert with_slip.trades["pnl"].sum() < no_slip.trades["pnl"].sum()

