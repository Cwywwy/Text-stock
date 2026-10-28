# -*- coding: utf-8 -*-
"""回测结果图表样张生成器 —— 不启动 Streamlit，直接出静态 HTML 截图对比。

为什么需要它：
Streamlit 页面要手动选策略、点「运行回测」才能看到图，没法自动截图；而且真实回测
耗时几分钟。这个脚本用**确定性合成数据**（固定随机种子）走一遍真实的工厂链路
（views/backtest.py 的 _hero_fig / _monthly_fig / _heatmap_fig / _tab_trades 里的图），
把改造前（旧样式）与改造后（新规范）各渲染一份 HTML，供 Edge headless 截图与用户拍板。
合成数据不代表真实收益，只用于看观感。

用法：
    .venv\\Scripts\\python.exe scripts\\preview_charts.py --out .dsh-tmp\\charts
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from stock_plan.backtest.engine import BacktestResult
from stock_plan.backtest.metrics import calc_metrics
from stock_plan.backtest.report import make_report
from stock_plan.ui import chart_style as cs
from stock_plan.ui.views import backtest as bt

_CSS = """
body{margin:0;background:#0E1117;color:#E6E8EB;
     font-family:-apple-system,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;}
.wrap{max-width:1180px;margin:0 auto;padding:26px 22px 60px;}
h1{font-size:22px;margin:0 0 6px;}
.sub{color:#8A9099;font-size:13px;margin:0 0 20px;line-height:1.6;}
h2{font-size:15px;margin:26px 0 8px;font-weight:600;}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:0 0 12px;}
.card{background:#161B24;border:1px solid #1E222A;border-radius:10px;padding:10px 12px;}
.card .k{color:#8A9099;font-size:12px;}
.card .v{font-size:20px;margin-top:3px;}
.cap{color:#8A9099;font-size:12px;margin:2px 0 22px;line-height:1.7;}
.tag{display:inline-block;font-size:11px;color:#8A9099;border:1px solid #1E222A;
     border-radius:999px;padding:1px 8px;margin-left:8px;vertical-align:middle;}
"""


def synthetic_result(n_days: int = 504, seed: int = 35) -> tuple[BacktestResult, dict, dict]:
    """构造一份可复现的合成回测结果（资金曲线 + 基准 + 交易明细）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-11-01", periods=n_days)

    ret = rng.normal(0.00055, 0.0105, n_days)
    ret[180:202] -= 0.0055  # 第一段回撤
    ret[330:348] -= 0.0090  # 第二段回撤
    equity = pd.Series(100_000 * np.cumprod(1 + ret), index=dates)

    bench_ret = rng.normal(0.00026, 0.0092, n_days)
    bench_ret[180:202] -= 0.0040
    benchmark = pd.Series(np.cumprod(1 + bench_ret), index=dates)

    n = 140
    reasons = rng.choice(["止盈", "止损", "到期"], size=n, p=[0.44, 0.30, 0.26])
    pnl = np.where(
        reasons == "止损",
        -rng.uniform(300, 2600, n),
        rng.normal(1100, 1500, n),
    ).round(2)
    hold = rng.integers(1, 21, size=n)
    entry_i = rng.integers(0, n_days - 31, size=n)
    trades = pd.DataFrame(
        {
            "code": rng.choice(["600519", "000001", "300750", "601318", "002594"], size=n),
            "reason": reasons,
            "pnl": pnl,
            "pnl_pct": (pnl / 20_000 * 100).round(2),
            "hold_days": hold,
            "entry_date": dates[entry_i],
            "exit_date": dates[entry_i + hold],
        }
    )
    result = BacktestResult(equity_curve=equity, trades=trades, benchmark=benchmark)
    return result, make_report(result), calc_metrics(equity, trades)


# ---------------------------------------------------------------------------
# 旧样式（改造前）：写死配色、分类月轴、RdYlGn 热力图、无基准、无 uirevision
# ---------------------------------------------------------------------------

def _legacy_theme(fig: go.Figure, height: int, y_title: str) -> go.Figure:
    """近似 Streamlit 旧观感：模板默认网格 + 系统默认字号。"""
    fig.update_layout(
        height=height,
        margin=dict(l=40, r=20, t=30, b=30),
        yaxis_title=y_title,
        paper_bgcolor="#0E1117",
        plot_bgcolor="#0E1117",
        font=dict(color="#FAFAFA", size=12),
    )
    return fig


def legacy_figures(result: BacktestResult, report: dict) -> list[tuple[str, go.Figure, str]]:
    """旧版四张图：资金曲线、收益曲线、月度柱、热力图、盈亏分布。"""
    equity = result.equity_curve
    out: list[tuple[str, go.Figure, str]] = []

    fig = go.Figure(go.Scatter(
        x=pd.to_datetime([p["date"] for p in report["equity_curve"]]),
        y=[p["value"] for p in report["equity_curve"]],
        mode="lines", name="总资产", line=dict(color="#1f77b4", width=2),
    ))
    out.append(("资金曲线（金额）", _legacy_theme(fig, 400, "资产（元）"), "旧版只有一条蓝色总资产线，没有基准、没有回撤阴影，纵轴标题还是「资产（元）」这种口语写法。"))

    pct = (equity / float(equity.iloc[0]) - 1) * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=pct.index, y=pct.cummax(), mode="lines",
                             line=dict(width=0), hoverinfo="skip", showlegend=False))
    fig.add_trace(go.Scatter(x=pct.index, y=pct, mode="lines", name="累计收益率",
                             line=dict(color="#1f77b4", width=2), fill="tonexty",
                             fillcolor="rgba(214,39,40,0.25)"))
    out.append(("收益曲线（动态变化）", _legacy_theme(fig, 400, "收益率（%）"), "旧版回撤阴影用的是正红 rgba(214,39,40)，在深色背景上发灰。"))

    months = [m["month"] for m in report["monthly_returns"]]
    fig = go.Figure(go.Bar(
        x=months,
        y=[m["return"] for m in report["monthly_returns"]],
        name="月度收益",
        marker_color=["#d62728" if m["return"] < 0 else "#2ca02c" for m in report["monthly_returns"]],
    ))
    out.append(("月度收益", _legacy_theme(fig, 300, "收益率（%）"),
                "旧版月份是字符串分类轴：放大后刻度不重算、不换格式，标签会一直挤在一起。"))

    hm = report["monthly_heatmap"]
    years = sorted({h["year"] for h in hm})
    months_i = list(range(1, 13))
    z = []
    for y in years:
        row = []
        for mo in months_i:
            row.append(next((h["return"] for h in hm if h["year"] == y and h["month"] == mo), None))
        z.append(row)
    fig = go.Figure(go.Heatmap(
        z=z,
        x=[f"{m}月" for m in months_i],
        # 年份后面带「年」是为了强制分类轴：纯数字字符串会被 Plotly 当数值轴，刻度变成 2025.5 这种。
        y=[f"{int(y)} 年" for y in years],
        colorscale="RdYlGn",
        zmid=0,
        text=[[("" if v is None else f"{v:.1f}") for v in row] for row in z],
        texttemplate="%{text}",
        colorbar=dict(title="收益%"),
    ))
    out.append(("月度收益热力图", _legacy_theme(fig, 300, ""),
                "旧版 RdYlGn 是给浅色背景设计的，深色底上又刺眼又分不清涨跌（红绿都被压暗）。"))

    if report["trade_distribution"]:
        fig = go.Figure(go.Bar(
            x=[t["range"] for t in report["trade_distribution"]],
            y=[t["count"] for t in report["trade_distribution"]],
            name="交易分布", marker_color="#ff7f0e",
        ))
        out.append(("交易盈亏分布", _legacy_theme(fig, 300, "笔数"),
                    "旧版固定橙色柱子，和策略主色 #00C896 不是一套色。"))
    return out


# ---------------------------------------------------------------------------
# 新样式（改造后）：直接复用页面里的工厂函数，保证样张 = 实页
# ---------------------------------------------------------------------------

def new_figures(result: BacktestResult, report: dict) -> list[tuple[str, go.Figure, str]]:
    out: list[tuple[str, go.Figure, str]] = []
    hero_pct = bt._hero_fig(result, "收益率")
    if hero_pct is not None:
        out.append(("主图 · 收益率口径", hero_pct,
                    "策略（绿）+ 基准虚线（灰）+ 红色回撤阴影；左上角口径可切「金额」，纵轴靠右、无网格。"))
    hero_money = bt._hero_fig(result, "金额")
    if hero_money is not None:
        out.append(("主图 · 金额口径", hero_money,
                    "同一份数据切到金额口径：基准按同一起始资金等比缩放，虚线是初始资金参考线。"))
    monthly = bt._monthly_fig(report)
    if monthly is not None:
        out.append(("月度收益", monthly,
                    "真日期轴（缩放时刻度自动重算）+ 柱顶数值 + 红涨绿跌，和主图同一套配色。"))
    heat = bt._heatmap_fig(report)
    if heat is not None:
        out.append(("月度收益热力图", heat,
                    "自定义色阶：绿→面板色→红，深色底下涨跌一眼可读，文字统一白色。"))
    return out


def _cards_html(metrics: dict) -> str:
    cards = [
        ("总收益率", f"{metrics.get('total_return', 0):.2f}%"),
        ("年化收益率", f"{metrics.get('annual_return', 0):.2f}%"),
        ("最大回撤", f"{metrics.get('max_drawdown', 0):.2f}%"),
        ("夏普比率", f"{metrics.get('sharpe', 0):.2f}"),
        ("胜率", f"{metrics.get('win_rate', 0):.2f}%"),
        ("盈亏比", f"{metrics.get('profit_loss', 0) or 0:.2f}"),
        ("交易次数", f"{metrics.get('trade_count', 0)}"),
        ("平均持仓", f"{metrics.get('avg_hold_days', 0):.1f} 天"),
    ]
    body = "".join(f'<div class="card"><div class="k">{k}</div><div class="v">{v}</div></div>' for k, v in cards)
    return f'<div class="cards">{body}</div>'


def _fig_html(fig: go.Figure, inline_js: bool) -> str:
    return fig.to_html(
        full_html=False,
        include_plotlyjs="inline" if inline_js else False,
        config={"displayModeBar": False, "responsive": True, "scrollZoom": True},
    )


def build_html(title: str, note: str, sections: list[tuple[str, go.Figure, str]],
               metrics: dict | None = None) -> str:
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        f"<title>{title}</title><style>{_CSS}</style></head><body><div class='wrap'>",
        f"<h1>{title}</h1><p class='sub'>{note}</p>",
    ]
    if metrics:
        parts.append(_cards_html(metrics))
    for i, (heading, fig, caption) in enumerate(sections):
        parts.append(f"<h2>{heading}</h2>")
        parts.append(f"<div class='fig'>{_fig_html(fig, inline_js=(i == 0))}</div>")
        if caption:
            parts.append(f"<p class='cap'>{caption}</p>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成回测图表样张（静态 HTML）")
    parser.add_argument("--out", default=".dsh-tmp/charts", help="输出目录")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    result, report, metrics = synthetic_result()

    before = build_html(
        "回测结果页 · 改造前",
        "旧样式：单条蓝色曲线、无基准、分类月轴、RdYlGn 热力图、默认网格与字号。",
        legacy_figures(result, report),
        metrics=metrics,
    )
    after = build_html(
        "回测结果页 · 改造后（分支3 · Phase 1）",
        "新规范：近黑画布、无背景网格、右侧纵轴、红涨绿跌、策略 vs 等权基准、可切金额/收益率、"
        "四个标签页收纳明细。合成数据仅用于看观感。",
        new_figures(result, report),
        metrics=metrics,
    )

    (out_dir / "preview-before.html").write_text(before, encoding="utf-8")
    (out_dir / "preview-after.html").write_text(after, encoding="utf-8")
    print(f"已生成：\n  {out_dir / 'preview-before.html'}\n  {out_dir / 'preview-after.html'}")


if __name__ == "__main__":
    main()
