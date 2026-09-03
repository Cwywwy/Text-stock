"""板块划分模块 — 按股票代码前缀划分交易所板块。

板块划分（A 股通用规则）：
- 主板：沪市 60 开头、深市 00 开头
- 创业板：30 开头（300/301/302）
- 科创板：68 开头（688/689）
- 北交所：82/83/87/43/920 开头

用于选股入口的板块自定义筛选（R5 需求）。
"""
from __future__ import annotations

import pandas as pd

# 板块名 → 代码前缀元组
BOARDS: dict[str, tuple[str, ...]] = {
    "主板": ("60", "00"),
    "创业板": ("30",),
    "科创板": ("68",),
    "北交所": ("82", "83", "87", "43", "92"),
}

BOARD_DESC: dict[str, str] = {
    "主板": "沪深主板，大中型成熟企业，涨跌幅限制 ±10%",
    "创业板": "深市创业板，成长型创新企业，涨跌幅限制 ±20%",
    "科创板": "沪市科创板，硬科技企业，涨跌幅限制 ±20%",
    "北交所": "北京证券交易所，专精特新中小企业，涨跌幅限制 ±30%",
}


def board_of(code: str) -> str:
    """返回股票所属板块名；无法识别时返回「其他」。"""
    code = str(code).split(".")[0]
    for board, prefixes in BOARDS.items():
        if any(code.startswith(p) for p in prefixes):
            return board
    return "其他"


def filter_codes_by_boards(codes: list[str], boards: list[str] | None) -> list[str]:
    """按板块过滤代码列表。boards 为 None/空列表 表示不过滤（但空列表在 UI 层默认全选）。"""
    if not boards or set(boards) >= set(BOARDS):
        return list(codes)
    return [c for c in codes if board_of(c) in boards]


def filter_universe_ui(
    stock_list: pd.DataFrame,
    bars_map: dict[str, pd.DataFrame],
    boards: list[str] | None = None,
    exclude_st: bool = True,
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """UI 板块筛选 + 剔除 ST 的统一入口（在策略硬过滤之前执行）。

    参数：
        stock_list: 全 A 股列表（含 code/name/is_st 列）。
        bars_map:   {code: 日线 DataFrame}。
        boards:     保留的板块列表；None 或全选表示不过滤。
        exclude_st: True 时剔除 ST/*ST 股票。

    返回：
        (过滤后的 stock_list, 过滤后的 bars_map)
    """
    sl = stock_list
    bm = bars_map
    if exclude_st and "is_st" in sl.columns:
        st_codes = set(sl.loc[sl["is_st"] == 1, "code"].astype(str))
        sl = sl[sl["is_st"] != 1]
        bm = {c: df for c, df in bm.items() if c not in st_codes}
    if boards and set(boards) != set(BOARDS):
        keep = set(filter_codes_by_boards(list(bm), boards))
        bm = {c: df for c, df in bm.items() if c in keep}
    return sl, bm


if __name__ == "__main__":
    samples = ["600000", "000001", "300750", "301236", "688981", "832000", "920001"]
    for s in samples:
        print(s, "→", board_of(s))
