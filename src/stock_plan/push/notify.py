"""推送模块 — 飞书 / 微信（Server酱）Webhook 推送。

配置方式（环境变量）：
- 飞书：FEISHU_WEBHOOK（自定义机器人 Webhook 地址）
- 微信：WECHAT_SENDKEY（Server酱 SendKey，https://sct.ftqq.com）

未配置时推送函数返回 False，不抛异常，保证 UI 正常。
"""
from __future__ import annotations

import os

import requests


def push_feishu(text: str, webhook: str | None = None) -> bool:
    """推送文本到飞书自定义机器人。

    参数：
        text: 要推送的文本内容。
        webhook: 飞书机器人 Webhook 地址（默认读环境变量 FEISHU_WEBHOOK）。

    返回：
        是否推送成功。
    """
    webhook = webhook or os.getenv("FEISHU_WEBHOOK", "")
    if not webhook:
        return False
    try:
        resp = requests.post(
            webhook,
            json={"msg_type": "text", "content": {"text": text}},
            timeout=10,
        )
        data = resp.json()
        return data.get("code") == 0 or data.get("StatusCode") == 0
    except Exception:
        return False


def push_wechat(text: str, sendkey: str | None = None) -> bool:
    """推送文本到微信（Server酱）。

    参数：
        text: 要推送的文本内容。
        sendkey: Server酱 SendKey（默认读环境变量 WECHAT_SENDKEY）。

    返回：
        是否推送成功。
    """
    sendkey = sendkey or os.getenv("WECHAT_SENDKEY", "")
    if not sendkey:
        return False
    try:
        resp = requests.post(
            f"https://sctapi.ftqq.com/{sendkey}.send",
            data={"title": "盘前选股信号", "desp": text},
            timeout=10,
        )
        return resp.json().get("code") == 0
    except Exception:
        return False


def format_signals_text(signals: list) -> str:
    """把信号列表格式化为推送文本。

    参数：
        signals: Signal 对象列表（含 code/name/score/entry_price/exit_price/stop_loss/hold_days）。

    返回：
        多行文本。
    """
    lines = ["📈 盘前选股信号", "=" * 20]
    for s in signals:
        lines.append(
            f"{s.code} {s.name}（分 {s.score}）\n"
            f"  买入 {s.entry_price} → 卖出 {s.exit_price}\n"
            f"  止损 {s.stop_loss}，持仓 {s.hold_days} 天"
        )
    return "\n".join(lines)