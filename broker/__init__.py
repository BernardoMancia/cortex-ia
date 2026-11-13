"""
Módulo de brokers do Projeto Córtex.

Abstração para execução de ordens nos modos simulação e MetaTrader 5.
Exporta os tipos de domínio, implementações concretas, a classe Portfolio,
e a função-fábrica create_broker() para instanciação baseada em configuração.
"""

from __future__ import annotations

import platform

from broker.base import (
    BaseBroker,
    BrokerBase,
    Order,
    OrderStatus,
    OrderType,
)
from broker.mt5_broker import MT5Broker
from broker.rest_mt5_broker import RestMT5Broker
from broker.simulator import SimulatorBroker
from models.data_models import PortfolioSummary, Position
from portfolio import Portfolio


def create_broker(mode: str | None = None) -> BrokerBase:
    """
    Função-fábrica que cria a instância de broker adequada.

    Seleciona a implementação baseado no modo configurado:
    - 'simulator': SimulatorBroker local.
    - 'mt5': RestMT5Broker se settings.MT5_BRIDGE_URL estiver configurado,
             MT5Broker nativo no Windows caso contrário.

    Args:
        mode: Modo de operação ('simulator' ou 'mt5').
              Se None, usa o valor de settings.broker_mode.

    Returns:
        Instância de BrokerBase.

    Raises:
        ValueError: Se o modo for desconhecido.
        RuntimeError: Se o modo 'mt5' for selecionado fora do Windows sem MT5_BRIDGE_URL.
    """
    from config.settings import settings

    if mode is None:
        mode = settings.broker_mode

    mode_clean = mode.lower().strip()

    if mode_clean in ("simulator", "sim", "paper", "simulador"):
        return SimulatorBroker()

    if mode_clean in ("bridge", "rest_mt5"):
        bridge_url = getattr(settings, "MT5_BRIDGE_URL", "").strip()
        if not bridge_url:
            raise ValueError(
                f"Modo '{mode}' requer MT5_BRIDGE_URL configurado no ambiente."
            )
        return RestMT5Broker(base_url=bridge_url)

    if mode_clean in ("mt5", "metatrader", "metatrader5", "real"):
        bridge_url = getattr(settings, "MT5_BRIDGE_URL", "").strip()
        if bridge_url:
            return RestMT5Broker(base_url=bridge_url)
        if platform.system() == "Windows":
            return MT5Broker()
        raise RuntimeError(
            "Modo 'mt5' fora do Windows requer MT5_BRIDGE_URL configurado no ambiente."
        )

    raise ValueError(
        f"Modo de broker desconhecido: '{mode}'. Use 'simulator', 'mt5' ou 'bridge'."
    )


__all__ = [
    "BaseBroker",
    "BrokerBase",
    "MT5Broker",
    "Order",
    "OrderStatus",
    "OrderType",
    "Portfolio",
    "PortfolioSummary",
    "Position",
    "RestMT5Broker",
    "SimulatorBroker",
    "create_broker",
]
