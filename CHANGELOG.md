# 变更日志（Changelog）

> 按日期分组，从新到旧排列。

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
- 验证：`compileall` 全包 0 错误；`pytest` **63 passed**；12 页 AppTest 全绿

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
