"""
Testes de Segurança Web (Auth, Rate-Limiting em /first-login, Anti-Spoofing IP,
CSRF / Origin Check, Headers CSP/XFO), Rotas de Risco (precisão de 0.5%) e
Isolamento Estrito de Contabilidade / Liquidação Forçada entre Homologação e Produção.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import auth as auth_mod
from broker.base import Order, OrderStatus, OrderType
from core.engine import CortexEngine
from dashboard.app import app
from data.database import DatabaseManager
from models.data_models import Position
from portfolio import Portfolio


def _login_fresh_user(client: TestClient, username: str = "operador_seguro") -> str:
    res = client.post(
        "/api/auth/first-login",
        json={
            "current_username": "Admin",
            "current_password": "Admin",
            "new_username": username,
            "new_password": "SenhaForte#2026!",
        },
    )
    assert res.status_code == 200, res.text
    return res.json().get("csrf_token", "")


class TestSecurityHardening:
    def test_first_login_password_policy_and_rate_limiting(self):
        DatabaseManager()
        client = TestClient(app)

        # Rejeita senha menor que 8 caracteres
        res_short = client.post(
            "/api/auth/first-login",
            json={
                "current_username": "Admin",
                "current_password": "Admin",
                "new_username": "trader_ok",
                "new_password": "1234567",
            },
        )
        assert res_short.status_code == 400

        # Rejeita manter usuário 'admin'
        res_adm = client.post(
            "/api/auth/first-login",
            json={
                "current_username": "Admin",
                "current_password": "Admin",
                "new_username": "admin",
                "new_password": "SenhaForte#2026!",
            },
        )
        assert res_adm.status_code == 400

        # Brute-force em /api/auth/first-login deve bloquear com HTTP 429 após max tentativas
        locked_seen = False
        for _ in range(8):
            r = client.post(
                "/api/auth/first-login",
                json={
                    "current_username": "Admin",
                    "current_password": "wrong-password",
                    "new_username": "trader_ok",
                    "new_password": "SenhaForte#2026!",
                },
            )
            if r.status_code == 429:
                locked_seen = True
                break
            assert r.status_code == 401
        assert locked_seen is True

    def test_ip_spoofing_protection_and_trusted_proxy(self):
        # Cliente externo tentando forjar X-Forwarded-For deve ser ignorado
        spoofed_req = SimpleNamespace(
            client=SimpleNamespace(host="203.0.113.55"),
            headers={"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"},
        )
        assert auth_mod.get_client_ip(spoofed_req) == "203.0.113.55"

        # Proxy reverso local confiável (Nginx em 127.0.0.1) usa X-Real-IP / último hop
        proxy_req = SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"X-Real-IP": "198.51.100.42", "X-Forwarded-For": "10.0.0.1, 198.51.100.42"},
        )
        assert auth_mod.get_client_ip(proxy_req) == "198.51.100.42"

    def test_security_headers_and_csrf_cross_origin_rejection(self):
        DatabaseManager()
        client = TestClient(app)
        _login_fresh_user(client, username=f"sec_user_{int(time.time())}")

        res_me = client.get("/api/auth/me")
        assert res_me.status_code == 200
        assert res_me.headers.get("X-Frame-Options") == "DENY"
        assert res_me.headers.get("X-Content-Type-Options") == "nosniff"
        assert "Content-Security-Policy" in res_me.headers
        assert "no-store" in res_me.headers.get("Cache-Control", "")

        # Tentativa de POST mutável vindo de Origin malicioso deve ser bloqueada com 403
        res_csrf = client.post(
            "/api/config/risk",
            json={
                "stop_loss_percent": 8.0,
                "trailing_trigger_percent": 3.0,
                "trailing_distance_percent": 2.0,
            },
            headers={"Origin": "https://evil-attacker.example"},
        )
        assert res_csrf.status_code == 403


class TestRiskSliderPrecisionAndEnvironmentIsolation:
    def test_risk_slider_half_percent_precision_and_validation(self):
        DatabaseManager()
        client = TestClient(app)
        csrf = _login_fresh_user(client, username=f"risk_user_{int(time.time())}")
        headers = {"X-CSRF-Token": csrf} if csrf else {}

        # Slider em 0.5% de distância e 1.0% de gatilho NÃO pode virar 50%!
        res_ok = client.post(
            "/api/config/risk",
            json={
                "stop_loss_percent": 5.0,
                "trailing_trigger_percent": 1.0,
                "trailing_distance_percent": 0.5,
            },
            headers=headers,
        )
        assert res_ok.status_code == 200, res_ok.text
        body = res_ok.json()
        assert body["trailing_trigger_percent"] == 1.0
        assert body["trailing_distance_percent"] == 0.5

        # Distância >= Gatilho deve ser rejeitada com HTTP 400
        res_bad = client.post(
            "/api/config/risk",
            json={
                "stop_loss_percent": 5.0,
                "trailing_trigger_percent": 2.0,
                "trailing_distance_percent": 2.5,
            },
            headers=headers,
        )
        assert res_bad.status_code == 400

    def test_simulator_reset_endpoint_preserves_production_records(self):
        db = DatabaseManager()
        client = TestClient(app)
        csrf = _login_fresh_user(client, username=f"rst_user_{int(time.time())}")
        headers = {"X-CSRF-Token": csrf} if csrf else {}

        db.insert_trade("PETR4", "BUY", 10, 30.0, 300.0, is_simulated=True)
        db.insert_trade("WEGE3", "BUY", 5, 40.0, 200.0, is_simulated=False)
        gid = db.create_withdrawal_goal(
            amount=30.0, mode="on_profit", target_equity=230.0, base_equity=200.0
        )

        res_reset = client.post("/api/config/simulator/reset", headers=headers)
        assert res_reset.status_code == 200

        sim_trades = db.get_trade_history(days=5, is_simulated=True)
        prod_trades = db.get_trade_history(days=5, is_simulated=False)
        assert len(sim_trades) == 0
        assert len(prod_trades) == 1
        assert prod_trades[0]["ticker"] == "WEGE3"
        assert db.get_active_withdrawal_goal()["id"] == gid


class TestPortfolioAndEngineCapitalSafety:
    def test_buying_power_zero_when_ai_ceiling_zero_and_vault_excluded_from_pnl(self):
        # Conta de Produção recém-conectada: R$ 200 de saldo na corretora, mas Teto da IA = R$ 0
        port = Portfolio(initial_capital=0.0)
        port.free_cash = 200.0
        port.ai_allocated_capital = 0.0
        port.vault_reserve_cash = 200.0

        assert port.buying_power == 0.0
        assert port.total_pnl == 0.0

        # Libera R$ 50 para a IA operar (R$ 150 permanecem protegidos no Cofre)
        port.release_from_vault(50.0)
        assert port.ai_allocated_capital == 50.0
        assert port.vault_reserve_cash == 150.0
        assert port.buying_power == 50.0
        assert port.total_pnl == 0.0

        # Compra 2x a R$ 20 (R$ 40) e ação valoriza para R$ 25 (+R$ 10 de lucro operacional = +20%)
        port.add_position(
            Position(ticker="VALE3", quantity=2, entry_price=20.0, current_price=25.0, stop_loss=18.0)
        )
        assert port.total_pnl == 10.0
        assert round(port.total_pnl_percent, 2) == 20.0

    def test_ceiling_reduction_liquidates_excess_positions_and_blocks_sim_withdrawal(self):
        engine = CortexEngine(force_simulation=True)
        try:
            # Em Homologação, execute_smart_withdrawal deve ser bloqueado
            res_sim_w = engine.execute_smart_withdrawal(50.0, mode="instant")
            assert res_sim_w["success"] is False

            # Configura carteira de Produção com 4x PETR4 @ R$ 25 (R$ 100 investidos) + R$ 0 livre
            mock_prod_broker = MagicMock()
            mock_prod_broker.is_connected = True
            mock_prod_broker.connect.return_value = True
            mock_prod_broker.get_balance.return_value = 50.0  # saldo após vender 2 ações
            mock_prod_broker.sell.return_value = Order(
                ticker="PETR4F",
                order_type=OrderType.SELL,
                quantity=2,
                price=25.0,
                status=OrderStatus.FILLED,
            )
            engine.prod_broker = mock_prod_broker
            engine.prod_portfolio = Portfolio(initial_capital=100.0)
            engine.prod_portfolio.free_cash = 100.0
            engine.prod_portfolio.ai_allocated_capital = 100.0
            engine.prod_portfolio.vault_reserve_cash = 0.0
            engine.prod_portfolio.add_position(
                Position(ticker="PETR4", quantity=4, entry_price=25.0, current_price=25.0, stop_loss=22.5)
            )

            # Reduzir o teto da IA de R$ 100 para R$ 50 DEVE liquidar automaticamente 2 ações (R$ 50)
            res = engine.adjust_production_ceiling(50.0, "TraderAdmin")
            assert res["success"] is True
            assert mock_prod_broker.sell.called
            assert len(res["liquidated_positions"]) == 1
            remaining_pos = engine.prod_portfolio.get_position("PETR4")
            assert remaining_pos is not None
            assert remaining_pos.quantity == 2
        finally:
            engine.stop()
