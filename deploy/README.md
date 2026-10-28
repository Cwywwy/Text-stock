# 国内服务器部署（方案 B：服务器只拉快照）

> 目标：把选股系统放到国内云服务器上，国内外都直连，打开延迟从「跨境 + 冷启动 2~7 秒」降到「同城机房几百毫秒」。
> 适用：Ubuntu 22.04 / 24.04（含腾讯云轻量、阿里云 ECS、华为云 Flexus 等）。
> 本文件是**运维手册**；设计取舍与阶段拆分见 `.github/plans/2026-10-29-1-new-deploy-on-china-cloud-server.md`。

## 0. 数据流与角色

```
本机 Windows（权威数据源）
  data/processed/  ──16:30 Windows 计划任务 \StockPlan\Daily_Publish──▶  GitHub Releases（tag=data-snapshot）
                                                                              │
                                                   17:10 每日定时器 ──────────┘
                                                          ▼
国内云服务器（访客实例，STOCK_PLAN_SERVICE=1）
  deploy/pull_snapshot.py 拉分卷 → 校验 sha256 → 原子替换 data/processed/
  → 重启 stock-plan 服务 → 浏览器访问 http://<公网IP>:8501
```

两个关键决定：

1. **服务器不自己更新行情**。它不跑 `update_daily`、不抓公告、不重建分类 —— 一旦两边都能写，就会出现「服务器数据比本机新但缺公告」这类漂移。服务器只消费本机发布的快照。
2. **服务器当「云端轻量实例」跑**。`STOCK_PLAN_CLOUD=1` 让并发降到安全值，`STOCK_PLAN_SERVICE=1` 关掉本机才该做的启动副作用（自动发布策略、同步境外 raw 链接），详见 `src/stock_plan/ui/startup_flags.py`。

## 1. 买服务器（腾讯云免费额度最快）

- 入口：<https://cloud.tencent.com/act/pro/free> → 轻量应用服务器。
- 规格：**2 核 4G / 5 Mbps / 60 GB 盘 / 500 GB 月流量**，新用户免费 **1 个月**（实名 + 产品新用户）。
  - 2C2G 也能跑，但没有余量给 pandas 排序/回测；1 G 内存不建议。
  - 到期**不会自动扣费**，需要手动续费；不想续就在控制台退订，数据自行备份。
- 付费参考：同规格续费约 100 元/月，2C2G 约 45 元/月。
- 镜像选 **Ubuntu 24.04 LTS**（自带 Python 3.12，脚本会用 uv 装 3.13）。
- 地区选离你近的（上海 / 广州 / 北京），晚高峰差别明显。
- 登录方式选**密钥**，把公钥填进控制台；后续命令都在本机 SSH 里执行。

阿里云免费档只有 2vCPU + 1 GiB，跑不动全市场数据；要用就选 ECS「300 元额度 / 3 个月」规格 2C4G 以上。华为云 Flexus L 的免费档规格以活动页为准。京东云免费期约 15 天。

## 2. 放代码上去

**别直接 scp 整个项目目录**（`.venv` + `data/` 有 600MB 以上，且 Windows 的 venv 在 Linux 上不能用）。

### 方式 A：本机打包成 bundle 再上传（推荐，绕开国内拉 GitHub 慢）

```powershell
# 本机：把当前分支打成单文件（只含 git 历史，不含 .venv/data）
git -C "D:\DeepSeekHarness\案例区\stock plan" bundle create D:\sp.bundle HEAD

# 本机：上传（把 IP 换成你的）
scp D:\sp.bundle root@<公网IP>:/tmp/

# 服务器：克隆到部署目录
ssh root@<公网IP>
git clone /tmp/sp.bundle /opt/stock-plan
cd /opt/stock-plan && git log --oneline -1
```

### 方式 B：服务器直接 clone GitHub

```bash
git clone https://github.com/Cwywwy/Text-stock.git /opt/stock-plan
```

国内直连 GitHub 常超时；卡住就把本机代理挂到服务器（`export HTTPS_PROXY=...`）或改用方式 A。

> 部署前确认代码里已经包含 `deploy/` 与本手册。若还没合并到 `main`，先合并，或者 clone 后 `git checkout <分支名>`。

## 3. 一键初始化

```bash
cd /opt/stock-plan
sudo bash deploy/bootstrap.sh
```

脚本做的事（可重复执行，幂等）：

1. `apt-get install curl ca-certificates git tzdata`，并把时区设为 `Asia/Shanghai`（系统里所有「今天」判断都按北京时间）。
2. 创建系统用户 `stockplan`，把你放进来的代码 chown 给它。
3. 装 uv → `uv venv --python 3.13` → `uv pip install -r requirements.txt`（默认走清华 PyPI 镜像，可用 `--pypi-index` 换）。
4. 建 `data/{processed,db,logs,cache,raw}` 与 `.streamlit/`。
5. 安装 3 个 systemd 单元并启用每日快照定时器。
6. 首次拉一次数据快照（约 360MB）。

常用开关：

```bash
sudo bash deploy/bootstrap.sh --app-dir /srv/stock-plan    # 换目录
sudo bash deploy/bootstrap.sh --skip-deps                  # 只装 unit，不重装依赖
sudo bash deploy/bootstrap.sh --pypi-index https://mirrors.aliyun.com/pypi/simple
```

## 4. 写密钥

```bash
sudo -u stockplan nano /opt/stock-plan/.streamlit/secrets.toml
```

内容照抄 `deploy/secrets.toml.example`（LLM 那三行必填，其余可选）。保存后：

```bash
sudo chown stockplan:stockplan /opt/stock-plan/.streamlit/secrets.toml
sudo chmod 600 /opt/stock-plan/.streamlit/secrets.toml
```

> **注意**：本系统没有登录/鉴权。放到公网后，任何拿到 IP:8501 的人都能用，也都能消耗这份 LLM key 的额度。详见第 10 节。

## 5. 启动与放行端口

```bash
sudo systemctl enable --now stock-plan
systemctl status stock-plan --no-pager      # 看到 active (running)
ss -lntp | grep 8501                        # 看到 0.0.0.0:8501
curl -sI http://127.0.0.1:8501 | head -3    # 本机自测 200
```

然后在**云控制台 → 防火墙 / 安全组**添加规则：

| 协议 | 端口 | 来源 | 说明 |
|---|---|---|---|
| TCP | 8501 | 0.0.0.0/0（或你的出口 IP） | Streamlit 端口 |

浏览器打开 `http://<公网IP>:8501` 即可。

**关于备案**（官方口径，腾讯云文档）：只用公网 IP 直连、不解析域名时**不需要备案**；一旦用未备案域名解析到这台机器，任何端口都会被监测阻断。所以这条路不需要域名，也不需要 HTTPS —— 代价是浏览器地址栏显示「不安全」，以及明文传输（只有公开行情和你自己的自选股，没有账号密码）。

## 6. 定时快照（每日 17:10）

```bash
systemctl list-timers stock-plan-snapshot.timer --no-pager   # 看下次触发时间
sudo systemctl start stock-plan-snapshot.service             # 手动立刻跑一次
journalctl -u stock-plan-snapshot.service -n 80 --no-pager   # 看本次结果
journalctl -u stock-plan -f                                  # 看应用日志
```

拉取脚本 `deploy/pull_snapshot.py` 的行为：

- 校验 manifest 与每个分卷的 sha256，整包再校验一次；失败不覆盖本地数据（先落 `data/snapshot_tmp`，成功才原子替换）。
- **快照超过 `--max-age-days`（默认 7）天会拒绝覆盖**，避免本机发布端掉线后服务器悄悄用旧数据。
- 距上次成功拉取不足 `--min-interval-hours`（默认 6）小时会跳过，避免重复下载 360MB。
- 成功记录写到 `data/snapshot_pull_state.json`，日志追加到 `data/logs/snapshot_pull.log`。

**让它拉完能自动重启服务**：`stockplan` 用户没有权限 `systemctl restart`，需要一条 sudoers 规则（先确认 `systemctl` 的实际路径）：

```bash
command -v systemctl   # 一般是 /usr/bin/systemctl
echo 'stockplan ALL=(root) NOPASSWD: /usr/bin/systemctl restart stock-plan' | sudo tee /etc/sudoers.d/stock-plan-restart
sudo chmod 440 /etc/sudoers.d/stock-plan-restart
sudo -u stockplan sudo -n systemctl restart stock-plan && echo OK
```

不配也能用：日志会写「重启失败，数据已更新」，下次重启后生效。

**GitHub 在国内拉不动时的三种对策**（按省事程度排序）：

1. 给 unit 加代理：编辑 `/etc/systemd/system/stock-plan-snapshot.service`，在 `[Service]` 里加
   `Environment=HTTPS_PROXY=http://127.0.0.1:7890`（服务器上自己起代理），然后 `sudo systemctl daemon-reload`。
2. 换仓库：把快照发到你自己的 Gitee/其他镜像，然后设置 `DATA_SNAPSHOT_REPO=<owner>/<repo>`（同样加 `Environment=`），地址规则是 `https://github.com/{repo}/releases/download/{tag}` —— 换成 GitHub 镜像站时也可用 `--base` 手动指定地址调试。
3. 兜底：本机 `scp` 直接把 `data/processed/` 传上去（最土但最快）：

```powershell
# 本机
scp -r "D:\DeepSeekHarness\案例区\stock plan\data\processed\*" root@<IP>:/opt/stock-plan/data/processed/
```
```bash
# 服务器
sudo chown -R stockplan:stockplan /opt/stock-plan/data && sudo systemctl restart stock-plan
```

## 7. 日常运维

```bash
# 更新代码（方式 A：本机重新 bundle 上传覆盖）
# 本机：git bundle create D:\sp2.bundle HEAD && scp D:\sp2.bundle root@<IP>:/tmp/
cd /opt/stock-plan
git fetch /tmp/sp2.bundle 'refs/heads/*:refs/remotes/bundle/*'
git log --oneline -3 bundle/main            # 确认要的提交
git merge --ff-only bundle/main             # 快进；有分叉就按需 rebase
sudo -u stockplan env HOME=/opt/stock-plan UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple \
  uv pip install --python /opt/stock-plan/.venv/bin/python -r requirements.txt
sudo systemctl restart stock-plan

# 换端口（比如 8501 被占用或被扫描）
sudo sed -i 's/--server.port 8501/--server.port 8888/' /etc/systemd/system/stock-plan.service
sudo systemctl daemon-reload && sudo systemctl restart stock-plan   # 别忘了改安全组

# 资源观察
free -h; df -h /opt; journalctl -u stock-plan --since today | tail -50
du -sh /opt/stock-plan/data/processed
```

磁盘规划：依赖约 1.5GB、数据约 360MB、日志很小，60GB 盘绰绰有余。

## 8. 排障对照表

| 症状 | 常见原因 | 处理 |
|---|---|---|
| 浏览器一直转圈 / 连接超时 | 安全组没放行，或 Streamlit 只听 127.0.0.1 | 查安全组；`ss -lntp | grep 8501` 必须是 `0.0.0.0:8501` |
| 页面报「数据快照恢复失败」 | 首次启动时 GitHub 拉不动，或快照过期 | `sudo systemctl start stock-plan-snapshot.service` 后看 journal；或按第 6 节兜底 scp |
| 页面正常但「今日信号」全空 | 快照只有行情（v1），分类/情绪缺 | 手动跑一次 `pull_snapshot.py --force`，新版快照会补齐分组 |
| 服务反复重启 | 依赖没装好 / Python 版本不对 / 端口占用 | `journalctl -u stock-plan -n 100`；确认 `/.venv/bin/python -V` 是 3.13.x |
| 进程被系统杀掉 | 2G 内存机型 OOM | `dmesg | grep -i oom`；换 4G 或把 `OMP_NUM_THREADS` 再降到 1 |
| 时间差 8 小时 / 期望交易日不对 | 系统时区是 UTC | `sudo timedatectl set-timezone Asia/Shanghai && sudo systemctl restart stock-plan` |
| LLM 页报鉴权失败 | secrets 没写或 key 失效 | 检查 `.streamlit/secrets.toml` 权限与内容；`journalctl` 里看具体报错 |
| 快照每天「跳过」 | 距上次成功不足 6 小时 | 正常；要强制拉就在命令行加 `--force` |

## 9. 成本与容量

- 免费 1 个月内成本 0；到期续费 2C4G 约 100 元/月（2C2G 约 45 元/月）。
- 流量：每天 360MB 拉取 + 页面对外，按 500GB/月额度算，自用 + 小范围分享够用（约够 1300 次快照拉取或大量页面浏览）。
- 内存实测参考：空闲约 400MB，打开回测/信号页峰值约 700–900MB。

## 10. 安全须知（务必看完）

- **没有鉴权**：拿到 `http://IP:8501` 就能用全部功能。别把链接发到公开群；要收回就换端口或关机。
- **收紧来源**：安全组把 8501 的来源改成你自己的出口 IP（家庭宽带 IP 会变，可改成公司/常用网络）。
- **key 保护**：服务器上放过 LLM key，额度被他人用掉不赔。定期在智谱控制台看用量。
- **想要密码**：在前面加一层 Caddy/Nginx Basic Auth（约 10 行配置，需备案域名才能上 443 HTTPS）；不做也不影响使用。
- 服务器别存个人隐私数据；`data/` 只有公开行情与你输入的自选股/持仓明细，若在意就别用同一台机器跑别的东西。
