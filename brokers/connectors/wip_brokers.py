from __future__ import annotations

from typing import Any

from brokers.base import BrokerConnector
from domain.b3_rules import get_brt_now, normalize_b3_ticker
from domain.models import (
    ConnectorImplementationStatus,
    Order,
    OrderSide,
    OrderStatus,
    Position,
)

WIP_BROKER_MESSAGE = (
    "Conector em desenvolvimento (WIP). No momento, apenas Clear Investimentos está 100% pronta na Produção."
)


class _BaseWIPBrokerConnector(BrokerConnector):
    implementation_status: ConnectorImplementationStatus = ConnectorImplementationStatus.WIP

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = dict(config or {})

    def connect(self) -> bool:
        return False

    def health_check(self) -> dict[str, Any]:
        return {
            "connected": False,
            "bridge_online": False,
            "trade_allowed": False,
            "server": "",
            "login": "",
            "status": self.implementation_status.value,
            "message": WIP_BROKER_MESSAGE,
        }

    def get_account_summary(self) -> dict[str, Any]:
        return {
            "connected": False,
            "balance": 0.0,
            "equity": 0.0,
            "free_margin": 0.0,
            "server": "",
            "login": "",
            "currency": "BRL",
            "message": WIP_BROKER_MESSAGE,
        }

    def get_broker_positions(self) -> list[Position]:
        return []

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
        clean_side = side if isinstance(side, OrderSide) else OrderSide(str(side).strip().upper())
        return Order(
            ticker=normalize_b3_ticker(symbol) or str(symbol).strip().upper(),
            side=clean_side,
            quantity=0,
            price=0.0,
            status=OrderStatus.REJECTED,
            stop_loss=stop_loss,
            fee=0.0,
            client_order_id=client_order_id,
            message=WIP_BROKER_MESSAGE,
            timestamp=get_brt_now().isoformat(),
        )

    def update_position_stop_loss(
        self,
        symbol: str,
        ticket: int,
        stop_loss: float,
    ) -> bool:
        return False

    def cancel_all_pending_orders(self) -> int:
        return 0


class XPMT5Connector(_BaseWIPBrokerConnector):
    connector_type: str = "xp_mt5"
    display_name: str = "XP Investimentos — B3 (MT5)"


class RicoMT5Connector(_BaseWIPBrokerConnector):
    connector_type: str = "rico_mt5"
    display_name: str = "Rico Investimentos — B3 (MT5)"


class BTGPactualConnector(_BaseWIPBrokerConnector):
    connector_type: str = "btg_api"
    display_name: str = "BTG Pactual — API / Home Broker"


class GenericB3Connector(_BaseWIPBrokerConnector):
    connector_type: str = "generic_b3"
    display_name: str = "Outra Corretora B3 (FIX / MT5 / API)"
