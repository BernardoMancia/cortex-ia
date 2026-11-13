from __future__ import annotations

from typing import Any

from brokers.base import BrokerConnector
from brokers.connectors.clear_mt5 import ClearMT5Connector
from brokers.connectors.wip_brokers import (
    BTGPactualConnector,
    GenericB3Connector,
    RicoMT5Connector,
    XPMT5Connector,
)
from config.settings import OWNER_USER_ID, settings
from domain.models import ConnectorImplementationStatus

_CONNECTOR_CATALOG: list[dict[str, Any]] = [
    {
        "connector_type": ClearMT5Connector.connector_type,
        "display_name": ClearMT5Connector.display_name,
        "status": ConnectorImplementationStatus.READY.value,
        "badge": "PRONTO (PRODUÇÃO)",
        "description": "Execução real na B3 via MetaTrader 5 Headless Bridge com fracionário automático e Stop-Loss nativo.",
        "fields": [
            {
                "name": "bridge_url",
                "label": "URL da MT5 Bridge",
                "type": "text",
                "required": False,
                "placeholder": "http://127.0.0.1:5000",
            },
            {
                "name": "bridge_api_key",
                "label": "Chave X-Bridge-Key",
                "type": "password",
                "required": False,
                "placeholder": "Chave HMAC de 32+ caracteres",
            },
            {
                "name": "mt5_login",
                "label": "Login Conta Clear MT5",
                "type": "text",
                "required": False,
                "placeholder": "Número da conta MT5",
            },
            {
                "name": "mt5_server",
                "label": "Servidor MT5",
                "type": "text",
                "required": False,
                "placeholder": "ClearInvestimentos-CLEAR",
            },
        ],
    },
    {
        "connector_type": XPMT5Connector.connector_type,
        "display_name": XPMT5Connector.display_name,
        "status": ConnectorImplementationStatus.WIP.value,
        "badge": "EM DESENVOLVIMENTO (WIP)",
        "description": "Integração dedicada XP Investimentos B3 via MT5 / FIX em homologação interna.",
        "fields": [
            {"name": "mt5_login", "label": "Login Conta XP MT5", "type": "text", "required": False},
            {"name": "mt5_server", "label": "Servidor XP", "type": "text", "required": False},
        ],
    },
    {
        "connector_type": RicoMT5Connector.connector_type,
        "display_name": RicoMT5Connector.display_name,
        "status": ConnectorImplementationStatus.WIP.value,
        "badge": "EM DESENVOLVIMENTO (WIP)",
        "description": "Integração dedicada Rico Investimentos B3 via MT5 em homologação interna.",
        "fields": [
            {"name": "mt5_login", "label": "Login Conta Rico MT5", "type": "text", "required": False},
            {"name": "mt5_server", "label": "Servidor Rico", "type": "text", "required": False},
        ],
    },
    {
        "connector_type": BTGPactualConnector.connector_type,
        "display_name": BTGPactualConnector.display_name,
        "status": ConnectorImplementationStatus.WIP.value,
        "badge": "EM DESENVOLVIMENTO (WIP)",
        "description": "Conector API / Home Broker BTG Pactual para renda variável B3.",
        "fields": [
            {"name": "api_key", "label": "Client ID / API Key", "type": "text", "required": False},
            {"name": "api_secret", "label": "Client Secret", "type": "password", "required": False},
        ],
    },
    {
        "connector_type": GenericB3Connector.connector_type,
        "display_name": GenericB3Connector.display_name,
        "status": ConnectorImplementationStatus.WIP.value,
        "badge": "EM DESENVOLVIMENTO (WIP)",
        "description": "Arquitetura aberta para demais corretoras B3 via gateway FIX / REST / MT5.",
        "fields": [
            {"name": "endpoint_url", "label": "Endpoint do Gateway", "type": "text", "required": False},
            {"name": "api_token", "label": "Token de Autenticação", "type": "password", "required": False},
        ],
    },
]


def list_available_connectors() -> list[dict[str, Any]]:
    return [
        {
            **item,
            "fields": [dict(f) for f in item["fields"]],
        }
        for item in _CONNECTOR_CATALOG
    ]


def is_connector_ready(connector_type: str) -> bool:
    return (connector_type or "").strip().lower() == ClearMT5Connector.connector_type


def build_wallet_connector(
    wallet_row: dict[str, Any],
    decrypted_config: dict[str, Any] | None = None,
) -> BrokerConnector:
    cfg = dict(decrypted_config or {})
    ctype = str(wallet_row.get("connector_type") or ClearMT5Connector.connector_type).strip().lower()
    wallet_id = int(wallet_row.get("id") or 0)
    user_id = int(wallet_row.get("user_id") or 0)
    allow_env_defaults = user_id in (0, OWNER_USER_ID)

    if ctype == ClearMT5Connector.connector_type:
        b_url = str(cfg.get("bridge_url") or "").strip()
        b_key = str(cfg.get("bridge_api_key") or "").strip()
        b_login = str(
            cfg.get("mt5_login") or cfg.get("account_login") or cfg.get("login") or ""
        ).strip()
        b_srv = str(cfg.get("mt5_server") or cfg.get("server") or "").strip()

        if allow_env_defaults:
            b_url = b_url or settings.MT5_BRIDGE_URL
            b_key = b_key or settings.BRIDGE_API_KEY
            b_login = b_login or settings.MT5_LOGIN
            b_srv = b_srv or settings.MT5_SERVER

        return ClearMT5Connector(
            bridge_url=b_url,
            bridge_api_key=b_key,
            account_login=b_login,
            server=b_srv,
            wallet_id=wallet_id,
        )

    if ctype == XPMT5Connector.connector_type:
        return XPMT5Connector(cfg)
    if ctype == RicoMT5Connector.connector_type:
        return RicoMT5Connector(cfg)
    if ctype == BTGPactualConnector.connector_type:
        return BTGPactualConnector(cfg)
    return GenericB3Connector(cfg)
