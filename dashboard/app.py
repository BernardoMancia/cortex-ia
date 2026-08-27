import os
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from dashboard.routes import router
from auth import extract_session_token, MAX_IDLE_SECONDS
from data.database import DatabaseManager

app = FastAPI(title="Córtex IA Dashboard")

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.include_router(router)

@app.get("/login")
async def login_page():
    login_path = os.path.join(static_dir, "login.html")
    if os.path.exists(login_path):
        return FileResponse(login_path)
    return {"message": "Página de login."}

@app.get("/")
async def root(request: Request):
    token = extract_session_token(request)
    if not token:
        return RedirectResponse(url="/login")
    
    db = DatabaseManager()
    session = db.get_valid_session(token, max_idle_seconds=MAX_IDLE_SECONDS)
    if not session:
        return RedirectResponse(url="/login?reason=expired")

    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Córtex IA Dashboard API is running."}

import threading
from datetime import datetime, timezone

class DashboardState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict = {
            "market_status": "DESCONHECIDO",
            "portfolio": {},
            "watchlist": [],
            "recent_decisions": [],
            "health": {},
            "uptime_seconds": 0.0,
            "log_lines": [],
            "updated_at": None,
        }

    def update(self, key: str, value) -> None:
        with self._lock:
            self._state[key] = value
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()

    def add_log_line(self, line: str, max_lines: int = 500) -> None:
        with self._lock:
            self._state["log_lines"].append(line)
            if len(self._state["log_lines"]) > max_lines:
                self._state["log_lines"] = self._state["log_lines"][-max_lines:]

    def get_state(self) -> dict:
        with self._lock:
            return dict(self._state)

    def get(self, key: str, default=None):
        with self._lock:
            return self._state.get(key, default)

import uvicorn

class _ThreadedServer(uvicorn.Server):
    def install_signal_handlers(self) -> None:
        pass

class DashboardServer:
    def __init__(
        self,
        state: DashboardState | None = None,
        host: str = "0.0.0.0",
        port: int = 8003,
    ) -> None:
        self.state = state
        self.host = host
        self.port = port
        self._server: _ThreadedServer | None = None
        self._thread: threading.Thread | None = None

        if state is not None:
            app.state.dashboard_state = state

    def start(self) -> None:
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="asyncio",
        )
        self._server = _ThreadedServer(config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="cortex-dashboard",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)

def create_app() -> FastAPI:
    return app
