# -*- coding: utf-8 -*-
"""盘前选股系统 — Streamlit 入口。

启动方式：
    uv run streamlit run src/stock_plan/ui/app.py

导航为平铺结构（便于后续功能模块化拆分）：
    今日信号 / 模拟交易 / 回测结果 / 策略对比 / 策略拼装 /
    策略管理 / 四大师研究 / LLM 智能分析 / 新闻舆情 / 数据更新

Revision History:
    2026-09-04  sync public strategies and trigger local pending publication
    2026-10-28  add checkout-consistency guard for mixed venv/copy starts
    2026-10-29  gate startup side effects with service-mode flags
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import streamlit as st


def _checkout_mismatch() -> str | None:
    """检查「app.py 所在副本」与「import 到的 stock_plan 包」是否同一个副本。

    为什么需要：本机存在两个项目副本（`D:\\DeepSeekHarness\\案例区\\stock plan` 与
    `D:\\Vscode\\stock plan`），各自 `.venv` 里的 `stock_plan.pth` 只指向自己的 `src`。
    若用 A 副本的 venv 跑 B 副本的 app.py，则 app 文件来自 B、而 `stock_plan` 包来自 A：
    A 副本若是旧版本（没有新页面模块），就会抛
    `ImportError: cannot import name 'market' from 'stock_plan.ui.views'`，
    且报错路径一半在 B、一半在 A，极其误导。这里提前比对并把真实原因说清楚。
    """
    try:
        import stock_plan  # noqa: PLC0415  — 必须在函数内 import 才能拿到实际加载位置
    except Exception:  # noqa: BLE001  — 连包都 import 不了时不抢真正报错的风头
        return None
    app_src = Path(__file__).resolve().parents[2]
    pkg_src = Path(stock_plan.__file__).resolve().parents[1]
    if app_src == pkg_src:
        return None
    return (
        "### 启动环境不一致：用错了虚拟环境\n\n"
        f"- `app.py` 所在副本：`{app_src}`\n"
        f"- 实际 import 到的 `stock_plan`：`{pkg_src}`\n\n"
        "本机有两个项目副本，各自 `.venv` 里的 `stock_plan.pth` 只指向自己的 `src`。"
        "现在是用「另一个副本」的 Python 在跑「这个副本」的 app，两边版本不同，"
        "必然出现 `ImportError` 或页面缺失。\n\n"
        "正确启动方式（在 `app.py` 所在目录执行，用同一副本的 venv）：\n"
        "```powershell\n"
        ".\\.venv\\Scripts\\python.exe -m streamlit run src\\stock_plan\\ui\\app.py --server.port 8501\n"
        "```\n"
        "自查一行：`.\\.venv\\Scripts\\python.exe -c \"import stock_plan; "
        "print(stock_plan.__file__)\"` —— 输出必须是本副本的 `src` 下的路径。"
    )


_MISMATCH = _checkout_mismatch()
if _MISMATCH:
    print(_MISMATCH, file=sys.stderr)  # 终端里也能看到（st.error 只在页面显示）
    st.error(_MISMATCH)
    st.stop()
    # 裸跑（未走 streamlit run）时 st.stop 不会中断，这里兜底，否则会接着抛出
    # 那个让人误解的 ImportError，把真正原因又盖掉
    raise SystemExit(2)

st.set_page_config(page_title="盘前选股系统", page_icon="📈", layout="wide")

# 云端/空数据环境：启动时自动从 GitHub 快照恢复行情数据（有数据则秒过）
from stock_plan.data.snapshot import PROJECT_ROOT, cloud_secrets_env

cloud_secrets_env()
if "snapshot_checked" not in st.session_state:
    st.session_state["snapshot_checked"] = True
    from stock_plan.data.snapshot import bootstrap_if_needed

    bootstrap_if_needed(ui=True)

from stock_plan.strategy.publication import sync_public_strategies_to_local
from stock_plan.ui.startup_flags import local_publish_enabled, remote_strategy_sync_enabled

if "public_strategy_sync_checked" not in st.session_state:
    st.session_state["public_strategy_sync_checked"] = True
    # 国内服务器访问 raw.githubusercontent.com 常超时（每次白等最多 30 秒），service 模式默认跳过
    if remote_strategy_sync_enabled():
        try:
            sync_public_strategies_to_local()
        except (RuntimeError, ValueError) as error:
            st.warning(f"共享策略同步失败：{error}")

if local_publish_enabled() and "pending_strategy_publication_started" not in st.session_state:
    st.session_state["pending_strategy_publication_started"] = True
    command = [sys.executable, "scripts/publish_snapshot.py", "--pending-only"]
    try:
        subprocess.Popen(
            command, cwd=str(PROJECT_ROOT),
            env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT / "src")},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError as error:
        st.warning(f"无法启动待发布策略任务：{error}")

from stock_plan.ui.views import (
    backtest,
    compare,
    four_masters,
    guide,
    llm,
    market,
    news,
    paper,
    portfolio,
    strategy_mgr,
    today,
    update_center,
    visual_builder,
)

nav = st.navigation(
    [
        st.Page(today.render, title="今日信号", icon="📋", url_path="today", default=True),
        st.Page(portfolio.render, title="持仓诊断", icon="🩺", url_path="portfolio"),
        st.Page(paper.render, title="模拟交易", icon="💼", url_path="paper"),
        st.Page(backtest.render, title="回测结果", icon="📊", url_path="backtest"),
        st.Page(compare.render, title="策略对比", icon="⚖️", url_path="compare"),
        st.Page(visual_builder.render, title="策略拼装", icon="🧩", url_path="builder"),
        st.Page(strategy_mgr.render, title="策略管理", icon="⚙️", url_path="strategy"),
        st.Page(four_masters.render, title="四大师研究", icon="🏛️", url_path="masters"),
        st.Page(llm.render, title="LLM 智能分析", icon="🤖", url_path="llm"),
        st.Page(news.render, title="新闻舆情", icon="📰", url_path="news"),
        st.Page(market.render, title="市场情绪", icon="🌡️", url_path="market"),
        st.Page(guide.render, title="新手指南", icon="🎓", url_path="guide"),
        st.Page(update_center.render, title="数据更新", icon="🔄", url_path="update"),
    ]
)
nav.run()
