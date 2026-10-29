# 换手治理（T-4）与择时样本外影子验证（T-7）计划

- **状态**：**暂停**（2026-10-29 13:29 用户决定：T-4 换手治理暂缓、后续自行推进；T-7 影子跟踪与末 6 个月 OOS 取消）。本文件的预注册条款（§2.1 的 T-4a/G1/G2/G3、§2.3 的 T-1/T-2）**保留不改**，将来续做时直接沿用；新目标见 `.github/plans/2026-10-29-7-new-strategy-rescreen-and-timing-tiers.md`
- **日期**：2026-10-29
- **分支**：`分支6`（用户指定；不合并 main、不推其它分支）
- **依据**：
  - 归档计划 `.github/plans/done/2026-10-29-5-new-backtest-performance.md` §9.5 的 T-4 / T-7 预注册验收条款
  - T0 择时体检报告 `tests/reports/2026-10-29-07-23.timing-health.md`（14 arm，同数据）
  - Phase 14–17 性能改造与终验报告 `tests/reports/2026-10-29-10-40.report-1.md`（测试基线 637 passed）
- **环境**：Python 3.13.15 + pandas 3.0.5 + Streamlit 1.63.0，解释器 `.venv\Scripts\python.exe`；机器 16 逻辑核 / 31.1 GB；数据快照见附录 A
- **口径提醒**：本计划不重跑 Phase 6/13 历史数字（其数据快照已被 2026-10-29 早晨的每日任务刷新，不可复现）；所有对比以「同一固定数据快照上的同口径基线」为准

---

## 0 目标与范围

### 0.1 目标

1. **T-4 换手治理**：把 7 个内置策略的年单边换手从当前 13.08–21.52 次（daily 口径，win_a 抽样 800 只）压到 **≤12 次**，且不显著牺牲收益与回撤（联合验收口径见 §2.1，先预注册、后跑数）。
2. **T-7 择时样本外验证**：
   - 立即完成「末 6 个月隔离段」验证（2026-04-29 ~ 2026-10-28），用与 T0 相同的 T-1 / T-2 阈值判定；
   - 建立 **2026-11 起 ≥3 个月影子跟踪** 的脚本、spec、报告模板与首月记录；最终 T-7 判读在数据齐后（约 2027-02）由后续会话完成（本计划只交付机制 + 首月）。
3. 产出可复核报告，并给出「是否可设为默认、是否值得继续做择时层」的建议；**任何用户可见默认值变更必须先获用户批准**（§8-2）。

### 0.2 不做（边界）

- **不新建择时层**（目标仓位、清仓、趋势+广度+波动多因子择时等）——本计划只验证现有「弱择时」开关；若 OOS 判读为「帮忙」，另立计划书。
- **不改策略打分公式与因子口径**；换手治理只允许在「持有/替换/再平衡」层加参数（默认关闭，关闭时逐值等价旧行为）。
- 不引入新依赖；不做跨进程并行（沿用 `scripts/measure_factor_ab.py` 的 arm 级并发）；不做大规模参数寻优。
- 不在验证窗口上迭代调参（预注册方案矩阵 C0–C5，验证窗口只跑一次，见 §2.2）。
- 不合并 main、不 push 未经用户批准的分支；不碰其它工作区/会话的未提交改动。

---

## 1 背景与现状（计划准备阶段已核实的数据）

### 1.1 T-4 现状（daily 口径，win_a 2022-03-07~2026-10-28，max_codes=800）

| 策略 | 未开择时（单边年换手） | 择时（单边年换手） |
| --- | ---: | ---: |
| 趋势跟随策略 | 16.35 | 14.99 |
| 动量策略 | 15.92 | 14.56 |
| 均值回归策略 | 21.52 | 19.32 |
| 突破策略 | 18.32 | 16.49 |
| 价值投资策略 | 14.12 | 12.48 |
| 段永平-高质量策略 | 13.08 | **11.35** |
| 李录-长期确定性策略 | 14.31 | 12.51 |

> 结论：T-4 仅「段永平/择时」一条 arm 通过；未开择时基线本身也全部 >12。换手成本是策略端的主要失血点之一。

**换手成本估算**（成本参数：佣金 0.03%/边、印花税 0.1%（卖）、滑点 0.1%/边，`min_commission=5` 未计入）：

- 每次「买入→卖出」往返成本 ≈ 买侧 0.13% + 卖侧 0.23% = **0.36%**；
- 年成本拖累 ≈ 单边年换手 × 0.36pp：16 次 ≈ **5.8pp/年**，压到 12 次 ≈ **4.3pp/年**（理论可回收 ~1.5pp/年）。

### 1.2 计划准备阶段的探索（非正式验收，仅用于确认方案可行性）

2026-10-29 13:07 用 `scripts/measure_factor_ab.py` 跑 14 arm（7 策略 × 择时开/关，**weekly** 再平衡，win_a，max_codes=800，结果在 `_scratch/plan6/weekly.json`，未入库）：

| 策略 | weekly/未开择时 | weekly/择时 |
| --- | ---: | ---: |
| 趋势跟随策略 | 13.94 | 12.19 |
| 动量策略 | 13.60 | 11.89 |
| 均值回归策略 | 15.57 | 13.30 |
| 突破策略 | 14.67 | 12.80 |
| 价值投资策略 | 12.35 | 10.60 |
| 段永平-高质量策略 | 11.47 | 9.60 |
| 李录-长期确定性策略 | 12.59 | 10.68 |

**关键判断**：

1. `daily → weekly` 单杠杆可把换手降 18%–27%，**零代码改动**（`BacktestConfig.rebalance_freq` 已支持），且 UI 三个页面（回测/对比/可视化）的 selectbox **默认本来就是 weekly**，只有引擎默认值与历史基线是 daily——T-4 的「现状」依赖口径，报告必须同时给 daily/weekly；
2. weekly 后择时口径 5/7 达标，但 趋势跟随 12.19、均值回归 13.30、突破 12.80 仍 >12；未开择时口径仅 1/7 达标 → **需要再加 1–2 个「持有/替换层」杠杆**，这与 Phase 19 的设计一致；
3. 但 weekly 会改变收益数值（上表总收益与 T0 daily 不同），因此必须用 §2.1 的联合验收（换手 + 收益/回撤护栏）判定，不能只看换手。

### 1.3 测试与数据基线

- 当前 `分支6` 已包含 Phase 14–17 全部改动；全量 pytest 基线 **637 passed**（Phase 17 终验）。
- 数据快照（2026-10-29 08:20 记录，附录 A）：`bars` 5579 个文件、listing sha256 `24fa553b…`、`meta.db` sha256 `eea568d1…`、200 只抽样内容 sha256 `7b5b4d8e…`。
- 项目每日任务在 06:49 / 08:00–08:02 会刷新股票池、估值与部分 K 线；**每个 Phase 开始与结束都要重新记录指纹**，不一致时按「同数据重跑基线」处理（沿用 Phase 14 的处理方式）。

---

## 2 关键口径与预注册验收（跑数前固定，T-6：事后不得改阈值）

### 2.1 T-4 联合验收（在验证窗口上判定；同一策略、同一 timing 口径对比）

| 代号 | 条款 | 阈值 |
| --- | --- | --- |
| T-4a | 年单边换手（`calc_turnover` 口径：`single_side ÷ 区间日历天数/365`） | **≤ 12** |
| G1 | 年化收益相对「现行同口径基线」下降 | **≤ 2pp** |
| G2 | 最大回撤相对「现行同口径基线」恶化 | **≤ 2pp** |
| G3 | 7 个策略中同时满足 T-4a + G1 的策略数 | **≥ 5/7** |

- 附加信息（不参与判定）：交易笔数、胜率、基准收益、分年换手、退出原因分布。
- 若同一策略下 timing on/off 两个口径都需达标：以 `market_timing=True`（用户可见默认）为主判定，`False` 作为须同时报告的对照（若两者结论冲突，报告必须并列说明，不以一个掩盖另一个）。

### 2.2 训练 / 验证窗口（防过拟合，先固定）

| 用途 | 区间 | 说明 |
| --- | --- | --- |
| 训练（选方案） | 2022-03-07 ~ 2025-06-30 | 只能在训练窗口比较 C0–C5 并选定候选 |
| 验证（判定） | 2025-07-01 ~ 2026-10-28 | 只跑一次；不达标 → 记录并停下问用户，不迭代 |
| 全期（信息性） | 2022-03-07 ~ 2026-10-28 | 与 T0 对齐，仅展示 |

### 2.3 T-7 OOS 判读（末 6 个月隔离段：2026-04-29 ~ 2026-10-28）

- 只用 **T-1 / T-2**（阈值不变）：T-1 = 同策略最大回撤改善 ≥3pp；T-2 = 年化收益下降 ≤2pp。
- 判读映射（跑数前固定）：**帮忙** = T-1 且 T-2 通过；**帮倒忙** = T-1 改善 <0 或 T-2 恶化 >2pp；其余 = **无差别**。
- 必附：**T-5 清单**（该段各月基准涨跌 + 各策略「错过/多得」及机会成本）+ T-4 换手；T-3 不适用（不足 3 个完整年），报告中注明不判定。
- 2026-11 起的影子跟踪沿用同一 T-1/T-2 阈值，按累计月份汇总判定，不得改阈值。

### 2.4 数据与报告口径（每份报告必须写明）

数据快照指纹、样本口径（`max_codes=800` 抽样 vs 全市场）、再平衡口径（daily/weekly）、`market_timing`（on/off）、区间、成本参数（佣金/印花/滑点/min_commission）、是否含并行负载。

---

## 3 模块划分

| 模块 | 职责 | 交互 | 状态 |
| --- | --- | --- | --- |
| `scripts/turnover_attribution.py`（新） | 读 `trades`/`equity`，计算单边/双边/分年/分月换手、退出原因分布、持仓时长、替换次数、成本拖累估算 | 输入 `BacktestResult`（复用 `run_backtest`）；输出 Markdown+JSON | 新增 |
| 引擎换手杠杆（`engine.py` 扩展） | 新增 `min_hold_days`（仅约束替换卖出）与 `replace_score_gap`（候选需超过持仓分数 ≥gap 才替换），**默认 0/0.0 = 旧行为** | `BacktestConfig` 字段；`run_backtest` 主循环；不影响 `prepare_backtest` 因子表 | 修改（可选，视 Phase 18 归因） |
| A/B 矩阵评估（spec + 复用 harness） | 预注册 C0–C5 × 窗口 × timing 口径；训练选方案、验证判定 | `scripts/measure_factor_ab.py --spec`；对比工具 | 新增 spec |
| 择时 OOS 验证（spec + 报告） | 末 6 个月隔离段 14 arm + 月度明细 | 复用 harness；月度探针脚本沿用 T0 模式 | 新增 spec |
| 影子验证工作流（`scripts/run_timing_shadow.py`，新） | 月度：数据覆盖检查 → 生成 spec → 调用 harness → 追加历史 JSON 与月度报告 | 只读数据；写 `tests/reports/` | 新增 |
| 文档层 | 计划勾选、README/CHANGELOG（仅默认值变更时）、报告索引 | Markdown | 修改/新增 |

---

## 4 阶段拆分（Phase 18–23，延续全局 Phase 编号）

### Phase 18 基线与换手归因（无行为改动）

**步骤**

1. 冻结基线：复用 `tests/reports/2026-10-29-08-52.phase14-head-baseline.json`（daily 21 arm，同数据 HEAD 基线）；新增 **weekly 14 arm 基线**（7 策略 × timing on/off，win_a，max_codes=800，结果 JSON 入 `tests/reports/`）。
2. 新增 `scripts/turnover_attribution.py`：输出每策略的 单边/双边/分年/分月换手、买入/卖出金额、退出原因分布（止盈/止损/超时/回测结束/退市停牌）、平均持仓天数、同 slot 连续换名次数、成本拖累估算。
3. 跑 7 策略 × {daily, weekly} × timing {on, off}（win_a，max_codes=800）归因。
4. 报告 `tests/reports/<ts>.turnover-attribution.md`（含“daily vs weekly”对照与退出原因表）。

**预期产出**：归因脚本 + 24 arm 基线 JSON + 归因报告

**完成标准**：报告含 7 策略 × 4 口径换手表、退出原因分布、成本拖累估算；基线 JSON 重跑 0 差异；pytest 637 不降。

**commit**：`(phase 18) add turnover attribution baseline and analysis`

### Phase 19 换手治理杠杆实装（默认关闭）

**步骤**（依据 Phase 18 归因；若归因显示 weekly 已足够，则只做②③的最小集，报告注明省略理由）

1. `BacktestConfig.min_hold_days: int = 0`：仅约束「替换卖出」（持仓未满 N 个自然日时不用新候选替换），止盈/止损/超时/强制平仓不受限；0 = 旧行为。
2. `BacktestConfig.replace_score_gap: float = 0.0`：仅当候选 `score` 超过「持仓当前 score + gap」才替换；需每日为持仓重算 score（复用当日候选表；持仓未进候选表的用该策略当日候选表外计算或豁免，规则须在实现前写入测试说明）；0 = 旧行为。
3. 若归因显示再平衡频率是主因：不改代码，仅把 weekly 作为方案矩阵 C1。
4. 新增 `tests/test_engine_turnover_levers.py`：默认值等价（21 arm 0 差异）、min_hold 不拦止盈止损、gap 边界、与 weekly/daily 组合、T+1 约束、无候选日不误替换。

**预期产出**：引擎新字段 + 单测 + 默认关闭证明

**完成标准**：默认参数下 21 arm 与 head 基线 **0 差异**；新单测全绿；pytest ≥637。

**commit**：`(phase 19) add min hold and replacement gap backtest levers`

### Phase 20 A/B 矩阵与 T-4 判定（预注册）

**步骤**

1. 生成 `.github/plans/specs/2026-10-29.turnover-matrix.spec.json`，方案矩阵（训练窗口 + 验证窗口各跑；timing on/off 两组）：

   | 代号 | 定义 |
   | --- | --- |
   | C0 | 现行基线（daily，无杠杆） |
   | C1 | weekly（零代码） |
   | C2 | weekly + min_hold=5 |
   | C3 | weekly + replace_score_gap=2.0 |
   | C4 | weekly + min_hold=5 + replace_score_gap=2.0 |
   | C5 | daily + min_hold=5 + replace_score_gap=2.0（隔离变量） |

2. 训练窗口比较并**只选一个候选**；验证窗口跑一次，按 §2.1 判定（T-4a + G1/G2/G3）。
3. 报告 `tests/reports/<ts>.turnover-ab.md`：矩阵表、训练/验证结果、判定、成本拖累变化、逐策略结论；若通过 → 给出推荐默认组合；若未通过 → 不改默认，记录原因并**停下问用户**。

**完成标准**：报告明确「通过/未通过」及逐策略理由；验证窗口只跑一次（报告记录跑数次数）；pytest ≥637。

**commit**：`(phase 20) evaluate turnover reduction levers on train and holdout windows`

### Phase 21 默认值与用户可见口径（条件执行）

**步骤**（仅当 Phase 20 通过 **且** §8-2 获用户批准；否则只记录结论并结束本阶段）

1. 按结论调整默认值（引擎默认 `rebalance_freq`、新杠杆默认值、必要时 UI 默认；UI selectbox 目前默认已是 weekly）。
2. 同步 README（回测页说明：换手/成本）、CHANGELOG（简体中文、按日期追加）。
3. 在报告中登记「用户批准记录」。

**完成标准**：行为变更仅发生在批准后；UI 文案与默认值一致；pytest 全绿；报告记录批准与回滚方式。

**commit**：`(phase 21) apply approved turnover defaults and documentation`

### Phase 22 T-7 末 6 个月隔离验证

**步骤**

1. 生成 `.github/plans/specs/2026-10-29.timing-oos.spec.json`：7 策略 × timing {on, off}，`start=2026-04-29`、`end=2026-10-28`、`lead_days=400`、`max_codes=800`、再平衡口径与 T0 一致（daily；另附 weekly 对照）。
2. 跑 14+14 arm；计算 OOS 的 T-1/T-2 判定表、T-5 清单（各月基准涨跌 + 各策略错过/多得）、T-4 换手；月度明细用 T0 同款探针方式（脚本副本放 `_scratch` 或入库脚本均可，须记录复现命令）。
3. 报告 `tests/reports/<ts>.timing-oos.md`：14 arm 总表 + 逐策略判定 + 与 T0 全期结论并列 + 数据指纹 + 「样本仅 6 个月」的置信度说明。

**完成标准**：报告含 14 arm 总表、逐策略 T-1/T-2 判定、T-5 清单、数据指纹；结论只允许 帮忙/帮倒忙/无差别；pytest ≥637。

**commit**：`(phase 22) validate market timing on six month holdout window`

### Phase 23 T-7 影子机制、首月记录与收尾

**步骤**

1. 新增 `scripts/run_timing_shadow.py`：检查数据是否覆盖上月 → 生成月度 spec（7 策略 × 2 口径，示例窗口 3 个月滚动）→ 调用 harness → 追加 `tests/reports/timing-shadow-history.json` 与 `tests/reports/<YYYY-MM>.timing-shadow.md`。
2. 跑 2026-11 首月记录（若 2026-11 数据尚未更新则记录「数据未到」并给出下次执行日期，不算失败）。
3. 报告 `tests/reports/<ts>.timing-shadow-setup.md`：机制说明、首月记录、T-7 剩余月份、最终判读口径（T-1/T-2 累计）。
4. 计划勾选完成项；按 AGENTS 规则归档到 `.github/plans/done/`（T-7 最终判读作为**后续跨月事项**在计划 §9 中保留说明，不阻塞本计划归档）。

**完成标准**：脚本可重复运行且幂等（同月不重复追加）；首月记录落盘；报告含剩余月份与判读口径；pytest ≥637；工作区干净。

**commit**：`(phase 23) add timing shadow validation workflow and first month record`

---

## 5 测试策略

| 层级 | 内容 |
| --- | --- |
| 单元 | 新增杠杆默认值等价旧行为（21 arm 0 差异）；min_hold 不拦截止盈/止损/超时；gap 边界（0/负数/超大）；无候选日不误替换；T+1；weekly/daily 组合 |
| 对拍 | `turnover_attribution.py` 的换手与 `calc_turnover` 一致；默认参数下 21 arm 与 head 基线逐字段 0 差异 |
| 契约 | `tests/test_factor_parity.py`、`tests/test_engine_perf_parity.py`、`tests/test_backtest*.py`、`tests/test_storage_market_maps.py` 保持全绿 |
| 回归 | 每个 Phase 全量 pytest（基线 637），报告 `tests/reports/<YYYY-MM-DD-HH-mm>.report-n.md`（同一分钟递增不覆盖） |
| 数据缩减 | 阶段验收允许 max_codes=800 抽样；若最终结论需要全市场复核，须在报告中写明缩减比例与原因 |
| 报告规范 | 每份报告含：时间、范围、通过/失败数量、失败原因、数据快照指纹、数据缩减说明、本地验证链接 |

---

## 6 风险与限制

| 编号 | 风险 | 应对 |
| --- | --- | --- |
| R1 | 换手治理**必然改变收益数值**，不能与 Phase 13/17 历史数字对拍 | 只与「同口径新基线」比较；杠杆默认关闭保证旧基线仍可复现；报告显式声明口径 |
| R2 | 过拟合 | 训练/验证分离（§2.2）+ 方案矩阵预注册 C0–C5 + 验证窗口只跑一次 |
| R3 | 样本不足 | 验证窗口仅 16 个月、OOS 仅 6 个月；结论须标注置信度；T-7 最终需 3 个月影子 |
| R4 | 数据快照刷新（每日任务 06:49/08:00） | 每 Phase 记录指纹；基线不可复现时按同数据重跑，不混用旧数字 |
| R5 | 现择时层较弱（只对新买入减半、不减仓不清仓） | 影子验证只能回答「现有开关是否值得保留」，不能证明系统性风险改善；不得据此宣称择时层有效 |
| R6 | 默认值改动影响 UI/云端（`分支1` 为云端 Streamlit 绑定分支） | 须用户批准（§8-2）、同步 README/CHANGELOG、保留回滚方式；不自动合并/推送 |
| R7 | 与其它会话并行重跑重型全市场 | 计时/基线跑批避开并发；报告注明负载与并发数 |
| R8 | T-4 无法在护栏内达标 | 不硬压换手：记录「现状不可达标」+ 替代建议（如换手成本显式建模、费率优化），交用户决策 |

---

## 7 预计文件清单

**新增**

- `.github/plans/2026-10-29-6-turnover-reduction-and-timing-oos-validation.md`（本计划）
- `.github/plans/specs/2026-10-29.turnover-matrix.spec.json`
- `.github/plans/specs/2026-10-29.timing-oos.spec.json`
- `scripts/turnover_attribution.py`
- `scripts/run_timing_shadow.py`
- `tests/test_engine_turnover_levers.py`
- `tests/reports/<ts>.turnover-attribution.md`、`<ts>.turnover-ab.md`、`<ts>.timing-oos.md`、`<ts>.timing-shadow-setup.md`、各 Phase `<ts>.report-n.md`
- `tests/reports/<ts>.turnover-baseline.json`、`<ts>.timing-oos.json`、`timing-shadow-history.json`

**修改**

- `src/stock_plan/backtest/engine.py`（Phase 19 两个新字段与替换逻辑；默认不改行为）
- `src/stock_plan/ui/views/{backtest,compare,visual_builder}.py`（仅 Phase 21 若改默认或暴露杠杆）
- `README.md`、`CHANGELOG.md`（仅 Phase 21/23；简体中文）
- `.github/plans/done/`（归档时移动本计划）

**删除**：无

---

## 8 待确认（执行会话开始前逐项与用户确认，建议默认值已给出）

1. **T-4 联合验收**是否按 §2.1 预注册（T-4a ≤12；G1 年化下降 ≤2pp；G2 回撤恶化 ≤2pp；G3 ≥5/7）？【建议：是】
2. 若 Phase 20 通过，是否允许修改**用户可见默认值**（引擎默认 daily→weekly、新杠杆默认值、UI 默认保持 weekly）？【建议：仅当通过且收益护栏满足时改，改前再次确认】
3. T-7 范围是否确认为「仅验证现有弱择时开关」，不新增目标仓位/清仓逻辑？【建议：是】
4. T-7 影子跟踪：每月**手动**跑一次并追加报告，还是接入每日任务自动运行？【建议：先手动，稳定后再评估自动】
5. 末 6 个月隔离窗口是否用 **2026-04-29 ~ 2026-10-28**？【建议：是】
6. 阶段验收是否允许 `max_codes=800` 抽样（最终是否需全市场复核）？【建议：阶段抽样；若 T-4/T-7 结论为「通过/帮忙」则对关键 arm 做一次全市场复核】
7. Phase 18 的 weekly 基线是否可参考本计划 §1.2 的探索结果（`_scratch/plan6/weekly.json`），但正式报告仍须重新跑并记录指纹？【建议：是，探索结果只作方向参考】

---

## 9 后续（跨月事项）

1. **T-7 最终判读**：2026-11 ~ 2027-01 三个月影子数据齐后（约 2027-02 初）由新会话按 §2.3 汇总判定；本计划归档时保留本说明。
2. 若 T-7 判读为「帮忙」且用户决定上择时层 → 另立计划书（目标仓位/清仓/换手约束）。
3. 若 T-4 通过并改默认 → `分支1`（云端 Streamlit）同步、部署与回滚方案另行确认。
4. 任何默认值变更都需要同步 README/CHANGELOG 并在 CHANGELOG 记录批次。

---

## 附录 A 数据快照与复现命令

**数据指纹（2026-10-29 08:20 记录，Phase 14/15/17 共用）**

| 项 | 值 |
| --- | --- |
| bars 文件数 | 5579 |
| bars listing sha256 | `24fa553ba5020d1d6d6f9389424ca936431581342ddf572d786b0567121c0b37` |
| meta.db sha256 | `eea568d1555b9dff6f731f003be6fef8040ec46154789fa5361e94a006ff0d85` |
| 200 只抽样内容 sha256 | `7b5b4d8e…`（完整值见 `tests/reports/2026-10-29-08-52.report-1.md` §3） |
| stock_list 行数 | 5575 |

**复现命令（PowerShell）**

```powershell
cd "D:\DeepSeekHarness\案例区\stock plan"
$env:PYTHONIOENCODING = "utf-8"
# 全量测试
.\.venv\Scripts\python.exe -m pytest -q
# 单 spec 跑批（示例：T-7 OOS）
.\.venv\Scripts\python.exe scripts\measure_factor_ab.py --spec .github\plans\specs\2026-10-29.timing-oos.spec.json --jobs 5 --out tests\reports\<ts>.timing-oos.json --md tests\reports\<ts>.timing-oos.md
# T0 体检复现（官方 spec，未改动 harness）
.\.venv\Scripts\python.exe scripts\measure_factor_ab.py --spec .github\plans\specs\2026-10-29.timing-health.spec.json --jobs 5 --out tests\reports\<ts>.timing-health.json --md tests\reports\<ts>.timing-health.md
```

**环境**：`.venv/Scripts/python.exe`（Python 3.13.15 + pandas 3.0.5）；Windows PowerShell 5.1 下避免 `python -c` 内嵌花括号，一律写临时 `.py` 文件执行；读中文文件用 `read` 工具（Get-Content 不加 `-Encoding` 会乱码）。

---

## 修订记录

- 2026-10-29：创建（依据 T0 体检结论 T-4 仅 1/7 通过、T-7 未做；Phase 14–17 已合并进 `分支6`/`main`/`分支1`）。
