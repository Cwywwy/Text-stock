# -*- coding: utf-8 -*-
"""回测引擎模块 — 用历史数据验证策略表现。

核心逻辑（逐日模拟）：
1. 每天收盘后，用截至当天的数据对候选股票打分，选出 Top N 买入
2. 每天检查持仓：达到止盈 / 止损 / 最大持仓天数 → 卖出
3. 记录每笔交易与每日资金曲线

交易规则（A 股）：
- T+1：买入后次日才能卖出
- 手续费：买入万三，卖出万三 + 印花税千一
- 滑点：成交价按千一偏移（买入加价、卖出减价）
- 涨跌停：涨停无法买入、跌停无法卖出（简化判断）

Revision History:
    2026-10-29  (phase 15) add PreparedBacktest and prepare_backtest; run_backtest reuses prepared factor tables
    2026-10-29  (phase 14) flatten per-code factor frames into numpy factor tables; drop per-row Series lookups
    2026-10-29  (phase 7) inject convergence factor columns (conv_pcf/vcf/acf/trcf/pvcf) into candidates
    2026-10-28  (phase 1) expose equal-weight benchmark on BacktestResult
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType

import numpy as np
import pandas as pd

from stock_plan.factors.convergence import add_conv_columns, compose_pvcf
from stock_plan.factors.momentum import add_ma_ratio_columns
from stock_plan.factors.technical import compute_technical
from stock_plan.factors.fundamental import compute_fundamental_or_neutral
from stock_plan.factors.timing import exposure_series, validate_timing_tier
from stock_plan.strategy.base import Strategy
from stock_plan.strategy.scorer import fundamental_composite


@dataclass
class BacktestConfig:
    """回测配置。"""

    start: date                 # 回测开始日期
    end: date                   # 回测结束日期
    initial_cash: float = 100_000  # 初始资金
    commission: float = 0.0003     # 手续费（万三，买卖都收）
    stamp_tax: float = 0.001       # 印花税（千一，仅卖出）
    slippage: float = 0.001        # 滑点（千一）
    top_n: int = 5                 # 每天最多买入几只
    max_hold_days: int = 20        # 最大持仓天数（上限；策略自带 hold_days 更短时以策略为准）
    rebalance_freq: str = "daily"  # 选股频率："daily" 每天 / "weekly" 每周
    market_timing: bool = False    # 弱择时：大盘低于 MA20 时，仅把**新买入预算**减半（旧行为）
    # 三档择时（Phase 19，默认关闭）：off | dual_ma。
    # dual_ma = 按双均线规则真实校准总仓位（100%/50%/0%，超额部分当日卖出），
    # 与 market_timing 同时开启时以本字段为准（弱择时逻辑不再执行）。
    timing_tier: str = "off"
    enable_price_limit: bool = True  # 是否启用涨跌停限制
    # —— 仓位约束（默认全关＝与历史行为一致；打开才生效，见 run_backtest 内注释）——
    # 单票占总资产上限。默认 None = 只用 budget=cash/top_n 的等分逻辑（首日实际投入会
    # 高于 1/top_n，因为后面几只买入时前面几只的现金已经花掉了，见审计 #7）。
    max_position_pct: float | None = None
    # 总仓位（持仓市值/总资产）上限，None = 不限制（原实现允许满仓甚至超过）。
    max_total_position_pct: float | None = None
    # 单日成交量占比上限（占当日 volume 的百分比），None = 不限制。
    # 原实现可以一次买 1.2e12 股，等于假设市场无限承接。
    max_volume_pct: float | None = None
    # 单笔佣金下限（元）。券商普遍 5 元起收：小额单按万三算只有几毛钱，
    # 不设下限会把成本算少一个数量级（买 1 万元：万三=3 元 < 5 元）。
    min_commission: float = 5.0
    # 财务指标快照的抓取日期。给定后，快照日之前的交易日财务因子中性化，
    # 消除"用今天抓到的财务数据回测去年行情"的前视偏差（详见 run_backtest 内注释）。
    fundamental_as_of: date | None = None


@dataclass
class BacktestResult:
    """回测结果。"""

    equity_curve: pd.Series   # 资金曲线（日期索引）
    trades: pd.DataFrame      # 每笔交易明细
    metrics: dict = field(default_factory=dict)  # 绩效指标
    # 基准净值（同期等权持有全部候选股，首日归一为 1.0）。旧缓存/旧调用方没有这个
    # 字段时为 None，展示层需判空降级（见 .github/plans/2026-10-28-1 第 7 节）。
    benchmark: pd.Series | None = None


def equal_weight_benchmark(
    codes: list[str],
    bars_map: dict[str, pd.DataFrame],
    start,
    end,
) -> pd.Series | None:
    """构造回测基准：同期等权持有**全部候选股**、不再选股也不择时。

    用途是回答「策略到底有没有超额收益」——策略曲线与这条基准的差就是 alpha。
    复用 factors.regime.equal_weight_index（等权收益累乘，不受高价股与成分变动影响），
    无新增数据源。无可用数据时返回 None。

    参数：
        codes: 候选股票代码列表（策略硬过滤后的池子）。
        bars_map: {code: 日线 DataFrame}。
        start/end: 回测区间。
    """
    if not codes:
        return None
    from stock_plan.factors.regime import MIN_CONTRIBUTORS, equal_weight_index

    window = {c: bars_map[c] for c in codes if c in bars_map and bars_map[c] is not None}
    if not window:
        return None
    index = equal_weight_index(
        window,
        start=start,
        end=end,
        min_contributors=max(1, min(MIN_CONTRIBUTORS, len(window))),
    )
    if index is None or len(index) < 2:
        return None
    return index


def _price_limit(code: str, bars: pd.DataFrame, idx: int) -> tuple[float, float]:
    """计算某日涨跌停价（简化：主板 ±10%，创业板/科创板 ±20%，北交所 ±30%）。

    参数：
        code: 股票代码（用于判断板块）。
        bars: 该股票日线。
        idx:  当日索引位置。

    返回：
        (涨停价, 跌停价)。
    """
    prev_close = bars["close"].iloc[idx - 1] if idx > 0 else bars["close"].iloc[idx]
    if code.startswith(("300", "301", "688")):
        limit = 0.20
    elif code.startswith(("4", "8", "9")):
        limit = 0.30
    else:
        limit = 0.10
    return round(prev_close * (1 + limit), 2), round(prev_close * (1 - limit), 2)


# 候选因子表列（Phase 14）：主循环只读这些列，顺序与下面的整数列号常量绑定。
# `avg_amount20` 只在行情有 amount 列时有意义；缺列时统一补一整列 NaN（语义等价于
# 原实现 `last.get("avg_amount20")` 取不到列时回落到 float("nan")）。
_FACTOR_COLUMNS: tuple[str, ...] = (
    "volume",
    "close",
    "atr14",
    "ma20",
    "ma60",
    "rsi14",
    "vol_ratio",
    "high20",
    "avg_amount20",
    "macd",
    "macd_signal",
    "trend_score",
    "ma5",
    "ma7",
    "ma10",
    "ma30",
    "conv_pcf",
    "conv_vcf",
    "conv_acf",
    "conv_trcf",
    "ma_ratio_20",
    "ma_ratio_60",
    "ma_ratio_120",
    "ma_ratio_240",
)
# 列号常量：主循环（5379 只 × 1184 个交易日）里避免每次候选都做 dict 查列名。
(
    _C_VOLUME,
    _C_CLOSE,
    _C_ATR14,
    _C_MA20,
    _C_MA60,
    _C_RSI14,
    _C_VOL_RATIO,
    _C_HIGH20,
    _C_AVG_AMT20,
    _C_MACD,
    _C_MACD_SIGNAL,
    _C_TREND,
    _C_MA5,
    _C_MA7,
    _C_MA10,
    _C_MA30,
    _C_CONV_PCF,
    _C_CONV_VCF,
    _C_CONV_ACF,
    _C_CONV_TRCF,
    _C_RATIO20,
    _C_RATIO60,
    _C_RATIO120,
    _C_RATIO240,
) = range(len(_FACTOR_COLUMNS))


class _FactorTable:
    """单只股票的候选因子表：列名元组 + float64 矩阵 + 日期 int64 数组。

    为什么需要（Phase 14 性能改造）：原实现每个交易日对每只股票做一次
    `factor_index[code].iloc[p]` 生成一行 Series，再按列名逐项取值 —— 全市场 5 年
    约 640 万次 Series 构造，固定开销远大于取值本身（cProfile 中
    `Series.__getitem__` 与 `DataFrame.__getitem__` 合计占单次回测约一半）。
    这里把整列预转成 numpy 矩阵，主循环只做「矩阵行 view + 列号」取值；
    构建路径、列顺序与改造前逐函数复用完全一致，因此数值逐值相同。

    只读约定：构造后不再修改，可被同一次回测内的多个 loop 迭代共享，也可被
    Phase 15 的 PreparedBacktest 跨策略复用（R4：调用方不得原地改写）。
    """

    __slots__ = ("columns", "col_index", "data", "dates")

    def __init__(self, columns: tuple[str, ...], data: np.ndarray, dates: np.ndarray):
        self.columns = tuple(columns)
        self.col_index = {name: i for i, name in enumerate(self.columns)}
        self.data = data
        self.dates = dates


def _build_factor_table(bars: pd.DataFrame) -> _FactorTable:
    """为单只股票构建候选因子表（与改造前的 pandas 构建步骤逐函数一致）。

    ⚠️ 构建顺序必须与改造前相同：先技术因子，再 high20 / avg_amount20 / 四组均线，
    最后收敛因子与 MA_L/close。滚动窗口的 min_periods 语义依赖 pandas 默认值，
    任何一步改写都会移动 NaN 的位置。

    参数：
        bars: 单只股票日线（含 date/open/high/low/close/volume/amount/turnover）。

    返回：
        _FactorTable（只读）。
    """
    f = compute_technical(bars).set_index("date")
    # 近 20 日最高收盘价（供突破策略使用，rolling 窗口止于当日，无未来函数）
    f["high20"] = f["close"].rolling(20).max()
    # 近 20 日平均成交额（供流动性筛选使用）。必须用 **rolling** 而不是
    # `bars["amount"].tail(20)` —— 后者取的是整条序列末尾，在逐日回测里就是未来数据。
    if "amount" in f.columns:
        f["avg_amount20"] = f["amount"].rolling(20).mean()
    else:
        # 缺可选列：补 NaN 列，保持所有股票列集一致（取值语义与 last.get(...) 回落 nan 相同）
        f["avg_amount20"] = np.nan
    # 自定义均线列（供自定义策略均线自由组合使用）
    for _p in (5, 7, 10, 30):
        f[f"ma{_p}"] = f["close"].rolling(_p).mean()
    # 均线收敛因子（2026-10-29）：滚动计算天然无未来函数，整列预计算、按日取行。
    # PVCF 依赖「当日截面」，只能在候选集拼成 DataFrame 后算（见下方 compose_pvcf）。
    f = add_conv_columns(f, bars)
    # 动量方向修正（2026-10-29）：MA_L/close 四列，同样整列滚动预计算。
    f = add_ma_ratio_columns(f)
    data = f[list(_FACTOR_COLUMNS)].to_numpy(dtype=np.float64)
    # 日期转 int64 纳秒：指针查找用整数比较，比 datetime64 更快，且口径与 ts.value 一致。
    dates = f.index.values.astype("datetime64[ns]").astype(np.int64)
    # 只读：防止后续代码（含 prepared 复用方）原地改写，破坏逐值对拍
    data.setflags(write=False)
    dates.setflags(write=False)
    return _FactorTable(_FACTOR_COLUMNS, data, dates)


@dataclass(frozen=True)
class PreparedBacktest:
    """回测准备层：一次算好的只读数据快照，供同一批行情上多次 run_backtest 复用。

    Phase 15 性能目标：7 策略 × 1 年对比时，因子表构建（全市场约 50~70s）只做一次，
    各策略只跑主循环；UI 对比页 / walk-forward / 今日信号路径共用同一份快照。

    只读约定（R4）：
        - 构造时浅拷贝三个容器（bars_map/fund_map/factor_tables），并用
          MappingProxyType 包成只读视图，避免调用方后续增删 key 影响已制备结果。
        - 容器内的 DataFrame / _FactorTable / dict **不深拷贝**，调用方不得原地改写；
          `_FactorTable.data` 已是 write=False 的 numpy 数组。

    不缓存的内容：
        - `trading_days`、`benchmark`、候选池（`filter_universe` 结果）都依赖策略与
          回测区间，每次 run_backtest 按传入的 config 与策略重新计算，数值语义与
          不传 prepared 时完全一致。
        - `config` 只是制备时的配置快照，便于 UI 侧校验/展示；run_backtest 仍以
          调用时传入的 config 为准（窗口可不同，因子表按整段行情预计算）。

    字段：
        factor_tables: {code: _FactorTable}，prepare_backtest 一次算好。
        stock_list:    制表时的股票列表快照（用于 filter_universe）。
        fund_map:      制表时的财务快照。
        bars_map:      制表时的行情快照（run_backtest 传 prepared 时以此为准）。
        config:        制备时的回测配置快照。
    """

    factor_tables: Mapping[str, _FactorTable]
    stock_list: pd.DataFrame
    fund_map: Mapping[str, dict]
    bars_map: Mapping[str, pd.DataFrame]
    config: BacktestConfig

    def __post_init__(self) -> None:
        # 浅拷贝容器 + 只读视图：外部 dict 增删 key 不影响本对象（R4）
        object.__setattr__(self, "factor_tables", MappingProxyType(dict(self.factor_tables)))
        object.__setattr__(self, "fund_map", MappingProxyType(dict(self.fund_map)))
        object.__setattr__(self, "bars_map", MappingProxyType(dict(self.bars_map)))


def prepare_backtest(
    bars_map: Mapping[str, pd.DataFrame],
    fund_map: Mapping[str, dict],
    stock_list: pd.DataFrame,
    config: BacktestConfig,
) -> PreparedBacktest:
    """一次算好全部 code 的因子表，返回只读快照供多次 run_backtest 复用。

    参数：
        bars_map:  {code: 日线 DataFrame}（建议传整段回测区间，各窗口复用）。
        fund_map:  {code: 财务指标 dict}。
        stock_list: 全 A 股列表（用于策略 filter_universe）。
        config:    制备时的回测配置（仅作快照/校验；run_backtest 以调用时 config 为准）。

    返回：
        PreparedBacktest（只读）。

    注意：
        - 本函数不跑策略、不截断候选池，因此不做 filter_universe —— 因子表对同一
          code 是整段行情上的滚动结果，与回测窗口无关；run_backtest 仍会按
          config.start 截断后再做策略过滤。
        - 缺 K 线/None 的 code 直接跳过（不写入 factor_tables），run_backtest 遇到
          候选池里的缺失 code 会惰性补建（仅影响性能，不影响结果）。
    """
    factor_tables = {
        code: _build_factor_table(bars)
        for code, bars in bars_map.items()
        if bars is not None
    }
    return PreparedBacktest(
        factor_tables=factor_tables,
        stock_list=stock_list,
        fund_map=fund_map,
        bars_map=bars_map,
        config=config,
    )


def run_backtest(
    strategy: Strategy,
    config: BacktestConfig,
    bars_map: dict[str, pd.DataFrame],
    fund_map: dict[str, dict],
    stock_list: pd.DataFrame,
    prepared: PreparedBacktest | None = None,
) -> BacktestResult:
    """执行回测。

    参数：
        strategy:  策略实例。
        config:    回测配置。
        bars_map:  {code: 日线 DataFrame}。
        fund_map:  {code: 财务指标 dict}。
        stock_list: 全 A 股列表（用于硬过滤）。
        prepared:  可选。Phase 15 准备层快照（prepare_backtest 的返回值）。提供时：
                   - 内部**以 prepared 的 bars_map/fund_map/stock_list 为准**，忽略
                     本次调用传进来的这三个位置参数，避免调用方混用不同数据快照
                     造成静默错值；
                   - 复用 prepared.factor_tables 中已有的因子表，缺某 code 时惰性
                     补建（不修改 prepared 本身）；
                   - codes / trading_days / benchmark / 候选池仍按策略与 config
                     重新计算，数值语义与不传 prepared 时完全一致。
                   不传时行为与历史版本完全相同（向后兼容）。

    返回：
        BacktestResult（资金曲线 + 交易明细 + 指标）。
    """
    # Phase 15：prepared 提供时以它的数据快照为准（只读视图，不复制容器）。
    _prepared_tables: Mapping[str, _FactorTable] | None = None
    if prepared is not None:
        bars_map = prepared.bars_map  # type: ignore[assignment]
        fund_map = prepared.fund_map  # type: ignore[assignment]
        stock_list = prepared.stock_list
        _prepared_tables = prepared.factor_tables

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
    # 相比逐日对每只股票重新 compute_technical，可大幅提升回测速度。
    # Phase 14：因子帧压平成 numpy 矩阵（见 _FactorTable），主循环不再逐行构造 Series；
    # 列顺序、取值语义与改造前的 factor_index 逐值相同。
    # Phase 15：prepared 提供时复用已有因子表（缺 code 惰性补建，不修改 prepared）。
    factor_tables: dict[str, _FactorTable] = {}
    for code in codes:
        _table = _prepared_tables.get(code) if _prepared_tables is not None else None
        factor_tables[code] = _table if _table is not None else _build_factor_table(bars_map[code])
    # 主循环的 (code, table) 配对：避免每个交易日对 5379 只股票重复做 dict 查找。
    code_items = tuple((code, factor_tables[code]) for code in codes)
    # 每只股票的日期数组（int64 纳秒），配合指针做单调递增查找
    code_ptr = {code: -1 for code in codes}

    # 财务分数缓存（Phase 14）：同一 code 的财务 dict 恒定，原实现里每个候选都调用
    # 一次 compute_fundamental_or_neutral，属于纯重复计算。这里预计算「正常快照」与
    # 「快照日之前中性化」两种取值；候选循环里只做一次 dict/元组取值。
    fund_scores_normal: dict[str, tuple[float, float, float, float]] = {}
    for code in codes:
        _scores = compute_fundamental_or_neutral(fund_map.get(code, {}))
        fund_scores_normal[code] = (
            _scores["value_score"],
            _scores["growth_score"],
            _scores["quality_score"],
            fundamental_composite(_scores),
        )
    _neutral_scores = compute_fundamental_or_neutral({})
    fund_scores_neutral = (
        _neutral_scores["value_score"],
        _neutral_scores["growth_score"],
        _neutral_scores["quality_score"],
        fundamental_composite(_neutral_scores),
    )

    # 大盘代理：**等权收益指数**（用于大盘择时）
    # 原实现是 closes.mean(axis=1) —— 对原始股价直接取平均，有两个方法学问题：
    #   1. 被高价股主导（茅台 1300 元与 3 元股在均值里权重相同），实际等价于"高价股指数"；
    #   2. 某只股票缺当日 K 线时会从均值里消失，指数水平**跳空** —— 于是
    #      "大盘跌破 MA20"可能由成分变动而非行情触发，仓位被莫名减半。
    # 改为先算每只股票的日收益率、等权平均后累乘，与价格水平和成分变动都无关。
    market_close: pd.Series | None = None
    market_ma20: pd.Series | None = None
    # 三档择时（Phase 19）：默认 off —— 不构造任何额外数据、不改任何分支，
    # 因此 21 arm 逐值对比与 HEAD 必须 0 差异（见 Phase 19 完成标准）。
    validate_timing_tier(config.timing_tier)
    use_tier_timing = config.timing_tier != "off"
    # 弱择时与三档互斥：同时开启时以三档为准，避免两种仓位逻辑叠加出难以解释的结果。
    use_weak_timing = bool(config.market_timing) and not use_tier_timing
    timing_exposure: dict[pd.Timestamp, float] | None = None
    if use_weak_timing or use_tier_timing:
        from stock_plan.factors.regime import MIN_CONTRIBUTORS, equal_weight_index

        # 候选池小于阈值时按候选数放宽，避免"股票太少 → 指数恒为 1.0 →
        # 大盘择时被静默禁用"这种看不出来的行为
        market_close = equal_weight_index(
            {code: bars_map[code] for code in codes},
            min_contributors=max(1, min(MIN_CONTRIBUTORS, len(codes))),
        )
        market_ma20 = market_close.rolling(20).mean() if len(market_close) else None
        if use_tier_timing and market_close is not None and not market_close.empty:
            # 档位「次日生效」已在 exposure_series 内部 shift(1)；
            # 这里再对齐到实际交易日并前向填充，保证每天都有确定的暴露比例。
            _exposure = exposure_series(market_close)
            _ts_index = pd.DatetimeIndex([pd.Timestamp(d) for d in trading_days])
            timing_exposure = (
                _exposure.reindex(_ts_index, method="ffill").fillna(1.0).to_dict()
            )

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

    def _close(
        code: str, pos: dict, when, close: float, reason: str, shares: int | None = None,
    ) -> float:
        """按给定价格平仓（或部分减仓）并记录交易，返回平仓后的现金。

        ``shares`` 为 None 时整笔平仓；给定小于持仓的股数时按比例拆分成本、
        更新剩余持仓（Phase 19 三档择时的「按市值比例降仓」复用它，
        否则只能整只卖出，降仓粒度会粗到"要么全留要么全卖"）。
        """
        sold = int(pos["shares"] if shares is None else shares)
        sell_price = close * (1 - config.slippage)
        proceeds = sold * sell_price
        # 佣金有单笔下限（券商 5 元起收），印花税按成交额单独算、无下限。
        commission_fee = max(proceeds * config.commission, config.min_commission)
        fee = commission_fee + proceeds * config.stamp_tax
        # 部分卖出时按股数比例拆分原始成本，保证逐笔盈亏可加总回总盈亏。
        cost_basis = pos["cost"] * sold / pos["shares"] if pos["shares"] else pos["cost"]
        trades.append(
            {
                "code": code,
                "entry_date": pos["entry_date"],
                "exit_date": when,
                "entry_price": pos["entry_price"],
                "exit_price": round(sell_price, 2),
                "shares": sold,
                # 实际持仓自然日数（原实现只把它当局部变量，流水里查不到；
                # 审计要核对"持有几天"必须先有这一列）
                "hold_days": (when - pos["entry_date"]).days,
                "pnl": round(proceeds - fee - cost_basis, 2),
                "pnl_pct": round(
                    ((proceeds - fee - cost_basis) / cost_basis * 100) if cost_basis else 0.0, 2
                ),
                "reason": reason,
            }
        )
        if sold < pos["shares"]:
            pos["shares"] -= sold
            pos["cost"] -= cost_basis
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

    def _reduce_to_cap(day, ts: pd.Timestamp, cap: float) -> None:
        """把持仓总市值压到 ``cap`` 以内：按每只持仓「市值 × cap/总市值」的比例减仓。

        Phase 19（三档择时）专用。相比「整只卖出」，按比例减仓更接近真实的
        "总仓位从 100% 降到 50%"，也不会因为只剩 2~3 只持仓就一次清光某只票。
        约束与引擎其余卖出逻辑一致：100 股整手、不足 100 股不卖、T+1（当日买入）
        不参与、跌停/停牌跳过（次日继续尝试；跳过的持仓会让当日实际仓位高于目标，
        报告里按"未能降仓"披露）。
        """
        nonlocal cash
        mv = _market_value(ts)
        if mv <= 0 or mv <= cap + 1e-6:
            return
        ratio = max(cap, 0.0) / mv
        for code in list(positions.keys()):
            pos = positions[code]
            if pos["entry_date"] == day:  # T+1：当日买入当日不能卖
                continue
            bars = date_index[code]
            if ts not in bars.index:  # 停牌：卖不出
                continue
            close = float(bars.loc[ts, "close"])
            if config.enable_price_limit:
                _up, down = _price_limit(code, bars_map[code], bars.index.get_loc(ts))
                if close <= down:  # 跌停：卖不出
                    continue
            if pos["shares"] < 100:  # 不足一手，不卖
                continue
            pos_value = pos["shares"] * close
            excess_value = pos_value - pos_value * ratio
            lots = int(excess_value / close / 100) * 100
            if lots <= 0:
                continue
            cash = _close(code, pos, day, close, "择时降仓", shares=min(lots, pos["shares"]))
            if pos["shares"] <= 0:
                del positions[code]

    for day in trading_days:
        ts = pd.Timestamp(day)
        # 当日在因子表里的表示：int64 纳秒（与 _FactorTable.dates 同口径），
        # 指针比较用整数比 datetime64 快；与改造前的 np.datetime64(day) 等价。
        ts_int = ts.value
        # 三档择时的目标仓位（exposure_series 内部已 shift(1)，即次日生效）；
        # timing_tier="off" 时为 None，恒按 1.0 处理，不触发任何新分支。
        exposure_today = float(timing_exposure.get(ts, 1.0)) if timing_exposure else 1.0
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

        # ---------- 1.5 择时降仓（三档，100% → 50% / 0%）----------
        # 只在档位低于满仓时触发，且先降仓再买新股：保证当日买入不会把仓位顶回超配。
        if timing_exposure is not None and exposure_today < 1.0:
            _reduce_to_cap(day, ts, (cash + _market_value(ts)) * exposure_today)

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
        # 财务前视修正按天判一次（等价于原实现每个候选都做的 ts.date() < _fund_as_of）
        _use_neutral_fund = _fund_as_of is not None and day < _fund_as_of
        for code, table in code_items:
            dates = table.dates
            p = code_ptr[code]
            # 单调推进指针：找到最后一个日期 <= 当天的行
            while p + 1 < len(dates) and dates[p + 1] <= ts_int:
                p += 1
            code_ptr[code] = p
            if p < 59:  # 至少 60 根 K 线历史
                continue
            # 当天必须**有行情**才能作为新候选：停牌与已退市的股票买不进。
            # 不加这一条的话，已退市股票的指针会停在最后一根 K 线上、带着过期数据
            # 一直参与打分并占住 top_n 名额 —— 买入环节虽然会跳过它们（当天无 K 线），
            # 但那天就少买了一只，等于被"僵尸候选"挤掉仓位。
            if dates[p] != ts_int:
                continue
            data = table.data
            row = data[p]
            if row[_C_VOLUME] == 0:
                continue
            if _use_neutral_fund:
                value_score, growth_score, quality_score, fund_score = fund_scores_neutral
            else:
                value_score, growth_score, quality_score, fund_score = fund_scores_normal[code]
            # 近 20 日动量（供动量策略使用）
            mom_ret = 0.0
            if p >= 20:
                mom_ret = row[_C_CLOSE] / data[p - 20, _C_CLOSE] - 1
            # 近 20 日新高偏离（供突破策略使用）
            high20 = row[_C_HIGH20]
            if not np.isnan(high20) and high20 > 0:
                high20_ratio = row[_C_CLOSE] / high20 - 1
            else:
                high20_ratio = 0.0
            candidates.append(
                {
                    "code": code,
                    "close": row[_C_CLOSE],
                    "atr14": row[_C_ATR14],
                    "ma20": row[_C_MA20],
                    "ma60": row[_C_MA60],
                    "rsi14": row[_C_RSI14],
                    "vol_ratio": row[_C_VOL_RATIO],
                    "mom_ret": mom_ret,
                    "high20_ratio": high20_ratio,
                    # 以下三列原先只在「今日信号」路径里有，导致自定义策略的
                    # 流动性下限与 MACD 条件在**回测**中被静默跳过（不一致是双向的）。
                    # 现与 build_factor_rows 对齐，并由 tests/test_factor_parity.py 锁死。
                    # avg_amount20：缺 amount 列时表中该列为 NaN，float(nan) 与原实现
                    # `last.get(...) is NaN → float("nan")` 逐值相同。
                    "avg_amount20": float(row[_C_AVG_AMT20]),
                    "macd": row[_C_MACD],
                    "macd_signal": row[_C_MACD_SIGNAL],
                    "value_score": value_score,
                    "growth_score": growth_score,
                    "quality_score": quality_score,
                    "trend_score": row[_C_TREND],
                    "fund_score": fund_score,
                    "ma5": float(row[_C_MA5]),
                    "ma7": float(row[_C_MA7]),
                    "ma10": float(row[_C_MA10]),
                    "ma30": float(row[_C_MA30]),
                    # 均线收敛因子（2026-10-29）：整列已在因子表里预计算，这里按「当日行」
                    # 取值，与 build_factor_rows 的末行口径一致；无未来函数（rolling 只回看）。
                    "conv_pcf": float(row[_C_CONV_PCF]),
                    "conv_vcf": float(row[_C_CONV_VCF]),
                    "conv_acf": float(row[_C_CONV_ACF]),
                    "conv_trcf": float(row[_C_CONV_TRCF]),
                    # 动量方向修正（2026-10-29）：MA_L/close，越小越强（研报口径）。
                    "ma_ratio_20": float(row[_C_RATIO20]),
                    "ma_ratio_60": float(row[_C_RATIO60]),
                    "ma_ratio_120": float(row[_C_RATIO120]),
                    "ma_ratio_240": float(row[_C_RATIO240]),
                }
            )
        if candidates:
            df = pd.DataFrame(candidates)
            # 截面合成列：PCF、VCF 各自 z-score 后再相加（研报口径，见 compose_pvcf）
            df["conv_pvcf"] = compose_pvcf(df)
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
                # 三档择时：新买入还受「持仓总市值不超过目标仓位」封顶（不追买，只封顶）。
                # 当日若因跌停/停牌没能降到位，room 会被压成 0，自然不开新仓。
                if timing_exposure is not None:
                    budget = min(
                        budget, max(equity_now * exposure_today - _market_value(ts), 0.0)
                    )
                # 弱择时（旧行为）：大盘低于 MA20 时**只把新买入预算**减半。
                # 与三档同时开启时已被 use_weak_timing 置为不执行（三档优先）。
                if use_weak_timing and market_close is not None and market_ma20 is not None:
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


if __name__ == "__main__":
    # 简单自测：用已缓存数据跑一段回测
    from datetime import timedelta

    from stock_plan.data.storage import Storage
    from stock_plan.strategy.builtin import TrendFollowingStrategy

    storage = Storage()
    stock_list = storage.load_stock_list()
    bars_map = {}
    for code in stock_list["code"].astype(str).tolist():
        if storage.cache_exists(code):
            bars_map[code] = storage.load_bars(code)
    fund_map = {}
    for code in bars_map:
        fin = storage.load_fundamentals(code)
        if fin:
            fund_map[code] = fin

    end = date.today()
    start = end - timedelta(days=365)
    config = BacktestConfig(start=start, end=end)
    strat = TrendFollowingStrategy()
    result = run_backtest(strat, config, bars_map, fund_map, stock_list)
    print("交易日:", len(result.equity_curve), "交易笔数:", len(result.trades))
    if len(result.trades):
        print(result.trades.head())