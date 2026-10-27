"""新增数据层（分类快照/资金流/情绪/公告）的离线测试。

为什么要有这一组：这四个模块都是"抓网络 + 落盘 + 再读回来给 UI 用"的结构，
真正容易错的地方不是接口调用本身（那部分只能靠联网验收），而是
**单位换算、时点边界、去重、以及缺数据时该不该静默降级**这四类纯逻辑。
这些逻辑一旦错，页面上会显示"看起来正常但差 7 个数量级"的数字，
比直接报错危险得多。所以这里全部用合成数据把边界钉死，不碰网络。
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from stock_plan.data import announcements as ann
from stock_plan.data import classification as cls
from stock_plan.data import market_snapshot as ms
from stock_plan.factors import emotion as emo


# ---------------------------------------------------------------- 单位换算
def test_parse_money_把中文单位换算成元():
    assert ms.parse_money("2.07亿") == pytest.approx(2.07e8)
    assert ms.parse_money("-4913.57万") == pytest.approx(-4.91357e7)
    assert ms.parse_money("1234.5") == pytest.approx(1234.5)
    assert ms.parse_money("--") != ms.parse_money("--")  # NaN


def test_parse_pct_去掉百分号():
    assert ms.parse_pct("20.01%") == pytest.approx(20.01)
    assert ms.parse_pct("-3.50%") == pytest.approx(-3.5)
    assert ms.parse_pct("3.5") == pytest.approx(3.5)


def test_money_series_数值列按亿元字符串列按带单位解析():
    """三张资金流表的单位不一致是这个模块最大的坑：行业/概念表是 float 亿元，
    个股表是 '1798.11万' 这种字符串。若统一按元处理，同屏比较会差 7 个数量级。"""
    floats = ms._money_series(pd.Series([1.0, -2.0]))
    assert list(floats) == pytest.approx([1e8, -2e8])

    strings = ms._money_series(pd.Series(["1亿", "-20000万"]))
    assert list(strings) == pytest.approx([1e8, -2e8])


# ---------------------------------------------------------------- 快照往返
def test_快照落盘再读回_长度与目录一致(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "MARKET_DIR", tmp_path / "market")
    frames = {
        ms.LIMIT_UP: pd.DataFrame({"code": ["600000", "000001"], "boards": [1, 2]}),
        ms.FUND_STK: pd.DataFrame({"code": ["600000"], "net": [1.0], "pct": [1.5]}),
    }
    paths = ms.save_day("2026-10-27", frames)
    assert len(paths) == 2

    back = ms.load_day("2026-10-27")
    assert len(back[ms.LIMIT_UP]) == 2
    assert len(back[ms.FUND_STK]) == 1
    assert "2026-10-27" in ms.available_days()


def test_读不存在的日期返回空字典而不是抛异常(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "MARKET_DIR", tmp_path / "market")
    assert ms.load_day("1999-01-01") == {}


# ---------------------------------------------------------------- 情绪指标
def _fake_day(up_boards, n_zhaban, stk_rows):
    up = pd.DataFrame(
        {
            "code": [f"6{i:05d}" for i in range(len(up_boards))],
            "boards": up_boards,
            "seal_fund": [1e7] * len(up_boards),
        }
    )
    zb = pd.DataFrame({"code": [f"0{i:05d}" for i in range(n_zhaban)]})
    stk = pd.DataFrame(stk_rows)
    return {ms.LIMIT_UP: up, ms.ZHABAN: zb, ms.FUND_STK: stk}


def test_炸板率分母同时含涨停与炸板(monkeypatch):
    """炸板率 = 炸板 /（涨停 + 炸板）。若写成 炸板/涨停（=0.5），就系统性高估情绪。

    这里 30 涨停 / 10 炸板，正确值 0.25，错的写法会得 0.333。
    """
    day = _fake_day([1] * 30, 10, {"pct": [1.0], "net": [1.0]})
    monkeypatch.setattr(ms, "load_day", lambda d=None: day)
    stats = emo.limit_stats("2026-10-27")
    assert stats["limit_up"] == 30
    assert stats["zhaban"] == 10
    assert stats["break_rate"] == pytest.approx(0.25)
    assert stats["max_boards"] == 1
    assert stats["up_down_ratio"] == math.inf  # 跌停 0 家


def test_连板梯队按板数分组(monkeypatch):
    day = _fake_day([1, 1, 2, 3, 3], 0, {"pct": [1.0], "net": [1.0]})
    monkeypatch.setattr(ms, "load_day", lambda d=None: day)
    stats = emo.limit_stats("2026-10-27")
    assert stats["ladder"] == {1: 2, 2: 1, 3: 2}
    assert stats["max_boards"] == 3


def test_个股广度用中位数净额而不是排名分位(monkeypatch):
    """`net_pct` 是排名分位，其中位数按构造恒 ≈0.5，与市场冷暖无关；
    曾经用它当广度指标，等于一个空转数字。这里要求返回真实净额中位数。"""
    stk = {
        "pct": [1.0, -2.0, 3.0, 0.0],
        "net": [1e7, -2e7, 3e7, 4e7],
    }
    monkeypatch.setattr(ms, "load_day", lambda d=None: _fake_day([1], 0, stk))
    breadth = emo.flow_breadth("2026-10-27")
    assert breadth["n_stocks"] == 4
    assert breadth["up_share"] == pytest.approx(0.5)  # 只有 >0 才算上涨
    assert breadth["inflow_share"] == pytest.approx(0.75)
    assert breadth["median_net"] == pytest.approx((1e7 + 3e7) / 2)


def test_无快照时情绪模块返回零值而不是崩溃(monkeypatch):
    monkeypatch.setattr(ms, "load_day", lambda d=None: {})
    stats = emo.limit_stats("2026-10-27")
    assert stats["limit_up"] == 0 and stats["zhaban"] == 0
    assert math.isnan(stats["break_rate"])
    breadth = emo.flow_breadth("2026-10-27")
    assert breadth["n_stocks"] == 0


# ---------------------------------------------------------------- 分类时点
def _sw_frame(rows):
    return pd.DataFrame(rows, columns=["code", "name", "sw_l1", "sw_l2", "sw_l3", "incl_date"])


def test_分类快照按月隔离_不跨月串味(tmp_path, monkeypatch):
    """时点快照的全部意义就是"某个日期能看到什么"。若读的是最新月份，
    历史回看就会用到未来才生效的行业归属。"""
    monkeypatch.setattr(cls, "CLASSIFY_DIR", tmp_path / "classify")
    cls.save_month(
        "2026-09",
        {cls.INDUSTRY_SW: _sw_frame([["000001", "平安银行", "银行", "股份制银行", "", "2020-01-01"]])},
    )
    cls.save_month(
        "2026-10",
        {cls.INDUSTRY_SW: _sw_frame([["000001", "平安银行", "食品饮料", "白酒", "", "2026-10-01"]])},
    )

    assert cls.industry_of("000001", month="2026-09")["sw_l1"] == "银行"
    assert cls.industry_of("000001", month="2026-10")["sw_l1"] == "食品饮料"
    assert cls.latest_month() == "2026-10"
    assert cls.available_months() == ["2026-09", "2026-10"]


def test_按行业筛选只保留命中且不改顺序(tmp_path, monkeypatch):
    monkeypatch.setattr(cls, "CLASSIFY_DIR", tmp_path / "classify")
    cls.save_month(
        "2026-10",
        {
            cls.INDUSTRY_SW: _sw_frame(
                [
                    ["600036", "招商银行", "银行", "股份制银行", "", "2020-01-01"],
                    ["600519", "贵州茅台", "食品饮料", "白酒", "", "2020-01-01"],
                    ["000001", "平安银行", "银行", "股份制银行", "", "2020-01-01"],
                ]
            )
        },
    )
    codes = ["600519", "000001", "600036"]
    assert cls.filter_codes_by_industries(codes, ["银行"], month="2026-10") == ["000001", "600036"]


def test_没有分类数据时按行业筛选不把池子清空(tmp_path, monkeypatch):
    """缺快照时若返回空列表，用户看到的是"今天没选出任何票"，
    会被误读成策略失效——所以降级为"不过滤"并保持原顺序。"""
    monkeypatch.setattr(cls, "CLASSIFY_DIR", tmp_path / "classify")
    codes = ["600519", "000001"]
    assert cls.filter_codes_by_industries(codes, ["银行"], month="2026-10") == codes
    assert cls.filter_codes_by_industries(codes, None, month="2026-10") == codes


# ---------------------------------------------------------------- 公告存档
def test_公告入库按主键去重(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "ANN_DB", tmp_path / "announcements.db")
    df = pd.DataFrame(
        {
            "code": ["600000", "600000"],
            "ann_date": ["2026-10-26", "2026-10-26"],
            "title": ["关于回购的公告", "关于回购的公告"],
            "ann_type": ["回购", "回购"],
            "url": ["http://x/1", "http://x/1"],
        }
    )
    assert ann.save(df) == 1  # 同主键只算 1 条新增
    assert ann.save(df) == 0  # 再存一次是 0 条新增

    rows = ann.load("600000", since="2026-10-01")
    assert len(rows) == 1
    assert ann.stats()["rows"] == 1


def test_非法代码在入口就报错(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "ANN_DB", tmp_path / "announcements.db")
    with pytest.raises(ValueError):
        ann.norm_code("abc")
    assert ann.norm_code("600000") == "600000"


def test_关注清单增删查(tmp_path, monkeypatch):
    monkeypatch.setattr(ann, "ANN_DB", tmp_path / "announcements.db")
    ann.add_watch("600000", note="底仓")
    ann.add_watch("000001")
    assert ann.watch_codes() == ["600000", "000001"]  # 按加入时间先后
    assert ann.remove_watch("000001") is True
    assert ann.remove_watch("000001") is False
    assert ann.watch_codes() == ["600000"]
