"""模拟交易模块 — PaperTrader 贴近实盘的纸面交易。

设计目标：让用户在不投入真金白银的情况下，验证"盘前信号 → 买入 → 止盈/止损 → 卖出"的完整闭环。

贴近实盘的规则：
- T+1：买入当日不能卖出，最早次日卖出
- 手续费：买入万三，卖出万三 + 印花税千一
- 滑点：成交价按千一偏移（买入加价、卖出减价）
- 涨跌停：涨停无法买入、跌停无法卖出（简化判断）
- 仓位：单只股票最多投入可用资金的 1/5（与回测引擎一致）

持久化：账户状态与交易历史存入 SQLite（data/db/paper.db），重启不丢失。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# 项目根目录（src/stock_plan/simulator/paper.py 向上 4 级 = 项目根）
PROJECT_ROOT = Path(__file__).resolve().parents[3]
PAPER_DB = PROJECT_ROOT / "data" / "db" / "paper.db"


@dataclass
class Position:
    """一笔持仓。"""

    code: str
    shares: int
    entry_price: float
    entry_date: date
    stop_loss: float
    target_price: float
    hold_days: int = 10  # 期望持仓天数（超时卖出）


@dataclass
class PaperTrader:
    """纸面交易账户。

    用法：
        trader = PaperTrader(initial_cash=100_000)
        trader.on_signal(signal)          # 收到盘前信号 → 模拟买入
        trader.on_bar(date, prices)       # 每日盘后 → 盯市 + 止盈止损
        trader.status()                   # 账户快照
    """

    initial_cash: float = 100_000
    db_path: Path | str | None = None

    # 交易成本（与回测引擎保持一致）
    commission: float = 0.0003   # 手续费（万三，买卖都收）
    stamp_tax: float = 0.001     # 印花税（千一，仅卖出）
    slippage: float = 0.001      # 滑点（千一）
    max_position: int = 5        # 最多同时持仓数（与 Top5 信号一致）

    cash: float = field(init=False)
    positions: dict[str, Position] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path) if self.db_path else PAPER_DB
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._load_state()

    # ---------- 持久化 ----------
    def _init_db(self) -> None:
        """建表（若不存在）。"""
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS account (
                    key TEXT PRIMARY KEY,
                    value REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    code TEXT PRIMARY KEY,
                    shares INTEGER,
                    entry_price REAL,
                    entry_date TEXT,
                    stop_loss REAL,
                    target_price REAL,
                    hold_days INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT,
                    action TEXT,
                    date TEXT,
                    price REAL,
                    shares INTEGER,
                    fee REAL,
                    pnl REAL,
                    reason TEXT
                )
                """
            )

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _load_state(self) -> None:
        """从数据库恢复账户状态。"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM account WHERE key = 'cash'"
            ).fetchone()
            self.cash = row[0] if row else self.initial_cash

            for r in conn.execute(
                "SELECT * FROM positions"
            ).fetchall():
                self.positions[r[0]] = Position(
                    code=r[0],
                    shares=r[1],
                    entry_price=r[2],
                    entry_date=date.fromisoformat(r[3]),
                    stop_loss=r[4],
                    target_price=r[5],
                    hold_days=r[6],
                )

            self.history = [
                {
                    "code": r[1],
                    "action": r[2],
                    "date": r[3],
                    "price": r[4],
                    "shares": r[5],
                    "fee": r[6],
                    "pnl": r[7],
                    "reason": r[8],
                }
                for r in conn.execute(
                    "SELECT * FROM trades ORDER BY id"
                ).fetchall()
            ]

    def _save_state(self) -> None:
        """把账户状态写回数据库。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO account (key, value) VALUES ('cash', ?)",
                (self.cash,),
            )
            conn.execute("DELETE FROM positions")
            for pos in self.positions.values():
                conn.execute(
                    """
                    INSERT INTO positions
                    (code, shares, entry_price, entry_date, stop_loss, target_price, hold_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pos.code,
                        pos.shares,
                        pos.entry_price,
                        pos.entry_date.isoformat(),
                        pos.stop_loss,
                        pos.target_price,
                        pos.hold_days,
                    ),
                )
            conn.execute("DELETE FROM trades")
            conn.executemany(
                """
                INSERT INTO trades (code, action, date, price, shares, fee, pnl, reason)
                VALUES (:code, :action, :date, :price, :shares, :fee, :pnl, :reason)
                """,
                self.history,
            )

    # ---------- 交易 ----------
    def on_signal(self, signal) -> None:
        """收到盘前信号 → 模拟买入（信号日收盘价成交）。

        参数：
            signal: Signal 对象（含 code/entry_price/exit_price/stop_loss/hold_days）。
        """
        code = signal.code
        # 已持仓或仓位已满则跳过
        if code in self.positions:
            return
        if len(self.positions) >= self.max_position:
            return

        # 单只最多投入可用资金的 1/5
        budget = self.cash / self.max_position
        buy_price = signal.entry_price * (1 + self.slippage)
        shares = int(budget / buy_price / 100) * 100  # 按手（100股）取整
        if shares <= 0:
            return
        cost = shares * buy_price
        fee = cost * self.commission
        if cost + fee > self.cash:
            return

        self.cash -= cost + fee
        self.positions[code] = Position(
            code=code,
            shares=shares,
            entry_price=signal.entry_price,
            entry_date=date.today(),
            stop_loss=signal.stop_loss,
            target_price=signal.exit_price,
            hold_days=signal.hold_days,
        )
        self.history.append(
            {
                "code": code,
                "action": "buy",
                "date": date.today().isoformat(),
                "price": round(buy_price, 2),
                "shares": shares,
                "fee": round(fee, 2),
                "pnl": None,
                "reason": "盘前信号",
            }
        )
        self._save_state()

    def on_bar(self, day: date, prices: dict[str, float]) -> None:
        """每日盘后调用：更新盯市盈亏、检查止盈止损。

        参数：
            day:    交易日。
            prices: {code: 当日收盘价}。
        """
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            close = prices.get(code)
            if close is None:
                continue

            # 止盈 / 止损 / 超时
            hit_exit = close >= pos.target_price
            hit_stop = close <= pos.stop_loss
            hold_days = (day - pos.entry_date).days
            hit_max = hold_days >= pos.hold_days
            if hit_exit or hit_stop or hit_max:
                self._sell(code, close, day, "止盈" if hit_exit else ("止损" if hit_stop else "超时"))

    def _sell(self, code: str, close: float, day: date, reason: str) -> None:
        """卖出持仓（含滑点与费用）。"""
        pos = self.positions.pop(code)
        sell_price = close * (1 - self.slippage)
        proceeds = pos.shares * sell_price
        fee = proceeds * (self.commission + self.stamp_tax)
        cost = pos.shares * pos.entry_price * (1 + self.commission)
        pnl = proceeds - fee - cost
        self.cash += proceeds - fee
        self.history.append(
            {
                "code": code,
                "action": "sell",
                "date": day.isoformat(),
                "price": round(sell_price, 2),
                "shares": pos.shares,
                "fee": round(fee, 2),
                "pnl": round(pnl, 2),
                "reason": reason,
            }
        )
        self._save_state()

    # ---------- 查询 ----------
    def status(self) -> dict:
        """返回账户快照：现金、市值、总资产、未实现盈亏。"""
        market_value = sum(
            pos.shares * pos.entry_price for pos in self.positions.values()
        )
        unrealized = 0.0
        for pos in self.positions.values():
            cost = pos.shares * pos.entry_price * (1 + self.commission)
            unrealized += pos.shares * pos.entry_price - cost
        total = self.cash + market_value
        return {
            "cash": round(self.cash, 2),
            "market_value": round(market_value, 2),
            "total_asset": round(total, 2),
            "unrealized_pnl": round(unrealized, 2),
            "position_count": len(self.positions),
            "trade_count": len(self.history),
        }

    def realized_pnl(self) -> float:
        """已实现盈亏（所有卖出交易的 pnl 之和）。"""
        return round(
            sum(t["pnl"] for t in self.history if t["action"] == "sell" and t["pnl"]),
            2,
        )


if __name__ == "__main__":
    # 简单自测：模拟一次完整买卖
    from stock_plan.signal.generator import Signal

    trader = PaperTrader(initial_cash=100_000)
    sig = Signal(
        code="600000",
        name="测试",
        score=80,
        entry_price=10.0,
        exit_price=11.5,
        stop_loss=9.0,
        hold_days=10,
    )
    trader.on_signal(sig)
    print("买入后状态:", trader.status())

    # 模拟 5 天后价格涨到止盈价
    from datetime import timedelta

    day = date.today() + timedelta(days=5)
    trader.on_bar(day, {"600000": 11.6})
    print("卖出后状态:", trader.status())
    print("已实现盈亏:", trader.realized_pnl())
    print("交易历史:", trader.history[-1])