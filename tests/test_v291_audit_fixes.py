"""
Suíte de regressão da versão v2.9.1 — Pós-Auditoria Forense de Produção e VPS.

Cobre:
1. [VPS-01] Reuso de ThreadPoolExecutor e fechamento de sessões HTTP em NewsScraper e MarketData (fim do OSError 24 Too many open files).
2. [VPS-02] Checagem explícita `is None` para DataFrames pré-carregados em CortexEngine._run_trading_cycle (fim do ValueError DataFrame is ambiguous).
3. [VPS-03] Proteção do SQLiteLogHandler contra `close()` externo (Uvicorn dictConfig), `log_config=None` no DashboardServer e propagação de loggers filhos `cortex.*`.
4. [REAL-01] ConnectionError explícito em falha transitória da Bridge preservando `ai_allocated_capital`, custódia e Circuit Breaker em Produção.
5. [REAL-02] Reconciliação pós-timeout em RestMT5Broker recusando fill fantasma de venda quando `/positions` falha (`None`).
6. [REAL-03] Consolidação multi-pernas (`PETR4` + `PETR4F`) em `_sync_portfolio_with_broker`, proteção contra short em lote padrão (`100` ações) e `calculate_partial_tp_quantity`.
7. [REAL-04 & ALT-01] Preservação de data civil `D1` em `MarketData._get_ohlcv_bridge` (detecção exata de crash de `BOVA11`) e fast-fail HTTP `503` em `waiting_broker_auth` na Bridge.
8. [REAL-05 & ALT-08] Meta de saque `on_profit` baseada em `operational_value` (excluindo Cofre Reserva) + compensação multilateral `D+2` (`BUY` abate `SELL` do mesmo dia).
9. [RSK-02, ENG-06 & Backtester] Piso de 1 centavo em `update_trailing_stop` p/ penny stocks, retenção Anti-Day-Trade em perda discricionária + bloqueio de recompra intraday, e TP parcial no Backtester.
10. [SEC & TG] Login sem inline script + CSP sem `'unsafe-inline'` em `script-src`, bloqueio de `channel_post` no Telegram e `/pensamentos` com `parse_mode='HTML'`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import sqlite3
import time
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import pytest
import requests

from analysis.decision import DecisionEngine
from analysis.sentiment import SentimentAnalyzer, SentimentResult
from analysis.technical import TechnicalAnalyzer, TechnicalResult, TrendSignal
from backtest.backtester import _portfolio_reduce_position
import bridge.app as bridge_app
from bridge.mql5_rpc import MQL5FileRPCBackend
from broker.base import OrderStatus
from broker.rest_mt5_broker import RestMT5Broker
from config.settings import settings
from core.engine import CortexEngine
from core.risk_manager import RiskManager
from core.scheduler import MarketScheduler
from dashboard.app import DashboardServer, app as dashboard_app
from data.database import DatabaseManager
from data.market_data import MarketData
from data.news_scraper import NewsScraper
from models.data_models import Action, Decision, Position
from notifications.telegram import TelegramNotifier
from portfolio import Portfolio
from utils.helpers import BRT
from utils.log_db_handler import SQLiteLogHandler
from utils.logger import setup_logger


def _sample_df(rows: int = 75, last_close: float = 28.0) -> pd.DataFrame:
    idx = pd.date_range(end=datetime.now(BRT), periods=rows, freq="B", tz=BRT)
    closes = np.linspace(last_close * 0.92, last_close, rows)
    return pd.DataFrame(
        {
            "Open": closes * 0.996,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": np.full(rows, 1_200_000, dtype=np.int64),
        },
        index=idx,
    )


def test_vps01_news_scraper_and_market_data_reuse_executors_without_fd_leak(monkeypatch):
    scraper = NewsScraper()
    monkeypatch.setattr(scraper, "_fetch_google_news", lambda *a, **kw: [])
    monkeypatch.setattr(scraper, "_fetch_infomoney", lambda: [])
    monkeypatch.setattr(scraper, "_fetch_valor_investe", lambda: [])
    monkeypatch.setattr(scraper, "_fetch_money_times", lambda: [])

    exec_first = scraper._executor
    for _ in range(6):
        scraper.fetch_all_news()

    assert scraper._executor is exec_first
    assert len(scraper._sessions) <= 4
    scraper.close()
    assert len(scraper._sessions) == 0

    md = MarketData()
    monkeypatch.setattr(
        md,
        "get_current_price",
        lambda t: {"last": 25.0, "bid": 24.99, "ask": 25.01, "ticker": t, "is_stale": False},
    )
    monkeypatch.setattr(md, "get_ohlcv", lambda t, period="3mo", interval="1d": _sample_df(60, 25.0))

    yf_exec_first = md._executor
    for _ in range(5):
        prices = md.get_prices_batch(["PETR4", "VALE3", "WEGE3"])
        bars = md.get_ohlcv_batch(["PETR4", "VALE3"])
        assert prices["PETR4"]["last"] == pytest.approx(25.0)
        assert len(bars["PETR4"]) == 60

    assert md._executor is yf_exec_first
    md.close()


def test_vps02_engine_cycle_with_preloaded_dataframe_never_raises_ambiguous_truth(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "vps02.db"))
    engine = CortexEngine(force_simulation=True)
    try:
        engine.settings.WATCHLIST = ["PETR4"]
        engine.news_scraper = MagicMock()
        engine.news_scraper.fetch_all_news.return_value = []
        engine.telegram = MagicMock()

        df_petr = _sample_df(75, 36.50)
        engine.market_data = MagicMock()
        engine.market_data.update_prices.return_value = {"PETR4": 36.50}
        engine.market_data.get_variation.return_value = None
        engine.market_data.get_prices_batch.return_value = {"PETR4": {"last": 36.50}}
        engine.market_data.get_ohlcv_batch.return_value = {"PETR4": df_petr}
        engine.market_data.get_market_regime.return_value = {
            "benchmark": "BOVA11",
            "change_pct": 0.2,
            "is_bearish_crash": False,
        }

        eval_spy = MagicMock(
            return_value=Decision(
                ticker="PETR4",
                action=Action.HOLD,
                confidence=0.4,
                reasoning="Hold",
                technical_score=0.4,
                sentiment_score=0.0,
                quantity=0,
                price=36.50,
                stop_loss=0.0,
            )
        )
        engine.decision_engine = MagicMock()
        engine.decision_engine.evaluate = eval_spy

        # Antes da v2.9.1, `ohlcv_batch.get(norm_t) or ohlcv_batch.get(ticker)` lançava ValueError aqui!
        engine._run_trading_cycle()

        assert eval_spy.call_count == 1
        passed_df = eval_spy.call_args.kwargs.get("ohlcv_df")
        assert isinstance(passed_df, pd.DataFrame)
        assert len(passed_df) == 75
    finally:
        engine.stop()


def test_vps03_sqlite_log_handler_survives_external_close_and_captures_cortex_children(tmp_path, monkeypatch):
    log_db = tmp_path / "central_logs.db"
    handler = SQLiteLogHandler(db_path=str(log_db))
    parent_logger = logging.getLogger("cortex_v291_parent")
    parent_logger.setLevel(logging.INFO)
    parent_logger.propagate = False
    parent_logger.addHandler(handler)

    try:
        parent_logger.info("Antes do Uvicorn dictConfig")
        # Simula `logging._clearExistingHandlers()` do Uvicorn chamando `handler.close()`
        handler.close()
        assert handler._closed is False

        child_logger = logging.getLogger("cortex_v291_parent.market_data")
        child_logger.warning("Depois do Uvicorn dictConfig com segredo BRIDGE_API_KEY=segredo123")
        handler.flush()

        with sqlite3.connect(log_db) as conn:
            rows = conn.execute("SELECT source, message FROM logs ORDER BY id").fetchall()
        assert len(rows) >= 2
        assert any(r[0] == "cortex_v291_parent.market_data" and "segredo123" not in r[1] for r in rows)
    finally:
        parent_logger.removeHandler(handler)
        handler.shutdown()

    # Garante também que setup_logger registra o logger pai "cortex"
    setup_logger("engine", log_dir=tmp_path / "file_logs")
    root_cortex = logging.getLogger("cortex")
    assert len(root_cortex.handlers) > 0

    # Verifica que DashboardServer passa log_config=None ao uvicorn.Config
    captured_cfg: dict = {}

    class DummyConfig:
        def __init__(self, *args, **kwargs):
            captured_cfg.update(kwargs)

    class DummyServer:
        def __init__(self, cfg):
            self.cfg = cfg
            self.should_exit = False

        def run(self):
            return

    monkeypatch.setattr("dashboard.app.uvicorn.Config", DummyConfig)
    monkeypatch.setattr("dashboard.app._ThreadedServer", DummyServer)
    srv = DashboardServer(host="127.0.0.1", port=18003)
    srv.start()
    srv.stop()
    assert captured_cfg.get("log_config") is None


def test_real01_transient_bridge_failure_raises_connection_error_and_preserves_production_state(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "real01.db"))
    broker = RestMT5Broker(base_url="http://127.0.0.1:5000")
    broker._connected = True
    monkeypatch.setattr(broker, "_api_call", lambda *a, **kw: None)

    with pytest.raises(ConnectionError):
        broker.get_balance()
    with pytest.raises(ConnectionError):
        broker.get_positions()

    engine = CortexEngine(force_simulation=True)
    try:
        engine.settings.SIMULATION_MODE = False
        engine.prod_portfolio = Portfolio(initial_capital=1000.0, is_simulated=False)
        engine.prod_portfolio.adjust_ai_capital(300.0)  # Cofre = 700, Teto IA = 300
        engine.prod_portfolio.add_position(
            Position(ticker="PETR4", quantity=5, entry_price=30.0, current_price=31.0, stop_loss=28.0)
        )
        engine.portfolio = engine.prod_portfolio
        engine.broker = broker
        engine._start_of_day_equity["production"] = engine.prod_portfolio.operational_value

        # Falha transitória de rede na Bridge NÃO pode zerar ai_allocated_capital nem acionar circuit breaker!
        engine._sync_portfolio_with_broker()
        engine._evaluate_circuit_breaker()

        assert engine.prod_portfolio.ai_allocated_capital == pytest.approx(300.0)
        assert engine.prod_portfolio.vault_reserve_cash == pytest.approx(700.0)
        assert engine.prod_portfolio.get_position("PETR4") is not None
        assert engine.risk_manager.is_circuit_breaker_active is False
    finally:
        engine.stop()


def test_real02_timeout_reconciliation_never_reports_phantom_sell_fill_when_positions_fail(monkeypatch):
    broker = RestMT5Broker(base_url="http://127.0.0.1:5000")
    broker._connected = True

    pos_calls = {"n": 0}

    def fake_api(endpoint: str, data=None, method: str = "POST", raise_on_timeout: bool = False):
        if endpoint == "positions":
            pos_calls["n"] += 1
            # Antes do envio (listagem de pernas e before_qty), custodia tem 10 PETR4F; após timeout, Bridge cai (None)
            if pos_calls["n"] <= 2:
                return [
                    {
                        "ticket": 9101,
                        "symbol": "PETR4F",
                        "type": 0,
                        "volume": 10.0,
                        "price_open": 30.0,
                        "price_current": 32.0,
                        "sl": 28.0,
                        "tp": 0.0,
                        "magic": broker.MAGIC_NUMBER,
                    }
                ]
            return None
        if endpoint.startswith("symbol_info/"):
            return {"filling_mode": 2, "digits": 2, "trade_exemode": 2}
        if endpoint.startswith("tick/"):
            return {"bid": 32.00, "ask": 32.04, "last": 32.02}
        if endpoint == "order_check":
            return {"retcode": 0, "comment": "OK"}
        if endpoint == "order_send":
            raise requests.exceptions.Timeout("Read timed out")
        return None

    monkeypatch.setattr(broker, "_api_call", fake_api)
    monkeypatch.setattr("time.sleep", lambda *_: None)

    sell_order = broker.sell("PETR4", quantity=10, price=32.00)
    assert sell_order.status == OrderStatus.REJECTED


def test_real03_multileg_consolidation_and_standard_lot_fractional_short_protection(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "real03.db"))
    engine = CortexEngine(force_simulation=True)
    try:
        t_old = datetime.now(BRT) - timedelta(days=3)
        t_new = datetime.now(BRT) - timedelta(days=1)
        leg_std = Position("PETR4", 200, 35.00, stop_loss=32.00, current_price=37.00, entry_time=t_old, ticket=1001)
        leg_frc = Position("PETR4F", 55, 36.00, stop_loss=33.00, current_price=37.00, entry_time=t_new, ticket=1002)

        engine.broker = MagicMock()
        engine.broker.get_balance.return_value = 1500.0
        engine.broker.get_positions.return_value = [leg_std, leg_frc]
        engine.portfolio = Portfolio(initial_capital=11000.0)

        engine._sync_portfolio_with_broker()

        assert len(engine.portfolio.positions) == 1
        merged = engine.portfolio.get_position("PETR4")
        assert merged is not None
        assert merged.quantity == 255
        expected_avg = round((200 * 35.00 + 55 * 36.00) / 255, 4)
        assert merged.entry_price == pytest.approx(expected_avg, abs=0.01)
        assert merged.entry_time.date() == t_old.date()
    finally:
        engine.stop()

    # Proteção contra venda fracionária (PETR4F) sobre custódia exclusiva em lote padrão (100 PETR4)
    broker = RestMT5Broker(base_url="http://127.0.0.1:5000")
    broker._connected = True
    sent_orders: list[dict] = []

    def fake_api_std_lot(endpoint: str, data=None, method: str = "POST", raise_on_timeout: bool = False):
        if endpoint == "positions":
            return [
                {
                    "ticket": 5501,
                    "symbol": "PETR4",
                    "type": 0,
                    "volume": 100.0,
                    "price_open": 34.0,
                    "price_current": 40.0,
                    "sl": 32.0,
                    "tp": 0.0,
                    "magic": broker.MAGIC_NUMBER,
                }
            ]
        if endpoint == "order_send":
            sent_orders.append(data)
            return {"retcode": 10009, "volume": data["volume"], "price": 40.0, "order": 77}
        return {"filling_mode": 2, "digits": 2, "retcode": 0, "bid": 40.0, "ask": 40.02}

    monkeypatch.setattr(broker, "_api_call", fake_api_std_lot)
    res_half = broker.sell("PETR4", quantity=50, price=40.0)
    assert res_half.status == OrderStatus.REJECTED
    assert not any(o.get("symbol") == "PETR4F" for o in sent_orders)

    rm = RiskManager()
    assert rm.calculate_partial_tp_quantity(Position("PETR4", 100, 30.0), simulation_mode=False) == 0
    assert rm.calculate_partial_tp_quantity(Position("PETR4", 200, 30.0), simulation_mode=False) == 100
    assert rm.calculate_partial_tp_quantity(Position("PETR4", 50, 30.0), simulation_mode=False) == 25


def test_real04_bridge_d1_preserves_civil_date_and_fast_503_when_waiting_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MT5_BRIDGE_URL", "http://127.0.0.1:5000")
    monkeypatch.setattr(settings, "BRIDGE_API_KEY", "test-bridge-key")

    md = MarketData()
    today_brt = datetime.now(BRT).date()
    last_dt_utc = datetime(today_brt.year, today_brt.month, today_brt.day, 0, 0, tzinfo=timezone.utc)
    base_day = last_dt_utc - timedelta(days=69)
    rows = []
    for i in range(69):
        dt_utc = base_day + timedelta(days=i)
        rows.append(
            {
                "time": int(dt_utc.timestamp()),
                "open": 130.0,
                "high": 131.0,
                "low": 129.5,
                "close": 130.0,
                "volume": 2_000_000,
            }
        )
    rows.append(
        {
            "time": int(last_dt_utc.timestamp()),
            "open": 129.0,
            "high": 129.2,
            "low": 126.0,
            "close": 126.5,  # -2.69% crash vs 130.0 no pregão de hoje!
            "volume": 3_500_000,
        }
    )

    class FakeResp200:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_bridge_get(url, headers=None, params=None, timeout=None):
        if "/rates/" in url:
            return FakeResp200(rows)
        if "/tick/" in url:
            return FakeResp200(
                {
                    "bid": 126.48,
                    "ask": 126.52,
                    "last": 126.50,
                    "spread_pct": 0.0003,
                    "volume_real": 50000,
                    "time": int(datetime.now(BRT).timestamp()),
                }
            )
        raise AssertionError(url)

    monkeypatch.setattr(md._bridge_session, "get", fake_bridge_get)
    try:
        df_d1 = md.get_ohlcv("BOVA11", period="3mo", interval="1d")
        assert df_d1 is not None
        # Data civil preservada: 2026-10-11 00:00 BRT (NUNCA deslocada para 2026-10-10 21:00 BRT!)
        assert df_d1.index[-1].date() == last_dt_utc.date()
        assert df_d1.index[-1].hour == 0

        regime = md.get_market_regime("BOVA11")
        assert regime["is_bearish_crash"] is True
        assert regime["change_pct"] == pytest.approx(-2.69, abs=0.05)
    finally:
        md.close()

    # Fast-fail HTTP 503 em /tick e /rates quando EA reporta connected=False (waiting_broker_auth)
    rpc_backend = MQL5FileRPCBackend(tmp_path, default_timeout=2.0)
    (tmp_path / "heartbeat.json").write_text(
        json.dumps(
            {
                "ok": True,
                "connected": False,
                "terminal_connected": False,
                "trade_allowed": False,
                "server": "ClearInvestimentos-CLEAR",
                "balance": 0.0,
                "equity": 0.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge_app, "mt5", rpc_backend)
    monkeypatch.setenv("BRIDGE_API_KEY", "fast-503-key")
    headers = {"X-Bridge-Api-Key": "fast-503-key"}
    with TestClient(bridge_app.app) as client:
        t0 = time.monotonic()
        r_tick = client.get("/tick/PETR4", headers=headers)
        r_rates = client.get("/rates/PETR4?timeframe=D1&count=10", headers=headers)
        elapsed = time.monotonic() - t0
        assert r_tick.status_code == 503
        assert r_rates.status_code == 503
        assert elapsed < 0.3


def test_real05_withdrawal_goal_uses_operational_value_and_d2_nets_same_day_buys(tmp_path, monkeypatch):
    db_path = tmp_path / "real05.db"
    monkeypatch.setattr(settings, "DB_PATH", str(db_path))
    monkeypatch.setattr(settings, "B3_FEE_RATE", 0.0003)

    engine = CortexEngine(force_simulation=True)
    try:
        engine.settings.SIMULATION_MODE = False
        engine.prod_portfolio = Portfolio(initial_capital=5200.0, is_simulated=False)
        engine.prod_portfolio.adjust_ai_capital(200.0)  # Cofre = 5000, IA = 200
        engine.portfolio = engine.prod_portfolio
        engine.prod_broker = MagicMock()
        engine.prod_broker.is_connected = True
        engine.prod_broker.get_balance.return_value = 5200.0
        engine.prod_broker.get_positions.return_value = []
        engine.broker = engine.prod_broker
        engine.telegram = MagicMock()
        engine.news_scraper = MagicMock()
        engine.news_scraper.fetch_all_news.return_value = []
        engine.settings.WATCHLIST = []
        engine.market_data = MagicMock()
        engine.market_data.update_prices.return_value = {}
        engine.market_data.get_prices_batch.return_value = {}
        engine.market_data.get_ohlcv_batch.return_value = {}
        engine.market_data.get_market_regime.return_value = {"is_bearish_crash": False, "change_pct": 0.0}

        # Cria meta de lucro operacional: base = 200, meta = +50 -> target_equity = 250
        gid = engine.db.create_withdrawal_goal(
            amount=50.0, mode="on_profit", target_equity=250.0, base_equity=200.0
        )
        # Mesmo com total_value = R$ 5.200 (> 250!), operational_value é R$ 200 -> NÃO pode concluir a meta!
        engine._run_trading_cycle()
        assert engine.db.get_active_withdrawal_goal() is not None
        assert engine.db.get_active_withdrawal_goal()["id"] == gid

        # Agora simula lucro operacional elevando operational_value para R$ 255 (>= 250)
        engine.prod_portfolio.set_free_cash(5255.0)
        engine.prod_portfolio.ai_allocated_capital = 255.0
        engine.prod_broker.get_balance.return_value = 5255.0
        engine._run_trading_cycle()
        assert engine.db.get_active_withdrawal_goal() is None

        # Netting multilateral D+2: Venda R$ 1.000,00 e Compra R$ 600,00 no mesmo dia -> saldo D+2 ~ R$ 399,70
        now_iso = datetime.now(BRT).isoformat()
        engine.db.insert_trade("VALE3", "SELL", 20, 50.0, 1000.0, is_simulated=False, environment="production", timestamp=now_iso)
        engine.db.insert_trade("ITUB4", "BUY", 20, 30.0, 600.0, is_simulated=False, environment="production", timestamp=now_iso)
        d2 = engine.db.get_d2_settlement_summary(scheduler=MarketScheduler())
        expected_net_d2 = round(1000.0 * (1.0 - 0.0003) - 600.0, 2)
        assert d2["unsettled_d2_cash"] == pytest.approx(expected_net_d2, abs=0.02)
    finally:
        engine.stop()


def test_penny_stock_trailing_anti_day_trade_rebuy_and_backtester_partial_tp(tmp_path, monkeypatch):
    # 1) Penny stock @ R$ 1,00 subindo a R$ 1,02 com distância de 0,5% -> stop móvel <= R$ 1,01 (nunca 1,02!)
    rm = RiskManager(stop_loss_percent=0.05)
    rm.update_parameters(stop_loss_percent=0.05, trailing_trigger_percent=0.015, trailing_distance_percent=0.005)
    penny = Position(ticker="COGN3", quantity=100, entry_price=1.00, stop_loss=0.95, current_price=1.02)
    assert rm.update_trailing_stop(penny) is True
    assert penny.stop_loss <= 1.01

    # 2) Anti-Day-Trade retém venda discricionária mesmo em leve oscilação negativa (acima do SL) e bloqueia recompra no mesmo dia
    monkeypatch.setattr(settings, "AVOID_DAY_TRADE_TAX", True)
    monkeypatch.setattr(settings, "OPENING_COOLDOWN_MINUTES", 0)
    monkeypatch.setattr(settings, "ENABLE_MTF_CONFIRMATION", False)
    db = DatabaseManager(str(tmp_path / "adt.db"))
    port = Portfolio(initial_capital=5000.0)
    port.add_position(
        Position("BBDC4", 20, entry_price=20.00, stop_loss=18.50, current_price=19.70, entry_time=datetime.now(BRT))
    )
    tech_mock = MagicMock(spec=TechnicalAnalyzer)
    tech_mock.analyze.return_value = TechnicalResult(
        signal=TrendSignal.STRONG_SELL,
        ema_9=19.8,
        ema_21=20.1,
        ema_50=20.4,
        rsi=72.0,
        support=18.8,
        resistance=20.5,
        macd_hist=-0.25,
        atr=0.4,
        rel_vol=1.2,
        bb_lower=19.0,
        bb_upper=20.8,
        confidence=0.82,
        reasoning="Sinal técnico de venda",
    )
    sent_mock = MagicMock(spec=SentimentAnalyzer)
    sent_mock.get_sentiment_for_ticker.return_value = SentimentResult(
        score=-0.3, label="NEGATIVO", confidence=0.8, news_count=1, top_headline="Queda", reasoning="Negativo"
    )
    md_mock = MagicMock()
    md_mock.get_current_price.return_value = {"last": 19.70, "bid": 19.69, "ask": 19.71, "is_stale": False}
    md_mock.is_quote_stale.return_value = False
    md_mock.get_ohlcv.return_value = _sample_df(75, 19.70)

    dec_eng = DecisionEngine(tech_mock, sent_mock, rm, md_mock, port, db)
    dec_loss_same_day = dec_eng.evaluate("BBDC4", [])
    assert dec_loss_same_day.action == Action.HOLD

    # Bloqueio de recompra no mesmo dia após venda no CortexEngine
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "rebuy.db"))
    engine = CortexEngine(force_simulation=True)
    try:
        engine.settings.AVOID_DAY_TRADE_TAX = True
        engine.settings.WATCHLIST = ["PETR4"]
        engine.db.insert_trade(
            "PETR4", "SELL", 10, 34.0, 340.0, is_simulated=True, environment="homologation",
            timestamp=datetime.now(BRT).isoformat(),
        )
        engine.news_scraper = MagicMock()
        engine.news_scraper.fetch_all_news.return_value = []
        engine.telegram = MagicMock()
        engine.market_data = MagicMock()
        engine.market_data.update_prices.return_value = {"PETR4": 35.0}
        engine.market_data.get_variation.return_value = None
        engine.market_data.get_prices_batch.return_value = {"PETR4": {"last": 35.0}}
        engine.market_data.get_ohlcv_batch.return_value = {"PETR4": _sample_df(75, 35.0)}
        engine.market_data.get_market_regime.return_value = {"is_bearish_crash": False, "change_pct": 0.1}
        engine.decision_engine = MagicMock()
        engine.decision_engine.evaluate.return_value = Decision(
            ticker="PETR4",
            action=Action.BUY,
            confidence=0.9,
            reasoning="Sinal de compra",
            technical_score=0.9,
            sentiment_score=0.4,
            quantity=5,
            price=35.0,
            stop_loss=32.5,
        )
        engine.broker = MagicMock()
        engine.broker.get_balance.return_value = 5000.0
        engine.broker.get_positions.return_value = []

        engine._run_trading_cycle()
        engine.broker.buy.assert_not_called()
    finally:
        engine.stop()

    # 3) _portfolio_reduce_position funciona sem TypeError no Take-Profit parcial do Backtester
    bt_port = Portfolio(initial_capital=5000.0)
    bt_port.add_position(Position("WEGE3", 10, entry_price=30.0, stop_loss=27.5, current_price=35.0))
    _portfolio_reduce_position(bt_port, "WEGE3", 5, 35.0)
    rem = bt_port.get_position("WEGE3")
    assert rem is not None and rem.quantity == 5


def test_security_csp_external_login_js_and_telegram_channel_isolation():
    DatabaseManager()
    client = TestClient(dashboard_app)
    res = client.get("/login")
    assert res.status_code == 200
    html_text = res.text
    assert '/static/js/login.js' in html_text
    assert "<script>" not in html_text
    csp = res.headers.get("Content-Security-Policy", "")
    script_directive = [part.strip() for part in csp.split(";") if part.strip().startswith("script-src")]
    assert len(script_directive) == 1
    assert "'unsafe-inline'" not in script_directive[0]

    # Isolamento Telegram: channel_post e mensagens vindas de TELEGRAM_CHANNEL_ID jamais executam comandos
    tg = TelegramNotifier(token="1234567:ABCDEFGHIJKLMNOPQRSTUV123456", chat_id="777001", channel_id="-100999888")
    sent_messages: list[tuple[str, str | None, str | None]] = []
    tg._send = lambda text, parse_mode="Markdown", target_chat_id=None: (  # type: ignore[method-assign]
        sent_messages.append((text, parse_mode, target_chat_id)) or True
    )
    status_spy = MagicMock(return_value="<b>OK HTML</b>")
    tg.register_command("/pensamentos", status_spy, parse_mode="HTML")

    tg._process_updates(
        [
            {"update_id": 1, "channel_post": {"chat": {"id": -100999888}, "text": "/pensamentos"}},
            {"update_id": 2, "message": {"chat": {"id": -100999888}, "text": "/pensamentos"}},
        ]
    )
    status_spy.assert_not_called()

    tg._process_updates([{"update_id": 3, "message": {"chat": {"id": 777001}, "text": "/pensamentos"}}])
    status_spy.assert_called_once()
    assert sent_messages[-1][1] == "HTML"
