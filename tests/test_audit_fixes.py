import pytest
from core.risk_manager import RiskManager
from broker.mt5_broker import MT5Broker
from broker.simulator import SimulatorBroker
from data.database import DatabaseManager

class TestAuditFixes:
    def test_small_capital_buying_and_validation(self):
        rm = RiskManager(stop_loss_percent=0.08)
        shares = rm.get_max_shares(
            price=13.0,
            available_capital=20.0,
            confidence=0.7,
            total_portfolio_value=20.0
        )
        assert shares == 1

        valid, reason = rm.validate_order(
            ticker="BBDC4",
            quantity=1,
            price=13.0,
            available_capital=20.0,
            total_portfolio_value=20.0
        )
        assert valid is True
        assert reason == "Ordem válida"

    def test_b3_fractional_ticker_suffix(self):
        assert MT5Broker._ensure_fractional_suffix("PETR4", quantity=10) == "PETR4F"
        assert MT5Broker._ensure_fractional_suffix("PETR4F", quantity=10) == "PETR4F"
        assert MT5Broker._ensure_fractional_suffix("VALE3", quantity=1) == "VALE3F"

        assert MT5Broker._ensure_fractional_suffix("PETR4", quantity=100) == "PETR4"
        assert MT5Broker._ensure_fractional_suffix("PETR4F", quantity=100) == "PETR4"

        assert MT5Broker._ensure_fractional_suffix("BOVA11", quantity=5) == "BOVA11"
        assert MT5Broker._ensure_fractional_suffix("AAPL34", quantity=2) == "AAPL34"

    def test_simulator_rejects_zero_price(self):
        sim = SimulatorBroker(initial_balance=1000.0)
        order = sim.buy("PETR4", quantity=10, price=0.0, stop_loss=0.0)
        assert order.status.value == "REJECTED"
        assert "zero" in order.comment.lower()

    def test_database_accepts_portuguese_actions(self, tmp_path):
        db_path = tmp_path / "test_actions.db"
        db = DatabaseManager(str(db_path))
        
        trade_id1 = db.insert_trade("PETR4", "COMPRA", 10, 30.0, 300.0, is_simulated=True)
        assert trade_id1 > 0
        
        trade_id2 = db.insert_trade("PETR4", "VENDA_EMERGENCIAL", 10, 28.0, 280.0, is_simulated=True)
        assert trade_id2 > 0

        trades = db.get_trade_history(days=1)
        assert len(trades) == 2
        assert trades[0]["action"] in ("BUY", "SELL")
