"""
Modelos de dados do Projeto Córtex.

Define todas as dataclasses, enums e tipos utilizados pelo sistema
de trading autônomo para a B3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional

BRT = timezone(timedelta(hours=-3))

class Action(Enum):
    """Ações possíveis no sistema de trading."""
    BUY = 'COMPRA'
    SELL = 'VENDA'
    HOLD = 'AGUARDAR'
    EMERGENCY_SELL = 'VENDA_EMERGENCIAL'

@dataclass
class Position:
    """Representa uma posição aberta em um ativo."""
    ticker: str
    quantity: int
    entry_price: float
    stop_loss: float
    entry_time: datetime = field(default_factory=lambda: datetime.now(BRT))
    current_price: float = 0.0
    ticket: int | None = None
    partial_exit_done: bool = False

    @property
    def total_cost(self) -> float:
        """Custo total da posição."""
        return self.entry_price * self.quantity

    @property
    def current_value(self) -> float:
        """Valor atual da posição."""
        return self.current_price * self.quantity

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
        return 'SIMULAÇÃO' if self.simulation_mode else 'PRODUÇÃO'

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
    mode: str = 'SIMULAÇÃO'

@dataclass
class HealthReport:
    """Relatório de saúde do sistema."""
    cpu_percent: float
    ram_percent: float
    disk_percent: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(BRT))
    is_healthy: bool = True
    alerts: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Calcula se o sistema está saudável após inicialização."""
        self.is_healthy = (
            self.cpu_percent < 90.0
            and self.ram_percent < 90.0
            and self.disk_percent < 95.0
        )
        if self.cpu_percent >= 90.0:
            self.alerts.append(f'CPU em {self.cpu_percent:.1f}%')
        if self.ram_percent >= 90.0:
            self.alerts.append(f'RAM em {self.ram_percent:.1f}%')
        if self.disk_percent >= 95.0:
            self.alerts.append(f'Disco em {self.disk_percent:.1f}%')

