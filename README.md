# 盘前选股系统

基于 A 股技术面 + 基本面选股，提供盘前信号（目标买入/卖出价、止损、持仓周期）、回测引擎与模拟交易，通过本地 Streamlit Web 仪表板查看。

> 详细设计见 [plan.md](./plan.md)。

## 环境要求

- Windows
- Python 3.13（由 uv 自动管理）

## 安装

```powershell
# 1. 安装 uv（若未安装）
irm https://astral.sh/uv/install.ps1 | iex

# 2. 进入项目目录并同步依赖（路径按你本机实际位置替换）
cd "D:\DeepSeekHarness\案例区\stock plan"
uv sync
```

## 启动

```powershell
uv run streamlit run src/stock_plan/ui/app.py
```

浏览器打开 http://localhost:8501 即可使用。

## 项目自检

```bash
python scripts/acceptance.py --full
```

以后任何改动后跑这一条，就能复验全部 37 项结论（环境/测试/12 页/数据完整性/快照往返/策略库/定时任务/敏感文件/应用健康），报告落在 `data/logs/acceptance.txt`。

```bash
python scripts/acceptance.py             # 32 项（本机实测约 18 秒）
python scripts/acceptance.py --full      # 37 项，额外做快照打包往返校验（约 22 秒）
python scripts/acceptance.py --no-pytest # 27 项，跳过测试套件（约 12 秒）
```

- **耗时为本机实测**（行情缓存 5890 只、每次冷启动一个 Python 进程），换机器或数据量变化会不同
- **退出码**：`0` = 全部通过，非 `0` = 有检查未通过（可用于 CI 或提交前钩子）
- **应用没运行不算失败**：应用健康检查是可选在线项，不可达时记为**跳过**、不影响退出码
  （此时显示 `31/31 通过；1 项跳过`）。可用 `--app-url` 指向别处，例如云端地址
- **为什么会有这个脚本**：本项目历史上出现过多个"跑通但结果不可信"的问题 —— 例如已退市股票
  占住候选名额（仅此一条就让同期回测总收益从 −5.23% 变成 −9.88%）、大盘代理对原始股价取平均、
  持仓在行情终结时被静默丢弃。这些结论当初是靠一次性脚本验证的，本脚本把它们固化下来，
  以后每次改动跑一遍就能确认没有被推翻
- **已知局限**：`AppTest` 看不出"表格内部 Arrow 转换失败"这类问题（异常被 Streamlit 吞掉，
  且元素里的列已被强制转换）。这类问题由 `tests/test_strategy_mgr_table.py` 直接断言
  行构造函数的 dtype 来拦

## 项目结构

```
stock plan/
├── pyproject.toml          # uv 项目配置与依赖
├── plan.md                 # 项目计划书（含 Phase Tasklist）
├── data/
│   ├── raw/                # 原始数据（AKShare 落盘）
│   ├── processed/          # 清洗后数据（Parquet）
│   └── db/                 # SQLite（策略/回测/模拟交易记录）
├── src/stock_plan/
│   ├── data/               # 数据获取与缓存
│   ├── factors/            # 因子计算
│   ├── strategy/           # 策略引擎
│   ├── backtest/           # 回测引擎
│   ├── signal/             # 盘前信号生成
│   ├── simulator/          # 模拟交易
│   ├── llm/                # LLM Agent（V3 已接入，配置见项目根 .env）
│   └── ui/
│       └── views/          # Streamlit 页面（12 页平铺导航）
├── scripts/run_update.cmd  # 数据定时更新包装脚本（Windows 计划任务用）
├── .streamlit/config.toml  # 深色主题配置
├── .env / .env.example     # LLM 等敏感配置（不入源码）
└── tests/                  # 测试用例
```

## 图表规范

所有图表共用一套观感（近黑画布、无背景网格、价格轴靠右、红涨绿跌、小字号紧凑行高），
目标是「像专业看盘软件」而不是「像默认的 Plotly 示例」。

- **样式单一真源**：`src/stock_plan/ui/chart_style.py` 只有 token 与样式函数（颜色、字号、
  页边距、轴格式、hover、`uirevision`），不 import streamlit，可单独做单元测试。
- **通用图只从工厂出**：`src/stock_plan/ui/widgets.py` 提供 `multi_line_fig()`、
  `equity_curve_fig()`、`benchmark_equity_fig()`、`price_level_fig()`、`score_waterfall_fig()`；
  页面里不要直接 `st.plotly_chart()`，统一走 `render_chart(fig, name=..., caption=...)`，
  导出文件名与缓存键都跟着图名走。
- **页面专用图可以自己组合**（如回测页的主图 / 月度柱 / 热力图），但颜色、字号、坐标轴
  必须全部取自 `chart_style`，视图里不许出现硬编码颜色。
  这三条由 `tests/test_chart_views.py` 守护，跑测试就能拦住回退。
- **改观感先看样张**：`scripts/preview_charts.py`（回测页）、`scripts/preview_report_section.py`
  （信号解释区块）、`scripts/preview_compare_portfolio.py`（策略对比 / 持仓诊断）用合成数据
  生成静态 HTML，不必启动 Streamlit 或等待全 A 股数据（40 秒以上）就能出图对比。

## 数据自动更新

数据需在**股票交易日**收盘后刷新。调度由 `scripts/daily_job.ps1` 承担（幂等，重复执行不会重复写入），
Windows 计划任务只负责按时间唤起它：

| 计划任务 | 时间 | 说明 |
|----------|------|------|
| `\StockPlan\Daily_Publish` | 交易日 16:30 | 增量更新 + 发布数据快照到 GitHub |
| `\StockPlan\Daily_Update` | 交易日 08:00 | 盘前兜底，只更新不发布（避免一天两次上传 300MB） |
| `\StockPlan\Monthly_Rebuild` | 每月 1 日 17:00 | 全历史重建，修正前复权因子的回溯偏差 |

**注册任务（换机器或重装系统后执行一次）**：

```powershell
# 用【管理员】身份打开 PowerShell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
```

本机 `schtasks` 需要管理员权限（非提权会报 `Access is denied`）。不方便提权时加 `-ExportOnly`
只生成 XML，再用「任务计划程序 → 操作 → 导入任务」逐个导入，效果等价：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_tasks.ps1 -ExportOnly
```

任务 XML 里已固定这些修正项（旧任务"莫名其妙不成功"的根因）：`WakeToRun`（睡眠中唤醒）、
`StartWhenAvailable`（错过的补跑）、`DisallowStartIfOnBatteries=false`（用电池也跑）、
`RunOnlyIfNetworkAvailable`、`ExecutionTimeLimit=PT3H`（卡死会被结束），并且动作直接调用
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File`——不再用 `cmd /c ""...""`
包装含空格与中文的路径。

- 增量只拉最近 30 天并按日期去重合并，已是最新数据的股票自动跳过，不联网（实测全市场约 23 秒）。
- 手动更新入口：左侧导航 **「🔄 数据更新」** 页——显示缓存状态指标，可点击「立即增量更新」实时查看进度；若存在未缓存的股票会提示并可单独补拉。
- 页面按访问者分两套：**本机（维护者）**看到增量更新为主操作；**云端（普通用户）**第一屏只有「同步到最新快照」，
  增量更新收进折叠的「🔧 维护者功能（普通用户无需操作）」。判定见 `maintainer_mode()`，
  可用环境变量 `STOCK_PLAN_MAINTAINER=1/0` 强制指定。
- 更新日志：`data/logs/update.log`、`data/logs/daily_job.log`（数据更新页内可直接查看最近记录）。

### 全历史重建（`--rebuild`）

**前复权价是回溯性的**：每次分红/送转后，数据源返回的整段历史价格都会按新因子改写。
而"只补最近 30 天再合并"只会覆盖最近那段，更早的行仍停留在旧因子上，导致历史序列在
除权日出现人为跳空，均线/ATR/涨跌幅/回测全线失真（实测间隔约 6 周：`000001` 偏移
2.19%、`601318` 偏移 1.74%）。

```powershell
uv run python -m stock_plan.data.update_daily --rebuild   # 约 10 分钟，5553 只
```

由于数据源每次请求都返回整份历史文件，"重拉 5 年"与"补 30 天"的网络成本几乎相同，
因此这个操作很划算。`\StockPlan\Monthly_Rebuild` 已把它固定为每月一次。

### 退市股数据（消除幸存者偏差）

股票列表来自新浪实时行情，只含**当前在市**的股票。历史上退市、暂停上市、被吸收合并的公司
从未进入样本，**回测样本因此不完整**。

**偏差有多大？先看"有多少退市股"是不够的，要区分两件事：**

| 问题 | 实测答案 |
|---|---|
| 有多少退市股？ | 元数据累计 **339 只**；其中退市日落在最近 5 年窗口内的约 **190 只** |
| 偏差有多大？ | **机制很严重，但对某个策略总收益的影响是"策略相关"的**（见下） |

**机制（与选股路径无关，可直接引用）**：退市股在**退市前最后 60 个交易日**的
20 日前瞻收益均值 **−25.19%**、胜率仅 **16.0%**；其余样本为 +1.11%、48.1%。
也就是说任何在"退市前那段"持有它们的策略都会吃到巨大亏损。

**但不要用单次回测的总收益去量化它**。实测这个只持 5 只的策略，总收益的跑动标准差高达
**15pp**；候选池只增 3.75% 就能让总收益差 ±21pp，成交股票被换掉 77%。同理，"含退市股 vs
不含"的单次差异（可能是 −0.02pp，也可能是 +21pp）**不携带偏差大小的信息**。
正确的做法是用**配对多组子样本 + 标准误**，或上面那种**不依赖选股路径**的前瞻收益指标。
完整测量见 [.github/plans/2026-10-15-4-audit-path-dependence-and-lookahead.md](.github/plans/2026-10-15-4-audit-path-dependence-and-lookahead.md)。

**怎么用**：回测页有「**包含已退市股票（消除幸存者偏差）**」开关，对应
`load_market_maps(include_delisted=True)`，**默认关闭** —— 打开后原先的系统性样本缺失被补上，
历史回测结果与调参结论都会变化，因此不做静默生效。

退市股行情只能走 **baostock**（新浪源对退市代码已不再返回数据，全部 `JSONDecodeError`）。

```powershell
# 补齐时点股票池（按月采样，最近 5 年约 61 次请求，首次约 10 分钟）
uv run python -m stock_plan.data.delisted --universe

# 补齐退市股完整历史行情 + 元数据（一次性；落地后不再依赖 baostock 在线）
uv run python -m stock_plan.data.delisted --delisted --limit 10   # 先试跑 10 只
uv run python -m stock_plan.data.delisted --all                   # 两者都补
```

落盘位置：时点池 `data/processed/universe/{交易日}.parquet`（只存代码，约 60KB/个）；
退市股行情复用 `data/processed/bars/{code}.parquet`；元数据在 SQLite `delisted_stocks` 表。

时间点池数量随年份单调增长（2021-10-31 **4525** 只 → 2026-10-15 **5224** 只），
即用今天的股票池回测 2021 年，样本量会高估约 15%。

设计说明：**baostock 是惰性导入**，应用与云端不安装它也能正常运行，因此
`requirements.txt` 无需新增依赖。完整调研数据与实现方案见
[.github/plans/2026-10-15-1-new-delisted-stock-history-db.md](.github/plans/2026-10-15-1-new-delisted-stock-history-db.md)。

### 股本与财报披露日（B 档字段，2026-10-29 起）

日线表只有 `amount` 与 `turnover`，此前只能算 `流通市值 = 成交额 ÷ 换手率`，**没有总股本**（小市值类策略做不了），
也**没有财报公告日**（`fundamentals.updated_at` 是抓取时刻，不是披露日）。这两项在 Phase 12 补齐，**日线表结构未动**：

| 字段 | 来源与口径 | 落点 |
| --- | --- | --- |
| 总股本 / 流通股本 / 总市值 / 流通市值 | 东财快照：`总股本 = 总市值 ÷ 最新价`、`流通股本 = 流通市值 ÷ 最新价` | `data/processed/shares/YYYY-MM.parquet`（当月快照） |
| 逐日流通股本 / 流通市值 | `float_shares = amount ÷ (turnover × close)`（由代理公式精确反推，无近似） | `data/processed/shares/history_float.parquet` |
| 逐日总股本 / 总市值 | `total_shares = float_shares × 当期总/流通比例`，标 `source=proxy` | `data/processed/shares/history_total.parquet` |
| 财报预约 / 实际披露日 | 东财预约披露接口，20 个报告期（2021Q4–2026Q3） | `meta.db` 表 `report_disclosure` |

```powershell
# 首次回补（需要网络；快照只抓一次）
uv run python scripts/backfill_share_capital.py
uv run python scripts/backfill_disclosure.py

# 网络不通时复用已落盘快照（纯本地计算，约 28 秒）
uv run python scripts/backfill_share_capital.py --skip-fetch

# 只统计不落盘
uv run python scripts/backfill_share_capital.py --skip-fetch --dry-run --json
```

消费一律走 `src/stock_plan/data/fields.py`（`as_of_total_shares / as_of_float_shares / as_of_total_mv / as_of_float_mv / as_of_disclosure`）：
**缺数据返回 `None` + 中文 `degraded` 说明，不抛异常也不静默**。披露日缺失时退回法定截止日（年报/一季报 4-30、
半年报 8-31、三季报 10-31），且 `as_of_disclosure()` 只返回 `announce_date <= 查询日` 的记录，杜绝未来函数。

已知限制（务必看）：**总股本历史是比例外推**，定增/回购/解禁不可回溯，只能用于市值分档，不可用于精确市值；
东财接口在本机间歇被网络层重置，重跑请用 `--skip-fetch`。完整覆盖率与降级实测见
[tests/reports/2026-10-29-05-53.phase12-fields.md](tests/reports/2026-10-29-05-53.phase12-fields.md)。

## 开发进度

当前 **Phase 0–16 全部完成**，并已修复数据层并发瓶颈与前复权回溯偏差。

- ✅ Phase 0 环境与骨架
- ✅ Phase 1 数据层
- ✅ Phase 2 因子层
- ✅ Phase 3 策略层
- ✅ Phase 4 信号层
- ✅ Phase 5 回测层
- ✅ Phase 6 模拟交易
- ✅ Phase 7 UI 仪表板
- ✅ Phase 8 MVP 集成验证
- ✅ Phase 9 V1 增强（多策略对比 / Walk-Forward / 详细报告 / LLM / 可视化拼装）
- ✅ Phase 10 V2 远期（LLM 复盘 / 新闻舆情 / 多周期并存 / 推送）
- ✅ Phase 11 V3 增强版（平铺 9 页导航 / 7 策略注册表 / 四大师研究页 / 均线自由组合 / 智谱 GLM-4-Flash 接入 / 深色主题）
- ✅ Phase 12 V4-Text（量价配置 / 板块自定义筛选 / 界面亲民化+新手指南 / 回测收益曲线 / 共享 UI 组件）
- ✅ Phase 13 V5（LLM 自然语言→结构化参数→保存策略全站打通 / 持仓诊断：清仓减仓做T建议 + 单股重回测）
- ✅ Phase 14 定时数据自动更新（增量引擎 + 计划任务 + 左侧「数据更新」页手动更新 + 进度 + 未缓存补拉）
- ✅ Phase 15 云端部署准备（requirements.txt + `st.secrets` 回退 + 平台化说明）
- ✅ Phase 16 云端数据快照持久化（`data-snapshot` 分卷发布/恢复 + 云端轻量模式 + 启动自动恢复）
- ✅ 数据层修复（2026-10-15）：新浪日线并发化（全市场 **75.5 → 13.7 分钟**）、`--rebuild`
  全历史重建修正前复权回溯、`save_bars` 原子写、计划任务重写为可重注册的脚本 + XML

### 策略刷新（`feature/strategy-refresh`，2026-10-29 起）

按 [策略选型调研](docs/策略选型调研-2026-10-29.md) 与
[实施计划书](.github/plans/done/2026-10-29-4-new-strategy-refresh-implementation.md) 落地五块改造，
每块都用同策略/同区间/同配置的 A/B 数字判定「成立」或「不成立」。进度：

- ✅ Phase 6 基线固化 + A/B 测量器（`scripts/measure_factor_ab.py`，7 策略 × 3 窗口基线）；
- ✅ Phase 7 均线收敛因子 PCF/VCF/PVCF/ACF/TRCF（新增 5 列；`conv_weight` 默认 0）——
  **结论：本数据上不成立，默认关闭**，理由见
  [A/B 报告](tests/reports/2026-10-29-04-41.phase7-ab.md)；
- ✅ Phase 8 动量方向修正（新增 `ma_ratio_20/60/120/240` 四列；`MomentumStrategy.momentum_mode`
  ∈ {`ret20`, `ma_ratio`, `ma_ratio_blend`}，默认 `ret20` 不变）——
  **结论：两种新口径均显著变差，保留原口径**，理由见
  [A/B 报告](tests/reports/2026-10-29-05-11.phase8-ab.md)；
- ✅ Phase 9 RRG 行业轮动 C1（新增 `factors/rrg.py` + `backtest/rotation.py` + `scripts/measure_rrg_industry.py`；
  31 个申万一级等权指数、`top_n=6`、20 日调仓）——
  **结论：行业层年化超额 −0.53pp（门槛 +2.0pp），C1 证伪、C2 不做**，理由见
  [C1 回测报告](tests/reports/2026-10-29-05-20.rrg-c1.md)；
- ✅ Phase 11 两段式「先选类型 → LLM 发挥」+ 规则级降级链路（新增 `strategy/archetypes.py`、
  `codegen.normalize_config_typed()`、`SignalReport.degraded`；六类型下拉，事件驱动类型仅占位不可回测；
  缺字段的规则会被禁用并在报告/UI 里标明「今天少算了哪几条」）；
- ✅ Phase 12 B 档字段补齐（新增 `data/share_capital.py` / `data/disclosure.py` / `data/fields.py`；
  总股本、逐日流通股本、财报披露日全部落盘，消费方缺数据一律 `degraded`）——
  **结论：7 策略默认输出逐值不变**，覆盖率见
  [字段报告](tests/reports/2026-10-29-05-53.phase12-fields.md)；
- ✅ Phase 13 集成与终验：全量 **547 passed**（改造前 445，+102 无下降）；7 策略在 full 窗口与 Phase 6 基线
  **逐值相同**（收益 / 年化 / 回撤 / 夏普 / 笔数 / 胜率），证明本轮所有新增能力默认全部关闭、未改变既有行为，见
  [基线 vs 最终对比](tests/reports/2026-10-29-06-02.phase13-compare.md)；
  计划书已归档到 `.github/plans/done/`，分支未合并 `main`。

**本轮总评（诚实版）**：A 收敛因子、B 动量方向、C RRG 三块在本数据上都没通过预注册判定线，因此保留为可选能力、默认关闭；
D 两段式与 E 字段补齐是纯增量能力，已可用。真正的主要矛盾是**日频换手 13–21 倍/年（年成本拖累约 5.8%–7.6%）与熊市段缺择时**，
建议下一阶段优先用已经存在但没人使用的 `factors/regime.py` + `BacktestConfig.market_timing`。

### 复筛与三档择时（`分支6`，2026-10-29 起）

按 [复筛计划书](.github/plans/2026-10-29-7-new-strategy-rescreen-and-timing-tiers.md) 执行，进度：

- ✅ Phase 18 全市场基线（7 策略 × W1 训练 / W2 验证 / W3 全期，`max_codes=0` 全市场）——
  三个窗口 **7 策略全部跑输等权基准**（基准 +21.95% / +8.31% / +32.67%）；年单边换手 14.0–22.2 倍，
  成本拖累 5.0–8.0pp/年，见 [基线报告](tests/reports/2026-10-29-17-08.rescreen-baseline.md)；
- ✅ Phase 19 双均线三档择时（`BacktestConfig.timing_tier`，默认 `"off"`）：`close ≥ MA20` 满仓 /
  `close < MA20` 半仓 / `close < MA60` 且 MA20 五日斜率向下 空仓；开启后**对已持仓真实降仓**（100 股整手、
  跌停停牌跳过、T+1 豁免、只封顶不追买）。回测 / 策略对比 / 可视化构建三页均新增「大盘择时档位」下拉，默认关闭；
  默认关闭时 21 arm 与 HEAD 基线**逐字段 0 差异**，全量 **654 passed**，见
  [实验报告](tests/reports/2026-10-29-17-30.phase18-19-timing.md)；
- ✅ Phase 20 全市场复筛矩阵（144 股票 arm + 6 行业轮动 arm，全市场 5379 只不抽样）：**候选层无一通过**
  预注册门槛——收敛族 W1 入选 `pvcf/突破`（+3.33pp）但 W2 只剩 +1.24pp（门槛 +2.0）；动量族
  `ma_ratio_blend/动量` W2 超额 +10.20pp 却只有 1/2 个年度为正（2025 下半年 -11.4%）。择时层 `weak` 对两组
  选中候选都「帮忙」，`tier` 回撤改善 16–20pp 但同样单年主导、按规则判「无差别」。RRG 行业层 W1 超额
  +0.46pp（门槛 +2.0），C2 不做。全量 **656 passed**，见
  [判定报告](tests/reports/2026-10-29-18-45.rescreen-judgement.md) 与
  [矩阵报告](tests/reports/2026-10-29-17-36.rescreen-matrix.md)；
- 🔄 Phase 21–22 待执行：结论落地（默认值不变）与文档、归档总结。

## 性能（回测与对比页，2026-10-29 起）

对回测、策略对比、Walk-Forward 与今日信号四条链路做了一轮「**结果逐值不变**」的性能改造
（计划书见 [.github/plans/done/2026-10-29-5-new-backtest-performance.md](.github/plans/done/2026-10-29-5-new-backtest-performance.md)，
完整前后对比、机器环境与复现命令见 [性能报告](tests/reports/2026-10-29-10-25.perf-backtest.md)）。

| 场景（全市场 5378 只） | 改造前 | 改造后（首次） | 命中缓存 |
| --- | --- | --- | --- |
| 单策略 5 年（lead 400） | ~512 s | ~135 s（加载 6.3 + 准备因子表 90.7 + 回测 37.9；不共享准备的单跑口径 ~82–87 s） | < 0.1 s |
| 单策略 1 年（页面默认，lead 180） | ~224 s | ~104 s | < 0.1 s |
| 策略对比 7 策略 × 1 年 | ~13 分钟 | ~2.6 分钟（155 s） | < 0.1 s |
| Walk-Forward（769 只抽样，2 窗口） | 113.1 s | 25.7 s | — |
| 今日信号「多周期并存」（3 周期） | ~155–159 s | ~85 s | — |

三条生效机制：

1. **引擎内层数组化**：因子表改为「列名 → numpy 矩阵 + int64 日期 + 行指针」，逐日逐股不再构造 pandas Series；
2. **准备层复用**：`prepare_backtest()` 一次算好全市场因子表，`run_backtest(..., prepared=...)` 跨策略、跨窗口复用；
   Walk-Forward 对整段区间只准备一次；
3. **UI 进程内缓存**：对比页共享同一份 prepared、逐策略缓存结果；缓存键包含区间、成本参数、持仓天数、择时、
   策略配置指纹与数据版本，数据更新后自动失效。

使用提示：

- 命中缓存时页面会显示「本次为缓存结果」；改区间 / 成本 / 择时 / 策略参数会自动重算，只改图表选项等展示参数不会重算。
- 回测页、对比页、可视化拼装页均有「🧹 清除缓存」按钮，可手动清空进程内缓存。
- 全市场 prepared 因子表约 1.2 GB，最多缓存 2 份（约 2.4 GB）；内存紧张时点「清除缓存」释放。
- 冷启动仍需为全市场构建因子表（约 76–91 s），因此「1 年单策略 ≤45 s」的计划目标在冷启动口径下**未达成**
  （命中缓存后秒开）；已知偏差与原因记录在计划书 §0.1 与性能报告中。

**逐值不变保证**：同一数据快照上 7 策略 × 3 窗口（21 arm）改造前后 0 差异（收益 / 回撤 / 笔数 / 胜率等全部字段），
`tests/test_factor_parity.py` 双向列集与数值对拍、`tests/test_engine_perf_parity.py` 内嵌旧实现对拍、
`tests/test_walkforward_prepared_parity.py` 均通过；全量测试 **637 passed**。

## 云端部署（Streamlit Community Cloud）

应用可免费部署到 [Streamlit Community Cloud](https://share.streamlit.io)，获得公网链接：

1. 将本仓库推送到 GitHub（如 `Cwywwy/Text-stock`，分支 `分支1`）
2. 打开 [share.streamlit.io](https://share.streamlit.io) 并用 GitHub 账号登录
3. 「Create app」→ 选择仓库 / 分支，主文件路径填 `src/stock_plan/ui/app.py`
4. Advanced settings 里 Python 版本选 **3.13**，点 Deploy

注意事项：

- 依赖读取根目录 `requirements.txt`（已按本地验证版本固定）
- 云端**数据自动恢复**：启动时检测本地无缓存数据，自动从 GitHub Releases 的 `data-snapshot`
  标签下载快照（约 360MB，首次 1~3 分钟），无需手动全量拉取；快照里**同时包含行情、行业分类、
  市场情绪、行业归属与公告库**，所以新访问者同步一次就有全部页面可看，不需要先点增量更新
  「🔄 数据更新」页的「同步到最新快照」走同一份地址表，快照超过 7 天会拒绝覆盖并提示
- **时区**：云端容器是 UTC，所有「今天/期望交易日」判断统一走 `stock_plan.data.clock`
  的北京时间，不依赖容器时区（否则北京时间 16:00–24:00 期望日会晚一天）
- Windows 计划任务自动更新仅在本地部署生效，云端请在「🔄 数据更新」页手动更新（云端轻量模式自动降低并发）
- 国内访问慢（首屏 20 秒以上）时，可改用下方「国内云服务器部署（方案 C）」，用同一份代码在自己的国内机器上跑
- LLM API Key 在云端走 **Secrets**：App 菜单 → Settings → Secrets，填入：

```toml
LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
LLM_MODEL = "glm-4-flash"
LLM_API_KEY = "你的key"

# 可选：显式启用云端轻量模式（推荐，比自动检测更可靠）
STOCK_PLAN_CLOUD = "1"
```

## 国内云服务器部署（方案 C）

Streamlit Community Cloud 的服务器在境外，国内首屏常要 20 秒以上。想快就在国内云服务器上自己跑一份，
代价是要自己维护机器。**设计前提**：服务器只做「托管 + 展示」，数据仍由本机发布快照、服务器定时拉取。

与云端实例的差别只有两点：

- 服务器是**常驻访客实例**（`STOCK_PLAN_SERVICE=1` + `STOCK_PLAN_CLOUD=1`），不跑 `update_daily` /
  分类 / 情绪 / 公告那一整套生产流程，也就不存在两套数据各自漂移
- 启动时不再同步 `raw.githubusercontent.com` 上的共享策略（国内常超时，每次白等最多 30 秒），
  也不再拉起本机发布脚本（这两个副作用由 `src/stock_plan/ui/startup_flags.py` 关掉）

三步跑起来（完整步骤、排障表、续费迁移见 [`deploy/README.md`](deploy/README.md)）：

1. 领机器：腾讯云轻量 **2 核 4G / 5Mbps / 60GB / Ubuntu 24.04**（新用户免费 1 个月）；控制台安全组放行 **TCP 8501**
2. 上传代码：服务器直连 GitHub `git clone`，或本机 `git bundle` 后 `scp`（国内回源 GitHub 慢时用后者）
3. 装依赖并起服务：

```bash
sudo bash deploy/bootstrap.sh        # 系统依赖 → uv → Python 3.13 venv → systemd → 首次拉快照
cp deploy/secrets.toml.example .streamlit/secrets.toml   # 填 LLM_API_KEY，然后 chmod 600
sudo systemctl enable --now stock-plan
```

之后浏览器打开 `http://<公网IP>:8501` 即可。

数据怎么更新：本机每交易日 16:30 由 `publish_snapshot.py` 发到 GitHub Releases 的 `data-snapshot` 标签，
服务器的 `stock-plan-snapshot.timer` 每天 **17:10** 拉一次，成功后重启服务让页面用上新数据（约 10–30 秒空窗）。

常被问到的问题：

- **不需要备案**：只用「公网 IP + 非 80 端口」，腾讯云文档明确「不使用域名解析指向服务器时不需要备案」；
  一旦绑了未备案域名，任何端口都会被监测阻断
- **GitHub 拉不动**：`deploy/README.md` 给了三种法子（设 `HTTPS_PROXY`、走镜像、从本机 `scp data/processed`），
  拉取失败只影响数据新旧，页面照常可用
- **没有鉴权**：任何人拿到 `IP:8501` 都能用，也会消耗你的 LLM 额度；要收回时在手册里加一层 Nginx/Caddy
  Basic Auth（本轮未提供）
- **不要在服务器上 `pip install .`**：`PROJECT_ROOT` 是按 `__file__` 往上三层算出来的，装进 site-packages
  会让 `data/` 指到 venv 里；依赖只装 `requirements.txt`，代码用 `PYTHONPATH=src` 引入
- **环境变量与阶段拆分的完整口径**：见
  [.github/plans/2026-10-29-1-new-deploy-on-china-cloud-server.md](.github/plans/2026-10-29-1-new-deploy-on-china-cloud-server.md)

## 云端数据快照（Phase 16）

云端容器磁盘是**临时的**：推送代码触发重建、闲置回收、内存超限重启都会清空 `data/`。
本方案由本机（权威源）每日发布数据快照，云端按需恢复，彻底解决"拉完一次数据之后又清空"。

架构：

```
本机 data/processed/（权威源：bars ≈306MB + 分类/情绪/行业/公告 ≈4MB）
   │  scripts/publish_snapshot.py（每交易日 16:30 计划任务 \StockPlan\Daily_Publish）
   ▼
GitHub Releases：tag = data-snapshot（资产：manifest.json + bars-<时间戳>.part00…，每卷 90MB）
   │  github.com/.../releases/download/data-snapshot 分发
   ▼
云端恢复：app 启动时 bars < 100 只自动下载（st.status 进度）；
「🔄 数据更新」页也有手动「同步到最新快照」按钮
```

- **快照 v2（2026-10-28）一个动作补齐全部数据**：zip 内布局为 `bars/*.parquet` +
  `classify/`、`market/`、`universe/`、`stock_basic.parquet`、`trade_calendar.csv`、
  `announcements.db`；恢复时按分组原子替换（旧数据改名 → 换新 → 校验文件数 → 删旧，
  任一步失败整体回滚）。旧版「只有行情」的快照仍能恢复（兼容平铺布局）
- **分卷名两种都认**：本地打包叫 `bars.zip.partNN`，Release 资产不能以 `.zip` 结尾，
  上传前改名成 `bars-<时间戳>.partNN`；校验器接受两种写法（只认前者时线上快照会被误判为损坏）
- **为什么用 Releases 而不是分支**：原先 force push 到 `data-snapshot` 孤儿分支，每天会往
  仓库里塞一份约 300MB 的 blob（GitHub 侧何时回收不可控），且 `raw.githubusercontent.com`
  有 CDN 缓存，force push 后短期内可能仍取到旧快照。改走 Release 资产后，仓库历史不再增长，
  下载地址也不经过那层缓存
- **发布原子性**：以 `manifest.json` 充当"提交指针" —— 先以带版本号的名字上传新分卷
  （不覆盖旧资产），全部就位后才替换 manifest，最后清理被取代的旧分卷。因此上传中途失败时
  线上仍是完整的旧快照
- **完整性校验**：逐卷 sha256 + 整包 zip_sha256 双重校验，原子替换，失败自动回滚不污染旧数据
- **回退能力**：恢复端会按 Release → 旧分支 的顺序尝试多个地址，发布方式切换期间不会拿不到数据；
  发布端可用 `STOCK_PLAN_SNAPSHOT_BACKEND=branch` 回退到旧的孤儿分支方式
- **旧快照安全阀**：启动恢复与手动「从云端快照恢复」共用同一份地址表
  （`snapshot_restore_bases()`），且手动恢复会校验 `manifest.created_at`，超过
  `SNAPSHOT_MAX_AGE_DAYS`（7 天）的旧快照直接判失败、继续尝试下一个地址，不再静默覆盖本地数据。
  2026-10-28 的云端事故就是手动恢复绕开地址表、写死读旧分支，导致"恢复成功"却退回 2026-09-03
- **云端轻量模式**：检测到云端时增量更新并发降为 3 线程、全量拉取仅 1 年（容器 2.7GB 内存防超限）
- **手动发布**：本机运行 `scripts\publish_snapshot.cmd` 或 `python scripts/publish_snapshot.py`
  （`--build-only` 只打包不推送，用于排查）
- **恢复快照**：云端页面 →「🔄 数据更新」→「⬇️ 同步到最新快照」；恢复完成后自动重扫
  `_freshness.json`，提示里会列出恢复了哪些数据（行情 N 只 + 行业分类 + 市场情绪 + 公告库），
  避免"提示成功但其实还是旧数据"
- **令牌来源**：显式参数 → `DATA_SNAPSHOT_TOKEN` 环境变量 → `.streamlit/secrets.toml`
  （本机计划任务不经过 Streamlit，靠最后这一层读取）

## 共享策略与全市场信号

本试用版不提供用户权限：用户新建的策略为**公开策略**，名称、参数与规则会对所有用户可见和可用。保存前必须确认公开提示；同名策略不会覆盖，请使用新名称。

新策略先进入“待生效”状态。本机每天的 `\StockPlan\Snapshot_2130` 任务会在发布行情快照前，使用完整 A 股数据计算全部待生效策略的 Top 20 信号；开发者打开本机 localhost 时也会补偿触发此任务。云端只读取已发布且与当前策略配置版本匹配的结果，因此不会在云端进行全市场扫描。

云端部署需在 Streamlit Secrets 增加仅供应用使用的细粒度 GitHub 令牌（只授予此仓库 Contents 读写权限）：

```toml
DATA_SNAPSHOT_TOKEN = "github_pat_..."
```

令牌不得写入代码、`README.md`、`.env` 示例或提交到 Git。未配置时，提交公共策略会显示明确的配置错误。

## LLM 配置（可选）

复制 `.env.example` 为 `.env`，填入 API Key 即可启用真实 LLM 分析（默认智谱 GLM-4-Flash，免费）：

```ini
LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
LLM_MODEL=glm-4-flash
LLM_API_KEY=你的key
```

未配置时自动降级为离线规则模式，所有功能仍可正常使用。

### 信号解读与 LLM 调用约束

今日信号页的「🔍 为什么是这几只」区块分成两层：

- **数据层**（默认展开即见）：入选者与紧随 3 名的分数对比、分差瀑布图、逐条扣分明细、被硬过滤挡掉的样本，
  全部由代码计算，不调模型、不额外耗时（与信号表共用同一次全市场打分）。
- **叙事层**（点按钮才调）：模型把事实清单组织成一段解读。校验不通过（编造数字、引用了不存在的代码、
  字段缺失）就自动回退到确定性模板，页面会写明原因——**模型只负责措辞，数字一律来自表格**。

相关的三个可选环境变量（写进 `.env` 或云端 Secrets）：

```ini
LLM_MAX_CONCURRENCY=2        # 同时在跑的 LLM 请求上限，默认 2
LLM_CACHE_DISABLE=1          # 设 1 关闭解读缓存（默认开启）
LLM_CACHE_TTL_HOURS=168      # 缓存有效期，默认 7 天
```

解读结果缓存在 `data/cache/llm/`（已被 `.gitignore` 忽略，不会进 Git）：同一份输入 7 天内重复点击直接秒开，
页面会标注「缓存于 x 分钟前」。清空缓存直接删这个目录即可。