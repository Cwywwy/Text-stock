# 策略复筛（全市场）与三档择时（默认关闭）计划

- **状态**：**已完成**（2026-10-29 18:56，Phase 18–22 全部完成，计划已归档）
- **日期**：2026-10-29
- **分支**：`分支6`（与 `origin/分支6` 已同步，HEAD `78f86e9`）；不合并 `main`、不推送其它分支
- **环境**：Python 3.13.15 + pandas 3.0.5 + Streamlit 1.63.0；`.venv\Scripts\python.exe`；16 逻辑核 / 31.1 GB
- **依据**：归档计划 `.github/plans/done/2026-10-29-5-new-backtest-performance.md` §9.5（T-1/T-2/T-4 原始条款）、T0 体检报告 `tests/reports/2026-10-29-07-23.timing-health.md`、Phase 17 终验报告 `tests/reports/2026-10-29-10-40.report-1.md`、用户 2026-10-29 13:19 指令与 13:29 六问回答
- **与上一份计划的关系**：`.github/plans/2026-10-29-6-turnover-reduction-and-timing-oos-validation.md` 的 T-4（换手治理）由用户决定**暂缓**（后续自行推进）、T-7（影子跟踪与末 6 个月 OOS）**取消**；本计划接续新的目标，不修改其预注册条款（保留以便将来续做）

---

## 0 目标与范围

### 0.1 目标

1. **三档择时（新能力，默认关闭）**：把现有「弱择时」（只对新买入减半、不减持持仓）扩展为「双均线三档 + 对已持仓真实降仓」——满仓 100% / 半仓 50% / 空仓 0%，作为可选开关，**默认关闭时逐值等价旧行为**。
2. **全市场复筛矩阵**：用 branch6 提速后的引擎（Phase 14–17），把此前在「800 只抽样 + 旧引擎」下判过的候选（收敛因子 5 变体、动量 3 模式、RRG 行业轮动）在全市场、固定数据快照、三种择时口径下重跑，按**预注册双层判定**给出通过/不通过。
3. **交付可复核证据**：全市场基线、复筛矩阵报告、三档择时对比报告、UI 下拉（默认关闭）、README/CHANGELOG 同步。

### 0.2 不做（边界）

1. **T-4 换手治理暂停**：不实装 `min_hold_days` / `replace_score_gap`、不做换手方案矩阵；相关阈值（≤12、G1/G2/G3）原样保留在 plan 6，将来续做时不得改动。
2. **T-7 影子跟踪取消**：不做 2026-11 起 3 个月影子、不做末 6 个月 OOS 判定（用户 2026-10-29 决定）。
3. **不做新择时因子**：不加广度、波动率、量能等联合判定；三档只用「等权指数 vs MA20/MA60 + MA20 斜率」。
4. **不做参数寻优**：三档规则与候选参数（收敛权重、动量模式、RRG top_n）跑数前固定，训练窗口只用于「选候选」，验证窗口只跑一次（§4.3）。
5. **不改策略打分公式与因子口径**（新增的 `conv_variant` 选择参数默认 `conv_pcf`，默认路径逐值不变）。
6. 不引入新依赖；不做跨进程并行扩展（沿用 harness 的 arm 级并发）；不改回测成交口径（滑点/佣金/印花税/成交时点不动）。
7. **不动用户可见默认值**：新能力默认关闭，UI 只加下拉（默认「关闭」）；`market_timing` 复选框保持现状默认（True，弱择时），与新档位互斥时以新档位优先并在 UI 文案说明。
8. 不合并 `main`、不推送其它分支；提交后是否推送 `origin/分支6` 由用户决定（§10-3）。

---

## 1 现状与依据

### 1.1 基线（branch6 已含 Phase 14–17）

| 项 | 值 |
| --- | --- |
| 全量测试 | **637 passed**（Phase 17 终验） |
| 全市场股票数 | 5378 只（日线 5579 个文件，含退市/北交所） |
| 单策略 5 年（load + prepare + backtest） | 6.25 s + 90.73 s + 37.94 s ≈ **135 s** |
| 单策略 3.3 年（训练窗口预计） | ≈ 120 s |
| 单策略 1.3 年（验证窗口预计） | ≈ 110 s（prepare 固定 ~90 s 占大头） |
| 抽样 800 只单 arm | 17–27 s（Phase 17，仅作对照） |

> 结论：全市场矩阵可行，但 `prepare`（因子表构建）是固定成本——每 arm 必须独立起进程，所以总时长 ≈ arm 数 × ~2 min ÷ 并发数。

### 1.2 数据指纹（Phase 14/15/17 共用，2026-10-29 08:20 记录）

| 项 | 值 |
| --- | --- |
| bars 文件数 | 5579 |
| bars listing sha256 | `24fa553ba5020d1d6d6f9389424ca936431581342ddf572d786b0567121c0b37` |
| meta.db sha256 | `eea568d1555b9dff6f731f003be6fef8040ec46154789fa5361e94a006ff0d85` |
| 200 只抽样内容 sha256 | `7b5b4d8e…`（完整值见 `tests/reports/2026-10-29-08-52.report-1.md` §3） |
| stock_list 行数 | 5575 |

**每日任务 06:49 / 08:00–08:02 会刷新股票池、估值与部分 K 线** → 每个 Phase 开始/结束重新记录指纹；不一致时按「同数据重跑基线」处理，不混用旧数字。

### 1.3 复筛候选清单（全部为已实现资产，无需从零实现）

| 族 | 候选 | 现有位置 | 固定参数（预注册，不寻优） |
| --- | --- | --- | --- |
| 收敛因子 | PCF / VCF / PVCF / ACF / TRCF | `factors/convergence.py`；`builtin.py`、`breakout.py` 的 `conv_weight` | `conv_weight=0.2`（五个变体同一权重，仅切换 `conv_variant`） |
| 动量方向 | `ret20`（现状）/ `ma_ratio` / `ma_ratio_blend` | `strategy/momentum.py` 的 `momentum_mode` | 无自由参数 |
| RRG 行业轮动 | C1（31 个申万一级等权指数，top_n=6，20 日调仓，10bp） | `factors/rrg.py` + `backtest/rotation.py` | `top_n=6`、`rebalance_days=20`、`cost_bps=10` |
| 对照基线 | 三只受影响策略（趋势跟随 / 突破 / 动量）无候选版本 | `strategy/registry.py` | 策略默认参数 |

> 说明：`conv_variant` 是 Phase 19 新增的**选择参数**（默认 `conv_pcf`），因为现有实现把列名写死为 `conv_pcf`；这是「允许换列」而非改公式，默认路径逐值不变，有 parity 兜底。

---

## 2 已确认口径（2026-10-29 13:29 六问回答，跑数前固定，事后不得改）

| 编号 | 问题 | 用户确认 |
| --- | --- | --- |
| A | T-4 换手治理 | **暂缓**（用户后续自行推进）；本轮不实装杠杆、不跑换手矩阵 |
| B | T-7 范围 | **影子跟踪与末 6 个月 OOS 取消**；改为把弱择时扩成「可调仓位档位」并纳入复筛矩阵 |
| C | 复筛范围 | **全矩阵**：每个候选都叠三档择时；覆盖收敛 5 变体 × 2 策略、动量 3 模式、RRG 1 组 + 基线 |
| D | 规模 | **全程全市场**（5378 只，`--max-codes 0`），不做抽样结论 |
| E | 三档规则 | 双均线三档：`> MA20 → 100%`；`跌破 MA20 → 50%`；`跌破 MA60 且 MA20 向下 → 0%`；**对已持仓真实降仓**（卖出超额部分） |
| F | 判定口径 | **双层判定**：候选层（vs 同策略无候选）+ 择时层（vs 同候选恒满仓），阈值见 §4.2；全矩阵跑完不得改 |

---

## 3 三档择时设计（Phase 19 实装，默认关闭）

### 3.1 规则（固定，不可调参）

大盘代理沿用引擎现有实现：候选池内股票的**等权收益指数**（`regime.equal_weight_index`，`min_contributors=max(1, min(20, len(codes)))`）。

| 条件（按优先级） | 目标仓位 |
| --- | --- |
| `close ≥ MA20` | **100%** |
| `close < MA20` 且不满足下一行 | **50%** |
| `close < MA60` 且 `MA20 向下` | **0%** |

- `MA20 向下` 定义固定为 `MA20[t] < MA20[t−5]`（5 个交易日斜率，严格小于）。
- 预热期（MA60 未成形，前 ~60 个交易日）按「100%」处理并在报告注明；不足 20 个交易日时按 100%。
- 档位只用收盘后数据计算，**次日生效**（防止用当日盘中不可得信息）：第 t 日收盘算出档位，第 t+1 日的仓位校准按该档位执行。

### 3.2 降仓语义（关键差异）

| 情形 | 行为 |
| --- | --- |
| 档位由 100% → 50% / 0% | 当日按目标仓位**卖出超额部分**（按持仓市值比例，100 股整手，最少卖 100 股；不足 100 股不卖） |
| 档位由 50% → 100% | 不做额外买入；只放开日常买入预算上限（不追买） |
| 当日新买入 | 仍受现有 `top_n`、现金、整手、涨跌停约束；档位 0% 时不开新仓 |
| 跌停/停牌无法卖出 | 跳过该持仓（记录到报告「未能降仓」清单），次日继续尝试 |
| 当日买入的持仓 | 受 T+1 约束，不参与当日降仓（沿用引擎既有约束） |

### 3.3 参数与默认值

```text
BacktestConfig.timing_tier: str = "off"      # off | dual_ma
```

- `off`（默认）= 旧行为：不构造大盘指数、不校准仓位，与 HEAD 逐值一致。
- `dual_ma` = 启用三档；与现有 `market_timing=True` 同时开启时**以 `timing_tier` 为准**并在报告/UI 注明（矩阵中两者互斥：`market_timing=False` + `timing_tier=dual_ma`）。
- RRG 侧：`run_industry_rotation(..., timing_tier: str = "off")`，同一规则，作用于行业组合总暴露（超配卖出按权重比例，成本沿用 10bp 单边）。

### 3.4 UI 敞口（默认关闭）

- 回测页 / 策略对比页 / 可视化构建页各加一个「大盘择时档位」下拉：`关闭（默认）` / `双均线三档`。
- 现有「大盘择时（弱）」复选框保留，文案改为「弱择时（仅对新买入减半）」，与新档位互斥时以新档位优先并显示提示。
- 默认「关闭」→ 用户不改控件时，结果与当前 UI 行为**完全一致**（因为 `market_timing` 默认值不变）。

---

## 4 复筛矩阵设计

### 4.1 矩阵与 arm 数（全市场，`max_codes=0`）

| 维度 | 取值 |
| --- | --- |
| 窗口 | W1 训练 `2022-03-07~2025-06-30`、W2 验证 `2025-07-01~2026-10-28`、W3 全期 `2022-03-07~2026-10-28`（`lead_days=400`） |
| 择时口径 | `off`（恒满仓）、`weak`（现有弱择时）、`tier`（三档） |
| 候选 | 收敛 5 变体 × 2 策略、动量 3 模式 × 1 策略、RRG 1 组、基线 3 策略 |

| 组 | arm 数 |
| --- | ---: |
| 基线（3 策略 × 3 窗口 × 3 口径） | 27 |
| 收敛（5 变体 × 2 策略 × 3 窗口 × 3 口径） | 90 |
| 动量（3 模式 × 1 策略 × 3 窗口 × 3 口径） | 27 |
| RRG（1 组 × 3 窗口 × 3 口径，独立 runner） | 9 |
| **合计** | **153** |

**耗时估算**：153 arm × ~2 min ÷ 并发 5–6 ≈ **1.0–1.7 小时**（含 CPU/内存争用），分 2–3 段后台跑；RRG 9 arm 单独跑（行业层，单 arm 秒级）。

### 4.2 预注册判定（跑数前固定）

**候选层**（同一策略、同一窗口、同一择时口径，候选 vs 该策略的「无候选」版本）：

| 代号 | 条款 |
| --- | --- |
| C-1 | 年化收益超额 **≥ +2.0pp** |
| C-2 | 最大回撤不劣于基线 **3pp** 以上（允许最差 +3pp） |
| C-3 | 分年度超额中，**≥3 个年度里 ≥2 年为正** |

→ **通过 = C-1 且 C-2 且 C-3**；否则不通过（记录，不重试、不改参数）。

**择时层**（同一候选、同一窗口，`weak` / `tier` vs `off`）：

| 代号 | 条款 |
| --- | --- |
| M-1 | 最大回撤改善 **≥ 3pp** |
| M-2 | 年化收益下降 **≤ 2pp** |
| M-3 | ≥3 个年度里 ≥2 年相对 `off` 更好 |

→ **帮忙 = M-1 且 M-2 且 M-3**；**帮倒忙 = M-1 改善为负 或 M-2 恶化 >2pp**；其余 = **无差别**。

**判定只用 W2（验证窗口）**；W1 仅用于「从同族候选中选一个」（例如 5 个收敛变体只能选 1 个进 W2 判定），W3 为信息性展示。若 W1 里同族候选全部不通过，则该族不进 W2，直接记为不通过。

**必附（不参与判定）**：换手（单边年化）、成本拖累、交易笔数、胜率、基准收益、分年度表、月度极值（错过/多得 Top5）、未能降仓清单、数据指纹。

### 4.3 防过拟合纪律

1. 候选参数固定（§1.3），不在 W1/W2 上调整；W2 只跑**一次**，报告记录跑数次数与时间戳。
2. 三档规则阈值固定（MA20/MA60、斜率 5 日），不做敏感性寻优；如需敏感性，仅追加报告附录，不参与判定。
3. 若 W2 结果与 W1 冲突，如实并列写出，不挑有利的一个。
4. 全部结论必须写明「全市场 5378 只、单快照、区间 4.6 年」的适用范围。

---

## 5 模块划分

| 模块 | 职责 | 交互 | 状态 |
| --- | --- | --- | --- |
| `backtest/engine.py` | 新增 `timing_tier` 参数、大盘档位计算、每日仓位校准（真实降仓） | `BacktestConfig` → `run_backtest` 主循环；`prepare_backtest` 不动 | 修改（默认关闭） |
| `backtest/rotation.py` | RRG 组合层同一三档暴露校准 | `run_industry_rotation(..., timing_tier=)` | 修改（默认关闭） |
| `strategy/builtin.py`、`strategy/breakout.py` | 新增 `conv_variant` 选择参数（默认 `conv_pcf`） | `conv_weight × z(conv_variant)` | 修改（默认等价） |
| `ui/views/backtest.py`、`compare.py`、`visual_builder.py` | 新增「大盘择时档位」下拉（默认关闭）+ 弱择时文案 | `BacktestConfig.timing_tier` | 修改 |
| `scripts/measure_factor_ab.py` | 无需改（`config` 字段已可传 `timing_tier`）；新增 spec 生成脚本 | spec JSON | 复用 + 新增 spec |
| `scripts/build_rescreen_specs.py`（新） | 按 §4.1 拼 153 arm spec（可行内联到报告脚本） | 读候选清单 → 写 spec | 新增 |
| `scripts/turnover_report.py`（新，可选） | 从 `BacktestResult.trades` 出换手/成本/退出原因（T-4 暂停后仍需报告换手） | 复用 `calc_turnover` | 新增 |

---

## 6 Phase 拆分与完成标准

### Phase 18 — 数据冻结与全市场基线（无行为改动）

**步骤**

1. 记录数据指纹（bars 文件数、listing sha256、meta.db sha256、200 只抽样 sha256、stock_list 行数），与 §1.2 比对。
2. 跑全市场基线 21 arm：7 策略 × 3 窗口 × `timing=off`（`max_codes=0`，`jobs=5`），JSON 入 `tests/reports/<ts>.rescreen-baseline.json`。
3. 与 `tests/reports/2026-10-29-08-52.phase14-head-baseline.json` 中同策略/同窗口的抽样 arm 对比：**应不同**（全市场 vs 800 抽样），报告必须写明「口径不同不构成回归」，回归判定只看 Phase 19 的默认关闭对照。

**预期产出**：全市场基线 JSON + 指纹记录表

**完成标准**：21 arm 全部成功；指纹与 §1.2 一致（或已按「同数据重跑」处理并注明）；`pytest` ≥637。

**commit**：`(phase 18) add full market baseline for strategy rescreen`

**执行记录（2026-10-29 17:08）**

- 数据指纹：bars 5579 个；bars 清单 sha256 `1e8cf9c7…`、`meta.db` sha256 `9447ffb2…` 与 §1.2 记录（`24fa553b…` / `eea568d1…`）**不一致**——`data/update.log` 显示 16:34 有一次增量更新（updated=5575）。按 §1.2 处理：本次不沿用任何旧基线数字，21 arm 全部在新数据上重跑，报告注明「同快照内可比」。
- 21 arm 全部成功（`max_codes=0`，全市场载入 5379 只、有效参与 5182 只，`jobs=5`，总耗时约 4 分钟）。产物 `tests/reports/2026-10-29-17-08.rescreen-baseline.json` / `.md`。
- 结论：W1/W2/W3 三个窗口 7 策略全部跑输等权基准（W1 基准 +21.95%、W2 +8.31%、W3 +32.67%）；W2 有 3 只转正但均低于基准。年单边换手 14.0–22.2 倍，成本拖累 5.0–8.0pp/年。
- 与 Phase 14 的 800 只抽样基线**口径不同、数字不同**，按步骤 3 只作信息性对比，不作回归依据。

### Phase 19 — 三档择时实装与默认关闭证明

**步骤**

1. `BacktestConfig.timing_tier: str = "off"`；`run_backtest` 内构造大盘等权指数 + MA20/MA60 + 5 日斜率；每日按 §3.2 校准仓位（真实降仓，100 股整手，跌停/停牌跳过，T+1 豁免当日买入）。
2. `run_industry_rotation(..., timing_tier="off")` 同上（组合暴露校准，成本 10bp）。
3. `conv_variant` 参数（趋势跟随 / 突破，默认 `conv_pcf`）。
4. UI：三页加下拉（默认「关闭」），弱择时文案调整。
5. 新增 `tests/test_engine_timing_tiers.py`（规则与降仓 12+ 用例）、`tests/test_rotation_timing.py`（4+ 用例）、`conv_variant` 默认等价用例。

**预期产出**：引擎/UI 改动 + 单测 + 默认关闭证明

**完成标准**：
- **默认参数下 21 arm 与 HEAD 基线逐字段 0 差异**（`timing_tier=off` 分支不触发任何新逻辑）；
- 三档规则单测覆盖：MA20/MA60 边界、斜率边界（相等不算向下）、预热期、0% 档清仓、50% 档只减不追、跌停不卖、整手取整、不足 100 股不卖、T+1；
- UI AppTest 冒烟通过；`pytest` ≥637。

**commit**：`(phase 19) add dual ma timing tiers with default off`

**执行记录（2026-10-29 17:30）**

- 实装：新增 `src/stock_plan/factors/timing.py`（档位规则 + 敞口序列，`shift(1)` 防未来函数）；`BacktestConfig.timing_tier`（默认 `"off"`，`__post_init__` 校验）；引擎每日真实降仓（按比例卖出、100 股整手、跌停/停牌跳过、T+1 豁免、买入侧只封顶不追买）；`run_industry_rotation(..., timing_tier=)` 同口径；`conv_variant`（pcf/vcf/pvcf/multi，默认 pcf）；UI 三页下拉（默认关闭）+ 缓存键纳入档位。
- 默认关闭证明：①21 arm（7 策略 × full/win_a/win_b，`max_codes=800`）与 `2026-10-29-08-52.phase14-head-baseline.json` **逐字段 0 差异**（metrics/annual/turnover/benchmark_return/codes/equity_points，容差 1e-12），产物 `tests/reports/2026-10-29-17-20.phase14-parity.json`；②同一全市场 spec 实现中途（16:52）与完成后（17:08）两次跑批，21 arm 六个指标逐值相同；③`off` 分支不构造档位序列、不进入降仓代码。
- 单测：`tests/test_engine_timing_tiers.py` 17 例、`tests/test_rotation_timing.py` 4 例、`tests/test_backtest_pages.py` +2 例 AppTest 冒烟；全量 pytest **654 passed**（基线 637）。
- 实验报告：`tests/reports/2026-10-29-17-30.phase18-19-timing.md`；测试报告：`tests/reports/2026-10-29-17-30.report-1.md`。

### Phase 20 — 全市场复筛矩阵与双层判定

**步骤**

1. 生成 spec（`.github/plans/specs/2026-10-29.rescreen-matrix.spec.json`）：153 arm，按 §4.1；分批后台跑（每批 ≤50 arm，记录开始/结束指纹）。
2. RRG 9 arm 单独跑（行业层 runner）。
3. 按 §4.2 判两层：候选层先出（W1 选候选 → W2 判定），再用 W2 判择时层。
4. 报告 `tests/reports/<ts>.rescreen-matrix.md`：153 arm 总表（分族分窗分口径）、双层判定表、分年度表、换手/成本、月度极值、未能降仓清单、数据指纹、跑数次数。

**预期产出**：矩阵 JSON + 双层判定报告

**完成标准**：153 arm 全部成功或明确记录失败原因；判定表逐条给出「通过/不通过/帮忙/帮倒忙/无差别」及理由；W2 只跑一次（报告记录）；`pytest` ≥637。

- [x] 矩阵与 RRG 全部 arm 跑完（实际 150 arm = 144 股票 + 6 RRG，成功 150、失败 0；与计划的 153 差异见执行记录）
- [x] 双层判定表逐条给出结论与理由（`tests/reports/2026-10-29-18-45.rescreen-judgement.md`）
- [x] W2 只做一次判定性跑批（明细回放另跑 12 arm 仅用于资金曲线披露，指标与矩阵逐值一致，见执行记录）
- [x] `pytest` ≥637（实际 656 passed）

**执行记录（2026-10-29 18:44）**

- 跑批：基线 21 arm（17:08，全市场 5379 只）；矩阵 144 arm（17:36–18:30，jobs=4）；RRG 6 arm（17:43–17:45）；明细回放 12 arm（18:40–18:43，含资金曲线与降仓失败记录）。
- 产物：`tests/reports/2026-10-29-17-36.rescreen-matrix.{json,md}`、`2026-10-29-17-45.rescreen-rrg.json`、`2026-10-29-18-40.rescreen-detail.{json,md}`、`2026-10-29-18-45.rescreen-judgement.{md,json}`；spec 与指纹在 `.github/plans/specs/`（`rescreen-matrix`、`rescreen-rrg`、`rescreen-detail`、`phase20-fingerprint`）。
- 数据冻结：跑批前（16:48）与跑批后（18:38）指纹逐项一致（bars 5579、meta.db、200 只抽样），期间唯一的 `update.log` 差异是 17:16 一条 `updated=1` 的 uncached 增量记录，不影响数据内容。
- **候选层 W1 入选**：收敛族 `pvcf/突破策略` 年化超额 +3.33pp（4/4 年为正）；动量族 `ma_ratio_blend/动量策略` +11.77pp（3/4）。族内其余变体/模式不入选。
- **候选层 W2 判定：无一通过**。`pvcf/突破` 超额 +1.24pp（C-1 ❌，门槛 +2.0）；`ma_ratio_blend/动量` 超额 +10.20pp（C-1 ✅）但 C-3 ❌（2025 下半年 -11.4% vs 基线，2026 才反超），即「单年主导、不稳定」。
- **择时层（选中候选，W2）**：`weak` 两组都「帮忙」（回撤改善 +3.57 / +7.98pp，年化 +9.00 / +9.78pp）；`tier` 两组「无差别」——回撤改善很大（+20.03 / +16.06pp）、年化 +18.13 / +4.23pp，但 M-3 只有 1/2 个年度更好，按预注册规则不算「帮忙」。
- **全矩阵信息性汇总（26 组）**：`weak` 帮忙 5、帮倒忙 3；`tier` 帮忙 1（基线动量 +34.54pp）、其余多为「无差别」。tier 的收益改善主要集中在 2026 下跌段（月度极值表：2026-07 躲开 +13~+20pp、2026-10 +7~+11pp，同时 2026-04/09 错过 -5~-8pp），符合「单年主导」判断。
- **RRG（6 arm）**：行业层 W1 超额 +0.46pp（< +2.0 → C-1 不通过），W2 -0.50pp、W3 -0.53pp；`dual_ma` 档位改善回撤 16–17pp、年化 +4.5~+7.3pp（W3 判「帮忙」）。因此 C2（个股层）按预注册规则不做，与 Phase 9 结论一致。
- **未能降仓**：tier 口径下回放记录 24–31 条/策略，集中在降档日；原因包括跌停/停牌卖不出、当日买入 T+1 豁免、仓位不足一手与整手取整——属于小资金 + 100 股整手的执行残差，已写进报告供后续评估。
- **与计划书的差异（诚实披露）**：①计划 153 arm，实际 150（144 股票 + 6 RRG）：RRG runner 没有「弱择时」口径，故只有 off/dual_ma 两档（3 窗口 ×2 = 6）；②判定在「族」层次做（收敛 5 变体选 1、动量 3 模式选 1），避免把同族多次尝试当多个发现；③W2 只有 2 个自然年度，C-3/M-3 按「可用年度全数为正」执行；④明细回放为了拿到资金曲线与降仓清单，重跑了 12 个 W2 arm（指标与矩阵逐值一致，不构成第二次判定）。
- 测试：全量 `pytest -q` **656 passed / 145.95s**（上一阶段 652）；报告 `tests/reports/2026-10-29-18-46.report-1.md`。

**commit**：`(phase 20) rescreen strategies on full market with timing tiers`

### Phase 21 — 结论落地（默认关闭）与文档

**步骤**

1. 依据 Phase 20 结论：**不修改任何用户可见默认值**（用户已定：默认关闭）；仅把「三档择时」下拉与说明写入 README 回测章节。
2. CHANGELOG 追加（简体中文、按日期）；README 增加「大盘择时档位（可选，默认关闭）」小节与限制说明。
3. 报告 `tests/reports/<ts>.rescreen-decisions.md`：每条结论 + 证据链（JSON/报告路径 + 数据指纹）+ 未通过项的替代建议。
4. 计划书勾选完成标准；写最终测试报告。

**完成标准**：README/CHANGELOG 与实现一致；默认值未变（21 arm 0 差异复核）；`pytest` ≥637；工作区干净。

- [x] README 增加「大盘择时档位（可选，默认关闭）」小节，CHANGELOG 追加本条（续 13）
- [x] 决策报告 `tests/reports/2026-10-29-18-50.rescreen-decisions.md`（逐条结论 + 证据链 + 替代建议）
- [x] 计划书勾选完成标准；`pytest` 656 passed（≥637）
- [x] 未改任何用户可见默认值（`timing_tier="off"`、7 策略参数、`momentum_mode`/`conv_variant`/`conv_weight` 全部原样）

**执行记录（2026-10-29 18:50）**

- README §「复筛与三档择时」补 Phase 21 行与「大盘择时档位（可选，默认关闭）」小节（口径、降仓语义、限制、实测结论）。
- 决策报告落盘：候选人层（收敛/动量/RRG）全部「不通过」、择时层 `weak` 帮忙但 `tier` 无差别，并写明「证据链、跑数次数、未决事项」。
- 默认值冻结声明：三档择时默认关闭；本轮未新增/修改任何默认路径，故 Phase 19 的 21 arm 0 差异结论继续有效。
- 未做：README 里「UI 默认开弱择时、A/B 默认关」的口径统一（需用户单独拍板，已写入未决事项）。

**commit**：`(phase 21) document timing tiers and rescreen conclusions`

### Phase 22 — 归档与总结

**步骤**

1. 最终全量 `pytest` + 报告落盘。
2. 把本计划归档到 `.github/plans/done/`，更新历史报告里的链接（若有）。
3. 给用户中文总结：改动、数字前后对比、证据链、commit 列表、风险与未决事项。

**完成标准**：归档完成、链接有效、总结含全部要求项。

- [x] 最终全量 `pytest` 与报告落盘（`tests/reports/2026-10-29-18-56.report-1.md`）
- [x] 计划归档到 `.github/plans/done/`，引用链接已同步（README / CHANGELOG / 计划 6 / 决策报告）
- [x] 给用户中文总结：改动、数字前后对比、证据链、commit 列表、风险与未决事项

**执行记录（2026-10-29 18:56）**

- 归档路径：`.github/plans/done/2026-10-29-7-new-strategy-rescreen-and-timing-tiers.md`。
- commit 链（本计划）：`6a472ea` Phase 18 → `6995f19` Phase 19 → `5b0b40c` Phase 20 → Phase 21 → Phase 22。
- 最终数字：pytest **656 passed**（Phase 18 起点 637）；矩阵 150 arm（144 + 6）；W2 判定「候选层无一通过」；默认值全部未变。
- 给用户的总结要点：①三条候选线都不成立（收敛 pvcf W2 +1.24pp、动量 blend 单年主导、RRG +0.46pp）；
  ②择时 `weak` 帮忙、`tier` 单年主导（不通过）；③所有结论都在同一份数据 + 预注册门槛下得出，未调参。
- 状态改为 **已完成**。

**commit**：`(phase 22) archive rescreen plan and summarise results`

---

## 7 测试策略

| 层级 | 内容 |
| --- | --- |
| 单元 | 三档规则（边界/斜率/预热）、降仓数量与整手、跌停跳过、T+1 豁免、0% 档清仓、50% 档只减不追、RRG 暴露校准、`conv_variant` 默认等价 |
| 对拍 | 默认关闭时 21 arm（7 策略 × 3 窗口）与 HEAD 基线逐字段 0 差异；`tests/test_factor_parity.py`、`test_engine_perf_parity.py`、`test_backtest*.py` 保持全绿 |
| 契约 | UI AppTest 三页冒烟（下拉存在、默认「关闭」、切换不报错） |
| 回归 | 每 Phase 全量 `pytest`（基线 637）→ `tests/reports/<YYYY-MM-DD-HH-mm>.report-n.md`（同分钟递增） |
| 数据 | 每个 Phase 前后记录指纹；不一致按「同数据重跑基线」并注明 |

---

## 8 风险与限制

| 编号 | 风险 | 应对 |
| --- | --- | --- |
| R1 | 三档择时**必然增加卖出换手与成本**（尤其 50%/0% 频繁切换） | 报告中单独列「择时新增换手/成本」，与收益/回撤并列；若成本吃掉收益则如实判「帮倒忙」 |
| R2 | 三档依赖固定阈值（MA20/60、5 日斜率），换区间可能失效 | 阈值固定不调；报告给「分年度 + 月度极值」暴露失效月份，并注明未做敏感性寻优 |
| R3 | 全市场 153 arm 耗时长（1–1.7 h）、并发内存压力 | 分批后台跑（每批 ≤50 arm），`jobs≤6`，跑前记录内存与指纹；失败 arm 记录原因不重试超 1 次 |
| R4 | 数据每日刷新导致基线不可复现 | 每 Phase 记指纹；指纹变化时同数据重跑基线，不与旧数字混用 |
| R5 | 复筛结论可能「全军覆没」 | 不硬改参数；如实报告并给替代方向（换手成本显式建模、择时档位单独评估、样本外跟随） |
| R6 | 三档与现有 `market_timing` 并存易混淆 | UI 文案互斥提示；矩阵中始终 `market_timing=False`；报告口径表写清 |
| R7 | RRG 是行业层结果，不可与股票引擎直接比 | 单独表 + 单独基准（31 行业等权）+ 注明口径不可跨表比较 |
| R8 | 用户可见默认值被误改 | 默认值冻结；Phase 19 用 21 arm 0 差异证明；任何改动需再次批准 |

---

## 9 预计新增/修改/删除文件

**新增**

- `.github/plans/2026-10-29-7-new-strategy-rescreen-and-timing-tiers.md`（本计划）
- `.github/plans/specs/2026-10-29.rescreen-matrix.spec.json`
- `scripts/build_rescreen_specs.py`（可并入报告脚本）
- `tests/test_engine_timing_tiers.py`、`tests/test_rotation_timing.py`
- `tests/reports/<ts>.rescreen-baseline.json`、`<ts>.rescreen-matrix.{json,md}`、`<ts>.rescreen-decisions.md`、各 Phase `<ts>.report-n.md`

**修改**

- `src/stock_plan/backtest/engine.py`（`timing_tier` + 档位/校准逻辑）
- `src/stock_plan/backtest/rotation.py`（RRG 暴露校准）
- `src/stock_plan/strategy/builtin.py`、`src/stock_plan/strategy/breakout.py`（`conv_variant`）
- `src/stock_plan/ui/views/backtest.py`、`compare.py`、`visual_builder.py`（下拉 + 文案）
- `README.md`、`CHANGELOG.md`（Phase 21）
- `.github/plans/done/`（Phase 22 归档）

**删除**：无

---

## 10 待批准事项（还有 3 个小口径，批计划时一并给答复即可）

1. **收敛权重固定 0.2**：5 个变体统一 `conv_weight=0.2`（此前 Phase 7 测过 0/0.1/0.2/0.3，本轮不再扫权重，避免二次调参）。可否？
2. **判定窗口只跑一次**：W2 若结论为「不通过」，直接记录并结束，不做二次确认跑（避免「跑到通过为止」）。可否？
3. **提交后是否推送 `origin/分支6`**：默认只本地提交、不推送，等你说推再推。可否？

---

## 附录 A 复现命令

```powershell
cd "D:\DeepSeekHarness\案例区\stock plan"
$env:PYTHONIOENCODING = "utf-8"

# 全量测试
.\.venv\Scripts\python.exe -m pytest -q

# 全市场基线（21 arm）
.\.venv\Scripts\python.exe scripts\measure_factor_ab.py --baseline --max-codes 0 --jobs 5 `
  --out tests\reports\<ts>.rescreen-baseline.json --md tests\reports\<ts>.rescreen-baseline.md

# 复筛矩阵（示例：单批）
.\.venv\Scripts\python.exe scripts\measure_factor_ab.py `
  --spec .github\plans\specs\2026-10-29.rescreen-matrix.spec.json --jobs 5 `
  --out tests\reports\<ts>.rescreen-matrix.json --md tests\reports\<ts>.rescreen-matrix.md
```

## 修订记录

- 2026-10-29：创建。依据用户 13:19 指令（branch6 已提速、要求复筛旧候选）与 13:29 六问回答；T-4 暂缓、T-7 取消，改为「三档择时 + 全市场复筛矩阵」。
