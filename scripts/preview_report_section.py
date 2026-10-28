"""生成「为什么是这几只」区块的静态样张（不启动 Streamlit、不连数据库）。

用途：改观感时反复截图比对，避免每次都等 40 多秒的全 A 股打分。
页面里的两个组件（对比表、分差瀑布图）直接复用 `signal_report_section` 与
`widgets` 的真实实现，所以样张与实页同源，不是另画一套。

用法（项目根目录）：
    .venv\\Scripts\\python.exe scripts/preview_report_section.py
然后就能截图：
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --headless=new \
        --disable-gpu --hide-scrollbars --window-size=1500,1500 \
        --screenshot=D:\\...\\shot-report.png file:///D:/.../preview-report-section.html

Revision History:
    2026-10-28  (phase 2) new preview generator for the signal explainability section
    2026-10-28  (phase 3) add llm narrative block using the template fallback
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from stock_plan.llm.signal_narrative import template_narrative
from stock_plan.signal.report import (
    CandidateFacts,
    EntryGap,
    Penalty,
    RejectedSample,
    SignalReport,
    UniverseFacts,
)
from stock_plan.ui import chart_style as cs
from stock_plan.ui.signal_report_section import comparison_frame, rejected_frame
from stock_plan.ui.widgets import score_waterfall_fig

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("D:/DeepSeekHarness/.dsh-tmp/charts/preview-report-section.html")

# 真实一次运行（2026-10-27 数据）的名次与分数，让样张看起来就是实页
_ENTRIES = [
    ("002956", "西麦食品", 89.8, 0.021, 62.0, 1.87, 86.0, 74.0),
    ("300012", "华测检测", 88.9, 0.048, 66.0, 1.42, 91.0, 71.0),
    ("600323", "瀚蓝环境", 88.4, 0.061, 69.0, 1.21, 88.0, 78.0),
    ("301215", "中汽股份", 87.9, 0.093, 73.0, 1.65, 85.0, 66.0),
    ("002332", "仙琚制药", 87.6, 0.114, 77.0, 1.02, 80.0, 82.0),
]
_RUNNERS = [
    ("300908", "水羊股份", 87.6, 0.131, 79.0, 0.96, 82.0, 70.0),
    ("600694", "大商股份", 87.1, -0.058, 54.0, 0.88, 76.0, 79.0),
    ("601288", "农业银行", 86.2, -0.032, 58.0, 1.05, 71.0, 88.0),
]


def synthetic_report() -> SignalReport:
    """构造一份确定性的报告（分数与真实一次运行对齐，便于比对观感）。"""
    picked: list[CandidateFacts] = []
    for rank, (code, name, score, dev, rsi, vol_ratio, trend, fund) in enumerate(
        _ENTRIES, start=1
    ):
        penalties: tuple[Penalty, ...] = ()
        if code == "002332":
            penalties = (
                Penalty(rule="chase_high", label="追高（偏离 ma20 过大）", points=-100.0, basis=dev),
                Penalty(rule="rsi_hot", label="RSI 过热", points=-20.0, basis=rsi),
            )
        elif code == "301215":
            penalties = (
                Penalty(rule="dev_high", label="偏离 ma20 偏大", points=-30.0, basis=dev),
            )
        picked.append(
            CandidateFacts(
                code=code, name=name, score=score, rank=rank, picked=True,
                close=round(12 + rank, 2), ma20=round(11 + rank, 2), ma60=round(10 + rank, 2),
                dev_ma20=dev, rsi14=rsi, vol_ratio=vol_ratio,
                trend_score=trend, fund_score=fund, bar_date="2026-10-27",
                penalties=penalties,
            )
        )
    runners: list[CandidateFacts] = []
    for offset, (code, name, score, dev, rsi, vol_ratio, trend, fund) in enumerate(
        _RUNNERS, start=6
    ):
        runners.append(
            CandidateFacts(
                code=code, name=name, score=score, rank=offset, picked=False,
                close=round(12 + offset, 2), ma20=round(11 + offset, 2), ma60=round(10 + offset, 2),
                dev_ma20=dev, rsi14=rsi, vol_ratio=vol_ratio,
                trend_score=trend, fund_score=fund, bar_date="2026-10-27",
            )
        )
    return SignalReport(
        generated_at=datetime(2026, 10, 27, 21, 0, 0),
        universe=UniverseFacts(
            total_in_list=5576,
            with_bars=5379,
            passed=4130,
            rejected_counts={
                "st": 197, "no_bars": 49, "empty_bars": 0, "new_listing": 3,
                "suspended": 0, "illiquid": 1200,
            },
            data_until="2026-10-27",
            strategy_name="趋势跟随策略",
            strategy_fingerprint="7c1f4a9b2d3e",
        ),
        picked=tuple(picked),
        runners_up=tuple(runners),
        rejected_samples=(
            RejectedSample(
                code="000013", name="*ST石化",
                reason="st", reason_text="ST/*ST 风险警示（今天的状态，非历史时点）",
                detail={"is_st": 1},
            ),
            RejectedSample(
                code="301612", name="新股样本",
                reason="new_listing", reason_text="次新股（日线根数不足）",
                detail={"bars": 32, "min_bars": 60},
            ),
            RejectedSample(
                code="000981", name="流动性样本",
                reason="illiquid", reason_text="流动性不足（近 20 日均额过低）",
                detail={"avg_amount20": 18_400_000.0, "min_amount": 50_000_000.0},
            ),
            RejectedSample(
                code="600145", name="停牌样本",
                reason="suspended", reason_text="停牌（最新一根日线成交量为 0）",
                detail={"last_volume": 0.0},
            ),
        ),
        entry_gap=EntryGap(last_picked_score=87.6, next_score=87.6),
        warnings=("硬过滤使用的 ST 判据是「今天」的 ST 状态，不是历史时点。",),
    )


def _narrative_html(text: str) -> str:
    """把解读文本（段落 + 「- 」列表）转成静态 HTML，模拟页面里的 markdown 渲染效果。"""
    blocks: list[str] = []
    bullets: list[str] = []

    def flush() -> None:
        if bullets:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in bullets) + "</ul>")
            bullets.clear()

    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        if line.startswith(("- ", "* ")):
            bullets.append(line[2:].strip())
            continue
        flush()
        blocks.append(f"<p>{line}</p>")
    flush()
    return "".join(blocks)


def _table_html(frame) -> str:
    head = "".join(f"<th>{column}</th>" for column in frame.columns)
    body = []
    for _, row in frame.iterrows():
        cells = []
        for column in frame.columns:
            text = str(row[column])
            class_name = ""
            if column == "状态":
                class_name = "picked" if text == "入选" else "runner"
            if column == "扣分原因" and len(text) > 40:
                class_name += " wrap"
            cells.append(f'<td class="{class_name}">{text}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def build_html(report: SignalReport) -> str:
    figure = score_waterfall_fig(report)
    figure_html = (
        figure.to_html(include_plotlyjs=True, full_html=False, config={"displayModeBar": False})
        if figure is not None
        else "<p>（无落选者，不画图）</p>"
    )
    penalties = [
        f"{item.code} {item.name}：合计 {item.penalty_points:+.0f} 分 → "
        + "；".join(penalty.explain() for penalty in item.penalties)
        for item in report.picked
        if item.penalties
    ]
    penalty_items = "".join(f"<li>{line}</li>" for line in penalties)
    universe = report.universe
    counts = "、".join(
        f"{label} {value} 只"
        for key, value in universe.rejected_counts.items()
        if value
        for label in [
            {
                "st": "ST/*ST", "no_bars": "无日线", "empty_bars": "日线为空",
                "new_listing": "次新", "suspended": "停牌", "illiquid": "流动性不足",
            }.get(key, key)
        ]
    )
    gap_note = report.entry_gap.note() if report.entry_gap else "—"
    gate = report.picked[-1]
    narrative = _narrative_html(template_narrative(report))
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>为什么是这几只 — 样张</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    background: #0E1117; color: {cs.TEXT}; margin: 0; padding: 28px 32px 40px;
    font-family: "Microsoft YaHei", "PingFang SC", "Segoe UI", sans-serif;
    font-size: 14px; line-height: 1.6;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .caption {{ color: {cs.AXIS_TEXT}; font-size: 12px; margin: 6px 0 14px; }}
  .block-title {{ font-size: 14px; font-weight: 600; margin: 22px 0 8px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; }}
  th {{ color: {cs.AXIS_TEXT}; font-weight: 500; text-align: left;
        padding: 7px 10px; border-bottom: 1px solid {cs.GRID}; white-space: nowrap; }}
  td {{ padding: 7px 10px; border-bottom: 1px solid rgba(27,32,39,0.45); }}
  td.picked {{ color: {cs.UP}; font-weight: 600; }}
  td.runner {{ color: {cs.AXIS_TEXT}; }}
  td.wrap {{ white-space: normal; max-width: 320px; }}
  .chart {{ margin: 6px 0 0; }}
  .narrative p {{ margin: 4px 0; font-size: 13px; }}
  .tag {{ color: {cs.AXIS_TEXT}; font-size: 11px; font-weight: 400; margin-left: 6px;
         border: 1px solid {cs.GRID}; border-radius: 999px; padding: 1px 8px; }}
  ul {{ margin: 6px 0 0 18px; padding: 0; color: {cs.TEXT}; font-size: 12.5px; }}
  li {{ margin: 3px 0; }}
</style></head>
<body>
  <h1>🔍 为什么是这几只（入选 vs 紧随对比）</h1>
  <p class="caption"><b>中线</b> · 数据截至 {universe.data_until}｜股票列表 {universe.total_in_list} 只
    ｜有日线 {universe.with_bars} 只｜通过硬过滤 {universe.passed} 只
    ｜策略 {universe.strategy_name}（指纹 {universe.strategy_fingerprint}）</p>

  <p class="block-title">候选对比（入选者 + 紧随其后）</p>
  {_table_html(comparison_frame(report))}
  <p class="caption">「原始分」= 综合分 - 扣分，代表没被规则拉下来时的排名实力；
    扣分来自策略里写死的规则（偏离均线过远、均线空头、RSI 过热等）。</p>

  <div class="chart">{figure_html}</div>
  <p class="caption">虚线 = 入选门槛 {gate.score:.1f} 分（第 {gate.rank} 名 {gate.code} {gate.name}）；
    柱高 = 紧随者与门槛的差距，圆点标出各自综合分。绿色表示分数更低、因此排在门外。</p>
  <p class="caption"><b>入选门槛</b>：{gap_note}</p>

  <p class="block-title">入选者也不是满分：扣分项逐条列出</p>
  <ul>{penalty_items}</ul>

  <p class="block-title">被硬过滤挡在门外的样本（每类原因各 1 只）</p>
  {_table_html(rejected_frame(report.rejected_samples))}
  <p class="caption">各类淘汰数量：{counts}。</p>
  <p class="caption">· 硬过滤使用的 ST 判据是「今天」的 ST 状态，不是历史时点。</p>

  <p class="block-title">🤖 大模型解读<span class="tag">模板兜底样式</span></p>
  <div class="narrative">{narrative}</div>
  <p class="caption">上面是模型不可用或输出校验不过时的兜底文本；模型可用时会换成模型组织
    的语言，但数字同样只能引用上面的表格与事实清单。</p>
</body></html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="生成信号解释区块的静态样张")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = synthetic_report()
    output = args.output
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_html(report), encoding="utf-8")
    print(f"已生成 {output}（{output.stat().st_size} bytes）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
