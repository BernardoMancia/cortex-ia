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

_PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent

_env_path = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_env_path if _env_path.exists() else None)

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
            if simulation_mode is not None:
                self.SIMULATION_MODE = simulation_mode
            if verbose:
                self.VERBOSE = verbose
            return
        self._initialized = True
        self.VERBOSE: bool = verbose

        self.PROJECT_ROOT: Final[Path] = _PROJECT_ROOT
        self.VERSION: Final[str] = "2.5.0"

        self.SIMULATION_MODE: bool = (
            simulation_mode if simulation_mode is not None
            else _env_bool("SIMULATION_MODE", default=True)
        )
        self.BROKER_MODE: str = os.getenv("BROKER_MODE", "simulator").strip().lower()

        self.CAPITAL_INICIAL: Final[float] = _env_float("CAPITAL_INICIAL", 100000.00)
        self.STOP_LOSS_PERCENT: Final[float] = _env_float("STOP_LOSS_PERCENT", 0.10)
        self.MAX_POSITIONS: Final[int] = _env_int("MAX_POSITIONS", 0)
        self.MAX_CONCENTRATION: Final[float] = _env_float("MAX_CONCENTRATION", 0.25)
        self.MAX_DAILY_LOSS_PERCENT: Final[float] = _env_float("MAX_DAILY_LOSS_PERCENT", 0.03)
        self.TRAILING_STOP_TRIGGER_PERCENT: Final[float] = _env_float("TRAILING_STOP_TRIGGER_PERCENT", 0.02)
        self.TRAILING_STOP_DISTANCE_PERCENT: Final[float] = _env_float("TRAILING_STOP_DISTANCE_PERCENT", 0.015)
        self.MAX_RISK_PER_TRADE_PERCENT: Final[float] = _env_float("MAX_RISK_PER_TRADE_PERCENT", 0.02)

        self.SENTIMENT_MODE: str = os.getenv("SENTIMENT_MODE", "lightweight").strip().lower()
        self.SENTIMENT_CACHE_TTL: int = _env_int("SENTIMENT_CACHE_TTL", 1800)

        self.MT5_LOGIN: str = os.getenv("MT5_LOGIN", "")
        self.MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
        self.MT5_SERVER: str = os.getenv("MT5_SERVER", "ClearInvestimentos-Server")
        self.MT5_PATH: str = os.getenv(
            "MT5_PATH",
            r"C:\Program Files\MetaTrader 5\terminal64.exe",
        )
        self.MT5_MAGIC: int = _env_int("MT5_MAGIC", 234000)

        self.TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "").strip()
        self.TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "").strip()
        
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()

        self.DASHBOARD_HOST: str = os.getenv("DASHBOARD_HOST", "0.0.0.0")
        self.DASHBOARD_PORT: int = _env_int("DASHBOARD_PORT", 8003)

        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        self.LOG_DIR: Path = _PROJECT_ROOT / os.getenv("LOG_DIR", "logs")

        self.LOG_DIR.mkdir(parents=True, exist_ok=True)

        self.DB_PATH: Path = _PROJECT_ROOT / "data" / "cortex.db"
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        self.TRADING_CYCLE_INTERVAL: int = _env_int("TRADING_CYCLE_INTERVAL", 60)
        self.CLOSED_CHECK_INTERVAL: int = _env_int("CLOSED_CHECK_INTERVAL", 300)
        self.HEALTH_CHECK_INTERVAL: int = _env_int("HEALTH_CHECK_INTERVAL", 120)
        self.ALERT_COOLDOWN: int = _env_int("ALERT_COOLDOWN", 1800)
        self.VOLATILITY_ALERT_THRESHOLD: float = _env_float(
            "VOLATILITY_ALERT_THRESHOLD", 5.0,
        )
        self.NEWS_REQUEST_TIMEOUT: int = _env_int("NEWS_REQUEST_TIMEOUT", 15)

        self.min_quantity: int = _env_int("MIN_QUANTITY", 1)
        self.max_quantity: int = _env_int("MAX_QUANTITY", 50000)

        self.price_cache_ttl_seconds: int = _env_int("PRICE_CACHE_TTL", 30)

        self.simulator_state_path: Path = _PROJECT_ROOT / "data" / "simulator_state.json"

        self.B3_PORTFOLIOS: Final[dict[str, list[str]]] = {
            "IBOV": [
                "PETR4", "VALE3", "ITUB4", "BBDC4", "BBAS3", "WEGE3", "RENT3", "ABEV3",
                "MGLU3", "SUZB3", "PRIO3", "B3SA3", "RDOR3", "VIVT3", "CSAN3", "GGBR4",
                "CSNA3", "TOTS3", "BPAC11", "CMIG4", "SBSP3", "HAPV3", "EQTL3", "RADL3",
                "LREN3", "BEEF3", "KLBN11", "SANB11", "BBSE3", "CXSE3", "TIMS3", "CPFE3",
                "EGIE3", "TAEE11", "ALOS3", "MULT3", "IGTI11", "CYRE3", "EZTC3", "MRVE3",
                "LWSA3", "CASH3", "COGN3", "YDUQ3", "CMIN3", "ENEV3", "SMTO3", "SLCE3",
                "RAIZ4", "DXCO3", "BRKM5", "RAIL3", "HYPE3", "FLRY3", "ASAI3", "AZZA3",
                "VIVA3", "PETR3", "BBDC3", "ITSA4", "UGPA3", "VBBR3", "USIM5", "GOAU4",
                "RECV3", "BRAV3"
            ],
            "IDIV": [
                "BBAS3", "TAEE11", "CMIG4", "EGIE3", "VIVT3", "BBSE3", "CXSE3", "SANB11",
                "ITSA4", "PSSA3", "CSMG3", "SAPR11", "ALUP11", "UNIP6", "FESA4", "KEPL3",
                "LEVE3", "VALE3", "PETR4", "BBDC4", "ITUB4"
            ],
            "SMLL": [
                "POMO4", "KEPL3", "LEVE3", "TUPY3", "UNIP6", "POSI3", "RANI3", "MYPK3",
                "WIZC3", "ROMI3", "SHUL4", "TGMA3", "LOGN3", "TEND3", "DIRR3", "CURY3",
                "PLPL3", "LAVV3", "TRIS3", "JHSF3", "LOGG3", "EVEN3", "BLAU3",
                "ANIM3", "SEER3", "INTB3", "BMOB3", "MDIA3", "CAML3", "AURA33"
            ]
        }

        all_unique = []
        for p_list in self.B3_PORTFOLIOS.values():
            for t in p_list:
                if t not in all_unique:
                    all_unique.append(t)
        self.B3_PORTFOLIOS["ALL"] = all_unique

        custom_watchlist = os.getenv("WATCHLIST", "").strip()
        portfolio_mode = os.getenv("WATCHLIST_PORTFOLIO", "ALL").strip().upper()

        if custom_watchlist:
            self.WATCHLIST: list[str] = [t.strip().upper() for t in custom_watchlist.split(",") if t.strip()]
        elif portfolio_mode in self.B3_PORTFOLIOS:
            self.WATCHLIST: list[str] = list(self.B3_PORTFOLIOS[portfolio_mode])
        else:
            self.WATCHLIST: list[str] = list(self.B3_PORTFOLIOS["ALL"])

        self.YFINANCE_SUFFIX_MAP: Final[dict[str, str]] = {
            ticker: f"{ticker}.SA" for ticker in self.WATCHLIST
        }

        self.SECTOR_MAP: Final[dict[str, str]] = {
            "PETR4": "Petróleo e Gás", "PETR3": "Petróleo e Gás", "PRIO3": "Petróleo e Gás",
            "RECV3": "Petróleo e Gás", "UGPA3": "Petróleo e Gás", "CSAN3": "Petróleo e Gás",
            "RAIZ4": "Petróleo e Gás", "BRAV3": "Petróleo e Gás", "VBBR3": "Petróleo e Gás",

            "VALE3": "Siderurgia e Mineração", "GGBR4": "Siderurgia e Mineração",
            "GOAU4": "Siderurgia e Mineração", "CSNA3": "Siderurgia e Mineração",
            "USIM5": "Siderurgia e Mineração", "CMIN3": "Siderurgia e Mineração",
            "UNIP6": "Química", "FESA4": "Siderurgia e Mineração",

            "ITUB4": "Financeiro", "BBDC4": "Financeiro", "BBDC3": "Financeiro",
            "BBAS3": "Financeiro", "SANB11": "Financeiro", "BPAC11": "Financeiro",
            "B3SA3": "Financeiro", "BBSE3": "Seguros", "CXSE3": "Seguros",
            "PSSA3": "Seguros", "ITSA4": "Financeiro", "WIZC3": "Seguros",

            "WEGE3": "Bens Industriais", "EMBR3": "Bens Industriais", "KEPL3": "Bens Industriais",
            "TUPY3": "Bens Industriais", "SHUL4": "Bens Industriais", "ROMI3": "Bens Industriais",
            "POMO4": "Bens Industriais", "MYPK3": "Bens Industriais", "LEVE3": "Bens Industriais",

            "ELET3": "Elétricas", "ELET6": "Elétricas", "CPLE6": "Elétricas",
            "CMIG4": "Elétricas", "EGIE3": "Elétricas", "TAEE11": "Elétricas",
            "TRPL4": "Elétricas", "EQTL3": "Elétricas", "ENEV3": "Elétricas",
            "CPFE3": "Elétricas", "NEOE3": "Elétricas", "SBSP3": "Saneamento",
            "SAPR11": "Saneamento", "CSMG3": "Saneamento", "ALUP11": "Elétricas",

            "MGLU3": "Varejo", "LREN3": "Varejo", "ARZZ3": "Varejo", "SOMA3": "Varejo",
            "VIVA3": "Varejo", "ALPA4": "Varejo", "ABEV3": "Bebidas", "JBSS3": "Alimentos",
            "BRFS3": "Alimentos", "MRFG3": "Alimentos", "BEEF3": "Alimentos",
            "SMTO3": "Agro", "SLCE3": "Agro", "ASAI3": "Varejo", "CRFB3": "Varejo",
            "NTCO3": "Cosméticos", "MDIA3": "Alimentos", "CAML3": "Alimentos",

            "CYRE3": "Construção", "EZTC3": "Construção", "MRVE3": "Construção",
            "DIRR3": "Construção", "CURY3": "Construção", "PLPL3": "Construção",
            "TEND3": "Construção", "LAVV3": "Construção", "TRIS3": "Construção",
            "JHSF3": "Construção", "MULT3": "Shoppings", "ALOS3": "Shoppings",
            "IGTI11": "Shoppings", "LOGG3": "Logística", "EVEN3": "Construção",

            "RDOR3": "Saúde", "HAPV3": "Saúde", "RADL3": "Saúde", "HYPE3": "Farmacêutica",
            "FLRY3": "Saúde", "ODPV3": "Saúde", "MATD3": "Saúde", "PARD3": "Saúde",
            "BLAU3": "Farmacêutica", "VVEO3": "Saúde",

            "TOTS3": "Tecnologia", "VIVT3": "Telecom", "TIMS3": "Telecom",
            "LWSA3": "Tecnologia", "POSI3": "Tecnologia", "CASH3": "Tecnologia",
            "INTB3": "Tecnologia", "BMOB3": "Tecnologia",

            "RENT3": "Locação", "RAIL3": "Transporte", "RUMO3": "Transporte",
            "CCRO3": "Concessões", "ECOR3": "Concessões", "STBP3": "Portos",
            "TGMA3": "Transporte", "LOGN3": "Transporte", "AZUL4": "Aéreo",
            "GOLL4": "Aéreo",

            "SUZB3": "Papel e Celulose", "KLBN11": "Papel e Celulose",
            "RANI3": "Papel e Celulose", "DXCO3": "Madeira e Painéis",

            "YDUQ3": "Educação", "COGN3": "Educação", "ANIM3": "Educação",
            "SEER3": "Educação",

            "AURA33": "Mineração",
        }
        self.MAX_SECTOR_EXPOSURE: Final[float] = 0.40

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

settings: Final[Settings] = Settings()
