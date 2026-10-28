# 新增需求：把系统部署到国内云服务器（方案 C）

> 状态：**已实施（2026-10-29）**——Phase 1/2/3 与 Phase 6 本机部分完成；Phase 4（本机推送脚本）按拍板取消；
> Phase 5 的顶级 README/CHANGELOG 因并发改动推迟。剩余为真机验收（§12 后四条）。
> 日期：2026-10-29
> 分支：`feature/deploy-china-server`（从 `main` = 7315819 新建）。
> 用户 2026-10-29 确认方向：不再折腾 Pages 类静态托管，直接用国内云服务器（先用厂商新用户免费试用跑 1–3 个月）。

---

## 0. 本轮范围（用户已确认的前提）

| # | 维度 | 用户决定 |
|---|---|---|
| ① | 目标 | 让 Streamlit 选股系统有国内公网地址、打开比现在快 |
| ② | 受众 | 公开分享：任何拿到链接的人都能用，不做账号体系 |
| ③ | 预算 | 0 元起步，用云厂商新用户免费试用；到期再议 |
| ④ | 形态 | 方案 C：国内云服务器（不是 Pages、不是家里电脑、不是内网穿透） |
| ⑤ | 鉴权 | **不做鉴权**。用户原话：「目前用的都是免费额度，随便烧，反正也不打算充钱哈哈」→ 接受「任何人可触发 LLM 调用」的后果 |
| ⑥ | 数据来源 | **服务器每天拉本机发布的快照**（本机仍是唯一权威数据源） |
| ⑦ | 现状基线 | Streamlit Cloud 实例：`https://cwywwy-text-stock-srcstock-planuiapp-1-sgdjb6.streamlit.app/` |

**已排除、不再讨论**（2026-10-28 已说明并获接受）：

- Gitee Pages：**2024 年 5 月已下线**，官方原话「由于不可抗原因，Pages 服务已经下线，暂无具体计划再次上线」。
- GitHub Pages / AtomGit Pages / Cloudflare Pages / 艾可秀（AxureShow）：只托管静态文件。本项目要跑 Python 进程、
  读 350MB 行情、写 SQLite —— 不是配置问题，是能力不匹配。
- 家里电脑直接对外：本机 IPv4 `192.168.5.2`、网关 `192.168.5.1`，**无公网 IPv4、无 IPv6 全局地址**；
  「开机≠别人能访问」，还差公网入口、端口映射、常在线、上行带宽四件事。

**本轮不做**：用户体系/登录、业务逻辑改动、数据层算法改动、多机集群、HTTPS 证书（用 `IP:8501` 直连）。

---

## 1. 关键决策：服务器是「访客实例」，不是「第二台维护机」

用户选了「服务器每天拉本机快照」，这条决定了整个架构。**服务器只负责「托管 + 展示」，不承担数据生产**：

- 服务器设置 `STOCK_PLAN_SERVICE=1`（配合 `STOCK_PLAN_CLOUD=1`）→ 代码走「常驻访客实例」路径：
  数据更新页是访客排版（只有「同步最新快照」按钮），且**不会**在启动时拉起
  `subprocess.Popen(scripts/publish_snapshot.py)`（该判断已从 `not is_cloud()` 改为 `local_publish_enabled()`）。
- 这与**现在的 Streamlit Cloud 实例的运行状态完全一致**：同样只有 `bars/*.parquet`，公告/情绪/分类/估值表由页面按需实时抓取。
  也就是说，服务器上的功能就等于用户现在云端看到的那些页面，**不新增也不减少功能**。
- 好处：服务器不需要跑 `update_daily`/`fill_pe_pb`/`announcements`/`classification` 这一整套（那是本机的活），
  2C4G 甚至 2C2G 都能扛；也不存在「两套数据各自漂移」的问题。

数据快照的实际内容（`src/stock_plan/data/snapshot.py:build_snapshot`）：

- **已提交的 `main` 口径（v1）**：只有 `data/processed/bars/*.parquet`，`ZIP_STORED` 打包后按 90MB 分卷，
  附 `manifest.json`（`created_at`、每卷 sha256、`bars_count`）。公告、情绪、分类、估值、`meta.db` 不在快照里。
- **工作区里还有一份未提交的「快照 v2」改动（2026-10-28，非本计划产出）**：`SNAPSHOT_FORMAT = 2`，
  zip 内增加 `classify/`、`market/`、`universe/`、`stock_basic.parquet`、`trade_calendar.csv`、`announcements.db`，
  恢复时按分组原子替换；同时改了 `update_center.py`、`tests/test_snapshot.py`、`README`。
  **本计划不碰这些文件**，但结论要分两种口径写：

| 快照版本 | 服务器拉一次能拿到什么 | 对本计划的影响 |
|---|---|---|
| v1（当前 main） | 只有行情 bars | 服务器功能 = 现在 Streamlit Cloud 的样子（公告/情绪/分类靠页面实时抓） |
| v2（工作区未提交） | 行情 + 分类/情绪/行业/基础表/公告库 | 更好：服务器拉一次即可全功能，`bootstrap.sh` 不需要额外补数据 |

→ 无论 v1 还是 v2，服务器端脚本都只需「拉快照 + 重启服务」，不需要改动；差异只在验收清单里核对的数据种类。
用户已拍板：**v2 那份未提交改动先不管**——本计划既不合并也不修改它，服务器首次运行按当时工作区的口径为准。

### 1.1 快照怎么到服务器（已拍板：只用 B）

| 方案 | 链路 | 状态 |
|---|---|---|
| A. 本机直推 | 本机打包 → `scp/rsync` → 服务器 `data/processed/bars` | **已砍**（2026-10-29 用户拍板） |
| **B. 服务器自己拉** | 服务器 → GitHub Releases `data-snapshot` 标签 | **采用**，实现为 `deploy/pull_snapshot.py` + systemd timer |

选 B 的代价（已知并接受）：国内服务器拉 GitHub 可能慢或失败，350MB 在 5Mbps 下约 10 分钟；
失败时页面继续用旧数据，不会假报新日期。手册给了三种绕法（代理、镜像、从本机 `scp data/processed`），
但没有本机侧推送脚本。

### 1.2 数据更新后怎么让页面看到

Streamlit 进程会把行情读进内存/缓存，磁盘数据换了页面不一定重读。方案：**拉取成功后滚动重启服务**
（`systemctl try-restart stock-plan`），代价是每次约 10–30 秒不可用，安排在 17:00 前后无人使用时段。

---

## 2. 目标架构

```
 本机（权威数据源，Windows）                       云服务器（Ubuntu 24.04，2C4G，≥5Mbps）
 ───────────────────────────                       ─────────────────────────────────────────────
 16:30 计划任务                                     systemd: stock-plan.service
   daily_job.ps1 ──▶ publish_snapshot.py             · streamlit run src/stock_plan/ui/app.py
       └─▶ GitHub Releases                           · 0.0.0.0:8501，Restart=always
            (data-snapshot 标签)                      · STOCK_PLAN_SERVICE=1、fileWatcher=none
                                                      · 环境变量见 deploy/stock-plan.service
                                                    systemd: stock-plan-snapshot.timer
 服务器 ──HTTPS──▶ GitHub Releases ────────────────▶   每交易日 17:10 拉一次 →
                                                      成功且数据有变则重启主服务
                                                    .streamlit/secrets.toml（LLM key，不进 git）
 用户浏览器 ──HTTP──▶ http://<公网IP>:8501
```

**为什么用 systemd + venv（Docker 方案已砍）**：

- 国内服务器拉 Docker Hub 常要配镜像加速器，多一个失败点；`python:3.13-slim` 基础镜像也不算小。
- venv 方案更新只需 `git pull && systemctl restart stock-plan`，日志走 `journalctl -u stock-plan`。
- Docker 的一致性收益在这个规模（单进程 + 一个定时任务）不明显，2026-10-29 用户拍板砍掉。

---

## 3. 模块划分

| 模块 | 职责 | 交互方式 | 状态 |
|---|---|---|---|
| `deploy/bootstrap.sh` | 一键引导：系统依赖 → 时区 → 建 `stockplan` 用户 → uv → Python 3.13 → venv → 装 `requirements.txt` → 建 `data/` 目录 → 装 systemd → 首次拉快照 | 读 `requirements.txt`；调用 `pull_snapshot.py` | 新增（已实现） |
| `deploy/pull_snapshot.py` | 服务器侧拉快照（唯一链路 B）：冷却判断 → 分布式锁 → `restore_with_fallback` → 写状态 → 可选重启；带 `--force`、`--dry-run`、`--max-age-days` | 被 `stock-plan-snapshot.timer` 调用；依赖标准库 + `stock_plan.data.snapshot` | 新增（已实现） |
| `deploy/stock-plan.service` | Streamlit 常驻：`0.0.0.0:8501`、`Restart=always`、`PYTHONPATH`、`STOCK_PLAN_SERVICE=1`、`STOCK_PLAN_CLOUD=1` | 读 `.streamlit/secrets.toml` | 新增（已实现） |
| `deploy/stock-plan-snapshot.service` + `.timer` | 每交易日 **17:10** 跑 `pull_snapshot.py --restart-service` | systemd timer | 新增（已实现） |
| `deploy/secrets.toml.example` | LLM 三项 + `STOCK_PLAN_CLOUD` / `STOCK_PLAN_SERVICE` / `STOCK_PLAN_REMOTE_SYNC` 模板 | 拷成 `.streamlit/secrets.toml` | 新增（已实现） |
| `deploy/README.md` | 从选机型到验收、排障、续费/退租的完整手册 | 用户操作指引 | 新增（已实现） |
| `src/stock_plan/ui/startup_flags.py` | 纯函数开关：`is_service_mode()`、`remote_strategy_sync_enabled()`、`local_publish_enabled()`、`describe()`；不 import streamlit | 被 `app.py` 调用 | 新增（已实现） |
| `src/stock_plan/ui/app.py` | 接线两处：共享策略同步、本机自动发布，改由上面两个开关决定 | — | 修改（已实现） |
| `tests/test_startup_flags.py` | 开关矩阵单测（未设/`1`/`0`/空串/大小写/拼错值） | — | 新增（已实现，34 例） |
| `tests/test_deploy_assets.py` | 静态校验部署物料：unit 关键字段、脚本参数、文档口径、无 CRLF | — | 新增（已实现，47 例） |
| `README.md` | 新增「国内云服务器部署（方案 C）」章节 | — | **本轮未做**：该文件正被并发的「快照 v2」改动占用，等其合并后补 |
| `CHANGELOG.md` | 2026-10-29 条目 | — | **本轮未做**：同上 |

> 方案 A 的 `scripts/push_snapshot_to_server.ps1` 与 `docker/` 三件套按 2026-10-29 拍板**不做**，已从本表移除。

---

## 4. 阶段拆分与完成标准

### Phase 1 — 计划书评审（✅ 完成 2026-10-29）

- 产出：本文件；§11 的三个点位已由用户拍板（B / 砍 Docker / 不管 v2 改动）。

### Phase 2 — 启动开关（✅ 完成 2026-10-29）

- 新增 `startup_flags.py`；`app.py` 接线两处（共享策略同步、本机自动发布）。
- 默认值保持现状（不设环境变量时行为不变）。
- 完成标准：`tests/test_startup_flags.py` 34 例通过；`pytest` 全量 **445 passed**（不低于基线 354）。
- 实际实现与原计划的差异：环境变量名定为 `STOCK_PLAN_SERVICE` + `STOCK_PLAN_REMOTE_SYNC`
  （原计划写的是 `STOCK_PLAN_SYNC_STRATEGIES`），并额外用 `local_publish_enabled()` 关掉了服务器上的
  `publish_snapshot.py --pending-only` 子进程——它在本机是「发快照」，在访客实例上毫无意义。

### Phase 3 — 部署物料（✅ 完成 2026-10-29，未真机执行）

- `deploy/` 七个文件（`bootstrap.sh`、`pull_snapshot.py`、两个 service、一个 timer、`secrets.toml.example`、`README.md`）；
  脚本 `set -euo pipefail`、幂等、日志、退出码。
- `bootstrap.sh`：建系统用户、`uv` 装 Python 3.13、按 `requirements.txt` 建 venv、建数据目录、装 systemd、
  首次拉快照，**绝不**把 secrets 写进 git（secrets 由用户手工拷模板）。
- 完成标准：`tests/test_deploy_assets.py` 47 例通过；本机无 bash/WSL，只做结构级检查（见 §6），
  真正的执行验证在服务器首次运行。

### Phase 4 — 本机侧推送脚本（❌ 已砍，2026-10-29 拍板）

- 用户选 B（服务器自己拉 GitHub Releases），本机侧不再需要推送脚本；相应文件不创建。

### Phase 5 — 文档（⚠️ 部分完成）

- `deploy/README.md`：已完成（10 节运维手册，命令与脚本一一对应）。
- 顶级 `README.md` 与 `CHANGELOG.md`：**本轮未改**——这两个文件正被并发的「快照 v2」工作修改，
  此刻追加会把他人未提交内容混入本次提交。等 v2 合并后补两处链接与条目（已记入遗留项）。

### Phase 6 — 验证交付（✅ 完成本机部分）

- 全量 `pytest -q` → **445 passed, 0 failed, 13.67 s**；报告 `tests/reports/2026-10-29-00-26.report-1.md`。
- 分阶段 commit；用户侧人工清单见 §10。
- 服务器冒烟（§12 后四条）等待用户提供机器，尚未执行。

---

## 5. 环境变量与运行参数口径（服务器侧）

| 变量 / 参数 | 服务器取值 | 作用 | 不设时的行为 |
|---|---|---|---|
| `STOCK_PLAN_SERVICE` | `1` | 声明「这是常驻服务器实例」：跳过共享策略同步、禁止本机自动发布 | 非服务器形态（本机行为不变） |
| `STOCK_PLAN_REMOTE_SYNC` | `0` | 显式关掉 `raw.githubusercontent.com` 同步（避免 30s 白等）；设为 `1` 可强制打开 | 服务器形态下默认关 |
| `STOCK_PLAN_CLOUD` | `1` | 访客模式：访客排版、不再尝试全市场更新 | 按环境特征自动判定 |
| `DATA_SNAPSHOT_REPO` | 空（用默认 `Cwywwy/Text-stock`） | 快照仓库 | 默认值 |
| `TZ` / 系统时区 | `Asia/Shanghai` | 服务与日志时区（`bootstrap.sh` 用 `timedatectl` 设置，不是环境变量） | 服务器默认 UTC |
| `PYTHONPATH` | `/opt/stock-plan/src` | 保证 `PROJECT_ROOT` 落在项目根（不能 `pip install .`） | 无 |
| `--server.address` | `0.0.0.0` | 对外监听 | `localhost` |
| `--server.port` | `8501` | 非 80 端口，绕开备案要求 | `8501` |
| `--server.headless` | `true` | 不弹浏览器提示 | `false` |
| `--server.fileWatcherType` | `none` | 省 inotify 与内存 | `auto` |
| `--browser.gatherUsageStats` | `false` | 不连 Streamlit 遥测 | `true` |
| `.streamlit/secrets.toml` | `LLM_BASE_URL` / `LLM_MODEL` / `LLM_API_KEY` | LLM 功能 | 无 key 时 LLM 页降级 |
| `OMP/OPENBLAS/MKL_NUM_THREADS` | `2` | 限制 BLAS 线程，2 核机器上防抢 CPU | 依 numpy 默认 |

---

## 6. 测试策略

| 层 | 方法 | 说明 |
|---|---|---|
| 单元测试 | `tests/test_startup_flags.py`：默认、`0`/`1`/空串/大小写/空白（正/反/边界） | 纯函数，无网络 |
| 单元测试 | `tests/test_deploy_assets.py`：unit 必有 `ExecStart`/`Restart`/`User`/`PYTHONPATH`/三个开关；`pull_snapshot.py` 参数齐全；`secrets.toml.example` 含三个开关；文档端口/路径与脚本一致 | 静态断言，防「改了脚本忘了改文档」 |
| 回归 | 全量 `pytest`（基线 354 passed） | **实测 445 passed**（含并发 v2 用例） |
| 脚本静态检查 | 无 bash/shellcheck/WSL 可用 → Python 断言 shebang、`set -euo pipefail`、无 `\r\n`、关键命令顺序 | 真执行只能上服务器 |
| 服务器冒烟 | 手册 10 条勾选清单（服务状态、端口、首页 200、快照时间、日志无 ERROR、重启自恢复、timer 下次触发时间） | 用户执行，结果回填 |
| 数据缩减说明 | 无需缩减：部署测试不依赖真实行情 | — |

---

## 7. 机型与厂商（2026-10 调研，含来源）

| 厂商 | 免费试用档 | 关键限制 | 结论 |
|---|---|---|---|
| **腾讯云**（首选） | 轻量 **2 核 4G / 5Mbps / 60GB 盘 / 500GB 月流量**，**1 个月** | 个人或企业实名 + 产品新用户；到期需手动续费（不自动扣） | 规格正合本方案，[cloud.tencent.com/act/pro/free](https://cloud.tencent.com/act/pro/free) |
| 阿里云 | 轻量免费档是 **2vCPU + 1GiB**（1 个月，200Mbps 峰值） | 内存 1GiB 跑不动 pandas 全市场规模；要 2C4G 得走 ECS「300 元额度 / 3 个月」 | 免费轻量档**不适合**，[free.aliyun.com](https://free.aliyun.com/) |
| 华为云 | Flexus L 免费档，规格/时长**未证实** | 实名 + V0 + 同类产品未购 | 备选，需用户在活动页确认 |
| 京东云 | 官方只证实「15 天」 | 需实名 | 备选，时长偏短 |

付费参考（到期后）：腾讯云 2C4G 轻量约 **100 元/月**（7M/1000GB，年付 1020 元）、2C2G 约 45 元/月。

**备案**：阿里云/腾讯云官方口径一致 —— **不使用域名、只用「公网 IP + 非 80 端口」不需要备案**
（腾讯云原文：「当您不使用域名解析指向服务器时，不需要进行备案」「仅通过服务器公网 IP 直接访问测试，可暂不备案」，
[cloud.tencent.com/document/product/243/19630](https://cloud.tencent.com/document/product/243/19630)）；
但只要解析了未备案域名，**任何端口都会被监测到并阻断**，所以本方案不碰域名。

---

## 8. 风险与限制（诚实披露）

1. **带宽才是体验上限**。腾讯云免费档 5Mbps ≈ 625KB/s，Streamlit 前端 JS 包约 2MB → 首屏约 3–5 秒。
   比现在跨境 20–40 秒好一个量级，但**不等于秒开**；想更快要么升带宽（按 Mbps 加价），要么后面做前端懒加载优化（不在本轮）。
2. **内存**：只有 bars 的访客模式下 2G 勉强够、4G 舒服；脚本检测到内存 <2G 会告警并加 swap。
3. **公开无鉴权**：任何人可访问、可点「同步最新快照」、可触发 LLM 调用。用户已明确接受（免费额度）。
   若日后想收回，加一层 Caddy/Nginx Basic Auth 即可（手册留一行命令，不单独做组件）。
4. **快照链路是单点**：本机不发布 → 服务器数据不新。手册给「数据落伍怎么手动补」步骤；
   拉取失败时页面会显示数据日期，不会假装是新的。
5. **GitHub 在国内服务器常不可达（已选 B 的已知代价）**：拉取失败不影响旧数据可用，只会让数据停在上次成功的时间；
   手册给了三种绕法（`HTTPS_PROXY`、镜像站、从本机 `scp data/processed`），但没有常备的本机推送脚本。
6. **免费试用 1 个月后到期**：转付费（约 100 元/月）或关停迁移；手册给迁移命令（打包 `data/` + `secrets.toml`）。
7. **服务器时区默认 UTC**：`bootstrap.sh` 用 `timedatectl set-timezone Asia/Shanghai` 固定，不是 systemd 环境变量；
   项目本身已用 `stock_plan.data.clock` 固定北京时间，不依赖容器时区。
8. **本机无 Docker、无 WSL 发行版、无 bash**：脚本无法在本机端到端执行，只能静态检查 + 服务器首跑验证。
9. **厂商政策随时变**（额度/时长/实名要求），实施前以用户实际开通页面为准，文档不写死价格。
10. **滚动重启有 10–30 秒空窗**，安排在 17:10 前后，若你恰好此时访问会看到重连提示。
11. **快速连续刷新 cheapest**：`pull_snapshot.py` 有 6 小时冷却 + 文件锁，但 GitHub 拉不动时它会重试 3 次（约 1 分钟），
    期间只占网络不占 CPU，不影响页面使用。

---

## 9. 文件清单

**新增（11）**

```
.github/plans/2026-10-29-1-new-deploy-on-china-cloud-server.md   本计划书
deploy/bootstrap.sh
deploy/pull_snapshot.py
deploy/stock-plan.service
deploy/stock-plan-snapshot.service
deploy/stock-plan-snapshot.timer
deploy/secrets.toml.example
deploy/README.md
src/stock_plan/ui/startup_flags.py
tests/test_startup_flags.py
tests/test_deploy_assets.py
```

（另：`tests/reports/2026-10-29-00-26.report-1.md` 为测试报告，不属于交付物。）

**修改（1）**

```
src/stock_plan/ui/app.py      启动副作用改由 startup_flags 两个开关控制（默认行为不变）
```

**本轮未做（2，等并发「快照 v2」改动合并后补）**

```
README.md                     新增「国内云服务器部署」章节 + 原云端章节交叉引用
CHANGELOG.md                  2026-10-29 条目
```

**删除（0）**：无。方案 A 的 `scripts/push_snapshot_to_server.ps1` 与 `docker/` 三件套按拍板不创建。

---

## 10. 用户侧人工操作清单（脚本管不到的部分）

1. 注册腾讯云（或华为云/京东云）+ 实名 + 领免费试用：**2 核 4G、≥40GB 系统盘、5Mbps、Ubuntu 24.04 LTS**。
2. 控制台安全组放行 **TCP 8501**（只放这一个；SSH 22 建议限制来源 IP）。
3. 拿到公网 IPv4 与登录方式，SSH 登录。
4. 执行手册第 2 节三条命令（引导 → 填 secrets → 起服务）。
5. 浏览器访问 `http://<公网IP>:8501`，按冒烟清单逐条勾选。
6. 把验收结果回填到本计划书 §12。

---

## 11. 用户拍板记录（2026-10-29 00:14）

| # | 问题 | 结论 |
|---|---|---|
| ① | 快照链路主用 A（本机 scp 直推）还是 B（服务器自己拉 GitHub）？ | **B**：服务器侧定时拉 Releases；A 与推送脚本不做 |
| ② | Docker 备选方案本轮做还是砍掉？ | **砍掉**：只要 systemd + venv |
| ③ | 工作区未提交的「快照 v2」改动如何处理？ | **先不管**：那些文件本轮一律不碰、不提交 |

由此产生两处与原计划的偏离，已在 §4 注明：Phase 4 取消、Phase 5 的顶级 README/CHANGELOG 推迟。

---

## 12. 验收标准

- [x] `pytest` 全量通过（**445 passed**，含新增 81 例）。
- [x] `deploy/` 物料齐全，文档命令与脚本步骤一一对应（`tests/test_deploy_assets.py` 静态校验）。
- [ ] 服务器 `systemctl status stock-plan` 为 `active (running)`，重启机器后自动恢复（待真机）。
- [ ] 公网 `http://<IP>:8501` 返回 200，首屏 5 秒内可交互（实测值回填，待真机）。
- [ ] 快照更新一次（B 链路）后，页面数据日期前进，服务自动重启成功（待真机）。
- [ ] 服务器日志无 Traceback/ERROR（待真机）。
- [x] 测试报告写入 `tests/reports/2026-10-29-00-26.report-1.md`。
