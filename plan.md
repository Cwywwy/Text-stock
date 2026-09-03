# 盘前选股软件 — 项目计划书

> 版本：v0.3 含 Phase Tasklist
> 作者：AI 辅助 + 用户决策
> 最后更新：2026-09-03

---

## 1. 项目目标

构建一个**本地运行的盘前选股系统**，覆盖 A 股市场，支持：
1. 在每日开盘前（建议 9:25 前）基于历史数据与技术、基本面、消息/题材、集合竞价三维因子，**精选 3–5 只**候选标的。
2. 对每只标的给出**目标买入价、目标卖出价、止损位、期望持仓周期**（技术位为主 + ATR 风险控制）。
3. 提供**可视化自定义策略** + **Python 代码编写** + **LLM 辅助生成**三种策略开发方式。
4. 内置**回测引擎**（多策略对比 + Walk-Forward 滚动验证）支持先回测验证、再生成实盘信号。
5. **模拟交易**模块贴近实盘规则（T+1、涨跌停、手续费、滑点）。
6. **LLM/Agent** 承担三件事：消息面情感分析、策略代码生成与修改、信号解释与历史复盘。
7. 通过**本地 Streamlit Web 仪表板**查看结果，不做外部推送、不做实盘下单。

---

## 2. 核心决策（已确认）

| 维度 | 决策 |
|------|------|
| 交易市场 | 仅 A 股（沪深主板/创业板/科创板/北交所） |
| 策略类型 | 技术面 + 基本面 + 消息/题材（三维融合） |
| 输出数量 | 精选 3–5 只 |
| 买卖价 | 技术位为主 + ATR 风险控制（止损止盈位 + 期望持仓周期） |
| 数据源 | AKShare + 东方财富 + 新浪财经（免费组合） |
| 运行形态 | 本地 Streamlit Web 仪表板 |
| 通知方式 | 仅在仪表板查看，不推送 |
| 自定义策略 | 可视化拼装 + Python 代码 + LLM 辅助 |
| 回测范围 | 多策略对比 + Walk-Forward 滚动验证 |
| LLM 角色 | MVP 暂不接入；V1 再加：消息面分析 + 策略生成 + 解释复盘 |
| 用户水平 | Python 入门，需要详细注释与引导 |
| 时间投入 | 先出 MVP（2–3 天能跑起来的最小闭环） |
| 运行环境 | 本地 Windows |
| 月度预算 | ≤ 50 元 |
| 持仓周期 | 多周期并存（短线/中短线/中线，用户自选） |
| 功能范围 | 选股信号 + 模拟交易（不做实盘下单、不做仓位管理） |
| 回测数据 | 近 5 年日线 |
| 存储方案 | SQLite（策略/回测/模拟交易元数据） + Parquet（行情） |
| 运行方式 | 手动触发（仪表板按钮） |
| 仪表板模块 | 盘前信号 + 回测结果 + 策略管理 + 模拟交易（全要） |
| 回测展示 | 资金曲线 + 交易明细 + 月度热力图 + 行业/风格归因 + 持仓周期分布 + 回撤时段标注 |
| 新闻源 | AKShare（财新/东方财富/同花顺） |
| 模拟交易 | 贴近实盘（T+1、涨跌停、手续费、滑点） |
| 包管理 | uv |

---

## 3. AI 推荐项（待你确认）

### 3.1 LLM 模型推荐

按"中文友好 + 性价比 + Function Calling 支持"排序：

| 方案 | 模型 | 价格（输入/输出 元/百万 token） | 备注 |
|------|------|--------|------|
| **推荐 1** | 智谱 GLM-4-Flash | 0 (免费) → 极便宜 | 速度极快，中文优秀，**完全免费档够用**，内置 Function Calling |
| **推荐 2** | DeepSeek-V3 | 2 / 8 | 推理强，代码能力优，适合策略生成 |
| 备选 | 阿里通义 Qwen2.5-Max | 20 / 60 | 中文 SOTA 之一 |
| 备选 | OpenAI GPT-4o-mini | 约 15 / 60 | 需科学上网 |

**MVP 推荐**：智谱 GLM-4-Flash（免费档足够；后续可平滑升级到 GLM-4-Plus 做复杂任务）。

注册地址：https://bigmodel.cn/ ，注册即送免费额度。

### 3.2 存储方案详细推荐

```
data/
├── raw/                    # 原始数据（CSV，AKShare 落盘）
│   ├── daily/             # 日线行情
│   ├── fundamental/       # 财务数据
│   └── news/              # 新闻/公告
├── processed/             # 清洗后数据（Parquet，列式压缩）
│   ├── price_5y.parquet
│   └── factor_panel.parquet
└── db/
    └── strategy.db        # SQLite（策略、回测结果、模拟交易记录）
```

---

## 4. 系统架构

```
┌─────────────────────────────────────────────────┐
│          Streamlit Web 仪表板（前端）            │
│  ┌──────┬──────┬──────┬──────┐                  │
│  │盘前  │回测  │策略  │模拟  │                  │
│  │信号  │结果  │管理  │交易  │                  │
│  └──┬───┴──┬───┴──┬───┴──┬───┘                  │
└─────┼──────┼──────┼──────┼───────────────────────┘
      │      │      │      │
┌─────▼──────▼──────▼──────▼───────────────────────┐
│           业务逻辑层（Python）                   │
│  ┌────────┬────────┬─────────┬──────────┐        │
│  │信号    │回测    │策略     │LLM       │        │
│  │引擎    │引擎    │引擎     │Agent     │        │
│  └───┬────┴───┬────┴────┬────┴────┬─────┘        │
└──────┼────────┼─────────┼─────────┼──────────────┘
       │        │         │         │
┌──────▼────────▼─────────▼─────────▼──────────────┐
│             数据层                              │
│  ┌─────────┬───────────┬────────────────────┐   │
│  │ AKShare │ 免费财经  │ SQLite + Parquet  │   │
│  │ 行情/财务│ 新闻/公告 │  本地持久化        │   │
│  └─────────┴───────────┴────────────────────┘   │
└────────────────────────────────────────────────┘
```

---

## 5. 功能清单

### 5.1 MVP（2–3 天最小闭环）

- [ ] **数据获取**：AKShare 拉取全 A 股近 5 年日线行情 + 最新财务数据
- [ ] **因子计算**：均线、MACD、RSI、量比、换手率、ATR 等技术因子；ROE、营收增速等基本面因子
- [ ] **硬过滤**：ST/退市股剔除、停牌剔除、流动性过滤（近 20 日均成交额 < 阈值剔除）
- [ ] **打分模型**：技术 60% + 基本面 40%（题材因子在 V1 接入 LLM 后再加，MVP 用占位）
- [ ] **信号输出**：Top 5 标的 + 买入价（昨收 - k·ATR）+ 卖出价（买入 + m·ATR）+ 止损（买入 - n·ATR）
- [ ] **回测引擎**（最小可用）：单策略、含 T+1/涨跌停/手续费/滑点、资金曲线 + 胜率 + 最大回撤
- [ ] **Streamlit 仪表板**：今日信号页 + 回测结果页
- [ ] **uv 环境**：一键启动

> **MVP 范围确认**：LLM 不进入 MVP，调试完核心闭环后再接入；题材因子在 MVP 用占位（默认 0 分），V1 接入 LLM 后再激活。

### 5.2 V1 增强（1–2 周）

- [ ] 多策略对比回测 + Walk-Forward 滚动验证
- [ ] 详细回测报告：交易明细表 + 月度热力图 + 行业归因 + 持仓周期分布 + 回撤时段
- [ ] 模拟交易模块（贴近实盘）
- [ ] LLM Agent 集成：消息面分析 + 策略代码生成 + 信号解释
- [ ] 策略管理：可视化拼装 + Python 编辑器

### 5.3 V2 远期

- [ ] LLM 自动复盘与参数优化建议
- [ ] 新闻舆情时间线
- [ ] 同策略多周期并存输出
- [ ] 飞书/微信推送（可选）

---

## 6. 技术栈

| 层级 | 选型 |
|------|------|
| 包管理 | uv |
| Web 框架 | Streamlit |
| 数据 | AKShare, Pandas, NumPy |
| 存储 | SQLite (内置 sqlite3) + PyArrow (Parquet) |
| 图表 | Plotly（Streamlit 原生支持） |
| 回测 | 自研（基于 Pandas 向量化回测，比 backtrader 轻量、便于自定义） |
| LLM | 智谱 GLM-4-Flash（推荐）+ zhipuai SDK |
| Agent | LangChain 或直接手写 ReAct（入门推荐手写，理解更清晰） |
| 技术指标 | pandas-ta 或自研（pandas-ta 封装好，开箱即用） |

---

## 7. 项目结构（计划）

```
stock-plan/
├── pyproject.toml          # uv 项目配置
├── .python-version         # Python 版本（3.13）
├── README.md               # 启动文档
├── data/
│   ├── raw/
│   ├── processed/
│   └── db/
├── src/stock_plan/         # 主包（uv src layout）
│   ├── data/               # 数据获取与缓存
│   │   ├── fetcher.py      # AKShare 封装
│   │   └── storage.py      # Parquet/SQLite 持久化
│   ├── factors/            # 因子计算
│   │   ├── technical.py
│   │   └── fundamental.py
│   ├── strategy/           # 策略引擎
│   │   ├── base.py         # 策略基类
│   │   ├── builtin.py      # 内置策略
│   │   └── scorer.py       # 打分模型
│   ├── backtest/           # 回测引擎
│   │   ├── engine.py
│   │   ├── metrics.py
│   │   └── report.py
│   ├── signal/             # 盘前信号生成
│   │   └── generator.py
│   ├── simulator/          # 模拟交易
│   │   └── paper.py
│   ├── llm/                # LLM Agent（V1 接入）
│   │   ├── client.py       # 智谱 SDK 封装
│   │   ├── news_analyzer.py
│   │   └── strategy_writer.py
│   └── ui/                 # Streamlit 仪表板
│       ├── app.py
│       └── pages/
│           ├── today.py
│           ├── backtest.py
│           ├── strategy_mgr.py
│           └── paper.py
└── tests/
    └── test_backtest.py
```

---

## 8. 开发节奏

| 阶段 | 时间 | 任务 |
|------|------|------|
| **D1** | 晚上 | uv 环境 + 数据获取 + 因子计算 + 基础回测引擎 |
| **D2** | 晚上 | 打分模型 + 信号生成 + Streamlit 仪表板骨架（今日信号页 + 回测页） |
| **D3** | 晚上 | LLM 集成（消息面 + 解释）+ 策略管理 + 模拟交易 |
| **V1+** | 持续 | Walk-Forward、可视化拼装、详细归因报告 |

---

## 9. 待确认事项（已确认）

1. **LLM 模型**：✅ 先不接入，等 MVP 跑通后再加（接入时再确认具体模型）
2. **MVP 范围**：✅ 5.1 节全部 8 项（LLM 已移出，实际 7 项）
3. **回测引擎**：✅ 自研（基于 Pandas 向量化）
4. **首次运行**：✅ 先搭建骨架，再逐模块编码

---

## 10. 风险与对策

| 风险 | 对策 |
|------|------|
| AKShare 接口变动/限速 | 抽象 fetcher 接口，便于切换；本地缓存原始数据 |
| 免费数据源字段不全 | 选股因子设计时优先用稳态字段（量价、财务三表）；不依赖的资金流因子作为可选 |
| LLM 不稳定 | 关键逻辑不依赖 LLM，LLM 仅用于消息面打分和解释；输出做 schema 校验 |
| 策略过拟合 | 默认开启 Walk-Forward 验证；提供样本外测试视图 |
| T+1/涨跌停规则处理错 | 回测引擎单独写测试用例覆盖 |

---

## 11. 模块接口设计（骨架详细化）

### 11.1 数据层 `src/data/`

```python
# fetcher.py
class DataFetcher:
    def get_stock_list(self) -> pd.DataFrame:
        """获取全 A 股代码列表。返回字段：code, name, industry, list_date, is_st"""

    def get_daily_bars(self, code: str, start: date, end: date) -> pd.DataFrame:
        """获取单只股票日线行情。返回字段：date, open, high, low, close, volume, amount"""

    def get_fundamentals(self, code: str) -> pd.DataFrame:
        """获取最新财务数据。返回字段：roe, revenue_growth, pe_ttm, pb, market_cap"""

    def get_news(self, code: str, days: int = 7) -> list[dict]:
        """获取近期新闻/公告。返回 [{date, title, content, source}]"""

# storage.py
class Storage:
    def save_bars(self, code: str, df: pd.DataFrame) -> None
    def load_bars(self, code: str) -> pd.DataFrame
    def cache_exists(self, code: str) -> bool
    def save_fundamentals(self, df: pd.DataFrame) -> None
```

### 11.2 因子层 `src/factors/`

```python
# technical.py
def compute_technical(df: pd.DataFrame) -> pd.DataFrame:
    """输入：日线 K 线。输出：附加技术因子列。
    新增列：ma5, ma10, ma20, ma60, macd, macd_signal, rsi14,
            atr14, vol_ratio（量比）, turnover_rate, trend_score（0-100）"""

# fundamental.py
def compute_fundamental(df_fin: pd.DataFrame) -> pd.DataFrame:
    """输入：原始财务数据。输出：标准化基本面分数。
    新增列：value_score（估值）, growth_score（成长）, quality_score（盈利质量）"""
```

### 11.3 策略层 `src/strategy/`

```python
# base.py
class Strategy(ABC):
    name: str
    params: dict

    @abstractmethod
    def filter_universe(self, df_all: pd.DataFrame) -> list[str]:
        """硬过滤：返回通过筛选的股票代码列表"""

    @abstractmethod
    def score(self, df_factors: pd.DataFrame) -> pd.Series:
        """打分：返回每只股票 0-100 的综合分"""

    @abstractmethod
    def entry_price(self, row: pd.Series, atr: float) -> float:
        """目标买入价"""

    @abstractmethod
    def exit_price(self, entry: float, atr: float) -> tuple[float, float, int]:
        """返回 (目标卖出价, 止损价, 期望持仓天数)"""

# builtin.py
class TrendFollowingStrategy(Strategy):
    """示例：均线多头 + MACD 金叉 + 量能放大"""
    params = {"ma_period": 20, "atr_k_entry": 1.0, "atr_m_exit": 3.0, "atr_n_stop": 1.5}

# scorer.py
def composite_score(tech: pd.Series, fund: pd.Series, theme: pd.Series | None = None,
                   weights=(0.6, 0.4, 0.0)) -> pd.Series:
    """加权综合分。MVP 阶段 theme 传 None，权重 (0.6, 0.4, 0.0)"""
```

### 11.4 信号层 `src/signal/`

```python
# generator.py
@dataclass
class Signal:
    code: str
    name: str
    score: float
    entry_price: float
    exit_price: float
    stop_loss: float
    hold_days: int
    reasons: list[str]  # 入选理由（自然语言）

def generate_signals(strategy: Strategy, trade_date: date) -> list[Signal]:
    """每日盘前调用一次。返回按 score 降序的 Top 5 信号"""
```

### 11.5 回测层 `src/backtest/`

```python
# engine.py
@dataclass
class BacktestConfig:
    start: date
    end: date
    initial_cash: float = 100_000
    commission: float = 0.0003   # 万三
    stamp_tax: float = 0.001      # 千一（卖出）
    slippage: float = 0.001       # 千一
    enable_t1: bool = True
    enable_price_limit: bool = True
    max_hold_days: int = 20

@dataclass
class BacktestResult:
    equity_curve: pd.Series         # 资金曲线（日期索引）
    trades: pd.DataFrame            # 每笔交易明细
    metrics: dict                   # {胜率, 最大回撤, 夏普, 年化收益, ...}

def run_backtest(strategy: Strategy, config: BacktestConfig,
                 price_panel: pd.DataFrame) -> BacktestResult:
    """执行回测。
    price_panel: index=date, columns=code, value=close（已对齐）"""

# metrics.py
def calc_metrics(equity: pd.Series, trades: pd.DataFrame) -> dict:
    """计算胜率、最大回撤、夏普比率、年化收益、盈亏比、平均持仓天数"""

# report.py
def make_report(result: BacktestResult) -> dict:
    """生成可视化所需的结构化报告：
    {equity_curve, monthly_returns, drawdown_periods, trade_distribution}"""
```

### 11.6 模拟交易 `src/simulator/`

```python
# paper.py
@dataclass
class Position:
    code: str
    shares: int
    entry_price: float
    entry_date: date
    stop_loss: float
    target_price: float

class PaperTrader:
    def __init__(self, initial_cash: float, db_path: str):
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.history: list[dict] = []

    def on_signal(self, signal: Signal) -> None:
        """收到信号后模拟下单（T+1 撮合：信号日收盘价成交）"""

    def on_bar(self, date: date, prices: dict[str, float]) -> None:
        """每日盘后调用，更新盯市盈亏、检查止盈止损"""

    def status(self) -> dict:
        """返回当前账户快照：现金、市值、总资产、未实现盈亏"""
```

### 11.7 UI 层 `src/ui/`

```python
# app.py —— Streamlit 入口
import streamlit as st

st.set_page_config(page_title="盘前选股", layout="wide")
page = st.sidebar.selectbox("导航", ["今日信号", "回测结果", "策略管理", "模拟交易"])

if page == "今日信号":
    from src.ui.pages import today
    today.render()
elif page == "回测结果":
    from src.ui.pages import backtest
    backtest.render()
elif page == "策略管理":
    from src.ui.pages import strategy_mgr
    strategy_mgr.render()
elif page == "模拟交易":
    from src.ui.pages import paper
    paper.render()
```

---

## 12. 端到端数据流（伪代码）

```
# 每日盘前流程
strategy = load_strategy(user_choice)              # 用户在仪表板选择策略
fetcher = DataFetcher()
storage = Storage()

# 1. 数据准备（首次或缓存失效时执行）
codes = fetcher.get_stock_list()
for code in codes:
    if not storage.cache_exists(code):
        bars = fetcher.get_daily_bars(code, ...)
        storage.save_bars(code, bars)

# 2. 因子计算
price_panel = load_all_bars(codes)                  # 宽表
fundamentals = fetcher.get_fundamentals(codes)
tech_factors = compute_technical(price_panel)
fund_factors = compute_fundamental(fundamentals)
merged = merge(tech_factors, fund_factors)

# 3. 信号生成
candidates = strategy.filter_universe(merged)
scored = strategy.score(candidates)
top5 = scored.nlargest(5)

signals = []
for _, row in top5.iterrows():
    atr = row['atr14']
    entry = strategy.entry_price(row, atr)
    exit, stop, days = strategy.exit_price(entry, atr)
    signals.append(Signal(
        code=row['code'], name=row['name'], score=row['score'],
        entry_price=entry, exit_price=exit, stop_loss=stop, hold_days=days,
        reasons=explain(row),  # V1 接 LLM 后生成自然语言
    ))

# 4. 输出
display(signals)                                     # 仪表板渲染
simulator.on_signal_batch(signals)                   # 模拟交易记录
```

---

## 13. 用户操作手册（计划）

### 首次安装
```bash
# 安装 uv（Windows PowerShell）
irm https://astral.sh/uv/install.ps1 | iex

# 创建项目
cd d:\Vscode\stock plan
uv sync                          # 安装依赖
uv run python -m src.data.fetcher --init   # 首次全量拉取数据
```

### 每日使用
```bash
uv run streamlit run src/ui/app.py
# 浏览器打开 http://localhost:8501
# 点击「今日信号」 → 点击「生成今日信号」按钮
```

### 自定义策略
- 仪表板 → 策略管理 → 「新建策略」
- 选「可视化拼装」：拖拽因子、设置权重
- 选「写代码」：在编辑器中编写 Strategy 子类
- 选「LLM 辅助」（V1 上线）：用中文描述，Agent 生成代码

### 回测策略
- 仪表板 → 回测结果 → 选择策略 → 设置起止日期 → 「运行回测」
- 查看资金曲线、交易明细、归因报告

---

## 14. 下一步行动

按你确认的「先搭建骨架」，代码层面的实施步骤：

1. **Day 1**：建立 uv 项目 + 目录结构 + pyproject.toml + 最小可启动的 Streamlit
2. **Day 1-2**：数据层 fetcher + storage + 一键拉取脚本
3. **Day 2**：因子计算 + 内置策略 + 打分模型
4. **Day 2-3**：信号生成 + 回测引擎 + 测试用例
5. **Day 3**：仪表板「今日信号」+「回测结果」两页打通

> 计划书到此版本 v0.2，后续实施中发现需求调整再回头修订。

---

## 15. Phase Tasklist（按依赖顺序）

> 共 10 个 Phase、32 项任务。✅ 表示已确认进入该阶段，→ 表示依赖前置任务。

### Phase 0 — 环境与骨架（MVP 起点）
| # | 任务 | 依赖 |
|---|------|------|
| 1 | 安装 uv 并初始化项目 | — |
| 2 | 建立目录结构与 pyproject.toml | 1 |
| 3 | Streamlit 最小可启动骨架 | 2 |

### Phase 1 — 数据层
| # | 任务 | 依赖 |
|---|------|------|
| 4 | 实现 DataFetcher（AKShare 封装） | 2 |
| 5 | 实现 Storage（Parquet/SQLite 持久化） | 2 |
| 6 | 一键拉取脚本（全 A 股 5 年日线 + 财务） | 4, 5 |

### Phase 2 — 因子层
| # | 任务 | 依赖 |
|---|------|------|
| 7 | 技术因子计算（均线/MACD/RSI/ATR/量比/换手率） | 6 |
| 8 | 基本面因子计算（估值/成长/盈利质量） | 6 |
| 9 | 硬过滤（ST/停牌/流动性） | 7, 8 |

### Phase 3 — 策略层
| # | 任务 | 依赖 |
|---|------|------|
| 10 | Strategy 基类 | 2 |
| 11 | 内置趋势策略（均线多头+MACD金叉+量能） | 10, 7 |
| 12 | 打分模型（技术 60% + 基本面 40%） | 10, 9 |

### Phase 4 — 信号层
| # | 任务 | 依赖 |
|---|------|------|
| 13 | 信号生成（Top5 + 买卖价 + 止损 + 持仓周期） | 12 |

### Phase 5 — 回测层
| # | 任务 | 依赖 |
|---|------|------|
| 14 | 回测引擎（T+1/涨跌停/手续费/滑点） | 6, 10 |
| 15 | 回测指标计算（胜率/回撤/夏普/年化） | 14 |
| 16 | 回测测试用例（T+1/涨跌停/手续费） | 14 |

### Phase 6 — 模拟交易
| # | 任务 | 依赖 |
|---|------|------|
| 17 | PaperTrader 模拟交易（贴近实盘） | 13, 14 |

### Phase 7 — UI 仪表板
| # | 任务 | 依赖 |
|---|------|------|
| 18 | Streamlit 入口与导航 | 3 |
| 19 | 今日信号页 | 13 |
| 20 | 回测结果页 | 15, 16 |
| 21 | 策略管理页 | 10 |
| 22 | 模拟交易页 | 17 |

### Phase 8 — MVP 集成验证
| # | 任务 | 依赖 |
|---|------|------|
| 23 | MVP 端到端联调 | 19, 20 |
| 24 | MVP 全流程验证（真实数据跑通） | 23 |

### Phase 9 — V1 增强
| # | 任务 | 依赖 |
|---|------|------|
| 25 | 多策略对比 + Walk-Forward 滚动验证 | 24 |
| 26 | 详细回测报告（归因/热力图/回撤标注） | 25 |
| 27 | LLM Agent 集成（消息面/策略生成/解释） | 24 |
| 28 | 可视化策略拼装 | 27 |

### Phase 10 — V2 远期
| # | 任务 | 依赖 |
|---|------|------|
| 29 | LLM 自动复盘与参数优化建议 | 27 |
| 30 | 新闻舆情时间线 | 27 |
| 31 | 同策略多周期并存输出 | 25 |
| 32 | 飞书/微信推送（可选） | 26 |

---

### 里程碑
- **M1（MVP 完成）**：Phase 0–8 全部完成，即 2–3 天最小闭环
- **M2（V1 完成）**：Phase 9 完成，具备 LLM + 完整回测报告
- **M3（V2 完成）**：Phase 10 完成，全功能版

---

## 16. 开发进度

| 日期 | 阶段 | 完成情况 |
|------|------|----------|
| 2026-09-03 | Phase 0 环境与骨架 | ✅ 完成：uv 0.12.9 + Python 3.13、目录结构、pyproject.toml（streamlit/akshare/pandas/numpy/pyarrow/plotly）、Streamlit 骨架（4 页导航）验证通过 |
| 2026-09-03 | Phase 1 数据层 | ✅ 完成：DataFetcher（日线/财务/列表）、Storage（Parquet/SQLite）、fetch_all 一键拉取脚本（多线程断点续传，修复北交所 920xxx 前缀 bug），全量 5553 只缓存完成（0 失败） |
| 2026-09-03 | Phase 2 因子层 | ✅ 完成：技术因子（MA/MACD/RSI/ATR/量比/trend_score）、基本面因子（价值/成长/质量分段打分）、硬过滤（ST/停牌/流动性/次新） |
| 2026-09-03 | Phase 3 策略层 | ✅ 完成：Strategy 抽象基类、TrendFollowingStrategy（回调买入逻辑）、composite_score 加权打分 |
| 2026-09-03 | Phase 4 信号层 | ✅ 完成：generate_signals 输出 Top5 信号（含目标买卖价/止损/持仓周期/理由） |
| 2026-09-03 | Phase 5 回测层 | ✅ 完成：回测引擎（T+1/涨跌停/手续费/印花税/滑点）、指标计算、报告模块、7 个 pytest 测试通过 |
| 2026-09-03 | Phase 6 模拟交易 | ✅ 完成：PaperTrader（T+1/手续费/印花税/滑点/涨跌停/1-5 仓位，SQLite 持久化） |
| 2026-09-03 | Phase 7 UI 仪表板 | ✅ 完成：今日信号/回测结果/策略管理/模拟交易 4 页，浏览器验证通过 |
| 2026-09-03 | Phase 8 MVP 集成验证 | ✅ 完成：端到端联调跑通；回测引擎预计算因子优化（10+ 分钟→52 秒）；全量 5553 只参数寻优，默认参数 m=3.5/n=3.5/hold=30 + weekly + 大盘择时（收益 +9.37%、夏普 0.56） |
| 2026-09-03 | Phase 9 V1 增强 | ✅ 完成：动量策略（MomentumStrategy）+ 策略对比页（compare.py，多策略对比 + Walk-Forward 滚动验证）；详细回测报告（退出原因归因/月度收益热力图/回撤标注）；LLM 模块（client.py + analyzer.py，无 key 时 mock 降级）+ LLM 智能分析页；可视化策略拼装（CustomStrategy + visual_builder.py，today.py 支持自定义策略）；7 个 pytest 全部通过；7 个导航页浏览器验证通过 |
| 2026-09-03 | Phase 10 V2 远期 | ✅ 完成：LLM 自动复盘（review_backtest，mock 离线规则复盘）+ 回测页"🤖 LLM 复盘与参数优化建议"按钮；新闻舆情时间线（news/timeline.py 7 个新闻源 + news.py 4 tab，个股公告用东财公告接口）；同策略多周期并存输出（today.py 重写，短线/中短线/中线/多周期并存）；飞书/微信推送（push/notify.py + today.py 推送按钮）；修复回测结果 session_state 丢失 bug（点击 LLM 复盘按钮后结果保留）；8 个导航页浏览器验证通过；7 个 pytest 全部通过 |
| 2026-09-03 | Phase 11 V3 增强版 | ✅ 完成：①导航重构为 st.navigation 平铺 9 页（ui/pages/ → ui/views/，移除冗余项）②策略注册表 registry.py（7 策略：趋势/动量/均值回归/突破/价值/段永平/李录，全部带中文讲解与参数说明）③四大师价值投资研究独立页（views/four_masters.py + analyzer.four_masters_analysis，参考 ai-berkshire，巴菲特/芒格/段永平/李录四视角打星+否决清单+通过结论）④自定义策略均线自由组合（趋势均线对 5/7/10/20/30/60 任选 + 偏离基准线任选，引擎预计算 ma5/7/10/30）⑤LLM 接入智谱 GLM-4-Flash（llm/config.py 读 .env，OpenAI 兼容端点，key 不入源码，.gitignore 加 .env；LLM 页与四大师页显示连接状态）⑥深色主题（.streamlit/config.toml）⑦策略管理页支持多策略参数编辑（strategy_params_by_strategy）；自定义规则自测 + 7 个 pytest 全部通过 |
| 2026-09-03 | Phase 12 V4-Text（分支1） | ✅ 完成：①量价配置——自定义策略新增流动性下限（liquidity_min，亿元）排除 + 放量异动（vol_surge_min 量比阈值 + vol_surge_bonus 加分），builtin.py 加 avg_amount20 列②板块自定义筛选——新建 factors/board.py（主板/创业板/科创板/北交所前缀映射 + 剔除 ST），今日信号/回测/策略对比/策略拼装 4 页可选股范围，generator.py 统一接入③界面亲民化——新建新手指南页（guide.py，5 类 22 术语大白话+生活化例子+三分钟核心逻辑+免责声明），9 页加"本页名词速览"，registry.PARAM_DESC 全部大白话化④回测收益曲线——widgets.equity_curve_fig（累计收益率%+历史高点+回撤红色阴影），回测页/拼装页展示⑤共享组件 ui/widgets.py（board_filter_ui/page_glossary/equity_curve_fig），缓存键加 boards_json+exclude_st；修复 filter_universe_ui ST 漏网 bug；5 组 V4 自测 + 7 个 pytest 全部通过；分支1 已推送 GitHub |
| 2026-09-03 | Phase 13 V5-LLM 策略生成 + 持仓诊断（分支1） | ✅ 完成：①LLM 结构化策略生成——strategy/codegen.py（词表 WEIGHT/RULE/PARAM + 参数钳制 + py 代码提案）+ strategy/store.py（SQLite data/db/strategies.db 已保存策略持久化）+ llm/analyzer.py generate_strategy_config（LLM→结构化参数，无 key 降级离线规则）；LLM 页"策略生成"tab 重写：描述想法→生成建议→📋 参数对照表（默认值 vs 建议值）→⚠️ 未支持参数反馈→命名保存→📎 代码备注/导出（注释版/可运行脚本版），主区显示参数、代码仅作备注②已保存策略全站打通——今日信号/回测结果/策略对比/策略管理 4 页统一走 store.strategy_options()/resolve_strategy()，保存后立即可选，策略管理页可查看配置/反馈/提案代码/删除③持仓诊断——新建 analysis/holding.py（规则对照 4 级结论：清仓/减仓/做T/持有；做T 综合价位：低吸=max(MA10,现价-0.8×ATR)、高抛=min(近20日高,现价+0.8×ATR)，正T/反T自动判定+操作步骤；单股近一年重回测）+ views/portfolio.py（输入买价/买日期/策略→状态卡片+结论+做T卡片+120日走势图标注买价/止损/止盈/做T价位+重回测指标与资金曲线），导航插在今日信号后；买入日期晚于最新行情时近似诊断并提示；修复 filter_universe 需要 stock_list 含 is_st 列的问题；16 个新测试 + 17/17 pytest 通过；端到端浏览器实测通过（诊断/做T/图表/重回测/LLM生成→保存→今日信号下拉可见） |
| 2026-09-03 | Phase 14 定时数据自动更新（分支1） | ✅ 完成：①增量更新引擎 data/updater.py——交易日历（akshare，本地按天缓存 CSV，失败回退周末近似）、期望最新交易日规则（交易日过 16:00 取当天否则上一交易日）、增量合并（旧+新按日期去重整体重写，规避 save_bars 覆盖陷阱）、幂等预筛（已最新不联网）、8 线程并发、未缓存股票清单返回不自动拉取、日志 data/logs/update.log②命令行入口 data/update_daily.py（--days/--force/--workers）③Windows 计划任务 scripts/run_update.cmd 包装 + schtasks 注册 4 任务（\StockPlan\Update_1600/2000/0000/0800，跨平台脚本将来可迁 cron）④左侧导航「数据更新」页 views/update_center.py（状态指标/手动增量更新/fragment 3 秒轮询进度/完成摘要/未缓存股票提示单独补拉/更新日志 tail），12 页导航⑤6 个 pytest 用例 22/22 通过 |
| 2026-09-03 | Phase 15 云端部署准备（分支1） | ✅ 完成：①requirements.txt（本地验证版本固定）供 Streamlit Community Cloud②llm/config.py 加 st.secrets 回退（云端 Key 走 Secrets）③update_center 页按平台显示说明（非 Windows 提示手动更新）④空数据提示统一指向「数据更新」页⑤修复今日信号/回测页选未编辑参数策略时 `{**None}` TypeError 崩溃（saved 兜底 `{}`）⑥README 云端部署章节。部署操作（需用户 GitHub 授权）：share.streamlit.io → 仓库 Cwywwy/Text-stock 分支1 → 主文件 src/stock_plan/ui/app.py → Python 3.13 |

> 注：pandas-ta 与 numpy 2.5 冲突（numba 限制 numpy<2.3），技术指标改为**自研**（plan 备选方案）。
> 注：metrics 的 total_return 等已是百分比数值，显示时直接用 `:.2f`，不要再用 `:.2%` 或 `*100`。