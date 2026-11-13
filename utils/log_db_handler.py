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
_GOOGLE_AI_KEY_RE: Final[re.Pattern[str]] = re.compile(r"AIza[0-9A-Za-z_-]{30,}")
_GOOGLE_AQ_KEY_RE: Final[re.Pattern[str]] = re.compile(r"AQ\.Ab8[0-9A-Za-z_-]{8,}")
_KV_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(BRIDGE_API_KEY|GEMINI_API_KEY|MT5_PASSWORD|VNC_PASSWORD|Password)\s*[:=]\s*\S+",
    re.IGNORECASE,
)

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


class _FlushRequest:
    """Sinalizador interno para sincronizar flush da fila SQLite sem encerrar o worker."""

    __slots__ = ("done",)

    def __init__(self) -> None:
        self.done = threading.Event()


def scrub_secrets(text: str) -> str:
    """Remove tokens e credenciais sensíveis acidentais de mensagens de log."""
    if not text:
        return ""
    cleaned = _TOKEN_RE.sub("bot***:***", text)
    cleaned = _KV_SECRET_RE.sub(r"\1=***", cleaned)
    cleaned = _GOOGLE_AI_KEY_RE.sub("AIza***", cleaned)
    cleaned = _GOOGLE_AQ_KEY_RE.sub("AQ.Ab8***", cleaned)
    return cleaned


class SQLiteLogHandler(logging.Handler):
    """
    Handler de logging assíncrono em thread dedicada com WAL, busy_timeout,
    fila limitada e commit em lote para não bloquear o loop de trading nem
    sofrer contenção com o Dashboard de Logs (porta 9000).
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH, max_queue_size: int = 10000) -> None:
        super().__init__()
        self.db_path = db_path
        self._queue: Queue[tuple[str, str, str, str, str] | _FlushRequest | None] = Queue(
            maxsize=max_queue_size
        )
        self._closed = False
        self._shutting_down = False
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
            conn.executescript(_CREATE_TABLE_SQL)
            conn.commit()
        finally:
            conn.close()

    def _write_loop(self) -> None:
        conn: sqlite3.Connection | None = None
        try:
            while True:
                try:
                    first_item = self._queue.get(timeout=1.0)
                except Empty:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                    continue

                if first_item is None:
                    break

                if isinstance(first_item, _FlushRequest):
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None
                    first_item.done.set()
                    continue

                batch: list[tuple[str, str, str, str, str]] = [first_item]
                flush_events: list[threading.Event] = []
                stop_after_batch = False
                while len(batch) < 100:
                    try:
                        nxt = self._queue.get_nowait()
                    except Empty:
                        break
                    if nxt is None:
                        stop_after_batch = True
                        break
                    if isinstance(nxt, _FlushRequest):
                        flush_events.append(nxt.done)
                        break
                    batch.append(nxt)

                try:
                    if conn is None:
                        conn = self._connect()
                    conn.executemany(_INSERT_SQL, batch)
                    conn.commit()
                except Exception as exc:
                    print(f"[SQLiteLogHandler] Falha ao gravar lote em {self.db_path}: {exc}", file=sys.stderr)
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None

                if flush_events or stop_after_batch or self._queue.empty():
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
                        conn = None

                for ev in flush_events:
                    ev.set()

                if stop_after_batch:
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

    def flush(self) -> None:
        """Aguarda a gravação de todos os registros pendentes na fila."""
        if self._closed or not self._writer.is_alive():
            return
        if threading.current_thread() is self._writer:
            return
        req = _FlushRequest()
        try:
            self._queue.put(req, timeout=1.5)
            req.done.wait(timeout=3.0)
        except Full:
            pass

    def close(self) -> None:
        """
        Drena registros pendentes sem encerrar o worker prematuramente quando invocado por
        `logging.config.dictConfig` / `_clearExistingHandlers` (Uvicorn).
        """
        self.flush()
        if self._shutting_down:
            super().close()

    def close_handler(self, force: bool = False) -> None:
        """Fecha o handler opcionalmente forçando o desligamento definitivo da thread."""
        if force:
            self.shutdown()
        else:
            self.close()

    def shutdown(self) -> None:
        """Encerra definitivamente a thread de escrita SQLite (registrado via atexit)."""
        with self._close_lock:
            if self._closed:
                return
            self._shutting_down = True
            self._closed = True
        try:
            self._queue.put(None, timeout=1.0)
        except Full:
            pass
        if self._writer.is_alive() and threading.current_thread() is not self._writer:
            self._writer.join(timeout=3.0)
        super().close()
