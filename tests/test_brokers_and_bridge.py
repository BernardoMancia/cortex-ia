"""
Testes abrangentes de Brokers (SimulatorBroker, MT5Broker, RestMT5Broker),
Bridge REST MT5 (autenticação X-Bridge-Api-Key, bloqueio de RCE em /connect,
/modify_sl, /order_send) e microestrutura de lotes/tickers da B3.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests
from fastapi.testclient import TestClient

import bridge.app as bridge_app_mod
import broker.mt5_broker as mt5_broker_mod
from broker.base import OrderStatus
from broker.mt5_broker import MT5Broker
from broker.rest_mt5_broker import RestMT5Broker
from broker.simulator import SimulatorBroker
from config.settings import settings
from utils.helpers import (
    format_b3_ticker_for_quantity,
    is_b3_unit_lot_asset,
    normalize_b3_ticker,
    split_b3_order_legs,
)


class TestB3TickerAndLotMicrostructure:
    def test_etfs_bdrs_vs_equity_units_and_stocks(self):
        # ETFs e BDRs operam em lote unitário de 1 ação SEM sufixo 'F'
        assert is_b3_unit_lot_asset("BOVA11") is True
        assert is_b3_unit_lot_asset("IVVB11") is True
        assert is_b3_unit_lot_asset("AAPL34") is True
        assert is_b3_unit_lot_asset("JBSS32") is True
        assert format_b3_ticker_for_quantity("BOVA11", 5) == "BOVA11"
        assert format_b3_ticker_for_quantity("AAPL34", 1) == "AAPL34"
        assert split_b3_order_legs("BOVA11", 250) == [("BOVA11", 250)]

        # Units corporativas (TAEE11, BPAC11, SANB11, KLBN11) e ações ON/PN usam lote padrão 100 e fracionário 'F'
        assert is_b3_unit_lot_asset("TAEE11") is False
        assert is_b3_unit_lot_asset("BPAC11") is False
        assert format_b3_ticker_for_quantity("TAEE11", 15) == "TAEE11F"
        assert format_b3_ticker_for_quantity("TAEE11", 100) == "TAEE11"
        assert normalize_b3_ticker("TAEE11F") == "TAEE11"
        assert normalize_b3_ticker("FOOD11") == "FOOD11"  # ticker terminado em 'F' real preservado se aplicável
        assert normalize_b3_ticker("PETR4F") == "PETR4"

        # Divisão automática em lote padrão + fracionário para quantidades mistas > 100
        assert split_b3_order_legs("PETR4", 255) == [("PETR4", 200), ("PETR4F", 55)]
        assert split_b3_order_legs("TAEE11", 130) == [("TAEE11", 100), ("TAEE11F", 30)]
        assert split_b3_order_legs("VALE3", 200) == [("VALE3", 200)]
        assert split_b3_order_legs("VALE3", 45) == [("VALE3F", 45)]


class TestBridgeApiSecurityAndOperations:
    def test_bridge_api_key_enforcement_and_no_secret_leak_on_health(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_API_KEY", "super-secret-bridge-key-2026")
        fake_mt5 = MagicMock()
        fake_mt5.initialize.return_value = True
        fake_mt5.account_info.return_value = SimpleNamespace(
            login=1019999999,
            server="ClearInvestimentos-PRD",
            balance=1500.0,
            equity=1520.0,
            currency="BRL",
        )
        monkeypatch.setattr(bridge_app_mod, "mt5", fake_mt5)

        client = TestClient(bridge_app_mod.app)

        # Sem chave -> 403
        res_no_key = client.get("/health")
        assert res_no_key.status_code == 403

        # Chave incorreta -> 403
        res_bad_key = client.get("/health", headers={"X-Bridge-Api-Key": "wrong-key"})
        assert res_bad_key.status_code == 403

        # Chave válida -> 200 e sem vazar login/server/balance/equity no /health
        res_ok = client.get("/health", headers={"X-Bridge-Api-Key": "super-secret-bridge-key-2026"})
        assert res_ok.status_code == 200
        payload = res_ok.json()
        assert payload["connected"] is True
        assert "login" not in payload
        assert "balance" not in payload
        assert "server" not in payload

    def test_bridge_connect_ignores_remote_path_and_handles_modify_sl(self, monkeypatch):
        monkeypatch.setenv("BRIDGE_API_KEY", "bridge-key-ok")
        monkeypatch.setenv("MT5_PATH", "")
        fake_mt5 = MagicMock()
        fake_mt5.TRADE_ACTION_SLTP = 6
        fake_mt5.initialize.return_value = True
        fake_mt5.account_info.return_value = SimpleNamespace(
            server="ClearInvestimentos-PRD",
            balance=500.0,
            equity=500.0,
            currency="BRL",
        )
        fake_mt5.symbol_select.return_value = True
        fake_mt5.symbol_info.return_value = SimpleNamespace(digits=2)
        fake_mt5.positions_get.return_value = [SimpleNamespace(tp=0.0)]
        fake_mt5.order_send.return_value = SimpleNamespace(
            retcode=10009,
            order=777111,
            deal=888222,
            volume=10.0,
            price=30.0,
            comment="Request executed",
        )
        monkeypatch.setattr(bridge_app_mod, "mt5", fake_mt5)

        headers = {"X-Bridge-Api-Key": "bridge-key-ok"}
        client = TestClient(bridge_app_mod.app)

        # Tentativa maliciosa de injetar binário arbitrário via /connect deve ser ignorada
        res_conn = client.post(
            "/connect",
            json={"path": r"C:\Windows\System32\calc.exe", "password": "hack"},
            headers=headers,
        )
        assert res_conn.status_code == 200
        # Garante que mt5.initialize nunca recebeu o path malicioso do body HTTP
        for call in fake_mt5.initialize.call_args_list:
            assert call.kwargs.get("path") != r"C:\Windows\System32\calc.exe"

        # /modify_sl envia TRADE_ACTION_SLTP (6) arredondado a 2 casas decimais
        res_sl = client.post(
            "/modify_sl",
            json={"position": 9901, "symbol": "PETR4F", "sl": 28.4567, "magic": 234000},
            headers=headers,
        )
        assert res_sl.status_code == 200
        data_sl = res_sl.json()
        assert data_sl["retcode"] == 10009
        assert data_sl["sl"] == 28.46
        sent_dict = fake_mt5.order_send.call_args[0][0]
        assert sent_dict["action"] == 6
        assert sent_dict["position"] == 9901
        assert sent_dict["sl"] == 28.46


class TestMT5BrokerNativeFixes:
    def _build_mock_mt5(self) -> MagicMock:
        m = MagicMock()
        m.ORDER_FILLING_FOK = 0
        m.ORDER_FILLING_IOC = 1
        m.ORDER_FILLING_RETURN = 2
        m.ORDER_TYPE_BUY = 0
        m.ORDER_TYPE_SELL = 1
        m.TRADE_ACTION_DEAL = 1
        m.TRADE_ACTION_SLTP = 6
        m.ORDER_TIME_DAY = 0
        m.TRADE_RETCODE_DONE = 10009
        m.TRADE_RETCODE_DONE_PARTIAL = 10010
        m.TRADE_RETCODE_PLACED = 10008
        m.TRADE_RETCODE_NO_CHANGES = 10025
        # Nota crítica: MetaTrader5 Python real NÃO possui SYMBOL_FILLING_FOK!
        del m.SYMBOL_FILLING_FOK
        m.account_info.return_value = SimpleNamespace(balance=50000.0, equity=50000.0)
        m.symbol_select.return_value = True
        return m

    def test_detect_filling_mode_without_symbol_filling_fok_attribute(self, monkeypatch):
        mock_mt5 = self._build_mock_mt5()
        monkeypatch.setattr(mt5_broker_mod, "mt5", mock_mt5)
        monkeypatch.setattr(mt5_broker_mod, "_mt5_available", True)

        broker = MT5Broker()
        broker._connected = True

        mock_mt5.symbol_info.return_value = SimpleNamespace(filling_mode=0, visible=True, digits=2)
        assert broker._detect_filling_mode("PETR4F") == 2  # ORDER_FILLING_RETURN

        mock_mt5.symbol_info.return_value = SimpleNamespace(filling_mode=2, visible=True, digits=2)
        assert broker._detect_filling_mode("PETR4F") == 1  # ORDER_FILLING_IOC

        mock_mt5.symbol_info.return_value = SimpleNamespace(filling_mode=1, visible=True, digits=2)
        assert broker._detect_filling_mode("PETR4F") == 0  # ORDER_FILLING_FOK

    def test_multileg_buy_partial_fill_and_position_methods_no_nameerror(self, monkeypatch):
        mock_mt5 = self._build_mock_mt5()
        mock_mt5.symbol_info.return_value = SimpleNamespace(filling_mode=0, visible=True, digits=2)
        mock_mt5.symbol_info_tick.return_value = SimpleNamespace(ask=20.0, bid=19.95, last=20.0)
        mock_mt5.order_check.return_value = SimpleNamespace(retcode=0, comment="Done")
        mock_mt5.order_send.side_effect = [
            SimpleNamespace(retcode=10009, volume=200.0, price=20.0, order=101, comment="Leg 1"),
            SimpleNamespace(retcode=10010, volume=40.0, price=20.10, order=102, comment="Leg 2 Partial"),
        ]

        monkeypatch.setattr(mt5_broker_mod, "mt5", mock_mt5)
        monkeypatch.setattr(mt5_broker_mod, "_mt5_available", True)

        broker = MT5Broker()
        broker._connected = True

        # Compra de 255 ações de PETR4 -> divide em 200 PETR4 + 55 PETR4F (parcial 40 executadas = 240 total)
        order = broker.buy("PETR4", quantity=255, price=20.004, stop_loss=18.126)
        assert order.status == OrderStatus.FILLED
        assert order.quantity == 240
        assert order.stop_loss == 18.13

        # get_position, modify_stop_loss e emergency_sell não disparam NameError: quantity
        mock_mt5.positions_get.return_value = [
            SimpleNamespace(
                ticket=5001,
                symbol="TAEE11F",
                volume=15.0,
                price_open=35.0,
                price_current=36.5,
                sl=32.0,
                tp=0.0,
                magic=broker.MAGIC_NUMBER,
                time=1760000000,
            )
        ]
        pos = broker.get_position("TAEE11")
        assert pos is not None
        assert pos.quantity == 15

        mock_mt5.order_send.side_effect = None
        mock_mt5.order_send.return_value = SimpleNamespace(
            retcode=10009, volume=15.0, price=36.4, order=5002, comment="OK"
        )
        assert broker.modify_stop_loss("TAEE11", 34.556) is True
        em_order = broker.emergency_sell("TAEE11")
        assert em_order.status == OrderStatus.FILLED
        assert em_order.quantity == 15


class TestRestMT5BrokerAndSimulator:
    def test_rest_mt5_broker_timeout_reconciliation_and_modify_sl(self, monkeypatch):
        settings.BRIDGE_API_KEY = "rest-bridge-secret"
        broker = RestMT5Broker(base_url="http://127.0.0.1:5000")
        broker._connected = True

        positions_state: list[dict] = []

        def fake_api_call(endpoint: str, data=None, method: str = "POST", raise_on_timeout: bool = False):
            if endpoint == "positions":
                return list(positions_state)
            if endpoint.startswith("symbol_info/"):
                return {"filling_mode": 0, "digits": 2}
            if endpoint == "order_check":
                return {"retcode": 0, "comment": "OK"}
            if endpoint == "order_send":
                # Simula que a ordem entrou no MT5, mas a resposta HTTP sofreu timeout
                positions_state.append(
                    {
                        "ticket": 8801,
                        "symbol": "PETR4F",
                        "volume": 10.0,
                        "price_open": 31.25,
                        "price_current": 31.25,
                        "sl": 28.50,
                        "tp": 0.0,
                        "magic": broker.MAGIC_NUMBER,
                    }
                )
                raise requests.exceptions.Timeout("Read timed out")
            if endpoint == "modify_sl":
                assert data["position"] == 8801
                assert data["sl"] == 29.90
                return {"retcode": 10009}
            return None

        monkeypatch.setattr(broker, "_api_call", fake_api_call)
        assert broker._headers()["X-Bridge-Api-Key"] == "rest-bridge-secret"

        buy_order = broker.buy("PETR4", quantity=10, price=31.25, stop_loss=28.50)
        assert buy_order.status == OrderStatus.FILLED
        assert buy_order.quantity == 10
        assert buy_order.price == 31.25

        assert broker.modify_stop_loss("PETR4", 29.90) is True

    def test_simulator_preserves_realized_pnl_and_partial_exit_flag(self, tmp_path):
        state_file = tmp_path / "sim_state.json"
        sim = SimulatorBroker(initial_balance=1000.0, state_path=state_file)
        sim.connect()

        # Compra 10x a R$ 20 (custo R$ 200 -> caixa R$ 800) e realiza lucro vendendo 5x a R$ 26 (+R$ 30)
        assert sim.buy("VALE3", 10, 20.0, stop_loss=18.0).status == OrderStatus.FILLED
        assert sim.sell("VALE3", 5, 26.0).status == OrderStatus.FILLED
        # Caixa agora: 800 + 130 = 930; 5x abertas a custo 100 => patrimônio realizado = 1030 (+R$ 30 de lucro)
        assert sim.get_balance() == pytest.approx(930.0)

        pos = sim.get_position("VALE3")
        assert pos is not None
        assert sim.set_partial_exit_done("VALE3", True) is True

        # Ajustar capital inicial de 1000 -> 1500 sem zerar posições DEVE preservar os +R$ 30 de lucro realizado (caixa = 1430)
        sim.set_initial_capital(1500.0, reset_positions=False)
        assert sim.get_balance() == pytest.approx(1430.0)

        # Recarregar do disco deve preservar partial_exit_done
        sim_reloaded = SimulatorBroker(initial_balance=1500.0, state_path=state_file)
        sim_reloaded.connect()
        reloaded_pos = sim_reloaded.get_position("VALE3")
        assert reloaded_pos is not None
        assert reloaded_pos.partial_exit_done is True
