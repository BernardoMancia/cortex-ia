from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dashboard.routes import router
import os

app = FastAPI(title="Córtex IA Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

from fastapi.responses import FileResponse

# Mount static files
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include routes
app.include_router(router)

@app.get("/")
async def root():
    index_path = os.path.join(static_dir, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Córtex IA Dashboard API is running."}


# ────────────────────────────────────────────────────────────
# DashboardState — thread-safe runtime state container
# ────────────────────────────────────────────────────────────
import threading
from datetime import datetime, timezone


class DashboardState:
    """
    Holds runtime dashboard state that the engine pushes into
    and the dashboard API reads from.

    Thread-safe: all mutations go through :pymeth:`update`.
    """

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

    # ── Mutations ─────────────────────────────────────────
    def update(self, key: str, value) -> None:
        """Update a single key in the dashboard state."""
        with self._lock:
            self._state[key] = value
            self._state["updated_at"] = datetime.now(timezone.utc).isoformat()

    def add_log_line(self, line: str, max_lines: int = 500) -> None:
        """Append a log line, keeping the buffer bounded."""
        with self._lock:
            self._state["log_lines"].append(line)
            if len(self._state["log_lines"]) > max_lines:
                self._state["log_lines"] = self._state["log_lines"][-max_lines:]

    # ── Reads ─────────────────────────────────────────────
    def get_state(self) -> dict:
        """Return a shallow copy of the current state."""
        with self._lock:
            return dict(self._state)

    def get(self, key: str, default=None):
        """Return a single value from the state."""
        with self._lock:
            return self._state.get(key, default)


# ────────────────────────────────────────────────────────────
# DashboardServer — runs the FastAPI app in a background thread
# ────────────────────────────────────────────────────────────
import uvicorn


class DashboardServer:
    """
    Wraps :pymod:`uvicorn` so the dashboard can run alongside
    the trading engine in a background daemon thread.

    Usage::

        state  = DashboardState()
        server = DashboardServer(state, host="0.0.0.0", port=8003)
        server.start()   # non-blocking
        ...
        server.stop()     # graceful shutdown
    """

    def __init__(
        self,
        state: DashboardState | None = None,
        host: str = "0.0.0.0",
        port: int = 8003,
    ) -> None:
        self.state = state
        self.host = host
        self.port = port
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

        # Expose state on the app so routes can access it
        if state is not None:
            app.state.dashboard_state = state

    def start(self) -> None:
        """Start uvicorn in a daemon thread (non-blocking)."""
        config = uvicorn.Config(
            app=app,
            host=self.host,
            port=self.port,
            log_level="info",
            loop="asyncio",
            install_signal_handlers=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._server.run,
            name="cortex-dashboard",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Signal uvicorn to shut down gracefully."""
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


# ────────────────────────────────────────────────────────────
# Factory function (used by __init__.py exports)
# ────────────────────────────────────────────────────────────
def create_app() -> FastAPI:
    """Return the pre-configured FastAPI application instance."""
    return app
