from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from domain.models import (
    ConnectorImplementationStatus,
    MarketQuote,
    Order,
    OrderSide,
    Position,
)


class BrokerConnector(ABC):
    connector_type: str = "unknown"
    display_name: str = "Corretora B3"
    implementation_status: ConnectorImplementationStatus = ConnectorImplementationStatus.WIP

    @abstractmethod
    def connect(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_account_summary(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def get_broker_positions(self) -> list[Position]:
        raise NotImplementedError

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        expected_price: float = 0.0,
        stop_loss: float = 0.0,
        client_order_id: str = "",
        bypass_guards: bool = False,
    ) -> Order:
        raise NotImplementedError

    @abstractmethod
    def update_position_stop_loss(
        self,
        symbol: str,
        ticket: int,
        stop_loss: float,
    ) -> bool:
        raise NotImplementedError

    @abstractmethod
    def cancel_all_pending_orders(self) -> int:
        raise NotImplementedError

    def get_quote(self, symbol: str) -> MarketQuote | None:
        return None
