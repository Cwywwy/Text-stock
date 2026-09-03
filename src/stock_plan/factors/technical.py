"""技术因子计算模块。

输入：单只股票的日线 K 线（DataFrame，含 date/open/high/low/close/volume/amount/turnover）。
输出：在原始数据上追加技术因子列，供策略打分使用。

新增列说明：
- ma5 / ma10 / ma20 / ma60   简单移动平均线
- macd / macd_signal         MACD 指标（快线 EMA12 - 慢线 EMA26，信号线为 MACD 的 EMA9）
- rsi14                      相对强弱指标（14 日）
- atr14                      平均真实波幅（14 日），用于计算目标买卖价与止损
- vol_ratio                  量比（当日成交量 / 5 日均量）
- trend_score                趋势综合分（0-100），数值越高代表趋势越强

说明：新浪日线自带的 turnover 列就是换手率，因此不再重复计算。
"""
from __future__ import annotations

import pandas as pd

# 技术因子计算所需的列
REQUIRED_COLUMNS = ["date", "open", "high", "low", "close", "volume", "amount", "turnover"]


def _ema(series: pd.Series, span: int) -> pd.Series:
    """指数移动平均（EMA）。adjust=False 表示从第一个数据点开始计算。"""
    return series.ewm(span=span, adjust=False).mean()


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """相对强弱指标 RSI。

    公式：RSI = 100 - 100 / (1 + RS)，其中 RS = 平均涨幅 / 平均跌幅。
    平均涨幅/跌幅用 EMA 平滑（Wilder 方法）。
    """
    delta = close.diff()
    gain = delta.clip(lower=0)  # 只保留上涨部分
    loss = -delta.clip(upper=0)  # 只保留下跌部分（取正）
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)  # 避免除零
    rsi = 100 - 100 / (1 + rs)
    return rsi.fillna(50)  # 无数据时用中性值 50


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """平均真实波幅 ATR。

    真实波幅 TR = max(最高-最低, |最高-昨收|, |最低-昨收|)。
    ATR 用 TR 的 EMA 平滑。
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period).mean()


def _trend_score(df: pd.DataFrame) -> pd.Series:
    """趋势综合分（0-100）。

    打分规则（各维度独立加分，满分 100）：
    - 均线多头排列（ma5 > ma10 > ma20 > ma60）：+40
    - 收盘价站上 ma20：+20
    - MACD 在零轴上方且金叉（macd > macd_signal）：+20
    - RSI 处于 50-70 强势区间（未超买）：+20
    """
    close = df["close"]
    ma5, ma10, ma20, ma60 = df["ma5"], df["ma10"], df["ma20"], df["ma60"]

    score = pd.Series(0.0, index=df.index)

    # 1. 均线多头排列（+40）
    bull_align = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
    score += bull_align * 40

    # 2. 收盘价站上 ma20（+20）
    score += (close > ma20) * 20

    # 3. MACD 零轴上方且金叉（+20）
    macd_ok = (df["macd"] > 0) & (df["macd"] > df["macd_signal"])
    score += macd_ok * 20

    # 4. RSI 强势区间（+20）
    rsi_ok = (df["rsi14"] >= 50) & (df["rsi14"] <= 70)
    score += rsi_ok * 20

    return score


def compute_technical(df: pd.DataFrame) -> pd.DataFrame:
    """计算技术因子，返回追加因子列后的 DataFrame。

    参数：
        df: 日线 K 线，需含 date/open/high/low/close/volume/amount/turnover 列。

    返回：
        追加 ma5/ma10/ma20/ma60/macd/macd_signal/rsi14/atr14/vol_ratio/trend_score 列的副本。
    """
    if df is None or df.empty:
        return df.copy()

    result = df.copy()
    close = result["close"]

    # 均线
    result["ma5"] = close.rolling(5).mean()
    result["ma10"] = close.rolling(10).mean()
    result["ma20"] = close.rolling(20).mean()
    result["ma60"] = close.rolling(60).mean()

    # MACD（12/26/9 标准参数）
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    result["macd"] = ema12 - ema26
    result["macd_signal"] = _ema(result["macd"], 9)

    # RSI 与 ATR
    result["rsi14"] = _rsi(close, 14)
    result["atr14"] = _atr(result, 14)

    # 量比：当日成交量 / 5 日均量
    result["vol_ratio"] = result["volume"] / result["volume"].rolling(5).mean()

    # 趋势综合分（依赖上面各列，需最后计算）
    result["trend_score"] = _trend_score(result)

    return result


if __name__ == "__main__":
    # 简单自测：用浦发银行缓存数据计算技术因子
    from stock_plan.data.storage import Storage

    storage = Storage()
    bars = storage.load_bars("600000")
    if bars.empty:
        print("无缓存数据，请先运行 fetch_all")
    else:
        factored = compute_technical(bars)
        print("原始列数:", len(bars.columns), "→ 因子后列数:", len(factored.columns))
        print("新增列:", [c for c in factored.columns if c not in bars.columns])
        print(factored.tail(3).to_string())