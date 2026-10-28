# -*- coding: utf-8 -*-
"""生成「策略对比」「持仓诊断」两页图表的静态样张（不启动 Streamlit、不连数据库）。

用途：改观感时快速出图核对，不用每次等 40 秒加载全 A 股。
页面上的图直接调 `views/compare.py`、`views/portfolio.py` 里的工厂函数，
因此样张与真实页面同源。

用法（在项目根目录运行）：
    .venv\\Scripts\\python.exe scripts/preview_compare_portfolio.py
然后截图：
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --headless=new \
        --disable-gpu --hide-scrollbars --window-size=1500,1750 \
        --screenshot=D:\\...\\shot-compare-portfolio.png file:///D:/.../preview-compare-portfolio.html

Revision History:
    2026-10-28  (phase 2) new preview generator for compare and portfolio charts
"""
from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from stock_plan.ui import chart_style as cs
from stock_plan.ui.views.compare import _equity_fig, _oos_fig
from stock_plan.ui.views.portfolio import _price_level_fig

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("D:/DeepSeekHarness/.dsh-tmp/charts/preview-compare-portfolio.html")


def _bdate_index(days: int) -> pd.DatetimeIndex:
    return pd.bdate_range(end=date(2026, 10, 27), periods=days)


def _walk(days: int, *, drift: float, wave: float, seed: int, start: float = 100_000.0) -> pd.Series:
    """造一条确定性的资产曲线（用三角函数代替随机数，保证每次出图一致）。"""
    values = []
    for i in range(days):
        trend = drift * i
        swing = wave * math.sin(i / 11.0 + seed) + wave * 0.45 * math.sin(i / 3.7 + seed * 2)
        values.append(start * (1 + trend + swing))
    return pd.Series(values, index=_bdate_index(days), dtype=float)


def compare_results() -> dict[str, dict]:
    days = 480
    index = _bdate_index(days)
    trend = _walk(days, drift=0.0011, wave=0.035, seed=0.4)
    mean_rev = _walk(days, drift=0.0003, wave=0.028, seed=2.1)
    multi = _walk(days, drift=0.0007, wave=0.045, seed=3.6)
    benchmark = pd.Series(
        [1.0 + 0.00032 * i + 0.012 * math.sin(i / 17.0) for i in range(days)],
        index=index,
        dtype=float,
    )
    return {
        "趋势跟随 v2（5 只）": {"equity": trend, "benchmark": benchmark, "metrics": {}},
        "均值回归（3 只）": {"equity": mean_rev, "benchmark": benchmark, "metrics": {}},
        "多因子精选（8 只）": {"equity": multi, "benchmark": benchmark, "metrics": {}},
    }


def oos_equity() -> pd.Series:
    return _walk(300, drift=0.0008, wave=0.03, seed=5.2)


def portfolio_result() -> dict:
    days = 120
    index = _bdate_index(days)
    close = []
    for i in range(days):
        base = 26.0 + 2.6 * math.sin(i / 9.0) - 0.012 * i
        base += 1.4 * math.sin(i / 2.3) * 0.3
        close.append(round(base, 2))
    bars = pd.DataFrame(
        {
            "date": [d.strftime("%Y-%m-%d") for d in index],
            "open": close,
            "high": [c * 1.015 for c in close],
            "low": [c * 0.985 for c in close],
            "close": close,
            "volume": [1_200_000 + 40_000 * (i % 7) for i in range(days)],
        }
    )
    last = close[-1]
    return {
        "code": "600323",
        "name": "瀚蓝环境",
        "buy_price": round(last * 1.04, 2),
        "bars": bars,
        "levels": {"stop_loss": round(last * 0.93, 2), "take_profit": round(last * 1.18, 2)},
        "t_trade": {"buy_low": round(last * 0.985, 2), "sell_high": round(last * 1.015, 2)},
    }


def _figure_html(figure, *, title: str, caption: str) -> str:
    if figure is None:
        return f'<div class="block"><div class="block-title">{title}</div><p class="caption">无数据。</p></div>'
    body = figure.to_html(include_plotlyjs=True, full_html=False, config={"displayModeBar": False})
    return (
        f'<div class="block"><div class="block-title">{title}</div>'
        f'<p class="caption">{caption}</p>{body}</div>'
    )


def build_html() -> str:
    equity = _equity_fig(compare_results(), initial_cash=100_000.0)
    oos = _oos_fig(oos_equity())
    price = _price_level_fig(portfolio_result())
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>策略对比 / 持仓诊断 · 改造后</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    background: #0E1117; color: {cs.TEXT}; margin: 0; padding: 28px 32px 40px;
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    font-size: 14px; line-height: 1.6;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .lead {{ color: {cs.AXIS_TEXT}; font-size: 12px; margin: 0 0 22px; }}
  .block {{ margin: 10px 0 26px; }}
  .block-title {{ font-size: 14px; font-weight: 600; margin: 18px 0 6px; }}
  .caption {{ color: {cs.AXIS_TEXT}; font-size: 12px; margin: 4px 0 10px; }}
</style></head>
<body>
<h1>策略对比 / 持仓诊断 · 改造后（分支3 · Phase 2）</h1>
<p class="lead">统一规范：近黑画布、无网格、右侧纵轴、红涨绿跌、灰虚线=等权基准、价位线用色区分（绿=买入、红=止损、琥珀=止盈、蓝/紫=做T）。数据为合成样本，仅用于看观感。</p>
{_figure_html(equity, title="资金曲线对比（策略对比页）", caption="每条线是一套策略在同一段历史里的账户资产；灰虚线是「等权持有全部候选股」的基准，用来判断策略本身有没有超额。")}
{_figure_html(oos, title="样本外资金曲线（Walk-Forward）", caption="只包含「训练段之后再没见过」的那段历史，比训练段好看才有意义。")}
{_figure_html(price, title="个股走势 + 价位参考（持仓诊断页）", caption="实线是收盘价；虚线是系统给的价位参考——绿=目标买入价、红=止损、琥珀=止盈，蓝/紫点线是做T低吸与高抛。线只代表规则算出的位置，不是预测。")}
</body></html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="生成策略对比 / 持仓诊断样张")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_html(), encoding="utf-8")
    print(f"已生成：{args.out}（{args.out.stat().st_size} bytes）")


if __name__ == "__main__":
    main()
