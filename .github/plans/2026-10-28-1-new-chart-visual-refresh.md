# 计划书：策略与回测图表可视化升级（观感对标观潮 TideView）

- 日期：2026-10-28
- 分支：`分支3`（基线 `agents/merge-envcheck-into-branch1`，main + 19 提交）
- 状态：**待用户审阅**
- 环境：Python **3.13.15**（`.venv`）；plotly **7.0.0**、streamlit **1.63.0**、pandas **3.0.5**、numpy 2.5.2；pytest 9.1.1
- 硬约束：**不新增依赖**（INSTRUCTION.md:35、README.md:176/214）

---

## 1. 目标与范围

### 1.1 用户确认的需求（7 问访谈结论）

| 维度 | 结论 |
| --- | --- |
| 目标 | 解决「专业观感」问题：配色、字体、网格、留白像半成品 |
| 受众 | 只有自己用（不必做营销级包装，但要经得起自己天天看） |
| 参考 | 观潮 TideView（guanchaotv.com）的界面观感 |
| 输出形式 | 就在现有 Streamlit 里改观感（不换技术栈） |
| 约束 | 以 UI 层改动为主；允许小改数据层接口（补基准曲线、买卖点） |
| 成功标准 | 出改造前后截图样张，用户看截图拍板 |
| 风险关注 | ① 改坏原有功能 ② 代码变复杂、用户维护不了 |

### 1.2 范围内

- `src/stock_plan/ui/` 下所有 Plotly 图表的统一样式与工厂函数
- 回测结果页（`backtest.py`）信息结构重排
- 对比页（`compare.py`）、持仓/个股页（`portfolio.py`）、今日页（`today.py`）的图表套用同一规范
- `.streamlit/config.toml` 主题微调
- 数据层**极小**改动：`BacktestResult` 增加基准序列字段

### 1.3 范围外

- 不换框架（不引入 Dash / ECharts / 任何前端组件）
- 不改 `data/`、`factors/`、`strategy/` 的业务逻辑
- 不做 App / 小程序（仅在计划书第 7 节记录预留纪律）
- 不改页面导航结构与 URL（`app.py:118-134`）

---

## 2. 现状诊断（审计结论摘要）

完整图表清单与缺陷见本次审计（19 张图 / 6 处冗余 / 10 处写死高度）。关键问题：

| 问题 | 位置 | 影响 |
| --- | --- | --- |
| 同一份净值画两张 400px 图（资产元 + 收益率%） | `backtest.py:196-215`、`backtest.py:217-232` | 吃掉 800px，交易分析被推到 2000px 之外 |
| 无基准叠加 | `engine.py:62-66` 只返回 equity/trades/metrics | 分不清 alpha 与 beta，结论无法自证 |
| 涨跌配色与中国习惯相反（正绿负红） | `backtest.py:243-246`、`backtest.py:329` 热力图 `RdYlGn` | 一眼看反，最像「半成品」的一处 |
| 月度收益 x 轴是 `"YYYY-MM"` 字符串（分类轴） | `backtest.py:240` | 缩放时刻度不重算，正是 `widgets.py:48-57` 要修的问题 |
| 颜色 token 散落 6 处 `#1f77b4`，与主题 `#00C896` 两套体系 | `widgets.py:228,277`、`backtest.py:205`、`compare.py:186`、`portfolio.py:138,205` | 图表与界面像两个产品 |
| 策略对比颜色只覆盖 2 个内置策略，其余 `#333` | `compare.py:186,196` | 深色底上几乎不可见 |
| 无 `uirevision` | 全程 | 任一控件操作后缩放/平移复位，图跳回全览 |
| `use_container_width=True` 在 1.63 已弃用 | 全部 `st.plotly_chart` 调用点 | 每次渲染刷弃用告警 |
| 3 张大表插在 5 张图之间 | `backtest.py:257-345` | 图表被表格切断，阅读节奏断裂 |
| 导出文件名固定 `stock_plan_chart` | `widgets.py:32-36` | 多图下载互相覆盖 |

---

## 3. 设计基线（从观潮截图提炼，已存参考图）

参考图：`D:\DeepSeekHarness\.dsh-tmp\ref\screen-pre.webp`、`screen-decision.webp`。

**观潮的设计手法**：近黑画布、无背景网格、价格轴靠右、小字号紧凑行高、克制的单色数据线 + 红绿只用于涨跌、关键位用虚线 + 小圆角标签、工具栏极简分组。

### 3.1 设计 token（新增 `chart_style.py`，单一真源）

| token | 值 | 用途 |
| --- | --- | --- |
| `CANVAS` | 透明（融入 `config.toml` 的 `#0E1117`） | 图背景 |
| `PANEL` | `#12161C` | 指标卡 / 容器底 |
| `GRID` | `rgba(27,32,39,0.55)` | 仅水平网格，默认关闭 |
| `AXIS_TEXT` | `#8A9099` | 轴刻度 / 图例文字 |
| `TEXT` | `#E6E8EB` | 正文 |
| `UP` | `#F0616D` | 涨 / 正收益（A 股习惯：红涨） |
| `DOWN` | `#2EBD85` | 跌 / 负收益（绿跌） |
| `PRIMARY` | `#00C896` | 品牌 / 主曲线 / 交互高亮 |
| `BENCHMARK` | `#8A9099`（虚线） | 基准曲线 |
| `DRAWDOWN` | `rgba(240,97,109,0.16)` | 回撤阴影 |
| `WARN` | `#F5A524` | 警示标注 |

字号：轴/图例 11px、图内标注 11px、副标题 12px；数字统一 `tabular` 观感（固定小数位 + 千分位）。

### 3.2 图表八条规范（写进 `chart_style.py` 文档字符串，所有图强制走工厂）

1. 一图一结论：每张图有标题 + 一句 caption；无标题不出图
2. 无背景网格；金额/价格类 y 轴靠右；x 轴必须是真日期轴
3. 颜色只表达数据语义（涨跌 / 基准 / 回撤），界面元素低饱和
4. 数值格式：金额千分位 + 万/亿缩写，百分比 2 位小数，时间 `YYYY-MM-DD`
5. `hovermode="x unified"` + 十字光标 `spikes`
6. `uirevision` 固定 → rerun 后保持缩放
7. 高度自适应：主图 420 / 副图 240；窄屏（<1200px）降级
8. 导出文件名带「页面-图名」，不再统一下载成 `stock_plan_chart`

---

## 4. 模块划分

| 模块 | 职责 | 交互方式 | 新增/修改 |
| --- | --- | --- | --- |
| `src/stock_plan/ui/chart_style.py` | 设计 token、plotly template、轴/格式/标注入参 | 被 `widgets.py` 引用，不被 view 直接引用 | **新增** |
| `widgets.py` 图表工厂（`chart_card` / `line_chart` / `bar_chart` / `heatmap_chart`） | 统一 layout、hover、uirevision、导出名、高度 | 各 view 通过工厂出图 | 修改 |
| `widgets.py` `PLOTLY_CONFIG` | 补 `dragmode=pan`、`responsive`、按图导出名 | 全局注入 | 修改 |
| `backtest.py` 结果区 | hero 主图 + 分区 tabs + 表格归位 | 调工厂 | 修改 |
| `compare.py` / `portfolio.py` / `today.py` | 套用同一规范并修本次审计缺陷 | 调工厂 | 修改 |
| `backtest/engine.py` | `BacktestResult` 增 `benchmark` 字段（默认 None，兼容旧数据） | 由 `run_backtest` 填充 | 修改（小） |
| `scripts/preview_charts.py` | 用真实数据生成样张 HTML/PNG | 独立脚本 | **新增** |
| `tests/test_chart_style.py` | token 完整性、格式化函数边界、工厂输出属性断言 | pytest | **新增** |
| `.streamlit/config.toml` | 主题微调（背景/面板色对齐 token） | Streamlit 启动读取 | 修改 |

---

## 5. 阶段拆分与完成标准

### Phase 0 — 设计基线与图表工厂（不改变现有外观）

产出：`chart_style.py`、`widgets.py` 工厂函数、`PLOTLY_CONFIG` 升级、`tests/test_chart_style.py`。

完成标准：
- 工厂能生成与现有 6 张图等价的输出（仅样式不同）
- `pytest tests/test_chart_style.py` 全绿
- 现有 view 尚未调用工厂 → 应用行为零变化

### Phase 1 — 回测页重构 + 样张截图（核心，先给用户拍板）

产出：
- hero 主图：净值 + 等权基准 + 回撤阴影，单张 420px（合并现在的两张 400px 图），`segmented_control` 切换「金额 / 收益率」
- `st.tabs`：总览 / 月度 / 交易 / 归因，三张大表移入对应 tab
- 月度收益：真日期轴 + 柱顶数值 + 红涨绿跌
- 热力图：语义配色（红涨绿跌）+ 标注最优/最差月
- 买卖点标注（`result.trades` 已有数据）
- `engine.py` 补 `benchmark`（复用 `factors/regime.equal_weight_index`，无新数据源）
- `scripts/preview_charts.py` 出样张；Edge headless 截改造前后对照图

完成标准：
- 回测页首屏（1600×1200）能看到「核心指标 + hero 主图」，纵向长度至少压缩 40%
- 改造前后截图各 ≥1 张，存放在 `tests/reports/` 或 `preview/`
- 全量 pytest 通过
- **用户对样张点头后才进入 Phase 2**

### Phase 2 — 其他页面统一

产出：`compare.py`（修 `#333`、基准叠加、表格下移、进度反馈）、`portfolio.py`（hline 避让、y 轴余量）、`today.py`（regime 图移出 expander、降高）。

完成标准：`grep` 全项目 `ui/` 无硬编码 hex（`chart_style.py` 除外）；无 `use_container_width` 弃用告警。

### Phase 3 — 回归验证与收尾

产出：`tests/reports/<YYYY-MM-DD-HH-mm>.report-1.md`；README 增加「图表规范」一节；CHANGELOG 记录；计划书归档到 `.github/plans/done/`。

完成标准：全量测试通过、报告归档、用户确认后再谈合并回主干。

---

## 6. 测试策略

| 类型 | 内容 |
| --- | --- |
| 单元测试 | `test_chart_style.py`：token 齐全、`fmt_money`/`fmt_pct` 边界（0、负数、None、亿级、NaN） |
| 结构测试 | 工厂产出的 fig 断言 `layout.hovermode`、`uirevision`、轴 `type`、`paper_bgcolor` |
| 回归测试 | 全量 `pytest`（23 个模块），重点 `test_backtest.py`、`test_engine_defects.py`（数据层小改后必跑） |
| 视觉验证 | Edge headless 截图：1600×1200（桌面）+ 1280×800（窄屏），改造前后对照 |
| 边界场景 | 空回测结果、单笔交易、超长区间（5 年）、无基准数据 |
| 人工验收 | 用户看截图拍板（本次选择的成功标准） |

报告按项目规矩写到 `tests/reports/<YYYY-MM-DD-HH-mm>.report-<run-times>.md`，同分钟递增不覆盖。

---

## 7. 风险与限制

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| Streamlit 1.63 无自定义 CSS 文件，深色微调空间有限 | 观潮的圆角工具栏、水印等无法 1:1 复刻 | 只做 plotly layout + config.toml 能覆盖的部分，不承诺像素级还原 |
| 不新增依赖 | 无 kaleido / 无 ECharts | 静态图改用浏览器截图；图表交互继续用 plotly |
| 视觉验证依赖 headless Edge 渲染 Streamlit（JS/WebSocket） | 可能截不出完整页面 | 已实测 Edge `--headless=new --screenshot` 可用；失败则用 `preview_charts.py` 的静态 HTML 兜底 |
| 数据层小改（`benchmark` 字段） | 旧缓存/序列化不兼容 | 字段默认 `None`，读取方判空降级 |
| 颜色语义改动（绿涨改红涨） | 推翻 6 处写死颜色，用户可能不习惯 | 一次性收口到 `chart_style.py`；本计划书**列为待确认决策** |
| 用户担心「改坏功能」 | 回测/策略结果口径变化 | 只动展示层；数据层改动仅新增字段不改既有字段；每阶段跑全量测试 |
| 用户担心「代码变复杂」 | 以后自己不敢改 | 工厂函数集中一处 + 每函数 docstring 写明「改这里会影响哪些页面」 |

### 7.1 待用户确认的决策点

1. **涨跌配色**：改为 A 股习惯「红涨绿跌」（与观潮一致），还是保持现在「绿涨红跌」？
2. **是否加买卖点标注**：图上更专业但线条更密。
3. **merged 主图形态**：一张图 + 切换按钮，还是上下双 y 轴（省滚动 vs 信息全）。

### 7.2 为将来 App / 小程序预留（本次不实现）

- 业务逻辑留在 `backtest/`、`data/`、`factors/`，UI 层只展示
- 所有图表从工厂函数出，样式集中一处 → 将来换 ECharts（小程序用不了 Plotly）只改工厂
- 回测结果输出稳定 JSON（当前 `report` 已是 dict，后续加 `version` 与 schema 校验）
- 微信小程序内嵌不了 Streamlit（web-view 需企业主体 + 备案域名），届时必须做独立后端 API

---

## 8. 文件清单

**新增（4）**
- `src/stock_plan/ui/chart_style.py`
- `scripts/preview_charts.py`
- `tests/test_chart_style.py`
- `.github/plans/2026-10-28-1-new-chart-visual-refresh.md`（本文件）

**修改（8）**
- `src/stock_plan/ui/widgets.py`
- `src/stock_plan/ui/views/backtest.py`
- `src/stock_plan/ui/views/compare.py`
- `src/stock_plan/ui/views/portfolio.py`
- `src/stock_plan/ui/views/today.py`
- `src/stock_plan/backtest/engine.py`（仅 `BacktestResult` 增字段 + `run_backtest` 填充）
- `.streamlit/config.toml`
- `CHANGELOG.md` / `README.md`（Phase 3）

**删除**：无

**数据/依赖变更**：无新增依赖；回测结果结构仅新增可选字段 `benchmark`（默认 `None`）。
