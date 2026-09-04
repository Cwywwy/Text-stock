# 变更日志（Changelog）

> 按日期分组，从新到旧排列。

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
