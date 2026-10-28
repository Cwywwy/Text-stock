# 新增需求：策略刷新落地实施计划（收敛因子 / 动量修正 / RRG 轮动 / 两段式 LLM / 字段补齐）

> 计划书编号：2026-10-29-4
> 上游依据：`.github/plans/2026-10-29-2-new-strategy-research-and-selection.md`（调研计划，§10 为拍板记录）+ `docs/策略选型调研-2026-10-29.md`（候选评估正文，本次口径以它为准）
> 状态：**执行中**（用户 2026-10-29 02:12 拍板 + 审阅确认；Q1–Q6 按推荐执行）。已完成 Phase 6，进行 Phase 7。

---

## 0. 目标与范围

### 0.1 本轮目标

在不动现有 7 个策略默认行为的前提下，按调研结论落地五件事，并用**可复现的回测数字**证明每一项「成立」或「不成立」：

1. **任务 A**：新增均线收敛因子五变体（PCF/VCF/PVCF/ACF/TRCF），接入趋势跟随与突破策略做 A/B；
2. **任务 B**：修正动量方向（`MA_L/close`），修正不成则保留原样并说明；
3. **任务 C**：RRG 行业轮动，先做 C1 行业层证伪，有超额才做 C2 个股层；
4. **任务 D**：两段式「先选类型 → LLM 白名单内发挥」，三层默认 + 降级可追溯；
5. **任务 E**：补齐总股本与财报披露日，消费方缺数据一律降级。

交付物 = 代码 + 分阶段测试报告 + A/B 与 C1 回测报告 + 文档同步；**不自动合并主干**。

### 0.2 范围边界（本轮明确不做）

| 不做 | 原因 |
| --- | --- |
| 事件驱动策略（涨停/打板、北向资金、CCK） | `data/processed/market/` 只有 2026-10-27、2026-10-28 两天快照；且 `limitup.parquet` 实际**没有** `float_mv` 列（见 §2.1），数据面不足以支撑。本轮只在任务 D 留空骨架。 |
| 引入任何新依赖（qlib / backtrader / pandas-ta 等） | 项目既有约定：回测与指标全部自研（`INSTRUCTION.md`）。本轮只加 pandas/numpy 原生计算。 |
| 改动 `data/processed/bars/*.parquet` 的既有列 / 结构 / 历史值 | 铁律。任务 E 采用「旁路新表」方案（见 §2.2、Phase 12），不动 5579 个日线文件。 |
| 合并回 `main` | 按项目规则：feature 分支全部计划完成后先询问用户。 |
| 改动其余 5 个策略的默认行为 | 任务 A/B/C2 的新参数一律默认关闭或默认沿用旧口径，靠回测结论+用户拍板才改默认。 |

---

## 1. 现状基线（2026-10-29 实测，非推测）

### 1.1 仓库与环境

| 项 | 值 |
| --- | --- |
| 分支 / HEAD | `feature/strategy-refresh` @ `ae0a53d`（工作区干净）；`main` @ `3c2c348` |
| Python | `.venv\Scripts\python.exe` 3.13.15；pandas 3.0.5 / numpy 2.5.2 / pyarrow 25.0.1 |
| 测试 | `445 passed in 11.73s`（本轮已用 `.\.venv\Scripts\python.exe -m pytest -q` 实测，0 failed、0 error）。说明：会话早期因沙箱写权限不足曾出现 114 个临时目录 teardown 报错，切换到完全访问后复跑为 445 passed / 0 失败，结论以本次复跑为准 |
| 测试文件 | `tests/` 下 34 个 `test_*.py`，无 `conftest.py`，各文件自带夹具 |
| 分支历史 | 阶段提交风格 `(phase N) 小写英文描述`，merge 提交为 `merge: ...` |

### 1.2 关键代码坐标（后续 Phase 直接按此定位）

| 能力 | 坐标 |
| --- | --- |
| 回测入口 / 配置 / 结果 | `src/stock_plan/backtest/engine.py:132` `run_backtest`；`BacktestConfig` `:32-61`；`BacktestResult` `:64-73` |
| 基准（等权持有候选池） | `engine.py:76-108` `equal_weight_benchmark` |
| 逐日候选池防未来函数 | `engine.py:157-159` `_pool_cutoff`（先截到 `start` 再过滤） |
| 因子预计算（回测路径） | `engine.py:186-198`：`compute_technical` + `high20` + `avg_amount20` + `ma5/7/10/30` |
| 每日候选行拼装（回测路径） | `engine.py:376-404`（列集见 §1.3） |
| 打分调用 | `engine.py:408` `df["score"] = strategy.score(df)` |
| 因子行拼装（实盘/今日信号路径） | `src/stock_plan/strategy/builtin.py:167-239` `build_factor_rows` |
| 因子列一致性测试 | `tests/test_factor_parity.py:73-101`（双向列集相等）、`:200-256`（末日终值数值对拍） |
| 指标 | `src/stock_plan/backtest/metrics.py:12-78` `calc_metrics` |
| 策略基类 | `src/stock_plan/strategy/base.py:18-60`：`filter_universe / score / entry_price / exit_price` |
| 趋势策略 | `builtin.py:38` `TrendFollowingStrategy`，`score_with_penalties` `:150-151` |
| 突破策略 | `strategy/breakout.py:25-102`，`score` `:62-91` |
| 动量策略 | `strategy/momentum.py:23-111`，`score` `:64-98`（用 `mom_ret` `:82-84`） |
| 动量侧重复死代码 | `momentum.py:114-149` 第二个 `build_factor_rows`，仅 `__main__` 自测引用，**无生产调用** |
| 自定义策略 | `strategy/custom.py:47-147`；哨兵 `dev_ma=20` `:89`、`mom_min=-100` `:37/108` |
| 配置清洗 / 词表 | `strategy/codegen.py:75` `normalize_config`；`RULE_SPEC` `:27-44`；`PARAM_SPEC` `:46-51`；`WEIGHT_SPEC` `:21-25` |
| 策略注册表 | `strategy/registry.py:19-27` `STRATEGIES`；`create_strategy` `:31` |
| 信号报告结构 | `signal/report.py:271` `SignalReport`（`warnings: tuple[str, ...]`） |
| 信号生成入口 | `signal/generator.py:446` `generate_signal_report`、`:525` `generate_signals_with_report` |
| UI 插入点 | `ui/strategy_ai.py:34` `render_ai_strategy_section`；`ui/views/visual_builder.py:157-159`（配置区顶部）、`:246-269`（config 组装）、`:102-138` `_apply_ai_config` |
| 数据路径常量 | `data/storage.py:25` `PROJECT_ROOT`、`:58` `BARS_DIR`、`:59` `DB_PATH`；`:221` `load_market_maps()` |
| 等权指数工具 | `factors/regime.py` `equal_weight_index`（`MIN_CONTRIBUTORS` `:47`） |

### 1.3 两条路径的因子列集（当前共 21 列，必须同步）

`code, close, atr14, ma20, ma60, rsi14, vol_ratio, avg_amount20, high20_ratio, macd, macd_signal, mom_ret, value_score, growth_score, quality_score, trend_score, fund_score, ma5, ma7, ma10, ma30`

> 说明：`build_factor_rows` 与 `engine.py` 候选 dict 目前**巧合地**等价，由 `test_factor_parity.py:73-101` 双向锁死。本轮任务 A/B 加列时，**两条路径、该测试、数值对拍必须同时升级**，否则测试会红（这是断言升级，不是删测试）。

### 1.4 数据事实

| 数据 | 实测 |
| --- | --- |
| 日线 | 5579 个 parquet，列 `date, open, high, low, close, volume, amount, turnover`，1243 行/只，区间 2021-09-06 ~ 2026-10-28，合计 6,366,746 行；`turnover` 100% 覆盖（小数） |
| 申万分类 | `classify/2026-10/industry_sw.parquet` 5220 行 / 31 个 `sw_l1`；`sw_l3` 100% 空；`sw_l2` 20 个空；`incl_date` 为字符串批次（3871 只为 2021-12-13） |
| 其他分类 | 同目录 `industry_csr.parquet`、`concept.parquet`；**只有 2026-10 一个月**，历史月份未追补 |
| meta.db | `stock_list(code,name,industry,list_date,is_st)` 5576 行，`industry` **全空**，`list_date` **全空**；`fundamentals` 10 指标 + `updated_at`（抓取时间，微秒 ISO，5579 个 code 全在 2026-09-03~10-28）；`delisted_stocks` 339 行 |
| 市场快照 | `processed/market/` 仅 2 天；`limitup.parquet` 列：`code, name, change_pct, close, amount, turnover_rate, consecutive_boards, seal_amount, broken_times` —— **无 `float_mv`** |
| universe | 61 个月度 parquet，仅 `code` 列 |
| 分类覆盖 | bars 有 5579 只，分类 5220 只；359 只无行业（353 只北交所 920xxx + 6 只其他）；`code` 格式两边一致（6 位无后缀） |

---

## 2. 勘察发现：与既有口径不一致的地方（需要你知情后再拍板）

> 按项目规则「诚实反馈」：以下不是推翻结论，而是**执行前必须校正的事实**，每条给出影响与处理建议。

### 2.1 `limitup` 缺 `float_mv`，事件驱动不做的理由比原来说的更硬

- 事实：你给的已知事实里 `limitup` 含 `float_mv`，实际没有；`market_snapshot.py:224-226` 的保留列白名单漏了它。
- 影响：即便补齐历史，涨停池也没有流通市值口径，打板策略的仓位/流动性约束无法计算。本轮不做事件驱动完全成立。
- 处理：本轮不动 `market_snapshot.py`（不属于这五块任务）；在 `CHANGELOG` 的「遗留问题」里记一条，后续做事件驱动前先修白名单并回补。

### 2.2 `stock_list.list_date` 与 `industry` 全空

- 事实：`stock_list` 的 `list_date`、`industry` 两列全为空字符串/空值。
- 影响：调研计划书把「上市日期」列为 A 档可得字段，实际不可用；研报里「剔除上市不足一年」的次新股口径暂时不能按真实上市日实现（现有 `filter_universe` 用 K 线长度近似）。
- 处理：本轮五块任务均不依赖真实 `list_date`，不阻塞。若后续要精确次新过滤，走 E 类「新数据源补齐」路线。

### 2.3 行业分类只有当前快照 → C1 存在行业归属前视偏差

- 事实：`industry_sw.parquet` 只有 2026-10 一期，`incl_date` 是批次字符串、且 3871 只同批，**不能**当作「当时是否已归类」的证据。
- 影响：C1 回测用「今天的行业归属」回溯 2023 年以来的历史。行业调整频率低但非零，属于**已知且必须披露**的偏差，不能声称零未来函数。
- 处理：C1 报告固化一句「行业归属使用 2026-10 快照回溯，存在行业调整前视偏差」；不使用 `incl_date` 做剔除（否则会引入更大偏差）。

### 2.4 `fundamentals.updated_at` 确非公告日，`announcements.db` 为空

- 事实：`updated_at` 是抓取批次时间（2026-09-03~10-28，全表同一批），`data/db/announcements.db` 当前为空表。
- 影响：任务 E 的「财报披露日」必须新拉数据，不能从现有表推导。
- 处理：Phase 12 新建 `data/disclosure.py` + `report_disclosure` 表，并按公告日/法定截止日做可见性约束（防未来函数）。

### 2.5 `metrics.py` 已有 9 项指标，缺的是「分年度」和「换手率」

- 事实：`calc_metrics` 返回 `total_return, annual_return, max_drawdown, sharpe, win_rate, profit_loss, trade_count, avg_hold_days, avg_pnl_pct`（你给的口径只列了 4 项）。
- 影响：四个验收指标里前三个现成，**换手率**与**分年度**需要新增函数。
- 处理：新增 `calc_annual_metrics()` 与 `calc_turnover()`，**不改** `calc_metrics` 签名与既有键（避免破坏 UI 与缓存）。

### 2.6 收敛因子公式有量纲问题（这条最重要，直接影响 A 的结论可信度）

- 事实：`std(MA_1..MA_120)` 的单位是**元**。同样 2% 的均线离散度，5 元股票的 std≈0.1、1300 元股票（茅台）的 std≈26 → `-log(1+std)` 分别是 -0.095 与 -3.3。也就是说 PCF/VCF/ACF/TRCF 的**原始值跨股票不可比**，截面排序会系统性偏向高价股/高成交量股，等于偷偷引入「价格水平」与「市值」暴露。
- 影响：若直接拿原始值进策略打分，A/B 的结论可能来自「高价股偏好」而不是「收敛结构」。研报原文只做了 z-score 合成（PVCF），单因子列的量纲问题没有处理。
- 处理建议（见 §9 Q1）：**按文档原式实现主口径**（可复现、不擅自改需求），同时增加无量纲诊断列 `cv_*`（`std(MA)/mean(MA)`，同号取负对数）只在报告里做对照；策略使用原始列时**先做截面 z-score 或百分位**，绝不直接比大小。

### 2.7 `MA_{jt,L}/close` 的方向必须写死，否则改完必然反向

- 事实：研报口径 `MA_L / close`。强势股 `close > MA` → 比值 **< 1**；「IC 全为负」意味着**因子值越小、未来收益越高**。
- 影响：若把它当「越高越好」直接加分，动量策略会从「追强」变成「追弱」，A/B 必然变差——这会是实现错误，不是结论错误。
- 处理：列定义固定为 `ma_ratio_L = MA_L / close`（越小越强，与研报一致），策略内使用 `-ma_ratio_L` 或 `1 - ma_ratio_L`，并在报告与代码注释里写明方向。

### 2.8 预热期与数据起点冲突，A/B 区间必须分开定

- 事实：日线起点 2021-09-06；`MA_120` 约 2022-03 可用，`MA_240` 约 2022-09 可用；完整 RRG（318 行）约 2022 年末~2023 年初可用。
- 影响：若统一从 2021-10 起跑，A 的前 5 个月、B 的前 11 个月因子为 NaN，结论会掺入暖机噪声。
- 处理（见 §9 Q6）：A 用 `2022-03-07 ~ 2026-10-28`；B 用 `2022-09-01 ~ 2026-10-28`；C1 从 RRG 首个有效日起算实际日期写进报告。三者各自与「同区间基线」对比，不做跨区间横比。

### 2.9 `equal_weight_index` 在成分不足时静默返回常数 1.0

- 事实：`factors/regime.py` 中当成分数 < `min_contributors` 时，等权指数退化为常数（无波动），不报错。
- 影响：C1 若直接复用它对 31 个行业聚合，行业成员很少的早期日期会得到「零波动行业指数」，RRG 会把它算成完美收敛，污染选行业。
- 处理：`factors/rrg.py` 自建聚合，显式要求 `n_constituents >= min_components=3`，不足则该行业当日置 NaN 并计数；另加断言「指数序列不是常数」，不复用静默逻辑。

### 2.10 两个 `build_factor_rows` 与 `build_custom_factor_rows` 的重复

- 事实：`builtin.py:167` 是唯一生产版本；`momentum.py:114` 是仅在 `__main__` 自测里用的重复实现；`custom.py:150-162` 是转发。
- 影响：任务 A/B 加列时若只改 `builtin.py`，`momentum.py` 那份会继续漂移，未来极易误用。
- 处理：Phase 8 顺手把 `momentum.py:114-149` 标记 `deprecated` 或删除（见 §9 Q6 附带确认）。不扩大改动面。

---

## 3. 验收口径（预注册，先定判定线，避免事后挑数字）

### 3.1 通用要求（每一块都要满足）

1. **同策略、同区间、同配置**下做加/不加对比；除被检验的因子外，其余参数逐字不变。
2. 必报四件套：**总收益、年化收益、最大回撤、换手率**；再加 **分年度表**（2022 / 2023 / 2024 / 2025 / 2026，2022 含熊市、2024 含 1-2 月微盘踩踏）。
3. 必报**基准超额**（`BacktestResult.benchmark`，同期等权持有候选池）。
4. 结果可复现：同一命令连跑两次，JSON 指标完全一致（脚本内做自检）。
5. 参数敏感度：关键参数至少给 3 档（如 `conv_weight ∈ {0.1, 0.2, 0.3}`），只看方向是否稳定，不挑最优值当结论。

### 3.2 换手率定义（新增，写进代码注释与报告）

- 单边年换手 = 年度**买入成交额** ÷ 年度平均总资产；双边 = 单边 × 2。
- 同时报：年买入笔数、年卖出笔数、平均持仓交易日、`avg_hold_days`（已有）。
- 数据来源：`trades` 已有 `entry_price / exit_price / shares`，无需新增数据。

### 3.3 任务 A 判定线（收敛因子是否值得默认开启）

四条**同时**满足才算「值得默认开」：

| # | 条件 |
| --- | --- |
| A-1 | 年化收益 ≥ 基线 + 0.5pp，**或** 最大回撤改善 ≥ 2pp |
| A-2 | 2022 年度与 2024 年度收益相对基线各不低于 -3pp（不显著恶化） |
| A-3 | 单边年换手增幅 ≤ 30% |
| A-4 | 两个接入策略（趋势跟随、突破）方向一致；只有一个变好则只在该策略开启 |

不满足 → 默认关闭，仅保留可选参数，报告写明「本数据上不成立」。

### 3.4 任务 B 判定线（动量方向修正）

- 「变差」定义为：年化收益 < 基线 - 1pp，**或** 最大回撤恶化 ≥ 2pp。
- 变差 → **保留 `mom_ret` 为默认**，报告写清差异与可能原因（如 A 股样本期、调仓频率、20 日窗口与研报口径不一致）。
- 变好 → 建议默认切到 `ma_ratio`，仍由你拍板（G3）。

### 3.5 任务 C1 判定线（行业层是否真有超额）

预注册四条，全满足才算「C1 有超额」并启动 C2：

| # | 条件 |
| --- | --- |
| C1-1 | 相对 31 行业等权基准，年化超额 ≥ **+2.0pp**（扣单边 10bp 调仓成本） |
| C1-2 | 最大回撤不劣于基准 **3pp** 以上 |
| C1-3 | 有效区间内 ≥ **3 个自然年度**正超额 |
| C1-4 | 年单边换手 ≤ **12 次** |

不满足 → **C2 不做**，报告写「证伪结论 + 可能的失败原因」，不继续加复杂度。

### 3.6 任务 D 判定线（两段式）

- LLM 输出越界字段 → 拒绝并给中文原因；不得静默丢弃。
- 缺字段 → 该条规则禁用 + `degraded` 标记，且**报告里能看到**「今天哪条规则没算」。
- 旧配置（无 `archetype` 键）行为与现在**逐值一致**（回归测试锁死）。
- 事件驱动类型仅占位，选择时明确提示「数据不足，本轮不可回测」。

### 3.7 任务 E 判定线（字段补齐）

- 总股本、流通股本、总市值、披露日 四类数据都有落盘来源 + 覆盖率报告。
- 任一消费点缺数据必须走降级默认 + `degraded` 标记，且单测覆盖。
- 现有 7 策略在默认参数下回测结果**逐值不变**（回归测试）。

---

## 4. 模块划分

| 模块 | 职责 | 交互 | 状态 |
| --- | --- | --- | --- |
| `factors/convergence.py` | 五变体收敛因子公式、窗口常量、截面 z-score 合成 | 被 `builtin.build_factor_rows`、`engine.py` 预计算、策略打分引入 | 新增 |
| `factors/momentum.py` | `ma_ratio_L = MA_L/close` 列、方向约定 | 同上 | 新增 |
| `factors/rrg.py` | 行业等权指数聚合、RS / RS-Ratio / RS-Momentum、四象限、选行业 | 被 `backtest/rotation.py`、`strategy/rrg_rotation.py` 使用 | 新增 |
| `backtest/rotation.py` | 行业层面轮动回测（周/月调仓、成本、基准对比） | 输入 `rrg.py` 面板，输出 `RotationResult` | 新增 |
| `strategy/builtin.py::build_factor_rows` | 加 `conv_*`、`ma_ratio_*` 列（只加不改） | 供今日信号与回测两路径 | 修改 |
| `backtest/engine.py` | 预计算与候选行同步新列；通用 `extra_columns` 注入（C2 用） | 与 `build_factor_rows` 列集/数值对齐 | 修改 |
| `backtest/metrics.py` | 新增 `calc_annual_metrics` / `calc_turnover` | 供 A/B/C1 报告 | 修改 |
| `strategy/trend` / `breakout` | 新增可选 `conv_weight`（默认 0） | 读 `conv_*` 列 | 修改 |
| `strategy/momentum.py` | 新增 `momentum_mode ∈ {ret20, ma_ratio, ma_ratio_blend}`（默认 `ret20`） | 读 `ma_ratio_*` 列 | 修改 |
| `strategy/rrg_rotation.py` | C2：转强行业内的个股选择 | 读 `rrg_quadrant` 外部列 + 现有打分 | 新增（条件） |
| `strategy/archetypes.py` | 6 个类型的骨架/白名单/默认参数/事件驱动占位 | 被 codegen / custom / UI 共用 | 新增 |
| `strategy/codegen.py` | `normalize_config` 增加按类型白名单过滤 | 输出 config + 越界键 + degraded | 修改 |
| `strategy/custom.py` | 接受 `archetype`/`degraded`，缺列规则禁用 | 供 `SignalReport` 汇总 | 修改 |
| `signal/report.py` / `signal/generator.py` | `SignalReport` 增加 `degraded` 字段并汇总 | UI 报告区展示 | 修改 |
| `ui/strategy_ai.py` / `ui/views/visual_builder.py` | 6 类型下拉、白名单提示、degraded 展示 | 调 codegen/custom/archetypes | 修改 |
| `data/share_capital.py` | 股本快照拉取、历史流通股本反推、总股本近似 | 供 `strategy/fields` 消费 | 新增 |
| `data/disclosure.py` | 财报披露日（预约/实际/法定截止日兜底） | 供 E 消费 | 新增 |
| `data/storage.py` | 新增数据目录常量与读写 | 被上述两个模块调用 | 修改 |
| `scripts/measure_factor_ab.py` | 无头 A/B 测量（子进程 worker + `RESULT {json}`） | 复用 `storage.load_market_maps()` | 新增 |
| `scripts/measure_rrg_industry.py` | C1 回测与报告生成 | 复用 `rotation.py` | 新增 |
| `scripts/backfill_share_capital.py` / `backfill_disclosure_dates.py` | 一次性回补（幂等、可断点续跑） | 调用两个 data 模块 | 新增 |

模块边界原则：**Core 不动**（`engine.py` 只做列集同步 + 一个通用注入钩子，不内嵌任何 RRG/收敛逻辑）；新能力全部落在独立新模块，通过列/面板注入接入。

---

## 5. Phase 拆分与完成标准

依赖关系：

```
Phase 6 基线+脚手架
   ├── Phase 7 任务A 收敛因子 ──[G2 拍板默认开关]
   ├── Phase 8 任务B 动量修正 ──[G3 拍板默认口径]
   ├── Phase 9 任务C1 RRG 行业层 ──[G4 判定：C2 做/不做]
   │        └── Phase 10 任务C2（条件执行）
   ├── Phase 11 任务D 两段式
   └── Phase 12 任务E 字段补齐
                └── Phase 13 集成、文档、交付
```

### Phase 6 — 基线固化与测量脚手架（不改任何策略行为）

**目标**：把「现状」变成可重复执行的数字，后面所有对比都对着它。

1. 全量 `pytest` 复跑并记录（当前 445 passed）；建立「新增测试只增不减」的计数规则。
2. `backtest/metrics.py` 新增：
   - `calc_annual_metrics(equity: pd.Series, trades: pd.DataFrame) -> dict[str, dict]`（按自然年：收益、最大回撤、交易数、平均持仓天数、单边换手）；
   - `calc_turnover(equity: pd.Series, trades: pd.DataFrame) -> dict`（§3.2 定义）；
   - 不改 `calc_metrics` 的签名与返回键。
3. 新增 `scripts/measure_factor_ab.py`：
   - 父进程起子进程跑真实 import，子进程打印 `RESULT {json}`，避免 import 缓存假结果（沿用 `scripts/measure_engine_defect_impact.py` 协议）；
   - **不学**旧脚本「临时改写 engine.py 再还原」的做法：新脚本只加参数，不改源码；
   - 参数：`--strategy 趋势跟随策略 --start --end --top-n --out`；
   - 内置确定性自检：同参数连跑两次，指标 JSON 必须逐字符相等。
4. 用 7 个策略跑三套基线（后续 A/B 各自对齐自己的可比区间，见 §2.8、Q6）：
   - `full`：`2021-12-07 ~ 2026-10-28`（总览；受 60 根均线预热约束：2021-12-06 只有 59 根，候选池为空，实测 7 个 arm 全部空结果，故起点顺延一个交易日）
   - `win_a`：`2022-03-07 ~ 2026-10-28`（任务 A 可比区间）
   - `win_b`：`2022-09-01 ~ 2026-10-28`（任务 B 可比区间）
   落 `tests/reports/<YYYY-MM-DD-HH-mm>.report-1.md` + 同名 `.json`。

**预期产出**
- `backtest/metrics.py` 新增两个函数（含单测）
- `scripts/measure_factor_ab.py`（可重复执行的 A/B 测量器）
- 7 策略 × 3 窗口基线 JSON + 报告 1 份
- 1 个 commit

**完成标准**
- [x] `pytest` 459 passed（445 基线 + 6 个 metrics 用例 + 8 个收敛因子用例；后者属 Phase 7，代码已先行落地、随 Phase 7 提交）
- [x] 三套基线 JSON 入库，抽 3 个 arm（三窗口各一）各跑两次，指标/分年度/换手逐字符相等
- [x] 报告含四指标 + 分年度表 + 基准超额
- [x] 1 个 commit：`(phase 6) add annual metrics and factor A/B measurement harness`

**执行记录（2026-10-29 04:27）**
- 产出：`backtest/metrics.py` 新增 `calc_annual_metrics()` / `calc_turnover()`（`calc_metrics` 签名与 9 键未动）、`tests/test_metrics.py`（6 例）、`scripts/measure_factor_ab.py`（子进程 worker + `RESULT {json}`，支持 `--baseline` / `--spec` / `--jobs`）。
- 基线：`tests/reports/2026-10-29-04-12.report-1.{json,md}`，7 策略 × 3 窗口 = 21 arm 全部成功（等间隔抽样 769 只，`BacktestConfig` 默认）。
- **口径修正**：`full` 起点由 2021-10-01 改为 **2021-12-07**。原因：日线自 2021-09-06 起，内置策略硬过滤要求 ≥60 根 K 线；2021-12-06 恰好只有 59 根 → 候选池为空、资金曲线 0 点，第一版 7 个 arm 全 `None`（已加守护：worker 遇空资金曲线直接 exit 3，不再静默写 None）。
- 关键数字（`full`／基准 +26.88%）：趋势跟随 −44.98%、动量 −59.34%、均值回归 −40.01%、突破 −62.18%、价值 −29.11%、段永平 −16.59%、李录 −26.23%。`win_a`（基准 +33.05%）与 `win_b`（基准 +37.11%）同向，**7 个策略全部跑输同期等权基准**，且日频单边年换手 13–21 倍，交易成本拖累明显。这条会作为后续 A/B 的对照前提写进最终报告。

### Phase 7 — 任务 A：均线收敛因子 PCF / VCF / PVCF / ACF / TRCF

**A1 纯计算层**
1. 新增 `src/stock_plan/factors/convergence.py`：
   - `CONVERGENCE_WINDOWS = (5, 10, 20, 60, 120)`；
   - `convergence_series(series: pd.Series, windows=CONVERGENCE_WINDOWS) -> pd.Series`：`-np.log1p(std(MA_1..MA_120))`，`MA_1` = 原序列；`std=0` → 0；不足窗口 → NaN；不 fillna、不静默补 0；
   - `convergence_columns(bars: pd.DataFrame) -> pd.DataFrame`：日线 → `conv_pcf / conv_vcf / conv_acf / conv_trcf`（收盘/成交量/成交额/换手率四列）与诊断列 `conv_pcf_cv`（`std/mean` 无量纲版，仅报告用）；
   - `compose_pvcf(df: pd.DataFrame) -> pd.Series`：截面 z-score 后 `z(conv_pcf) + z(conv_vcf)`；NaN 保持 NaN；单只/零方差时返回 NaN 并计数。
2. `builtin.build_factor_rows`：取末行 4 个原始列，再对整表调 `compose_pvcf` 补 `conv_pvcf`。
3. `engine.py:186-198` 预计算里加同样 4 列（滚动计算，天然无未来函数）；`:376-404` 候选 dict 同步；`df` 打分前调 `compose_pvcf` 补第 5 列。
4. 升级 `tests/test_factor_parity.py`：列集期望加 5 列、数值对拍列表加 5 项；新增 `tests/test_convergence_factor.py`（公式手算、全收敛=0、NaN 预热、零方差、截面 z-score、5 变体齐全、引擎/实盘一致性）。

**A2 策略接入（默认关闭）**
5. `TrendFollowingStrategy.score_with_penalties` 与 `BreakoutStrategy.score` 增加 `params["conv_weight"]`（默认 `0.0`）：分数 `+= conv_weight × zscore(conv_pcf)`（截面 z-score，见 §2.6）；NaN → 贡献 0。**默认参数下分数必须逐值等于旧实现**（回归测试）。

**A3 A/B 与报告**
6. 4 组实验：趋势跟随×(关/开)、突破×(关/开)；`conv_weight ∈ {0.1, 0.2, 0.3}` 敏感度。
7. 按 §3.3 判定线出结论：是否建议默认开启（G2 交你拍板）。

**预期产出**
- `factors/convergence.py` + 单测
- 两路径 5 个新列 + parity 升级
- 趋势/突破的 `conv_weight` 可选参数（默认 0）
- A/B 报告（4 组 + 敏感度）+ G2 结论

**完成标准**
- [ ] 21 列数值逐列不变（与 Phase 6 基线 JSON 对比）
- [ ] 默认参数下 7 策略回测指标与 `win_a` 基线完全一致
- [ ] A/B 报告含四指标 + 分年度 + 敏感度表 + 判定结论
- [ ] `pytest` 全绿且总数 ≥ 445 + 新增
- [ ] commit `(phase 7) add PCF/VCF/PVCF/ACF/TRCF convergence factors and A/B report`

### Phase 8 — 任务 B：动量因子方向修正

1. 新增 `factors/momentum.py`：`ma_ratio_columns(bars, windows=(20,60,120,240)) -> DataFrame`，列名 `ma_ratio_20/60/120/240 = MA_L / close`（**越小越强**，注释写死方向）。
2. `build_factor_rows` 与 `engine.py` 候选行同步加 4 列；parity 测试升级。
3. `MomentumStrategy` 增加 `momentum_mode`：
   - `"ret20"`（默认，现有 `mom_ret` 行为逐值不变）；
   - `"ma_ratio"`：用 `-ma_ratio_60`（或 `1 - ma_ratio_60`）替换动量加分项，映射为 0~30 分；
   - `"ma_ratio_blend"`：四个窗口截面百分位取均值后映射；NaN 视为中性。
4. 只跑动量策略：三模式 × 区间 `2022-09-01 ~ 2026-10-28` 对比，按 §3.4 判定。
5. 顺手处理 `momentum.py:114-149` 重复 `build_factor_rows`（用户确认后删除或标 deprecated）。

**预期产出**
- `factors/momentum.py` + 4 个 `ma_ratio_*` 列 + parity 升级
- `MomentumStrategy.momentum_mode` 三模式（默认 `ret20` 不变）
- 三模式对比报告 + G3 结论

**完成标准**
- [ ] 三模式均可跑；默认模式输出与 `win_b` 基线逐值一致
- [ ] 报告含四指标 + 分年度 + 方向说明 + 判定结论（变差就保留原样）
- [ ] commit `(phase 8) add MA/close momentum ratio mode and A/B report`

### Phase 9 — 任务 C1：RRG 行业层（可证伪）

1. 新增 `factors/rrg.py`：
   - `build_industry_index(bars_map, industry_map, level="sw_l1", min_components=3) -> pd.DataFrame`（长表：`date, industry, index, n_components`）：
     - 等权：每日成分股收益均值 → 链式累乘；缺行情成分当日剔除并计入 `n_components`；
     - `n_components < 3` → 该行业当日 NaN（不静默、不退化常数）；北交所（920xxx）无行业，排除并在报告披露 359 只覆盖面。
   - `compute_rrg(panel, benchmark, ratio_lb=220, mom_lb=60, smooth=20) -> pd.DataFrame`：
     - `RS = index / benchmark × 100`；`RS-Ratio = (100 × RS / RS.shift(220)).rolling(20).mean()`；`RS-Momentum = (100 × RS-Ratio / RS-Ratio.shift(60)).rolling(20).mean()`；
     - 预热：`< 239` 行抛 `ValueError`（消息含「RS-Ratio 需 239 行，实际 N 行」）；`< 318` 行抛 `ValueError`（「完整 RRG 需 318 行」）——明确按用户口径；
     - 四象限：`>100 & >100` 领先 / `>100 & <100` 转弱 / `<100 & <100` 落后 / `<100 & >100` 转强；边界（恰好 100）统一归「上方」（`>=100`），注释写明。
   - `select_industries(rrg_day, top_n=6) -> list[str]`：只在「转强」象限内，按到 `(100,100)` 欧氏距离降序取前 6（A 方案）；不足 6 个取实际数量并返回 warning。
2. 新增 `backtest/rotation.py`：`run_industry_rotation(index_panel, rrg, *, start, end, rebalance_days=20, top_n=6, cost_bps=10) -> RotationResult`（`equity_curve / holdings / metrics / benchmark`）；成本口径 = 每次调仓按换手金额收单边 10bp，报告注明「ETF 口径，与股票引擎的印花税口径不同，收益不可直接对比」。
3. 新增 `scripts/measure_rrg_industry.py` + 报告；预热日期与有效区间以实际输出为准写进报告。
4. 按 §3.5 判定：有超额 → G4 通过进 Phase 10；无超额 → 停止 C2，报告写证伪结论。

**预期产出**
- `factors/rrg.py`（聚合 + RRG + 选行业）+ 单测
- `backtest/rotation.py` + `scripts/measure_rrg_industry.py`
- C1 回测报告 + G4 判定（C2 做/不做）

**完成标准**
- [ ] 238/239/317/318 行边界单测（前后差 1 行都要覆盖）
- [ ] 手算小样本对拍（3 个行业合成序列，RS/比值/动量逐值验证）
- [ ] 行业指数聚合与现有 `equal_weight_index` 在「全市场等权」口径下一致（允许 1e-9 误差）
- [ ] C1 回测报告含四指标 + 分年度 + 相对基准超额 + 判定结论 + 前视偏差披露
- [ ] commit `(phase 9) add RRG industry rotation signal and C1 backtest report`

### Phase 10 — 任务 C2（仅当 Phase 9 判定通过）

- 实现方式（推荐，见 §9 Q5）：`BacktestConfig` 增加通用注入字段 `extra_columns: dict[str, pd.DataFrame] | None`（`{列名: date×code 宽表}`），`engine.py` 每日候选拼行时按 `ts/code` 查表注入；`build_factor_rows` 增加同构可选参数，今日信号路径复用同一 helper。属于 40 行内的**通用**扩展，不含 RRG 业务语义。
- 新增 `strategy/rrg_rotation.py::RrgRotationStrategy`：`filter_universe` 全量；`score` 先按 `rrg_quadrant == 转强` 保留，再用现有 `trend_score/fund_score` 打分（不新造打分逻辑）；注册进 `registry.py`（中文名「行业轮动(RRG)」）。
- 降级：RRG 预热不足/行业缺失 → 该策略禁用并在报告 `degraded` 打标，不静默跑。
- 报告：C2 个股版四指标 + 分年度 + 与 C1 行业版/股票基准对比。

**预期产出**
- 引擎通用 `extra_columns` 注入 + 测试
- `strategy/rrg_rotation.py` + 注册 + 单测
- C2 报告（个股版 vs C1 行业版 vs 股票基准）

**完成标准**
- [ ] 注入列在引擎与今日信号两路径数值一致（parity 测试）
- [ ] 预热不足时明确报错/降级，不产生空结果
- [ ] 其他 7 策略输出逐值不变
- [ ] commit `(phase 10) add RRG stock rotation strategy on top of C1`

### Phase 11 — 任务 D：两段式「先选类型 → LLM 发挥」

1. 新增 `strategy/archetypes.py`：
   - `@dataclass(frozen=True) class Archetype`：`key / label / tagline / requires / allowed_weights / allowed_rules / allowed_params / defaults / skeleton / status / note`；
   - `ARCHETYPES`：趋势跟随、动量轮动、均值回归、多因子、小市值、事件驱动（最后一个 `status="pending_data"`、白名单为空、skeleton 仅注释）；
   - `resolve_config(archetype_key, llm_config) -> tuple[dict, list[dict]]`：三层兜底——类型默认值 → 全局默认值（`atr_m_exit=3.0 / atr_n_stop=3.0 / top_n=5`）→ 缺字段 `requires` 时禁用该规则并产 `degraded` 条目（含中文原因）。
2. `codegen.py`：新增 `normalize_config_typed(raw, archetype_key) -> tuple[dict, list[str], list[dict]]`（返回 清洗后 config / 越界键 / degraded），`normalize_config` 保持原签名转调（向后兼容）。
3. `custom.py`：`__init__` 接受 `archetype`/`degraded`；`score()` 增加 `self._disabled_rules` 跳过被禁用规则；`degraded` 挂到实例供报告读取。
4. `signal/report.py`：`SignalReport` 增加 `degraded: tuple[str, ...] = ()`（**带默认值，向后兼容**）；`signal/generator.py` 汇总为「降级：缺少 XX，规则 YY 未计算」。
5. `ui/views/visual_builder.py:157-159` 加 6 类型下拉，切换时套用类型默认参数（`:246-269` 组装 config 时带 `archetype`）；事件驱动类型灰显 + 提示「数据不足（缺历史涨停池/披露日），本轮仅占位」。
6. `ui/strategy_ai.py`：prompt 注入类型白名单与默认参数；结果区展示越界字段中文原因 + degraded 清单；保存配置带 `archetype`。
7. 测试：`tests/test_archetypes.py`（白名单越界、三层兜底、degraded）、`tests/test_custom_degraded.py`（缺列规则禁用、报告可见、旧配置逐值不变）。

**预期产出**
- `strategy/archetypes.py`（6 类型定义 + 三层兜底）
- `codegen` 类型化清洗、`custom` 降级执行、`SignalReport.degraded`
- 两处 UI 改造（类型下拉 + 白名单提示 + 降级展示）
- 单测 + 旧配置逐值回归测试

**完成标准**
- [ ] 6 类型下拉可用；事件驱动明确提示不可用
- [ ] LLM 越界字段被拒且给中文原因，不静默丢弃
- [ ] 缺字段 → degraded，报告与 UI 可见
- [ ] 旧配置（无 archetype）行为逐值不变
- [ ] commit `(phase 11) add strategy archetypes and typed two-stage LLM config`

### Phase 12 — 任务 E：B 档字段补齐（总股本 / 财报披露日）

1. 数据来源（akshare，不新增依赖，项目已用 1.18）：
   - `data/share_capital.py`：
     - `fetch_share_snapshot() -> DataFrame`：一次拉全市场快照（总市值/流通市值/最新价），反推 `total_shares = 总市值 / 最新价`、`float_shares = 流通市值 / 最新价`，落 `data/processed/shares/YYYY-MM.parquet`（列：`code, total_shares, float_shares, total_mv, float_mv, as_of, source`）；
     - `derive_float_shares_history(bars_map, snapshot) -> DataFrame`：流通股本历史 = `amount / (turnover × close)`（由「流通市值 = amount/turnover」精确反推，**不是近似**），逐日落 `shares/history_float.parquet`；
     - `derive_total_shares_history(...)`：`total_shares_t ≈ float_shares_t × (total/float 当期比例)`，落 `shares/history_total.parquet`，`source="proxy"`——**比例变化（定增/回购/解禁）无法回溯**，用途限定为市值分档，不得用于精确市值。
2. `data/disclosure.py`：
   - 拉取 2021Q4~2026Q3 每期预约披露日（`stock_yysj_em` 类接口），写入 `meta.db` 新表 `report_disclosure(code, report_period, announce_date, actual_date, source, updated_at, PRIMARY KEY(code, report_period))`；
   - 缺失时兜底「法定披露截止日」：年报次年 4-30、一季报 4-30、半年报 8-31、三季报 10-31（保守，不可能早于真实公告日）；
   - `as_of_disclosure(code, date) -> date | None`：只返回 `announce_date <= date` 的记录，杜绝未来函数。
3. `data/storage.py`：加 `SHARES_DIR` 常量与 `load_shares(code)` / `load_disclosure(code)`；`updater.py` 每日任务尾部接两个更新（失败只告警不阻断，写进度日志）。
4. 回补脚本 `scripts/backfill_share_capital.py`、`scripts/backfill_disclosure_dates.py`：幂等、限速、`--dry-run`、断点续跑。
5. 消费接口：新增 `data/fields.py`（`as_of_float_mv` / `as_of_total_mv` / `as_of_disclosure`），消费方一律 `.get()` + 缺列降级 + `degraded`。
6. 测试：mock 网络（monkeypatch 模块级 fetch 函数，不真联网）、空值/接口失败/停牌缺行情/日期对齐/比例手算/DB 迁移幂等。

**预期产出**
- `data/share_capital.py`、`data/disclosure.py`、`data/fields.py`
- 两个可断点续跑的回补脚本 + 覆盖率报告
- `meta.db` 新表 `report_disclosure`、`data/processed/shares/` 新数据目录
- 单测（网络全 mock）+ 7 策略逐值回归

**完成标准**
- [ ] 四类数据落盘 + 覆盖率报告（含 353 只北交所无行业、上市不足 240 天的样本占比）
- [ ] 缺失路径有单测，任何情况下不让策略崩
- [ ] 现有 7 策略默认参数输出逐值不变
- [ ] commit `(phase 12) add share capital and disclosure date fields with fallbacks`

### Phase 13 — 集成、文档、交付

1. 全量 `pytest` 复跑，记录通过数（≥ 445 + 新增）与报告。
2. 7 策略「Phase 6 基线 vs 最终」对比表：哪些默认变了、哪些没变、为什么。
3. 同步 `README.md`（新因子/新类型/新字段说明）、`CHANGELOG.md`（按日期分组，追加到 2026-10-29 标题下）、`plan.md`（Phase 状态）、`INSTRUCTION.md`（若新增目录约定）。
4. 按项目规则更新本计划书状态，完成后归档到 `.github/plans/done/`。
5. 向你汇报最终结论（A/B/C 各自的「成立/不成立」+ 默认值是否改动），询问是否合并 `feature/strategy-refresh` → `main`（**不自动合并**）。

**预期产出**
- 全量测试报告 1 份
- 「Phase 6 基线 vs 最终」7 策略对比表
- 文档同步（README / CHANGELOG / plan.md / INSTRUCTION.md）
- 归档后的计划书 + 合并建议

**完成标准**
- [ ] 全绿 + 报告齐全 + 文档同步
- [ ] 不合并主干，等用户指示

---

## 6. 测试策略

| 层级 | 内容 | 判别力要求 |
| --- | --- | --- |
| 单元 | 因子公式手算、NaN 预热、零方差、截面 z-score、RRG 四象限与边界、指标函数、类型白名单/三层兜底/缺字段降级、股本公式、日期可见性 | 每个新测试必须在「实现回退」时失败（参考 `scripts/reverse_check_*.py` 思路，每 phase 至少 1 条反向验证） |
| 对拍 | `test_factor_parity.py` 双向列集 + 末日终值数值（扩展 9 列）；引擎/实盘 RRG 注入列数值一致 | 差一格即失败 |
| 集成 | 小样本合成 bars 跑完整回测（复用现有 `_bars()` 夹具风格）；行业轮动 3 行业合成面板 | 断言持仓与调仓日 |
| 回归 | 默认参数下 7 策略指标与基线 JSON 逐值比对；旧自定义配置行为不变 | 任意漂移即失败 |
| 网络隔离 | 任务 E 全部 mock，测试不依赖外网 | 断网也能跑全量 |

- 测试报告：`tests/reports/<YYYY-MM-DD-HH-mm>.report-<n>.md`，同一分钟从 1 递增、不覆盖。
- 基线：**445 passed 不得下降**；新增测试只增不减。`test_factor_parity.py` 的列集断言属于「随实现升级期望值」，需在报告里写明理由与新旧列集。
- 数据缩减：A/B 先用 600 只样本快速筛查（与旧脚本 `MAX_CODES=600` 口径一致），**最终结论必须全市场**；报告写明缩减比例。

---

## 7. 风险与限制

| # | 风险 | 影响 | 缓解 |
| --- | --- | --- | --- |
| R1 | 收敛因子量纲偏价格/成交量（§2.6） | A/B 结论可能是「高价股偏好」而非收敛结构 | 主口径按文档 + 上 z-score/百分位 + `cv` 诊断列对照，报告披露 |
| R2 | 研报口径是「买行业 ETF」，C1 改买个股属二次改动 | C2 效果不保证，甚至更差 | C1 先证伪；C2 只在 C1 过关后做，且保留「不做」结论 |
| R3 | RRG 预热 318 行 → 有效样本仅约 3.5 年 | 统计显著性弱，易过拟合 | 预注册判定线（§3.5）；报告给逐年超额；不做参数寻优 |
| R4 | 行业快照只有 2026-10（§2.3） | 前视偏差 | 报告固定披露；不用 `incl_date` 剔除 |
| R5 | `MA_240` 预热与数据起点冲突（§2.8） | 前期 NaN 污染结论 | 分区间定义（§9 Q6），统一「可比区间」对比 |
| R6 | 总股本历史为比例外推 | 历史总市值不准（定增/回购无法回溯） | 标 `source="proxy"`；用途限制在市值分档；报告给覆盖率 |
| R7 | akshare 接口不稳定/限速/字段改名 | E 的拉取失败 | 幂等 + 断点续跑 + 失败不阻断每日任务 + mock 测试；必要时降级为「当日快照」 |
| R8 | 两段式改动面跨 4 个模块（custom/codegen/report/UI） | 回归风险高 | 旧配置零 `archetype` 走原路径；逐值回归测试锁死 |
| R9 | `equal_weight_index` 静默退化（§2.9） | RRG 被骗成「零波动」 | 不复用其静默逻辑，自建聚合并断言非常数 |
| R10 | 换手率指标是新定义 | 与其他系统口径不可比 | 定义写进代码注释与报告；同时报笔数/持仓天数 |
| R11 | 事件驱动只有空骨架 | 用户可能误以为可用 | UI 灰显 + 明确文案；报告不产生任何回测数字 |
| R12 | 测试计数「下降」被误判 | 纪律冲突 | 明确：只升级期望值，不删用例；报告注明 |
| R13 | 子进程回测脚本运行时间长（5579 只 × 5 年） | 每轮 A/B 数十分钟 | 600 只筛查 + 全市场终验；显式进度日志 |

---

## 8. 预计文件清单

### 新增（16 个源文件 + 10 个测试 + 本文档 + 报告）

| 文件 | Phase |
| --- | --- |
| `.github/plans/2026-10-29-4-new-strategy-refresh-implementation.md` | 本文档 |
| `src/stock_plan/factors/convergence.py` | 7 |
| `src/stock_plan/factors/momentum.py` | 8 |
| `src/stock_plan/factors/rrg.py` | 9 |
| `src/stock_plan/backtest/rotation.py` | 9 |
| `src/stock_plan/strategy/archetypes.py` | 11 |
| `src/stock_plan/strategy/rrg_rotation.py` | 10（条件） |
| `src/stock_plan/data/share_capital.py` | 12 |
| `src/stock_plan/data/disclosure.py` | 12 |
| `src/stock_plan/data/fields.py` | 12 |
| `scripts/measure_factor_ab.py` | 6 |
| `scripts/measure_rrg_industry.py` | 9 |
| `scripts/backfill_share_capital.py` | 12 |
| `scripts/backfill_disclosure_dates.py` | 12 |
| `tests/test_convergence_factor.py` / `test_ma_ratio_factor.py` / `test_rrg.py` / `test_industry_rotation.py` / `test_archetypes.py` / `test_custom_degraded.py` / `test_share_capital.py` / `test_disclosure_dates.py` / `test_metrics_annual.py` / `test_extra_columns_engine.py` | 7–12 |
| `tests/reports/<ts>.report-<n>.md` × 每 phase | 6–13 |

### 修改

| 文件 | 改动 |
| --- | --- |
| `src/stock_plan/strategy/builtin.py` | `build_factor_rows` 加 9 列；趋势策略可选 `conv_weight` |
| `src/stock_plan/backtest/engine.py` | 预计算/候选行同步 9 列；`extra_columns` 注入；`compose_pvcf` 调用 |
| `src/stock_plan/backtest/metrics.py` | 加 `calc_annual_metrics` / `calc_turnover` |
| `src/stock_plan/strategy/breakout.py` | 可选 `conv_weight` |
| `src/stock_plan/strategy/momentum.py` | `momentum_mode` 三模式；清理重复 `build_factor_rows` |
| `src/stock_plan/strategy/registry.py` | 条件注册 RRG 策略 |
| `src/stock_plan/strategy/custom.py` | archetype / degraded 支持 |
| `src/stock_plan/strategy/codegen.py` | 类型白名单清洗 |
| `src/stock_plan/signal/report.py` | `SignalReport.degraded` |
| `src/stock_plan/signal/generator.py` | 汇总 degraded |
| `src/stock_plan/ui/strategy_ai.py` | 类型选择 + 白名单 + degraded 展示 |
| `src/stock_plan/ui/views/visual_builder.py` | 6 类型下拉 + 默认值应用 |
| `src/stock_plan/data/storage.py` | 新增目录常量与读取 |
| `src/stock_plan/data/updater.py` | 每日字段更新 hook |
| `tests/test_factor_parity.py` | 列集/数值断言升级 |
| `README.md` / `CHANGELOG.md` / `plan.md` / `INSTRUCTION.md` | 文档同步 |
| `data/db/meta.db` | 新表 `report_disclosure`（数据迁移，不动既有表） |

### 删除

- 无独立文件删除。仅 `src/stock_plan/strategy/momentum.py:114-149` 内重复的 `build_factor_rows`（无生产引用）按 §9 Q6 确认后删除或标注 `deprecated`。

---

## 9. 待你确认的 6 个问题（附推荐）

| # | 问题 | 推荐 |
| --- | --- | --- |
| Q1 | 收敛因子量纲（§2.6）：主口径用文档原式（绝对值）+ 诊断列 `cv`，还是直接改用无量纲 `std/mean`？ | **按文档原式做主口径 + 加 `cv` 诊断对照**；策略打分前统一截面 z-score。理由：不擅自改需求，同时用诊断列证明/证伪量纲问题 |
| Q2 | 任务 A 接入：`conv_weight` 默认 0（只加列、A/B 后再定默认），还是直接默认开启？ | **默认 0**，A/B 出结论后 G2 由你拍板。理由：默认行为改动必须可回退 |
| Q3 | 任务 C1 判定线（§3.5）四条是否照用？ | **照用**（年化超额 ≥2pp / 回撤不劣 3pp / ≥3 年正超额 / 年单边换手 ≤12） |
| Q4 | 任务 E 历史总股本：快照 + 流通股本精确反推 + 总/流通比例外推（标 proxy），还是只做当日快照不留历史？ | **前者**，用途限定市值分档并披露精度；不阻塞 C2（C2 只用行业+价格） |
| Q5 | 任务 C2 动态行业过滤：引擎加通用 `extra_columns` 注入（推荐），还是退为「回测起点静态行业过滤」（近似）？ | **前者**（约 40 行 + 测试）；若你想控制改动面，退后者并在报告标注近似 |
| Q6 | 附带确认：(a) A/B 区间分别用 A=2022-03-07 起、B=2022-09-01 起（避开预热）；(b) `momentum.py` 重复 `build_factor_rows` 删除 | (a) **是**；(b) **删除**（无引用、易误用） |

---

## 10. 执行纪律（对齐项目规则）

- 分支：`feature/strategy-refresh`；每 phase 至少 1 个 commit，message 英文 ≤20 词、前缀 `(phase N)`。
- 每 phase 结束：全量 `pytest` → 写 `tests/reports/<YYYY-MM-DD-HH-mm>.report-<n>.md`（含时间/范围/通过失败数/失败原因/数据缩减说明）→ 更新计划书状态 → 提交。
- 文档/注释/CHANGELOG 一律简体中文；改行为同步 `README.md` 与 `CHANGELOG.md`。
- 不引入新依赖；不动 `bars` 既有结构与历史值；不自动合并主干。
- 每完成一个 gate（G2/G3/G4）向你汇报结论并等你确认，再继续下一 phase。

---

## 11. 预计工作量与排队

| Phase | 内容 | 预计 |
| --- | --- | --- |
| 6 | 基线 + 脚手架 | 0.5 天 |
| 7 | 任务 A | 2 天 |
| 8 | 任务 B | 0.5 天 |
| 9 | 任务 C1 | 3 天 |
| 10 | 任务 C2（条件） | 2 天 |
| 11 | 任务 D | 3 天 |
| 12 | 任务 E | 3 天 |
| 13 | 集成文档 | 1 天 |
| 合计 | | 约 15 人日（C2 不计则为 13） |
