from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class GlobalOperationalMode(str, Enum):
    MAINTENANCE = "MAINTENANCE"
    ACTIVE = "ACTIVE"
    PANIC = "PANIC"


class WalletOperationalMode(str, Enum):
    ACTIVE = "ACTIVE"
    OBSERVE_ONLY = "OBSERVE_ONLY"
    PAUSED_UNCONFIGURED = "PAUSED_UNCONFIGURED"
    PAUSED_BROKER_ERROR = "PAUSED_BROKER_ERROR"
    PANIC = "PANIC"


class ConnectorImplementationStatus(str, Enum):
    READY = "ready"
    WIP = "work_in_progress"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class TrendSignal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


@dataclass
class Position:
    ticker: str
    quantity: int
    entry_price: float
    current_price: float
    stop_loss: float
    opened_at: str = ""
    highest_price: float = 0.0
    partial_tp_taken: bool = False
    do_not_touch: bool = False
    ticket: int = 0
    fiscal_avg_price: float = 0.0

    def __post_init__(self) -> None:
        if self.highest_price <= 0.0:
            self.highest_price = max(self.entry_price, self.current_price)
        if self.fiscal_avg_price <= 0.0:
            self.fiscal_avg_price = self.entry_price

    @property
    def market_value(self) -> float:
        return round(self.quantity * self.current_price, 2)

    @property
    def cost_basis(self) -> float:
        return round(self.quantity * self.entry_price, 2)

    @property
    def pnl(self) -> float:
        return round((self.current_price - self.entry_price) * self.quantity, 2)

    @property
    def pnl_percent(self) -> float:
        if self.entry_price <= 0.0:
            return 0.0
        return round(((self.current_price - self.entry_price) / self.entry_price) * 100.0, 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "quantity": self.quantity,
            "entry_price": round(self.entry_price, 2),
            "current_price": round(self.current_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "highest_price": round(self.highest_price, 2),
            "market_value": self.market_value,
            "cost_basis": self.cost_basis,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "opened_at": self.opened_at,
            "partial_tp_taken": self.partial_tp_taken,
            "do_not_touch": self.do_not_touch,
            "ticket": self.ticket,
            "fiscal_avg_price": round(self.fiscal_avg_price, 4),
        }


@dataclass
class Order:
    ticker: str
    side: OrderSide
    quantity: int
    price: float
    status: OrderStatus
    stop_loss: float = 0.0
    fee: float = 0.0
    order_id: str = ""
    client_order_id: str = ""
    message: str = ""
    timestamp: str = ""
    legs: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_value(self) -> float:
        return round(self.quantity * self.price, 2)


@dataclass(frozen=True)
class MarketQuote:
    ticker: str
    price: float
    bid: float = 0.0
    ask: float = 0.0
    spread_pct: float = 0.0
    volume: float = 0.0
    change_pct: float = 0.0
    quote_ts: float = 0.0
    source: str = "yfinance"
    is_stale: bool = False


@dataclass(frozen=True)
class TechnicalVector:
    ticker: str
    signal: TrendSignal
    ema_9: float = 0.0
    ema_21: float = 0.0
    ema_50: float = 0.0
    rsi: float = 50.0
    macd_hist: float = 0.0
    atr: float = 0.0
    adx: float = 0.0
    rel_vol: float = 1.0
    bb_lower: float = 0.0
    bb_upper: float = 0.0
    support: float = 0.0
    resistance: float = 0.0
    mtf_bullish: bool = True
    confidence: float = 0.0
    reasoning: str = ""


@dataclass(frozen=True)
class SentimentVector:
    ticker: str
    score: float = 0.0
    label: str = "NEUTRO"
    confidence: float = 0.0
    news_count: int = 0
    top_headline: str = ""
    reasoning: str = ""


@dataclass(frozen=True)
class MarketCycleSnapshot:
    cycle_id: str
    generated_at: datetime
    market_open: bool
    opening_cooldown_active: bool
    bova11_change_pct: float
    regime_bearish_veto: bool
    quotes: dict[str, MarketQuote]
    technicals: dict[str, TechnicalVector]
    sentiments: dict[str, SentimentVector]
    headlines: list[dict[str, Any]]


@dataclass
class DecisionCandidate:
    ticker: str
    action: str
    confidence: float
    trend_signal: str
    sentiment_score: float
    price: float
    suggested_stop_loss: float
    atr: float
    reasoning: str


@dataclass
class PreFlightReport:
    ready: bool
    bridge_online: bool
    broker_authenticated: bool
    account_server: str
    broker_balance: float
    custody_synced: bool
    audit_chain_intact: bool
    clock_synced: bool
    issues: list[str] = field(default_factory=list)
    positions_found: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "bridge_online": self.bridge_online,
            "broker_authenticated": self.broker_authenticated,
            "account_server": self.account_server,
            "broker_balance": round(self.broker_balance, 2),
            "custody_synced": self.custody_synced,
            "audit_chain_intact": self.audit_chain_intact,
            "clock_synced": self.clock_synced,
            "issues": list(self.issues),
            "positions_found": self.positions_found,
        }
