from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class SignalStrength(str, Enum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class TradeSignal(BaseModel):
    ticker: str
    direction: Direction
    strength: SignalStrength
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    rationale: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class Order(BaseModel):
    ticker: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None


class OrderResult(BaseModel):
    order_id: str
    status: str
    filled_price: Optional[float] = None
    filled_quantity: Optional[float] = None
    message: str = ""


# --- Alert models ---

class AlertPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class NewsAlert(BaseModel):
    ticker: str
    price: Optional[float] = None
    float_shares: Optional[float] = None   # shares in millions
    market_cap_usd: Optional[float] = None
    premarket_change_pct: Optional[float] = None
    headline: str
    source: str = ""
    priority: AlertPriority = AlertPriority.MEDIUM
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ChartAction(str, Enum):
    BUY = "buy"
    SELL = "sell"
    WATCH = "watch"
    HOLD = "hold"


class ChartAlert(BaseModel):
    ticker: str
    action: ChartAction
    strength: SignalStrength = SignalStrength.MODERATE
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    rationale: str
    rules_triggered: list[str] = Field(default_factory=list)
    priority: AlertPriority = AlertPriority.MEDIUM
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# --- Discovery / daily watchlist ---

class WatchlistCandidate(BaseModel):
    ticker: str
    rank: int
    price: Optional[float] = None
    gap_pct: Optional[float] = None
    float_m: Optional[float] = None        # float shares in millions
    market_cap_m: Optional[float] = None   # market cap in millions
    relative_volume: Optional[float] = None
    catalyst: str = ""
    catalyst_source: str = ""
    priority: "AlertPriority" = None       # type: ignore — resolved at runtime
    rationale: str = ""

    def summary_line(self) -> str:
        parts = [f"#{self.rank} {self.ticker}"]
        if self.price:
            parts.append(f"${self.price:.2f}")
        if self.gap_pct:
            parts.append(f"+{self.gap_pct:.1f}%")
        if self.float_m:
            parts.append(f"float {self.float_m:.1f}M")
        parts.append(self.catalyst[:60] or "no catalyst")
        return "  ".join(parts)


class DailyWatchlist(BaseModel):
    date: str                              # YYYY-MM-DD
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    candidates: list[WatchlistCandidate] = Field(default_factory=list)
    watchlist: list[str] = Field(default_factory=list)   # ordered tickers for NewsAgent


# --- Inter-agent messaging ---

class AgentMessage(BaseModel):
    topic: str                         # routing key: "news", "chart", "execution", "broadcast"
    from_agent: str
    to_agent: str = "broadcast"        # "broadcast" = all subscribers
    payload: Any
    timestamp: datetime = Field(default_factory=datetime.utcnow)
