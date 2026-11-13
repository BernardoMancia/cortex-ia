from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from config.b3_universe import ALL_B3_TICKERS, B3_PORTFOLIOS, SECTOR_MAP
from config.env_profile import (
    CENTRAL_LOG_DB,
    CYCLE_SECOND_OFFSET,
    DEFAULT_CYCLE_INTERVAL,
    DEFAULT_PORT,
    ENVIRONMENT_ID,
    ENVIRONMENT_LABEL,
    FORCE_MAINTENANCE_ON_BOOT,
    IS_PRODUCTION,
    IS_SIMULATION,
    PROJECT_SLUG,
)

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
_env_file = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_env_file if _env_file.exists() else None)

BRT: Final[ZoneInfo] = ZoneInfo("America/Sao_Paulo")
OWNER_USERNAME: Final[str] = "Luke"
OWNER_USER_ID: Final[int] = 1


def _env_bool(key: str, default: bool = False) -> bool:
    val = os.getenv(key, str(default)).strip().lower()
    return val in ("true", "1", "yes", "sim")


def _env_float(key: str, default: float = 0.0) -> float:
    try:
        res = float(os.getenv(key, str(default)))
        return res if math.isfinite(res) else default
    except (ValueError, TypeError):
        return default


def _env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


class Settings:
    def __init__(self) -> None:
        self.PROJECT_ROOT: Path = PROJECT_ROOT
        self.VERSION: str = "0.2.0"
        self.ENVIRONMENT_ID: str = ENVIRONMENT_ID
        self.ENVIRONMENT_LABEL: str = ENVIRONMENT_LABEL
        self.IS_PRODUCTION: bool = IS_PRODUCTION
        self.IS_SIMULATION: bool = IS_SIMULATION
        self.PROJECT_SLUG: str = PROJECT_SLUG
        self.CENTRAL_LOG_DB: str = os.getenv("CENTRAL_LOG_DB", CENTRAL_LOG_DB).strip() or CENTRAL_LOG_DB
        self.FORCE_MAINTENANCE_ON_BOOT: bool = FORCE_MAINTENANCE_ON_BOOT
        self.CYCLE_SECOND_OFFSET: int = CYCLE_SECOND_OFFSET

        self.DATA_DIR: Path = Path(os.getenv("CORTEX_DATA_DIR", str(PROJECT_ROOT / "data")))
        self.KEYS_DIR: Path = Path(os.getenv("CORTEX_KEYS_DIR", str(self.DATA_DIR / ".keys")))
        self.DB_PATH: Path = Path(os.getenv("CORTEX_DB_PATH", str(self.DATA_DIR / "cortex.db")))
        self.LOG_DIR: Path = Path(os.getenv("LOG_DIR", str(PROJECT_ROOT / "logs")))
        self.BACKUPS_DIR: Path = Path(os.getenv("CORTEX_BACKUPS_DIR", str(PROJECT_ROOT / "backups")))

        for d in (self.DATA_DIR, self.KEYS_DIR, self.LOG_DIR, self.BACKUPS_DIR):
            d.mkdir(parents=True, exist_ok=True)

        self.DASHBOARD_HOST: str = os.getenv("DASHBOARD_HOST", "0.0.0.0").strip() or "0.0.0.0"
        self.DASHBOARD_PORT: int = _env_int("DASHBOARD_PORT", DEFAULT_PORT)
        self.ALLOWED_ORIGINS: list[str] = [
            o.strip().rstrip("/")
            for o in os.getenv(
                "ALLOWED_ORIGINS",
                f"http://localhost:{self.DASHBOARD_PORT},http://127.0.0.1:{self.DASHBOARD_PORT}",
            ).split(",")
            if o.strip()
        ]
        self.COOKIE_SECURE: bool = _env_bool("COOKIE_SECURE", default=False)
        self.MAX_REQUEST_BODY_BYTES: int = _env_int("MAX_REQUEST_BODY_BYTES", 65536)

        self.OWNER_USERNAME: str = OWNER_USERNAME
        self.OWNER_USER_ID: int = OWNER_USER_ID
        self.LUKE_BOOTSTRAP_PASSWORD: str = os.getenv("LUKE_BOOTSTRAP_PASSWORD", "Luke").strip() or "Luke"

        self.AUTH_MAX_IDLE_SECONDS: int = _env_int("AUTH_MAX_IDLE_SECONDS", 900)
        self.AUTH_MAX_SESSION_SECONDS: int = _env_int("AUTH_MAX_SESSION_SECONDS", 28800)
        self.AUTH_MAX_LOGIN_ATTEMPTS: int = _env_int("AUTH_MAX_LOGIN_ATTEMPTS", 5)
        self.AUTH_LOCKOUT_SECONDS: int = _env_int("AUTH_LOCKOUT_SECONDS", 900)
        self.AUTH_MAX_CONCURRENT_SESSIONS: int = _env_int("AUTH_MAX_CONCURRENT_SESSIONS", 3)
        self.STEP_UP_TTL_SECONDS: int = _env_int("STEP_UP_TTL_SECONDS", 180)
        self.INVITE_EXPIRY_HOURS: int = _env_int("INVITE_EXPIRY_HOURS", 24)

        self.DEFAULT_SIM_CAPITAL: float = max(100.0, _env_float("CAPITAL_INICIAL", 100000.0))
        self.STOP_LOSS_PERCENT: float = max(0.01, min(0.50, _env_float("STOP_LOSS_PERCENT", 0.10)))
        self.TRAILING_STOP_TRIGGER_PERCENT: float = max(0.005, min(0.30, _env_float("TRAILING_STOP_TRIGGER_PERCENT", 0.02)))
        self.TRAILING_STOP_DISTANCE_PERCENT: float = max(0.005, min(0.20, _env_float("TRAILING_STOP_DISTANCE_PERCENT", 0.015)))
        if self.TRAILING_STOP_DISTANCE_PERCENT >= self.TRAILING_STOP_TRIGGER_PERCENT:
            self.TRAILING_STOP_DISTANCE_PERCENT = round(self.TRAILING_STOP_TRIGGER_PERCENT * 0.75, 4)

        self.MAX_CONCENTRATION: float = _env_float("MAX_CONCENTRATION", 0.25)
        self.MAX_SECTOR_EXPOSURE: float = _env_float("MAX_SECTOR_EXPOSURE", 0.40)
        self.MAX_DAILY_LOSS_PERCENT: float = _env_float("MAX_DAILY_LOSS_PERCENT", 0.03)
        self.MAX_RISK_PER_TRADE_PERCENT: float = _env_float("MAX_RISK_PER_TRADE_PERCENT", 0.02)
        self.MAX_SPREAD_PERCENT: float = _env_float("MAX_SPREAD_PERCENT", 0.008)
        self.MAX_SLIPPAGE_PERCENT: float = _env_float("MAX_SLIPPAGE_PERCENT", 0.003)
        self.B3_FEE_RATE: float = _env_float("B3_FEE_RATE", 0.0003)
        self.AVOID_DAY_TRADE_TAX: bool = _env_bool("AVOID_DAY_TRADE_TAX", default=True)
        self.PROTECT_B3_20K_EXEMPTION: bool = _env_bool("PROTECT_B3_20K_EXEMPTION", default=True)
        self.B3_SWING_EXEMPTION_LIMIT: float = _env_float("B3_SWING_EXEMPTION_LIMIT", 20000.0)
        self.B3_SWING_EXEMPTION_GUARD_BUFFER: float = _env_float("B3_SWING_EXEMPTION_GUARD_BUFFER", 19500.0)

        self.MAX_SINGLE_ORDER_NOTIONAL: float = _env_float("MAX_SINGLE_ORDER_NOTIONAL", 250000.0)
        self.MAX_ORDERS_PER_MINUTE: int = max(2, _env_int("MAX_ORDERS_PER_MINUTE", 12))
        self.STALE_QUOTE_MAX_SECONDS: int = max(15, _env_int("STALE_QUOTE_MAX_SECONDS", 90))
        self.WALLET_EXECUTION_TIMEOUT_SECONDS: float = max(2.0, _env_float("WALLET_EXECUTION_TIMEOUT_SECONDS", 12.0))
        self.OPENING_COOLDOWN_MINUTES: int = _env_int("OPENING_COOLDOWN_MINUTES", 15)
        self.MARKET_REGIME_MAX_DROP_PCT: float = _env_float("MARKET_REGIME_MAX_DROP_PCT", -1.5)
        self.ENABLE_MTF_CONFIRMATION: bool = _env_bool("ENABLE_MTF_CONFIRMATION", default=True)

        self.TRADING_CYCLE_INTERVAL: int = max(15, _env_int("TRADING_CYCLE_INTERVAL", DEFAULT_CYCLE_INTERVAL))
        self.CLOSED_CHECK_INTERVAL: int = max(30, _env_int("CLOSED_CHECK_INTERVAL", 300))
        self.HEALTH_CHECK_INTERVAL: int = max(30, _env_int("HEALTH_CHECK_INTERVAL", 120))
        self.DECISION_RETENTION_DAYS: int = max(3, _env_int("DECISION_RETENTION_DAYS", 14))
        self.NEWS_REQUEST_TIMEOUT: int = max(3, _env_int("NEWS_REQUEST_TIMEOUT", 12))
        self.PRICE_CACHE_TTL: int = max(5, _env_int("PRICE_CACHE_TTL", 30))
        self.OHLCV_CACHE_TTL: int = max(30, _env_int("OHLCV_CACHE_TTL", 300))
        self.SENTIMENT_CACHE_TTL: int = max(60, _env_int("SENTIMENT_CACHE_TTL", 1800))
        self.SENTIMENT_MODE: str = os.getenv("SENTIMENT_MODE", "lightweight").strip().lower() or "lightweight"
        self.GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()

        self.MT5_BRIDGE_URL: str = os.getenv("MT5_BRIDGE_URL", "http://127.0.0.1:5000").strip().rstrip("/")
        self.BRIDGE_API_KEY: str = os.getenv("BRIDGE_API_KEY", "").strip()
        self.MT5_LOGIN: str = os.getenv("MT5_LOGIN", "").strip()
        self.MT5_PASSWORD: str = os.getenv("MT5_PASSWORD", "")
        self.MT5_SERVER: str = os.getenv("MT5_SERVER", "ClearInvestimentos-CLEAR").strip()
        self.MT5_MAGIC: int = _env_int("MT5_MAGIC", 234000)

        self.TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "").strip()
        self.TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        raw_admins = os.getenv("TELEGRAM_ADMIN_USER_IDS", "").strip()
        parsed_admins: set[int] = set()
        for part in raw_admins.split(","):
            p = part.strip()
            if p.lstrip("-").isdigit():
                val = int(p)
                if val > 0:
                    parsed_admins.add(val)
        if not parsed_admins and self.TELEGRAM_CHAT_ID.isdigit():
            parsed_admins.add(int(self.TELEGRAM_CHAT_ID))
        self.TELEGRAM_ADMIN_USER_IDS: frozenset[int] = frozenset(parsed_admins)

        self.LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"

        self.B3_PORTFOLIOS: dict[str, list[str]] = {
            **B3_PORTFOLIOS,
            "ALL": list(ALL_B3_TICKERS),
        }
        custom_wl = os.getenv("WATCHLIST", "").strip()
        portfolio_mode = os.getenv("WATCHLIST_PORTFOLIO", "ALL").strip().upper()
        if custom_wl:
            seen: list[str] = []
            for item in custom_wl.split(","):
                t = item.strip().upper()
                if t and t not in seen:
                    seen.append(t)
            self.WATCHLIST: list[str] = seen
        elif portfolio_mode in self.B3_PORTFOLIOS:
            self.WATCHLIST = list(self.B3_PORTFOLIOS[portfolio_mode])
        else:
            self.WATCHLIST = list(ALL_B3_TICKERS)
        self.SECTOR_MAP: dict[str, str] = dict(SECTOR_MAP)


settings = Settings()
