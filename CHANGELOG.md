# 变更日志（Changelog）

> 按日期分组，从新到旧排列。

## 2026-10-29（续 13）：复筛 Phase 20 — 全市场 150 arm 矩阵与双层判定（候选层无一通过）

按 [复筛计划书](.github/plans/2026-10-29-7-new-strategy-rescreen-and-timing-tiers.md) 完成 Phase 20：
全市场 150 arm（144 股票 + 6 行业轮动）、预注册双层判定、月度极值与「未能降仓」披露。全量测试 654 → **656 passed**。

- **矩阵**：7 策略基线 + 收敛 5 变体 ×2 策略 + 动量 3 模式 + RRG，三窗口（W1 训练 / W2 验证 / W3 全期）×
  三口径（off / weak / tier），全市场 5379 只不抽样、`jobs=4`，144 arm 全部成功（`tests/reports/2026-10-29-17-36.rescreen-matrix.{json,md}`）；
  RRG 6 arm 另行跑（该 runner 无「弱择时」口径，故为计划的 9 → 实际 6，`2026-10-29-17-45.rescreen-rrg.json`）。
- **判定结论**（`tests/reports/2026-10-29-18-45.rescreen-judgement.md`）：
  ① 候选层 W1 各选 1 个进 W2——收敛族 `pvcf/突破`（+3.33pp、4/4 年正）、动量族 `ma_ratio_blend/动量`（+11.77pp、3/4）；
  ② W2 判定**无一通过**：`pvcf/突破` 超额 +1.24pp（C-1 门槛 +2.0 ❌），`ma_ratio_blend/动量` 超额 +10.20pp 但 C-3 ❌（2025 下半年 -11.4%、2026 才反超，属单年主导）；
  ③ 择时层：`weak` 对两组选中候选都「帮忙」（回撤 +3.57 / +7.98pp，年化 +9.00 / +9.78pp）；`tier` 回撤改善很大（+20.03 / +16.06pp）但 M-3 只有 1/2 年更好，按预注册规则判「无差别」；
  ④ 全矩阵 26 组信息性汇总：`weak` 帮忙 5、帮倒忙 3；`tier` 帮忙 1（基线动量 +34.54pp），改善集中在 2026 下跌段（月度极值：2026-07 躲开 +13~+20pp，同时 2026-04/09 错过 -5~-8pp）；
  ⑤ RRG 行业层 W1 超额 +0.46pp（C-1 ❌）→ 按预注册规则不做 C2，与 Phase 9 一致。
- **新增脚本**：`scripts/build_rescreen_specs.py`（生成 144 arm spec）、`scripts/measure_rrg_batch.py`（RRG 6 arm）、
  `scripts/judge_rescreen.py`（双层判定 + 月度极值 + 降仓披露 + 数据指纹）；`measure_factor_ab.py` 增加 `--dump-equity`。
- **引擎**：`BacktestResult.timing_failures` 记录降档日「未能降仓」残差（跌停/停牌/T+1/不足一手/整手取整），
  明细回放 12 arm 验证加记录器后六项指标与矩阵逐值一致；`convergence.py` 变体扩到 6 个（补 acf/trcf）。
- **数据冻结**：跑批前后指纹（bars 5579 / meta.db / 200 只抽样）逐项一致，跑批期间数据未变。
- 报告：实验 `tests/reports/2026-10-29-18-45.rescreen-judgement.md`；测试 `tests/reports/2026-10-29-18-46.report-1.md`。
- **结论口径**：本轮没有任何候选通过预注册门槛，因此**不改任何默认值**（择时档位保持默认关闭）；
  下一轮若要继续，应先扩大验证窗口或做真正样本外验证，而不是继续在同一 W2 上调参。

## 2026-10-29（续 12）：复筛 Phase 18–19 — 全市场基线冻结 + 双均线三档择时（默认关闭）

按 [复筛计划书](.github/plans/2026-10-29-7-new-strategy-rescreen-and-timing-tiers.md) 完成 Phase 18、Phase 19。
新增能力**默认关闭**，21 arm 与 HEAD 基线逐字段 **0 差异**；全量测试 637 → **654 passed**。

- **Phase 18 全市场基线**（`(phase 18) add full market baseline for strategy rescreen`）：数据指纹与原记录不一致
  （`data/update.log` 显示 16:34 增量更新 5575 只），按计划纪律改为「同数据重跑基线」——7 策略 × W1 训练 / W2 验证 / W3 全期，
  `max_codes=0`（载入 5379 只、有效参与 5182）、`jobs=5`，21 arm 全部成功，
  产物 `tests/reports/2026-10-29-17-08.rescreen-baseline.{json,md}`。结论：三个窗口 7 策略全部跑输等权基准
  （基准 W1 +21.95% / W2 +8.31% / W3 +32.67%）；W2 有 3 只转正但均低于基准；年单边换手 14.0–22.2 倍，成本拖累 5.0–8.0pp/年。
- **Phase 19 三档择时**（`(phase 19) add dual ma timing tiers with default off`）：新增 `src/stock_plan/factors/timing.py`
  （`close ≥ MA20` 满仓 / `close < MA20` 半仓 / `close < MA60` 且 MA20 五日斜率向下 空仓；预热不足按满仓）；
  `BacktestConfig.timing_tier`（默认 `"off"`，非法值中文报错）；引擎每日**真实降仓**（按比例卖出、100 股整手、
  跌停停牌跳过、T+1 豁免、买入侧只封顶不追买）；行业轮动 `run_industry_rotation(..., timing_tier=)` 同口径；
  `conv_variant`（pcf/vcf/pvcf/multi，默认 pcf，与 Phase 7 逐值一致）；回测 / 策略对比 / 可视化构建三页新增
  「大盘择时档位」下拉（默认关闭），缓存键纳入档位；旧「弱择时」保留并在文案中标注为旧行为。
- **默认关闭证明**：21 arm（7 策略 × full/win_a/win_b，`max_codes=800`）与 Phase 14 同数据 HEAD 基线
  `2026-10-29-08-52.phase14-head-baseline.json` 逐字段 **0 差异**（metrics / annual / turnover / benchmark_return /
  codes / equity_points，容差 1e-12，产物 `tests/reports/2026-10-29-17-20.phase14-parity.json`）；
  同一全市场 spec 在实现中途与完成后两次跑批，21 arm 六项指标逐值相同。
- 新增测试 `tests/test_engine_timing_tiers.py`（17 例：档位边界、斜率相等不算向下、0%/50% 档、跌停不卖、整手、
  不足一手不卖、T+1、预热、`off` 零副作用）、`tests/test_rotation_timing.py`（4 例）、`tests/test_backtest_pages.py` +2 例 AppTest 冒烟。
- 报告：实验 `tests/reports/2026-10-29-17-30.phase18-19-timing.md`；测试 `tests/reports/2026-10-29-17-30.report-1.md`。
- **仍未做**：Phase 20 复筛矩阵（153 arm，含 `weak`/`tier` 口径，预计 1.0–1.7 小时）需用户批准后执行；
  本阶段只证明「能力可用且默认不改变行为」，不构成「择时是否帮忙」的结论。

## 2026-10-29（续 11）：回测性能改造 Phase 14–17 — 同数据逐值不变，等待时间降到秒级

按 [性能计划书](.github/plans/done/2026-10-29-5-new-backtest-performance.md) 完成四阶段改造，**不改任何回测数字**：
同一数据快照上 7 策略 × 3 窗口（21 arm）改造前后逐字段 **0 差异**，全量测试 547 → **637 passed**。

- **Phase 14 引擎内层数组化**（`befad62`）：因子表改为「列名 → numpy 矩阵 + int64 日期 + 行指针」，
  逐日逐股不再构造 pandas Series；`builtin.py::score_with_penalties` 增加 `enabled` 开关（默认关闭，
  今日信号路径显式打开）避免回测每日建扣分明细表；新增 `tests/test_engine_perf_parity.py` 内嵌旧实现对拍。
  5 年全市场单次回测 449.4 s → 约 82–87 s。
- **Phase 15 准备层 + 存储并行加载**（`9ae34ab`）：新增 `prepare_backtest()` / `PreparedBacktest`（只读快照），
  `run_backtest(..., prepared=...)` 跨策略复用因子表；`load_market_maps` 用有界线程池 + pyarrow 窗口下推，
  15.7 s → 6.4 s；对比页 7 策略 × 1 年 154.9–155.5 s（目标 ≤3 min）。`scripts/measure_factor_ab.py` 支持 `--max-codes 0`（全市场）。
- **Phase 15b Walk-Forward 与今日信号共享准备层**（`183d641`）：`walk_forward(..., prepared=...)` 整段区间只准备一次，
  改造前后 OOS 输出 0 数值差异，耗时 113.12 s → 约 25–27 s；今日信号 `generate_signals_with_report(..., prepared=...)`
  「多周期并存」准备一次、3 次打分，154.5–158.7 s → 84.5–86.0 s（约 1.8–1.9×），信号与 report 全字段一致。
- **Phase 16 UI 缓存与进度**（`60da69c`）：新增 `src/stock_plan/ui/backtest_cache.py`（进程内 prepared/结果缓存、
  缓存键含策略配置指纹与数据版本、命中提示「本次为缓存结果」）；对比页逐策略进度条「正在跑 k/N」；
  回测页 / 对比页 / 可视化拼装页新增「🧹 清除缓存」按钮；命中缓存全市场 7 策略二次运行 0.004 s。
  新增 `tests/test_backtest_cache.py` / `tests/test_backtest_pages.py`。
- **Phase 17 终验与文档**：21 arm 与 Phase 14 同数据 HEAD 基线逐字段对拍 0 差异；性能报告
  `tests/reports/2026-10-29-10-25.perf-backtest.md`（五场景前后对比、机器环境、复现命令）；README 新增「性能」小节；
  计划书标记完成并归档到 `.github/plans/done/`；分支 `feature/strategy-refresh` 未合并 `main`。
- **数据口径偏差**：计划书 §1 的 04:12 旧基线在 06:49–08:02 每日数据任务刷新后不可复现（任何代码都复现不了），
  故全部验收改为「同一数据快照上 HEAD 引擎基线 vs 新代码」逐字段对拍，已记录于计划书 §1.2 与 Phase 14 报告。
- **已知偏差**：「1 年单策略冷启动 ≤45 s」未达成（实测 100.6–103.6 s：加载 6.2 + 全市场因子表准备 79.4–82.2 + 回测 15.1），
  原因是因子表构建是与窗口长度无关的全市场固定成本；共享 prepared + UI 缓存后用户感知为秒级（命中 0.004 s）。
- 全量测试：**637 passed / 0 failed**（`tests/reports/2026-10-29-09-58.report-1.md`）；未引入新依赖；
  未改回测口径 / 策略公式 / 默认窗口；未做跨进程并行与面板化重构。

## 2026-10-29（续 10）：策略刷新 Phase 13 — 集成、文档与终验

收尾阶段：**不改行为，只做终验与文档同步**，并给出「基线 vs 最终」的逐值对比证据。

- 全量测试复跑：**547 passed / 0 failed**（87.30 s，`tests/reports/2026-10-29-06-05.report-1.md`），较改造前基线 445 增加 102 例，无下降。
- 新增对比文件 `tests/reports/2026-10-29-06-02.phase13-compare.md`：Phase 6 基线（`2026-10-29-04-12.report-1.json`）与最终状态
  （`2026-10-29-06-02.phase13-final.json`）在 full 窗口（2021-12-07~2026-10-28）对 7 个策略逐项对比，**收益 / 年化 / 回撤 / 夏普 / 笔数 / 胜率全部逐值相同**；
  另以 win_a 窗口程序化对拍 7/7 相同。原因是 A/B/C 三块的结论均为「不成立」，默认参数一律保持原口径。
- 文档同步：`README.md` 新增「股本与财报披露日（B 档字段）」小节并更新策略刷新进度表；`INSTRUCTION.md` §7 新增 9 条关键技术决策
  （只加列不改口径、收敛 / 动量 / RRG 三块默认关闭的理由、类型白名单、东财直连与离线复用、总股本 proxy、披露日兜底、回补必须 `exclude_st=False`）；
  `plan.md` §16 新增策略刷新进度行。
- 计划书 `.github/plans/done/2026-10-29-4-new-strategy-refresh-implementation.md` 标记完成并归档到 `.github/plans/done/`；分支 `feature/strategy-refresh`
  **未合并 `main`**，等用户指示。
- 新增 `docs/策略刷新工作报告-2026-10-29.md`：面向非开发者的全过程复盘（大白话 → 每个决策的备选与利弊 → 量化代码思路逐段解释），供日后复盘与交接使用。
- 本轮结论（诚实版）：A 收敛因子、B 动量方向修正、C RRG 行业轮动在本数据上均未通过预注册判定线，保留为可选能力、默认关闭；
  D 两段式与 E 字段补齐为纯增量能力，已可用。真正的主要矛盾是**日频换手 13–21 倍/年（年成本拖累约 5.8%–7.6%）与熊市段缺择时**，
  建议下一阶段优先用现成的 `factors/regime.py` + `BacktestConfig.market_timing`。

## 2026-10-29（续 9）：策略刷新 Phase 12 — B 档字段补齐（总股本 / 财报披露日）

补齐调研报告 §4.2 的 B 档字段。**日线表结构未改动，只新增数据目录与新表；现有 7 策略默认输出逐值不变。**

- 新增 `src/stock_plan/data/share_capital.py`：东财 `push2` 直连抓全市场快照（总市值 / 流通市值 / 最新价），反推
  `total_shares = 总市值 ÷ 最新价`、`float_shares = 流通市值 ÷ 最新价`；历史流通股本由 `amount ÷ (turnover × close)` 逐日精确反推；
  历史总股本按当期「总 / 流通比例」外推并标 `source=proxy`。落盘 `data/processed/shares/`（月快照 0.2 MB + 两张历史长表约 228 MB）。
- 新增 `src/stock_plan/data/disclosure.py`：拉取 2021Q4–2026Q3 共 20 期预约 / 实际披露日，写入 `meta.db` 新表 `report_disclosure`；
  缺失时退回法定披露截止日（年报 / 一季报 4-30、半年报 8-31、三季报 10-31）；`as_of_disclosure()` 严格只取
  `announce_date <= date` 的记录，杜绝未来函数。
- 新增 `src/stock_plan/data/fields.py`：`as_of_total_shares / as_of_float_shares / as_of_total_mv / as_of_float_mv / as_of_disclosure`
  统一消费接口，缺数据返回 `None` + 中文 `degraded` 说明，不抛异常、不静默。
- `data/storage.py` 新增 `SHARES_DIR`、`load_shares()`、`load_total_mv()`、`load_float_mv()`、`load_disclosure()`；
  `data/updater.py` 新增 `refresh_aux_fields()` 并在每日更新尾部接入（披露日只在法定截止前 30 天内刷新，失败只告警不阻断）。
- 新增回补脚本 `scripts/backfill_share_capital.py`、`scripts/backfill_disclosure.py`：幂等、`--dry-run`、`--skip-fetch`（离线复用快照）、`--json`。
- 覆盖率（详见 `tests/reports/2026-10-29-05-53.phase12-fields.md`）：股本快照 5571 只 / 历史 5570 只、635.7 万行；北交所 353/353 全覆盖；
  历史流通市值与快照比值中位 0.997；披露日 99,191 行 / 5223 只 / 20 期（98.2% 有实际披露时间）。
- 修复：首次回补因 `exclude_st=True` 漏抓 197 只 ST 股（已改为 `exclude_st=False` 重跑）；`load_snapshot()` 误把历史表当月快照；
  无效日期曾错误回退到快照次日（改为直接降级）。
- 测试：新增 44 例（`test_share_capital` 15 / `test_disclosure` 17 / `test_fields` 12），全量 **547 passed / 85.60s**；
  7 策略 win_a 区间回归与 Phase 6 基线逐值一致。
- 已知限制：历史总股本为比例外推，只能用于市值分档（定增 / 回购 / 解禁不可回溯）；东财接口在本机间歇被网络层重置，重跑用 `--skip-fetch`。

## 2026-10-29（续 8）：策略刷新 Phase 11 — 两段式「先选类型 → LLM 发挥」

落地调研报告 §6 的两段式改造，并把「缺字段静默跳过」改成「禁用规则 + 降级留痕」。**未改动任何现有策略的打分公式与默认值。**

- 新增 `src/stock_plan/strategy/archetypes.py`：六种策略类型画像（趋势跟随 / 动量轮动 / 均值回归 / 多因子 / 小市值 / 事件驱动），
  每类带「骨架 + 可用字段白名单 + 类型默认参数」；`resolve_config()` 做三层兜底（类型默认值 → 全局默认值 → 缺字段禁用规则 + `degraded`），
  `rule_columns()` 按配置动态推断依赖的均线列（如 `dev_ma=10 → ma10`）。事件驱动类型 `status="pending_data"`：白名单为空、`is_ready=False`，
  只占位不产出可回测配置。
- `src/stock_plan/strategy/codegen.py`：新增 `normalize_config_typed(raw, archetype_key, *, available_columns)`，返回
  `(config, issues, degraded)`；`normalize_config` 签名不变、内部转调，旧调用方无需改动。新增 `mv_pct`（小市值分位）规则。
- `src/stock_plan/strategy/custom.py`：`CustomStrategy` 接受 `archetype` / `disabled_rules` / `degraded`；`score()` 中每条规则先过
  `_rule_enabled()`——缺依赖列时**禁用该规则并记录 `degraded`**，不再静默跳过；`degraded_messages()` 供报告读取。
  旧配置（不含新键）打分逐值不变。
- `src/stock_plan/signal/report.py`：`SignalReport` 增加 `degraded: tuple[str, ...] = ()`（带默认值，向后兼容），并写入
  `to_dict()` 与 `to_facts_text()`；`signal/generator.py` 从策略实例汇总降级说明；今日信号页以 warning 展示「今天少算了哪几条」。
- UI 两处：可视化拼装页顶部新增「策略类型」下拉（切类型即用该类型默认值重置表单；事件驱动类型回测按钮禁用且不给套用/保存）；
  AI 分析页改为「先选类型 → 生成」，prompt 只注入该类型的字段白名单，结果表按白名单过滤，越界字段与降级逐条列中文原因。
- 测试：新增 `tests/test_archetypes.py`（10 例）与 `tests/test_custom_degraded.py`（6 例），全量 **503 passed / 14.22s**，
  报告见 `tests/reports/2026-10-29-05-34.report-1.md`。

## 2026-10-29（续 7）：策略刷新 Phase 9 — RRG 行业轮动 C1（结论：无超额，C2 不做）

实现调研 Top 3 的第③项的第一段（C1：行业层可证伪验证）。**未改动任何现有策略/引擎行为。**

- 新增 `src/stock_plan/factors/rrg.py`：`build_industry_index()` 按申万一级**等权**聚合行业指数
  （每日成分收益均值 → 链式累乘；成分 < 3 只置 NaN 不补 0；北交所 `920xxx` 无行业，排除）；
  `compute_rrg()` 按文档口径算 `RS = index/benchmark×100`、`RS-Ratio = (100×RS/RS.shift(220)).rolling(20).mean()`、
  `RS-Momentum = (100×RS-Ratio/RS-Ratio.shift(60)).rolling(20).mean()`，预热不足**显式抛
  `ValueError`**（RS-Ratio 需 239 行、完整 RRG 需 318 行，信息含实际行数）；`select_industries()`
  只在「转强」象限里按到 `(100,100)` 欧氏距离降序取前 6（A 方案，不足不补并告警）。
- 新增 `src/stock_plan/backtest/rotation.py`：`run_industry_rotation()` 行业轮动回测，
  信号严格早于持仓日（防未来函数）、每 20 交易日调仓、单边 10bp 成本、按年累计真实成本、
  合成交易记录复用 `calc_metrics`（9 键契约）；报告标注「ETF 口径，不可与股票引擎收益直接对比」。
- 新增 `scripts/measure_rrg_industry.py`：无头脚本，输出 JSON + Markdown（覆盖度、分年度、
  §3.5 四条预注册判定、象限分布、前视偏差披露、复现命令）。
- 新增 `tests/test_rrg.py`（13 例，覆盖 238/239/317/318 边界、手算对拍、与 `regime.equal_weight_index`
  一致性、因果性、成本、四象限边界、错误路径）。
- **C1 结果（2022-12-29 ~ 2026-10-28，925 个交易日，5026 只股票 / 31 个申万一级）**：
  轮动 总收益 +37.15% / 年化 +8.59% / 最大回撤 −31.17% / 夏普 0.47 / 年单边换手 5.16 次；
  基准（31 行业等权）+39.73% / +9.12% / −29.50% / 0.52。
  §3.5 判定：C1-1 年化超额 **−0.53pp < +2.0pp ❌**；C1-2 回撤差 −1.67pp ✅；C1-3 3 年正超额 ✅；
  C1-4 换手 5.16 次 ✅ → **C1 不通过，按预注册规则停止 C2（Phase 10 不做），RRG 行业轮动证伪。**
- 诚实说明：① 变差主要来自 2026 年（超额 −4.23pp），2023–2025 虽为正超额但补不回；② 行业分类只有
  `classify/2026-10` 单月快照回溯、`incl_date` 无剔除时间，**存在前视偏差——带前视偏差仍无超额，
  是更强的证伪信号**；③ 持仓并未长期空仓（平均 5.5 个行业/次、仅 1 次空仓），不是空仓拖累造成的假象；
  ④ 研报口径是买行业 ETF，我们算的是行业等权指数，真实可交易收益只会更低。
- 报告：`tests/reports/2026-10-29-05-20.rrg-c1.{md,json}`；测试报告：`tests/reports/2026-10-29-05-24.report-1.md`
  （全量 `pytest` **487 passed**，较 474 增加 13 例、无下降）。

## 2026-10-29（续 6）：策略刷新 Phase 8 — 动量方向修正（结论：变差，保留原口径）

实现调研 Top 3 的第②项：把「裸涨幅动量」换成研报口径 `MA_L / close` 系（L = 20/60/120/240），
在动量策略上做三模式 A/B。**默认行为未变（仍是 `ret20` 的 `mom_ret`）。**

- 新增 `src/stock_plan/factors/momentum.py`：`ma_ratio_columns()`（`ma_ratio_L = MA_L / close`，
  **越小越强**，注释写死方向）、`ma_ratio_strength()`（`1 − ratio`，越大越强）、
  `cross_section_percentile()`（只用当日截面、NaN 保留，避免未来函数）。
- `strategy/builtin.py::build_factor_rows()` 与 `backtest/engine.py` 因子预计算 / 候选行同步加
  `ma_ratio_20/60/120/240` 四列；`tests/test_factor_parity.py` 数值对拍补这 4 列，并把 NaN 判定改为
  「两边同为 NaN 视为一致」（200 行样本上 240 窗口两边都未成形，属同一种退化口径）。
- `MomentumStrategy` 新增 `momentum_mode ∈ {ret20, ma_ratio, ma_ratio_blend}`（默认 `ret20`）：
  `ma_ratio` 用 `ma_ratio_strength(60)` 打分，`ma_ratio_blend` 用四窗口强度的截面百分位均值、
  NaN 视为中性 0.5；两种新模式仍映射到 0~30 分，与旧加分项同量纲。默认模式与 Phase 6 基线
  `win_b` 逐指标完全一致（收益/年化/回撤/夏普/胜率/笔数）。
- 顺带删除 `strategy/momentum.py` 里重复的死代码 `build_factor_rows`（用户已确认），
  该文件现只含策略类。
- A/B 报告 `tests/reports/2026-10-29-05-11.phase8-ab.{md,json}`（3 arm，区间 2022-09-01~2026-10-28，
  基准 +37.11%）：`ret20` −32.91%（年化 −9.15%，回撤 −41.16%）／`ma_ratio` −53.26%（−16.71%／−55.88%）／
  `ma_ratio_blend` −45.99%（−13.77%／−51.79%）。**按 §3.4 判定线两种新口径均显著变差 → 保留 `ret20`
  为默认**，参数保留可选不删除。
- 诚实说明：问题不在因子符号，而在用法是**单边做多**——涨幅与 `MA/close` 都是在买近期强势股，
  样本期 2023–2024 强势股整体跑输（动量反转期），换度量改不了方向；2025 单边上涨年 `ma_ratio`
  反而更好（+11.99% vs +7.16%）。改进方向应是择时/市场状态过滤（`factors/regime.py` 与
  `BacktestConfig.market_timing` 至今未被任何策略使用）或降低换手，而非继续换动量度量。
- 新增 `tests/test_momentum_factor.py`（13 例）；全量 `pytest` **474 passed**。测试报告：
  `tests/reports/2026-10-29-05-15.report-1.md`。
- 修正 `scripts/measure_factor_ab.py` 脚手架缺陷：`defaults` 曾把 `start/end/lead_days` 硬编码为
  `win_a/400`，导致 `--start/--end/--lead-days` 被静默忽略（Phase 8 首跑因此跑错窗口，已作废重跑）。

## 2026-10-29（续 5）：策略刷新 Phase 7 — 均线收敛因子 A/B（结论：不成立，默认关闭）

实现调研 Top 3 的第①项：均线收敛因子（PCF 收盘 / VCF 成交量 / PVCF 截面合成 / ACF 成交额 /
TRCF 换手率），并做同策略、同区间、同配置的加/不加 A/B。**未改变任何策略默认行为。**

- 新增 `src/stock_plan/factors/convergence.py`：`convergence_series()`（`−log(1+std(MA_1..MA_120))`，
  `std` 用 `skipna=False`，避免均线未成形时把「算不出的窗口」当成「收敛」）、`convergence_columns()`
  （四列 + 无量纲诊断列 `conv_pcf_cv`）、`add_conv_columns()`、`cross_section_zscore()`、
  `compose_pvcf()`（截面 z-score 后相加，零方差截面取 0 中性值）；`MA_1` 即原始价格。
- 接入两条路径：`strategy/builtin.py::build_factor_rows()` 与 `backtest/engine.py` 的因子预计算 /
  候选行 / 打分前合成；`tests/test_factor_parity.py` 的列集双向断言与数值对拍各补 5 列，
  保证「回测 → 今日信号」两条路径同值（单股票零方差截面两边都为 0）。
- 趋势跟随、突破策略新增 `conv_weight` 参数（默认 0，`score += conv_weight × z(conv_pcf)`）；
  默认 0 时 `win_a` 上收益/年化/回撤/夏普/胜率/笔数与 Phase 6 基线**逐指标完全一致**。
- A/B 报告 `tests/reports/2026-10-29-04-41.phase7-ab.{md,json}`（8 arm，`conv_weight ∈ {0,0.1,0.2,0.3}`）。
  **按预注册判定线 G2 结论：不成立，默认关闭。** 趋势跟随三档权重全部变差（年化 −0.7~−0.9pp、
  回撤劣化 2.3~2.7pp）；突破仅 0.2 档年化 +0.69pp（<0.5pp 门槛外仅勉强）、回撤改善 1.13pp（<2pp），
  且 2025 上涨年反而变差、三档无单调性 → 判为样本噪声。参数保留为可选开关，不删除。
- 新增 `tests/test_convergence.py`（10 例）；`tests/test_metrics.py` 保持 6 例；全量 `pytest`
  **461 passed**（Phase 6 的 459 + 2）。测试报告：`tests/reports/2026-10-29-04-42.report-1.md`。
- 附带结论（诚实披露）：样本内 7 策略全部跑输同期等权基准，真正的共同短板是**日频换手 16–18 倍/年**
  （估算年成本拖累 4–5pp），远大于本轮因子的贡献。建议后续把「调仓频率 / 持有期 / 成本假设」单独立项，
  而不是继续加因子。

## 2026-10-29（续 4）：策略刷新 Phase 6 — 基线固化与 A/B 测量器

进入 `feature/strategy-refresh` 实施阶段。Phase 6 不改任何策略行为，只把「现状」变成可复跑的数字，
供后面五块改造（收敛因子 / 动量修正 / RRG / 两段式 / 字段补齐）做 A/B 对照。

- `src/stock_plan/backtest/metrics.py` 新增 `calc_annual_metrics()`（按自然年切资金曲线，交易按平仓日
  归属年份）与 `calc_turnover()`（单边换手 = 买入成交额 ÷ 期间平均总资产，另给双边与分年度）；
  `calc_metrics()` 签名与 9 个返回键保持不变。
- 新增 `scripts/measure_factor_ab.py`：A/B 测量器。父进程只发 spec、子进程真跑回测并回 `RESULT {json}`，
  避免「改完源码在主进程重跑拿到内存旧代码」的假结果；支持 `--baseline` / `--spec` / `--jobs`，
  自动生成报告（总表 + 分年度 + 换手 + 基准超额）。
- 新增 `tests/test_metrics.py`（6 例）；全量 `pytest` **459 passed**（基线 445 + 6 个 metrics 用例 +
  8 个收敛因子用例；后者代码属 Phase 7 先行落地）。
- 基线 `tests/reports/2026-10-29-04-12.report-1.{json,md}`：7 策略 × 3 窗口共 21 arm。**7 个策略全部
  跑输同期等权基准**（full 基准 +26.88%，最好的段永平 −16.59%），日频单边年换手 13–21 倍 —— 这是后续
  改造必须直面的前提：不先压成本/换手，单加因子很难翻身。
- 口径修正：`full` 窗口起点 2021-10-01 → **2021-12-07**。日线自 2021-09-06 起、策略硬过滤要求 ≥60 根
  K 线，2021-12-06 只有 59 根 → 候选池为空、资金曲线 0 点（第一版 7 个 arm 全 `None`）；已加守护：
  worker 遇空资金曲线直接失败退出，不再把 None 静默写进报告。

## 2026-10-29（续 3）：策略选型调研（只调研，不改代码）

默认 7 个策略同质化、缺整块市场风格。本轮按新分支 `feature/strategy-refresh` 做一次外部策略调研，
产出选型文档给用户拍板；**本轮不动任何策略代码、不跑回测**。

- 新增 `docs/策略选型调研-2026-10-29.md`（833 行）：六大类 39 个候选逐条评估（趋势跟随 8 / 动量轮动 6 /
  均值回归 7 / 多因子 10 / 小市值 3 / 事件驱动 5，另含低星抽奖 10 个），每个候选给「一句话赚什么钱 /
  核心规则 / 我们能算吗 / A 股适配度 / 落地难度 / 与现有策略重叠度 / 风险」。
- 数据资产实测（写进文档 §4）：日线 `turnover` 覆盖率 100%；`流通市值 = 成交额 ÷ 换手率` 实测对得上
  （平安银行 2198.9 亿 / 茅台 16710.7 亿 / 宁德 9832.2 亿 / 比亚迪 2884.5 亿）；申万一级行业映射已落盘
  （5220 只、31 类）；涨停池字段完整但**只有 2 天**，事件驱动类因此无法回测。
- Top 3 建议（按投产比）：① 均线收敛因子 PCF/TRCF（一个 `rolling.std()`）② 动量因子方向修正
  （现有 `mom_ret` 按研报实测方向是反的）③ RRG 行业轮动。筹码因子、小市值因成本/稳健性降级，理由写进 §5.5。
- 新增 `.github/plans/2026-10-29-2-new-strategy-research-and-selection.md`（计划书，Phase 1–5 全完成）。
- 子代理交付后做了交叉核对与补写：新增 3.4 候选 11「天风多因子指数增强」（20 因子 + MAD 去极值 / 行业市值中性化 /
  Löwdin 正交 / IC-IR 加权流水线，数据缺但骨架可抄）、补齐筹码因子 4 个算子公式与「A=1.0 隐含日换手 100%、须重标定」
  的坑、Alpha158 结构（9 K 线 + 4 归一价 + 29 算子×5 窗口）与 Alpha101 残缺情况（82/101 有实现，18 个只剩注释）。
- 未改代码；全量 `pytest -q` **445 passed**，与基线一致。

## 2026-10-29（续 2）：国内云服务器部署（方案 C）

Streamlit Cloud 在国内打开要跨境，首屏 20 秒以上。新增「在自己国内的云服务器上跑一份」的完整物料；
服务器只做展示，数据仍由本机发布、服务器定时拉。

- 新增 `deploy/`（7 个文件）：`bootstrap.sh`（幂等引导：系统依赖 → 北京时间 → 建 `stockplan` 用户 →
  uv 装 Python 3.13 → venv 装 `requirements.txt` → 建数据目录 → 装 systemd → 首次拉快照）、
  `pull_snapshot.py`（6 小时冷却 + 文件锁 + 重试 3 次 + `--dry-run`/`--force` + 可选重启）、
  `stock-plan.service`（`0.0.0.0:8501`、`Restart=always`、`PYTHONPATH=src`）、
  `stock-plan-snapshot.service`/`.timer`（每交易日 17:10 拉快照）、`secrets.toml.example`、
  `README.md`（选机型到排障、续费迁移的 10 节手册）
- 新增 `src/stock_plan/ui/startup_flags.py`：`is_service_mode()` / `remote_strategy_sync_enabled()` /
  `local_publish_enabled()` / `describe()`，只认 `1/true/yes/on`。服务器形态下默认跳过共享策略同步
  （`raw.githubusercontent.com` 国内常超时）并禁止本机自动发布；不设变量时本机行为完全不变
- `src/stock_plan/ui/app.py`：两处启动副作用改由上面两个开关判定（原 `if not is_cloud()` 判断移除）
- 新增 `.gitattributes`：`*.sh` / `*.service` / `*.timer` 强制 LF，避免 Windows 检出带 CRLF 到 Linux 上
  出现 `bad interpreter`
- 新增 81 项测试（`tests/test_startup_flags.py` 34 + `tests/test_deploy_assets.py` 47）：开关矩阵、
  unit 关键字段、脚本参数、无 CRLF、`--dry-run`、冷却/强制/重试/锁超时/重启告警、`app.py` 集成回归。
  全量 `pytest` **445 passed**，报告 `tests/reports/2026-10-29-00-26.report-1.md`
- `README.md` 新增「国内云服务器部署（方案 C）」章节，原 Streamlit Cloud 章节加交叉引用
- 未做（按 2026-10-29 拍板）：本机 scp 推送脚本（改由服务器自己拉 GitHub Releases）、Docker 方案、访问鉴权

## 2026-10-29（续 1）：修「快照同步失败」+ 快照一次带上全部数据

用户反馈三个问题：① 点「同步到最新快照」报「恢复失败」；② 长时间不用的访问者是不是也得先点增量更新；
③ 能不能所有数据都直接走云端快照。

- **修 bug（恢复必然失败）**：`validate_manifest()` 只认本地打包名 `bars.zip.partNN`，而 Release
  资产名是 `bars-<时间戳>.partNN`（GitHub 拒收 `.zip` 结尾的资产），于是线上快照**每次都**被判
  「parts[0].file 应为 bars.zip.part00」而拒绝。现改为 `PART_NAME_RE` 正则同时接受两种写法，
  并补了序号连续、重名、路径穿越校验（`src/stock_plan/data/snapshot.py`）。
- **快照 v2：行情 + 分类/情绪/行业/公告一起下发**。原先快照只有 `bars/`，新访问者同步完还得点一次
  增量更新才有分类/情绪页面。现在 zip 内布局 `bars/*.parquet` + `classify/`、`market/`、`universe/`、
  `stock_basic.parquet`、`trade_calendar.csv`、`announcements.db`（多约 4MB），manifest 增
  `format: 2` 与 `extras` 计数；恢复时按分组原子替换，旧版「只有行情」的快照仍兼容（平铺布局）。
- 新增 `_extract_snapshot()`：解压到同卷暂存目录 → 逐组 rename 覆盖 → 校验各组文件数 → 删旧备份，
  任一步失败整体回滚，不会留下"半新半旧"数据；新增 `missing_extras()` 供页面提示旧快照缺哪些分组。
- 页面文案同步：普通用户第一屏「📦 更新数据：同步最新快照」；恢复成功提示列出实际恢复了哪些数据
  （行情 N 只 + 行业分类 + 市场情绪 + 公告库），旧快照则提示缺哪些分组。
- 新增 8 项测试（`tests/test_snapshot.py`）：Release 分卷名接受、路径穿越/重名拒绝、v2 打包含 extras、
  v2 恢复全覆盖且清旧目录、旧平铺快照兼容、文件数不符回滚、`missing_extras`。全量 `pytest` **362 passed**。

## 2026-10-28（续 13）：数据更新页分「维护者 / 普通用户」两套排版

用户反馈：非本机用户只需要行情快照，增量更新按钮摆在最显眼的主要位置，其他用户不懂、也不会用。

- 新增 `maintainer_mode()`（`src/stock_plan/ui/views/update_center.py`）：显式环境变量
  `STOCK_PLAN_MAINTAINER=1/0` 优先，未设置时按 `is_cloud()` 推断——本机 = 维护者，云端 = 普通用户。
- 普通用户视图：第一屏就是「📦 更新行情数据：同步最新快照」大按钮 + 一句「不需要执行增量更新」，
  并附一个「为什么普通用户用快照就够？」的说明折叠；增量更新收进折叠的「🔧 维护者功能」，
  区首写明「非管理者无需点击」；分类/情绪/公告抓取、灾备恢复等维护动作同样不再出现在主流程。
- 维护者视图（本机）保持原样：增量更新仍是主按钮，快照入口改为折叠的「从云端快照恢复（灾备）」。
- 数据落后时的提示按身份分流：维护者看到「建议立即增量更新」，普通用户看到「建议点上方『同步到最新快照』」。
- 新增 `tests/test_update_center.py`（14 项）：环境变量判定真假值/优先级/默认推断，以及用 AppTest
  钉住两种排版下按钮的出场顺序与警示文案。
- 验证：全量 `pytest` **354 passed**。

## 2026-10-28（续 12）：统一按北京时间算「今天」，修云端期望交易日慢一天

用户反馈：本机（Windows，北京时区）页面正确，云端同一时刻「期望最新交易日」却停在 10-27。
查下来是容器时区问题，不是数据问题：

- **根因**：云端容器跑 UTC。`latest_expected_trade_date()` 用 `datetime.now()` 读容器本地时间，
  北京时间 16:00–24:00 之间容器还停在当天 15:00–15:59，判定「未过 16:00」→ 期望日退回上一交易日，
  每天慢 8 小时窗口。页面「体检时间 2026-10-28T15:07:51」正好是北京 23:07，差 8 小时整。
  同一问题还潜伏在交易历缓存有效期、情绪快照日期目录、公告抓取区间、快照 manifest 时间戳上。

改法：新增 `src/stock_plan/data/clock.py`（`CN_TZ` 固定 UTC+8、`to_cn()`、`now_cn()`、
`today_cn()`、`stamp_cn()`），data 层与 UI 默认区间全部改走它：`updater`（交易历判期、
增量/全量区间、股票列表当日标记、更新日志时间戳）、`freshness`（`scanned_at` 带 +08:00）、
`snapshot`（`created_at` / `restored_at` / 分卷戳、`manifest_age_days` 用 aware 时间比较）、
`market_snapshot`、`classification`、`announcements`、`delisted`、`fetcher`、`ui/widgets`（市场温度窗口）。
naive 入参仍视为北京时间，既有调用与测试语义不变。

页面同步把「体检时间」标注为（北京时间）。新增 `tests/test_clock.py`（10 个用例，含
「UTC 容器 15:07 必须给出当天」「UTC 17:30 跨日」「manifest 年龄不受容器时区影响」）。
结果：全量 `pytest` 330 → **340 passed**；测试报告见 `tests/reports/2026-10-28-23-32.report-1.md`。

## 2026-10-28（续 11）：修「从云端快照恢复」读旧分支导致云端行情停在 9 月

用户反馈云端「行情最新交易日 2026-09-03」，点恢复快照也没变化。查下来是两个入口各写一套地址：

- **根因一**：`update_center._restore_from_snapshot()` 写死 `raw_base_url()`，读孤儿分支
  `data-snapshot`；该分支最后更新仍是 2026-09-04（`manifest.created_at 2026-09-04 02:50:51`，
  5553 只），而发布端 2026-10-15 起已改到 GitHub Releases —— 于是每次"恢复成功"都把云端
  数据退回 9 月，页面还提示完成。
- **根因二**：`bootstrap_if_needed()` 只要 bars ≥ 100 就直接返回，云端已有 5553 个旧文件，
  冷启动再也不会从 Release 追平，唯一入口就是那个错地址按钮。

改法（P0）：`snapshot.py` 新增 `snapshot_restore_bases()`，启动恢复与手动恢复共用同一份
地址表（Release 优先、旧分支兜底、去重）；恢复成功后 `freshness.refresh_marker()` 重扫并
在页面显示恢复后的行情最新交易日。
安全阀（P0.5）：`restore_from_url()` / `restore_with_fallback()` 增加 `max_age_days`，
手动恢复按 `SNAPSHOT_MAX_AGE_DAYS = 7` 校验 `manifest.created_at`，旧快照直接判失败并继续
尝试下一个地址 —— 宁可报错也不静默拿旧数据覆盖。启动恢复（本地无数据）不卡年龄，有旧快照
好过没有。
结果：全量 `pytest` 324 → **330 passed**；测试报告见 `tests/reports/2026-10-28-23-23.report-1.md`。

## 2026-10-28（续 10）：分支4 收尾打磨（删冗余 / 补买卖点 / 对比页结论化）

`分支3` 上线后回头通读图表代码，揪出四处「能用但不干净」的地方，在 `分支4` 一次改完；
用户外出前授权「分支4 的改动你可以全权决定」。计划书：`.github/plans/done/2026-10-28-3-new-branch4-polish.md`。

- **主图只剩一个工厂**：`widgets.equity_curve_fig()`（收益率口径）与回测页私有的 `_hero_fig()`
  （收益率 + 金额双口径）逻辑重复，回撤带、基准换算各写一遍。合并为
  `widgets.backtest_hero_fig(result, *, mode, trades, height)`，回测页、可视化构建页、
  样张脚本都调它；删掉 `equity_curve_fig()` 并加测试守护，防止「改一处忘另一处」复发。
- **补上已批准但漏做的功能**：Phase 1 曾定「买卖点标注默认关、可开关」，代码里没有。
  现在回测页主图右侧有「标注买卖点」开关（默认关），打开后 ▲ 买（红）/ ▼ 卖（绿），
  hover 显示代码与成交价；140 笔交易时密度仍可读，因此默认不打扰。
- **策略对比页能说结论了**：指标对比表新增「超额%」（策略总收益 − 同期等权基准），
  每列最优值上底色（收益类红、回撤类绿），表格上方一句话说清谁跑赢基准多少；
  全部跑输时明确写「本次全部策略跑输基准」，不糊弄成「表现良好」。
- **清理重复文案**：持仓诊断页「单股回测样本单一」原来在图注和 caption 各打一遍，删掉后者。
- **`chart_style`**：新增 `BEST_UP` / `BEST_DOWN`（表格最优值底色，只染背景不改字体色）、
  `add_level_line(..., value_text=...)`（金额类参考线不再被迫显示两位小数）。
- 修一个静默错误：pandas 3.0 的 `Styler.highlight_max` 语义与 2.x 不同，会让「每列最优值」上错格子；
  改为逐列 `Styler.apply()`，并加测试锁死着色格子数。
- 结果：全量 `pytest` 由 309 增至 **324 passed**；测试报告见
  `tests/reports/2026-10-28-18-12.report-1.md`；样张 `.dsh-tmp/charts/shot-branch4-markers.png`。

## 2026-10-28（续 9）：图表观感重做（Phase 0/1）+ 信号可解释与 LLM 解读（Phase 0–3）

两件事同时在 `分支3` 上推进，各自一个计划书、分阶段提交。

### A. 图表可视化升级（`.github/plans/2026-10-28-1-new-chart-visual-refresh.md`）

- **新增 `src/stock_plan/ui/chart_style.py`**：把配色、字号、间距、轴格式收进一个纯函数模块
  （近黑画布 / 面板色 / 红涨绿跌 / 右侧纵轴 / 无网格 / 统一 hover 与 `uirevision`），
  之后所有图都从这里取样式；换技术栈时只改这一处。
- **`src/stock_plan/ui/widgets.py`**：新增 `chart_config()`（导出文件名带图名）、
  `render_chart()`、`multi_line_fig()`；`equity_curve_fig()` 改为走 `chart_style`。
- **回测结果页重排**（`src/stock_plan/ui/views/backtest.py`）：策略曲线 + **等权基准曲线**叠加、
  回撤阴影、四指标卡、`st.tabs` 收纳「总览 / 月度 / 交易 / 归因」，不再把 5 图 3 表一路铺开；
  月度收益改真日期轴、柱顶只标出 |值| ≥ 2% 的月份；热力图去标题、只留刻度与 `%` 后缀。
- **`src/stock_plan/backtest/engine.py`**：`BacktestResult` 新增 `benchmark`，新增
  `equal_weight_benchmark()` 复用 `factors.regime.equal_weight_index()`，旧调用不受影响。
- **`scripts/preview_charts.py`**：不启动 Streamlit、不连数据库就能生成「改造前 / 改造后」样张 HTML，
  用固定种子合成数据；改观感时先看样张，不反复跑全 A 股回测。
- 结果：全量 `pytest` 从 210 增至 **229 passed**（`tests/test_chart_style.py` 15 项 +
  `tests/test_backtest_benchmark.py` 4 项）。
- **Phase 2 其他页面统一**：`src/stock_plan/ui/views/compare.py` 的资金曲线改用
  `widgets.benchmark_equity_fig()`（等权基准乘初始资金，与资产曲线同量纲，不再画成贴底直线）；
  `src/stock_plan/ui/views/portfolio.py` 的价位线图下沉为 `widgets.price_level_fig()`——
  买入价 / 止损 / 止盈 / 做T低吸 / 做T高抛五条线统一由 `chart_style.add_level_line()` 出；
  可视化构建器的收益曲线也改走 `render_chart()`。全 `ui/` 去掉 Streamlit 1.63 已弃用的
  `use_container_width`（表格统一 `width="stretch"`），`multi_line_fig()` 的基准不再占用系列色板
  （第一条策略回到品牌绿）。新增 `scripts/preview_compare_portfolio.py` 出对比页 / 持仓页样张。
- **修一个真 bug**：`apply_base_layout()` 原先整体赋值 `annotations`，会把 `add_level_line()`
  加上的价位标签整批冲掉；改成先保留图上已有注释。
- **新增 `tests/test_chart_views.py`**（9 项守护）：视图层不许硬编码颜色、不许再用
  `use_container_width`、`st.plotly_chart` 只能出现在 `widgets.py`，并断言基准换算与五条价位线齐全。
- **Phase 3 回归与收尾**：README 新增「图表规范」一节（样式单一真源、通用图只从工厂出、
  页面专用图也不许硬编码颜色、改观感先看样张）；全量 `pytest` **309 passed**；
  测试报告归档到 `tests/reports/2026-10-28-18-03.report-1.md`；计划书移入 `.github/plans/done/`。
  至此图表升级 Phase 0–3 全部完成。

### B. 信号可解释 + LLM 并发加固（`.github/plans/2026-10-28-2-signal-explain-report.md`）

问「为什么是这 5 只」以前只能看每只股票的一句理由。现在有四层回答，且**不新增首屏耗时**。

- **Phase 0 LLM 基础设施**（`6183fae`）：`llm/budget.py` 按 token 预算裁剪 prompt 并记录丢掉了什么；
  `llm/cache.py` 把 `(任务, 输入哈希)` 写进 `data/cache/llm/`（TTL 7 天，`LLM_CACHE_DISABLE=1` 可关）；
  `llm/client.py` 加 `Semaphore(2)` 并发闸门、429/超时退避一次、`ChatResult(text, error, cached, usage)`，
  并把共享的 `last_error` 改成 `threading.local`（多人同时用不会串错误提示）。
- **Phase 1 事实层**（`222d4fb`）：`factors/filter.py` 新增 `filter_universe_explained()`，
  把「被硬过滤挡掉的理由」保留下来（ST / 无日线 / 次新 / 停牌 / 流动性不足，各带触发数值）；
  `strategy/builtin.py` 新增 `score_with_penalties()`，逐条记录扣分（追高 −100、趋势向下 −100、
  偏离过大 −30/−50、RSI 过热 −20），`score()` 数值逐位不变；`signal/report.py` 是新的事实层数据类；
  `signal/generator.py` 新增 `generate_signal_report()`。
- **Phase 2 页面即时对比**（`6734816`、`34ed5a8`）：今日页新增默认折叠的「🔍 为什么是这几只」区块——
  入选者与紧随 3 名的对比表、分差瀑布图、扣分逐条清单、被硬过滤挡掉的样本表；
  `generate_signals_with_report()` 让信号表与报告**共用一次打分**（不再算两遍 47 秒）。
- **Phase 3 LLM 深度解读**（`0870b7b`）：新增 `llm/signal_narrative.py`，把事实清单压成
  ≤1400 token 的 prompt，要求模型只输出 JSON；**数字闸门**——模型只能原样引用事实清单里的数字，
  编造的数字会被拦下并回退确定性模板；`risks` 写成对象也兼容。缓存命中会在页面上写「缓存于 x 分钟前」。
  真实调用（GLM-4-Flash，免费档）一次约 21 秒、1311+269 token。
- 结果：全量 `pytest` 从 229 增至 **300 passed**。**Phase 4（回归报告、README、归档）本次完成。**

### 踩坑记录（两件值得留的）

- 以 `#` 开头的柱子标签会被 plotly.js 当数字（`"#6 300908"` → `6300908`），整条 x 轴退化成
  `6M/7M` 数值轴——分类标签别以 `#` 开头，并显式 `update_xaxes(type="category")`。
- `LLMClient(api_key="", ...)` **不会**降级成 mock：构造函数会用 `.env` 的 key 兜底，
  测试里想造 mock 必须显式 `client.mock = True`，否则真的会打到线上、烧掉免费额度。

## 2026-10-28（续 8）：归档旧副本，并拔掉脚本里写死的旧路径

用户认可「清理现场」后执行五件事。

- **改名而不是删除**：`D:\Vscode\stock plan` → `D:\Vscode\stock plan-已停用-20260905`
  （该副本最后一次改动是 2026-09-05）。副本原样保留，随时能翻回去查历史，
  但它已经不可能再被误用：其 `.venv\Lib\site-packages\stock_plan.pth` 写的是旧路径，
  改名后 `import stock_plan` 直接 `ModuleNotFoundError`——**用错解释器会当场露馅**，
  而不是再产生「app 在这边、报错在那边」的误导性 ImportError。
- **`.cmd` 包装脚本改用相对锚点**：`scripts/run_update.cmd`、`scripts/publish_snapshot.cmd`
  原本硬编码 `cd /d "D:\Vscode\stock plan\Text"`，旧副本一改名就彻底失效；
  改为 `cd /d "%~dp0.."`（脚本所在 `scripts\` 的上一级＝项目根），换副本、换盘符都不用再改。
  附带说明：现役计划任务**并不**调用这两个 `.cmd`——实测 `\StockPlan\Daily_Update` 与
  `\StockPlan\Daily_Publish` 的 XML 动作是
  `powershell -File "D:\DeepSeekHarness\案例区\stock plan\scripts\daily_job.ps1"`，
  `WorkingDirectory` 也指向案例区副本；这两个 `.cmd` 属历史包装，留着供手工复现。
- **清残留进程**：结束 06:10 起挂着的孤儿探测进程（`data\logs\_probe_fin.py`）。
- **摘掉 3 个指向旧副本的 git worktree**：`D:\Vscode\stock plan.worktrees` 下的
  `branch2`、`branch2-updates-and-improvements`、`fix-cloud-oom`，其 `.git` 文件仍指向
  已归档副本的 `.git/worktrees\...`，`git -C` 进去直接报
  `gitfile does not point to a valid repository`，`git worktree remove` 也拒绝
  （`is not a .git file`）。做法是删目录 + `git worktree prune -v`：三个分支
  （`branch2`、`agents/branch2-updates-and-improvements`、`分支1`）及其提交全部保留，
  现在 `git worktree list` 只剩主工作区；空目录 `branch1-data-update`、
  `error-handling-in-chatbot` 与空的 `stock plan.worktrees` 一并删除。
- **把 490 MB 的嵌套旧副本挪出项目目录**：`案例区\stock plan\Text`（19,687 个文件、
  最后改动 2026-09-04、未入版本控制）整体移到
  `D:\DeepSeekHarness\_archive\stock-plan-Text-20260904`；项目目录里不再有第二份
  `src\`/`.venv`/`data\`，也就没有"从项目内部再捡到半旧副本"的可能。

### 验证

- 负向复验（改名前）：用旧副本 venv 跑本副本 `app.py` → 退出码 2、只输出中文说明、
  **无 traceback、无 ImportError**，即上一节的自检按预期拦住。
- 改名后：旧副本 venv 连 `stock_plan` 都 import 不到（`.pth` 指向已不存在的路径），
  误用同样会立刻报错，不会静默跑起一个半旧版本。
- `pytest tests -q`：**210 passed / 7.27 s**（报告见 `tests/reports/2026-10-28-15-34.report-1.md`）。
- 8501 页面 `_stcore/health` = `200 ok`；`import stock_plan` 指向案例区副本。
- 清理后：`git worktree list` 只剩主工作区、`git status` 干净、`import stock_plan` 正常。
- 应用重启后 headless 渲染（`AppTest`）：`exceptions=[]`、`errors=[]`、退出码 0。

## 2026-10-28（续 7）：启动环境自检——用错 venv 不再报「半个路径」的 ImportError

用户贴来一段报错：`ImportError: cannot import name 'market' from 'stock_plan.ui.views'`，
而下面一行却指向 `D:\Vscode\stock plan\src\stock_plan\ui\views\__init__.py`，同时终端里还叠着
`“<” 运算符是为将来使用而保留的` / `ParserError: RedirectionNotSupported`。

### 根因（两条，互不相关）

1. **用错虚拟环境**：本机两个副本各有一个 `.venv`，其 `stock_plan.pth` 只指向自己的 `src`。
   用户用 `D:\Vscode\stock plan` 的 Python 跑了 `案例区` 的 `app.py` → app 文件来自案例区、
   `stock_plan` 包来自 Vscode（旧版本，没有 `views/market.py`）→ ImportError。
   实测 Vscode 副本 `views/` 下只有 13 个模块，确实没有 `market.py`。
2. **把 traceback 粘回 PowerShell 提示符**：PowerShell 把 `+ …`、`from …` 当命令解析，
   于是出现"运算符""关键字"这类与 Python 无关的 ParserError。

### 修法：`app.py` 启动自检

`_checkout_mismatch()` 比较 `Path(__file__).parents[2]`（app 所在副本的 `src`）与
`stock_plan.__file__` 的 `src`，不一致时：终端打印 + 页面 `st.error` 中文说明
（两个真实路径、为什么错、正确启动命令、一行自查命令），然后 `st.stop()`；
再用 `SystemExit(2)` 兜底裸跑场景（否则 `st.stop()` 不中断，仍会抛那个误导性 ImportError）。

### 验证

- 负向（`D:\Vscode\stock plan\.venv\python` 跑本副本 app.py）：输出只有中文说明，**无 ImportError**。
- 正向（本副本 venv）：无任何告警，正常进入启动流程。
- `compileall` 0 错误、`pytest` **210 passed**。
- 文档：`INSTRUCTION.md` 新增 §9 排障（9.1 用错 venv / 9.2 别把报错粘回提示符）+ 1 条决策记录。

## 2026-10-28（续 6）：行情数据全量重建（从 0 清洗）

用户要求「**以防万一，重新做一次数据清洗，从 0 开始重新下载全部股票数据**，以保证目前数据
已经是最新，至少不会缺少 30 天以上」。这次不是增量补洞，而是**清空后全量重拉**。

### 做法

- **先备份再动手**：`data/processed/bars` 整体改名为 `bars_backup_20261028_0615`
  （5553 个 parquet / 298.3 MB），全程不删除；重建期间停掉本地应用，避免页面读到半成品。
- **不复用 `rebuild_history()`**：它只遍历"已缓存的 parquet"，目录一空就什么都不做；
  改用 `updater.fetch_uncached()` 显式传入当天最新全市场列表（5576 只，含北交所 920xxx）。
- **每只重拉近 5 年完整日线**（新浪每次返回整份历史，重拉 5 年与补 30 天成本几乎相同），
  顺带重算财务与 `pe/pb`；整份覆盖写，**不存在新旧因子拼接的跳空**。
- **补回历史行（关键一步）**：新列表取新浪 spot，**停牌股不在列表里** → 4 只只存在于备份库；
  且 5 年窗口起点比备份库首日（2021-09-06）晚 → 35 只比备份少 1~13 行。于是把备份行按
  「同一日期以新数据为准、日期并集」合回：**整份回填 4 只 + 合并 4397 只、补回 143,525 行**。
  不做这一步，回测会悄悄少一段历史。

### 结果（实测）

| 项 | 重建前 | 重建后 |
|---|---|---|
| 行情文件 | 5553 | **5579**（含 4 只停牌/失败股的历史） |
| 总行数 | 6,183,379 | **6,361,180** |
| 最新交易日 | 2026-10-27 | **2026-10-27** |
| 落后 7 天以上 | 344 只 | **7 只**（全部是长期停牌，非漏抓） |
| 抓取耗时 | — | **944.8 秒 / 5576 只（5.9 只/秒）**，成功 5575、失败 1 |
| 财务表 | 5579 行 / pe 5570 / pb 5576 | 重抓后 `updated_at=2026-10-28T06:25` |

唯一失败是 `301139`（`No value to decode`，新浪该代码数据本身异常，与上次增量更新同因）。

### 验证

- `pytest` **210 passed**；`compileall` 0 错误；`acceptance.py --full` **37/37**（13 页 + 健康 200）。
- 数据更新页 AppTest：`行情最新交易日=2026-10-27`、`期望=2026-10-27`、
  `已更新到最新=5568/5579`、`落后 7 天以上=7`。
- 回滚方式：`bars` 改名回去、`bars_backup_20261028_0615` 改回即可（备份保留，未删除）。

## 2026-10-28（续 5）：数据日期体检 + 增量窗口缺口保护

用户问了两件事：**「数据更新页要能确认现在用的是哪一天的数据」**，以及
**「上一次数据好像已经超过 30 天了，现在增量更新还有用吗」**。第二问暴露了一个真实缺陷：
`update_incremental(days=30)` 的窗口是**固定 30 天**，机器停用超过 30 天后增量会显示
"成功"，但旧数据与新抓取之间留一整段空洞 —— 回测凭空少几十天，还查不出来。

### 新增：数据新鲜度体检（`src/stock_plan/data/freshness.py`）

- **问题**：页面原先只显示「本地已缓存股票数」和「期望最新交易日」，两个都不回答
  "我实际用的是哪一天的数据"；且行情/分类/情绪/公告四处数据更新机制不同，只看行情会误判。
- **修法**：新模块 `freshness.py`。`scan_bars()` 32 线程扫 5916 个 parquet（实测 **8.2 秒**），
  算出最新交易日、`current`（最后交易日 == 最新交易日的只数）、`stale_7/stale_30` 与示例代码；
  结果写 `data/processed/bars/_freshness.json` 缓存，平时读缓存（1 秒内），
  `refresh_marker()` 显式重扫。`status()` 汇总行情/分类月快照/情绪日快照/公告库四处日期。
  命令行：`python -m stock_plan.data.freshness [--refresh] [--json]`。
- **口径说明（用测量纠正过一次）**：`current` 最初写成"能读出 date 列就算新"，结果
  5916/5916 恒等、指标毫无信息量；改为"最后交易日等于全市场最新交易日"后是 **5568/5916**，
  剩下 344 只是退市/长期停牌，这才是用户需要看到的事实。
- **页面**（`ui/views/update_center.py::_data_status`）：顶部一行结论
  （`✅ 当前行情数据日期：2026-10-27（已是最新交易日）` 或黄色警示）、四个指标卡、
  一行"其他三类数据"、落后股票说明、`🔍 重新扫描数据日期` 按钮。
- **验证**：`tests/test_freshness.py` 7 项；`AppTest` 实测页面输出
  `行情最新交易日=2026-10-27 / 期望=2026-10-27 / 已更新到最新交易日=5568/5916 / 落后 7 天以上=344`。

### 修复：数据落后超过窗口时，增量更新会自动扩大窗口

- **问题**：`start = today - days` 固定 30 天。若上次更新在 60 天前，只补最近 30 天，
  中间的 30 天永久缺失，而任务日志显示成功。
- **修法**：`updater.update_incremental` 开头读一次本地最新交易日（`freshness.latest_bar_date`，
  读缓存不扫盘），`gap = 今天 - 本地最新`；`gap > days` 时把窗口扩到 `gap + 5` 天
  （+5 天与前一段重叠，合并时天然去重），并 `logger.warning` 记录。
  `stats` 新增 `window_days` / `data_lag_days` 两个字段，页面与日志都能看到实际用了多大窗口。
  更新结束后自动 `refresh_marker()`，页面日期立刻变新。
- **不影响**：没落后时窗口仍是 30 天（不落后还扩窗口，等于每次多抓几十天、白白变慢）。
- **验证**：`pytest` **210 passed**（203 → 210，新增 7 项）；反向验证
  `scripts/reverse_check_freshness.py` **5/5 有判别力**（`current` 口径、缓存复用、缺口保护、
  无条件扩窗口、汇总日期，逐条改回坏版本后对应测试确实 FAIL）；`acceptance.py --full` **37/37**。
- **同时修掉的一个副作用**：新测试最初把 `pytest` 从 6 秒拖到 **59 秒** —— 因为更新结束时
  `refresh_marker()` 扫的是**生产 bars 目录**（5916 个文件 × 4 个用例）。已让 marker 跟着
  `storage.bars_dir` 走（`marker_path(bars_dir)`），测试扫临时目录，耗时回到 **7.2 秒**。

## 2026-10-28（续 4）：删除做T回测、LLM 生成搬进策略拼装、增量更新提速

用户提的三条修正。前两条是"功能位置/取舍"，第三条是**实测后定位的性能问题**：
增量更新慢不是数据量大，而是**每次请求都重新做 TLS 握手**。

### 移除：做T回测页（`backtest/t_trade.py`）

- **决定**：删除整块功能（3 个文件：`src/stock_plan/ui/views/t_backtest.py`、
  `src/stock_plan/backtest/t_trade.py`、`tests/test_t_trade.py`），页面从 14 页回到 **13 页**。
- **为什么删掉是对的**：它是**独立模拟器**（只有日线、只做正T、收盘价成交），与主引擎
  口径不同，放在同一排菜单里容易被当成"同一个回测"；实测在 8 只大蓝筹 × 119 个交易日上
  默认 3% 触发几乎不触发（仅 1 笔、T 贡献 **−0.04pp**），参考价值低于维护成本。
- **没有删的**：`analysis/holding.py` 里的「做T建议」（持仓诊断页在用，是给已有持仓
  的日内建议，与回测无关）；`ui/widgets.py` 的 `board_filter_ui` 等共享组件照旧。
- **验证**：`compileall` 0 错误；`pytest` 203 passed；`acceptance.py` 13 页 AppTest 全过；
  全库检索确认无残留 import。

### 变更：LLM「一句话生成策略」从 LLM 页搬到策略拼装页

- **问题**：原先生成参数在「LLM 智能分析」页，而"拿这套参数跑回测"在「策略拼装」页，
  中间要用户自己抄参数；生成完也没法立刻验证，于是这个功能实际是"看完就忘"。
- **修法**：新建 `src/stock_plan/ui/strategy_ai.py`（两页共用的展示/保存/导出块，
  `render_ai_strategy_section(key_prefix, apply_config=None)`）；策略拼装页顶部加
  「🤖 AI 一句话生成（可选）」展开区，生成后逐项显示「当前默认 → AI 建议值」，
  按钮「把建议值套用到下面的表单」把值写进各滑杆的 `session_state` 并提示已填好。
  LLM 页删掉「策略生成」整个 tab，只保留「解释今天的信号」和「个股消息面分析」。
- **套用时的取舍**：滑杆有步长与上下限，AI 给的 0.62 不能直接塞进 0.5 步长的控件，
  所以按"就近取整到可选值"并**在提示里说明改成了多少**（不静默改）。
- **验证**：端到端套用测试（桩 LLM 返回 ATR 0.5 / 止盈 4.0 / 止损 2.5 / 持有 15 天 →
  四个滑杆值逐一断言）；AppTest 两页 render 无异常。

### 性能：增量更新全市场 30+ 分钟 → 本地约 6 分钟

- **先测量**：每只股票要发 **3 个** HTTP 请求（历史 blob 82KB / outstanding_share 3.2KB /
  前复权因子 1.6KB），时间分布 **网络 88.6% / V8 解密+pandas 11.4% / parquet 1%**；
  而 3.2KB 的请求比 82KB 的还慢 → 瓶颈是 **TLS 握手与 RTT**，不是带宽。
  akshare 每次 `requests.get` 都新建连接，握手就吃掉 0.6~0.7 秒/只。
- **四条修法**（全部只在抓取层，**不碰回测与选股逻辑**）：
  1. 新增 `src/stock_plan/data/http_pool.py`：线程本地 keep-alive Session（每线程 2 条
     连接，worker 之间不共享）+ 默认 30 秒超时（原先是无限等，一条半死连接能卡住 worker）；
  2. 股票列表**当天只刷一次**（新浪 spot 接口翻 70 页约 20 秒，而列表一天只变一次），
     标记文件放在该 Storage 自己的 db 目录下，测试/临时库互不干扰；
  3. 预筛 `last_date` 并行（每只 5ms，5550 只串行 28 秒 → 并行 2~3 秒）；
  4. 并发默认值 16 → **32**（实测 32 线程饱和约 18 只/秒，48 线程不再更快）；
     云端 `CLOUD_WORKERS` 3 → **8**（容器 1GB 内存 / 2 vCPU，8 线程约 +120MB）。
- **A/B 实测**（同一台机、同一批 300 只真实行情、同样 force 重拉）：
  旧配置（16 线程、无 keep-alive）**7.92 只/秒**（外推 11.7 分钟）；
  新配置（32 线程 + keep-alive）**16.8 只/秒**（外推 **5.5 分钟**）→ **2.1×**。
  用户看到的 30+ 分钟主要来自云端 3 线程 + 每次握手，这两处都已修。
- **为什么不做"一次 spot 补当天一根"**：`stock_zh_a_spot()` 15.7 秒能拿全市场 OHLCV，
  但它只有**最后交易日一根**、且是**不复权**价，与现有 qfq 序列不能混存（要改成
  "不复权 + 复权因子"两套数据），且没有换手率 —— 收益大但风险更高，留作后续。
- **反向验证**：`scripts/reverse_check_update_speed.py` → **5/5 有判别力**
  （Session 不复用、退出不还原、不注入超时、列表每次重刷、预筛漏股票，各自都会让对应测试红）。

## 2026-10-28（续 3）：审计剩余 5 项（#5/#6/#7/#8/#9）+ 快照更新

上一轮修的是"会让历史结果变得不可信"的 5 条硬缺陷；这一轮清掉审计里剩下的
5 条「口径类」问题。**全部默认关闭或与旧行为等价**，唯一例外是 #6（见下，会改变
均值回归策略的历史结果），已单独量化。

### 修复：#6 策略自带的期望持仓天数被丢弃

- **问题**：策略 `exit_price()` 返回三元组 `(目标价, 止损价, 期望持仓天数)`，第三个值
  在引擎里被赋给局部变量后**从未使用**，实际离场只看 `config.max_hold_days`。
  于是"用户策略写 15 天、页面默认 30 天"就是跑 30 天；均值回归策略自己的 15 天也失效。
- **修法**：建仓时记 `pos["max_hold_days"] = min(config.max_hold_days, 策略 hint)`，
  超时判断改用该值；`config` 仍是硬上限，所以 hint 更长的策略（20/30/60/90 天）行为不变。
  成交流水新增 `hold_days` 列（原来只有 `entry_date/exit_date`，要自己算）。
- **验证**：`test_策略自带持仓天数被采用_而不是一律用config上限`（config 设 200 天时
  持仓必须 ≤12 天）；反向验证改回"只用 config"该项失败。
- **历史结果：会变，且只影响均值回归策略**（hint 15 < 引擎默认上限 20）。同一路径仅翻此
  开关：均值回归 **−2.94pp**（+33.55% → +30.61%）、451 → 431 笔；趋势跟随/价值投资的
  hint ≥ 20，逐位不变。

### 修复：#7 仓位模型与注释不符（注释说"最多 1/5 仓位"，实际首日就投 65%~74%）

- **问题**：`budget = cash / top_n` 用的是**剩余现金**而非总资产，且循环内现金被逐笔扣减，
  于是第 1 只吃掉 1/5 现金、第 2 只吃掉剩余 1/4……首日实际投入 top_n=5 时 **65.1%**、
  top_n=2 时 **74.1%**；注释写的却是"最多 1/5 仓位"。
- **修法**：不改算法（改了会动所有历史结果），只把注释改成与实现一致，并新增
  `max_position_pct`（单票占总资产上限，默认 None）让用户能真正限制单票。
- **验证**：`test_单票仓位上限_默认None时不受影响_开启后每笔不超总资产比例`
  （默认档单笔 >1 万；限 5% 后每笔 ≤5%×总资产）；反向验证 `if False` 后该项失败。
- **历史结果：不改变**（默认 None）。

### 修复：#8 没有总仓位/单票上限、没有成交量约束

- **问题**：引擎允许满仓甚至超过（top_n=2 实测同时持有 6 只）；买入量假设市场无限承接，
  合成数据里能一次买 1.2e12 股。
- **修法**：`BacktestConfig` 新增三个可选字段，默认全 None = 与历史行为逐位一致：
  `max_position_pct`（单票占比）、`max_total_position_pct`（总仓位占比）、
  `max_volume_pct`（单日最多吃掉当日成交量的比例，0.1 = 10%）。
- **验证**：`test_总仓位上限...`（20% 总仓 + 等分现金 ⇒ 首日只买 1 只）、
  `test_单日成交量占比上限_限制可买股数`（volume=100 万股、限 0.01% ⇒ 100 股）；
  反向验证两项都失败。
- **历史结果：不改变**（默认 None）。

### 修复：#9 买卖两端记账口径不对称

- **问题**：买入记的是**策略报价**（不含滑点），卖出记的是**含滑点成交价**，
  一进一出把滑点算成单向，逐笔盈亏系统性偏高。
- **修法**：建仓时记 `round(买价×(1+滑点), 2)`，与卖出侧 `close×(1−滑点)` 对称。
- **验证**：`test_买卖两端滑点口径对称_买入价记实际成交价`（滑点 0.2% 的买入价必须
  是滑点 0 的 1.002 倍，容差 5e-4）。**注意**：最初容差写 rel=2e-3，反向验证时发现
  "改回记报价"仍能通过（1.000 也落在区间内）→ 收紧到 abs=5e-4 才有判别力。
- **历史结果：会变**（滑点从单向变双向）。默认滑点 0.1% 下量级约 0.1%~0.2%/笔，
  已在影响表里与其它项合并体现。

### 修复：#5 死配置 `enable_t1`

- **问题**：`BacktestConfig.enable_t1` 全项目只有定义、没有任何读取处；True/False 结果相同。
- **修法**：删除该字段（T+1 是由"建仓在收盘、卖出块只处理已存在仓位"结构保证的），
  并在 `_close` 附近留注释说明；UI 与文档里对它的引用一并清理。
- **验证**：`pytest` 205 项全过；`grep enable_t1` 0 命中。
- **历史结果：不改变**（本来就是死字段）。

### 文档：README 退市股口径复核

- 任务书遗留项说 README 第 130 行起仍是"退市股占比 3.5%"的旧口径。**实测已不存在**：
  `README.md:132-156` 现在写的是机制性结论（退市前 60 个交易日 20 日前瞻收益 −25.19%、
  胜率 16.0%），由 `0936249` 那次提交改过，本轮仅复核，无需再改。

### 快照与定时任务

- **数据快照已更新到 2026-10-27**：`bars_count=5916`、5 卷共 389 MB、manifest 与
  公共策略文件一并上传到 GitHub Releases `data-snapshot`（2026-10-28 01:37 本地构建）。
  顺带修掉 `scripts/publish_snapshot.py` 的两个真问题：① 读待发布策略失败（DNS 抖动）
  会直接崩掉整个发布 → 改为告警降级；② 最后一行固定读 `result["branch"]`，而默认
  release 后端返回的是 `tag` → 数据其实上传成功却以 `KeyError` 退出码 1 结束，
  定时任务会误判失败。
- **定时任务：XML 已生成，注册被权限挡住**。`scripts/register_tasks.ps1 -ExportOnly`
  已产出 `scripts/tasks/{Daily_Publish,Daily_Update,Monthly_Rebuild}.xml`（UTF-16）；
  本会话实测 `schtasks /create` 无论带不带 `/xml`、无论任务放根目录还是 `\StockPlan\`
  都返回 **Access is denied**（当前进程未提权，`whoami /groups` 里没有 Administrators
  的启用 SID）→ 需要管理员 PowerShell 跑一次注册脚本，或在「任务计划程序」里导入 XML。

## 2026-10-28（续 2）：补全 pe/pb —— 估值分不再恒 50，并纳入每日更新

### 修复：`fundamentals.pe` / `pb` 全表为 NULL（估值分恒 50）

- **问题**：`fundamentals` 表 **5553 行 pe/pb 全为 NULL**（实测），于是 `value_score` 恒 50，
  价值类策略的「便宜」这一维等于不存在。根因不是数据源缺数据，而是
  `data/fetcher.py:150 get_fundamentals(code, latest_price=None)` 只有**传了最新价**才算
  `pe = 价格/eps`、`pb = 价格/bvps`，而 `data/fetch_all.py:70` 与 `data/updater.py:252`
  两处调用**都没传** → 两列永远是 None。这类「漏传参数」不报错、只静默降级。
- **修法**：① `Storage.last_close(code)` 取缓存日线最后一根收盘价；
  ② `Storage.refresh_valuation(code, price=None)` 用已入库的 eps/bvps × 价格重算并回写，
  **不更新 `updated_at`**（它是 PIT 快照日，动了会让 `fundamental_as_of` 前视修正失效）；
  ③ 两处调用补传 `latest_price`；④ 新增 `scripts/fill_pe_pb.py` 离线回填（幂等，`--dry-run` 只统计）；
  ⑤ `scripts/daily_job.ps1` 在增量更新后加一步「估值回填（pe/pb）」，失败只告警。
- **验证**：`tests/test_valuation_backfill.py` 5 项（回填算法、不动快照日、缺 eps/bvps 返回 False、
  `last_close` 取最后一根、补拉时真的把最新价传给财务接口）；
  反向验证 `scripts/reverse_check_valuation.py` **4/4 有判别力**（改回坏版本后对应测试全部失败）。
  回填实测：5553 只 / 58.6s / 缺价格 0 → **pe 有值 99.8%、pb 99.7%、两者都有 99.5%**；
  `pe<=0` 1413 只（亏损股，按中性处理）。回填后 `fund_score` 标准差 **9.04**、**51 种取值**、
  等于 50 的仅 **4.9%**（此前 100% 恒 50）。
- **历史回测结果：不改变。** 默认 `fundamental_as_of` = 财务快照日（现为 2026-10-28），
  历史窗口内财务因子本来就被中性化成 50，所以回填前后同路径数字完全一致
  （趋势跟随 +27.31%/420 笔、价值投资 +21.30%/379 笔，与回填前逐位相同）。
  **改变的是今日选股**：估值分从「恒 50」变成有区分度，价值/质量类策略的排序会变。
- **口径提醒**：因为只有一份「当前快照」，按 PIT 正确处理就等于**所有历史回测都不用财务因子**；
  想真正回测基本面，需要一个带报告期的历史财务库（计划书 §12.5 遗留项）。

## 2026-10-28（续）：回测引擎既有缺陷修复 + 财务因子"恒 50"缺陷

用户对《计划书》§10 第 4 问的回答是「修复」，指审计列出的 5 条会改变历史回测结果的缺陷。
本轮把它们全部修掉，并在度量过程中发现了一个审计**没提到、但更致命**的缺陷：财务因子在整个
系统里恒等于 50（等于没有基本面）。分支 `fix/backtest-engine-defects`（从 `main` 新建）。

### 修复：涨跌停方向判反（跌停日能卖、涨停日禁止卖）

- **问题**：`backtest/engine.py` 卖出侧只判 `close >= up`（涨停），而 `down` 解包后从未使用；
  与 docstring「跌停无法卖出」相反。合成日线实测：跌停当日照样按止损成交，止盈却被拖到涨停次日。
- **修法**：改为 `if close <= down: continue`（跌停卖不出、顺延到次日），涨停日照常可卖。
- **验证**：`tests/test_engine_defects.py` 2 项；反向验证：换回旧方向后两项都失败。
- **历史结果**：会变。同一路径仅翻此开关：趋势跟随 **+0.72pp**、价值投资 **+1.19pp**。

### 修复：缺少单笔最低 5 元佣金

- **问题**：佣金只按成交额×费率算。10 万本金、5 只持仓时单笔约 2 万元，万三只有 6 角，
  真实券商 5 元起收 —— 小额单的成本被系统性低估约 8 倍。
- **修法**：`BacktestConfig` 新增 `min_commission: float = 5.0`，买卖两侧佣金都取
  `max(成交额×费率, min_commission)`；印花税/过户费仍在卖方单独计提。
- **验证**：`test_engine_defects.py` 1 项（一次完整买卖恰好少 10 元）；UI 回测页新增输入框。
- **历史结果**：会变。仅翻此开关：趋势跟随 **+2.36pp**、价值投资 **+2.90pp**（去掉下限收益更高）。

### 修复：候选池用到开始日之后的数据（前视）

- **问题**：`factors/filter.py` 的硬过滤用 `tail(20)` / `iloc[-1]` 判流动性，而引擎把
  `[start-lead, end]` 的整段日线交给 `filter_universe()`，于是候选池看到了未来。
  实测：同一起始日、只改结束日，就有股票从"在池中"变成"被剔除"。
- **修法**：引擎建池前把每只股票的日线截到 `start` 当日（`_pool_cutoff`），只建一次池的行为不变。
- **验证**：`test_engine_defects.py` 1 项（用"按 bars 最后一根收盘价过滤"的嗅探策略暴露差异）。
- **历史结果**：会变。仅翻此开关：趋势跟随 **+16.40pp**、价值投资 **+5.28pp**。

### 修复：财务因子前视 + 财务因子恒等于 50（新发现）

- **问题一（审计有）**：`fundamentals` 表每个 code 只有 1 行、**没有报告期**，`updated_at`
  只是抓取日（实测全表同一批 2026-09-03）。在快照日之前的交易日用它打分＝提前看到未来财报。
- **问题二（审计没有，度量时发现）**：`fundamental_composite` 读的是
  `value_score/growth_score/quality_score` 三个**算好的分数键**，而 `Storage.load_fundamentals`
  返回的是 `roe/revenue_growth/...` 原始指标。两条路径（今日信号 `build_factor_rows`、
  回测 `engine`）都直接把原始 dict 交进去，读不到分数键就回退 50 → 实测 578 只股票
  `fund_score` **全部 50.0、唯一取值**；`factors/fundamental.py::compute_fundamental`
  写好了却**从未被任何生产代码调用**。所以"技术 0.6 + 基本面 0.4"里的基本面权重一直不起作用，
  所谓"财务前视"其实是**死因子**，不会真的前视。
- **修法**：① 新增 `compute_fundamental_or_neutral()`，把原始指标换算成三档分数（空数据给
  中性 50，而不是 `compute_fundamental({})` 的 30 —— 30 是利空、50 才是无信息）；
  今日信号、回测、动量策略三条路径统一走它；② 引擎新增
  `BacktestConfig.fundamental_as_of`，快照日**之前**把财务因子中性化（空 dict → 50）；
  ③ UI 回测/对比/可视化构建三处自动传入快照日（`Storage().fundamentals_snapshot_date()`）。
- **验证**：`test_engine_defects.py` 1 项 + 新增 `tests/test_fundamental_factor.py` 5 项；
  反向验证：把 5 个原始指标喂回旧的 `fundamental_composite` 后，好/坏公司分差断言失败。
- **历史结果**：**快照日之前的窗口不变**（中性 50 = 旧版恒 50，实测两档完全相同）；
  含 2026-09-03 之后的窗口会变（财务分真正生效）。若**关掉**中性化（等于回到旧的前视口径），
  同一样本上趋势跟随 **+16.43pp**、价值投资 **+48.54pp** —— 这就是这条前视的虚高量级。
- **副作用（要写进使用说明）**：`pe`/`pb` 全表为 NULL → 估值分恒 50，"价值"类策略的估值项
  仍然无效；同时今日选股排名会变（见下）。

### 修复：Walk-Forward 丢配置

- **问题**：`walkforward.py` 每个窗口手工构造 `BacktestConfig` 只透传 5 个字段，
  `initial_cash`/佣金/印花税/滑点/最低佣金全部重置回默认。实测 `initial_cash=1e6` 时
  oos 起点变成 99,917。
- **修法**：改用 `dataclasses.replace(base_config, start=..., end=..., max_hold_days=...)`。
- **验证**：`test_engine_defects.py` 1 项（monkeypatch 捕获每个窗口的 config 并断言继承）。
- **历史结果**：影响所有 walk-forward 的历史结论（资金/成本被重置过）。

### 度量：这些修复到底改变了多少历史结果

真实本地行情，2025-01-02 ~ 2025-09-30，578 只抽样，初始 10 万，同一样本逐个开关：

| 策略 | 口径 | 总收益 | 笔数 | 最大回撤 | 胜率 |
|---|---|---|---|---|---|
| 趋势跟随 | 新版（全部修复） | +24.95% | 419 | −18.01% | 52.74% |
| 趋势跟随 | 佣金下限去掉 | +27.31% | 420 | −17.83% | 54.05% |
| 趋势跟随 | 财务快照不做前视修正 | +41.38% | 316 | −11.26% | 57.91% |
| 趋势跟随 | 候选池不截到开始日 | +41.35% | 436 | −14.97% | 55.73% |
| 趋势跟随 | 涨跌停判回旧方向 | +25.67% | 417 | −17.58% | 53.00% |
| 价值投资 | 新版（全部修复） | +18.40% | 377 | −20.75% | 49.60% |
| 价值投资 | 佣金下限去掉 | +21.30% | 379 | −20.53% | 50.92% |
| 价值投资 | 财务快照不做前视修正 | +66.94% | 195 | −7.11% | 58.46% |
| 价值投资 | 候选池不截到开始日 | +23.68% | 356 | −20.93% | 51.12% |
| 价值投资 | 涨跌停判回旧方向 | +19.59% | 378 | −20.92% | 50.79% |

结论：五条修复**都会改变历史回测结果**，此前所有基于旧引擎的调参/选股结论都要按新版重跑。
但表里的差值**不能**当成"某缺陷的收益贡献"——持仓路径一变，噪声本身就是十几 pp 量级
（同一策略总收益跑动标准差 15pp），这只是一次同一路径的敏感性对比。

财务因子修好对**今日选股**的影响（2026-10-27 数据、583 只候选）：
趋势跟随 top10 重合 9/10（排序 Spearman 0.945）、价值投资 top10 重合 **3/10**（0.932）。
即：财务因子从"死"到"活"以后，价值类策略的名单会明显换人 —— 这是修好缺陷的正常后果，
不是回归。

## 2026-10-28

本轮修《计划书》工作流④（LLM 自然语言 → 策略代码 → 回测/选股 链路）的 6 个已知缺陷。
分支 `fix/llm-chain-hardening`（从 `main` 新建）。**全部改动都只影响 LLM 生成/导出/选策略的
交互，不改变任何历史回测的成交逻辑**；唯一会改变历史结果的是「最大持仓天数」默认值的修正
（见下），凡是用到自存策略回测的结论都应重跑。

### 修复：百分比止盈止损被当成 ATR 倍数（错量纲，止盈永不触发）

- **问题**：系统词表只有「ATR 倍数」口径（`atr_m_exit` / `atr_n_stop`），用户口语常说百分比。
  旧实现 `止盈[^\d]{0,4}(\d+)` 在「涨 10% 止盈，跌 5% 止损」上会跨过逗号抓到**止损那个 5**，
  于是 10%/5% 双双丢失、`atr_m_exit` 变成 5（等于"涨 5 个日常波动才止盈"）；说「盈利/亏损」
  则两条都不命中且不报任何提示。
- **修法**：`llm/analyzer.py` 新增 `_keyword_number()`，在关键词**前后**各找数字并取更近者，
  同时标记是否带 `%`；带 `%` 的不写入配置，改为在「未支持参数」里回显用户原话（10%/5%）
  并给出换算建议。附注：1% 涨跌幅 ≈ 0.25 倍 ATR。
- **验证**：`tests/test_llm_chain.py` 3 项；反向验证：恢复旧正则后测试失败。

### 修复：LLM 失败时页面全空白（连原因都不显示）

- **问题**：`generate_strategy_config` 失败只返回 `config={}`，而页面渲染条件是
  `if result.get("config")` → 失败时整块不渲染，用户看不到是没配 key、超时还是额度用尽。
  另外 `llm/client.py` 的 `OpenAI(...)` 没传 `timeout`，SDK 默认 600 秒且自动重试，
  网络不通时页面会卡十几分钟。
- **修法**：新增 `error` 字段（带底层异常类型与信息）；客户端显式 `timeout=30`、
  `max_retries=1`，并把失败原因记到 `last_error`；页面在 `config` 为空时用 `st.error`
  显示原因。
- **验证**：`tests/test_llm_chain.py::test_failure_exposes_error_field`；反向验证同上。

### 修复：策略名带换行可注入到导出脚本

- **问题**：`codegen.normalize_config` 对名称只做 `str(...)[:30]`，换行原样保留；
  `name="a\nimport os\nos.system('calc')"` 会让生成脚本的注释在下一行断掉、
  **变出可执行语句**，`ast` 顶层能解析到 `Import`。
- **修法**：新增 `codegen._one_line()`（去换行/制表/控制字符、压空格），名称与注释行统一走它。
- **验证**：`test_export_script_ignores_injection_in_name` 断言生成脚本能 `ast.parse`
  且顶层无 `Import`/`Expr`；反向验证：去掉单行化后测试失败。

### 修复：与内置同名的自存策略在下拉框里消失

- **问题**：`store.strategy_options()` 用 `n for n in list_strategies() if n not in STRATEGIES`
  过滤同名项 → 用户保存的「趋势跟随策略」**根本不出现**（比审计说的"选错"更严重）；
  策略对比页同样把它静默丢掉。
- **修法**：新增 `display_name`/`strip_display_name`/`SAVED_SUFFIX`（`（自存）`）。
  同名项显示为「名字（自存）」，两条都能选；`resolve_strategy` 带后缀时明确取自存版本，
  不带后缀保持旧语义（内置优先），不会让内置策略被顶掉。
- **验证**：`test_saved_strategy_with_builtin_name_is_visible` + 两条反向验证（恢复过滤逻辑 /
  去掉显式自存分支，均失败）。

### 修复：回测页的「最大持仓天数」无视策略自带设置

- **问题**：`ui/views/backtest.py` 默认值写死 30，而已保存策略 `MA5金叉MA30策略` 记录的是
  `hold_days=15` → **选中自己的策略，跑的是持 30 天的版本**，用户以为参数没生效。
  （`compare.py` 同样写死 30。）
- **修法**：回测页默认值改为「所选策略自带的 `hold_days`，没有则 30」，控件 key 随策略变化以
  正确重置；对比页按**每个策略自己的** `hold_days` 回测，页面滑杆只对没设过的策略生效。
- **影响历史结果**：⚠️ 会改变"用自存策略回测/对比"的结果（持 15 天 vs 30 天），需重跑这类结论。
- **验证**：14 页 AppTest 冒烟 + 手工确认控件默认值。

### 修复：回测缓存键不含策略配置（改参数后 10 分钟不变）

- **问题**：`@st.cache_data(ttl=600)` 的缓存键是 14 个显式参数，策略配置是在函数体内按名字读库的，
  不在键里 → 改了权重/规则后最长 10 分钟仍返回旧结果。
- **修法**：新增 `config_sig` 参数（策略配置的 JSON 指纹）参与缓存键，页面计算后传入。
- **验证**：AppTest 冒烟；参数签名向后兼容（默认空串，单点调用）。

### 修复：保存策略必须先提交公开成功

- **问题**：`ui/views/llm.py` **先**调 `submit_public_strategy`（需 token + 网络），成功后才
  `save_strategy` 落本地 → 没网/没配 token 时一条策略都存不下来。
- **修法**：先本地保存（本地能力不该被网络卡住），再尝试公开提交；提交失败只 `st.warning`
  提示"本地已保存，不影响使用"。按钮文案改为「💾 保存策略（并尝试提交公开）」。
- **验证**：代码复核 + 14 页 AppTest。

**整体验证**：`pytest` → **184 passed**（178 + 6 项新增）；`compileall -q src/stock_plan` → 0 错误；
`acceptance.py --full` → **37/37 通过**（含 `[3] 14 页全部无异常`，应用在运行故健康检查也通过）；
反向验证 5/5 有判别力（改回坏版本后对应测试全部失败）。

## 2026-10-27

本轮把《计划书 `2026-10-15-5-new-classification-emotion-flow-and-weekly-t.md`》的四个工作流落地。
分支 `feat/classification-emotion-t-announcement`（从 `main` 新建）。

### 新增：板块分类层（申万 + 证监会，按月时点快照）

- **问题**：项目原来的"板块"只是按代码前缀猜（沪市/深市/创业板），行业与概念完全缺失；
  `stock_list.industry` 列存在但**0 行非空**。计划书指定口径为"行业=申万三级 + 证监会 84 类都存，概念只做板块级"。
- **修法**：新建 `src/stock_plan/data/classification.py`，产出 `data/processed/classify/{YYYY-MM}/`
  下三个 parquet：`industry_sw.parquet`（`code,name,sw_l1,sw_l2,sw_l3,incl_date`）、
  `industry_csr.parquet`（baostock 证监会口径）、`concept.parquet`（同花顺概念板块级）。
  所有表都带 `snapshot_month`，读取时按"不晚于目标日期的最近月份"取，**不返回未来快照**。
- **数据源实测（2026-10-27，akshare 1.18.94）**：
  - 东财 `push2.eastmoney.com` **不可达**（0.3s 断），东财行业/概念接口全 FAIL → 原定的"东财行业+概念"不可用；
  - 乐咕乐股 legulegu.com（akshare `sw_index_*_info` 的底层）**间歇性整站 504**，
    同一晚 23:06 正常、23:16 起连续 504，`sw_index_first_info()` 抛 `AttributeError: 'NoneType' object has no attribute 'find_all'`；
  - **改走申万官网**：`ak.index_realtime_sw`（一级 31 / 二级 124）+ `ak.index_component_sw`（含**计入日期**，正是时点语义所需）。
- **降级与限制（已写进 docstring 与页面告警）**：官网源**没有三级行业名录**（`index_realtime_sw('三级行业')` 直接
  `ValueError: Length mismatch`；`stock_industry_clf_hist_sw` 走 xls 且 **SSLError**）→ 本轮 `sw_l3` **全空**，
  明细里显式标注"申万三级为 0"，**绝不拿二级冒充三级**。概念无任何可靠成分股来源（东财被墙、akshare 无同花顺成分接口、
  富途只有 3 个硬编码美股概念），故概念只做板块级资金流。
- **验证**：`--all` 写入 2026-10 快照——申万 5220 只（一级覆盖 100%、二级 5200、耗时 98.2s）、
  证监会 5561 行（5224 有行业、334 空=退市股、21.3s）、概念 375 个；文件 99.1 / 99.2 / 8.7 KB。

### 新增：市场情绪与板块资金流（日频快照 + 因子）

- **问题**：情绪指标原来只有价格派生的 RSI/均线，缺少"涨停家数、炸板率、连板高度、资金净流入"这类盘面温度。
- **修法**：新建 `src/stock_plan/data/market_snapshot.py`（落 `data/processed/market/{YYYY-MM-DD}/`）
  与 `src/stock_plan/factors/emotion.py`（纯读快照计算，不联网）。
- **分层抓取的取舍（实测成本决定）**：日频只抓"资金流三表 + 涨停/跌停/炸板池"= **约 12s**；
  申万成分、证监会行业、板块指数行情放月频（申万 2.6 分钟 + 板块行情 2.8 分钟 + baostock 98s）。
  存储 **≈0.3 MB/天、≈100 MB/年**，可以承受，但没必要天天重抓月频数据。
- **修掉一个会静默出错的单位 bug**：同花顺三张资金流表单位**不一致**——行业/概念表的净额是
  **float 单位亿元**，个股表是**字符串带单位**（`"1798.11万"`）。不统一时同屏比较差 7 个数量级；
  另个股的 `涨跌幅/换手率` 是带 `%` 字符串，喂给 `parse_money` 会全变 NaN。已加 `parse_pct` 分流。
- **修掉一个空转指标**：`flow_breadth['median_net_pct']` 恒等于 **0.5001**——因为 `net_pct` 是排名分位，
  中位数按构造永远 ≈0.5，与市场冷暖无关；改为 `median_net`（中位数净额，元）。
- **不做的事**：不产出单一 0~100 情绪分（需要 ≥20 个交易日分布校准，目前只有 1 天）；
  三张资金流表的净额**不可跨源相加**（同日行业合计 −600.9 亿 vs 个股 −779.8 亿，覆盖范围不同）。
- **首日（2026-10-27）实测输出**：涨停 41 / 跌停 22 / 炸板 28 → 炸板率 **40.6%**，最高 **5 板**
  （梯队 1板×29、2板×8、4板×3、5板×1）；5217 只个股中上涨 **11.0%**、主力净流入 **14.3%**；
  行业净流入前三 银行 +74.32 亿 / 工业金属 +6.22 亿 / 白酒 +5.78 亿。

### 新增：公告抓取与存档（持仓 + 自选）

- **问题**：公告接口现成可用（`get_stock_announcements`），但抓到就丢，没有任何存档，无法回溯"当时看到了什么"。
- **修法**：新建 `src/stock_plan/data/announcements.py`，独立 sqlite `data/processed/announcements.db`
  （**不塞进 `meta.db`**：避免主库膨胀与写锁风险）。表 `announcements(code,ann_date,title,ann_type,url,fetched_at,
  PRIMARY KEY(code,ann_date,title))` + 表 `watch`（自选/备注）。
- **为什么按股票抓而不是全市场**：用户范围是持仓与自选；全市场每天几千条且要翻页。
- **验证**：临时库抓 600000+000001 近 20 天 → 5 条；**重复抓新增 0**（主键去重生效）；自选增删正常。
- 连接管理**显式关闭**，不沿用项目里 `store._conn()` 的 `with sqlite3.connect(...)`（只提交事务、从不关闭连接）写法。

### 新增：做T（底仓 + 周内 T）回测与页面

- **问题**：原引擎**表达不了**"同一只股票同时有底仓和 T 仓"——持仓以 `code` 为唯一键
  （`backtest/engine.py:154`）、`_close()` 全额平仓（`:173-191`）、退出价入场即冻结（`:359-360`）、
  策略无 `on_bar` 钩子（`strategy/base.py:26-60`）。且只有日线，**真正意义的"日内 T"不可回测**。
- **修法**：新建独立模拟器 `src/stock_plan/backtest/t_trade.py`（不改既有引擎，避免动到刚审计完的成交路径）：
  底仓首日等权买入后不动；T 仓在底仓存在且当日收盘价较成本跌 `entry_drop` 时买、涨 `exit_gain` 或亏 `stop_loss`
  或持有超 `max_hold_days` 或周五收盘强制了结。只做**正T**（先买后卖），T+1 由循环结构保证（买入块 `continue` 收尾，
  卖出块只对已存在仓位执行）。成本含佣金/最低 5 元/印花税/滑点，按整手 100 股。
- **验证（10 项测试 + 反向验证 4/4）**：`tests/test_t_trade.py` 10 passed；
  逐一把代码改回坏版本，对应测试**全部如预期 FAIL**（`close_on_friday=False`、无底仓也做T、破整手、
  对照曲线起点置 0），还原后全绿。
- **删掉一个空转守卫**：卖出分支原本的 `if held >= 1:` 删掉后 10 项测试**照样全过** → 它守不住任何东西，
  按项目规矩删除并写明理由（空转守卫比没有守卫更糟）。
- **真实数据冒烟（8 只大蓝筹、119 个交易日）**：底仓**只建成 5 只**（茅台等 1 手就超等权预算 →
  新增 `skipped_code_count` 把这个坑暴露出来）；底仓不动 **+1.80%** vs 底仓+做T **+1.76%**（T 贡献 −0.04pp）；
  只触发 1 笔（周五强平，−2.52%），同日买卖 0 笔。**默认 3% 触发在大蓝筹上极难触发，这是真实结论，不为好看调参。**
- **页面**：`ui/views/t_backtest.py`「做T回测」——底仓范围（板块 + 申万一级行业 + 只数）、参数滑杆、成本展开、
  底仓 vs 做T 对照、资金曲线、逐笔明细、`skipped_code_count` 警告、笔数 <30 时提示统计意义弱、参数敏感性扫描。

### 新增：「市场情绪·板块资金·公告存档」页面

- `ui/views/market.py`，三个 tab：盘面温度（指数/涨停/炸板/连板梯队/行业涨停分布）、板块资金流（行业/概念 top&bottom）、
  公告存档（抓取按钮 + 自选管理 + 列表）。分类快照缺失或申万三级为 0 时**显式告警**，不静默给空结论。

### 新增：行业筛选进选股入口（用户确认的"通过行业板块指定策略"）

- `ui/widgets.py` 新增 `industry_filter_ui()`（申万一级多选）；`ui/views/today.py` 选股范围里加入；
  `signal/generator.py` 的 `generate_signals(..., industries=None)` 加行业过滤，并沿用到回测/对比/拼装四页的共用入口。
- **默认值全部保持"不限"**，不传时行为与历史完全一致 → **不改变任何既有回测结果**。

### 工程与验证

- 页面由 **12 → 14**：`ui/app.py` 注册 `做T回测`、`市场情绪`；`scripts/acceptance.py` 的 `PAGES` 同步为 14 项，
  页数改为 `len(PAGES)` **动态取**（不再写死"12 页"）。
- `compileall -q src/stock_plan` → **0 错误**；`pytest -q` → **178 passed**
  （153 原有 + 10 项做T + 15 项新增数据层）。
- 新增 `tests/test_market_data.py`（15 项，全离线、不联网）：钉死四类容易"看起来正常但静默出错"的逻辑——
  单位换算（`parse_money`/`parse_pct`/亿与万混用）、快照按月隔离不串味、缺数据时**不静默清空**选股池、
  公告按主键去重。**反向验证 3/3 有判别力**（`scripts/reverse_check_market_data.py`）：
  炸板率分母改回 `n_zb/n_up`、广度改回常数、缺分类数据改回返回 `[]`，对应测试均 **FAILED**，
  还原后 178 passed。
- `scripts/acceptance.py --full` → **全绿**（含 14 页 AppTest 渲染、数据完整性、快照校验）。
- 数据更新：本次已实际抓取并落盘 2026-10 分类快照 + 2026-10-27 市场快照；公告库仅测试数据，正式抓取留给日频任务。
- 新增 `docs/使用指南（简明版）.docx`（5 页，由 `scripts/make_user_guide.py` 生成，便于他人阅读）。

## 2026-10-15

### 修复：不带参数的 `pytest` 会中断收集（`Text/` 副本的同名模块冲突）

- **问题**：`pytest`（不带参数）报 4 个 `import file mismatch`，**中断整轮收集（0 passed）**，
  于是"改完跑 `pytest`（应 153+ passed）"这条验证跑不起来。
  根因是项目根下还有一个 `Text/` —— 711.6 MB 的解压副本，它**是个独立的 git 仓库**
  （自带 `.git`），被 `.gitignore:30` 忽略、外层仓库**跟踪 0 个文件**；
  它内部也有一份 `tests/`，会**抢先注册** `test_backtest` / `test_llm_codegen` /
  `test_snapshot` / `test_updater` 四个模块名，随后收集 `tests/` 下的同名文件即冲突
- **修法**：`pyproject.toml` 新增 `[tool.pytest.ini_options]` 的 `testpaths = ["tests"]`。
  **只限定收集范围，不删除任何东西** —— `Text/` 的去留仍由用户决定
  （它是独立仓库，且用户此前对同类重复副本明确选择保留）
- **验证（双向）**：
  - 修复前：`pytest` → ❌ `4 errors`、**0 passed**、收集被中断
  - 修复后：`pytest` → ✅ **153 passed**（4.94s）
  - 显式 `pytest tests` → ✅ 153 passed（显式路径优先于 `testpaths`，不受影响）
  - `scripts/acceptance.py --full` → ✅ 仍 **36/36**（`[2] 测试套件` = 153 passed）
  - `compileall -q src/stock_plan` → ✅ 0 错误
- 说明：`INSTRUCTION.md` 第 8 节记录的本来就是 `uv run pytest tests/`（该命令一直可用），
  本次修的是"**不带参数**"这一种写法。本改动**不改变任何回测结果**

### 端到端验收（本轮）

- **快照离线往返完整性 ✅**：重新打包 5890 只 → 369.0 MB / 5 卷；
  `validate_manifest` 通过；逐卷核对文件名/字节数/sha256 **5/5 通过**；
  **按序拼接分卷重现 `zip_sha256` = True**（这正是 `restore_from_url` 下载后的最后一道校验）；
  拼出的 zip 可读、`testzip` 无损坏、条目数与清单一致；抽验成员能读成 DataFrame
- **定时任务定义 ✅**：三个 XML 均为 UTF-16LE；`daily_job.ps1` 确实声明了
  `SkipPublish`/`Rebuild` 参数（与 XML 的 `-SkipPublish`/`-Rebuild` 对得上）；
  两个 .ps1 语法检查无误；触发器如设计（发布=工作日 16:30、更新=工作日 08:00、重建=每月 1 日 17:00）
- **数据完整性**：日期重复 0、日期未递增 0、成交量为负 0；
  **发现并修复 3 只股票的零值占位行**（见下）
- **振幅异常的解释（非缺陷）**：全库有 279 行日振幅 > 50%，集中在 1990 年代（当时无涨跌幅限制）
  与 2020 年代的新股首日/退市整理期。科创板/创业板在 ±20% 限制下振幅上限恰为 50%，
  超过即必然是无限制日 —— 属合法市场现象，不改

### 修复：数据源的"零值占位行"会污染 ATR

- **现象**：新浪 `stock_zh_a_daily` 对 3 只科创板股票（688089 / 688143 / 688173，全库 3/5890）
  在 2024-11-06 返回 `open=high=low=0`、`volume=0`、`close=沿用前收` 的占位行。
  **已直接重新拉取同区间确认：新浪返回里同样带这一行，不是本项目管线造的**
- **危害**：`TR = max(high-low, |high-前收|, |low-前收|)` 在 `low=0` 时变成 `|0-前收|`≈20
  （正常约 1）。该尖峰进入 ATR 的 EMA 后把 ATR **放大 1.5~1.75 倍并持续近一个月**
  （688143 峰值 3.812 vs 干净 2.178）。乘上默认 `m=n=3.5`，止损位被拉远约 5.7 元 →
  单笔风险敞口被严重误算；图上也会出现掉到 0 的尖刺
- **修复**：在 `Storage.load_bars`（所有行情消费方的唯一入口）读取时剔除
  `open/high/low ≤ 0` 的行。真实停牌日本就没有 K 线，**剔除是语义正确的做法**（不是填值）；
  缓存保留原始行以便追溯
- **验证**：新增 `tests/test_storage_sanitize.py` 5 个用例；**反向验证**（只停用 `load_bars`
  里的调用、保留函数）→ `test_load_bars_applies_sanitization` **FAILED**，恢复 → 5 passed。
  实测：缓存各 1 行零值 → 经 `load_bars` 出来 0 行
- 验证：`compileall` 全包 0 错误；`pytest` **148 passed**；12 页 AppTest 全绿

## 2026-10-15

### 新增项目自检脚本 `scripts/acceptance.py`

把本项目审计与验收期的结论**固化成可重复运行的一条命令**，以后改动后随时复验，不再依赖一次性脚本：

```
python scripts/acceptance.py            # 常规自检（约 30 秒）
python scripts/acceptance.py --full     # 额外做快照打包往返校验
python scripts/acceptance.py --no-pytest
```

覆盖 9 组共 27 项：环境与依赖（含 py_mini_racer）／测试套件／12 页渲染／数据完整性
（零值行已被 `load_bars` 清洗、日期唯一递增、OHLC 自洽、退市股与时点池）／快照打包往返
（`--full`：逐卷 sha256 + 拼接重现 `zip_sha256`）／策略库 schema（tags/owner/settings）／
定时任务定义（UTF-16、参数名）／敏感文件（未跟踪、已忽略、历史无记录）／应用健康检查。
报告写入 `data/logs/acceptance.txt`，退出码非 0 表示有检查未通过。

### 修复：策略详情表的值列混合类型会触发 Streamlit 内部 Arrow 错误

- **现象**：`值` 列混装数字（权重/买卖价参数）与字符串（规则的"启用/关闭"）→ dtype=object。
  Streamlit 内部 `pyarrow.Table.from_pandas` 抛
  `ArrowInvalid: Could not convert '启用' with type str: tried to convert to double`
- **修法**：抽出 `_config_rows(cfg)` 并把该列**统一成字符串**（`str(v)`）
- **更正我自己的一个未经验证的说法**：我一度写成"用户看不到表"。实测发现该异常被 Streamlit
  吞掉后**自行退化处理**，元素仍在、列被强制成 pandas 的 `string` 扩展类型，
  数据也能正常显示。所以严重程度是"**日志里有内部错误、行为脆弱**"，而不是"表格不见了"。
  这条判定我没有逐帧验证过用户视角，故按实测证据改口径
- **验证**：新增 `tests/test_strategy_mgr_table.py` 5 个用例，直接断言行构造函数输出同一种
  dtype、且能被 Arrow 序列化；**反向验证**（把该列改回混合类型）→ **3 failed**，恢复 → 5 passed

### 记录一个失败的验证尝试（避免以后重复踩）

我原想在自检里加一条"所有页面上的表格都能被 Arrow 序列化"的通用守卫，但**两次都空转**
（在故意改回混合类型的代码上仍报通过）：

1. 对 `at.dataframe` 元素试 `pa.Table.from_pandas` —— 走到元素时该列已被强制成
   pandas 的 `string` 扩展类型，序列化会成功；
2. 捕获 logging 记录 / 重定向 stderr —— 该 `ArrowInvalid` 两处都不出现。

**结论：AppTest 看不出这类"表格内部转换失败"。** 我把这两条空转的守卫**删掉**了
（探测不到它声称要检查的失败，比没有守卫更糟），改为依赖有判别力的单元测试，
并在脚本里写明知情局限。

- 验证：`compileall` 全包 0 错误；`pytest` **153 passed**；自检 **32/32 通过**
  （`--full` 为 37/37）；12 页 AppTest 全绿

### README 增加「项目自检」章节

- 把自检命令提到独立章节（原来只有一条要点，且被塞在「云端数据快照」下，位置不对）
- 文档里的项数与耗时**都是实测值**：`python scripts/acceptance.py` = **32 项 / 约 18 秒**、
  `--full` = **37 项 / 约 22 秒**、`--no-pytest` = **27 项 / 约 12 秒**
- 我起初在这段里写了"不加 `--full` 是 27 项、约 30 秒"——**两个数字都是估的、都不对**
  （27 是加 `--no-pytest` 时的项数；耗时实测约 18 秒）。已按实测改写，并在正文里注明
  "本机实测"

### 修正：应用健康检查不该把"服务没开"算成失败

- **问题**：`check_app_health` 在应用不可达时 `check(..., False, ...)` → 记为**失败**、
  退出码变 1，但提示文字写着"非失败项"——**说法与行为不一致**。用户一旦停掉
  Streamlit 服务再跑自检，就会看到"31/32、退出码 1"而困惑
- **修法**：新增 `note()` 记录"跳过"（`ok=None`），不计入通过数、不影响退出码；
  汇总行显示 `31/31 通过；1 项跳过`；报告里该行标 `SKIP`。另加 `--app-url` 便于指向云端地址
- **验证**：应用在运行时 **32/32、exit 0**；指向死端口时 **31/31 + 1 项跳过、exit 0**

### 更正 README「退市股数据」一节的旧口径

归档前最后一处已知的过期说法。原文有三处问题：

| 原文 | 问题 | 改为 |
|---|---|---|
| "回测收益会被**系统性高估**" | 过于绝对。实测机制严重，但对某个策略**总收益**的影响是**策略相关**的 | 拆成"有多少退市股"与"偏差有多大"两个问题分别回答 |
| "仅采样 3 个历史时点就能推出 **298 只**期间退市" | 这是被取代的旧方法；权威口径是元数据累计 **339 只**、其中退市日落在最近 5 年窗口内约 **190 只** | 按权威口径改写 |
| 未提正确量级与用法 | 读者会以为"占比 3.5%"就是偏差大小 | 补上**与选股路径无关**的机制数字：退市前最后 60 个交易日的 20 日前瞻收益均值 **−25.19%**、胜率 **16.0%**（其余样本 +1.11%、48.1%）；并写明回测页的「包含已退市股票」开关（默认关闭） |

同时补上"**不要用单次回测总收益去量化它**"的警告（该策略总收益跑动标准差 15pp，
候选池只增 3.75% 就能让结果差 ±21pp），并指向审计文档。

## 2026-10-15（第二批：暂时需求 1 / 2 / 3 / 5a / 5b / 5c）

- **暂时需求 1 — 图表缩放与刻度密度**（`4634bb3`）
  - 根因：日期被写成 `str(d)[:10]` 再画，Plotly 当成**分类轴** —— 刻度位置固定，
    缩放时不重算密度也不换格式；另外 Streamlit 里 Plotly 默认**禁用滚轮缩放**
  - 新增 `PLOTLY_CONFIG`（scrollZoom / doubleClick=reset / dragmode=pan / 去掉无用按钮）
    与 `apply_time_axis()`（date 轴 + 4 档随缩放切换的格式 + 十字准线）；5 处字符串日期
    改 `pd.to_datetime`，10 处 `st.plotly_chart` 统一传 config
- **暂时需求 2 — 要闻不再把下方选项顶走**（`254239d`）
  - 原先每条要闻一个 expander，展开长文会把财新刷新按钮与 CCTV 日期选择器挤出屏幕
  - 改为「紧凑列表（每行一条 + 查看全文）→ 详情子视图（← 返回列表）」，默认只列 8 条
  - 顺带修掉隐患：条目原存在按钮块内的局部变量里，改用按钮后必然 rerun 会丢，已存 session_state
- **暂时需求 3 — 个股新闻搜索**（`9a85617`）
  - 支持输入**代码或名称**（`600519` / `贵州茅台` / `茅台`）；模糊命中多只时给候选让用户选
  - 时间跨度 近3天/近1周/近1月/近3月/近1年；空结果提示"最新一条是 X 日"，避免误以为功能坏了
  - 外部联动：东方财富搜索 / 百度新闻 / 雪球讨论 / 巨潮公告原文；另加「让 LLM 分析该股舆情」
  - 修复 `get_news(code, days=7)` 的 `days` 从未被使用、调用方却把 `page_size` 当它传入的
    bug —— 条数与时间跨度双双失效，永远固定取 20 条
- **暂时需求 5a/5b — 回测成本自定义 + 过拟合自查**（`c6954ec`）
  - 佣金/印花税/滑点原先写死在 `BacktestConfig` 默认值，UI 无法修改；现提供万分之输入
    （默认 3/10/10，与原硬编码完全等价）并实时显示往返成本（0.36%）
  - 回测页右侧新增「过拟合自查」，用本次回测**真实数字**诊断样本长度、交易笔数、
    单笔均收益 vs 往返成本，附 6 条规避清单；`metrics` 新增 `avg_pnl_pct`
  - 修复 `_render_result` 内函数级 `from ... import` 导致该名字变局部变量、函数前部调用
    必抛 `UnboundLocalError` 的问题（渲染任何回测结果都会崩）
- **暂时需求 5c — 退市股数据层**（`8922c48`）
  - 新增 `data/delisted.py`：时点股票池 + 退市股历史行情（baostock，**惰性导入**，
    应用与云端无需安装）；深交所名单走 akshare，上交所改用「时点池差集」推算
  - 新增 `delisted_stocks` 表与 `save_delisted` / `load_delisted`；新增 7 个单测
  - 修复 `is_a_share` 漏判交易所（上证指数 `sh.000001` 会被当成深市 A 股并与
    平安银行 `sz.000001` 的 6 位代码撞车）
  - 修复自然月末取不到数据（回退到最近有数据的交易日；实测 25 个月末里 9 个是空的）
  - 口径校验：与新浪逐列对比，600519 差异全为 0.0000、000001 最大 0.0059；
    volume / amount / turnover 比值均为 1.0000
- **修复历史遗留**：`mvp_check.py` 第 66 行起 5 行多余缩进导致 `IndentationError`，
  整个模块无法导入（`93e4b09`）
- **暂时需求 5c 接入**：`load_market_maps(include_delisted=...)` 可把退市股纳入回测样本
  （默认关闭）；回测页新增开关。前置修复：引擎原先会在"持仓期间行情终结"时让持仓
  **凭空消失**（估值计 0、收尾静默丢弃），已改为按最后可成交价强制平仓并归因 ——
  该 bug 不需要退市股就能触发，任何被持有的停牌股都会中招（`0f3a9a4`、`16da62a`）
- **暂时需求 6（前半）— 市场阶段因子与分阶段策略建议**（`416ed6f`）
  - 新增 `factors/regime.py`：**等权收益指数**（日收益等权平均后累乘，与价位和成分变动无关）、
    逐日/最新阶段判定（`close > MA20 > MA60` 且 MA60 上行 → 上升；反向 → 下降；其余 → 震荡）、
    波动率历史分位、通道位置，以及阶段 → 策略偏好 + 建议仓位的 playbook
  - **顺带修掉引擎的大盘代理缺陷**：原为 `closes.mean(axis=1)`（对原始股价取平均），
    会被高价股主导、且成分股缺 K 线时指数跳空，导致"大盘跌破 MA20"可能由成分变动而非
    行情触发。`market_timing=True` 是默认配置，故该缺陷一直在影响回测。改为等权指数后，
    实测 297 只候选的指数 1.0 → 1.4288，39% 的交易日低于 MA20（择时不再空转）
  - 「今日信号」页顶部新增市场阶段面板（四个指标 + 判定理由 + 策略偏好 + 等权指数图）
  - 新增 `tests/test_regime.py` 10 个用例；其中一条暴露出我自己写的**平凡断言**
    （`min_contributors` 默认 20 而用例只有 1 只股票 → 指数恒为 1.0），已修正为非平凡
- **暂时需求 6（后半）— 缠论结构识别**（`c37b288`）
  - 新增 `factors/chan.py`：K 线包含处理、分型（要求中间那根的高点与低点**都是**最高/最低）、
    笔（异类型分型 + 间距 ≥ 4，且**必须首尾相接**）、笔中枢（连续三笔重叠区间）、三买
  - **明确未实现**并在代码与 UI 里声明：线段、走势类型、背驰，因此也**不提供一买/二买**。
    定位是结构识别的辅助特征，不是完整缠论交易系统
  - 测试过程中抓到两个**真问题**：① 第一版"一发现异类型分型就立即成笔"会产生**断开的两笔**
    （中间悬空）且无法延伸端点，改为「先归约再成笔」；② 真实数据上 26 笔产生了 **22 个**
    冗余中枢，加入**中枢延伸**合并后降到 **3 个**（000001 16→2、300750 19→2、601318 20→7）
  - 「持仓诊断」页新增可折叠的缠论结构区块（笔/中枢/分型/三买 + 当前价相对中枢上下沿的位置）
  - `tests/test_chan.py` 24 个用例，全部基于**可手算**的人工折线行情与手工构造分型
- **快照分发改 GitHub Releases**（`8fa4e2e`）
  - 原先 force push 到 `data-snapshot` 孤儿分支：每天往仓库塞约 300MB blob（GitHub 侧回收
    不可控），且 `raw.githubusercontent.com` 有 CDN 缓存，force push 后可能仍取到旧快照
  - 新增 `publish_to_release()`：**用 manifest 充当"提交指针"** —— 先以带版本号的名字上传新分卷
    （不覆盖旧资产），全部就位后才替换 manifest，最后清理被取代的旧分卷。上传中途失败时
    线上仍是完整旧快照；切换成功后 Release 只保留一份数据
  - 恢复端新增 `restore_with_fallback()`：按 Release → 旧分支 顺序尝试，切换期不影响云端；
    发布端可用 `STOCK_PLAN_SNAPSHOT_BACKEND=branch` 回退
  - 补上 `resolve_token()`：显式参数 → 环境变量 → `.streamlit/secrets.toml`。
    原实现只认环境变量，而本机计划任务不经过 Streamlit、读不到 `st.secrets`，
    定时发布会直接报"缺少 token"
  - 只读飞行前检查：token 有效（认证为 Cwywwy）、仓库 public、`permissions.push=True`（可发布）
  - `publish_snapshot.py --build-only` 实测：5776 只 → **351MB / 4 卷**
- **修复"僵尸候选"占位（影响所有回测）**（`ea768f2`）
  - 量化幸存者偏差时先用同一策略跑"含退市股 vs 不含"，得到 -10.6pp，我一度当成幸存者偏差。
    但两处证据对不上：退出原因里**没有**"退市/停牌"；190 只退市股均匀分布在 2022~2026，
    却只成交 5 笔且全在回测首日
  - 根因：候选指针单调推进，已退市/长期停牌股票的指针**停在最后一根 K 线上**，带着**过期数据**
    继续参与打分并占住 top_n 名额；买入环节虽会跳过它，但那天就少买一只
  - 修法：候选必须当天有行情。**仅这一条就让同期"在市股"回测的总收益从 -5.23% 变为 -9.88%**
    （差 4.65pp）—— 也就是说此前所有回测都受此影响，与是否用退市股无关
  - 修正后再量化：同一策略、同一区间，"含退市股 vs 不含"的总收益差只有 **-0.02pp**
    （-9.88% vs -9.90%），交易笔数 1008 vs 1011
  - **结论修正**："退市股占 3.5%"本身**不等于**偏差。偏差取决于策略会不会挑中它们，
    因此是**策略相关**的；以后必须用"同一策略 + include_delisted 开关"实测
- **暂时需求 4（部分）— 策略分组标签 + 批量删除**（`ea9e22b`）
  - `strategies` 表新增 `tags` 列，老库轻量迁移不丢数据；按标签筛选用
    `',' || tags || ',' LIKE '%,tag,%'` 保证**精确匹配**（「客户-张三」不命中「客户-张三丰」）
  - 新增 `normalize_tags` / `set_tags` / `all_tags` / `delete_strategies`
  - 策略管理页新增标签筛选 + 「🧹 批量管理」（总览表格 / 标签编辑 / 多选+二次确认批量删除）
  - 修一个 UX 缺陷：`st.success(...)` 紧跟 `st.rerun()` 导致**提示被丢弃、用户看不到确认**
    （AppTest 里 success 列表为空暴露出来的），改为 session_state flash，共 3 处
  - **未做**：分客户保存。三种方案与工作量已写入
    `.github/plans/2026-10-15-3-new-strategy-grouping-and-per-customer.md`，
    等确认后再实施；注意其中 owner 方案只是便于分类，**不是安全边界**
- **审计：未来函数与路径依赖**（见 `.github/plans/2026-10-15-4-audit-path-dependence-and-lookahead.md`）
  - ✅ 干净：`factors/technical.py` 的指标全为因果（无未来函数）；
    `signal/generator.py` 盘前取最后一根已完成 K 线正确；`filter.py` 的 `MIN_BARS=60`
    保证指标预热期不会流入打分
  - ⚠️ **发现 1（未修，已加代码内警告）**：`filter_universe` 用 `stock_list["is_st"]`
    （= **今天**的状态）排除 ST，而引擎开跑前一次性建池 → **用 2026 年的 ST 状态决定
    2022 年能不能买**。修它需要 ST 时点历史（现无此数据源），故在 `factors/filter.py`
    模块文档写入醒目警告
  - ⚠️ **发现 2（方法论，更重要）**：候选池只增 3.75% 就让总收益差 **+20.97pp**，
    而成交股票重合度仅 **Jaccard 0.226**（换掉 77%）、被测试的 ST 股自己只成交 8 笔。
    引擎经确认是确定性的（同输入两次差 0.0000pp）。**结论：单次 A/B 的差异由路径依赖
    主导，不能当成效应量。** 这推翻了我上一轮"幸存者偏差 −0.02pp ≈ 0"的说法 ——
    那只说明"噪声范围内看不出差异"
  - 🔧 **发现 3（已修）**：`_rsi` 的 `fillna(50)` 把"只涨不跌"的极限值 100 填成中性 50，
    使连续涨停的最强动量反而拿到 `trend_score` 的「50≤rsi≤70 → +20 分」奖励。
    实测影响面 590/7,472,101 股票-日（0.0079%）。修正后新增
    `tests/test_technical_edges.py` 6 个用例，反向验证（stash 掉修正）→ 2 failed
  - ⚠️ **发现 4（已加代码内警告，未改行为）**：回测是**同柱决策 + 同柱成交** ——
    用 `ts` 当天收盘算出的因子行打分，又按 `ts` 当天收盘价成交。
    而实盘路径（`generate_signals` + `paper.py`）是"盘前用**昨收**出信号、按今日收盘成交"，
    **两者成交口径不同，收益不能直接对比**。
    实测方向（路径无关，1008 笔逐笔）：次日开盘 / 当日收盘 − 1 的均值 **−0.103%**
    （中位数 0.000%，为正比例仅 34.2%）→ 改成次日开盘成交反而**更便宜**，
    所以当前约定对买入价偏保守、**没有虚高收益**。
    **又一次"机制存在但方向与预期相反"**。未测的是决策侧未来信息值多少
    （需把因子行滞后一根，但那会改变选股、落入路径噪声）
  - ✅ 本轮其他检查干净：`strategy/scorer.py`（纯加权无跨期统计量）、
    `simulator/paper.py`（盘前信号、无未来函数）、引擎因子预计算（全 `rolling`/`ewm` 因果）
  - 📌 待办（非缺陷）：`paper.py` 印花税用千一，而 A 股 2023-08-28 起已减半为 0.05%；
    偏保守、方向安全，若要贴近现实可按日期分段
- **审计发现 5（已修）：回测与实盘的因子列不一致，规则被静默跳过**
  - 两条路径各自内联构造因子行，列集**双向不一致**：
    `mom_ret`/`ma5/7/10/30` 只有回测有（实盘的动量规则被静默跳过）；
    `avg_amount20`/`macd`/`macd_signal` 只有实盘有（回测的流动性/MACD 规则被静默跳过）
  - 因为 `CustomStrategy.score` 用 `if "列" in df.columns` 守卫，**缺列不报错、直接跳过规则** ——
    用户看到的现象是"同一个策略在两个页面选出不同的股票"
  - 修法：统一两条路径的因子列集；**`avg_amount20` 在引擎里必须用 `rolling(20)`**，
    不能用 `bars["amount"].tail(20)`（后者取序列末尾，在逐日回测里就是未来数据）
  - 新增 `tests/test_factor_parity.py` 4 个用例锁死 schema 一致；
    **反向验证：stash 掉修正 → 4 failed，恢复 → 4 passed**
  - 过程中我自己写错三版用例（不可判别 / 用错 API / 测错方向），均已修正并记录
- **复审（2026-10-15 续）：未发现新 bug，审计出现收敛迹象**
  - 补上**数值级**一致性用例 `test_factor_values_match_between_paths`：
    `mom_ret`（`iloc[p-20]` vs `iloc[-21]`）、`high20_ratio`（`rolling(20).max()` vs
    `iloc[-20:].max()`）、`avg_amount20`（`rolling(20).mean()` vs `tail(20).mean()`）
    在同一根 K 线上窗口完全相同、数值一致 ✓
    `tests/test_factor_parity.py` 现 5 个用例，反向验证（`git checkout` 取回修正前版本）
    → **5 failed**，恢复 → **5 passed**
  - 扫描 7 个策略文件引用的因子列：**全部落在 21 列统一 schema 内**，
    不存在"读了 schema 外的列 → 规则在两条路径上都不生效"这类更隐蔽的问题
  - 确认动态列名 `ma{周期}` 的取值受 `_MA_CHOICES = (0,5,7,10,20,30,60)` 约束，
    且两条 LLM 路径都经过 `normalize_config`，schema 覆盖全部合法值 ✓
- **审计发现 6（已量化、未改默认值）：印花税统一费率高估了成本**
  - 印花税是卖方单边征收且**税率在历史上调整过**，而引擎与 `paper.py` 用统一标量（千一）
    覆盖整个回测区间。实测（1008 笔真实成交逐笔算、路径无关）：
    全程千一 = **3,962 元**，按日期分段 = **2,560 元**，**差额 1,403 元 = 初始资金的 1.40%**
  - 方向是**高估成本、偏悲观**（与此前几个乐观偏差相反，是个有用的对照）
  - ⚠️ **未核实**：具体税率与生效日期来自既有知识，本次会话 web 搜索与抓取均不可用。
    因此**没有改引擎默认值**（不把未经核实的事实写进金融模型）、也未据此断言收益数字；
    只把回测页费用设置里那句可能过时的「当前 A 股为千分之一」改掉，
    改成"卖方单边、历史上调整过、统一费率有偏差"并附实测 1.4%
- **更正我此前的一个错误判断**：我曾在计划文档里写"所有已保存策略都会被发布成公开策略，
  这本身就是一个问题"。**不成立** —— `README.md:220` 明确写了这是试用版的**文档化设计**
  （不提供用户权限、保存前有公开提示）。已在对应计划文档中更正
- **审计收尾 + 两个负结果/自我更正**（本轮）
  - `analysis/holding.py`：4 处 `except Exception` **都不吞错误**，各自把
    `f"...失败：{e}"` 返回给用户 ✅
  - `ui/views/visual_builder.py`：**不会**写出越界均线周期（`ma_options = ["关闭",5,7,10,20,30,60]`、
    `dev_ma` 选项 `[5,7,10,20,30,60]`，widget 本身即受限）；三类 bug 模式也无匹配 ✅
  - 清理：删除 `build_custom_factor_rows` 的无用导入，并把该函数改为**转发**
    `build_factor_rows`（列集统一后已冗余）。验证两者输出的**列集与数值完全一致**（21 列）
  - ⚠️ **ST 反推启发式实测失败**：设想用"ST 主板涨跌幅 ±5%"从 K 线反推 ST 区间，
    实测**ST 与非 ST 的特征中位数完全相同（0.0500）**，主板召回 19.3%、**精确率仅 4.2%**
    （比随机猜更差，非 ST 误判 631 只）。推理错误在于假设非 ST 股会频繁出现 >5.5% 单日波动，
    实测 ≥10% 的非 ST 股在 120 日内一次都没有
  - ⚠️ **我自己的工具陷阱**：此前用 `Select-String -Path 'src\stock_plan\**\*.py'` 做全库扫描，
    但 **PowerShell 5.1 的 `**` 只匹配一层目录**，`ui/views/`（两层）从未被搜到。
    已用 ripgrep 重做关键扫描：baostock 惰性导入、无负向 shift/居中窗口/`except: pass`
    等结论**均不受影响**；"死代码"那条证据不全已更正
- **暂时需求 4 收尾：分客户保存（按方案 B — owner 归属标签）**
  - `strategies` 表新增 `owner` 列（老库自动迁移、默认 `local`）+ 同库 `settings` 表存放"当前客户"
  - `store.current_owner()` / `set_current_owner()` / `set_owner()` / `all_owners()`；
    `list_strategies(owner=...)` 按客户过滤；`save_strategy` 不传 owner 时自动用当前客户
  - **归属冲突规则**：同名策略保存时**不改变 owner**（避免把别人的策略悄悄划到自己名下），
    改归属须显式 `set_owner()`；`delete_strategies(names, owner=...)` 限定范围，
    返回数小于请求数即表示有跨客户项被跳过
  - 策略管理页新增「当前客户」切换；列表、标签筛选、批量删除都在该客户范围内；
    总览表增加「归属」列；帮助文字里明确写出**这不是权限控制**
  - 新增 8 个用例（默认归属、当前客户持久化、按客户过滤、同名不夺归属、改归属、
    跨客户删除被拦、老库迁移到默认归属）。AppTest 实测：切到「客户-张三」只看到张三的、
    批量删除候选也只有张三的；绕过界面直接请求跨客户删除 → 只删掉 1 个，别人的仍在
  - 🐛 **修掉我自己在这次改动中引入的崩溃**：给批量管理加客户范围包裹 `if/else` 时，
    把 `victims = st.multiselect(...)` 留在了 `else` 里而用它的按钮在外层 →
    **切换到没有策略的新客户名就抛 `cannot access local variable 'victims'`**。
    AppTest 验证时抓到，已给 `victims`/`confirm` 预置默认值
- 验证：`compileall` 全包 0 错误；`pytest` **143 passed**；12 页 AppTest 全绿

## 2026-10-15

- **数据拉取并发化：全市场 75.5 → 13.7 分钟**（分支 `fix/data-fetch-performance`）
  - 根因：`fetcher.py` 的 `SINA_DAILY_LOCK` 包住了整个 `ak.stock_zh_a_daily` 调用，而该调用内部含 3 个串行
    HTTP（日线 blob / outstanding_share / 前复权因子），于是网络被一起串行化。实测 8 线程相对单线程仅
    **1.03x** 加速，`workers` 参数形同虚设
  - 锁要防的崩溃来自 akshare **每次调用都 `new` 一个 `py_mini_racer.MiniRacer()`**，多线程并发*创建* V8
    上下文才触发原生崩溃。改为线程本地单例、且只把"创建"这一步加锁，既消除崩溃成因又恢复并发
  - 保留环境变量 `STOCK_PLAN_SINA_LOCK=1` 可回退到旧的全局串行行为
  - A/B 实测（24 只 × 16 线程）：19.57s → **3.56s**；V8 上下文创建 16 次 / 24 请求 / 16 线程，证明复用生效
  - `workers` 默认 8 → 16（实测 16 线程 0.130s/请求，24/32 线程无进一步收益）
- **新增 `--rebuild` 全历史重建**（`updater.rebuild_history`）
  - 前复权因子是**回溯性**的：每次分红/送转都会改写整段历史价格，而"只补最近 30 天再合并"会让更早的行
    停留在旧因子上，导致历史序列在除权日人为跳空（实测间隔约 6 周：`000001` 偏 2.19%、`601318` 偏 1.74%）
  - 由于数据源每次请求都返回整份历史文件，"重拉 5 年"与"补 30 天"的网络成本几乎相同，因此定期全量重建划算
  - 实测 5553 只、9.6 分钟、0 失败；`000001` 11.88→11.62、`601318` 58.00→56.99，与直接重抓逐位一致
  - 已固化为每月 1 日的计划任务 `\StockPlan\Monthly_Rebuild`
- **数据层健壮性**
  - `Storage.save_bars` 改为临时文件 + `os.replace` 原子替换，避免重建期间被并发读取方读到半个 parquet
  - `Storage.last_date` 只读 `date` 列；`cache_exists` 单次遍历分桶（原来正反各调一遍 = 每只 stat 两次）
- **计划任务重写为可重注册的脚本 + XML**（`scripts/daily_job.ps1`、`scripts/register_tasks.ps1`、`scripts/tasks/*.xml`）
  - 旧任务的问题：`LogonMode=Interactive only`（未登录不跑）、动作用 `cmd /c ""...""` 包装含空格与中文的路径、
    指向已失效的旧目录与旧谱系，且 4 个增量更新任务已丢失
  - 新 XML 固定 `WakeToRun` / `StartWhenAvailable`（错过补跑）/ `DisallowStartIfOnBatteries=false` /
    `RunOnlyIfNetworkAvailable` / `ExecutionTimeLimit=PT3H`，日志落 `data/logs/daily_job.log`
- **安全**：`.streamlit/secrets.toml` 加入 `.gitignore`。该文件内含 GitHub PAT（Contents 读写），此前未被忽略，
  一次 `git add -A` 就会入库并推送。已确认它**从未进入任何提交或远端，无需轮换 token**
- **文档**：README / INSTRUCTION / plan 同步到 Phase 16，修正写死的 `D:\Vscode\...` 旧路径、README 内部
  自相矛盾的计划任务描述（§数据自动更新说 2 个任务、§开发进度却写 4 个）
- 验证：`pytest` **54 passed**；12 页 `AppTest` 全部渲染正常、0 异常

## 2026-09-04

- **本机计划任务与快照发布修复**（分支1）
  - 快照发布复用已有的本地 `data-snapshot` 分支，避免后续运行因同名孤儿分支失败
  - 本机自动行情更新调整为交易日 08:00 与 20:00；README 使用 `cmd.exe` 包装含空格的脚本路径，避免计划任务错误拆分路径
  - 新浪日线解码改为互斥调用，避免 AkShare 的 `mini_racer` V8 上下文在 8 线程更新中触发原生 DLL 崩溃
- **共享策略发布与全市场信号**（分支1）
  - 新增公共策略提交流程：命名、强制公开确认、名称唯一且不允许覆盖；策略名称、规则与参数明确面向所有用户公开
  - 新增 `strategy/publication.py`：配置指纹、待发布/已发布状态、GitHub `data-snapshot` JSON 同步、本机全 A 股 Top 20 信号计算与发布
  - 本机每日 `Snapshot_2130` 发布任务与打开 localhost 均会处理待发布策略；云端仅读取版本匹配的已发布结果
  - 今日信号页在公共策略待发布、配置过期或不支持的二次范围筛选时显示明确提示；模拟交易只接收该页的已发布信号
  - 回测结果与策略对比页新增云端 1800 只样本市场提示
- **云端回测/信号 OOM 修复**（分支1）
  - 新增 `Storage.load_market_maps()`：回测/对比/拼装/信号统一走「日期窗口 + 板块/ST 预筛选 + 数量上限」加载，指标预热 180 个日历日
  - 新增云端常量 `CLOUD_MAX_CODES = 1800`：云端回测/信号参与计算的股票数上限（等间隔抽样保留板块分布），避免全市场 5553 只全量加载撑爆 2.7GB 容器
  - 重构 4 处全量加载调用点：`backtest.py`、`compare.py`（对比 + Walk-Forward）、`visual_builder.py`、`signal/generator.py`（信号仅加载最近 180 天窗口）
  - 新增 `tests/test_storage_market_maps.py` 11 个用例，pytest 41/41 通过；受影响 4 页 AppTest 无异常
- **Phase 16 云端数据快照持久化**（分支1）
  - 新增 `src/stock_plan/data/snapshot.py`：打包 bars 为分卷 zip + manifest 双 sha256 校验；git worktree 孤儿分支 `data-snapshot` 单 commit force push 发布；下载校验 + 原子替换恢复；`is_cloud` / `cloud_secrets_env` 云端检测
  - `app.py`：启动时本地无缓存数据（<100 只）自动从云端快照恢复，带 `st.status` 进度
  - 「🔄 数据更新」页：云端/本机模式徽章、手动「从云端快照恢复」入口、云端轻量参数（增量并发 3 线程、全量拉取 366 天）
  - 新增 `scripts/publish_snapshot.py` + `scripts/publish_snapshot.cmd`：本机发布 CLI（`--build-only` 仅打包）
  - 首份快照已发布并验证：5553 只 / 299MB / 4 卷（raw.githubusercontent.com 返回 200）
  - 注册 Windows 计划任务 `\StockPlan\Snapshot_2130`：每日 21:30 自动发布快照
  - 新增 `tests/test_snapshot.py` 9 个用例，pytest 31/31 通过
  - 新增 `CHANGELOG.md`；同步 README（云端快照章节）、plan.md（Phase 16 行）、INSTRUCTION.md（3 条决策记录）
  - 修复 snapshot 子进程调用 git 在 Windows 下 GBK 解码失败的问题（`encoding="utf-8"`）

## 2026-09-03

- **Phase 15 云端部署准备**（分支1）
  - 新增 `requirements.txt`（Streamlit Community Cloud 依赖，版本按本地验证固定）
  - `llm/config.py` 支持 `st.secrets` 回退，云端 LLM Key 走 Secrets
  - 「🔄 数据更新」页按平台显示说明；空数据提示统一指向该页
  - README 新增云端部署章节
  - 修复今日信号/回测页选择未编辑参数策略时 `TypeError: 'NoneType' object is not a mapping` 崩溃（`saved` 兜底 `{}`）
- **Phase 14 定时数据自动更新**（分支1，commit fd14af5）
  - `data/updater.py` 增量更新引擎：交易日历、幂等预筛、8 线程并发、按日期去重合并
  - Windows 计划任务 4 时点自动增量拉取（交易日 16:00 / 20:00 / 次日 0:00 / 8:00）
  - 新增左侧「🔄 数据更新」页：手动更新 + 实时进度 + 未缓存股票补拉 + 更新日志
