# -*- coding: utf-8 -*-
"""生成《使用指南（简明版）》Word 文档。

为什么要做成脚本而不是手写一份 docx：
页面和功能会变，手写的 Word 很快就过期且没人愿意改；用脚本生成后，
只要改这里的文字再重跑一次，就能得到格式统一的新版文档。

用法（项目虚拟环境即可，需要 python-docx）：
    .\\.venv\\Scripts\\python.exe scripts\\make_user_guide.py
输出：docs\\使用指南（简明版）.docx
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT = PROJECT_ROOT / "docs" / "使用指南（简明版）.docx"

BODY_FONT = "微软雅黑"
MONO_FONT = "Consolas"

# 界面里的 emoji 图标在 Word/WPS 里靠系统 emoji 字体渲染，某些环境会变成空方框。
# 给"别人"看的文档不该赌对方机器装了什么字体，所以统一剥掉，只留文字。
_EMOJI_RE = __import__("re").compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D]"
)


def _clean(text: str) -> str:
    """去掉 emoji 与多余空格；行首/行尾只留一个空格。"""
    return _EMOJI_RE.sub("", text).replace("  ", " ").strip()


def _set_font(run, name: str = BODY_FONT, size: float | None = None, bold: bool = False) -> None:
    """同时设置西文与中日韩字体：只设 font.name 对中文无效，必须再写 w:eastAsia。"""
    run.text = _clean(run.text)
    run.font.name = name
    if size is not None:
        run.font.size = Pt(size)
    run.bold = bold
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), name)
    return run


def _style_document(doc: Document) -> None:
    """统一正文/标题样式，避免逐段设置。"""
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(10.5)
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)
    normal.paragraph_format.space_after = Pt(4)
    normal.paragraph_format.line_spacing = 1.25
    for name, size in (("Heading 1", 15), ("Heading 2", 12)):
        st = doc.styles[name]
        st.font.name = BODY_FONT
        st.font.size = Pt(size)
        st.font.bold = True
        st.font.color.rgb = RGBColor(0x1F, 0x3B, 0x57)
        st.element.rPr.rFonts.set(qn("w:eastAsia"), BODY_FONT)


def _para(doc: Document, text: str, *, bold: bool = False, size: float | None = None,
          space_after: float | None = None) -> None:
    p = doc.add_paragraph()
    _set_font(p.add_run(text), size=size, bold=bold)
    if space_after is not None:
        p.paragraph_format.space_after = Pt(space_after)


def _bullets(doc: Document, items: list[str]) -> None:
    for it in items:
        p = doc.add_paragraph(style="List Bullet")
        _set_font(p.add_run(it))


def _numbered(doc: Document, items: list[str]) -> None:
    for it in items:
        p = doc.add_paragraph(style="List Number")
        _set_font(p.add_run(it))


def _heading(doc: Document, text: str, level: int = 1):
    """标题：禁止"标题在页尾、内容在下一页"的孤行。"""
    h = doc.add_heading(_clean(text), level=level)
    h.paragraph_format.keep_with_next = True
    return h


def _row_flags(row, header: bool = False) -> None:
    """禁止表格行跨页断开；表头行在续页自动重复。"""
    from docx.oxml import OxmlElement

    trpr = row._tr.get_or_add_trPr()
    trpr.append(OxmlElement("w:cantSplit"))
    if header:
        trpr.append(OxmlElement("w:tblHeader"))


def _table(doc: Document, header: list[str], rows: list[list[str]], widths: list[float] | None = None) -> None:
    t = doc.add_table(rows=1, cols=len(header))
    t.style = "Light Grid Accent 1"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    # 让 Word/WPS 自己按内容分配列宽：手填的 pt 宽度在不同纸张/字号下容易超出右边界
    t.autofit = True
    for i, h in enumerate(header):
        cell = t.rows[0].cells[i]
        cell.text = ""
        _set_font(cell.paragraphs[0].add_run(h), size=9.5, bold=True)
    _row_flags(t.rows[0], header=True)
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            _set_font(cells[i].paragraphs[0].add_run(val), size=9.5)
        _row_flags(t.rows[-1])
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def build() -> Path:
    doc = Document()
    _style_document(doc)

    # ---- 封面区（不单独做封面页，简明版直接开始）----
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_font(title.add_run("A 股盘前选股系统 · 使用指南"), size=20, bold=True)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_font(sub.add_run("简明版 ｜ 2026-10 ｜ 写给第一次上手的同事"), size=10).font.color.rgb = RGBColor(0x60, 0x60, 0x60)
    doc.add_paragraph()
    _para(doc, "一句话：它每天开盘前把全市场 5000 多只股票扫一遍，按你选的策略挑出最值得关注的几只，"
               "并告诉你在什么价位买、跌到哪里止损、大概持有多久。所有结论都可以先用历史数据回测验证。",
          size=10.5)
    _para(doc, "它不是“荐股软件”，不保证赚钱；它的价值是把你每天翻几百只股票的动作压缩到 5 分钟，"
               "并且让每个判断都能被回测复核。", size=10.5)

    # ---- 1 ----
    _heading(doc, "一、第一次使用：三步", level=1)
    _numbered(doc, [
        "双击项目里的启动脚本（或命令行运行 streamlit run src/stock_plan/ui/app.py），"
        "浏览器打开 http://localhost:8501 。",
        "左侧点「🔄 数据更新」→ 点「立即增量更新」。第一次需要拉全市场历史行情，耗时较久（十几分钟到半小时），"
        "之后每天只补当天，几秒到几十秒。",
        "回到「📈 今日信号」，选策略、设置持股数量，点「生成信号」。等几秒出现表格即可。",
    ])
    _para(doc, "提示：如果页面是空的或提示“无数据”，99% 是第 2 步没做完。", bold=True, size=10)

    # ---- 2 ----
    _heading(doc, "二、每天怎么用（盘前 5 分钟）", level=1)
    _bullets(doc, [
        "打开「🔄 数据更新」，点一次「立即增量更新」（把昨天的行情补齐）。",
        "打开「📈 今日信号」，生成信号，记下代码与买入区间。",
        "打开「💼 持仓诊断」，把你已经持有的股票代码填进去，看它是“继续拿 / 减仓 / 清仓”。",
        "想练手就打开「📝 模拟交易」，按信号下一笔虚拟单，系统会按真实规则（T+1、手续费、印花税）记账。",
    ])
    _para(doc, "盘后（可选）：去「📰 新闻舆情」「🌡️ 市场情绪」看看当天消息面和资金面，判断明天的环境。", size=10)

    # ---- 3 ----
    # 页面地图是一张 15 行的表，若从半页开始会被切成"只剩一行"的碎片，宁可另起一页
    doc.add_page_break()
    _heading(doc, "三、页面地图", level=1)
    _table(doc,
           ["页面", "用来干什么", "什么时候用"],
           [
               ["📈 今日信号", "按策略选出今天的候选股（含买价/止损/持有天数）", "每天开盘前"],
               ["💼 持仓诊断", "已有持仓该不该继续拿、要不要减仓", "有持仓时"],
               ["📝 模拟交易", "用虚拟资金按真实规则下单、看盈亏", "练手 / 验证信号"],
               ["📊 回测结果", "把策略放到历史上跑一遍，看赚不赚钱、回撤多大", "换策略前"],
               ["⚖️ 策略对比", "多个策略同台比收益/回撤/胜率", "挑策略时"],
               ["🔁 做T回测", "有底仓时，测算“周内低买高卖”能不能增厚收益", "想做 T 时"],
               ["🧩 策略拼装", "用可视化方式把因子组合成新策略", "想自定义时"],
               ["🗂️ 策略管理", "保存/分组/删除自己的策略", "有多个策略后"],
               ["🎓 四大师研究", "价值/成长/趋势/逆向四种视角看同一只股", "深挖个股时"],
               ["🤖 LLM 智能分析", "用大白话描述策略，系统转成参数存下来", "不想手调参数时"],
               ["📰 新闻舆情", "个股/板块新闻与情绪", "复盘时"],
               ["🌡️ 市场情绪·板块资金·公告存档", "涨停家数、炸板率、资金流向、公告", "判断大盘冷热"],
               ["📖 新手指南", "名词解释与流程图", "第一次用"],
               ["🔄 数据更新", "补行情、抓分类/情绪/公告数据", "每天第一件事"],
           ],
           widths=[120, 210, 90])

    # ---- 4 ----
    _heading(doc, "四、四个新增功能怎么用", level=1)
    _para(doc, "这四个是 2026-10 新加的，都不影响原来的页面和结论。", size=10)

    _heading(doc, "1）按行业选股（今日信号页）", level=2)
    _bullets(doc, [
        "在「选股范围」里找到「申万一级行业」，勾选一个或多个行业（不勾＝全市场）。",
        "适合“今天只看银行和白酒”这种场景：策略还是那个策略，只是在指定赛道里挑。",
        "行业标签来自申万分类，按月更新；没有行业数据的股票会被排除。",
    ])

    _heading(doc, "2）做T回测页", level=2)
    _bullets(doc, [
        "先说清楚：这里模拟的是“底仓 + 周内做 T”，不是当天来回的日内 T —— 系统只有日线数据，日内 T 无法回测。",
        "填底仓比例、单次 T 比例、触发跌幅、止盈/止损，点开始，会同时给出“底仓不动”和“底仓+做T”两条曲线。",
        "看结论时注意：笔数少于 30 笔时，胜率基本是噪音，不要当真。",
        "默认参数（跌 3% 买、涨 2% 卖）在茅台这类大蓝筹上几乎不触发，属正常现象，可以把触发幅度调大或换中小盘标的。",
    ])

    _heading(doc, "3）市场情绪页", level=2)
    _bullets(doc, [
        "盘面温度：涨停/跌停/炸板家数、炸板率、最高连板、连板梯队 —— 判断今天该激进还是谨慎。",
        "板块资金：哪些行业/概念在净流入、哪些在净流出（单位统一为元）。",
        "公告存档：点「抓持仓/自选公告」抓最近 7 天公告，自动去重；可把股票加入“关注清单”。",
    ])

    _heading(doc, "4）数据更新页新增的三个按钮", level=2)
    _table(doc,
           ["按钮", "抓什么", "多久跑一次", "耗时"],
           [
               ["📈 抓今日情绪/资金流", "涨停池、炸板池、资金流三张表", "每个交易日", "约 12 秒"],
               ["🏷️ 抓本月分类快照", "申万行业、证监会行业、同花顺概念", "每月一次", "2~3 分钟"],
               ["📰 抓持仓/自选公告", "持仓 + 关注清单的公告", "每个交易日", "按股票数，约 1 秒/只"],
           ],
           widths=[130, 180, 80, 70])
    _para(doc, "设定好后这些会由每日定时任务自动跑，一般不需要手动点。", size=10)

    # ---- 5 ----
    _heading(doc, "五、回测结果怎么看才不会被误导", level=1)
    _para(doc, "这一节比前面都重要。系统里的回测引擎遵循“同一套规则”，但结论很容易被过度解读：", size=10)
    _bullets(doc, [
        "单次回测的收益差不能当结论。同一策略只持 5 只股票时，总收益的随机波动可达 ±15 个百分点；"
        "换几只标的就能让结果差 20 个百分点。要看多组结果、看分布，而不是看一个数字。",
        "参数调得越漂亮，越可能是过拟合。系统里有「过拟合自查」，会用不同时间段检验；"
        "如果只在某一段特别好，基本就是曲线拟合。",
        "回测里的成本（佣金/印花税/滑点）是估算值。税率历史上调整过，系统默认按千分之一算，"
        "属于“偏高”的保守设定。",
        "记录里的历史数字都对应某一版引擎。引擎修 bug 后结论会变，看数字时注意它是什么时候跑的。",
    ])

    # ---- 6 ----
    _heading(doc, "六、常见问题", level=1)
    _table(doc,
           ["现象", "原因与处理"],
           [
               ["页面空白 / 提示无数据", "行情没拉。去「数据更新」点「立即增量更新」。"],
               ["提示“无行业数据”", "分类快照没抓。去「数据更新」点「抓本月分类快照」（一个月一次即可）。"],
               ["情绪页显示“尚无快照”", "当天情绪快照没抓，点「抓今日情绪/资金流」。"],
               ["信号生成很慢", "首次生成要算全市场因子，之后 10 分钟内有缓存；换策略或改参数会重算。"],
               ["更新中途失败", "多为网络抖动或某只股票停牌，重跑一次即可；日志在 data/logs/。"],
               ["浏览器打不开 8501", "服务没启动，或端口被占用。命令行启动时看它打印的实际端口。"],
           ],
           widths=[150, 290])

    # ---- 7 ----
    _heading(doc, "七、名词小抄", level=1)
    _table(doc,
           ["名词", "意思"],
           [
               ["涨停 / 跌停", "当日涨幅/跌幅达到交易所限制，价格被封住。"],
               ["炸板", "盘中触及涨停后又打开，通常代表分歧加大。"],
               ["连板 / 最高连板", "连续多日涨停；最高连板是市场情绪强弱的直观指标。"],
               ["炸板率", "炸板家数 ÷(涨停+炸板)。越高说明追高越容易受伤。"],
               ["底仓", "打算中长期持有的那部分仓位，做 T 时不动它。"],
               ["做 T", "在底仓之外，低买高卖赚差价，降低持仓成本。"],
               ["胜率 / 最大回撤", "赚钱笔数占比 / 从最高点回落的最大幅度。后者更能反映“扛不扛得住”。"],
               ["前瞻收益", "某一天买入后未来 N 天的收益，用来衡量信号本身有没有效，不依赖你怎么选股。"],
           ],
           widths=[110, 330])

    doc.add_paragraph()
    _para(doc, "最后一句：先用模拟盘跑两周，再动真钱。系统的信号是“候选”，不是“指令”。",
          bold=True, size=10.5)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc.save(OUT)
    return OUT


if __name__ == "__main__":
    p = build()
    print(f"已生成：{p}")
