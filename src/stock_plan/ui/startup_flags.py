# -*- coding: utf-8 -*-
"""文件：startup_flags.py
作者：Cwywwy
用途：把「当前是哪种部署形态」的判断收在一处，供 Streamlit 入口决定启动副作用是否执行。
      本机（Windows 开发/日常）、Streamlit Community Cloud、国内服务器三种形态的行为各不同，
      以前用 `is_cloud()` 单条件散落在 app.py 里，服务器形态没法表达，也没法单测。
所属模块：ui（入口支撑；刻意不 import streamlit，便于纯函数测试）

形态与默认行为：

| 形态 | 判定 | 共享策略同步 | 本机自动发布 |
|---|---|---|---|
| 本机 | 无开关 | 开（历史行为） | 开（历史行为） |
| 国内服务器 | `STOCK_PLAN_SERVICE=1` | **关**（境外 raw 域名常超时） | 关（不是权威数据源） |
| Streamlit Cloud | `is_cloud()` | 开 | 关（历史行为） |

环境变量：
    STOCK_PLAN_SERVICE       1/true/yes/on 表示「国内服务器服务端」，只看这一项
    STOCK_PLAN_REMOTE_SYNC   显式覆盖共享策略同步：0/false/no/off 关闭，其他真值开启

Revision History:
    2026-10-29  add service-mode startup flags for china cloud deployment (phase 2)
"""
from __future__ import annotations

import os
from collections.abc import Mapping

SERVICE_ENV = "STOCK_PLAN_SERVICE"
REMOTE_SYNC_ENV = "STOCK_PLAN_REMOTE_SYNC"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _lookup(name: str, env: Mapping[str, str] | None) -> str:
    """取环境变量并归一化：None/缺失 → 空串，其余去空格转小写。"""
    source = os.environ if env is None else env
    value = source.get(name)
    return "" if value is None else str(value).strip().lower()


def is_service_mode(env: Mapping[str, str] | None = None) -> bool:
    """是否运行在国内服务器服务端形态。

    只认 STOCK_PLAN_SERVICE 的真值；写错的值（如 `staging`）按 False 处理 —— 宁可多跑
    副作用，也不要因为拼错一个值把本机的自动发布悄悄关掉。
    """
    return _lookup(SERVICE_ENV, env) in _TRUE


def remote_strategy_sync_enabled(env: Mapping[str, str] | None = None) -> bool:
    """启动时是否从 GitHub 同步公开策略。

    国内服务器访问 `raw.githubusercontent.com` 经常连不上，而
    `publication._read_remote_json()` 每次要白等最多 30 秒，所以 service 模式默认关闭；
    需要时显式 `STOCK_PLAN_REMOTE_SYNC=1` 打开。
    """
    raw = _lookup(REMOTE_SYNC_ENV, env)
    if raw:
        return raw not in _FALSE
    return not is_service_mode(env)


def local_publish_enabled(
    env: Mapping[str, str] | None = None, cloud: bool | None = None
) -> bool:
    """是否由本进程触发「待发布策略」的本机发布任务。

    service 模式与云端实例都不是权威数据源，不该在这里拉起 `scripts/publish_snapshot.py`。
    `cloud` 参数留给测试注入，默认才去问 `snapshot.is_cloud()`（避免模块导入期依赖）。
    """
    if is_service_mode(env):
        return False
    if cloud is None:
        from stock_plan.data.snapshot import is_cloud  # noqa: PLC0415 — 延迟导入，保持模块轻量

        cloud = is_cloud()
    return not cloud


def describe(env: Mapping[str, str] | None = None) -> str:
    """一行描述，写进 systemd journal 方便确认当前形态。"""
    from stock_plan.data.snapshot import is_cloud  # noqa: PLC0415

    cloud = is_cloud()
    return (
        f"service_mode={is_service_mode(env)} cloud={cloud} "
        f"remote_strategy_sync={remote_strategy_sync_enabled(env)} "
        f"local_publish={local_publish_enabled(env, cloud)}"
    )
