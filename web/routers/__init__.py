from __future__ import annotations

from web.routers.auth_routes import router as auth_router
from web.routers.bi_routes import router as bi_router
from web.routers.capital_routes import router as capital_router
from web.routers.cockpit_routes import router as cockpit_router
from web.routers.luke_admin_routes import router as luke_admin_router
from web.routers.risk_routes import router as risk_router
from web.routers.security_routes import router as security_router
from web.routers.system_routes import router as system_router
from web.routers.wallets_routes import router as wallets_router

ALL_ROUTERS = (
    auth_router,
    security_router,
    luke_admin_router,
    wallets_router,
    system_router,
    cockpit_router,
    risk_router,
    capital_router,
    bi_router,
)

__all__ = [
    "ALL_ROUTERS",
    "auth_router",
    "bi_router",
    "capital_router",
    "cockpit_router",
    "luke_admin_router",
    "risk_router",
    "security_router",
    "system_router",
    "wallets_router",
]
