"""统一时钟 —— 全项目的「现在几点 / 今天是哪天」都按北京时间（UTC+8）计算。

为什么要单独一个模块（2026-10-28 事故）：
- 云端容器默认跑在 UTC。北京时间 16:00–24:00 之间，容器认为「还没过 16:00」，
  `latest_expected_trade_date()` 于是取上一交易日 —— 页面「期望最新交易日」比
  北京时间晚一天，用户看到「期望 10-27、行情 09-03」，误以为数据没更新。
- 同一个时钟问题还会把情绪快照的日期目录（`date.today()`）、公告抓取区间、
  交易日历缓存的有效期判断写成 UTC 日期，凌晨 0–8 点之间会错一天。
- 中国 1991 年后不再使用夏令时，固定 +8 偏移最稳，不依赖系统 tzdata 是否完整。

约定：
- `now_cn()` 返回**带 +08:00 的 aware datetime**，用于「现在几点」判断与日志时间戳；
- `today_cn()` 返回北京日期，替代 `date.today()`；
- `to_cn(moment)` 把任意时刻换算到北京时间；**naive 值视为已经是北京时间**
  （保持既有调用方与单元测试的语义不变，避免把 `datetime(2026,10,28,9,0)` 又折算 8 小时）。

Revision History:
    2026-10-28  add Beijing-time clock helpers for cloud containers running in UTC
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

# 北京时间固定 UTC+8（中国无夏令时）。
CN_TZ = timezone(timedelta(hours=8), "Asia/Shanghai")


def to_cn(moment: datetime | None = None) -> datetime:
    """把任意时刻换算成北京时间。

    - 传 None：返回当前北京时间；
    - 传 naive：视为「已经是北京时间」，只补上时区标记；
    - 传 aware：按真实时刻换算（云端 UTC 的 15:07 → 北京 23:07）。
    """
    if moment is None:
        return datetime.now(CN_TZ)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=CN_TZ)
    return moment.astimezone(CN_TZ)


def now_cn() -> datetime:
    """当前北京时间（aware，isoformat 会带 +08:00）。"""
    return to_cn()


def today_cn(moment: datetime | None = None) -> date:
    """北京时间的「今天」。`moment` 仅供测试注入固定时刻。"""
    return to_cn(moment).date()


def stamp_cn(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """北京时间的字符串时间戳，默认格式与快照 manifest 的 created_at 一致。"""
    return now_cn().strftime(fmt)
