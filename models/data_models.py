"""
Modelos de dados do Projeto Córtex.

Define todas as dataclasses, enums e tipos utilizados pelo sistema
de trading autônomo para a B3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

BRT: ZoneInfo = ZoneInfo("America/Sao_Paulo")


class Action(Enum):
    """Ações possíveis no sistema de trading."""
    BUY = 'COMPRA'
    SELL = 'VENDA'
    HOLD = 'AGUARDAR'
    EMERGENCY_SELL = 'VENDA_EMERGENCIAL'


@dataclass(init=False)
class Position:
    """Representa uma posição aberta em um ativo (unificada entre Engine e Brokers)."""
    ticker: str
    quantity: int
    entry_price: float
    stop_loss: float = 0.0
    entry_time: datetime = field(default_factory=lambda: datetime.now(BRT))
    current_price: float = 0.0
    ticket: int | None = None
    partial_exit_done: bool = False

    def __init__(
        self,
        ticker: str,
        quantity: int,
        entry_price: float,
        stop_loss: float = 0.0,
        entry_time: datetime | None = None,
        current_price: float = 0.0,
        ticket: int | None = None,
        partial_exit_done: bool = False,
        timestamp: datetime | None = None,
    ) -> None:
        self.ticker = ticker.strip().upper()
        self.quantity = quantity
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        resolved_dt = timestamp if timestamp is not None else (entry_time if entry_time is not None else datetime.now(BRT))
        self.entry_time = resolved_dt
        self.current_price = current_price if (current_price > 0 or entry_price <= 0) else entry_price
        self.ticket = ticket
        self.partial_exit_done = partial_exit_done

    @property
    def timestamp(self) -> datetime:
        """Alias sincronizado com `entry_time` (nunca diverge em `dataclasses.replace`)."""
        return self.entry_time

    @timestamp.setter
    def timestamp(self, value: datetime | None) -> None:
        if value is not None:
            self.entry_time = value

    @property
    def total_cost(self) -> float:
        """Custo total da posição."""
        return self.entry_price * self.quantity

    @property
    def invested_value(self) -> float:
        """Alias compatível com Broker Position."""
        return round(self.total_cost, 2)

    @property
    def current_value(self) -> float:
        """Valor atual da posição."""
        price = self.current_price if (self.current_price and self.current_price > 0) else self.entry_price
        return price * self.quantity

    @property
    def total_value(self) -> float:
        """Alias compatível com Broker Position."""
        return round(self.current_value, 2)

    @property
    def pnl(self) -> float:
        """Lucro/prejuízo absoluto."""
        return self.current_value - self.total_cost

    @property
    def pnl_percent(self) -> float:
        """Lucro/prejuízo percentual."""
        if self.total_cost == 0:
            return 0.0
        return (self.pnl / self.total_cost) * 100.0

    def to_dict(self) -> dict:
        """Serializa a posição para dicionário."""
        ts = self.entry_time.isoformat() if isinstance(self.entry_time, datetime) else str(self.entry_time)
        return {
            "ticker": self.ticker,
            "quantity": self.quantity,
            "entry_price": round(self.entry_price, 2),
            "current_price": round(self.current_price, 2),
            "stop_loss": round(self.stop_loss, 2),
            "ticket": self.ticket,
            "timestamp": ts,
            "entry_time": ts,
            "partial_exit_done": self.partial_exit_done,
            "pnl": round(self.pnl, 2),
            "pnl_percent": round(self.pnl_percent, 2),
            "total_value": self.total_value,
        }


@dataclass
class Decision:
    """Decisão gerada pelo DecisionEngine."""
    ticker: str
    action: Action
    confidence: float
    reasoning: str
    technical_score: float = 0.0
    sentiment_score: float = 0.0
    quantity: int = 0
    price: float = 0.0
    stop_loss: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(BRT))

    @property
    def is_actionable(self) -> bool:
        """Verifica se a decisão é acionável (não é HOLD)."""
        return self.action in (Action.BUY, Action.SELL, Action.EMERGENCY_SELL)


@dataclass
class OHLCV:
    """Dados de candle OHLCV."""
    ticker: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class MarketSnapshot:
    """Snapshot do mercado em um momento específico."""
    timestamp: datetime
    prices: dict[str, float] = field(default_factory=dict)
    volumes: dict[str, int] = field(default_factory=dict)
    variations: dict[str, float] = field(default_factory=dict)


@dataclass
class PortfolioSummary:
    """Resumo consolidado da carteira."""
    total_value: float = 0.0
    free_cash: float = 0.0
    allocated_capital: float = 0.0
    positions: list[Position] = field(default_factory=list)
    total_pnl: float = 0.0
    total_pnl_percent: float = 0.0
    num_positions: int = 0
    simulation_mode: bool = True

    @property
    def mode_str(self) -> str:
        """String legível do modo de operação."""
        return 'HOMOLOGAÇÃO' if self.simulation_mode else 'PRODUÇÃO'


@dataclass
class TradeRecord:
    """Registro de uma operação executada."""
    ticker: str
    action: Action
    quantity: int
    price: float
    total: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(BRT))
    stop_loss: float = 0.0
    reasoning: str = ''
    mode: str = 'HOMOLOGAÇÃO'


@dataclass
class HealthReport:
    """Relatório de saúde do sistema."""
    cpu_percent: float
    ram_percent: float
    disk_percent: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(BRT))
    is_healthy: bool = True
    alerts: list[str] = field(default_factory=list)
    cpu_threshold: float = field(default=90.0, repr=False)
    ram_threshold: float = field(default=90.0, repr=False)
    disk_threshold: float = field(default=95.0, repr=False)

    def __post_init__(self) -> None:
        """Calcula se o sistema está saudável preservando falhas explícitas."""
        thresholds_ok = (
            self.cpu_percent < self.cpu_threshold
            and self.ram_percent < self.ram_threshold
            and self.disk_percent < self.disk_threshold
        )
        self.is_healthy = bool(self.is_healthy and thresholds_ok and not self.alerts)
        if self.cpu_percent >= self.cpu_threshold:
            msg = f'CPU em {self.cpu_percent:.1f}%'
            if msg not in self.alerts:
                self.alerts.append(msg)
        if self.ram_percent >= self.ram_threshold:
            msg = f'RAM em {self.ram_percent:.1f}%'
            if msg not in self.alerts:
                self.alerts.append(msg)
        if self.disk_percent >= self.disk_threshold:
            msg = f'Disco em {self.disk_percent:.1f}%'
            if msg not in self.alerts:
                self.alerts.append(msg)
