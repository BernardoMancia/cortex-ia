"""
Sistema de logging estruturado do Projeto Córtex.

Fornece loggers com rotação de arquivo, saída colorida no console,
streaming WebSocket em memória e persistência SQLite singleton (/LOGS-PROJETOS).
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Final

from utils.log_db_handler import SQLiteLogHandler, scrub_secrets

_MAX_BYTES: Final[int] = 10 * 1024 * 1024
_BACKUP_COUNT: Final[int] = 5
_LOG_FORMAT: Final[str] = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"

_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",
    logging.INFO: "\033[32m",
    logging.WARNING: "\033[33m",
    logging.ERROR: "\033[31m",
    logging.CRITICAL: "\033[1;31m",
}
_RESET: Final[str] = "\033[0m"


def _supports_color() -> bool:
    """Verifica se o terminal suporta cores ANSI."""
    if os.getenv("NO_COLOR"):
        return False

    if sys.platform == "win32":
        return (
            "WT_SESSION" in os.environ
            or "ANSICON" in os.environ
            or os.getenv("TERM_PROGRAM") == "vscode"
            or (hasattr(sys.stderr, "isatty") and sys.stderr.isatty())
        )

    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


class _ScrubbingFormatter(logging.Formatter):
    """Formatter que mascara tokens sensíveis automaticamente."""

    def format(self, record: logging.LogRecord) -> str:
        return scrub_secrets(super().format(record))


class _ColorFormatter(_ScrubbingFormatter):
    """Formatter que adiciona cores ANSI ao nível do log no console."""

    def __init__(self, fmt: str, datefmt: str) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._use_color: bool = _supports_color()

    def format(self, record: logging.LogRecord) -> str:
        if self._use_color:
            color = _COLORS.get(record.levelno, "")
            original_levelname = record.levelname
            record.levelname = f"{color}{record.levelname}{_RESET}"
            try:
                return super().format(record)
            finally:
                record.levelname = original_levelname
        return super().format(record)


_recent_logs: deque[str] = deque(maxlen=400)
_log_listeners: list[Callable[[str], None]] = []
_listeners_lock = threading.Lock()


class LogBroadcastHandler(logging.Handler):
    """Handler que armazena mensagens recentes em buffer e notifica listeners em tempo real."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(_ScrubbingFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _recent_logs.append(msg)
            with _listeners_lock:
                listeners = list(_log_listeners)
            for listener in listeners:
                try:
                    listener(msg)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)


_broadcast_handler = LogBroadcastHandler()
_sqlite_handler: SQLiteLogHandler | None = None
_sqlite_init_attempted: bool = False
_logger_lock = threading.Lock()
_configured_loggers: set[str] = set()


def _get_shared_sqlite_handler() -> SQLiteLogHandler | None:
    """Retorna singleton de SQLiteLogHandler em ambientes Linux (/LOGS-PROJETOS)."""
    global _sqlite_handler, _sqlite_init_attempted
    if _sqlite_init_attempted:
        return _sqlite_handler
    _sqlite_init_attempted = True
    if sys.platform.startswith("linux") or os.getenv("CORTEX_LOG_DB_PATH"):
        try:
            db_path = os.getenv("CORTEX_LOG_DB_PATH", "/LOGS-PROJETOS/cortex-ia/logs.db")
            handler = SQLiteLogHandler(db_path=db_path)
            handler.setFormatter(_ScrubbingFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
            _sqlite_handler = handler
        except Exception as exc:
            print(f"[logger] Aviso: SQLiteLogHandler indisponível ({exc})", file=sys.stderr)
    return _sqlite_handler


def get_recent_logs(limit: int = 100) -> list[str]:
    """Retorna as últimas N mensagens de log formatadas."""
    logs_list = list(_recent_logs)
    if limit > 0:
        return logs_list[-limit:]
    return logs_list


def add_log_listener(callback: Callable[[str], None]) -> None:
    """Registra uma função callback para receber novas linhas de log em tempo real."""
    with _listeners_lock:
        if callback not in _log_listeners:
            _log_listeners.append(callback)


def remove_log_listener(callback: Callable[[str], None]) -> None:
    """Remove um callback de log."""
    with _listeners_lock:
        if callback in _log_listeners:
            _log_listeners.remove(callback)


def setup_logger(
    name: str,
    level: str | int | None = None,
    log_dir: Path | str | None = None,
    verbose: bool = False,
) -> logging.Logger:
    """
    Configura e retorna um logger com handlers de arquivo, console, broadcast e SQLite singleton.
    """
    with _logger_lock:
        if name in _configured_loggers:
            existing = logging.getLogger(name)
            if verbose:
                existing.setLevel(logging.DEBUG)
            return existing

        from config.settings import settings

        if verbose and level is None:
            resolved_level: int = logging.DEBUG
        elif level is None:
            resolved_level = getattr(logging, str(settings.LOG_LEVEL).upper(), logging.INFO)
        elif isinstance(level, str):
            resolved_level = getattr(logging, level.upper(), logging.INFO)
        else:
            resolved_level = int(level)

        if log_dir is None:
            log_dir = settings.LOG_DIR
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        logger = logging.getLogger(name)
        logger.setLevel(resolved_level)
        logger.propagate = False

        safe_name = name.replace(".", "_").replace("/", "_").replace("\\", "_")
        log_file = log_dir / f"{safe_name}.log"

        file_handler = RotatingFileHandler(
            filename=str(log_file),
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(resolved_level)
        file_handler.setFormatter(_ScrubbingFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(stream=sys.stderr)
        console_handler.setLevel(resolved_level)
        console_handler.setFormatter(_ColorFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT))
        logger.addHandler(console_handler)

        logger.addHandler(_broadcast_handler)

        sqlite_handler = _get_shared_sqlite_handler()
        if sqlite_handler is not None:
            logger.addHandler(sqlite_handler)

        _configured_loggers.add(name)
        return logger


def get_logger(module_name: str) -> logging.Logger:
    """Função de conveniência para obter um logger configurado."""
    return setup_logger(module_name)
