# 回测与策略对比性能优化（Phase 14–17）

> 状态：待审阅（2026-10-29）
> 分支：`feature/strategy-refresh`（当前 HEAD `ac09f64`）
> 前置：Phase 6–13 已全部完成并提交；本轮不改变任何策略口径与回测结果。

## 0. 目标与范围

### 0.1 目标

把「回测页 / 策略对比页」的等待时间从分钟级降到十几秒到两三分钟级，**不改变任何一个回测数字**。

| 场景 | 现状（实测） | 目标 |
| --- | --- | --- |
| 单策略回测：全市场 × 1 年（页面默认） | ~115 s + 加载 16 s | ≤ 45 s |
| 单策略回测：全市场 × 5 年 | 496 s | ≤ 150 s |
| 策略对比：7 策略 × 1 年 | ~13 min | ≤ 3 min |

### 0.2 不做（本轮明确排除）

1. 不改进回测口径：滑点、佣金、印花税、成交时点、`_pool_cutoff`、财务前视修正一律不动。
2. 不改策略打分公式、不做参数寻优、不新增因子。
3. 不引入新依赖（不加 numba、joblib、cython）。
4. 不做跨进程并行（Windows 下 bars_map 序列化成本高于收益，收益不确定）。
5. 不做面板化长表重构（记忆体峰值过高，云端 2 GB 轻量模式会崩）——留作候选方案 C。
6. 不改默认窗口/默认抽样：改变默认值等于改变用户看到的结论。
7. 择时层（极端/熊市降仓或空仓、收紧买入条件）**不属本轮性能范围**；但下一对话开工前先做一次零代码「择时体检」，口径与预注册验收见 §9（附节，不占 Phase 14–17 工期）。

## 1. 基线测量（本机，2026-10-29 06:45–06:52）

| 项目 | 数值 |
| --- | --- |
| 装载 `Storage().load_market_maps(start=2021-12-06, end=2026-10-28, lead_days=180)` | 15.7 s，5379 只 |
| `run_backtest(趋势跟随, 2022-03-07~2026-10-28)` | 496.3 s，1888 笔，1126 个净值点 |
| 折算：每「股票×交易日」 | 约 74 µs |
| cProfile（2026 年初至今 196 日，含 profiler 开销） | 485 s；`Series.__getitem__` 121.9 s、`DataFrame.__getitem__` 141.0 s、`fast_xs` 52.6 s、`isinstance` 49.2 s |

### 1.1 三个根因

1. **逐日逐股单行抽取**：主循环里 `factor_index[code].iloc[p]` 共约 606 万次，每次构造一个 Series 并做索引校验，实测约 74 µs/次，占单次回测约 1/3。
2. **因子预计算每策略重复**：`compute_technical` + 收敛因子 5 列 + `MA_L/close` 4 列，单次约 30 s；对比页 7 个策略各算一遍，白费约 3 分钟。
3. **逐日扣分明细建表**：`builtin.py::_log` 每天构建一次长表，profile 中占约 7%（真实耗时占比约 2–3%），非主因但可顺手降级。

## 2. 模块划分

| 模块 | 职责 | 交互 | 状态 |
| --- | --- | --- | --- |
| `src/stock_plan/backtest/engine.py` | 新增私有 `_FactorTable`（列名 → numpy 数组 + 日期 int64 数组 + 行指针），主循环改为数组取值；新增可选 `prepared` 参数供多策略复用 | 被 `run_backtest` 内部使用；对 `compare.py` / `backtest.py` 保持原函数签名兼容 | 修改 |
| `src/stock_plan/data/storage.py` | `load_market_maps` 的 parquet 读取改为有界线程池（8 并发），保持返回顺序与内容不变 | 被所有回测/信号入口调用 | 修改 |
| `src/stock_plan/ui/views/compare.py` | 行情与因子预计算用 `st.cache_resource` 共享；每个策略结果单独 `st.cache_data`；`st.status` 显示「正在跑 k/7」 | 调 `engine.prepare()` + `run_backtest(..., prepared=...)` | 修改 |
| `src/stock_plan/ui/views/backtest.py` | 复用同一份 `cache_resource` 数据与预计算 | 同上 | 修改 |
| `src/stock_plan/ui/views/visual_builder.py` | 同上（自定义策略回测） | 同上 | 修改 |
| `src/stock_plan/strategy/builtin.py` | `_log()` 增加 `enabled` 开关：默认不建明细表，需要明细时才记录 | 被 `score_with_penalties` 调用 | 修改 |
| `tests/test_engine_perf_parity.py` | 新旧实现候选行逐列对拍（合成数据，含停牌/缺列/预热不足） | 新增 | 新增 |
| `scripts/measure_factor_ab.py` | 支持 `--max-codes 0`（全市场）用于 5 年基线对拍 | 被回归流程调用 | 修改 |

## 3. Phase 拆分与完成标准

### Phase 14 — 引擎主循环 numpy 化（唯一需要小心的一步）

步骤：
1. `engine.py` 新增 `_FactorTable`：从 `factor_index[code]` 一次性抽出 `dates(int64)` 与 `values(float64 二维矩阵)`、`column_pos(dict)`；`filter_universe` / `equal_weight_benchmark` 仍用原 DataFrame。
2. 主循环把 `last = factor_index[code].iloc[p]` 替换为矩阵行 view；`mom_ret`、`high20`、`volume` 直接用列下标取值；候选 dict 字段名、顺序、类型完全保持。
3. 候选 dict 里的 `pd.notna(...)` 改为 `math.isnan` / `numpy.isfinite` 的等价判断（行为必须一致：`pd.notna(None)` 与 NaN 的语义要在测试里覆盖）。
4. `builtin.py::_log()` 加开关，默认关闭明细表（`signal/generator.py` 显式开启，保持今日信号页的明细不变）。

完成标准：
- [ ] `pytest -q` 全绿且总数不低于 **547 passed**
- [ ] `tests/test_factor_parity.py` 双向列集断言与 17 列数值对拍通过（新增 5 列 + 4 列后共 26 列）
- [ ] `tests/test_backtest*.py` / `test_engine_defects*.py` 全部通过且断言数字未改
- [ ] 7 策略 × full/win_a/win_b 与 `tests/reports/2026-10-29-04-12.report-1.json` 基线**逐值一致**（含收益、回撤、笔数、胜率）
- [ ] 单策略全市场 5 年 ≤ 150 s（同机复测）
- [ ] commit：`(phase 14) vectorise backtest inner loop with numpy factor tables`

### Phase 15 — 预计算复用与并行加载

步骤：
1. `engine.py` 新增 `prepare_backtest(bars_map, fund_map, stock_list, config)`：返回含 `factor_tables`、`trading_days`、`benchmark` 的只读结构；`run_backtest(..., prepared=None)` 可复用。
2. 对比页一次 `prepare`，7 个策略复用；`filter_universe` 仍按策略各自执行（池子不同不影响预计算）。
3. `storage.load_market_maps` 改 `ThreadPoolExecutor(8)` 读 parquet，保持错误处理（坏文件跳过）与输出顺序。

完成标准：
- [ ] 预计算复用前后，单策略结果逐值一致（同一基线对拍）
- [ ] 对比页 7 策略 × 1 年 ≤ 3 min（含加载）
- [ ] `load_market_maps` 15.7 s → ≤ 8 s，返回的 `bars_map` 内容逐值一致（抽样 200 只 hash 比对）
- [ ] commit：`(phase 15) reuse prepared factor tables across strategies and parallelise bar loading`

### Phase 16 — UI 缓存与进度反馈

步骤：
1. `compare.py`：`@st.cache_resource` 缓存 `(prepared, stock_list, fund_map)`；每个策略 `@st.cache_data` 单独缓存（键 = 策略名 + 配置指纹 + 区间 + 参数）。
2. 运行时 `st.status` + `st.progress` 显示当前策略；完成后折叠为「耗时统计」。
3. `backtest.py` / `visual_builder.py` 复用同一 `cache_resource`，删除各自重复的加载缓存（保留结果缓存）。

完成标准：
- [ ] 首次运行有可见进度，不再是长时间白屏
- [ ] 同参数二次运行 < 2 s（命中缓存）
- [ ] 只改「初始资金 / 图表选项」等展示参数时不重算回测
- [ ] 单测：缓存键构造、`prepared` 只读约定（对 `bars_map` 做浅拷贝防护）

### Phase 17 — 回归、报告与文档

步骤：全量 pytest；性能报告 `tests/reports/<ts>.perf-backtest.md`（改造前后对比表、机器环境、复现命令）；`CHANGELOG.md` 续 11；`README.md` 补「性能」小节；计划书勾选并归档到 `done/`；G 汇报。

完成标准：
- [ ] 全量测试 ≥ 547 passed
- [ ] 性能报告含「5 年单策略 / 1 年单策略 / 7 策略对比」三行前后对比
- [ ] 7 策略终验数字与 Phase 13 终验报告逐值一致
- [ ] 工作区干净，commit：`(phase 17) document backtest performance optimisation and regression results`

## 4. 测试策略

| 层次 | 内容 | 文件 |
| --- | --- | --- |
| 单元 | `_FactorTable` 索引对齐、缺列、预热不足、NaN/None 语义与 `pd.notna` 等价 | `tests/test_engine_perf_parity.py` |
| 对拍 | 同一合成行情，旧路径（保留为测试内实现）与新路径候选行逐列 `approx(1e-12)` 相等 | 同上 |
| 契约 | `test_factor_parity.py` 列集/数值；`test_backtest*.py` 指标；`test_engine_defects*.py` 缺陷修复不回退 | 既有 |
| 回归 | 7 策略 × 3 窗口对 Phase 6/13 基线 | `scripts/measure_factor_ab.py` + 报告 |
| 性能 | 三场景计时（冷缓存、热缓存） | `tests/reports/<ts>.perf-backtest.md` |

数据缩减说明：本轮不使用抽样做验收（必须全市场对拍），仅性能计时允许在无其他进程干扰时单次运行。

## 5. 风险与限制

| 编号 | 风险 | 后果 | 应对 |
| --- | --- | --- | --- |
| R1 | 浮点求和顺序变化导致末位差异 | 「逐值一致」验收失败，结论可信度受质疑 | 只改取值路径、不改运算顺序；对拍用 `approx(1e-12)`，出现真实差异即回退该步骤 |
| R2 | `pd.notna` 与 `math.isnan` 语义差异（None/NaT/字符串） | 个别股票被误判为有效/无效 | 专门用例覆盖 4 种缺失形态；`_FactorTable` 只处理数值列 |
| R3 | 双份内存（DataFrame + numpy 矩阵） | 云端 2 GB 内存可能 OOM | 预计算后**只保留** numpy 表；`cache_resource` 设上限并记录峰值 |
| R4 | `st.cache_resource` 共享可变对象被下游改写 | 页面间串味、结果不可复现 | 只读约定 + 入口处浅拷贝 `bars_map` 键视图；测试断言不改原对象 |
| R5 | 线程池读取打乱异常处理 | 个别坏 parquet 被静默跳过或抛出行号错乱 | 保留逐文件 try/except 语义，顺序按 `codes` 重排 |
| R6 | 命中缓存的旧结果让人误以为「改参数没生效」 | 用户误判 | 缓存键显式包含策略配置指纹与成本参数；UI 显示「本次为缓存结果」 |

不支持：跨进程并行、GPU、增量回测（数据更新后仍全量重跑）。

## 6. 预计新增/修改/删除文件

新增：
- `tests/test_engine_perf_parity.py`
- `tests/reports/<ts>.perf-backtest.md`、`tests/reports/<ts>.report-N.md`
- `.github/plans/done/2026-10-29-5-new-backtest-performance.md`（归档后路径）

修改：
- `src/stock_plan/backtest/engine.py`（主循环、`prepare_backtest`）
- `src/stock_plan/data/storage.py`（并行读取）
- `src/stock_plan/ui/views/compare.py`、`backtest.py`、`visual_builder.py`（缓存/进度）
- `src/stock_plan/strategy/builtin.py`（`_log` 开关）、`src/stock_plan/signal/generator.py`（显式开启明细）
- `scripts/measure_factor_ab.py`（`--max-codes 0`）
- `CHANGELOG.md`、`README.md`、本计划书

删除：无（不删任何既有逻辑；旧路径若保留则只作为测试内实现）。

## 7. 纪律

- 每 phase 至少一个 commit，格式 `(phase N) <英文说明，≤20 词>`。
- 每 phase 结束跑全量 `pytest` 并写报告到 `tests/reports/`。
- 文档/注释/CHANGELOG 简体中文；改行为同步 README 与 CHANGELOG。
- 结果数字若出现任何变化，停下来报告，不「调测试凑绿」。
- 发现问题直接说明问题 → 后果 → 替代方案。

## 8. 待确认

1. 目标数字（1 年 ≤ 45 s、对比 ≤ 3 min）是否按此验收？
2. Phase 14 完成后是否先给你看一次数字、再继续 15–17（分两批汇报），还是一次做完再汇报？
3. 比较页缓存是否需要「手动清缓存」按钮（数据更新后用）？

## 9. 附：择时层前置体检与预注册验收（不占 Phase 14–17 工期）

> 起因（2026-10-29 讨论）：用户提出「A/B 结论是否受测试区间过短、或含 2026-07 / 2026-10 / 2024-10 这类极端月份影响；是否应对极端/熊市加择时，不产生信号或更谨慎买入」。
> 本节只定义**怎么验证**，不预设结论；真正的择时层若要做，另开计划书，不塞进本计划 Phase 14–17。

### 9.1 先修正一个口径不一致（必须先说清）

Phase 6–13 的全部 A/B 与基线，跑的都是 `BacktestConfig.market_timing` **默认 False**（引擎默认值）。
但 UI 回测页、策略对比页传的是 `market_timing=True`。
→ 也就是说：**我们报告里的「基线数字」是「未开择时」口径，用户日常在页面上跑出来的是「开择时」口径。** 两者不可直接比较。任何后续讨论必须先统一，否则结论错位。

### 9.2 现有择时实现的真实能力（`src/stock_plan/backtest/engine.py`）

| 位置 | 事实 |
| --- | --- |
| `engine.py:52` | `market_timing: bool = False`，注释「大盘低于 MA20 时仓位减半」 |
| `engine.py:223-232` | 大盘代理 = 候选池内股票的**等权收益指数**（`regime.equal_weight_index`），再取 `rolling(20).mean()` 作 MA20 |
| `engine.py:489-493` | 只在**新买入**时把 `budget *= 0.5`；**不减持已有持仓、不清仓** |

→ 现实现是「弱择时」：熊市里仓位降低但一直有底仓，且已持仓回撤完全没有保护。**它不能被当成「极端市场清仓」的等价物。** 若要「清仓/按目标仓位降仓」，需要在引擎里增加「目标仓位」参数（默认 None，保证不改变现有结果），属于新功能，不在本计划范围。

### 9.3 体检实验（T0，10–15 分钟，零代码改动）

| 项 | 口径 |
| --- | --- |
| 策略 | 7 个内置策略（趋势跟随 / 动量 / 均值回归 / 突破 / 价值投资 / 段永平-高质量 / 李录-长期确定性） |
| 区间 | `win_a`：start 2022-03-07、end 2026-10-28、lead_days 400 |
| 抽样 | `max_codes=800`（实际约 769 只；报告中标注缩减比例） |
| 档位 | `market_timing=False` / `True` 两组，共 14 个 arm |
| 工具 | `scripts/measure_factor_ab.py --spec`（spec 的 `config` 字段可直接传 `market_timing`） |
| 产出 | `tests/reports/<ts>.timing-health.{md,json}`：总表（总收益/年化/最大回撤/夏普/单边年换手/笔数/胜率）+ 分年度 + 「开－关」差值列 + 错过的上涨清单 |

判读只允许三种结论：**帮忙 / 帮倒忙 / 无差别**（按 9.5 的 T-1~T-3 量化），**不做参数寻优、不改阈值**。

### 9.4 必须一起看的「错过的上涨」（等权基准实测，5378 只、61 个月）

| 月份 | 等权基准月收益 | 说明 |
| --- | --- | --- |
| 2024-01 | **−18.79%** | 区间最差月（微盘踩踏），任何择时都应覆盖到 |
| 2022-04 | −14.79% | 2022 熊市主跌段 |
| 2026-03 | −9.80% | |
| 2026-07 | **−7.69%** | 用户点名的极端月 |
| 2026-10 | **−7.33%**（截至 10-28） | 用户点名的极端月，未走完 |
| 2024-06 | −7.90% | |
| 2024-09 | **+23.53%** | 区间最好月；若「连续下跌→空仓」在 9 月初触发，代价极大 |
| 2024-10 | **+8.48%** | 月初剧烈波动但整月收涨 → 「极端=清仓」的反例 |
| 2022-05 | +11.00% | 熊市中的急反弹，最容易被「趋势过滤」错过 |

结论性观察：**「极端」不等于「下跌」**——2024-10 是「波动极端但收涨」，2024-09 是「单月暴涨」。因此规则应按「趋势 + 广度 + 波动」组合判定，绝不能按单月涨跌正负。

### 9.5 预注册验收口径（体检为正、且后续真做择时层时，逐条卡）

| 编号 | 条件 | 备注 |
| --- | --- | --- |
| T-1 | 最大回撤相对同策略 `no-timing` 改善 ≥ 3pp | 主目标 |
| T-2 | 年化收益相对 `no-timing` 下降不超过 2pp（有提升更好） | 防止「空仓换平安」 |
| T-3 | ≥3 个完整年度中，≥2 年相对 `no-timing` 更好 | 拒绝单年主导 |
| T-4 | 年单边换手 ≤ 12 次 | 与 C1 同口径，防成本吃掉收益 |
| T-5 | 必须列出「因降仓/空仓错过的最大 5 个月度涨幅」，并给出机会成本数字 | **硬性**，缺此项报告不合格 |
| T-6 | 阈值与规则预注册后不得按结果修改；若修改必须重跑并标注「探索性」，不得写成默认 | 防过拟合 |
| T-7 | 样本外：2026-11 起至少 3 个月影子跟踪（或末 6 个月前视隔离段）通过后才可设为默认 | 门槛 |

同时明确：择时是**仓位层**，不能用它「复活」已判定不通过的因子方向（Phase 7 收敛因子、Phase 8 动量方向、Phase 9 RRG C1）。两者结论在报告里分开写，不合并成一个「策略变好了」。

### 9.6 对已完成 Phase 6–13 的表述修正（不改判定）

1. `docs/策略刷新工作报告-2026-10-29.md`、Phase 6/13 基线报告加脚注：**结论均在 `market_timing=False` 口径下得出**；若 9.3 体检显示差异显著，需在报告正文点明，必要时重跑关键对比（重跑属探索性，不覆盖原判定）。
2. Phase 9 RRG 的结论措辞由「C1 被证伪」改为「**C1 未通过（结论不稳定：2026 单年 −4.23pp 主导；扣除 2026 后 2023–2025 平均 +2.28pp/年，恰在 +2pp 门槛附近）**」。判定与动作不变：**仍不做 C2**。
3. Phase 7 / Phase 8 的因子方向结论不用改：趋势跟随在各 `conv_weight` 档位一致变差 2.3–2.7pp、动量三模式相差 20pp 以上，属方向性问题，与窗口无关。

### 9.7 与 Phase 14–17 的排序

1. 下一对话开工：先跑 9.3 体检（10–15 分钟），出 `timing-health` 报告，判断「帮忙 / 帮倒忙 / 无差别」。
2. 无论结果如何，接着按 Phase 14 → 17 做性能改造（两者无依赖；体检不修改任何代码）。
3. 若体检为「帮忙」，再单开一份择时层计划书（含目标仓位参数、规则定义、9.5 验收），**不并入本计划**。
