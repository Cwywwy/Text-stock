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

股票列表来自新浪实时行情，只含**当前在市**的 5573 只。历史上退市、暂停上市、被吸收
合并的公司从未进入样本，**回测收益会被系统性高估**——这不是精度问题，而是会让
"策略有效"这个结论本身失真。

实测量级：2021-10-29 全市场只有 **4525** 只 A 股，而今天是 **5573** 只；仅采样 3 个
历史时点就能推出 298 只期间退市的股票。

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
- 云端**数据自动恢复**：启动时检测本地无缓存数据，自动从 GitHub `data-snapshot` 分支下载快照（约 300MB，首次 1~3 分钟），无需手动全量拉取
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
本机 bars/（权威源，298MB+）
   │  scripts/publish_snapshot.py（每日 21:30 计划任务 \StockPlan\Snapshot_2130）
   ▼
GitHub data-snapshot 分支（孤儿分支，单 commit，manifest.json + bars.zip.part00~03，每卷 90MB）
   │  raw.githubusercontent.com 分发
   ▼
云端恢复：app 启动时 bars < 100 只自动下载（st.status 进度）；
「🔄 数据更新」页也有手动「从云端快照恢复」按钮
```

- **完整性校验**：逐卷 sha256 + 整包 zip_sha256 双重校验，原子替换，失败自动回滚不污染旧数据
- **云端轻量模式**：检测到云端时增量更新并发降为 3 线程、全量拉取仅 1 年（容器 2.7GB 内存防超限）
- **手动发布**：本机运行 `scripts\publish_snapshot.cmd` 或 `python scripts/publish_snapshot.py`（`--build-only` 只打包不推送）
- **恢复快照**：云端页面 →「🔄 数据更新」→「☁️ 从云端快照恢复数据」

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