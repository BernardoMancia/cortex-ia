"""
SQLite Log Handler para o Projeto Córtex.

Grava registros de log em um banco de dados SQLite centralizado:

    /LOGS-PROJETOS/<nome-do-projeto>/logs.db

Schema: (timestamp, level, source, message, raw_line)
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Final

_DEFAULT_DB_PATH: Final[str] = "/LOGS-PROJETOS/cortex-ia/logs.db"

_CREATE_TABLE_SQL: Final[str] = """
CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT    NOT NULL,
    level     TEXT    NOT NULL,
    source    TEXT    NOT NULL,
    message   TEXT    NOT NULL,
    raw_line  TEXT    NOT NULL
);
"""

_INSERT_SQL: Final[str] = """
INSERT INTO logs (timestamp, level, source, message, raw_line)
VALUES (?, ?, ?, ?, ?);
"""


class SQLiteLogHandler(logging.Handler):
    """
    A :class:`logging.Handler` that writes log records into a SQLite
    database.  All writes happen on a dedicated background thread so
    the caller is never blocked by disk I/O.

    Parameters
    ----------
    db_path : str
        Full path to the SQLite database file.
        Parent directories are created automatically.
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH) -> None:
        super().__init__()
        self.db_path = db_path
        self._queue: SimpleQueue[logging.LogRecord | None] = SimpleQueue()
        self._closed = False

        # Ensure directory exists
        Path(os.path.dirname(db_path)).mkdir(parents=True, exist_ok=True)

        # Bootstrap schema
        self._init_db()

        # Background writer thread
        self._writer = threading.Thread(
            target=self._write_loop,
            name="log-db-writer",
            daemon=True,
        )
        self._writer.start()

    # ── DB bootstrap ──────────────────────────────────────
    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(_CREATE_TABLE_SQL)
            conn.commit()
        finally:
            conn.close()

    # ── Background writer ─────────────────────────────────
    def _write_loop(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            while True:
                try:
                    record = self._queue.get(timeout=1.0)
                except Empty:
                    continue
                if record is None:          # Sentinel → exit
                    break
                try:
                    ts = datetime.fromtimestamp(
                        record.created, tz=timezone.utc,
                    ).isoformat()
                    raw_line = self.format(record) if self.formatter else record.getMessage()
                    conn.execute(_INSERT_SQL, (
                        ts,
                        record.levelname,
                        record.name,
                        record.getMessage(),
                        raw_line,
                    ))
                    conn.commit()
                except Exception:
                    # Silently drop — never crash the writer thread
                    pass
        finally:
            conn.close()

    # ── Handler interface ─────────────────────────────────
    def emit(self, record: logging.LogRecord) -> None:
        if not self._closed:
            self._queue.put(record)

    def close(self) -> None:
        self._closed = True
        self._queue.put(None)       # Sentinel to stop the writer
        self._writer.join(timeout=3)
        super().close()
