from __future__ import annotations

from typing import Final

ENVIRONMENT_ID: Final[str] = "production"
ENVIRONMENT_LABEL: Final[str] = "PRODUÇÃO REAL B3"
IS_PRODUCTION: Final[bool] = True
IS_SIMULATION: Final[bool] = False
DEFAULT_PORT: Final[int] = 8003
PROJECT_SLUG: Final[str] = "cortex-ia"
CENTRAL_LOG_DB: Final[str] = "/LOGS-PROJETOS/cortex-ia/logs.db"
FORCE_MAINTENANCE_ON_BOOT: Final[bool] = True
CYCLE_SECOND_OFFSET: Final[int] = 2
DEFAULT_CYCLE_INTERVAL: Final[int] = 60
