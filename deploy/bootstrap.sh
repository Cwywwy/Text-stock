#!/usr/bin/env bash
# 文件：bootstrap.sh
# 作者：Cwywwy
# 用途：国内云服务器（Ubuntu 22.04 / 24.04）一次性初始化 —— 基础包、时区、运行用户、
#      uv + Python 3.13 + 依赖、systemd 单元、每日快照定时器、首次拉快照。可重复执行。
# 所属模块：deploy（服务器运维脚本，不随应用导入）
#
# 用法：
#   sudo bash deploy/bootstrap.sh                    # 默认装到 /opt/stock-plan
#   sudo bash deploy/bootstrap.sh --app-dir /srv/sp  # 换安装目录
#   sudo bash deploy/bootstrap.sh --skip-deps        # 只装 unit 文件，不重装依赖
#   sudo bash deploy/bootstrap.sh --pypi-index https://mirrors.aliyun.com/pypi/simple
#
# Revision History:
#   2026-10-29  add one-shot server bootstrap for china cloud deployment (phase 3)
set -euo pipefail

APP_DIR="/opt/stock-plan"
APP_USER="stockplan"
SKIP_DEPS=0
PYPI_INDEX="${PYPI_INDEX:-https://pypi.tuna.tsinghua.edu.cn/simple}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-dir)    APP_DIR="${2:?需要目录参数}"; shift 2 ;;
    --user)       APP_USER="${2:?需要用户名}"; shift 2 ;;
    --pypi-index) PYPI_INDEX="${2:?需要镜像地址}"; shift 2 ;;
    --skip-deps)  SKIP_DEPS=1; shift ;;
    -h|--help)    sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

log()  { printf '\033[1;32m[bootstrap]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap]\033[0m %s\n' "$*" >&2; }

[[ $EUID -eq 0 ]] || { echo "请用 sudo bash deploy/bootstrap.sh 执行" >&2; exit 1; }
[[ -d "$APP_DIR" ]] || { echo "找不到应用目录 $APP_DIR：先把代码放上去再运行本脚本" >&2; exit 1; }
[[ -f "$APP_DIR/requirements.txt" ]] || { echo "$APP_DIR/requirements.txt 不存在，目录可能放错了" >&2; exit 1; }

# 1) 基础包与时区（云端容器/服务器默认 UTC，本系统所有「今天」判断依赖北京时间）
log "安装基础依赖"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates git tzdata
timedatectl set-timezone Asia/Shanghai || warn "时区设置失败，请手动执行 timedatectl set-timezone Asia/Shanghai"

# 2) 运行用户（systemd 以它身份跑，不用 root）
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  log "创建服务用户 $APP_USER"
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER" || true
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# 3) uv + Python 3.13 + 依赖（项目要求 >=3.13，Ubuntu 自带 3.10/3.12 不够）
if [[ "$SKIP_DEPS" -eq 0 ]]; then
  if ! command -v uv >/dev/null 2>&1; then
    log "安装 uv（若卡在 astral.sh 可手动装到 /usr/local/bin 后重跑加 --skip-deps）"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
  fi
  log "创建 .venv（Python 3.13）并安装依赖，索引：$PYPI_INDEX"
  cd "$APP_DIR"
  sudo -u "$APP_USER" env HOME="$APP_DIR" UV_DEFAULT_INDEX="$PYPI_INDEX" PIP_INDEX_URL="$PYPI_INDEX" \
    uv venv --python 3.13 .venv
  sudo -u "$APP_USER" env HOME="$APP_DIR" UV_DEFAULT_INDEX="$PYPI_INDEX" PIP_INDEX_URL="$PYPI_INDEX" \
    uv pip install --python "$APP_DIR/.venv/bin/python" -r "$APP_DIR/requirements.txt"
fi

# 4) 数据与配置目录
log "准备数据目录"
mkdir -p "$APP_DIR/data"/{processed,db,logs,cache,raw} "$APP_DIR/.streamlit"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data" "$APP_DIR/.streamlit"

# 5) systemd 单元（服务 + 每日快照定时器）
log "安装 systemd 单元"
install -m 644 "$APP_DIR/deploy/stock-plan.service" /etc/systemd/system/stock-plan.service
install -m 644 "$APP_DIR/deploy/stock-plan-snapshot.service" /etc/systemd/system/stock-plan-snapshot.service
install -m 644 "$APP_DIR/deploy/stock-plan-snapshot.timer" /etc/systemd/system/stock-plan-snapshot.timer
systemctl daemon-reload
systemctl enable stock-plan-snapshot.timer >/dev/null 2>&1
systemctl restart stock-plan-snapshot.timer || warn "快照定时器启动失败，稍后用 systemctl status 排查"

# 6) 首次拉快照（放在启动应用之前，避免空库启动后一路报错）
if [[ "$SKIP_DEPS" -eq 0 ]]; then
  log "首次拉取数据快照（约 360MB，国内网络可能较慢）"
  if sudo -u "$APP_USER" env HOME="$APP_DIR" PYTHONPATH="$APP_DIR/src" \
     "$APP_DIR/.venv/bin/python" "$APP_DIR/deploy/pull_snapshot.py" --force --max-age-days 7; then
    log "快照就绪"
  else
    warn "快照拉取失败（GitHub 在国内可能不稳）。稍后手动重跑：sudo -u $APP_USER env PYTHONPATH=$APP_DIR/src $APP_DIR/.venv/bin/python $APP_DIR/deploy/pull_snapshot.py --force"
  fi
fi

cat <<EOF

[bootstrap] 初始化完成。剩下的必须手动做：

  1) 写密钥：     sudo -u $APP_USER nano $APP_DIR/.streamlit/secrets.toml
                  模板：$APP_DIR/deploy/secrets.toml.example
  2) 启动服务：   sudo systemctl enable --now stock-plan
  3) 云控制台 → 防火墙/安全组放行 TCP 8501（或你改用的端口）
  4) 浏览器打开： http://<服务器公网IP>:8501

  查状态：systemctl status stock-plan --no-pager
  看日志：journalctl -u stock-plan -f
  快照：  journalctl -u stock-plan-snapshot.service -n 50 --no-pager
EOF
