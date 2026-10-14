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
- **暂时需求 5c 接入**：`load_market_maps(include_delisted=...)` 可把退市股纳入回测样本
  （默认关闭）；回测页新增开关。前置修复：引擎原先会在"持仓期间行情终结"时让持仓
  **凭空消失**（估值计 0、收尾静默丢弃），已改为按最后可成交价强制平仓并归因 ——
  该 bug 不需要退市股就能触发，任何被持有的停牌股都会中招（`0f3a9a4`、`16da62a`）
- **暂时需求 6（前半）— 市场阶段因子与分阶段策略建议**（`416ed6f`）
  - 新增 `factors/regime.py`：**等权收益指数**（日收益等权平均后累乘，与价位和成分变动无关）、
    逐日/最新阶段判定（`close > MA20 > MA60` 且 MA60 上行 → 上升；反向 → 下降；其余 → 震荡）、
    波动率历史分位、通道位置，以及阶段 → 策略偏好 + 建议仓位的 playbook
  - **顺带修掉引擎的大盘代理缺陷**：原为 `closes.mean(axis=1)`（对原始股价取平均），
    会被高价股主导、且成分股缺 K 线时指数跳空，导致"大盘跌破 MA20"可能由成分变动而非
    行情触发。`market_timing=True` 是默认配置，故该缺陷一直在影响回测。改为等权指数后，
    实测 297 只候选的指数 1.0 → 1.4288，39% 的交易日低于 MA20（择时不再空转）
  - 「今日信号」页顶部新增市场阶段面板（四个指标 + 判定理由 + 策略偏好 + 等权指数图）
  - 新增 `tests/test_regime.py` 10 个用例；其中一条暴露出我自己写的**平凡断言**
    （`min_contributors` 默认 20 而用例只有 1 只股票 → 指数恒为 1.0），已修正为非平凡
- **暂时需求 6（后半）— 缠论结构识别**（`c37b288`）
  - 新增 `factors/chan.py`：K 线包含处理、分型（要求中间那根的高点与低点**都是**最高/最低）、
    笔（异类型分型 + 间距 ≥ 4，且**必须首尾相接**）、笔中枢（连续三笔重叠区间）、三买
  - **明确未实现**并在代码与 UI 里声明：线段、走势类型、背驰，因此也**不提供一买/二买**。
    定位是结构识别的辅助特征，不是完整缠论交易系统
  - 测试过程中抓到两个**真问题**：① 第一版"一发现异类型分型就立即成笔"会产生**断开的两笔**
    （中间悬空）且无法延伸端点，改为「先归约再成笔」；② 真实数据上 26 笔产生了 **22 个**
    冗余中枢，加入**中枢延伸**合并后降到 **3 个**（000001 16→2、300750 19→2、601318 20→7）
  - 「持仓诊断」页新增可折叠的缠论结构区块（笔/中枢/分型/三买 + 当前价相对中枢上下沿的位置）
  - `tests/test_chan.py` 24 个用例，全部基于**可手算**的人工折线行情与手工构造分型
- **快照分发改 GitHub Releases**（`8fa4e2e`）
  - 原先 force push 到 `data-snapshot` 孤儿分支：每天往仓库塞约 300MB blob（GitHub 侧回收
    不可控），且 `raw.githubusercontent.com` 有 CDN 缓存，force push 后可能仍取到旧快照
  - 新增 `publish_to_release()`：**用 manifest 充当"提交指针"** —— 先以带版本号的名字上传新分卷
    （不覆盖旧资产），全部就位后才替换 manifest，最后清理被取代的旧分卷。上传中途失败时
    线上仍是完整旧快照；切换成功后 Release 只保留一份数据
  - 恢复端新增 `restore_with_fallback()`：按 Release → 旧分支 顺序尝试，切换期不影响云端；
    发布端可用 `STOCK_PLAN_SNAPSHOT_BACKEND=branch` 回退
  - 补上 `resolve_token()`：显式参数 → 环境变量 → `.streamlit/secrets.toml`。
    原实现只认环境变量，而本机计划任务不经过 Streamlit、读不到 `st.secrets`，
    定时发布会直接报"缺少 token"
  - 只读飞行前检查：token 有效（认证为 Cwywwy）、仓库 public、`permissions.push=True`（可发布）
  - `publish_snapshot.py --build-only` 实测：5776 只 → **351MB / 4 卷**
- **修复"僵尸候选"占位（影响所有回测）**（`ea768f2`）
  - 量化幸存者偏差时先用同一策略跑"含退市股 vs 不含"，得到 -10.6pp，我一度当成幸存者偏差。
    但两处证据对不上：退出原因里**没有**"退市/停牌"；190 只退市股均匀分布在 2022~2026，
    却只成交 5 笔且全在回测首日
  - 根因：候选指针单调推进，已退市/长期停牌股票的指针**停在最后一根 K 线上**，带着**过期数据**
    继续参与打分并占住 top_n 名额；买入环节虽会跳过它，但那天就少买一只
  - 修法：候选必须当天有行情。**仅这一条就让同期"在市股"回测的总收益从 -5.23% 变为 -9.88%**
    （差 4.65pp）—— 也就是说此前所有回测都受此影响，与是否用退市股无关
  - 修正后再量化：同一策略、同一区间，"含退市股 vs 不含"的总收益差只有 **-0.02pp**
    （-9.88% vs -9.90%），交易笔数 1008 vs 1011
  - **结论修正**："退市股占 3.5%"本身**不等于**偏差。偏差取决于策略会不会挑中它们，
    因此是**策略相关**的；以后必须用"同一策略 + include_delisted 开关"实测
- **暂时需求 4（部分）— 策略分组标签 + 批量删除**（`ea9e22b`）
  - `strategies` 表新增 `tags` 列，老库轻量迁移不丢数据；按标签筛选用
    `',' || tags || ',' LIKE '%,tag,%'` 保证**精确匹配**（「客户-张三」不命中「客户-张三丰」）
  - 新增 `normalize_tags` / `set_tags` / `all_tags` / `delete_strategies`
  - 策略管理页新增标签筛选 + 「🧹 批量管理」（总览表格 / 标签编辑 / 多选+二次确认批量删除）
  - 修一个 UX 缺陷：`st.success(...)` 紧跟 `st.rerun()` 导致**提示被丢弃、用户看不到确认**
    （AppTest 里 success 列表为空暴露出来的），改为 session_state flash，共 3 处
  - **未做**：分客户保存。三种方案与工作量已写入
    `.github/plans/2026-10-15-3-new-strategy-grouping-and-per-customer.md`，
    等确认后再实施；注意其中 owner 方案只是便于分类，**不是安全边界**
- **审计：未来函数与路径依赖**（见 `.github/plans/2026-10-15-4-audit-path-dependence-and-lookahead.md`）
  - ✅ 干净：`factors/technical.py` 的指标全为因果（无未来函数）；
    `signal/generator.py` 盘前取最后一根已完成 K 线正确；`filter.py` 的 `MIN_BARS=60`
    保证指标预热期不会流入打分
  - ⚠️ **发现 1（未修，已加代码内警告）**：`filter_universe` 用 `stock_list["is_st"]`
    （= **今天**的状态）排除 ST，而引擎开跑前一次性建池 → **用 2026 年的 ST 状态决定
    2022 年能不能买**。修它需要 ST 时点历史（现无此数据源），故在 `factors/filter.py`
    模块文档写入醒目警告
  - ⚠️ **发现 2（方法论，更重要）**：候选池只增 3.75% 就让总收益差 **+20.97pp**，
    而成交股票重合度仅 **Jaccard 0.226**（换掉 77%）、被测试的 ST 股自己只成交 8 笔。
    引擎经确认是确定性的（同输入两次差 0.0000pp）。**结论：单次 A/B 的差异由路径依赖
    主导，不能当成效应量。** 这推翻了我上一轮"幸存者偏差 −0.02pp ≈ 0"的说法 ——
    那只说明"噪声范围内看不出差异"
  - 🔧 **发现 3（已修）**：`_rsi` 的 `fillna(50)` 把"只涨不跌"的极限值 100 填成中性 50，
    使连续涨停的最强动量反而拿到 `trend_score` 的「50≤rsi≤70 → +20 分」奖励。
    实测影响面 590/7,472,101 股票-日（0.0079%）。修正后新增
    `tests/test_technical_edges.py` 6 个用例，反向验证（stash 掉修正）→ 2 failed
  - ⚠️ **发现 4（已加代码内警告，未改行为）**：回测是**同柱决策 + 同柱成交** ——
    用 `ts` 当天收盘算出的因子行打分，又按 `ts` 当天收盘价成交。
    而实盘路径（`generate_signals` + `paper.py`）是"盘前用**昨收**出信号、按今日收盘成交"，
    **两者成交口径不同，收益不能直接对比**。
    实测方向（路径无关，1008 笔逐笔）：次日开盘 / 当日收盘 − 1 的均值 **−0.103%**
    （中位数 0.000%，为正比例仅 34.2%）→ 改成次日开盘成交反而**更便宜**，
    所以当前约定对买入价偏保守、**没有虚高收益**。
    **又一次"机制存在但方向与预期相反"**。未测的是决策侧未来信息值多少
    （需把因子行滞后一根，但那会改变选股、落入路径噪声）
  - ✅ 本轮其他检查干净：`strategy/scorer.py`（纯加权无跨期统计量）、
    `simulator/paper.py`（盘前信号、无未来函数）、引擎因子预计算（全 `rolling`/`ewm` 因果）
  - 📌 待办（非缺陷）：`paper.py` 印花税用千一，而 A 股 2023-08-28 起已减半为 0.05%；
    偏保守、方向安全，若要贴近现实可按日期分段
- **审计发现 5（已修）：回测与实盘的因子列不一致，规则被静默跳过**
  - 两条路径各自内联构造因子行，列集**双向不一致**：
    `mom_ret`/`ma5/7/10/30` 只有回测有（实盘的动量规则被静默跳过）；
    `avg_amount20`/`macd`/`macd_signal` 只有实盘有（回测的流动性/MACD 规则被静默跳过）
  - 因为 `CustomStrategy.score` 用 `if "列" in df.columns` 守卫，**缺列不报错、直接跳过规则** ——
    用户看到的现象是"同一个策略在两个页面选出不同的股票"
  - 修法：统一两条路径的因子列集；**`avg_amount20` 在引擎里必须用 `rolling(20)`**，
    不能用 `bars["amount"].tail(20)`（后者取序列末尾，在逐日回测里就是未来数据）
  - 新增 `tests/test_factor_parity.py` 4 个用例锁死 schema 一致；
    **反向验证：stash 掉修正 → 4 failed，恢复 → 4 passed**
  - 过程中我自己写错三版用例（不可判别 / 用错 API / 测错方向），均已修正并记录
- **复审（2026-10-15 续）：未发现新 bug，审计出现收敛迹象**
  - 补上**数值级**一致性用例 `test_factor_values_match_between_paths`：
    `mom_ret`（`iloc[p-20]` vs `iloc[-21]`）、`high20_ratio`（`rolling(20).max()` vs
    `iloc[-20:].max()`）、`avg_amount20`（`rolling(20).mean()` vs `tail(20).mean()`）
    在同一根 K 线上窗口完全相同、数值一致 ✓
    `tests/test_factor_parity.py` 现 5 个用例，反向验证（`git checkout` 取回修正前版本）
    → **5 failed**，恢复 → **5 passed**
  - 扫描 7 个策略文件引用的因子列：**全部落在 21 列统一 schema 内**，
    不存在"读了 schema 外的列 → 规则在两条路径上都不生效"这类更隐蔽的问题
  - 确认动态列名 `ma{周期}` 的取值受 `_MA_CHOICES = (0,5,7,10,20,30,60)` 约束，
    且两条 LLM 路径都经过 `normalize_config`，schema 覆盖全部合法值 ✓
- 验证：`compileall` 全包 0 错误；`pytest` **135 passed**；12 页 AppTest 全绿

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
