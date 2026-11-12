import pytest
from unittest.mock import MagicMock
from config.settings import settings
from data.database import DatabaseManager
from core.engine import CortexEngine


def test_unexpected_crash_fail_safe_to_homologation(tmp_path):
    db_file = tmp_path / "cortex_crash_test.db"
    settings.DB_PATH = str(db_file)
    db = DatabaseManager(str(db_file))

    db.set_config("shutdown_status", "running")
    db.set_config("last_environment", "production")

    engine = CortexEngine(force_simulation=False)
    try:
        assert engine.settings.SIMULATION_MODE is True
        assert engine.broker.__class__.__name__ == "SimulatorBroker"
        assert db.get_config("environment") == "homologation"
    finally:
        engine.stop(graceful=True)


def test_uncaught_crash_marks_shutdown_status_crashed(tmp_path):
    db_file = tmp_path / "cortex_uncaught_test.db"
    settings.DB_PATH = str(db_file)
    db = DatabaseManager(str(db_file))

    engine = CortexEngine(force_simulation=True)
    engine.stop(graceful=False)
    assert db.get_config("shutdown_status") == "crashed"


def test_graceful_shutdown_restores_homologation(tmp_path):
    db_file = tmp_path / "cortex_graceful_test.db"
    settings.DB_PATH = str(db_file)
    db = DatabaseManager(str(db_file))

    db.set_config("shutdown_status", "graceful")
    db.set_config("last_environment", "homologation")

    engine = CortexEngine(force_simulation=False)
    try:
        assert engine.settings.SIMULATION_MODE is True
    finally:
        engine.stop(graceful=True)
    assert db.get_config("shutdown_status") == "graceful"


def test_graceful_production_failsafe_when_mt5_unavailable(tmp_path, monkeypatch):
    db_file = tmp_path / "cortex_prod_failsafe.db"
    settings.DB_PATH = str(db_file)
    db = DatabaseManager(str(db_file))

    db.set_config("shutdown_status", "graceful")
    db.set_config("last_environment", "production")
    db.set_config("environment", "production")

    monkeypatch.setattr(CortexEngine, "_create_production_broker", lambda self: None)
    engine = CortexEngine(force_simulation=False)
    try:
        # Sem MT5/Bridge real disponível, o Córtex jamais deve fingir produção com simulador
        assert engine.settings.SIMULATION_MODE is True
        assert engine.broker.__class__.__name__ == "SimulatorBroker"
    finally:
        engine.stop(graceful=True)


def test_switch_environment_sends_telegram_alert_and_validates_prod_broker(monkeypatch):
    engine = CortexEngine(force_simulation=True)
    engine.telegram = MagicMock()
    try:
        assert engine.switch_environment("homologation", user_name="Luke Arwolf") is True

        # Sem broker de produção conectado, deve recusar mudança para produção
        engine.prod_broker = None
        monkeypatch.setattr(engine, "_create_production_broker", lambda: None)
        assert engine.switch_environment("production", user_name="Luke Arwolf") is False
        assert engine.settings.SIMULATION_MODE is True

        # Com broker real conectado (mockado), deve alternar para produção e voltar para homologação
        mock_prod_broker = MagicMock()
        mock_prod_broker.is_connected = True
        mock_prod_broker.connect.return_value = True
        mock_prod_broker.get_balance.return_value = 500.0
        mock_prod_broker.get_positions.return_value = []
        engine.prod_broker = mock_prod_broker

        assert engine.switch_environment("production", user_name="Luke Arwolf") is True
        assert engine.settings.SIMULATION_MODE is False
        assert engine.broker is mock_prod_broker

        ok = engine.switch_environment("homologation", user_name="Luke Arwolf")
        assert ok is True
        assert engine.settings.SIMULATION_MODE is True
        assert engine.telegram.send_alert.called
        last_msg = engine.telegram.send_alert.call_args[0][0]
        assert "PRODUÇÃO ➔ HOMOLOGAÇÃO" in last_msg
        assert "Luke Arwolf" in last_msg
    finally:
        engine.stop()
