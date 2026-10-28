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
