from unittest.mock import MagicMock

from core.engine import CortexEngine


def test_engine_initialization_homologation():
    engine = CortexEngine(force_simulation=True)
    try:
        assert engine.settings.SIMULATION_MODE is True
        assert engine.broker.__class__.__name__ == "SimulatorBroker"
        assert engine.portfolio is not None
        assert engine.risk_manager is not None
        assert engine.decision_engine is not None
    finally:
        engine.stop()


def test_engine_switch_environment(monkeypatch):
    engine = CortexEngine(force_simulation=True)
    try:
        assert engine.settings.SIMULATION_MODE is True

        # Idempotente para o mesmo modo
        assert engine.switch_environment("homologation", user_name="TestUser") is True
        assert engine.settings.SIMULATION_MODE is True

        # Sem MT5/Bridge conectado, deve recusar transição para produção
        engine.prod_broker = None
        monkeypatch.setattr(engine, "_create_production_broker", lambda: None)
        assert engine.switch_environment("production", user_name="TestUser") is False
        assert engine.settings.SIMULATION_MODE is True

        # Com broker real conectado, deve alternar homologation <-> production com sucesso
        mock_broker = MagicMock()
        mock_broker.is_connected = True
        mock_broker.connect.return_value = True
        mock_broker.get_balance.return_value = 250.0
        mock_broker.get_positions.return_value = []
        engine.prod_broker = mock_broker

        assert engine.switch_environment("production", user_name="TestUser") is True
        assert engine.settings.SIMULATION_MODE is False
        assert engine.broker is mock_broker
        assert engine.portfolio is engine.prod_portfolio

        assert engine.switch_environment("homologation", user_name="TestUser") is True
        assert engine.settings.SIMULATION_MODE is True
        assert engine.broker is engine.sim_broker
        assert engine.portfolio is engine.sim_portfolio
    finally:
        engine.stop()


def test_engine_capital_summary_methods():
    engine = CortexEngine(force_simulation=True)
    try:
        sim_summary = engine.get_simulation_summary()
        assert sim_summary is not None
        assert "total_equity" in sim_summary
        assert "free_cash" in sim_summary
        assert "buying_power" in sim_summary

        prod_summary = engine.get_production_summary()
        assert prod_summary is not None
        assert "total_equity" in prod_summary
        assert "ai_allocated_capital" in prod_summary
        assert prod_summary["ai_allocated_capital"] == 0.0
    finally:
        engine.stop()
