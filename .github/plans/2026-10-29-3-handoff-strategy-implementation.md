# 交接提示词：策略改造落地（2026-10-29）

> 用途：本对话已完成调研与选型，上下文过长。**把下面分隔线之间的内容整段复制到新对话**，作为新对话的第一条消息。
> 本文件不入交付文档，只作为交接物料。

---

我要在 `D:\DeepSeekHarness\案例区\stock plan`（A 股盘前选股系统）上，按已完成的策略选型调研结论落地改造。**先写计划书给我审，审完我确认后再动代码。**

## 0. 先读这几个文件，不要重新调研

- `.github/plans/2026-10-29-2-new-strategy-research-and-selection.md`（调研计划书，**§10 是我全部拍板记录**）
- `docs/策略选型调研-2026-10-29.md`（40 个候选的逐条评估，参数/公式/坑都在里面；本次任务的口径以它为准）
- 项目根的 `AGENTS.md` 或等效规则文件（命名、测试报告、commit、CHANGELOG 规范）

## 1. 分支与环境

- 分支：`feature/strategy-refresh`（已存在，HEAD 应在 `49cab06` 之后）；主干 `main` 在 `3c2c348`
- Python：`.venv\Scripts\python.exe`（3.13.15）
- 测试：`.\.venv\Scripts\python.exe -m pytest -q`，**基线 445 passed，不允许下降**
- 所有改动都在这个分支，除非我另有指示

## 2. 我已拍板（2026-10-29 02:12）

1. **Top 3 照做**，顺序：① 收敛因子 → ② 动量方向修正 → ③ RRG 行业轮动
2. **事件驱动本轮不做**（涨停/打板、北向资金、CCK 全部暂缓，我后面另想办法）
3. **「先选类型 → LLM 发挥」两段式现在做**
4. **B 档字段（总股本、财报披露日）补齐**

## 3. 五块任务的具体口径（照文档执行，不要自己改需求）

### 任务 A：新增均线收敛因子 PCF / TRCF（文档 §3.4 候选 1）

- 公式：`factor = −log(1 + std(MA_1, MA_5, MA_10, MA_20, MA_60, MA_120))`，其中 `MA_1` 就是原始价格
- 五变体：**PCF**（收盘价）、**VCF**（成交量）、**PVCF**（PCF+VCF 截面 z-score 合成）、**ACF**（成交额）、**TRCF**（换手率）；窗口 `[5,10,20,60,120]`
- 落点：`src/stock_plan/strategy/builtin.py::build_factor_rows()` 加列。**只加列，不改现有列**
- 为什么排第一：一个 `rolling.std()` 的成本，加完之后所有策略同时多一个"波动结构"维度
- 接入策略：至少 `TrendFollowingStrategy` + `BreakoutStrategy`（收敛→突破是天生一对）；其余策略加不加按回测结果决定
- 验收：同策略、同区间，加/不加该因子对比；报告里必须给**收益、最大回撤、换手率、分年度（2022 熊市 + 2024 初微盘踩踏）**，不要只报年化

### 任务 B：动量因子方向修正（§3.2 候选 2）

- 现状：`MomentumStrategy` 用的是 `mom_ret`（20 日涨幅）
- 研报实测：A 股裸涨幅动量 M1/M3/M6/M12/M24 **IC 全为负**
- 改法：换成 `MA_{jt,L} / close`（L = 20 / 60 / 120 / 240）
- 这是**修 bug，不是加新策略**，改动量约两行
- 验收：只跑动量策略，改前/改后对比，同样要分年度。如果改完变差，**就保留原样并在报告里说明**——不要为了"按研报改"而变差还硬上

### 任务 C：RRG 行业轮动（§3.2 候选 1）

**必须分两步，每步可独立证伪：**

- **C1（先做，2–3 天）**：只用申万一级行业（`data/processed/classify/2026-10/industry_sw.parquet`，5220 只 / 31 个一级行业）按**等权**聚合出行业指数序列，然后算 RRG：
  - `RS = price / benchmark × 100`
  - `RS-Ratio = (100 × RS / RS.shift(220)).rolling(20).mean()`
  - `RS-Momentum = (100 × RS-Ratio / RS-Ratio.shift(60)).rolling(20).mean()`
  - 中枢 `(100,100)`；四象限：>100&>100 领先、>100&<100 转弱、<100&<100 落后、<100&>100 转强
  - `top_n=6`，选法用"距中枢欧氏距离最远的 6 个"（A 方案）
  - **预热期必须显式报错不要静默**：RS-Ratio 需 239 行、完整 RRG 需 318 行，不足就抛 `ValueError`
  - **如果 C1 在行业层就没有超额，C2 不要做**——这是成本最低的证伪路径
- **C2（C1 有超额才做）**：把行业信号落到个股层（在"转强"行业里用现有打分选股）
- 注意三条：①研报口径是**买行业 ETF**，我们改成买个股属二次改动，效果不保证；②仓库作者自己写明「不要当作可直接使用的策略或已验证的 alpha」；③该仓库 **license 为 null**，商用需注意

### 任务 D：两段式「先选类型 → LLM 发挥」（§6）

- 第一段：6 个类型下拉（趋势跟随 / 动量轮动 / 均值回归 / 多因子 / 小市值 / 事件驱动），每类带一份「骨架 + 可用字段白名单 + 类型默认参数」
- 第二段：LLM 只能在该类型的字段白名单内发挥，越界即拒绝（不要让它写出取不到的字段然后静默失败）
- 三层默认值兜底：类型默认值 → 全局默认值 → **缺字段则该条规则禁用 + 打 `degraded` 标记**，用户能在报告里看到"今天少算了哪一条"
- 落点：`src/stock_plan/strategy/custom.py`（现有哨兵值机制可复用：`dev_ma=20`、`mom_min=-100` 表示禁用）+ `src/stock_plan/ui/strategy_ai.py` + `src/stock_plan/ui/views/visual_builder.py`
- **事件驱动类型先留空骨架**（数据不够），不要硬造

### 任务 E：B 档字段补齐（§4.2）

1. **总股本** —— 有了它才能算总市值（现在只能 `流通市值 = 成交额 ÷ 换手率`）
2. **财报披露日** —— 现在 `fundamentals` 表只有 `updated_at`，不是公告日
- 铁律：**日线表结构只加列，不删不改**；所有消费方一律 `.get()` 取列，缺列走降级默认值 + `degraded` 标记
- 补历史时注意防未来函数：用报告期 + 合理滞后，或用真实公告日

## 4. 必须先写计划书

按项目惯例 `.github/plans/YYYY-MM-DD-N-new-<slug>.md` 写，必须包含：目标与范围、模块划分表（模块/职责/交互/新增或修改）、phase 拆分与每阶段完成标准、每阶段预期产出、测试策略、风险与限制、预计新增/修改/删除文件清单。

**写完给我审，我确认后再开始改代码。**

## 5. 已知事实清单（别重新查，直接用）

**数据**

- 日线 `data/processed/bars/*.parquet`：`date, open, high, low, close, volume, amount, turnover`；**`turnover` 覆盖率 100%**（小数，如 `0.008693`）
- **流通市值代理公式**：`流通市值 = amount / turnover`。已实测验证：`000001 → 2198.9 亿`、`600519 → 16710.7 亿`、`300750 → 9832.2 亿`、`002594 → 2884.5 亿`
- 申万行业：`data/processed/classify/2026-10/industry_sw.parquet`（`code, name, sw_l1, sw_l2, sw_l3, incl_date`，31 个一级）；同目录 `industry_csr.parquet`、`concept.parquet`。**历史月份未追补**（`src/stock_plan/data/classification.py` 本来就按月落盘）
- `data/db/meta.db`：`stock_list(code,name,industry,list_date,is_st)` 5576 行——**`industry` 全是空字符串，别用**；`fundamentals(code,roe,revenue_growth,net_profit_growth,gross_margin,debt_ratio,eps,bvps,pe,pb,updated_at)`；`delisted_stocks(...)`
- 市场快照 `data/processed/market/` **只有 2026-10-27、2026-10-28 两天**（`limitup` 含连板数/封单/炸板/float_mv）→ 这就是事件驱动本轮不做的原因
- universe：`data/processed/universe/` 61 个月度 parquet，**只有 `code` 一列**

**代码**

- 回测：`src/stock_plan/backtest/engine.py::run_backtest(strategy, config, bars_map, fund_map, stock_list)` → `BacktestResult`；`BacktestConfig(top_n=5, max_hold_days=20, rebalance_freq="daily", market_timing=False, fundamental_as_of=None, ...)`；`_pool_cutoff` 已做防未来函数
- 指标：`src/stock_plan/backtest/metrics.py::calc_metrics` → `total_return / max_drawdown / win_rate / trade_count`
- 策略基类：`src/stock_plan/strategy/base.py`（`filter_universe / score / entry_price / exit_price`）；注册表 `strategy/registry.py::STRATEGIES`
- 因子列：`strategy/builtin.py::build_factor_rows()`；共享扣分 `PENALTY_RULES`
- 无头回测模板：`scripts/measure_engine_defect_impact.py`（子进程 worker + `RESULT {json}` 输出，避免 import 缓存假结果）
- **已有但没被任何策略使用的资产**：`factors/chan.py`（缠论：分型/笔/中枢/三买）、`factors/regime.py`（市场阶段 + `equal_weight_index()` 等权指数——任务 C 聚合行业指数可复用）、`factors/emotion.py`、`factors/board.py`、`BacktestConfig.market_timing`

**现有 7 策略**

`TrendFollowingStrategy`(趋势跟随) / `MomentumStrategy`(动量) / `MeanReversionStrategy`(均值回归) / `BreakoutStrategy`(突破) / `ValueInvestingStrategy`(价值投资) / `DuanYongpingStrategy`(段永平) / `LiLuStrategy`(李录)。共享 `ma20>ma60` 硬门槛与 5 条 `PENALTY_RULES`（注意：`dev<−5%` 和 `dev>+5%` 都扣分，等于所有策略共享"窄带偏好"，这是同质化根源之一）。

## 6. 纪律（硬要求）

- 每 phase 至少一个 commit，英文 commit message ≤20 词，前缀 `(phase N)`
- 每次改完跑全量 `pytest`，写报告到 `tests/reports/<YYYY-MM-DD-HH-mm>.report-<n>.md`；**基线 445 passed 不得下降**
- 文档/注释/CHANGELOG 一律简体中文；改行为要同步 `README.md` 与 `CHANGELOG.md`
- **诚实反馈**：发现我的方向不对、或研报结论在我们数据上不成立，直接指出并给替代方案，不要硬做
- 不要引入新依赖（尤其不要为了一个因子引入 qlib / backtrader）

## 7. 起点

第一步：读第 0 节那三个文件 → 写计划书（含 A–E 五块的 phase 拆分与完成标准）→ 提交给我审。**审完再写代码。**

---

*（分隔线以上为交接提示词正文，可直接复制）*
