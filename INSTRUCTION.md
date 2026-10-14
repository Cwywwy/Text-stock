# 开发 Instruction（开发指南）

> 本文件是项目的开发指导文档，**每完成一个 Phase 必须同步更新**。
> 最后更新：2026-10-15（数据层并发化与前复权回溯修复）

---

## 1. 项目概述

盘前选股系统：基于 A 股技术面 + 基本面选股，输出盘前信号（目标买入/卖出价、止损、持仓周期），内置回测引擎与模拟交易，通过本地 Streamlit Web 仪表板查看。

- 详细设计：见 [plan.md](./plan.md)
- 启动文档：见 [README.md](./README.md)

## 2. 技术栈约定（已锁定）

| 项 | 选型 | 说明 |
|----|------|------|
| 包管理 | uv 0.12.9 | Python 3.13（`.python-version` 固定） |
| Web | Streamlit 1.63 | 侧边栏 radio 导航 + 页面模块 `render()` 函数 |
| 数据 | AKShare 1.18 | 免费数据源（东方财富/新浪） |
| 数据处理 | pandas 3.0 / numpy 2.5 | 注意：pandas 3.0 有 API 变更，勿用旧版写法 |
| 存储 | Parquet（行情）+ SQLite（元数据） | 路径见 `data/` |
| 图表 | Plotly 7 | Streamlit 原生支持 |
| 回测 | 自研（Pandas 向量化） | 不用 backtrader/vectorbt |
| 技术指标 | **自研** | ⚠️ pandas-ta 弃用：依赖 numba 限制 numpy<2.3，与 numpy 2.5 冲突 |
| LLM | 智谱 GLM-4-Flash（V3 已接入） | OpenAI 兼容端点，配置见项目根 `.env`（模板 `.env.example`） |

## 3. 代码规范

1. **注释**：用户为 Python 入门水平，所有函数/类必须有中文 docstring，关键逻辑加中文注释。
2. **导入**：统一 `from stock_plan.xxx import ...`（主包 src layout）。
3. **类型**：函数签名尽量带类型标注（`-> pd.DataFrame` 等）。
4. **命名**：模块/函数/变量用英文 snake_case；页面标题、UI 文案用中文。
5. **不引入**：不新增未在 plan 中的重型依赖；不写死绝对路径（用 `pathlib` 相对项目根）。
6. **数据路径**：统一通过 `stock_plan.data.paths` 或常量管理，禁止散落硬编码路径。

## 4. 目录结构（当前）

```
stock plan/
├── pyproject.toml          # uv 配置与依赖
├── plan.md                 # 计划书 + Phase Tasklist + 开发进度
├── INSTRUCTION.md          # 本文件
├── README.md               # 启动文档
├── data/
│   ├── raw/                # 原始数据（AKShare 落盘）
│   ├── processed/          # 清洗后数据（Parquet）
│   └── db/                 # SQLite（策略/回测/模拟交易记录）
├── scripts/
│   ├── daily_job.ps1       # 每日任务入口（增量更新 → 发布快照），计划任务调用
│   ├── register_tasks.ps1  # 一键注册/重注册计划任务
│   ├── publish_snapshot.py # 打包并发布数据快照到 GitHub
│   └── tasks/              # 任务计划程序 XML（版本化，换机器可直接导入）
├── src/stock_plan/
│   ├── data/               # 数据获取与缓存
│   ├── factors/            # 因子计算
│   ├── strategy/           # 策略引擎
│   ├── backtest/           # 回测引擎
│   ├── signal/             # 盘前信号生成
│   ├── simulator/          # 模拟交易
│   ├── llm/                # LLM Agent（V3 已接入，配置来自 .env）
│   └── ui/
│       └── views/          # Streamlit 页面（12 页平铺导航）
└── tests/                  # 测试用例
```

## 5. 开发流程

1. 每个 Phase 开始前：查询 `todos` 表确认可执行任务（依赖已满足）。
2. 每个任务开始：`UPDATE todos SET status='in_progress'`。
3. 每个任务完成：验证通过后 `UPDATE todos SET status='done'`。
4. **每个 Phase 完成后**：
   - 更新本文件（第 6 节进度 + 第 7 节决策记录）
   - 更新 [plan.md](./plan.md) 第 16 节开发进度
   - 更新 [README.md](./README.md) 开发进度
5. 验证方式：`uv run python -c "..."` 导入测试 / `uv run streamlit run` 启动测试 / 真实数据冒烟测试。

## 6. 开发进度

| Phase | 状态 | 说明 |
|-------|------|------|
| Phase 0 环境与骨架 | ✅ 完成 | uv + Python 3.13、目录结构、依赖、Streamlit 4 页骨架 |
| Phase 1 数据层 | ✅ 完成 | DataFetcher / Storage / 一键拉取脚本（多线程断点续传） |
| Phase 2 因子层 | ✅ 完成 | 技术因子 / 基本面因子 / 硬过滤 |
| Phase 3 策略层 | ✅ 完成 | Strategy 基类 / 内置趋势策略 / 打分模型 |
| Phase 4 信号层 | ✅ 完成 | 信号生成（Top5 + 买卖价 + 止损 + 持仓周期） |
| Phase 5 回测层 | ✅ 完成 | 回测引擎 / 指标 / 报告 / 7 个测试通过 |
| Phase 6 模拟交易 | ✅ 完成 | PaperTrader（T+1/手续费/印花税/滑点/涨跌停） |
| Phase 7 UI 仪表板 | ✅ 完成 | 4 个页面全部浏览器验证通过 |
| Phase 8 MVP 集成验证 | ✅ 完成 | 端到端联调 + 全量数据回测 + 参数寻优 |
| Phase 9 V1 增强 | ✅ 完成 | 多策略对比 + Walk-Forward + 详细报告 + LLM + 可视化拼装 |
| Phase 10 V2 远期 | ✅ 完成 | LLM 复盘 / 新闻舆情 / 多周期并存 / 推送 |
| Phase 11 V3 增强版 | ✅ 完成 | 平铺导航 / 7 策略注册表 / 四大师研究页 / 均线自由组合 / 智谱 GLM 接入 / 深色主题 |
| Phase 12 V4-Text 分支（分支1） | ✅ 完成 | 量价配置 / 界面亲民化 / 回测收益曲线 / 分支 API KEY / 板块自定义筛选 / 新手指南页 |
| Phase 13 V5-LLM 策略生成 + 持仓诊断 | ✅ 完成 | LLM 自然语言→结构化参数→保存策略全站打通 / 持仓诊断（清仓/减仓/做T建议 + 单股重回测） |
| Phase 14 定时数据自动更新 | ✅ 完成 | 增量更新引擎（幂等）+ Windows 计划任务 + 左侧导航「数据更新」页（手动更新/进度/未缓存补拉） |
| Phase 15 云端部署准备 | ✅ 完成 | requirements.txt（Cloud 只认它）+ `st.secrets` 回退 + 平台化说明 |
| Phase 16 云端数据快照持久化 | ✅ 完成 | `data-snapshot` 分卷发布/恢复（双 sha256 + 原子替换回滚）+ 云端轻量模式 + 启动自动恢复 |
| 数据层修复（2026-10-15） | ✅ 完成 | 新浪日线并发化 75.5→13.7 分钟、`--rebuild` 修正前复权回溯、`save_bars` 原子写、计划任务脚本化 |
| 暂时需求（2026-10-15，已合入 main） | ✅ 完成 | 1 图表交互 / 2 要闻紧凑列表 / 3 个股新闻搜索 / 4 策略分组标签 + **客户归属** + 批量删除 / 5a 回测成本自定义 / 5b 过拟合自查 / 5c 退市股历史库（含回测接入）/ 6 市场阶段因子 + 缠论结构识别。**注**：第 4 项的"分客户"按**方案 B**（owner 归属标签）实现，真多用户（身份+隔离）未做，见 `.github/plans/2026-10-15-3-...` |

## 7. 关键决策记录（持续追加）

| 日期 | 决策 | 原因 |
|------|------|------|
| 2026-09-03 | 技术指标**自研**，弃用 pandas-ta | pandas-ta 依赖 numba 限制 numpy<2.3，与 numpy 2.5 冲突 |
| 2026-09-03 | 目录用 `src/stock_plan/` 主包结构 | uv 标准 src layout，import 为 `from stock_plan.xxx` |
| 2026-09-03 | Streamlit 用侧边栏 radio 导航 + 页面 `render()` | 简单清晰，符合入门水平 |
| 2026-09-03 | 8501 端口被其他进程占用，开发用 8502 | 环境限制，README 仍写 8501（用户机器默认） |
| 2026-09-03 | LLM 不进入 MVP | 用户确认，调试完核心闭环后再接入 |
| 2026-09-03 | 数据源统一走**新浪**接口 | 东方财富/上交所接口在当前网络环境 RemoteDisconnected 不可用；新浪 `stock_zh_a_spot`/`stock_zh_a_daily`/`stock_financial_abstract` 可用 |
| 2026-09-03 | 北交所代码前缀规则含 **9 开头（920xxx）** | 北交所新代码 920xxx 以 9 开头，`_to_sina_symbol` 需映射为 `bj`；否则误判为 `sh` 导致日线拉取 KeyError 'date' |
| 2026-09-03 | 回测引擎**预计算因子** + 指针查表 | 逐日对每只股票重算因子太慢（10+ 分钟）；预计算后按日指针单调推进，单次全量回测降到 ~52 秒 |
| 2026-09-03 | 回测默认配置：**weekly + 大盘择时 + 30 天持仓** | 全量 5553 只数据参数寻优：weekly 优于 daily，大盘择时在 weekly 下改善收益 |
| 2026-09-03 | 策略默认参数：**m=3.5 / n=3.5 / hold=30** | 全量数据邻域验证：止盈 3.5×ATR + 止损 3.5×ATR + 30 天持仓，收益 +9.37%、夏普 0.56 |
| 2026-09-03 | 回调买入逻辑（ma20>ma60 + 偏离 ma20 惩罚） | 因子分析：偏离 ma20 0~5% 的股票正收益，>10% 明显亏损（追高陷阱） |
| 2026-09-03 | **不启用** vol_ratio 与 RSI<40 惩罚 | 隔离测试：两者单独都拖累收益（+1.88%→-2.05%/-0.19%），叠加更差（-10.17%） |
| 2026-09-03 | metrics 的 total_return 等**已是百分比数值** | 显示时直接用 `:.2f`，不要再用 `:.2%` 或 `*100`（会放大 100 倍） |
| 2026-09-03 | 新增**动量策略**（MomentumStrategy）用于策略对比 | 第二策略：mom_ret 近 20 日涨幅加分 + ma20>ma60 + 偏离>10% 排除 + RSI>80 扣分，子集 800 只 +15.60% |
| 2026-09-03 | Walk-Forward 滚动验证模块（walkforward.py） | train_days=180 + test_days=90 滚动窗口，输出样本外 OOS 指标；1 年数据只产生 1 窗口，建议 2 年以上数据 |
| 2026-09-03 | 详细回测报告增强（report.py） | 新增退出原因归因 / 月度收益热力图 / 回撤标注，backtest.py UI 同步展示 |
| 2026-09-03 | LLM 模块可插拔（llm/client.py + analyzer.py） | OpenAI 兼容客户端，无 API key 时 mock 降级返回模板文本；UI 页含信号解释/消息面分析/策略生成 3 tab |
| 2026-09-03 | 可视化策略拼装（custom.py + visual_builder.py） | CustomStrategy 由 config dict 驱动（权重/规则/参数），UI 页可配置/回测/保存策略，today.py 支持自定义策略 |
| 2026-09-03 | 策略对比页（compare.py） | 多策略对比 tab（资金曲线/指标对比）+ Walk-Forward tab（滚动窗口 OOS 结果） |
| 2026-09-03 | LLM 信号解释读取 `entry_price`/`exit_price` 键 | today.py 存入 session_state 的信号 dict 用 `entry_price`/`exit_price`，而 analyzer.py 误读 `buy_price`/`sell_price` 导致显示 0；统一为 `entry_price`/`exit_price`（保留旧键名兜底） |
| 2026-09-03 | 个股公告用**东财公告接口**（`np-anotice-stock.eastmoney.com/api/security/ann`） | `stock_news_em` 在 pandas 3.0 下正则非法转义报 ArrowInvalid；东财搜索 API 只返回 passportWeb 不返回新闻；公告接口实测可用（status 200，20 条/页） |
| 2026-09-03 | 新闻舆情时间线（news/timeline.py）聚合 7 个新闻源 | 个股公告（东财接口）+ 市场要闻（stock_info_global_em）+ 财经日历（news_economic_baidu）+ 分红/停牌公告（news_trade_notify_*_baidu）+ 央视新闻（news_cctv）+ 财新要闻（stock_news_main_cx）；UI 页 news.py 4 个 tab |
| 2026-09-03 | 同策略多周期并存输出（today.py 重写） | PERIODS 配置（短线 10 / 中短线 20 / 中线 30 天），持仓周期下拉框含"多周期并存"选项，并存时生成 3 组信号分别渲染 |
| 2026-09-03 | 推送模块（push/notify.py）支持飞书/微信 | 飞书 Webhook（FEISHU_WEBHOOK）+ Server酱（WECHAT_SENDKEY）环境变量，today.py 信号块加"推送"按钮 |
| 2026-09-03 | LLM 自动复盘（analyzer.py review_backtest） | 无 API key 时 mock 降级为离线规则复盘（总体评价/退出归因/参数优化建议/风险提示）；backtest.py 加"🤖 LLM 复盘与参数优化建议"按钮 |
| 2026-09-03 | 回测结果存入 `session_state`（backtest.py 重构） | 原结果只在"运行回测"按钮块内渲染，点击 LLM 复盘按钮触发 rerun 后按钮返回 False 导致结果丢失；改为存 session_state 后从状态渲染，任何按钮交互不丢结果 |
| 2026-09-03 | V3 导航改为 **st.navigation 平铺 9 页**（ui/pages/ → ui/views/） | 用户要求后续功能模块化拆分，分组导航冗余；平铺便于按需增删页面 |
| 2026-09-03 | **策略注册表**（strategy/registry.py，7 策略统一管理） | 新增均值回归 / 突破 / 价值 / 段永平 / 李录 5 个策略，含 description 讲解与 PARAM_DESC 参数说明，today/compare/backtest/strategy_mgr 全部走注册表 |
| 2026-09-03 | **四大师研究独立页面**（views/four_masters.py + analyzer.four_masters_analysis） | 巴菲特/芒格/段永平/李录四视角对抗式基本面分析（参考 ai-berkshire 框架）；LLM 页主定位保持"模糊想法→策略实现"，两者分离；无 LLM 时降级为按 value/growth/quality 分数的规则化打星报告 |
| 2026-09-03 | 自定义策略**均线自由组合**（trend_ma_fast/trend_ma_slow/dev_ma，周期 5/7/10/20/30/60） | 引擎预计算 ma5/7/10/30 列；趋势条件为快线>慢线任选对，偏离基准线任选 |
| 2026-09-03 | LLM 配置走 **.env**（llm/config.py + python-dotenv 式自研加载器） | 用户选定智谱 GLM-4-Flash（open.bigmodel.cn OpenAI 兼容端点，免费）；key 存 .env 不入源码，`.gitignore` 已加 `.env` |
| 2026-09-03 | **深色主题**（.streamlit/config.toml） | 用户偏好深色系界面；base=dark + 自定义配色 |
| 2026-09-03 | 策略管理页支持**多策略参数编辑**（strategy_params_by_strategy） | 参数按策略名存 session_state，通用参数编辑器按类型分派控件；趋势策略兼容旧 strategy_params |
| 2026-09-03 | V4 在 Text 克隆+分支1 实施，**不动主目录主分支** | 用户要求：主分支保持可运行版本，Text/data junction 共享数据目录 |
| 2026-09-03 | 量价配置：流动性下限（liquidity_min 亿元）+ 放量异动（vol_surge_min 量比阈值 / vol_surge_bonus 加分） | custom.py 规则实现：流动性不足 -100 分排除，放量布尔×bonus 加分；builtin.py 加 avg_amount20 列 |
| 2026-09-03 | **板块筛选**（factors/board.py）| BOARDS 前缀映射：主板 60/00、创业板 30、科创板 68、北交所 82/83/87/43/92；filter_universe_ui 在策略硬过滤前执行，ST 剔除与板块过滤需同步作用于 stock_list 与 bars_map 两处（否则 ST 漏网） |
| 2026-09-03 | **共享 UI 组件**（ui/widgets.py） | board_filter_ui / apply_universe_filter / page_glossary / equity_curve_fig 四组件，today/backtest/compare/builder 4 页复用；缓存键加 boards_json+exclude_st |
| 2026-09-03 | **收益曲线图**（equity_curve_fig） | 累计收益率%（首值归一）+ 历史最高点线 + 回撤红色阴影（fill tonexty）；backtest/builder 两页展示 |
| 2026-09-03 | **亲民化文案**：新手指南页（guide.py）+ 9 页名词速览 + PARAM_DESC 大白话 | 5 类 22 术语（趋势/量价/风控/回测/基本面板块），每词含一句话解释+生活化例子+为什么有用；三分钟核心逻辑+免责声明 |
| 2026-09-03 | 分支1 专用 API KEY 走 Text/.env | 智谱 GLM-4-Flash，.gitignore 已排除不入库 |
| 2026-09-03 | **LLM 策略生成走参数主路线 + 代码提案旁路**（strategy/codegen.py + llm/analyzer.py） | LLM 分析自然语言 → 输出 WEIGHT/RULE/PARAM 三类结构化参数（严格钳制区间：weights 0~2.0、ATR 倍数 0.5~20、hold_days 1~250、dev_ma ∈ {5,7,10,20,30,60}，均线快慢成对否则双零）→ UI 参数对照表（默认值 vs 建议值）；现有参数表达不了的想法进"未支持参数反馈"清单并生成注释版/可运行脚本版代码提案，代码不直接参与选股 |
| 2026-09-03 | **已保存策略持久化**（strategy/store.py，SQLite data/db/strategies.db） | save_strategy（同名覆盖，source=llm/builder）+ strategy_options() + resolve_strategy()；今日信号/回测/对比/策略管理 4 页统一走 store，保存后立即可用 |
| 2026-09-03 | **持仓诊断引擎**（analysis/holding.py + views/portfolio.py，导航第 2 位） | 输入买入日期/买价/策略 → 规则对照给 4 级结论（清仓/减仓/做T/持有）+ 止盈止损位按策略口径折算到用户买价；做T建议：低吸=max(MA10, 现价-0.8×ATR)、高抛=min(近20日高, 现价+0.8×ATR)，按现价距哪端更近定正T/反T；附 120 日走势图（买入价/止损/止盈/做T价位 hline）与单股近一年重回测 |
| 2026-09-03 | 买入日期晚于最新行情日时**近似诊断** | 当日刚买的情况：以最新交易日为买点近似计算，UI 显示 st.info 提示，避免直接报错 |
| 2026-09-03 | filter_universe 需要 stock_list 含 **is_st 列** | 单股回测构造 stock_list 时必须带 `is_st: 0`，否则 KeyError |
| 2026-09-03 | **定时更新用独立增量脚本 + 系统级调度**（data/updater.py + update_daily.py + scripts/run_update.cmd） | 用户要求网页将来分享给朋友，调度放 OS 层（Windows schtasks / 将来 Linux cron）；任务名 `\StockPlan\Update_1600/2000/0000/0800`，日志 data/logs/update.log |
| 2026-09-03 | 增量更新**幂等设计**：本地最后日期 ≥ 期望最新交易日则跳过且不联网 | 一天 4 个时间点重复运行无副作用；周末/节假日自动空转；期望日期规则=交易日且过 16:00 取当天，否则上一交易日（交易日历 akshare 拉取，本地按天缓存 CSV，失败回退周一~周五近似） |
| 2026-09-03 | 增量合并=**旧数据 + 新数据 concat 后按日期去重（keep=last）整体重写** | storage.save_bars 是整体覆盖写（无 append），直接写 30 天新数据会丢 5 年历史；合并后单文件重写成本可忽略 |
| 2026-09-03 | 「数据更新」做成**左侧导航独立页**（views/update_center.py） | 右上角悬浮按钮会遮挡界面元素，用户两轮反馈后定为导航模块：状态指标 + 手动增量更新 + fragment 3 秒轮询进度 + 未缓存股票提示单独补拉 + 更新日志 |
| 2026-09-03 | 更新任务以**独立子进程**执行，UI 经 data/logs/update_progress.json 轮询进度 | akshare 依赖 py_mini_racer（V8 引擎），在 Streamlit 进程内初始化偶发 `FATAL:partition_address_space.cc` 致命崩溃拖垮整个服务器（exit -2147483645）；子进程隔离后崩溃不影响主服务。fragment auto-rerun 内 `st.rerun(scope="app")` 不向客户端传播（Streamlit 1.63 怪癖），故结果全部在 fragment 内每 tick 重绘 |
| 2026-09-03 | 云端部署走 **requirements.txt + st.secrets** | Streamlit Community Cloud 只认 requirements.txt（不读 pyproject），版本按本地验证固定；.env 不入库，云端 LLM Key 从 st.secrets 读取回退 |
| 2026-09-03 | 今日信号/回测页 `saved` 参数兜底 `{}` | 选择未在「策略管理」编辑过参数的策略时 `by_name.get(name)` 返回 None，`{**saved}` 抛 TypeError；改为 `or {}` 走策略默认参数 |
| 2026-09-04 | **云端数据快照外置**（data/snapshot.py + GitHub `data-snapshot` 孤儿分支） | 云端容器磁盘临时：推送重建/闲置回收/内存超限重启都会清空 data/（"拉完一次又清空"根因）。本机为权威源，每日 21:30 计划任务发布快照（单 zip 分卷每卷 90MB + manifest 双 sha256 校验，单 commit force push 不占仓库历史）；云端启动时 bars<100 自动恢复，原子替换失败回滚 |
| 2026-09-04 | **云端轻量模式**（is_cloud + workers=3 + 全量 366 天） | Streamlit Community Cloud 容器 2.7GB 内存，8 线程全市场拉取易被打爆；显式 `STOCK_PLAN_CLOUD=1`（Secrets）优先，其次平台特征变量与 HOSTNAME 尽力检测 |
| 2026-09-04 | snapshot 子进程调用 git 加 `encoding="utf-8"` | Windows 下 Python 默认 GBK 解码 git 输出，UTF-8 中文路径/输出会 UnicodeDecodeError（publish 首跑实测触发） |
| 2026-10-15 | **新浪日线改并发**：全局锁 → 线程本地 V8 上下文（只锁"创建"那一步） | 原锁包住了 3 个串行 HTTP，实测 8 线程仅 1.03x 加速、全市场 78~104 分钟。崩溃成因是 akshare 每次调用都 `new` 一个 MiniRacer，**并发创建** V8 上下文才崩（partition_address_space.cc），复用上下文即安全。保留 `STOCK_PLAN_SINA_LOCK=1` 可回退旧行为 |
| 2026-10-15 | 新增 **`--rebuild` 全历史重建**（`rebuild_history`） | 前复权因子是回溯性的，"只补最近 30 天再合并"会让更早的行停留在旧因子上。实测间隔约 6 周：000001 偏 2.19%、601318 偏 1.74%。新浪每次请求返回整份历史文件，重拉 5 年与补 30 天成本几乎相同（实测 5553 只 9.6 分钟） |
| 2026-10-15 | `Storage.save_bars` 改**临时文件 + `os.replace` 原子写** | 增量更新/全量重建会在 Streamlit 运行期间改写同一文件，直接覆盖会让并发读取方读到半个 parquet |
| 2026-10-15 | 幂等预筛改 `Storage.last_date`（**只读 date 列**）+ `cache_exists` 单次遍历分桶 | 为拿一个日期解析整份 parquet 不划算；原 cached/uncached 各调一次等于每只股票 stat 两次 |
| 2026-10-15 | `workers` 默认 8 → **16** | 实测 16 线程 0.130s/请求，24/32 线程无进一步收益 |
| 2026-10-15 | 计划任务改为**脚本 + XML 注册**（`scripts/register_tasks.ps1` + `scripts/tasks/*.xml`） | 旧任务 `LogonMode=Interactive only`、动作用 `cmd /c ""...""` 包装含空格与中文的路径、指向已失效的旧目录与旧谱系，且 4 个更新任务已丢失。新 XML 固定 WakeToRun / StartWhenAvailable / 非交流电 / PT3H 超时 |
| 2026-10-15 | `.ps1` 必须带 **UTF-8 BOM**，任务 XML 必须 **UTF-16** | PowerShell 5.1 对无 BOM 脚本按 GBK 解码，中文注释直接语法报错；任务计划程序要求 UTF-16，否则中文路径失真 |
| 2026-10-15 | `.streamlit/secrets.toml` 加入 `.gitignore` | 内含 GitHub PAT（Contents 读写），此前未被忽略，`git add -A` 一次就会入库并推送。已确认它从未进入任何提交或远端，**无需轮换** |
| 2026-10-15 | 复制/搬移项目后必须重跑 `uv sync` | `.venv\Lib\site-packages\stock_plan.pth` 存的是**绝对路径**，复制后仍指向旧目录，`import stock_plan` 会静默加载旧代码（表现为测试假绿、app 读旧数据）。`uv sync --frozen` 会把 editable 安装改指新路径 |
| 2026-10-15 | 图表统一走 `PLOTLY_CONFIG` + `apply_time_axis`，x 轴改用真日期类型（暂时需求 1） | 原先日期被转成 `str(d)[:10]`，Plotly 当成**分类轴**：刻度位置固定，缩放时不重算密度也不换格式。另外 Streamlit 里 Plotly 默认禁用滚轮缩放，只能框选做阶梯式放大 |
| 2026-10-15 | 要闻改「紧凑列表 + 详情子视图」，条目存 session_state（暂时需求 2） | 原先每条要闻一个 expander，展开长文会把下方模块（财新按钮、CCTV 日期选择器）顶出屏幕。改用按钮后必然触发 rerun，条目不缓存就会一进详情就丢 |
| 2026-10-15 | 个股新闻搜索：代码或名称 + 时间跨度 + 外部链接/LLM 联动（暂时需求 3） | `resolve_stock()` 名称命中多只时返回候选让用户选，不猜。同时修掉 `get_news(code, days=7)` 的 days 从未被使用、调用方却把 page_size 当它传入的 bug（条数与跨度双双失效，永远固定 20 条） |
| 2026-10-15 | 回测交易成本可自定义（UI 用万分之）+ 过拟合自查面板（暂时需求 5a/5b） | 原先佣金万三/印花税千一/滑点千一写死在 `BacktestConfig` 默认值，UI 改不了。自查面板用本次回测的**真实数字**（样本长度、交易笔数、单笔均收益 vs 往返成本）而非固定文案 |
| 2026-10-15 | `baostock` 作为退市股唯一可用数据源，**惰性导入**（暂时需求 5c） | 新浪源对退市代码全部 `JSONDecodeError`；上交所名单接口 `query.sse.com.cn` 在本网络不可达，改用「历史时点池并集 − 今日在市」推算。惰性导入使应用与云端不必安装 baostock |
| 2026-10-15 | `is_a_share` 必须按**交易所**判断代码前缀 | 上证指数是 `sh.000001`，只看数字前缀会被当成深市 A 股：不仅虚增股票池，落库成 6 位代码后还与平安银行 `sz.000001` 撞车 |
| 2026-10-15 | 时点股票池取"最近有数据的交易日"而非自然月末 | 自然月末常落在周末/节假日，baostock 对非交易日返回空——实测最近 2 年的 25 个月末里有 9 个取不到数据（如 2021-10-31 是周日，需回退到 10-29） |
| 2026-10-15 | 时点池改由 `query_stock_basic()` 一次调用**本地推导**，废弃按月爬 `query_all_stock` | 长会话会退化（第 21 个月起空/截断，之后连续 24 个月全空）、baostock 无超时会**无限挂起**；且取数循环原先在 `error_code != 0` 时静默返回空列表，把"请求失败"伪装成"没有数据"，把问题埋了 24 个月。新方案 61 个月 **0.2 秒**完成、0 失败 |
| 2026-10-15 | 引擎新增"**持仓期间行情终结**"处理：按最后可成交价强制平仓并归因 | 原先三处联动缺陷让持仓**凭空消失**：持仓检查跳过无 K 线的股票（永不退出）、每日估值计 0（资金曲线假暴跌）、收尾只平仓"最后一天有 K 线"的股票（不记交易、成本不回现金）。该 bug **不需要退市股**就能触发，任何被持有的停牌股都会中招。用 `last_bar < 当天` 区分"退市"与"临时停牌"，无需宽限期参数 |
| 2026-10-15 | 退市股纳入回测样本改为**显式开关**且默认关闭 | 纳入后原先的系统性高估被去掉，回测结果与历史调参结论都会明显变化，不应无声生效。实测 5 年窗口内约 3.5%（190~192 只）的标的在此期间退市 |
| 2026-10-15 | 大盘代理改**等权收益指数**，替换 `closes.mean(axis=1)` | 平均原始股价会被高价股主导（茅台 1300 元与 3 元股权重相同），且成分股缺 K 线时指数**跳空**，导致"大盘跌破 MA20"可能由成分变动而非行情触发。`market_timing=True` 是默认配置，故一直在影响回测 |
| 2026-10-15 | 市场阶段判定规则写成**可解释、可测试**的三段式 | `close > MA20 > MA60` 且 MA60 近 20 日上行 → 上升；反向 → 下降；其余 → 震荡；均线未成形时标"数据不足"。阶段 → 策略偏好与建议仓位（上升 0.9 / 震荡 0.5 / 下降 0.2，高波动再打七折）。**注意：随机游走的末段本身有方向，判成趋势并不算错**——第一版自测用随机游走当"震荡"用例，反而误判了自己 |
| 2026-10-15 | 缠论只做**机械可算**的部分，并显式声明未实现范围（暂时需求 6 后半） | 包含处理/分型/笔/笔中枢/三买 的规则明确、可逐步人工验证；而线段、走势类型、**背驰**（无统一定义，MACD 面积法属启发式）容易实现得似是而非，因此不做，一买/二买也随之不提供。把"识别出中枢"当成买卖信号是常见误用 |
| 2026-10-15 | 快照分发从**孤儿分支**改为 **GitHub Releases 资产** | 每天 force push 会往仓库塞约 300MB blob（回收不可控），且 raw 的 CDN 缓存会让 force push 后短期取到旧快照。Release 资产地址恰好是 `{base}/{资产名}`，所以恢复端只需换 base URL |
| 2026-10-15 | **不要用单次 A/B 的收益差量化池子/规则变更的影响** | 实测：引擎是确定性的（同输入两次差异 0.0000pp），但候选池只增 3.75%（放入 201 只今天的 ST 股）就让总收益差 **+20.97pp**，而**成交股票的重合度只有 Jaccard 0.226**（换掉 77%），被测试的 ST 股自己只成交 8 笔。即差异由**路径依赖**主导，不携带"被测试性质"的信息。要看分布（配对多组子样本 + 标准误），或改用不依赖选股路径的指标（如池内前瞻收益对比） |
| 2026-10-15 | **修正此前结论**：上一轮"幸存者偏差 −0.02pp ≈ 0"的说法不成立 | 按上一条，−0.02pp 只说明"在这个策略的噪声范围内看不出差异"，**不等于**偏差为 0。机制性结论（僵尸候选占位）有专门单元测试与反向验证支撑，但**幅度**不宜当作精确数字引用 |
| 2026-10-15 | ST 过滤是**未来函数**，暂不修但已加代码内警告 | `filter_universe` 用 `stock_list["is_st"]`（= 今天状态）排除 ST，而引擎开跑前一次性建池 → 用 2026 年的 ST 状态决定 2022 年能不能买。股票被戴帽往往因前期大跌，构成前瞻偏差。修它需要 **ST 时点历史**（现无此数据源，baostock 也不提供），故在 `factors/filter.py` 模块文档写入醒目警告 |
| 2026-10-15 | RSI 只涨不跌时应为 **100**，不能用中性的 50 掩盖 | 原 `fillna(50)` 会把连续涨停这种最强动量填成中性，反而拿到 `trend_score` 的「50≤rsi≤70 → +20 分」奖励。实测影响面 590/7,472,101 股票-日（0.0079%），集中在次新股上市初期。保留 `fillna(50)` 仅用于预热期 |
| 2026-10-15 | 审计要先**测量**再下结论 | 我最初以为"近 14 日无一下跌"的行都会中招 RSI 填充，实测这些行是 79/93/96 —— 因为 `avg_loss` 是**覆盖全历史的 EMA**、不是只看 14 天。先测量避免了把错误结论写进文档 |
| 2026-10-15 | 回测是**同柱决策 + 同柱成交**，与实盘路径口径不同（已加代码内警告，未改行为） | 引擎用 `ts` 当天收盘算出的因子行打分、又按 `ts` 当天收盘价成交；实盘路径是"盘前用昨收出信号、按今日收盘成交"。**两者收益不能直接对比**。实测方向：次日开盘 / 当日收盘 − 1 的均值 **−0.103%**（为正比例仅 34.2%）→ 改成次日开盘成交反而更便宜，故当前约定对买入价**偏保守、未虚高收益**。**又一次"机制存在但方向与预期相反"**。改哪种约定属产品选择，应先定再动引擎 |
| 2026-10-15 | `paper.py` 的印花税千一是**保守**取值 | A 股印花税自 2023-08-28 起已减半为 0.05%，2023 年前才是 0.1%。用千一偏高、方向安全（成本写得偏贵），若要贴近现实可按日期分段。记为待办而非缺陷 |
| 2026-10-15 | **回测与实盘的因子列必须完全一致**（曾双向不一致，已修） | 两条路径各自内联构造因子行：`mom_ret`/`ma5/7/10/30` 只有回测有、`avg_amount20`/`macd`/`macd_signal` 只有实盘有。因 `CustomStrategy.score` 用 `if "列" in df.columns` 守卫，**缺列不报错、直接跳过规则** → "同一策略在两个页面选出不同股票"。现统一列集并由 `tests/test_factor_parity.py` 锁死 |
| 2026-10-15 | 引擎里的 `avg_amount20` 必须用 `rolling(20)`，不能用 `bars["amount"].tail(20)` | `tail(20)` 取的是**整条序列末尾**：在"序列止于现在"的实盘路径里是对的，放进逐日回测就是未来数据。这是本类 bug 的经典陷阱，已单独立一条用例锁死 |
| 2026-10-15 | 写测试要确认它**真的有判别力** | 本项我写错三版：① 用"某只股票没被买入"断言，实测**旧代码上也通过**（受 top_n 与候选数影响）→ 改为直接测规则；② 用 `Series.set_index`（那是 `DataFrame` 的方法）→ AttributeError；③ 测错方向（`avg_amount20` 缺的是引擎侧而非实盘侧）。最终以"stash 掉修正后 4 failed"确认四条都具判别力 |
| 2026-10-15 | **PowerShell 的 `**` 不是递归通配符**，全库扫描必须用 ripgrep 或 `rglob` | 我此前几轮用 `Select-String -Path 'src\stock_plan\**\*.py'`，实际**只匹配一层目录**，`src/stock_plan/ui/views/`（两层）从未被搜到。靠 `visual_builder.py` 里一处我断言"不存在"的导入才发现。已用 ripgrep 重做关键扫描：baostock 惰性导入、无负向 shift/居中窗口/`except: pass` 等结论**均不受影响**，但"死代码"那条证据不全已更正 |
| 2026-10-15 | ST 的时点状态**用现有可达数据无法重建**（已实测负结果） | 三条路都不通：沪市更名接口无日期、深市更名接口只有公司全称（ST 是**简称**标记）、东财 ST 名单 `ConnectionError`。另试了"ST 主板涨跌幅 ±5% 反推"：**ST 与非 ST 的特征中位数完全相同（0.0500）**，主板召回 19.3%、精确率仅 4.2%（比随机猜更差）。我的推理错在假设非 ST 股会频繁出现 >5.5% 单日波动，实测 ≥10% 的非 ST 股在 120 日内一次都没有。发现 1 彻底收口 |
| 2026-10-15 | 剩余审计模块结论 | `analysis/holding.py` 的 4 处 `except` **都不吞错误**（各自把 `f"...失败：{e}"` 返回给用户）；`ui/views/visual_builder.py` **不会**写出越界均线周期（`ma_options`/`dev_ma` 选项本身受限，`int(dev_ma)` 不会遇到"关闭"）。顺带清理：删除 `build_custom_factor_rows` 的无用导入，并把该函数改为转发 `build_factor_rows`（列集统一后已冗余），验证两者输出列集与数值完全一致 |
| 2026-10-15 | "分客户"按**方案 B**（owner 归属标签）落地，并在多处显式声明**不是安全边界** | 需求本身有歧义，方案 A/B/C 工作量差一个数量级。选 B 的理由：① 是方案 C（真多用户）的**兼容前置**；② 它是纯增量、可回退；③ A（纯标签）已具备、C 要 3~5 天且改云端数据流。**owner 只是自由字符串，换个名字就能看到全部**，不能用于对外服务的权限控制 —— 这句已写进 `store.py` 文档、UI 帮助文字与决策记录 |
| 2026-10-15 | 归属冲突规则：同名策略保存时**不改变 owner** | 否则 A 客户保存一个同名策略就会把 B 客户的策略悄悄划到自己名下，接着在自己的列表里看到它、甚至删掉它。要改归属必须显式调 `set_owner()`。批量删除也限定在当前客户（`delete_strategies(names, owner=...)`），返回数小于请求数即表示有跨客户项被跳过 |
| 2026-10-15 | 界面"空状态"也要能到达，`st.` 组件依赖的变量必须先兜底 | 我给批量管理加客户范围包裹 `if/else` 时，把 `victims = st.multiselect(...)` 留在了 `else` 里，而用它的删除按钮在外层 → **切换到没有策略的新客户名就崩溃**（`cannot access local variable 'victims'`）。这是 AppTest 验证时抓到的。默认值是 `victims = []`、`confirm = False` |
| 2026-10-15 | 行情读取时剔除"零值占位行"（`open/high/low ≤ 0`） | 数据源本身偶尔给这种行（实测新浪对 3/5890 只科创板股票在 2024-11-06 返回 `open=high=low=0`、`close=沿用前收`；已重新拉取确认非本项目管线所致）。`TR` 会变成 `|0-前收|`≈20（正常约 1），把 ATR **放大 1.5~1.75 倍并持续近一个月**，止损位被拉远约 5.7 元。真实停牌日本就没有 K 线，故**剔除**（不是填值）；缓存保留原始行以便追溯。注意引擎第 276 行本就有 `if last["volume"] == 0: continue`，所以这类行不会被买入，但 ATR 污染仍在 |
| 2026-10-15 | 日振幅 > 50% 的行**不是**数据缺陷 | 全库 279 行，集中在 1990 年代（当时无涨跌幅限制）与 2020 年代的新股首日/退市整理期。科创板/创业板在 ±20% 限制下振幅上限**恰好 50%**，超过即必然是无限制日 —— 属合法市场现象，不要"修" |
| 2026-10-15 | 发布原子性靠 **manifest 充当"提交指针"** | 顺序必须是：先上传带版本号的新分卷（不覆盖旧资产）→ 替换附带文件 → **替换 manifest（切换点）** → 清理旧分卷。这样上传中途失败时线上仍是完整旧快照。若反过来先删旧的，中途失败就没有可用快照了 |
| 2026-10-15 | 恢复端支持**多地址回退**（Release → 旧分支） | 发布方式切换期间云端不能拿不到数据；同理发布端保留 `STOCK_PLAN_SNAPSHOT_BACKEND=branch` 回退 |
| 2026-10-15 | GitHub token 解析要**三级回退**（参数 → 环境变量 → secrets 文件） | 原先只认环境变量，而本机计划任务（daily_job.ps1 → publish_snapshot.py）不经过 Streamlit、读不到 `st.secrets`，定时发布会直接报"缺少 token" —— 这是实现时才发现的真缺口 |
| 2026-10-15 | **`py_compile` 通过不等于函数体正确** | 删残留代码时让 `bootstrap_if_needed` 的体只剩一个 docstring（合法语句），编译照样通过，但函数什么都不做。这类问题只能靠人工核对结构或行为测试发现 |
| 2026-10-15 | 候选必须**当天有行情**：已退市/停牌股票不能继续参与打分 | 候选指针单调推进，这类股票的指针会停在最后一根 K 线上，带着**过期数据**占住 top_n 名额；买入环节虽会跳过它，但那天就少买一只。**仅此一条就让同期在市股回测的总收益从 -5.23% 变为 -9.88%（差 4.65pp）** —— 与是否用退市股无关，此前所有回测都受影响 |
| 2026-10-15 | **"退市股占比"不等于幸存者偏差**，偏差是**策略相关**的 | 退市前 60 个交易日的 20 日前瞻收益均值 **-25.19%**、胜率 **16.0%**（其余样本 +1.11%、48.1%），即机制真实且严重。但"占 3.5% 的股票"说明不了偏差大小 —— 取决于策略会不会在**退市前那段**持有它们 |
| 2026-10-15 | 幸存者偏差的正确量化方式（**不要看总收益**） | 实测同一策略 8 组配对子样本：总收益差均值 +4.03pp、标准误 5.36（**\|t\|=0.75，看不出**），因为该策略只持 5 只、总收益跑动标准差高达 **15pp**；但**退市股自身交易盈亏** 8 组全负、均值 **-8,113 元**、标准误 246（**\|t\|=33，极显著**）。结论：偏差必须用"直接成本"或"前瞻收益"这类不依赖选股路径的指标来量化 |
| 2026-10-15 | 缠论的笔必须**首尾相接**，且端点可延伸 | 第一版"一发现异类型分型就立即固定成笔"会生成断开的两笔（如 UP(1→5) 与 DOWN(9→15)，中间 5→9 悬空），同时导致更高的顶无法延伸前一笔。改为「先归约（同类型取更极端）再成笔」，并加用例锁死连续性 |
| 2026-10-15 | 中枢要按**中枢延伸**语义合并，不能每个三笔窗口都算一个 | 真实数据上 26 笔产生 22 个互相重叠的候选中枢。合并后 600519 降到 3 个（000001 16→2、300750 19→2、601318 20→7），区间也变得连贯 |

## 8. 常用命令

```powershell
# 同步依赖
uv sync

# 启动仪表板
uv run streamlit run src/stock_plan/ui/app.py

# 运行 Python 片段
uv run python -c "..."

# 运行测试
uv run pytest tests/

# 增量更新（幂等：数据已最新则跳过且不联网，实测全市场约 23 秒）
uv run python -m stock_plan.data.update_daily

# 全历史重建（修正前复权因子的回溯偏差，约 10 分钟）
uv run python -m stock_plan.data.update_daily --rebuild

# 退市股数据（消除回测幸存者偏差）
uv run python -m stock_plan.data.delisted --universe   # 时点股票池（按月，首次约 10 分钟）
uv run python -m stock_plan.data.delisted --delisted   # 退市股完整历史行情
uv run python -m stock_plan.data.delisted --all        # 两者都补

# 注册/重注册 Windows 计划任务（需管理员；不提权可加 -ExportOnly 只生成 XML）
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_tasks.ps1
```