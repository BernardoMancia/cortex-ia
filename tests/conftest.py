"""
Fixtures compartilhadas e isolamento hermético para os testes do Projeto Córtex.
"""

from __future__ import annotations

import math
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Generator

import pytest

# Isola logs SQLite centralizados durante execuções do pytest em servidores Linux (/LOGS-PROJETOS)
os.environ.setdefault(
    "CORTEX_LOG_DB_PATH",
    str(Path(tempfile.gettempdir()) / "cortex_pytest_logs.db"),
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import settings
from models.data_models import Action, BRT, Decision, OHLCV, Position


@pytest.fixture(autouse=True)
def isolated_test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path, None, None]:
    """
    Garante isolamento total por teste:
    - Redireciona SQLite (`settings.DB_PATH`) e estado do simulador para `tmp_path`.
    - Desativa credenciais externas reais (Telegram/Gemini/MT5 Bridge) por padrão.
    - Reseta rate limiters em memória e restaura `settings` ao final do teste.
    """
    original_dict = dict(settings.__dict__)

    test_db = tmp_path / "cortex_test.db"
    test_sim_state = tmp_path / "simulator_state_test.json"

    settings.DB_PATH = str(test_db)
    settings.simulator_state_path = str(test_sim_state)
    settings.SIMULATION_MODE = True
    settings.MT5_BRIDGE_URL = ""
    settings.TELEGRAM_BOT_TOKEN = ""
    settings.TELEGRAM_TOKEN = ""
    settings.TELEGRAM_CHAT_ID = ""
    settings.GEMINI_API_KEY = ""
    # Defaults determinísticos para testes sintéticos independentes do relógio de parede;
    # test_v290_real_money.py exercita explicitamente cada proteção ativada.
    settings.AVOID_DAY_TRADE_TAX = False
    settings.OPENING_COOLDOWN_MINUTES = 0
    settings.ENABLE_MTF_CONFIRMATION = False
    if hasattr(settings, "ALLOWED_ORIGINS"):
        settings.ALLOWED_ORIGINS = (
            "http://testserver,https://testserver,http://localhost:8003,http://127.0.0.1:8003"
        )

    # Limpa caches de inicialização de banco, caso existam
    try:
        import data.database as db_mod
        from data.database import DatabaseManager

        if hasattr(db_mod, "_INITIALIZED_PATHS"):
            db_mod._INITIALIZED_PATHS.clear()
        for cache_attr in ("_initialized_paths", "_INITIALIZED_PATHS"):
            cache_obj = getattr(DatabaseManager, cache_attr, None)
            if cache_obj is not None:
                cache_obj.clear()
    except Exception:
        pass

    # Reseta rate limiters globais do módulo auth entre testes
    try:
        import auth as auth_mod

        for attr_name in ("rate_limiter", "login_limiter", "first_login_limiter", "ip_limiter", "user_limiter"):
            limiter = getattr(auth_mod, attr_name, None)
            if limiter is not None:
                if hasattr(limiter, "_failures"):
                    limiter._failures.clear()
                if hasattr(limiter, "_locked_until"):
                    limiter._locked_until.clear()
                if hasattr(limiter, "_attempts"):
                    limiter._attempts.clear()
    except Exception:
        pass

    # Reseta engine/db globais das rotas do dashboard caso tenham sido cacheados em outro tmp_path
    try:
        import dashboard.routes as routes_mod

        if getattr(routes_mod, "_engine", None) is not None:
            try:
                routes_mod._engine.stop()
            except Exception:
                pass
            routes_mod._engine = None
    except Exception:
        pass

    yield tmp_path

    try:
        import dashboard.routes as routes_mod

        if getattr(routes_mod, "_engine", None) is not None:
            try:
                routes_mod._engine.stop()
            except Exception:
                pass
            routes_mod._engine = None
    except Exception:
        pass

    settings.__dict__.clear()
    settings.__dict__.update(original_dict)


@pytest.fixture
def sample_position() -> Position:
    """Posição de exemplo para testes."""
    return Position(
        ticker="PETR4",
        quantity=10,
        entry_price=30.00,
        stop_loss=27.00,
        current_price=30.00,
    )


@pytest.fixture
def sample_position_below_sl() -> Position:
    """Posição com preço abaixo do stop-loss."""
    return Position(
        ticker="VALE3",
        quantity=5,
        entry_price=60.00,
        stop_loss=54.00,
        current_price=53.00,
    )


@pytest.fixture
def sample_decision_buy() -> Decision:
    """Decisão de compra de exemplo."""
    return Decision(
        ticker="WEGE3",
        action=Action.BUY,
        confidence=0.75,
        reasoning="Convergência técnica + sentimento positivo",
        technical_score=0.8,
        sentiment_score=0.5,
        quantity=5,
        price=40.00,
        stop_loss=36.00,
    )


@pytest.fixture
def sample_ohlcv_data() -> list[OHLCV]:
    """Dados OHLCV com 70 candles em tendência de alta saudável (pullbacks naturais, RSI ~ 58-65, MACD > 0)."""
    candles: list[OHLCV] = []
    base_time = datetime(2026, 6, 1, 10, 0, tzinfo=BRT)
    # Padrão realista: 2 dias de alta, 1 dia de leve realização, acelerando suavemente nos últimos 5 pregões
    pattern = [0.65, 0.55, -0.75, 0.70, -0.45]
    price = 100.0

    for i in range(70):
        if i < 63:
            delta = pattern[i % len(pattern)]
        elif i < 66:
            delta = -0.35  # breve consolidação para manter RSI saudável (< 68)
        else:
            delta = 0.65   # retomada altista limpa no fim (EMA9 > EMA21 > EMA50 e MACD_HIST > 0)
        price = round(price + delta, 2)
        vol = 1_000_000 if i < 65 else 1_350_000
        candles.append(
            OHLCV(
                ticker="PETR4",
                timestamp=base_time + timedelta(days=i),
                open=round(price - 0.25, 2),
                high=round(price + 0.70, 2),
                low=round(price - 0.65, 2),
                close=price,
                volume=vol,
            )
        )

    return candles


@pytest.fixture
def bearish_ohlcv_data() -> list[OHLCV]:
    """Dados OHLCV com 70 candles em tendência de baixa consistente (repiques naturais, MACD < 0)."""
    candles: list[OHLCV] = []
    base_time = datetime(2026, 6, 1, 10, 0, tzinfo=BRT)
    pattern = [-0.65, -0.55, 0.75, -0.70, 0.45]
    price = 130.0

    for i in range(70):
        if i < 63:
            delta = pattern[i % len(pattern)]
        elif i < 66:
            delta = 0.35
        else:
            delta = -0.70  # retomada baixista limpa no fim (EMA9 < EMA21 < EMA50 e MACD_HIST < 0)
        price = round(price + delta, 2)
        vol = 900_000 if i < 65 else 1_250_000
        candles.append(
            OHLCV(
                ticker="MGLU3",
                timestamp=base_time + timedelta(days=i),
                open=round(price + 0.25, 2),
                high=round(price + 0.65, 2),
                low=round(price - 0.70, 2),
                close=price,
                volume=vol,
            )
        )

    return candles
