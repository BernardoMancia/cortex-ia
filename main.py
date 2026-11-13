from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from config.settings import settings
from engine.orchestrator import CortexOrchestrator
from observability.logger import get_logger
from security.fs_guard import enforce_secure_permissions
from storage.connection import DatabaseConnection
from web.server import create_web_app

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    enforce_secure_permissions(
        data_dir=settings.DATA_DIR,
        keys_dir=settings.KEYS_DIR,
        backups_dir=settings.BACKUPS_DIR,
        project_root=settings.PROJECT_ROOT,
    )
    db: DatabaseConnection = getattr(app.state, "db", None) or DatabaseConnection()
    orchestrator: CortexOrchestrator = getattr(app.state, "orchestrator", None) or CortexOrchestrator(db=db)
    app.state.db = db
    app.state.orchestrator = orchestrator
    app.state.panic_service = orchestrator.panic_service
    app.state.custody_reconciler = orchestrator.custody_reconciler

    logger.info(
        "Inicializando servidor web e orquestrador Córtex IA v%s (%s) em %s:%s",
        settings.VERSION,
        settings.ENVIRONMENT_LABEL,
        settings.DASHBOARD_HOST,
        settings.DASHBOARD_PORT,
    )
    orchestrator.start()
    try:
        yield
    finally:
        orchestrator.stop()
        logger.info("Servidor Córtex IA finalizado com segurança.")


def create_production_app(db: DatabaseConnection | None = None) -> FastAPI:
    target_db = db or DatabaseConnection()
    orchestrator = CortexOrchestrator(db=target_db)
    return create_web_app(
        orchestrator=orchestrator,
        db=target_db,
        lifespan=lifespan,
    )


app = create_production_app()


def run() -> None:
    enforce_secure_permissions(
        data_dir=settings.DATA_DIR,
        keys_dir=settings.KEYS_DIR,
        backups_dir=settings.BACKUPS_DIR,
        project_root=settings.PROJECT_ROOT,
    )
    uvicorn.run(
        app,
        host=settings.DASHBOARD_HOST,
        port=settings.DASHBOARD_PORT,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    run()
