# -*- coding: utf-8 -*-
"""文件：pull_snapshot.py
作者：Cwywwy
用途：国内服务器每日从 GitHub Releases 拉取最新数据快照（方案 B）。
      服务器是「访客实例」：不跑 update_daily / 公告抓取 / 分类重建，只消费本机发布的快照，
      因此服务器永远只有一份权威数据源（本机 data/processed），不会两边各自漂移。
所属模块：deploy（服务器运维脚本，不随应用导入，也不被 stock_plan 包引用）

用法（在服务器上，工作目录 = 应用根目录）：
    PYTHONPATH=/opt/stock-plan/src /opt/stock-plan/.venv/bin/python \\
        deploy/pull_snapshot.py --max-age-days 7 --min-interval-hours 6 --restart-service

参数：
    --max-age-days        快照最大年龄（默认 7 天，超期拒绝覆盖本地，防止发布端掉线后静默用旧数据）
    --min-interval-hours  距上次成功拉取的冷却时间（默认 6 小时；定时器 17:10 跑，手动调试会跳过）
    --retries             网络失败重试次数（默认 3，国内直连 GitHub 经常第一次失败）
    --part-workers        分卷并发数（默认 4，免费轻量 2M 带宽够用，别调太高）
    --restart-service     拉取成功后重启 Streamlit 服务，让页面立刻用上新数据
    --service-name        要重启的服务名（默认 stock-plan）
    --force               忽略冷却时间与「数据已齐」判断，强制拉取
    --dry-run             只打印将要访问的地址，不下载、不写文件

退出码：0 成功或按策略跳过；1 拉取失败；2 参数错误。

Revision History:
    2026-10-29  add server-side snapshot puller for china cloud deployment (phase 3)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from stock_plan.data.snapshot import (  # noqa: E402  — 必须先补 sys.path 才能导入
    BARS_DIR,
    PROJECT_ROOT,
    restore_with_fallback,
    snapshot_restore_bases,
)

try:  # 快照 v2 才有；v1 服务器上退回「不需要补分组」的实现
    from stock_plan.data.snapshot import missing_extras  # noqa: E402
except ImportError:  # pragma: no cover - 兼容仍是 v1 快照格式的服务器
    def missing_extras(processed_dir: Path | None = None) -> list[str]:
        return []

STATE_PATH = PROJECT_ROOT / "data" / "snapshot_pull_state.json"
LOCK_PATH = PROJECT_ROOT / "data" / "snapshot_pull.lock"
LOG_PATH = PROJECT_ROOT / "data" / "logs" / "snapshot_pull.log"
LOCK_STALE_SECONDS = 2 * 60 * 60
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def log(message: str) -> None:
    """同时写 stdout（进 journal）和 data/logs/snapshot_pull.log（可追溯）。"""
    line = f"[{datetime.now().strftime(STAMP_FORMAT)}] {message}"
    print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def read_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_state(state: dict) -> None:
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        log(f"警告：写状态文件失败 {error}")


def last_pull_age_hours(state: dict) -> float | None:
    raw = str(state.get("pulled_at") or "").strip()
    if not raw:
        return None
    try:
        return (datetime.now() - datetime.strptime(raw, STAMP_FORMAT)).total_seconds() / 3600
    except ValueError:
        return None


def acquire_lock() -> bool:
    """进程级互斥：定时器与手动调试撞车时，后来的直接跳过而不是并发下载。"""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOCK_PATH.exists():
        age = time.time() - LOCK_PATH.stat().st_mtime
        if age > LOCK_STALE_SECONDS:
            log(f"发现残留锁（{age / 3600:.1f} 小时未释放），按过期处理")
            LOCK_PATH.unlink(missing_ok=True)
        else:
            return False
    try:
        handle = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    os.write(handle, str(os.getpid()).encode())
    os.close(handle)
    return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def restart_service(name: str) -> None:
    """重启 Streamlit 服务。

    stockplan 用户默认没有 systemctl 权限：先直连试一次，失败再试 `sudo -n`
    （需要 deploy/README.md 里那条 sudoers 规则）。两条都失败也只告警 ——
    数据已经更新，服务下次重启后生效，不该因为重启失败把整次拉取判为失败。
    """
    attempts = (
        ["systemctl", "restart", name],
        ["sudo", "-n", "systemctl", "restart", name],
    )
    errors: list[str] = []
    for command in attempts:
        try:
            subprocess.run(command, check=True, timeout=120, capture_output=True, text=True)
            log(f"已重启服务 {name}（{' '.join(command[:2])}）")
            return
        except (OSError, subprocess.SubprocessError) as error:
            detail = getattr(error, "stderr", "") or str(error)
            errors.append(f"{' '.join(command[:2])}: {str(detail).strip()[:120]}")
    log(f"警告：重启 {name} 失败（{' | '.join(errors)}）；数据已更新，服务下次重启后生效")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从 GitHub Releases 拉取数据快照")
    parser.add_argument("--max-age-days", type=int, default=7, help="快照最大年龄（天）")
    parser.add_argument("--min-interval-hours", type=float, default=6.0, help="两次拉取的最小间隔（小时）")
    parser.add_argument("--retries", type=int, default=3, help="网络失败重试次数")
    parser.add_argument("--retry-wait", type=float, default=20.0, help="重试基础等待秒数（按次数线性放大）")
    parser.add_argument("--part-workers", type=int, default=4, help="分卷并发下载数")
    parser.add_argument("--restart-service", action="store_true", help="成功后重启 Streamlit 服务")
    parser.add_argument("--service-name", default="stock-plan", help="要重启的 systemd 服务名")
    parser.add_argument("--force", action="store_true", help="忽略冷却与完整性判断，强制拉取")
    parser.add_argument("--dry-run", action="store_true", help="只打印快照地址，不下载")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    bases = snapshot_restore_bases()
    log(f"快照地址：{' | '.join(bases)}")
    if args.dry_run:
        log(f"本地 bars 目录：{BARS_DIR}（只检查，不下载）")
        log(f"缺失分组：{missing_extras() or '无'}")
        return 0

    if not acquire_lock():
        log("已有拉取进程在运行，跳过本次")
        return 0

    try:
        state = read_state()
        age_hours = last_pull_age_hours(state)
        extras_missing = bool(missing_extras())
        if (
            not args.force
            and age_hours is not None
            and age_hours < args.min_interval_hours
            and not extras_missing
        ):
            log(f"距上次成功拉取 {age_hours:.1f} 小时（< {args.min_interval_hours} 小时），跳过")
            return 0
        if extras_missing:
            log(f"本地缺失分组：{'、'.join(missing_extras())}，本次补齐")

        stats: dict | None = None
        last_error: Exception | None = None
        for attempt in range(1, max(args.retries, 1) + 1):
            try:
                stats = restore_with_fallback(
                    bases,
                    BARS_DIR,
                    PROJECT_ROOT / "data" / "snapshot_tmp",
                    part_workers=args.part_workers,
                    max_age_days=args.max_age_days,
                )
                break
            except Exception as error:  # noqa: BLE001 — 任何失败都换下一次重试
                last_error = error
                log(f"第 {attempt}/{args.retries} 次拉取失败：{str(error)[:300]}")
                if attempt < args.retries:
                    time.sleep(args.retry_wait * attempt)

        if stats is None:
            log(f"拉取失败：{str(last_error)[:300]}")
            return 1

        write_state(
            {
                "pulled_at": datetime.now().strftime(STAMP_FORMAT),
                "bars_count": stats.get("bars_count"),
                "snapshot_created_at": stats.get("created_at"),
                "restored_at": stats.get("restored_at"),
            }
        )
        log(
            f"拉取完成：行情 {stats.get('bars_count')} 只，快照生成于 {stats.get('created_at')}，"
            f"恢复于 {stats.get('restored_at')}"
        )
        if args.restart_service:
            restart_service(args.service_name)
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
