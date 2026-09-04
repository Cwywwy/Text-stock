# 快照与更新计划任务修复计划

> 创建：2026-09-04 | 作者：Cwywwy + Copilot | 状态：已完成（2026-09-05）
> Python：CPython 3.13.15（由 `uv run python --version` 确认；项目要求 `>=3.13`）

## 目标与范围

修复本机 Windows 计划任务与快照发布流程：

1. 后续快照发布复用已有的本地 `data-snapshot` 分支，避免第二次及以后发布因同名分支失败。
2. 修正文档中的计划任务注册命令，避免 Windows 将含空格的脚本路径拆分为不存在的可执行文件。
3. 将本机自动增量更新策略固定为工作日 08:00 与 20:00 两个时点。

不在范围内：重拉全量历史行情、修改云端恢复协议、修改用户已有的本机行情缓存。

## 模块划分

| 模块 | 职责 | 交互方式 | 状态 |
| --- | --- | --- | --- |
| `data/snapshot.py` | 创建或复用快照发布工作树并强制推送最新快照 | `publish_snapshot.py` 和 21:30 计划任务调用 | 修改 |
| `data/fetcher.py` | 串行调用非线程安全的新浪日线 V8 解码器 | `updater.py` 的并发更新任务调用 | 修改 |
| `tests/test_snapshot.py` | 覆盖已有快照分支的工作树命令选择 | pytest | 修改 |
| `tests/test_fetcher.py` | 覆盖新浪日线调用的并发互斥 | pytest | 新增 |
| `README.md` | 说明两个更新时点及安全的任务计划程序界面配置 | 管理员手动配置 | 修改 |
| `CHANGELOG.md` | 记录本次修复 | 发布说明 | 修改 |

## 阶段与完成标准

| 阶段 | 工作内容 | 完成标准 | 状态 |
| --- | --- | --- | --- |
| 1 | 修复快照分支复用逻辑并补充单元测试 | 已有 `data-snapshot` 分支时不再执行 `checkout --orphan` | 已完成 |
| 2 | 更新自动任务文档 | 仅列出 08:00/20:00，并使用 `cmd /c call` 与界面配置避免路径和引号解析错误 | 已完成 |
| 3 | 运行目标测试、全量测试并写测试报告 | pytest 通过，报告包含任务部署限制 | 已完成 |
| 4 | 修复 AkShare 新浪日线 V8 并发崩溃 | 并发请求在进入 AkShare 前互斥，新增测试通过 | 已完成 |

## 预期产出

- 快照发布可在同一本机仓库重复执行。
- 管理员可按文档正确修复 `Update_0800` 和 `Update_2000`。
- 一份可复核的测试报告。
- Windows 计划任务不会再并发初始化 AkShare 的 MiniRacer V8 解码器。

## 测试策略

- 单元测试：模拟本地 `data-snapshot` 分支存在与不存在时的 git 命令。
- 回归测试：运行现有 pytest 全量套件。
- 任务配置：使用 `schtasks /Query /TN ... /XML` 验证命令字段为 `cmd.exe`，参数含完整带引号脚本路径。

## 风险与限制

- 当前自动化进程无管理员权限，无法直接覆写 `\StockPlan\Update_0800` 和 `\StockPlan\Update_2000`；需用户按任务计划程序界面配置。
- 快照实际发布会打包约 300 MB 行情并执行强制推送，不在自动化测试中运行。
- 现有 `uv.lock` 未提交改动由先前环境建立操作产生，不纳入本次修复。
- 当前修复仍位于 `agents/branch1-data-update` 工作树；计划任务目标目录 `D:\Vscode\stock plan\Text` 必须先纳入这项修复，随后再按 README 重新注册任务。

## 预计文件清单

| 文件 | 操作 |
| --- | --- |
| `src/stock_plan/data/snapshot.py` | 修改 |
| `src/stock_plan/data/fetcher.py` | 修改 |
| `tests/test_snapshot.py` | 修改 |
| `tests/test_fetcher.py` | 新增 |
| `README.md` | 修改 |
| `CHANGELOG.md` | 修改 |
| `tests/reports/2026-09-04-21-xx.report-1.md` | 新增 |
| `.github/plans/2026-09-04-3-bugfix-repair-snapshot-update-task-paths.md` | 新增，完成后归档 |
