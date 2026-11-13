from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from config.settings import settings
from engine.custody_sync import CustodyReconciler
from engine.orchestrator import CortexOrchestrator
from engine.panic_service import PanicService
from security.bi_tokens import is_bi_bearer_token
from security.session_csrf import extract_session_credentials, get_client_ip
from storage.audit_repo import AuditRepository
from storage.bi_tokens_repo import BiTokensRepository
from storage.connection import DatabaseConnection
from storage.users_repo import UsersRepository
from web.routers import ALL_ROUTERS

STATIC_DIR = Path(__file__).resolve().parent / "static"

_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class _BodyLimitExceeded(Exception):
    pass


class MaxBodySizeMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max(1024, int(max_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        for header_name, header_val in scope.get("headers", []):
            if header_name.lower() == b"content-length":
                try:
                    declared = int(header_val.decode("latin1").strip())
                except ValueError:
                    resp = JSONResponse(
                        status_code=400,
                        content={"detail": "Cabeçalho Content-Length inválido."},
                    )
                    await resp(scope, receive, send)
                    return
                if declared > self.max_bytes:
                    resp = JSONResponse(
                        status_code=413,
                        content={
                            "detail": f"Corpo da requisição excede o limite máximo de {self.max_bytes} bytes."
                        },
                    )
                    await resp(scope, receive, send)
                    return

        received_bytes = 0
        response_started = False

        async def _limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"") or b""
                received_bytes += len(chunk)
                if received_bytes > self.max_bytes:
                    raise _BodyLimitExceeded()
            return message

        async def _tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, _limited_receive, _tracked_send)
        except _BodyLimitExceeded:
            if not response_started:
                resp = JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"Corpo em stream excede o limite máximo de {self.max_bytes} bytes."
                    },
                )
                await resp(scope, receive, send)


def _has_authenticated_browser_session(request: Request, db: DatabaseConnection) -> bool:
    raw_token, _ = extract_session_credentials(request)
    if not raw_token:
        return False
    users_repo = UsersRepository(db)
    sess, _ = users_repo.validate_session(
        raw_token,
        request.headers.get("User-Agent", ""),
        touch=False,
    )
    if sess is None:
        return False
    user = users_repo.get_by_id(int(sess["user_id"]))
    if user is None or not bool(user.get("is_active", 1)):
        return False
    if users_repo.is_invite_expired(user):
        return False
    if bool(user.get("must_change_password", 0)):
        return False
    return True


def create_web_app(
    orchestrator: CortexOrchestrator | None = None,
    db: DatabaseConnection | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[Any]] | None = None,
) -> FastAPI:
    target_db = db or (orchestrator.db if orchestrator is not None else DatabaseConnection())

    app = FastAPI(
        title="Córtex IA B3",
        version=settings.VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    app.state.db = target_db
    app.state.orchestrator = orchestrator
    app.state.panic_service = (
        orchestrator.panic_service
        if orchestrator is not None
        else PanicService(db=target_db)
    )
    app.state.custody_reconciler = (
        orchestrator.custody_reconciler
        if orchestrator is not None
        else CustodyReconciler(db=target_db)
    )

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next: Any) -> Response:
        auth_hdr = (request.headers.get("Authorization") or "").strip()
        bearer_tok = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else ""
        raw_cred, _ = extract_session_credentials(request)
        has_bi_tok = is_bi_bearer_token(bearer_tok) or is_bi_bearer_token(raw_cred)

        if has_bi_tok and not request.url.path.startswith("/api/bi/v1/"):
            active_db = getattr(request.app.state, "db", None) or target_db
            offending = bearer_tok if is_bi_bearer_token(bearer_tok) else (raw_cred or "")
            actor_uid, target_wid = BiTokensRepository(active_db).lookup_owner_for_audit(offending)
            AuditRepository(active_db).record_event(
                event_type="BI_TOKEN_SCOPE_VIOLATION",
                actor_user_id=actor_uid,
                target_wallet_id=target_wid,
                ip_address=get_client_ip(request),
                details={
                    "path": request.url.path,
                    "method": request.method.upper(),
                    "token_prefix": offending[:14],
                },
            )
            response = JSONResponse(
                status_code=403,
                content={
                    "detail": "Tokens BI possuem acesso estritamente somente-leitura em /api/bi/v1/*."
                },
            )
        else:
            response = await call_next(request)

        response.headers["Content-Security-Policy"] = _CSP_POLICY
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.COOKIE_SECURE:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)

    for rtr in ALL_ROUTERS:
        app.include_router(rtr)

    @app.get("/login", include_in_schema=False)
    def serve_login_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "login.html", media_type="text/html")

    @app.get("/", include_in_schema=False)
    def serve_cockpit_index(request: Request) -> Response:
        active_db = getattr(request.app.state, "db", None) or target_db
        if not _has_authenticated_browser_session(request, active_db):
            return RedirectResponse(url="/login", status_code=302)
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
