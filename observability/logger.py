from __future__ import annotations

import atexit
from collections import deque
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
from queue import Empty, Full, Queue
import re
import sqlite3
import sys
import threading
from typing import Callable, Final

from config.settings import BRT, settings

_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}")
_BI_KEY_RE: Final[re.Pattern[str]] = re.compile(r"\bctx_bi_[A-Za-z0-9_-]{20,}\b")
_GOOGLE_AI_KEY_RE: Final[re.Pattern[str]] = re.compile(r"AIza[0-9A-Za-z_-]{30,}")
_GOOGLE_AQ_KEY_RE: Final[re.Pattern[str]] = re.compile(r"AQ\.Ab8[0-9A-Za-z_-]{8,}")
_KV_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(BRIDGE_API_KEY|GEMINI_API_KEY|MT5_PASSWORD|VNC_PASSWORD|CORTEX_MASTER_KEY|Password|step_up_token)\s*[:=]\s*\S+",
    re.IGNORECASE,
)

_CREATE_LOG_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL,
    level     TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    message   TEXT    NOT NULL,
    raw_line  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp);
"""

_INSERT_LOG_SQL: Final[str] = """
INSERT INTO logs (timestamp, level, source, message, raw_line)
VALUES (?, ?, ?, ?, ?);
"""

_LOG_FORMAT: Final[str] = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"


def scrub_secrets(text: str) -> str:
    if not text:
        return ""
    cleaned = _TOKEN_RE.sub("bot***:***", text)
    cleaned = _BI_KEY_RE.sub("ctx_bi_***", cleaned)
    cleaned = _KV_SECRET_RE.sub(r"\1=***", cleaned)
    cleaned = _GOOGLE_AI_KEY_RE.sub("AIza***", cleaned)
    cleaned = _GOOGLE_AQ_KEY_RE.sub("AQ.Ab8***", cleaned)
    return cleaned


class _ScrubbingFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=BRT)
        return dt.strftime(datefmt) if datefmt else dt.isoformat(timespec="seconds")

    def format(self, record: logging.LogRecord) -> str:
        return scrub_secrets(super().format(record))


class SQLiteLogHandler(logging.Handler):
    def __init__(self, db_path: str, max_queue_size: int = 8000) -> None:
        super().__init__()
        self.db_path = db_path
        self._queue: Queue[tuple[str, str, str, str, str] | None] = Queue(maxsize=max_queue_size)
        self._closed = False
        self._lock = threading.Lock()
        parent = os.path.dirname(db_path)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._writer = threading.Thread(target=self._loop, name="cortex-log-db", daemon=True)
        self._writer.start()
        atexit.register(self.shutdown)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_CREATE_LOG_TABLE_SQL)
            conn.commit()
        finally:
            conn.close()

    def _loop(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            while True:
                try:
                    item = self._queue.get(timeout=1.0)
                except Empty:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                    continue
                if item is None:
                    break
                batch = [item]
                stop_after = False
                while len(batch) < 100:
                    try:
                        nxt = self._queue.get_nowait()
                    except Empty:
                        break
                    if nxt is None:
                        stop_after = True
                        break
                    batch.append(nxt)
                try:
                    if conn is None:
                        conn = self._connect()
                    conn.executemany(_INSERT_LOG_SQL, batch)
                    conn.commit()
                except Exception:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                if stop_after or self._queue.empty():
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                if stop_after:
                    break
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        try:
            ts = datetime.fromtimestamp(record.created, tz=BRT).isoformat(timespec="seconds")
            msg = scrub_secrets(record.getMessage())
            raw = self.format(record) if self.formatter else f"[{ts}] [{record.name}] [{record.levelname}] {msg}"
            try:
                self._queue.put_nowait((ts, record.levelname, record.name, msg, raw))
            except Full:
                pass
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        return

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._queue.put_nowait(None)
        except Full:
            pass
        self._writer.join(timeout=2.0)


_recent_logs: deque[str] = deque(maxlen=500)
_listeners: list[Callable[[str], None]] = []
_listeners_lock = threading.Lock()
_sqlite_handler: SQLiteLogHandler | None = None
_sqlite_attempted = False
_setup_lock = threading.Lock()
_configured: set[str] = set()


class _AdminBroadcastHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.setFormatter(_ScrubbingFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            _recent_logs.append(line)
            with _listeners_lock:
                subs = list(_listeners)
            for fn in subs:
                try:
                    fn(line)
                except Exception:
                    pass
        except Exception:
            pass


_broadcast_handler = _AdminBroadcastHandler()


def register_log_listener(fn: Callable[[str], None]) -> None:
    with _listeners_lock:
        if fn not in _listeners:
            _listeners.append(fn)


def unregister_log_listener(fn: Callable[[str], None]) -> None:
    with _listeners_lock:
        if fn in _listeners:
            _listeners.remove(fn)


def get_recent_admin_logs(limit: int = 200) -> list[str]:
    capped = max(1, min(int(limit), 500))
    return list(_recent_logs)[-capped:]


def get_logger(name: str = "cortex") -> logging.Logger:
    global _sqlite_handler, _sqlite_attempted
    full_name = name if name.startswith("cortex") else f"cortex.{name}"
    with _setup_lock:
        root_cortex = logging.getLogger("cortex")
        if "cortex" not in _configured:
            lvl = getattr(logging, settings.LOG_LEVEL, logging.INFO)
            root_cortex.setLevel(lvl)
            root_cortex.propagate = False
            fmt = _ScrubbingFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)

            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            root_cortex.addHandler(sh)

            settings.LOG_DIR.mkdir(parents=True, exist_ok=True)
            fh = RotatingFileHandler(
                settings.LOG_DIR / f"{settings.PROJECT_SLUG}.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            root_cortex.addHandler(fh)
            root_cortex.addHandler(_broadcast_handler)

            if not _sqlite_attempted:
                _sqlite_attempted = True
                target_db = settings.CENTRAL_LOG_DB
                if sys.platform == "win32" and target_db.startswith("/LOGS-PROJETOS"):
                    target_db = str(settings.LOG_DIR / "logs.db")
                try:
                    _sqlite_handler = SQLiteLogHandler(db_path=target_db)
                    _sqlite_handler.setFormatter(fmt)
                    root_cortex.addHandler(_sqlite_handler)
                except Exception:
                    _sqlite_handler = None

            _configured.add("cortex")
        return logging.getLogger(full_name)
