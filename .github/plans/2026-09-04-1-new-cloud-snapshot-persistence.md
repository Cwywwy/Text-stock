# Phase 16 计划书：云端数据快照持久化 + 云端轻量模式

> 创建：2026-09-04 | 作者：Cwywwy + Copilot | 状态：✅ 已完成（2026-09-04，commit b07fd1e，已推送；首份快照已发布并验证；\StockPlan\Snapshot_2130 已注册）
> 背景：云端（Streamlit Community Cloud）容器磁盘为临时盘，容器重建后 data/ 归零，用户"拉完一次数据又清空"；且全量拉取 5553 只在云端小内存环境易超限。用户确认方案 1+2 组合。

## 一、目标与范围

1. **方案 1（数据快照外置）**：行情数据（data/processed/bars，约 298MB / 5553 个 parquet）打包分卷快照发布到 GitHub 同仓库 `data-snapshot` 孤儿分支；云端应用启动时检测本地无数据则自动下载→校验→解压，秒级~分钟级恢复全部历史数据；本机提供手动/计划发布入口保持快照更新。
2. **方案 2（云端轻量模式）**：云端环境下降低更新任务资源占用（workers 8→3）、全量拉取默认 5 年改为 1 年；UI 显示当前运行模式徽章。

不在范围内：云端向 GitHub 回传快照（容器无 git 推送凭据且占用资源；快照以本机为权威源）；策略库（strategies.db）云端持久化（量小，重建后用户可重建，后续需要再做）。

## 二、模块划分及职责

| 模块 | 职责 | 状态 |
|------|------|------|
| `data/snapshot.py`（新增） | 快照打包（zip 分卷 <90MB + manifest.json 含 sha256）、git worktree 发布到 data-snapshot 分支、下载恢复（校验后解压）、云端模式检测 | 新增 |
| `scripts/publish_snapshot.py`（新增） | 本机 CLI：发布快照（供计划任务/手动） | 新增 |
| `scripts/publish_snapshot.cmd`（新增） | 计划任务包装（对齐 run_update.cmd 模式） | 新增 |
| `ui/app.py`（修改） | 启动时调用 bootstrap：空数据 + 快照可达 → st.status 展示下载进度 | 修改 |
| `ui/views/update_center.py`（修改） | 模式徽章、云端说明、手动「从快照恢复」按钮、_start_task 云端轻量参数 | 修改 |
| `data/update_daily.py`（不修改） | workers/days 由 UI 启动参数控制，脚本本身不动 | 不动 |
| `tests/test_snapshot.py`（新增） | 分卷/清单/校验/文件名纯函数测试 | 新增 |

交互：app 启动 → snapshot.bootstrap_if_needed()（有数据跳过）→ 各页正常；更新中心 _start_task → update_daily 子进程（云端自动降参）。

## 三、阶段拆分与完成标准

| 阶段 | 内容 | 完成标准 |
|------|------|---------|
| P1 | snapshot.py 打包/清单/发布/恢复核心 + publish 脚本 | 本地 build→(模拟恢复)校验通过；纯函数单测过 |
| P2 | app.py 启动恢复 + 更新中心 UI（徽章/手动恢复/轻量参数） | 本地模拟空数据触发恢复成功；UI 正常渲染 |
| P3 | 首份快照发布到 GitHub（~300MB 分 4 卷）+ 注册 schtasks 21:30 | data-snapshot 分支可见 manifest + 4 卷；raw URL 可下载 |
| P4 | pytest + 文档（README/plan/INSTRUCTION/CHANGELOG）+ commit push | 22+ 用例全过；文档同步；推送完成 |

## 四、预期产出

新增 4 文件、修改 2 文件、GitHub 新增 data-snapshot 分支、Windows 新增 1 计划任务。

## 五、测试策略

- 单测：分卷大小计算、manifest 结构、sha256 校验、part 文件名/URL、恢复校验失败拒绝落盘（临时目录小文件）
- 集成：本机删除(备份)后模拟恢复 → bars 数量一致；真实 raw URL 下载 1 卷校验
- pytest 全量回归

## 六、风险与限制

- 首次 push ~300MB 上传耗时取决于本机带宽
- GitHub 单文件 <100MB 硬限制 → 分卷 90MB；仓库总量 <1GB 软限制 → 快照整分支 force push 单 commit，不占历史
- 云端首次恢复需下载 ~300MB（1~3 分钟），每容器重建一次
- 云端容器内存 2.7GB：恢复用流式下载+解压，不整包驻留内存
- raw.githubusercontent.com 在部分网络环境可能需代理（云端在美国，无碍；本机已验证可达性后确认）

## 七、预计文件清单

- 新增：`src/stock_plan/data/snapshot.py`、`scripts/publish_snapshot.py`、`scripts/publish_snapshot.cmd`、`tests/test_snapshot.py`、`.github/plans/2026-09-04-1-new-cloud-snapshot-persistence.md`
- 修改：`src/stock_plan/ui/app.py`、`src/stock_plan/ui/views/update_center.py`、`README.md`、`plan.md`、`INSTRUCTION.md`、`CHANGELOG.md`（新建）
