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

from stock_plan.data.storage import PROJECT_ROOT
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
    from stock_plan.data.snapshot import CLOUD_FULL_DAYS, CLOUD_WORKERS, is_cloud

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
    elif mode == "full":
        # 全量拉取：云端轻量 1 年 / 本机 5 年
        cmd += ["--days", str(CLOUD_FULL_DAYS if is_cloud() else 1250), "--force"]
    if is_cloud():
        # 云端容器 2.7GB 内存：降低并发防超限
        cmd += ["--workers", str(CLOUD_WORKERS)]
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
    """当前数据日期一览：先回答「我现在用的是哪一天的数据」，再谈要不要更新。

    为什么把它放在页面最上面、而且是四块日期一起显示：
    用户真正会踩的坑不是"忘了点更新"，而是"行情补到了今天，但分类快照还是上个月"——
    那样选行业选出来的是旧名单。分开看四个指标很容易只盯行情，合在一起才能发现错位。
    行情日期来自缓存体检文件（扫描 5916 个 parquet 要 8 秒，不能每次 rerun 都扫）。
    """
    from stock_plan.data import freshness

    expected = latest_expected_trade_date()
    info = freshness.status(expected)

    latest = info.get("bars_latest")
    lag = info.get("lag_days")
    if latest and lag is not None and lag <= 0:
        st.success(f"✅ **当前行情数据日期：{latest}**（已是最新交易日）")
    elif latest:
        st.warning(f"⚠️ **当前行情数据日期：{latest}**，落后期望交易日 {lag} 天，建议立即增量更新。")
    else:
        st.error("❌ 还没有任何行情数据，请先执行一次全量更新。")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("行情最新交易日", latest or "无数据")
    c2.metric("期望最新交易日", str(expected.date()))
    c3.metric("已更新到最新交易日", f"{info.get('bars_current', 0)}/{info.get('bars_total', 0)} 只")
    c4.metric("落后 7 天以上", f"{info.get('bars_stale_7', 0)} 只")

    st.caption(
        f"其他三类数据：分类快照 **{info.get('classify_latest') or '无'}**"
        f"（{info.get('classify_months', 0)} 个月） · "
        f"情绪快照 **{info.get('market_latest') or '无'}**"
        f"（{info.get('market_days', 0)} 天） · "
        f"公告存档 **{info.get('ann_rows', 0)} 条 / {info.get('ann_codes', 0)} 只**"
        f"（最新 {info.get('ann_latest') or '无'}）"
    )

    if info.get("bars_stale_7"):
        examples = "、".join(info.get("bars_stale_codes", [])[:5])
        st.caption(
            f"有 {info['bars_stale_7']} 只股票 7 天以上没有新行情 —— 多为退市/长期停牌，"
            f"不影响选股与回测（回测会用它自己的历史区间）。例如：{examples or '—'}"
        )

    b1, b2 = st.columns([1, 3])
    if b1.button("🔍 重新扫描数据日期", key="btn_rescan_freshness", help="重扫全部行情文件，约 8 秒"):
        freshness.refresh_marker()
        st.rerun()
    b2.caption(f"体检时间：{info.get('bars_scanned_at') or '—'}（每次更新完成后自动刷新）")


def _render_log_tail() -> None:
    """展示最近几次更新日志（自动 + 手动）。"""
    with st.expander("📜 最近更新日志", expanded=False):
        if _UPDATE_LOG.exists():
            lines = _UPDATE_LOG.read_text(encoding="utf-8").splitlines()[-8:]
            st.code("\n".join(lines) or "（暂无记录）", language=None)
        else:
            st.caption("暂无更新记录")


def _cloud_badge() -> None:
    """显示当前运行环境徽章（云端轻量 / 本机完整）。"""
    from stock_plan.data.snapshot import CLOUD_WORKERS, is_cloud

    if is_cloud():
        st.info(f"☁️ **云端轻量模式**：增量更新并发 {CLOUD_WORKERS} 线程；"
                "数据快照在首次启动时自动恢复，数据丢失可点下方「从快照恢复」。")
    else:
        st.success("🖥️ **本机完整模式**：本机为数据权威源，可发布快照供云端恢复。")


def _run_extra_source(label: str, args: list[str], timeout: int) -> None:
    """以子进程跑一个新数据源的抓取命令，并把结果存进 session_state。

    为什么用子进程而不是直接 import 调用：这几个模块都依赖 akshare 的网络抓取，
    akshare 会在异常路径上卡住（实测没有 timeout 会挂十几分钟），
    子进程可以用 `timeout` 硬性兜底，卡死也只损失一个子进程，不会把页面拖死。
    为什么结果放 session_state 再显示：Streamlit 点按钮即整页重跑，
    只 `st.success` 的话下次重跑就消失（项目里踩过的坑）。
    """
    with st.spinner(f"正在抓取{label}（最长 {timeout} 秒）…"):
        try:
            proc = subprocess.run(
                [sys.executable, "-m", *args],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=timeout, cwd=str(PROJECT_ROOT),
            )
            tail = "\n".join((proc.stdout or "").splitlines()[-15:])
            ok = proc.returncode == 0
            if not ok:
                tail += "\n" + "\n".join((proc.stderr or "").splitlines()[-10:])
        except subprocess.TimeoutExpired:
            ok, tail = False, f"超时（>{timeout}s），已终止子进程。网络可能不稳定，稍后重试。"
        except Exception as e:  # noqa: BLE001
            ok, tail = False, f"启动失败：{e}"
    st.session_state["extra_src_result"] = {"label": label, "ok": ok, "tail": tail}


def _extra_sources_panel() -> None:
    """新数据源（分类/情绪/公告）的手动更新入口。"""
    st.subheader("🗂️ 分类 / 情绪 / 公告数据")
    st.caption(
        "行情以外的三类补充数据。它们**不参与历史回测**，只用于选股过滤、盘面研判与公告查阅。"
        "分类快照按月一份即可（行业归属一个月内基本不变），情绪快照与公告按天抓。"
    )

    # 当前状态（读不到就跳过，不能让页面崩）
    col1, col2, col3 = st.columns(3)
    try:
        from stock_plan.data import market_snapshot as ms

        days = ms.available_days()
        col1.metric("情绪快照（日）", f"{len(days)} 天", days[-1] if days else "尚无")
    except Exception as e:  # noqa: BLE001
        col1.metric("情绪快照（日）", "读取失败", help=str(e))
    try:
        from stock_plan.data import classification as clf

        months = clf.available_months()
        col2.metric("分类快照（月）", f"{len(months)} 个月", months[-1] if months else "尚无")
    except Exception as e:  # noqa: BLE001
        col2.metric("分类快照（月）", "读取失败", help=str(e))
    try:
        from stock_plan.data import announcements as ann

        s = ann.stats()
        col3.metric("公告存档", f"{s['rows']} 条", f"{s['codes']} 只股票")
    except Exception as e:  # noqa: BLE001
        col3.metric("公告存档", "读取失败", help=str(e))

    b1, b2, b3 = st.columns(3)
    if b1.button("📈 抓今日情绪/资金流", key="btn_snap_market", width="stretch",
                 help="涨停池、炸板池、跌停池 + 行业/概念/个股资金流，约 12 秒"):
        _run_extra_source("市场情绪快照", ["stock_plan.data.market_snapshot"], 240)
    if b2.button("🏷️ 抓本月分类快照", key="btn_snap_classify", width="stretch",
                 help="申万一级/二级成分 + 证监会行业 + 同花顺概念，约 2~3 分钟"):
        _run_extra_source("分类快照", ["stock_plan.data.classification", "--all"], 900)
    if b3.button("📰 抓持仓/自选公告", key="btn_snap_ann", width="stretch",
                 help="回溯 7 天，按持仓 + 自选清单抓，去重入库"):
        _run_extra_source("公告", ["stock_plan.data.announcements", "--days", "7"], 600)

    res = st.session_state.get("extra_src_result")
    if res:
        if res["ok"]:
            st.success(f"{res['label']}：完成")
        else:
            st.error(f"{res['label']}：失败")
        with st.expander("查看命令输出", expanded=not res["ok"]):
            st.code(res["tail"] or "（无输出）", language="text")


def _restore_from_snapshot() -> None:
    """手动从 GitHub data-snapshot 分支恢复行情数据（带进度显示）。"""
    from stock_plan.data.snapshot import BARS_DIR, PROJECT_ROOT, raw_base_url, restore_from_url

    url = raw_base_url()
    tmp_dir = PROJECT_ROOT / "data" / "snapshot_tmp"
    with st.status("⬇️ 正在从云端快照恢复行情数据 …", expanded=True) as status:
        st.write(f"快照地址：{url}")

        def on_prog(done_bytes: int, total_bytes: int) -> None:
            st.write(f"下载进度 {done_bytes / 1048576:.0f}/{total_bytes / 1048576:.0f} MB")

        try:
            stats = restore_from_url(url, BARS_DIR, tmp_dir, progress=on_prog)
            status.update(label="✅ 快照恢复完成", state="complete", expanded=False)
            st.success(f"已恢复 {stats['bars_count']} 只股票的行情数据（快照生成于 {stats['created_at']}）")
            st.rerun(scope="app")
        except Exception as e:  # noqa: BLE001
            status.update(label="❌ 快照恢复失败", state="error")
            st.error(f"恢复失败：{e}")


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
    _cloud_badge()

    with st.expander("☁️ 从云端快照恢复数据", expanded=False):
        st.caption("从 GitHub data-snapshot 分支下载最新数据快照并覆盖本地行情数据（本机数据为权威源，云端数据丢失时使用）。")
        if st.button("⬇️ 立即从快照恢复", key="btn_restore_snapshot"):
            _restore_from_snapshot()

    st.divider()

    prog = _progress_state()
    if st.button(
        "🔄 立即增量更新",
        key="btn_update_data",
        type="primary",
        width="stretch",
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
    _extra_sources_panel()

    st.divider()
    _render_log_tail()