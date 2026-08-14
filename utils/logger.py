"""
Sistema de logging estruturado do Projeto Córtex.

Fornece loggers com rotação de arquivo e saída colorida no console.
Cada módulo deve obter seu logger via get_logger(__name__).
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

# ────────────────────────────────────────────────────────────
# Constantes
# ────────────────────────────────────────────────────────────
_MAX_BYTES: Final[int] = 10 * 1024 * 1024  # 10 MB
_BACKUP_COUNT: Final[int] = 5
_LOG_FORMAT: Final[str] = "[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s"
_DATE_FORMAT: Final[str] = "%Y-%m-%d %H:%M:%S"

# ────────────────────────────────────────────────────────────
# Cores ANSI para console (apenas plataformas que suportam)
# ────────────────────────────────────────────────────────────
_COLORS: dict[int, str] = {
    logging.DEBUG: "\033[36m",      # Ciano
    logging.INFO: "\033[32m",       # Verde
    logging.WARNING: "\033[33m",    # Amarelo
    logging.ERROR: "\033[31m",      # Vermelho
    logging.CRITICAL: "\033[1;31m", # Vermelho negrito
}
_RESET: Final[str] = "\033[0m"


def _supports_color() -> bool:
    """
    Verifica se o terminal suporta cores ANSI.

    Returns:
        True se o terminal suportar saída colorida.
    """
    # Força desativação via variável de ambiente
    if os.getenv("NO_COLOR"):
        return False

    # Windows: verifica se é Windows Terminal ou ConEmu
    if sys.platform == "win32":
        return (
            "WT_SESSION" in os.environ
            or "ANSICON" in os.environ
            or os.getenv("TERM_PROGRAM") == "vscode"
            or hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        )

    # Unix: verifica se é TTY
    return hasattr(sys.stderr, "isatty") and sys.stderr.isatty()


class _ColorFormatter(logging.Formatter):
    """
    Formatter que adiciona cores ANSI ao nível do log no console.

    Utilizado apenas quando o terminal suporta saída colorida.
    """

    def __init__(self, fmt: str, datefmt: str) -> None:
        super().__init__(fmt=fmt, datefmt=datefmt)
        self._use_color: bool = _supports_color()

    def format(self, record: logging.LogRecord) -> str:
        """Formata o registro com cores se suportado."""
        if self._use_color:
            color = _COLORS.get(record.levelno, "")
            original_levelname = record.levelname
            record.levelname = f"{color}{record.levelname}{_RESET}"
            result = super().format(record)
            record.levelname = original_levelname
            return result
        return super().format(record)


# Cache de loggers já configurados para evitar handlers duplicados
_configured_loggers: set[str] = set()


def setup_logger(
    name: str,
    level: str | int | None = None,
    log_dir: Path | str | None = None,
    verbose: bool = False,
) -> logging.Logger:
    """
    Configura e retorna um logger com handlers de arquivo e console.

    O logger usa RotatingFileHandler com limite de 10 MB e 5 backups,
    além de StreamHandler com saída colorida quando suportado.

    Args:
        name: Nome do logger (geralmente __name__ do módulo).
        level: Nível de logging (ex: 'DEBUG', 'INFO'). Se None, usa LOG_LEVEL do .env.
        log_dir: Diretório para arquivos de log. Se None, usa LOG_DIR do .env.
        verbose: Se True e level é None, força nível DEBUG.

    Returns:
        Logger configurado e pronto para uso.
    """
    # Evita reconfigurar o mesmo logger
    if name in _configured_loggers:
        return logging.getLogger(name)

    # Importação tardia para evitar dependência circular
    from config.settings import settings

    # Resolve nível
    if verbose and level is None:
        level = logging.DEBUG
    elif level is None:
        level = settings.LOG_LEVEL
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # Resolve diretório
    if log_dir is None:
        log_dir = settings.LOG_DIR
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Previne propagação para o root logger
    logger.propagate = False

    # ── Handler de arquivo (rotação) ──────────────────────
    # Usa o nome base do módulo para nomear o arquivo
    safe_name = name.replace(".", "_").replace("/", "_").replace("\\", "_")
    log_file = log_dir / f"{safe_name}.log"

    file_handler = RotatingFileHandler(
        filename=str(log_file),
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # ── Handler de console ────────────────────────────────
    console_handler = logging.StreamHandler(stream=sys.stderr)
    console_handler.setLevel(level)
    console_formatter = _ColorFormatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # ── Handler SQLite (apenas Linux / produção) ──────────
    if sys.platform.startswith('linux'):
        try:
            from utils.log_db_handler import SQLiteLogHandler
            sqlite_handler = SQLiteLogHandler()
            sqlite_handler.setLevel(level)
            sqlite_formatter = logging.Formatter(fmt=_LOG_FORMAT, datefmt=_DATE_FORMAT)
            sqlite_handler.setFormatter(sqlite_formatter)
            logger.addHandler(sqlite_handler)
        except Exception:
            # Falha silenciosa — não impedir o sistema de iniciar
            pass

    _configured_loggers.add(name)
    return logger


def get_logger(module_name: str) -> logging.Logger:
    """
    Função de conveniência para obter um logger configurado.

    Equivale a chamar setup_logger(module_name) com configurações padrão.

    Args:
        module_name: Nome do módulo (usar __name__).

    Returns:
        Logger configurado.

    Exemplo::

        from utils.logger import get_logger
        logger = get_logger(__name__)
        logger.info("Sistema iniciado com sucesso")
    """
    return setup_logger(module_name)
