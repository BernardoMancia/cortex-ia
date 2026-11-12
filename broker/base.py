"""
Módulo base do broker — classes de domínio e contrato abstrato.

Define os tipos fundamentais (Order, Position, OrderType, OrderStatus)
e a interface abstrata BrokerBase que todas as implementações de corretora
devem seguir.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

from models.data_models import Position

logger = logging.getLogger("cortex.broker")

BRT: ZoneInfo = ZoneInfo("America/Sao_Paulo")


class OrderType(Enum):
    """Tipo de ordem: compra ou venda."""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    """Status de uma ordem enviada ao mercado."""

    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    """
    Representa uma ordem de compra ou venda.
    """

    ticker: str
    order_type: OrderType
    quantity: int
    price: float
    stop_loss: Optional[float] = None
    status: OrderStatus = OrderStatus.PENDING
    ticket: Optional[int] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=BRT))
    comment: str = ""

    def __post_init__(self) -> None:
        """Valida campos básicos e normaliza ticker."""
        self.ticker = self.ticker.upper().strip()
        if self.status in (OrderStatus.REJECTED, OrderStatus.CANCELLED):
            self.quantity = max(0, int(self.quantity))
            self.price = max(0.0, float(self.price))
            return
        if self.quantity < 0:
            raise ValueError(f"Quantidade inválida: {self.quantity}. Deve ser >= 0.")
        if self.price < 0:
            raise ValueError(f"Preço inválido: {self.price}. Deve ser >= 0.")

    @property
    def total_value(self) -> float:
        """Valor total da ordem (quantidade × preço)."""
        return round(self.quantity * self.price, 2)

    def to_dict(self) -> dict:
        """Serializa a ordem para dicionário."""
        return {
            "ticker": self.ticker,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "status": self.status.value,
            "ticket": self.ticket,
            "timestamp": self.timestamp.isoformat(),
            "comment": self.comment,
        }


class BrokerBase(ABC):
    """
    Contrato abstrato que toda implementação de corretora deve seguir.
    """

    @abstractmethod
    def connect(self) -> bool:
        """Estabelece conexão com a corretora."""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """Encerra a conexão com a corretora."""
        ...

    @abstractmethod
    def get_balance(self) -> float:
        """Retorna o saldo em caixa disponível para operações."""
        ...

    @abstractmethod
    def get_equity(self) -> float:
        """Retorna o patrimônio total (saldo + valor das posições abertas)."""
        ...

    @abstractmethod
    def buy(self, ticker: str, quantity: int, price: float, stop_loss: float) -> Order:
        """Envia ordem de compra."""
        ...

    @abstractmethod
    def sell(self, ticker: str, quantity: int, price: float) -> Order:
        """Envia ordem de venda."""
        ...

    @abstractmethod
    def emergency_sell(self, ticker: str) -> Order:
        """Venda de emergência — liquida toda a posição de um ativo a preço de mercado."""
        ...

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Retorna todas as posições abertas."""
        ...

    @abstractmethod
    def get_position(self, ticker: str) -> Optional[Position]:
        """Retorna a posição de um ativo específico."""
        ...

    @abstractmethod
    def modify_stop_loss(self, ticker: str, new_sl: float) -> bool:
        """Modifica o stop-loss de uma posição aberta."""
        ...


BaseBroker = BrokerBase
