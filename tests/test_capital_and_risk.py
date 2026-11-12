from __future__ import annotations

import time
import random
import pytest
from fastapi.testclient import TestClient

from config.settings import settings
from data.database import DatabaseManager
from models.data_models import Position
from portfolio import Portfolio
from core.risk_manager import RiskManager
from dashboard.app import app

class TestCapitalAndCompounding:
    def test_auto_compounding_on_profitable_trade(self):
        port = Portfolio(initial_capital=100.0)
        assert port.ai_allocated_capital == 100.0
        assert port.buying_power == 100.0

        pos = Position(
            ticker="PETR4",
            quantity=5,
            entry_price=20.0,
            current_price=20.0,
            stop_loss=18.0,
        )
        port.add_position(pos)
        assert port.free_cash == 0.0
        assert port.allocated_capital == 100.0
        assert port.buying_power == 0.0

        port.remove_position("PETR4", sell_price=21.0)
        assert port.free_cash == 105.0
        assert port.cumulative_realized_pnl == 5.0
        assert port.ai_allocated_capital == 105.0
        assert port.buying_power == 105.0

    def test_vault_reserve_protection(self):
        port = Portfolio(initial_capital=200.0)
        assert port.buying_power == 200.0

        port.deposit_to_vault(50.0)
        assert port.vault_reserve_cash == 50.0
        assert port.ai_allocated_capital == 150.0
        assert port.buying_power == 150.0

        port.release_from_vault(20.0)
        assert port.vault_reserve_cash == 30.0
        assert port.ai_allocated_capital == 170.0
        assert port.buying_power == 170.0

class TestRiskManagerLiquidation:
    def test_smart_liquidation_ranking_and_exact_fractional_qty(self):
        rm = RiskManager()
        pos1 = Position(ticker="VALE3", quantity=10, entry_price=50.0, current_price=60.0, stop_loss=45.0)
        pos2 = Position(ticker="PETR4", quantity=10, entry_price=30.0, current_price=30.0, stop_loss=27.0)
        pos3 = Position(ticker="ITUB4", quantity=10, entry_price=40.0, current_price=36.0, stop_loss=36.0)

        positions = [pos2, pos3, pos1]
        plan = rm.select_positions_for_liquidation(positions, target_amount=120.0)
        assert len(plan) > 0
        assert plan[0]["ticker"] == "VALE3"
        assert plan[0]["quantity"] == 2
        assert plan[0]["estimated_value"] == 120.0

    def test_update_risk_parameters(self):
        rm = RiskManager(stop_loss_percent=0.10)
        rm.update_parameters(
            stop_loss_percent=0.08,
            trailing_trigger_percent=0.03,
            trailing_distance_percent=0.02,
        )
        assert rm.stop_loss_percent == 0.08
        assert rm.trailing_stop_activation == 0.03
        assert rm.trailing_stop_distance == 0.02

class TestDatabaseCapitalManagement:
    def test_system_config_and_withdrawal_goals(self):
        db = DatabaseManager()
        db.set_config("test_key", "12345")
        assert db.get_config("test_key") == "12345"

        gid = db.create_withdrawal_goal(amount=50.0, mode="on_profit", target_equity=150.0, base_equity=100.0, notes="meta")
        active = db.get_active_withdrawal_goal()
        assert active is not None
        assert active["id"] == gid
        assert active["amount"] == 50.0

        db.complete_withdrawal_goal(gid, notes="Concluída")
        assert db.get_active_withdrawal_goal() is None

        db.record_capital_transaction("APORTE", 100.0, 100.0, 0.0, "Aporte teste")
        txs = db.get_capital_transactions()
        assert len(txs) > 0
        assert txs[0]["tx_type"] == "APORTE"

class TestCapitalApiEndpoints:
    def test_capital_summary_and_risk_endpoints(self):
        db = DatabaseManager()
        if not db.get_user_by_username("Admin"):
            db.create_user("Admin", "Admin", must_change_password=True)
        else:
            db.reset_user_password("Admin", "Admin", force_first_login=True)

        client = TestClient(app)
        unique_user = f"capital_user_{int(time.time())}"
        res_first = client.post("/api/auth/first-login", json={
            "current_username": "Admin",
            "current_password": "Admin",
            "new_username": unique_user,
            "new_password": "MinhaSenhaForte@2026"
        })
        assert res_first.status_code == 200

        client.post("/api/config/simulator/capital", json={"capital": 1000000.0, "reset_positions": True})
        db.set_config("prod_broker_account_balance", "50.00")
        client.post("/api/capital/adjust-allocation", json={"ai_capital": 20.0})

        res = client.get("/api/capital/summary")
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert "simulation" in data
        assert "production" in data

        assert data["simulation"]["total_equity"] >= 1000000.0 or data["simulation"]["free_cash"] >= 1000000.0
        assert data["production"]["ai_allocated_capital"] == 20.0

        risk_res = client.post("/api/config/risk", json={
            "stop_loss_percent": 8.0,
            "trailing_trigger_percent": 3.0,
            "trailing_distance_percent": 2.0
        })
        assert risk_res.status_code == 200
        assert risk_res.json()["stop_loss_percent"] == 8.0

        goal_res = client.post("/api/capital/withdrawal-goal", json={
            "amount": 50.0,
            "mode": "on_profit"
        })
        assert goal_res.status_code == 200
        assert goal_res.json()["success"] is True

        tx_res = client.get("/api/capital/transactions")
        assert tx_res.status_code == 200
        assert "transactions" in tx_res.json()

    def test_ai_capital_zero_initially_and_max_broker_balance_enforced(self):
        db = DatabaseManager()
        if not db.get_user_by_username("Admin"):
            db.create_user("Admin", "Admin", must_change_password=True)
        else:
            db.reset_user_password("Admin", "Admin", force_first_login=True)

        client = TestClient(app)
        unique_user = f"cap_test_{int(time.time())}_{random.randint(100, 999)}"
        res_first = client.post("/api/auth/first-login", json={
            "current_username": "Admin",
            "current_password": "Admin",
            "new_username": unique_user,
            "new_password": "MinhaSenhaForte@2026"
        })
        assert res_first.status_code == 200

        # Simula estado inicial com IA zerada e saldo na Clear de R$ 20.00
        db.set_config("prod_broker_account_balance", "20.00")
        db.set_config("prod_ai_allocated_capital", "0.0")
        db.set_config("prod_vault_reserve_cash", "20.00")

        res = client.get("/api/capital/summary")
        assert res.status_code == 200
        prod = res.json()["production"]
        assert prod["ai_allocated_capital"] == 0.0
        assert prod["vault_reserve_cash"] == 20.0
        assert prod["total_equity"] == 20.0

        # Tentativa de definir valor maior que o saldo da corretora
        res_invalid = client.post("/api/capital/adjust-allocation", json={"ai_capital": 50.0})
        assert res_invalid.status_code == 200
        data_invalid = res_invalid.json()
        assert data_invalid["success"] is False
        assert "não pode ser maior" in data_invalid["message"]

        # Definição válida de teto
        res_valid = client.post("/api/capital/adjust-allocation", json={"ai_capital": 15.0})
        assert res_valid.status_code == 200
        assert res_valid.json()["success"] is True

        res_check = client.get("/api/capital/summary")
        prod_check = res_check.json()["production"]
        assert prod_check["ai_allocated_capital"] == 15.0
        assert prod_check["vault_reserve_cash"] == 5.0
