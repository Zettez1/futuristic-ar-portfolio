from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @classmethod
    def from_list(cls, row):
        return cls(ts=int(row[0]), open=float(row[1]), high=float(row[2]), low=float(row[3]), close=float(row[4]), volume=float(row[5]))


@dataclass
class Signal:
    symbol: str
    side: str
    entry: float
    stop_loss: float
    take_profit: Optional[float]
    confidence: float
    strategy: str
    timeframe: str
    reason: str = ""
    features: dict = field(default_factory=dict)

    def risk_pct(self) -> float:
        if self.entry <= 0:
            return 0.0
        return abs(self.entry - self.stop_loss) / self.entry


@dataclass
class Position:
    id: str
    symbol: str
    side: str
    entry: float
    qty: float
    stop_loss: float
    take_profit: Optional[float]
    strategy: str
    opened_at: float
    features: dict = field(default_factory=dict)
    realized_pnl: float = 0.0
    status: str = "open"
    fees: float = 0.0


@dataclass
class MarketSnapshot:
    symbol: str
    klines: dict
    ticker: dict = field(default_factory=dict)
    orderbook: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    flow: dict = field(default_factory=dict)
    sentiment: dict = field(default_factory=dict)
    onchain: dict = field(default_factory=dict)
    last_price: float = 0.0

    @property
    def main_klines(self) -> list:
        return self.klines.get("15m", [])
