# -*- coding: utf-8 -*-
"""数据更新中心 — 左侧导航独立功能模块。

功能：
- 展示当前数据状态（已缓存股票数、期望最新交易日）
- 手动触发增量更新 / 未缓存股票补拉：以**独立子进程**执行
  （akshare 的 py_mini_racer 在部分环境下会致命崩溃拖垮宿主进程，
  子进程隔离保证 UI 稳定，更新进程崩溃也不影响页面）
- 进度与结果通过 data/logs/update_progress.json 传递，fragment 每 3 秒轮询
- 展示最近几次自动/手动更新的日志
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import streamlit as st

from stock_plan.data.storage import PROJECT_ROOT, Storage
from stock_plan.data.updater import (
    LOG_DIR,
    PROGRESS_PATH,
    latest_expected_trade_date,
)

_UPDATE_LOG = LOG_DIR / "update.log"


def _progress_state() -> dict:
    """读取（或初始化）本次会话的更新状态标记（进度数据本体在进度文件里）。"""
    if "update_progress" not in st.session_state:
        st.session_state["update_progress"] = {"running": False, "mode": "", "result": None, "error": None}
    return st.session_state["update_progress"]


def _read_progress_file() -> dict | None:
    try:
        if PROGRESS_PATH.exists():
            return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def _start_task(mode: str, codes: list[str] | None = None) -> None:
    """以独立子进程启动更新任务，进度落到 JSON 文件供 fragment 轮询。"""
    prog = _progress_state()
    prog.update({"running": True, "mode": mode, "result": None, "error": None})
    # 先清掉旧进度文件，避免读到上一轮的完成状态
    try:
        PROGRESS_PATH.unlink(missing_ok=True)
    except Exception:
        pass

    cmd = [sys.executable, "-m", "stock_plan.data.update_daily"]
    if mode == "uncached":
        cmd += ["--uncached", *(codes or [])]
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _fmt_stats(res: dict, mode: str) -> str:
    if mode == "incremental":
        msg = (
            f"✅ 增量更新完成：本次更新 **{res.get('updated', 0)}** 只，"
            f"已是最新 {res.get('current', 0)} 只，失败 {res.get('failed', 0)} 只，"
            f"耗时 {res.get('elapsed', 0)}s（数据截至 {res.get('expected_date', '-')}）"
        )
    else:
        msg = (
            f"✅ 未缓存股票补拉完成：成功 **{res.get('updated', 0)}** 只，"
            f"失败 {res.get('failed', 0)} 只，耗时 {res.get('elapsed', 0)}s"
        )
    if res.get("errors"):
        sample = "、".join(c for c, _ in res["errors"][:3])
        msg += f"\n\n失败示例：{sample}"
    return msg


def _show_result(prog: dict) -> None:
    """渲染刚完成的任务结果 + 未缓存股票补拉入口（fragment 每次刷新重绘，保持稳定显示）。"""
    result = prog.get("result")
    if result is None:
        return
    if prog.get("error"):
        st.error(f"更新失败：{prog['error']}")
    else:
        st.success(_fmt_stats(result, prog.get("mode", "incremental")))

    uncached = result.get("uncached") or []
    if prog.get("mode") == "incremental" and uncached:
        with st.expander(f"⚠️ 发现 {len(uncached)} 只未缓存股票（新上市/此前失败）", expanded=False):
            st.caption("示例：" + "、".join(uncached[:10]) + ("…" if len(uncached) > 10 else ""))
            st.caption(
                "这些股票本地没有历史数据，定时任务不自动拉取。点击下方按钮单独补拉（近 5 年全量，耗时较久）。"
            )
            if st.button(f"⬇️ 单独拉取这 {len(uncached)} 只", key="btn_fetch_uncached", type="primary"):
                _start_task("uncached", codes=uncached)
                st.rerun(scope="app")


@st.fragment(run_every=3)
def _update_panel() -> None:
    """每 3 秒轮询进度文件：运行中显示进度条，完成后展示结果（均在 fragment 内自动刷新）。"""
    prog = _progress_state()
    if prog.get("running"):
        pf = _read_progress_file()
        if pf is not None and not pf.get("running"):
            # 子进程已完成，把结果搬进会话状态并清理进度文件
            prog.update(
                {
                    "running": False,
                    "mode": pf.get("mode", prog.get("mode")),
                    "result": pf.get("result"),
                    "error": pf.get("error"),
                }
            )
            try:
                PROGRESS_PATH.unlink(missing_ok=True)
            except Exception:
                pass
        elif pf is not None:
            total = pf.get("total") or 0
            done = pf.get("done") or 0
            pct = min(done / total, 1.0) if total else 0.0
            mode_name = "增量更新" if pf.get("mode") == "incremental" else "补拉未缓存股票"
            st.progress(
                pct,
                text=f"⏳ {mode_name}中：{done}/{total or '?'} 只（失败 {pf.get('failed', 0)}）"
                + (f" 当前：{pf.get('current', '')}" if pf.get("current") else ""),
            )
            return
        else:
            st.info("任务已启动，正在准备数据（首次进度稍后出现）…")
            return

    _show_result(prog)


def _data_status() -> None:
    """当前数据状态一览。"""
    storage = Storage()
    bars_dir = PROJECT_ROOT / "data" / "processed" / "bars"
    cached = len(list(bars_dir.glob("*.parquet"))) if bars_dir.exists() else 0
    expected = latest_expected_trade_date()
    c1, c2 = st.columns(2)
    c1.metric("本地已缓存股票", f"{cached} 只")
    c2.metric("期望最新交易日", str(expected.date()))


def _render_log_tail() -> None:
    """展示最近几次更新日志（自动 + 手动）。"""
    with st.expander("📜 最近更新日志", expanded=False):
        if _UPDATE_LOG.exists():
            lines = _UPDATE_LOG.read_text(encoding="utf-8").splitlines()[-8:]
            st.code("\n".join(lines) or "（暂无记录）", language=None)
        else:
            st.caption("暂无更新记录")


def render() -> None:
    """数据更新页主入口。"""
    st.title("🔄 数据更新")
    if os.name == "nt":
        st.caption(
            "系统已配置每日自动更新：交易日 16:00 / 20:00 / 次日 0:00 / 8:00 由 Windows 计划任务"
            "后台执行（幂等设计，重复运行无副作用，数据已是最新时秒级跳过）。"
            "此页面用于手动补充更新。"
        )
    else:
        st.caption(
            "云端部署模式：定时计划任务仅在本机 Windows 部署时生效，请在此页面手动更新数据"
            "（幂等设计，重复运行无副作用，数据已是最新时秒级跳过）。"
        )

    _data_status()
    st.divider()

    prog = _progress_state()
    if st.button(
        "🔄 立即增量更新",
        key="btn_update_data",
        type="primary",
        use_container_width=True,
        help="为全部已缓存股票补拉最近 30 天日线（未缓存股票不自动拉取）",
    ):
        if prog.get("running"):
            st.warning("已有更新任务在后台运行，请等待完成。")
        else:
            _start_task("incremental")
            st.rerun(scope="app")

    # 运行中：实时进度条；刚完成：结果 + 未缓存补拉入口（均在 fragment 内自动刷新）
    _update_panel()

    st.divider()
    _render_log_tail()