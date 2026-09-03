"""数据获取模块 — AKShare 封装。

数据源说明（2026-09-03 实测）：
- 新浪接口可用：股票列表 stock_zh_a_spot、日线 stock_zh_a_daily、财务摘要 stock_financial_abstract
- 东方财富 / 上交所接口在当前网络环境不可用（连接被断开），故统一走新浪源

注意：Windows 控制台默认 GBK 编码，打印中文列名会乱码，但数据本身正常。
运行脚本时建议设置环境变量 PYTHONIOENCODING=utf-8。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import akshare as ak
import pandas as pd


def _to_sina_symbol(code: str) -> str:
    """把 6 位数字代码转成新浪格式（带交易所前缀）。

    规则：
    - 6 开头 → 沪市主板/科创板（sh）
    - 0/3 开头 → 深市主板/创业板（sz）
    - 4/8 开头 → 北交所老代码（bj）
    - 9 开头 → 北交所新代码 920xxx（bj），注意 A 股列表不含沪市 B 股 900xxx
    """
    code = code.strip()
    if code.startswith(("0", "3")):
        return f"sz{code}"
    if code.startswith(("4", "8", "9")):
        return f"bj{code}"
    return f"sh{code}"


def _strip_prefix(symbol: str) -> str:
    """去掉新浪代码前缀，返回 6 位数字代码。如 'bj920000' → '920000'。"""
    return symbol[-6:]


class DataFetcher:
    """AKShare 数据获取封装，统一返回标准化字段。"""

    def get_stock_list(self) -> pd.DataFrame:
        """获取全 A 股代码列表。

        返回字段：code（6位数字）, name, industry, list_date, is_st
        说明：新浪实时行情接口不含行业/上市日期，industry 与 list_date 暂为空，
        is_st 通过名称是否含 'ST' 判断。
        """
        df = ak.stock_zh_a_spot()  # 新浪全 A 股实时行情
        result = pd.DataFrame(
            {
                "code": df["代码"].map(_strip_prefix),
                "name": df["名称"],
                "industry": "",
                "list_date": pd.NaT,
                "is_st": df["名称"].str.contains("ST", case=False, na=False),
            }
        )
        return result

    def get_daily_bars(
        self, code: str, start: date | str, end: date | str
    ) -> pd.DataFrame:
        """获取单只股票日线行情（前复权）。

        返回字段：date, open, high, low, close, volume, amount, turnover
        """
        symbol = _to_sina_symbol(code)
        start_str = start.strftime("%Y%m%d") if isinstance(start, date) else start
        end_str = end.strftime("%Y%m%d") if isinstance(end, date) else end
        df = ak.stock_zh_a_daily(
            symbol=symbol, start_date=start_str, end_date=end_str, adjust="qfq"
        )
        if df is None or df.empty:
            return pd.DataFrame(
                columns=["date", "open", "high", "low", "close", "volume", "amount", "turnover"]
            )
        # 新浪日线字段：date, open, high, low, close, volume, amount, outstanding_share, turnover
        result = df[["date", "open", "high", "low", "close", "volume", "amount", "turnover"]].copy()
        result["date"] = pd.to_datetime(result["date"])
        return result

    def get_fundamentals(self, code: str, latest_price: float | None = None) -> dict:
        """获取最新一期财务指标。

        返回字段：
        - roe 净资产收益率(ROE)
        - revenue_growth 营业总收入增长率
        - net_profit_growth 归属母公司净利润增长率
        - gross_margin 毛利率
        - debt_ratio 资产负债率
        - eps 基本每股收益
        - bvps 每股净资产
        - 若提供 latest_price，额外计算 pe（市盈率）、pb（市净率）
        """
        df = ak.stock_financial_abstract(symbol=code)
        # 结构：指标 × 报告期，第 2 列起为各报告期，取最新一期（第 2 列）
        indicator_col = df.columns[1]
        latest_col = df.columns[2]
        # 用指标名定位（指标名有重复，取第一个匹配）
        def get_value(indicator_name: str) -> float | None:
            mask = df[indicator_col] == indicator_name
            if mask.any():
                val = df.loc[mask, latest_col].iloc[0]
                return None if pd.isna(val) else float(val)
            return None

        result = {
            "code": code,
            "roe": get_value("净资产收益率(ROE)"),
            "revenue_growth": get_value("营业总收入增长率"),
            "net_profit_growth": get_value("归属母公司净利润增长率"),
            "gross_margin": get_value("毛利率"),
            "debt_ratio": get_value("资产负债率"),
            "eps": get_value("基本每股收益"),
            "bvps": get_value("每股净资产"),
        }
        if latest_price is not None:
            result["pe"] = (
                latest_price / result["eps"] if result["eps"] else None
            )
            result["pb"] = (
                latest_price / result["bvps"] if result["bvps"] else None
            )
        return result

    def get_news(self, code: str, days: int = 7) -> list[dict]:
            """获取个股近期公告/新闻（东方财富公告接口）。

            返回字段：title, time, source, url, content
            说明：东方财富搜索新闻接口在当前环境不可用（仅返回用户主页），
            改用公告接口（np-anotice-stock）获取个股公告作为新闻时间线数据源。
            """
            import requests

            url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
            params = {
                "sr": "-1",
                "page_size": "20",
                "page_index": "1",
                "ann_type": "A",
                "client_source": "web",
                "stock_list": code,
            }
            headers = {
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
                "referer": f"https://data.eastmoney.com/notices/detail/{code}.html",
            }
            try:
                r = requests.get(url, params=params, headers=headers, timeout=10)
                data = r.json()
                items = data.get("data", {}).get("list", []) or []
                result = []
                for it in items:
                    result.append(
                        {
                            "title": it.get("title", ""),
                            "time": it.get("notice_date", ""),
                            "source": "东方财富公告",
                            "url": it.get("art_code", ""),
                            "content": it.get("columns", [{}])[0].get("column_name", "")
                            if it.get("columns") else "",
                        }
                    )
                return result
            except Exception:
                return []


def get_latest_price(code: str) -> float | None:
    """获取单只股票最新价（用于估值计算）。"""
    df = ak.stock_zh_a_spot()
    row = df[df["代码"].map(_strip_prefix) == code]
    if row.empty:
        return None
    return float(row.iloc[0]["最新价"])


if __name__ == "__main__":
    # 简单自测：拉取浦发银行日线与财务数据
    fetcher = DataFetcher()
    bars = fetcher.get_daily_bars("600000", date(2025, 1, 1), date(2025, 1, 31))
    print("日线条数:", len(bars))
    print(bars.head(3).to_string())
    fin = fetcher.get_fundamentals("600000", latest_price=10.0)
    print("财务指标:", fin)