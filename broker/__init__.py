"""
Módulo de brokers do Projeto Córtex.

Abstração para execução de ordens nos modos simulação e MetaTrader 5.
Exporta os tipos de domínio, implementações concretas, a classe Portfolio,
e a função-fábrica create_broker() para instanciação baseada em configuração.
"""

from broker.base import (
    BaseBroker,
    BrokerBase,
    Order,
    OrderStatus,
    OrderType,
)
from broker.simulator import SimulatorBroker
from models.data_models import Position, PortfolioSummary
from portfolio import Portfolio

def create_broker(mode: str | None = None) -> BrokerBase:
    """
    Função-fábrica que cria a instância de broker adequada.

    Seleciona a implementação baseado no modo configurado
    (variável de ambiente CORTEX_BROKER_MODE ou parâmetro explícito).

    Args:
        mode: Modo de operação ('simulator' ou 'mt5').
              Se None, usa o valor de settings.broker_mode.

    Returns:
        Instância de BrokerBase (SimulatorBroker ou MT5Broker).

    Raises:
        ValueError: Se o modo for desconhecido.
        RuntimeError: Se o modo 'mt5' for selecionado fora do Windows.
    """
    from config.settings import settings

    if mode is None:
        mode = settings.broker_mode

    mode = mode.lower().strip()

    if mode in ("simulator", "sim", "paper", "simulador"):
        return SimulatorBroker()
    elif mode in ("mt5", "metatrader", "metatrader5", "real"):
        from broker.rest_mt5_broker import RestMT5Broker
        return RestMT5Broker()
    else:
        raise ValueError(
            f"Modo de broker desconhecido: '{mode}'. "
            f"Use 'simulator' ou 'mt5'."
        )

__all__ = [
    "BaseBroker",
    "BrokerBase",
    "Order",
    "OrderStatus",
    "OrderType",
    "Position",
    "SimulatorBroker",
    "Portfolio",
    "PortfolioSummary",
    "create_broker",
]
