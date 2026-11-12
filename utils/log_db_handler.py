"""
SQLite Log Handler para o Projeto Córtex.

Grava registros de log em um banco de dados SQLite centralizado:

    /LOGS-PROJETOS/<nome-do-projeto>/logs.db

Schema: (timestamp, level, source, message, raw_line)
"""

from __future__ import annotations

import atexit
import logging
import os
import re
import sqlite3
import sys
import threading
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Final
from zoneinfo import ZoneInfo

_DEFAULT_DB_PATH: Final[str] = "/LOGS-PROJETOS/cortex-ia/logs.db"
_BRT: Final[ZoneInfo] = ZoneInfo("America/Sao_Paulo")
_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"bot\d{6,}:[A-Za-z0-9_-]{20,}")

_CREATE_TABLE_SQL: Final[str] = """
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

_INSERT_SQL: Final[str] = """
INSERT INTO logs (timestamp, level, source, message, raw_line)
VALUES (?, ?, ?, ?, ?);
"""


def scrub_secrets(text: str) -> str:
    """Remove tokens sensíveis acidentais de mensagens de log."""
    if not text:
        return ""
    return _TOKEN_RE.sub("bot***:***", text)


class SQLiteLogHandler(logging.Handler):
    """
    Handler de logging assíncrono em thread dedicada com WAL, busy_timeout,
    fila limitada e commit em lote para não bloquear o loop de trading nem
    sofrer contenção com o Dashboard de Logs (porta 9000).
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH, max_queue_size: int = 10000) -> None:
        super().__init__()
        self.db_path = db_path
        self._queue: Queue[tuple[str, str, str, str, str] | None] = Queue(maxsize=max_queue_size)
        self._closed = False
        self._close_lock = threading.Lock()

        parent = os.path.dirname(db_path)
        if parent:
            Path(parent).mkdir(parents=True, exist_ok=True)

        self._init_db()

        self._writer = threading.Thread(
            target=self._write_loop,
            name="log-db-writer",
            daemon=True,
        )
        self._writer.start()
        atexit.register(self.close)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_CREATE_TABLE_SQL)
            conn.commit()
        finally:
            conn.close()

    def _write_loop(self) -> None:
        conn = self._connect()
        try:
            while True:
                try:
                    first_item = self._queue.get(timeout=1.0)
                except Empty:
                    continue
                if first_item is None:
                    break

                batch: list[tuple[str, str, str, str, str]] = [first_item]
                stop_after_batch = False
                while len(batch) < 100:
                    try:
                        nxt = self._queue.get_nowait()
                    except Empty:
                        break
                    if nxt is None:
                        stop_after_batch = True
                        break
                    batch.append(nxt)

                try:
                    conn.executemany(_INSERT_SQL, batch)
                    conn.commit()
                except Exception as exc:
                    print(f"[SQLiteLogHandler] Falha ao gravar lote em {self.db_path}: {exc}", file=sys.stderr)

                if stop_after_batch:
                    break
        finally:
            conn.close()

    def emit(self, record: logging.LogRecord) -> None:
        if self._closed:
            return
        try:
            ts = datetime.fromtimestamp(record.created, tz=_BRT).isoformat()
            msg = scrub_secrets(record.getMessage())
            raw_line = scrub_secrets(self.format(record) if self.formatter else msg)
            payload = (ts, record.levelname, record.name, msg, raw_line)
            try:
                self._queue.put_nowait(payload)
            except Full:
                pass
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        try:
            self._queue.put(None, timeout=1.0)
        except Full:
            pass
        if self._writer.is_alive():
            self._writer.join(timeout=3.0)
        super().close()
