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

## 云端部署（Streamlit Community Cloud）

应用可免费部署到 [Streamlit Community Cloud](https://share.streamlit.io)，获得公网链接：

1. 将本仓库推送到 GitHub（如 `Cwywwy/Text-stock`，分支 `分支1`）
2. 打开 [share.streamlit.io](https://share.streamlit.io) 并用 GitHub 账号登录
3. 「Create app」→ 选择仓库 / 分支，主文件路径填 `src/stock_plan/ui/app.py`
4. Advanced settings 里 Python 版本选 **3.13**，点 Deploy

注意事项：

- 依赖读取根目录 `requirements.txt`（已按本地验证版本固定）
- 云端**数据自动恢复**：启动时检测本地无缓存数据，自动从 GitHub Releases 的 `data-snapshot`
  标签下载快照（约 350MB，首次 1~3 分钟），无需手动全量拉取
- Windows 计划任务自动更新仅在本地部署生效，云端请在「🔄 数据更新」页手动更新（云端轻量模式自动降低并发）
- LLM API Key 在云端走 **Secrets**：App 菜单 → Settings → Secrets，填入：

```toml
LLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
LLM_MODEL = "glm-4-flash"
LLM_API_KEY = "你的key"

# 可选：显式启用云端轻量模式（推荐，比自动检测更可靠）
STOCK_PLAN_CLOUD = "1"
```

## 云端数据快照（Phase 16）

云端容器磁盘是**临时的**：推送代码触发重建、闲置回收、内存超限重启都会清空 `data/`。
本方案由本机（权威源）每日发布数据快照，云端按需恢复，彻底解决"拉完一次数据之后又清空"。

架构：

```
本机 bars/（权威源，约 350MB）
   │  scripts/publish_snapshot.py（每交易日 16:30 计划任务 \StockPlan\Daily_Publish）
   ▼
GitHub Releases：tag = data-snapshot（资产：manifest.json + bars-<时间戳>.part00~03，每卷 90MB）
   │  github.com/.../releases/download/data-snapshot 分发
   ▼
云端恢复：app 启动时 bars < 100 只自动下载（st.status 进度）；
「🔄 数据更新」页也有手动「从云端快照恢复」按钮
```

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
- **云端轻量模式**：检测到云端时增量更新并发降为 3 线程、全量拉取仅 1 年（容器 2.7GB 内存防超限）
- **手动发布**：本机运行 `scripts\publish_snapshot.cmd` 或 `python scripts/publish_snapshot.py`
  （`--build-only` 只打包不推送，用于排查）
- **恢复快照**：云端页面 →「🔄 数据更新」→「☁️ 从云端快照恢复数据」
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