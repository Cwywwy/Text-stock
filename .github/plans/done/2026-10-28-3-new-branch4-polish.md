# 计划书：分支4 收尾打磨（删冗余 + 补买卖点 + 对比页结论化）

- 日期：2026-10-28
- 分支：`分支4`（从 `分支3` 的 `b3f7663` 拉出）
- 授权：用户明确「分支4 的改动你可以全权决定，但只能在分支4 修改，不能动其他项目以及系统内文件」→ 本计划书由我代用户批准，逐条留痕，用户回来可整体回退。
- 硬约束：只在 `D:\DeepSeekHarness\案例区\stock plan` 内改动；不新增依赖；不改数据口径与回测算法。
- 基线：全量 `pytest` **309 passed**，工作区干净。

## 1. 目标与范围

图表升级（计划书 `2026-10-28-1`，已归档）之后，仍有四处「能用但不干净」的地方：

1. **同一件事两个工厂**：`widgets.equity_curve_fig()`（收益率口径）和回测页私有的 `_hero_fig()`（收益率 + 金额两个口径）逻辑重复，回撤带、基准换算各写一遍。改一处必须记得改两处，正是最容易腐化的地方。
2. **少了一个已批准但没做的功能**：Phase 1 用户批准「买卖点标注**默认关、可开关**」，代码里没有开关也没有标注。
3. **对比页只能说「谁赚了」，不能说「谁赢了基准多少」**：表格有 8 列指标，但没有超额列，也没有一句话结论。
4. **重复文案**：持仓诊断页同一句「单股回测仅反映…」打印了两遍。

范围内：以上 4 项 + 对应测试、报告、CHANGELOG。
范围外：数据层、回测算法、LLM 链路、其他 9 个页面、依赖升级、UI 框架替换。

## 2. 模块划分

| 模块 | 职责 | 状态 |
| --- | --- | --- |
| `src/stock_plan/ui/widgets.py` | 新增 `backtest_hero_fig(result, *, mode, trades, height)`：收益率/金额双口径 + 基准 + 回撤带 + 可选买卖点；删除重复的 `equity_curve_fig()` | 修改 |
| `src/stock_plan/ui/views/backtest.py` | 首屏主图改调工厂；新增「标注买卖点」开关（默认关，符合已批准默认值） | 修改 |
| `src/stock_plan/ui/views/visual_builder.py` | 复用同一个工厂，删掉对旧 `equity_curve_fig` 的依赖 | 修改 |
| `src/stock_plan/ui/views/compare.py` | 对比表加「超额%」列 + 最优值高亮 + 图上方一句结论 | 修改 |
| `src/stock_plan/ui/views/portfolio.py` | 删重复 caption | 修改 |
| `scripts/preview_charts.py` | 样张改调新工厂，买卖点开关纳入对照图 | 修改 |
| `tests/test_chart_views.py` | 新增工厂与买卖点标记断言；守护「只有一个主图工厂」 | 修改 |

## 3. 阶段与完成标准

### Phase 0 — 主图工厂统一 + 买卖点标注

- `backtest_hero_fig()` 承担两个口径；买卖点标记用 ▲/▼（红买绿卖，与红涨绿跌一致），hover 显示代码、日期、成交价；`trades=None` 时不画。
- 删除 `equity_curve_fig()`，`visual_builder` 与样张脚本改调新工厂。
- 完成标准：`grep equity_curve_fig src/ scripts/` 为空；回测页开关默认关；开图时标记数量 = 交易笔数×2；全量测试通过。

### Phase 1 — 对比页结论化

- 表格增加「超额%」= 策略总收益 − 同期等权基准总收益；用 `Styler` 高亮每列最优值（红涨绿跌语义，最优 = 收益最高 / 回撤最小）。
- 图上方一句话：`趋势跟随 v2（5 只） 跑赢基准 12.3 个百分点`（无人跑赢时说「全部跑输基准」）。
- 完成标准：无基准数据时超额列显示「—」且不崩；AppTest 冒烟通过。

### Phase 2 — 回归与留痕

- 全量 `pytest`、`tests/reports/<时间>.report-<n>.md`、CHANGELOG、计划书归档。
- 完成标准：报告落盘、提交 `(branch4 phase N) …`、工作区干净。

## 4. 测试策略

| 类型 | 内容 |
| --- | --- |
| 单元测试 | `backtest_hero_fig` 两口径的 trace/label/轴格式；买卖点数量与坐标；无 trades 时无标记 |
| 守护测试 | `equity_curve_fig` 不再存在于 `ui/`；视图层仍无硬编码颜色 |
| 结构测试 | 对比页超额列与结论句（纯函数抽出，便于测试） |
| 视觉验证 | `scripts/preview_charts.py` + Edge headless 截图，对比开关打开前后的主图 |
| 回归 | 全量 `pytest`（基线 309） |

## 5. 风险与限制

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| 删 `equity_curve_fig` 有遗漏调用点 | 页面运行时报 ImportError | 先全仓 `grep`，再用测试守护；`visual_builder` 冒烟 import |
| 买卖点标记太多糊成一团（100+ 笔交易） | 主图不可读 | 默认关；打开时只标买卖价位圆点、不加文字；caption 说明密度 |
| 超额列依赖基准序列，旧缓存可能没有 | 表格出现空列 | 缺基准时统一显示「—」，不抛异常 |
| 对比页 Styler 与深色主题冲突 | 文字看不清 | 只用背景色，不改字体色；截图确认 |

## 6. 文件清单

- 修改：`src/stock_plan/ui/widgets.py`、`views/backtest.py`、`views/visual_builder.py`、`views/compare.py`、`views/portfolio.py`、`scripts/preview_charts.py`、`tests/test_chart_views.py`、`README.md`（如有必要）
- 新增：`tests/reports/2026-10-28-18-XX.report-1.md`、`.github/plans/done/2026-10-28-3-new-branch4-polish.md`（归档时）
- 删除：`widgets.equity_curve_fig()`（约 40 行）与持仓页重复 caption

## 7. 完成情况（2026-10-28 18:12）

| 阶段 | 状态 | 实际产出 |
| --- | --- | --- |
| Phase 0 主图工厂统一 + 买卖点标注 | ✅ | `widgets.backtest_hero_fig()` 上线、`equity_curve_fig()` 删除、回测页「标注买卖点」开关（默认关）、样张新增买卖点对照图 |
| Phase 1 对比页结论化 | ✅ | 「超额%」列 + 每列最优底色 + 一句话结论（含「全部跑输」分支）；持仓页重复文案删除 |
| Phase 2 回归与留痕 | ✅ | 全量 `pytest` **323 passed**（基线 309，新增 14）；报告 `tests/reports/2026-10-28-18-12.report-1.md`；CHANGELOG 续 10；本计划书归档 |

偏差说明：
- 原计划「对比页 Styler 只染背景」保持不变，未引入任何前端依赖。
- 原计划外多做了两件小事：`chart_style.add_level_line()` 增加 `value_text` 参数（金额参考线不再显示 `9.99 万 99963.00` 这种双份数字）、`price_level_fig` 与 `benchmark_equity_fig` 一并纳入 `tests/test_chart_views.py` 守护（这两个是上一分支留下的函数，本轮补测）。
- 未做：README 未改（本轮没有新的使用方式，只有观感与结论改进）。
