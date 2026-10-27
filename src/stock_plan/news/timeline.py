"""新闻舆情时间线模块 — 聚合多源新闻/公告/日历数据。

数据源（2026-09-03 实测可用）：
- 个股公告：东方财富公告接口（np-anotice-stock）
- 全球市场要闻：akshare stock_info_global_em
- 财经日历：akshare news_economic_baidu
- 分红送转：akshare news_trade_notify_dividend_baidu
- 停复牌：akshare news_trade_notify_suspend_baidu
- CCTV 新闻：akshare news_cctv
- 财新要闻：akshare stock_news_main_cx

说明：东方财富个股新闻搜索接口（stock_news_em）在当前环境不可用
（pyarrow 正则 bug + 接口仅返回用户主页），个股新闻用公告接口替代。
"""
from __future__ import annotations

from datetime import date


def get_stock_announcements(
    code: str, page_size: int = 50, days: int | None = None
) -> list[dict]:
    """获取个股公告时间线（东方财富公告接口）。

    参数：
        page_size: 取回条数（接口上限约 100，超出会被服务端截断）。
        days: 只要最近 N 天内的公告；None 表示不按时间过滤。

    返回字段：title, time, source, url, content
    """
    from stock_plan.data.fetcher import DataFetcher

    return DataFetcher().get_news(code, page_size=page_size, days=days)


def resolve_stock(text: str) -> tuple[str | None, str | None, list[tuple[str, str]]]:
    """把用户输入的"股票代码或名称"解析为 (代码, 名称, 候选列表)。

    规则：
    - 6 位纯数字 → 直接当代码用，名称从本地列表补齐
    - 否则按名称匹配：先精确匹配，再子串匹配
    - 子串命中多只时返回候选，由调用方让用户选，不猜（避免查错股票）
    - 本地列表不可用时，6 位数字仍可用，名称无法解析
    """
    from stock_plan.data.storage import Storage

    text = (text or "").strip()
    if not text:
        return None, None, []

    try:
        listing = Storage().load_stock_list()
    except Exception:
        listing = None

    if listing is None or listing.empty:
        return (text if (text.isdigit() and len(text) == 6) else None), None, []

    codes = listing["code"].astype(str)
    names = listing["name"].astype(str)

    if text.isdigit() and len(text) == 6:
        hit = listing[codes == text]
        return text, (str(hit["name"].iloc[0]) if not hit.empty else None), []

    exact = listing[names == text]
    if not exact.empty:
        row = exact.iloc[0]
        return str(row["code"]), str(row["name"]), []

    part = listing[names.str.contains(text, case=False, na=False, regex=False)]
    if part.empty:
        return None, None, []

    candidates = [(str(r["code"]), str(r["name"])) for _, r in part.head(20).iterrows()]
    if len(candidates) == 1:
        return candidates[0][0], candidates[0][1], []
    return None, None, candidates


def get_market_news(limit: int = 50) -> list[dict]:
    """获取全球市场要闻（东方财富全球财经快讯）。

    返回字段：title, summary, time, url
    """
    import akshare as ak

    try:
        df = ak.stock_info_global_em()
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.head(limit).iterrows():
            result.append(
                {
                    "title": row.get("标题", ""),
                    "summary": row.get("摘要", ""),
                    "time": str(row.get("发布时间", "")),
                    "url": row.get("链接", ""),
                }
            )
        return result
    except Exception:
        return []


def get_economic_calendar(d: date | None = None) -> list[dict]:
    """获取财经日历（百度财经日历）。

    返回字段：date, time, region, event, importance, actual, forecast, previous
    """
    import akshare as ak

    d = d or date.today()
    try:
        df = ak.news_economic_baidu(date=d.strftime("%Y%m%d"))
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append(
                {
                    "date": str(row.get("日期", "")),
                    "time": str(row.get("时间", "")),
                    "region": row.get("地区", ""),
                    "event": row.get("事件", ""),
                    "importance": row.get("重要性", ""),
                    "actual": row.get("公布", ""),
                    "forecast": row.get("预期", ""),
                    "previous": row.get("前值", ""),
                }
            )
        return result
    except Exception:
        return []


def get_dividend_notices(d: date | None = None) -> list[dict]:
    """获取分红送转公告（百度）。

    返回字段：code, name, ex_date, dividend, bonus, transfer, exchange
    """
    import akshare as ak

    d = d or date.today()
    try:
        df = ak.news_trade_notify_dividend_baidu(date=d.strftime("%Y%m%d"))
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append(
                {
                    "code": str(row.get("股票代码", "")),
                    "name": row.get("股票简称", ""),
                    "ex_date": str(row.get("除权日", "")),
                    "dividend": row.get("分红", ""),
                    "bonus": row.get("送股", ""),
                    "transfer": row.get("转增", ""),
                    "exchange": row.get("交易所", ""),
                }
            )
        return result
    except Exception:
        return []


def get_suspend_notices(d: date | None = None) -> list[dict]:
    """获取停复牌公告（百度）。

    返回字段：code, name, suspend_time, resume_time, reason
    """
    import akshare as ak

    d = d or date.today()
    try:
        df = ak.news_trade_notify_suspend_baidu(date=d.strftime("%Y%m%d"))
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append(
                {
                    "code": str(row.get("股票代码", "")),
                    "name": row.get("股票简称", ""),
                    "suspend_time": str(row.get("停牌时间", "")),
                    "resume_time": str(row.get("复牌时间", "")),
                    "reason": row.get("停牌事项说明", ""),
                }
            )
        return result
    except Exception:
        return []


def get_cctv_news(d: date | None = None) -> list[dict]:
    """获取 CCTV 新闻联播内容摘要。

    返回字段：date, title, content
    """
    import akshare as ak

    d = d or date.today()
    try:
        df = ak.news_cctv(date=d.strftime("%Y%m%d"))
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.iterrows():
            result.append(
                {
                    "date": str(row.get("date", "")),
                    "title": row.get("title", ""),
                    "content": row.get("content", ""),
                }
            )
        return result
    except Exception:
        return []


def get_caixin_news(limit: int = 30) -> list[dict]:
    """获取财新要闻。

    返回字段：tag, summary, url
    """
    import akshare as ak

    try:
        df = ak.stock_news_main_cx()
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.head(limit).iterrows():
            result.append(
                {
                    "tag": row.get("tag", ""),
                    "summary": row.get("summary", ""),
                    "url": row.get("url", ""),
                }
            )
        return result
    except Exception:
        return []