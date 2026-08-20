"""
Módulo de configurações centrais do Projeto Córtex.

Carrega variáveis de ambiente, define constantes de negociação,
horários de mercado, feriados da B3 e lista de ativos monitorados.
Implementa padrão singleton para garantir instância única.
"""

import os
import logging
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from typing import Any, Final

from dotenv import load_dotenv

# ────────────────────────────────────────────────────────────
# Diretório raiz do projeto (dois níveis acima: config/settings.py -> raiz)
# ────────────────────────────────────────────────────────────
_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

# Carrega .env se existir
_env_path = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_env_path if _env_path.exists() else None)

# ────────────────────────────────────────────────────────────
# Timezone BRT (UTC-3)
# ────────────────────────────────────────────────────────────
BRT: Final[timezone] = timezone(timedelta(hours=-3), name="BRT")


def _env_bool(key: str, default: bool = False) -> bool:
    """Converte variável de ambiente para booleano."""
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("true", "1", "yes", "sim")


def _env_float(key: str, default: float = 0.0) -> float:
    """Converte variável de ambiente para float."""
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int = 0) -> int:
    """Converte variável de ambiente para inteiro."""
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


class Settings:
    """
    Configurações centrais do sistema Córtex.

    Carrega parâmetros de ambiente e define constantes de negociação,
    horários de mercado, feriados e lista de ativos.
    Utiliza padrão singleton — a instância global é `settings`.
    """

    _instance: "Settings | None" = None

    def __new__(cls, **kwargs: object) -> "Settings":
        """Garante instância única (singleton)."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        simulation_mode: bool | None = None,
        verbose: bool = False,
        **kwargs: object,
    ) -> None:
        if self._initialized:
            # Permitir sobrescrever simulation_mode mesmo após init
            if simulation_mode is not None:
                self.SIMULATION_MODE = simulation_mode
            if verbose:
                self.VERBOSE = verbose
            return
        self._initialized = True
        self.VERBOSE: bool = verbose

        # ── Diretório raiz ─────────────────────────────────
        self.PROJECT_ROOT: Final[Path] = _PROJECT_ROOT

        # ── Modo de execução ──────────────────────────────
        self.SIMULATION_MODE: bool = (
            simulation_mode if simulation_mode is not None
            else _env_bool("SIMULATION_MODE", default=True)
        )
        self.BROKER_MODE: str = os.getenv("BROKER_MODE", "simulator").strip().lower()

        # ── Capital e risco ───────────────────────────────
        self.CAPITAL_INICIAL: Final[float] = _env_float("CAPITAL_INICIAL", 100000.00)
        self.STOP_LOSS_PERCENT: Final[float] = _env_float("STOP_LOSS_PERCENT", 0.10)
        self.MAX_POSITIONS: Final[int] = _env_int("MAX_POSITIONS", 0)  # 0 = posições ilimitadas
        self.MAX_CONCENTRATION: Final[float] = _env_float("MAX_CONCENTRATION", 0.25)  # Máx 25% por ativo
        self.MAX_DAILY_LOSS_PERCENT: Final[float] = _env_float("MAX_DAILY_LOSS_PERCENT", 0.03)  # 3% circuit breaker
        self.TRAILING_STOP_TRIGGER_PERCENT: Final[float] = _env_float("TRAILING_STOP_TRIGGER_PERCENT", 0.02)  # +2% ativa BE
        self.TRAILING_STOP_DISTANCE_PERCENT: Final[float] = _env_float("TRAILING_STOP_DISTANCE_PERCENT", 0.015)  # 1.5% trail
        self.MAX_RISK_PER_TRADE_PERCENT: Final[float] = _env_float("MAX_RISK_PER_TRADE_PERCENT", 0.02)  # 2% do capital

        # ── Análise de sentimento ─────────────────────────
        self.SENTIMENT_MODE: str = os.getenv("SENTIMENT_MODE", "lightweight").strip().lower()
        self.SENTIMENT_CACHE_TTL: int = _env_int("SENTIMENT_CACHE_TTL", 1800)  # 30 minutos por padrão

        # ── MetaTrader 5 ──────────────────────────────────
        self.MT5_LOGIN: str = os.getenv("MT5_LOGIN", "")
        self.MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
        self.MT5_SERVER: str = os.getenv("MT5_SERVER", "ClearInvestimentos-Server")
        self.MT5_PATH: str = os.getenv(
            "MT5_PATH",
            r"C:\Program Files\MetaTrader 5\terminal64.exe",
        )
        self.MT5_MAGIC: int = _env_int("MT5_MAGIC", 234000)

        # ── Telegram ──────────────────────────────────────
        self.TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "").strip()
        self.TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
        
        # ── IA (Gemini) ───────────────────────────────────
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()

        # ── Dashboard ─────────────────────────────────────
        self.DASHBOARD_HOST: str = os.getenv("DASHBOARD_HOST", "0.0.0.0")
        self.DASHBOARD_PORT: int = _env_int("DASHBOARD_PORT", 8003)

        # ── Logging ───────────────────────────────────────
        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        self.LOG_DIR: Path = _PROJECT_ROOT / os.getenv("LOG_DIR", "logs")

        # Cria diretório de logs se não existir
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)

        # ── Banco de dados ────────────────────────────────
        self.DB_PATH: Path = _PROJECT_ROOT / "data" / "cortex.db"
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        # ── Intervalos de operação ────────────────────────
        self.TRADING_CYCLE_INTERVAL: int = _env_int("TRADING_CYCLE_INTERVAL", 60)
        self.CLOSED_CHECK_INTERVAL: int = _env_int("CLOSED_CHECK_INTERVAL", 300)
        self.HEALTH_CHECK_INTERVAL: int = _env_int("HEALTH_CHECK_INTERVAL", 120)
        self.ALERT_COOLDOWN: int = _env_int("ALERT_COOLDOWN", 1800)
        self.VOLATILITY_ALERT_THRESHOLD: float = _env_float(
            "VOLATILITY_ALERT_THRESHOLD", 5.0,
        )
        self.NEWS_REQUEST_TIMEOUT: int = _env_int("NEWS_REQUEST_TIMEOUT", 15)



        # ── Lotes e Quantidades ───────────────────────────
        self.min_quantity: int = _env_int("MIN_QUANTITY", 1)
        self.max_quantity: int = _env_int("MAX_QUANTITY", 50000)


        # ── Cache ─────────────────────────────────────────
        self.price_cache_ttl_seconds: int = _env_int("PRICE_CACHE_TTL", 30)

        # ── Simulador ────────────────────────────────────
        self.simulator_state_path: Path = _PROJECT_ROOT / "data" / "simulator_state.json"

        # ── Propriedades de acesso unificado ─────────────
        # (aliases em snake_case para compatibilidade com todo o codebase)

        # ── Watchlist ─────────────────────────────────────
        self.WATCHLIST: Final[list[str]] = [
            "PETR4", "VALE3", "ITUB4", "BBDC4", "BBAS3",
            "WEGE3", "RENT3", "ABEV3", "MGLU3", "SUZB3",
            "EMBR3", "PRIO3", "B3SA3", "RDOR3", "VIVT3",
            "CSAN3", "GGBR4", "CSNA3", "TOTS3", "BPAC11",
        ]

        # ── Mapeamento de tickers para o Yahoo Finance ────
        self.YFINANCE_SUFFIX_MAP: Final[dict[str, str]] = {
            ticker: f"{ticker}.SA" for ticker in self.WATCHLIST
        }

        # ── Setores (Correlação e Risco) ──────────────────
        self.SECTOR_MAP: Final[dict[str, str]] = {
            "PETR4": "Petróleo e Gás",
            "PRIO3": "Petróleo e Gás",
            "CSAN3": "Petróleo e Gás",
            "VALE3": "Siderurgia e Mineração",
            "GGBR4": "Siderurgia e Mineração",
            "CSNA3": "Siderurgia e Mineração",
            "ITUB4": "Financeiro",
            "BBDC4": "Financeiro",
            "BBAS3": "Financeiro",
            "BPAC11": "Financeiro",
            "B3SA3": "Financeiro",
            "WEGE3": "Bens Industriais",
            "EMBR3": "Bens Industriais",
            "RENT3": "Locação",
            "ABEV3": "Bebidas",
            "MGLU3": "Varejo",
            "SUZB3": "Papel e Celulose",
            "RDOR3": "Saúde",
            "VIVT3": "Telecom",
            "TOTS3": "Tecnologia",
        }
        self.MAX_SECTOR_EXPOSURE: Final[float] = 0.40  # Máximo de 40% da carteira por setor



    def __getattr__(self, name: str) -> Any:
        """Permite acesso snake_case para propriedades UPPERCASE (ex: settings.capital_inicial)."""
        upper_name = name.upper()
        if upper_name in self.__dict__:
            return self.__dict__[upper_name]
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")


    def __repr__(self) -> str:
        mode = "SIMULAÇÃO" if self.SIMULATION_MODE else "PRODUÇÃO"
        return (
            f"Settings(mode={mode}, capital=R${self.CAPITAL_INICIAL:.2f}, "
            f"stop_loss={self.STOP_LOSS_PERCENT:.0%}, "
            f"watchlist={len(self.WATCHLIST)} ativos, "
            f"sentiment={self.SENTIMENT_MODE})"
        )


# ────────────────────────────────────────────────────────────
# Instância singleton — importar com: from config import settings
# ────────────────────────────────────────────────────────────
settings: Final[Settings] = Settings()
