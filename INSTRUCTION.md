# 开发 Instruction（开发指南）

> 本文件是项目的开发指导文档，**每完成一个 Phase 必须同步更新**。
> 最后更新：2026-09-03（Phase 11 / V3 完成）

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
├── src/stock_plan/
│   ├── data/               # 数据获取与缓存
│   ├── factors/            # 因子计算
│   ├── strategy/           # 策略引擎
│   ├── backtest/           # 回测引擎
│   ├── signal/             # 盘前信号生成
│   ├── simulator/          # 模拟交易
│   ├── llm/                # LLM Agent（V3 已接入，配置来自 .env）
│   └── ui/
│       └── views/          # Streamlit 页面（9 页平铺导航）
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
```