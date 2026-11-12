"""
Testes integrados da versão v2.9.0 — Blindagem Operacional de Dinheiro Real na B3 e MT5 Bridge.

Cobre:
1. Isolamento estrito do Cofre Reserva (`vault_reserve_cash`) nos limites de concentração (25%/40%).
2. Spread Guard (`MAX_SPREAD_PERCENT`) e Slippage dinâmico (`MAX_SLIPPAGE_PERCENT`) em `RestMT5Broker` e `MT5Broker`.
3. Endpoints `/tick/{symbol}`, `/rates/{symbol}` e `/health` da Bridge + prioridade 0ms e fallback no `MarketData`.
4. Escudo Tributário PF Anti-Day-Trade (`AVOID_DAY_TRADE_TAX`) em vendas discricionárias e Take-Profit parcial, preservando Stop-Loss emergencial imediato.
5. Guarda de Abertura (`OPENING_COOLDOWN_MINUTES`), Filtro de Regime (`BOVA11`) e Confirmação Multi-Timeframe (`60m`).
6. Contabilização líquida de emolumentos B3 (`B3_FEE_RATE`), Liquidação `D+2` em dias úteis (`add_business_days` + `get_d2_settlement_summary`) e expurgo seguro (`prune_old_operational_logs`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
import numpy as np
import pandas as pd
import pytest

from analysis.decision import DecisionEngine
from analysis.sentiment import SentimentAnalyzer, SentimentResult
from analysis.technical import TechnicalAnalyzer, TechnicalResult, TrendSignal
import bridge.app as bridge_app
from broker.base import OrderStatus
from broker.mt5_broker import MT5Broker
from broker.rest_mt5_broker import RestMT5Broker
from config.settings import settings
from core.engine import CortexEngine
from core.risk_manager import RiskManager
from core.scheduler import MarketScheduler
from data.database import DatabaseManager
from data.market_data import MarketData
from models.data_models import Action, Decision, Position
from portfolio import Portfolio
from utils.helpers import BRT


def _make_ohlcv_df(rows: int = 80, last_close: float = 20.0) -> pd.DataFrame:
    idx = pd.date_range(end=datetime.now(BRT), periods=rows, freq="B", tz=BRT)
    closes = np.linspace(last_close * 0.9, last_close, rows)
    return pd.DataFrame(
        {
            "Open": closes * 0.995,
            "High": closes * 1.01,
            "Low": closes * 0.99,
            "Close": closes,
            "Volume": np.full(rows, 1_500_000, dtype=np.int64),
        },
        index=idx,
    )


def _make_sentiment(score: float = 0.5, label: str = "POSITIVO") -> SentimentResult:
    return SentimentResult(
        score=score,
        label=label,
        confidence=0.82,
        news_count=2,
        top_headline="Resultado operacional forte",
        reasoning="Fluxo corporativo positivo.",
    )


def _bullish_tech(price: float = 20.0) -> TechnicalResult:
    return TechnicalResult(
        signal=TrendSignal.STRONG_BUY,
        ema_9=price * 0.99,
        ema_21=price * 0.97,
        ema_50=price * 0.94,
        rsi=58.0,
        support=price * 0.95,
        resistance=price * 1.08,
        macd_hist=0.35,
        atr=0.5,
        rel_vol=1.4,
        bb_lower=price * 0.93,
        bb_upper=price * 1.06,
        confidence=0.88,
        reasoning="Tendência altista sólida.",
    )


def _bearish_tech(price: float = 22.0) -> TechnicalResult:
    return TechnicalResult(
        signal=TrendSignal.STRONG_SELL,
        ema_9=price * 1.01,
        ema_21=price * 1.03,
        ema_50=price * 1.05,
        rsi=74.0,
        support=price * 0.94,
        resistance=price * 1.02,
        macd_hist=-0.40,
        atr=0.5,
        rel_vol=1.3,
        bb_lower=price * 0.92,
        bb_upper=price * 1.04,
        confidence=0.85,
        reasoning="Reversão baixista confirmada.",
    )


# =============================================================================
# 1. Isolamento Estrito do Cofre Reserva nos Limites de Concentração (25%/40%)
# =============================================================================


def test_vault_reserve_excluded_from_concentration_limits_in_decision_and_engine(tmp_path, monkeypatch):
    """
    Conta real com R$ 5.000,00 (R$ 4.900,00 no Cofre Reserva e R$ 100,00 no Teto da IA)
    JAMAIS deve usar R$ 5.000,00 como denominador de 25% por ativo: o limite por ativo
    deve ser 25% de R$ 100,00 = R$ 25,00 (máx 1 ação de R$ 20,00, nunca 5 ações).
    """
    monkeypatch.setattr(settings, "MAX_CONCENTRATION", 0.25)
    monkeypatch.setattr(settings, "OPENING_COOLDOWN_MINUTES", 0)
    monkeypatch.setattr(settings, "ENABLE_MTF_CONFIRMATION", False)

    db = DatabaseManager(tmp_path / "vault_test.db")
    portfolio = Portfolio(initial_capital=5000.0)
    portfolio.adjust_ai_capital(100.0)  # Cofre = 4900, Teto IA = 100, Poder Compra = 100
    assert portfolio.vault_reserve_cash == pytest.approx(4900.0)
    assert portfolio.buying_power == pytest.approx(100.0)
    assert portfolio.operational_value == pytest.approx(100.0)

    risk_mgr = RiskManager(max_concentration=0.25)
    tech_mock = MagicMock(spec=TechnicalAnalyzer)
    tech_mock.analyze.return_value = _bullish_tech(20.0)

    sent_mock = MagicMock(spec=SentimentAnalyzer)
    sent_mock.get_sentiment_for_ticker.return_value = _make_sentiment(0.5, "POSITIVO")

    md_mock = MagicMock()
    md_mock.get_current_price.return_value = {"last": 20.0, "bid": 19.99, "ask": 20.00, "is_stale": False}
    md_mock.is_quote_stale.return_value = False
    md_mock.get_ohlcv.return_value = _make_ohlcv_df(80, 20.0)
    md_mock.get_market_regime.return_value = {"benchmark": "BOVA11", "change_pct": 0.4, "is_bearish_crash": False}

    dec_engine = DecisionEngine(
        technical=tech_mock,
        sentiment=sent_mock,
        risk_manager=risk_mgr,
        market_data=md_mock,
        portfolio=portfolio,
        db=db,
    )

    decision = dec_engine.evaluate("PETR4", [])
    assert decision.action == Action.BUY
    # 25% de R$ 100,00 = R$ 25,00 -> apenas 1 ação @ R$ 20,00 (se usasse R$ 5.000 compraria até 5 ações)
    assert decision.quantity == 1

    # Valida também a segunda barreira em CortexEngine._execute_buy
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "engine_vault.db")
    engine = CortexEngine(force_simulation=True)
    engine.portfolio = portfolio
    engine.broker = MagicMock()

    oversized_decision = Decision(
        ticker="PETR4",
        action=Action.BUY,
        confidence=0.90,
        reasoning="Teste bloqueio concentração operacional",
        technical_score=0.9,
        sentiment_score=0.5,
        quantity=4,  # 4 * R$ 20 = R$ 80 (> 25% de R$ 100 op, embora < 25% de R$ 5.000 total)
        price=20.0,
        stop_loss=19.0,
    )
    engine._execute_buy(oversized_decision)
    engine.broker.buy.assert_not_called()


# =============================================================================
# 2. Spread Guard & Slippage Dinâmico em RestMT5Broker e MT5Broker
# =============================================================================


def test_rest_mt5_broker_spread_guard_and_dynamic_slippage(monkeypatch):
    monkeypatch.setattr(settings, "MAX_SPREAD_PERCENT", 0.008)  # 0.8%
    monkeypatch.setattr(settings, "MAX_SLIPPAGE_PERCENT", 0.003)  # 0.3%

    broker = RestMT5Broker(base_url="http://127.0.0.1:5000")
    broker._connected = True

    # 1) Spread abusivo (2.5% > 0.8%) -> REJECTED imediatamente sem enviar ordem à bolsa
    sent_payloads: list[dict] = []

    def fake_api_wide_spread(endpoint, data=None, method="POST", raise_on_timeout=False):
        if endpoint.startswith("symbol_info/"):
            return {"filling_mode": 4, "digits": 2}
        if endpoint.startswith("tick/"):
            return {"bid": 20.00, "ask": 20.50, "last": 20.20}
        if endpoint == "order_send":
            sent_payloads.append(data)
        return {"retcode": 0}

    monkeypatch.setattr(broker, "_api_call", fake_api_wide_spread)
    rej_order = broker.buy("PETR4", 10, 20.10, stop_loss=19.20)
    assert rej_order.status == OrderStatus.REJECTED
    assert len(sent_payloads) == 0

    # 2) Spread saudável (0.20% <= 0.8%) -> alinha ao ask real e calcula deviation dinâmico
    def fake_api_tight_spread(endpoint, data=None, method="POST", raise_on_timeout=False):
        if endpoint.startswith("symbol_info/"):
            return {"filling_mode": 4, "digits": 2}
        if endpoint.startswith("tick/"):
            return {"bid": 30.00, "ask": 30.06, "last": 30.03}
        if endpoint == "positions":
            return []
        if endpoint == "order_check":
            return {"retcode": 0, "comment": "Done"}
        if endpoint == "order_send":
            sent_payloads.append(data)
            return {"retcode": 10009, "order": 77701, "volume": 10.0, "price": 30.06, "comment": "Executed"}
        return None

    monkeypatch.setattr(broker, "_api_call", fake_api_tight_spread)
    ok_order = broker.buy("PETR4", 10, 30.00, stop_loss=28.80)
    assert ok_order.status == OrderStatus.FILLED
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["price"] == pytest.approx(30.06)
    # 30.06 * 0.003 / 0.01 = 9.018 -> 9 centavos de desvio máximo
    assert sent_payloads[0]["deviation"] == 9


def test_native_mt5_broker_spread_guard(monkeypatch):
    monkeypatch.setattr(settings, "MAX_SPREAD_PERCENT", 0.008)
    monkeypatch.setattr(settings, "MAX_SLIPPAGE_PERCENT", 0.003)

    fake_mt5 = MagicMock()
    fake_mt5.TRADE_ACTION_DEAL = 1
    fake_mt5.ORDER_TYPE_BUY = 0
    fake_mt5.ORDER_TIME_GTC = 0
    fake_mt5.ORDER_FILLING_RETURN = 2
    fake_mt5.symbol_info.return_value = SimpleNamespace(visible=True, digits=2, filling_mode=4)
    fake_mt5.symbol_info_tick.return_value = SimpleNamespace(bid=15.00, ask=15.45, last=15.10)  # 3.0% spread

    with patch("broker.mt5_broker.mt5", fake_mt5), patch("broker.mt5_broker._mt5_available", True):
        broker = MT5Broker()
        broker._connected = True
        order = broker.buy("VALE3", 5, 15.10, stop_loss=14.30)
        assert order.status == OrderStatus.REJECTED
        fake_mt5.order_send.assert_not_called()


# =============================================================================
# 3. Endpoints /tick, /rates e /health da Bridge + MarketData 0ms & Fallback
# =============================================================================


def test_bridge_tick_rates_and_health_endpoints(monkeypatch):
    monkeypatch.setenv("BRIDGE_API_KEY", "secret-bridge-test-key")
    fake_mt5 = MagicMock()
    fake_mt5.TIMEFRAME_D1 = 16408
    fake_mt5.TIMEFRAME_H1 = 16385
    fake_mt5.account_info.return_value = SimpleNamespace(
        login=123456, server="ClearInvestimentos-PRD", balance=2500.0, equity=2500.0, currency="BRL"
    )
    fake_mt5.terminal_info.return_value = SimpleNamespace(connected=True, trade_allowed=True)
    fake_mt5.symbol_select.return_value = True

    class FakeTick:
        def _asdict(self):
            return {"bid": 38.00, "ask": 38.04, "last": 38.02, "volume": 500, "time": 1731418800}

    fake_mt5.symbol_info_tick.return_value = FakeTick()
    fake_mt5.copy_rates_from_pos.return_value = [
        {"time": 1731330000, "open": 37.5, "high": 38.2, "low": 37.3, "close": 38.0, "tick_volume": 120, "real_volume": 45000},
        {"time": 1731416400, "open": 38.0, "high": 38.5, "low": 37.9, "close": 38.3, "tick_volume": 140, "real_volume": 52000},
    ]

    monkeypatch.setattr(bridge_app, "mt5", fake_mt5)
    headers = {"X-Bridge-Api-Key": "secret-bridge-test-key"}

    with TestClient(bridge_app.app) as client:
        h = client.get("/health", headers=headers)
        assert h.status_code == 200
        h_json = h.json()
        assert h_json["connected"] is True
        assert h_json["terminal_connected"] is True
        assert h_json["trade_allowed"] is True
        assert "login" not in h_json and "balance" not in h_json

        t = client.get("/tick/PETR4", headers=headers)
        assert t.status_code == 200
        assert t.json()["spread_pct"] == pytest.approx(0.04 / 38.00, rel=1e-3)

        r = client.get("/rates/PETR4?timeframe=D1&count=2", headers=headers)
        assert r.status_code == 200
        rows = r.json()
        assert len(rows) == 2
        assert rows[-1]["close"] == pytest.approx(38.3)
        assert rows[-1]["volume"] == 52000


def test_market_data_prioritizes_0ms_mt5_bridge_and_falls_back_to_yfinance(monkeypatch):
    monkeypatch.setattr(settings, "MT5_BRIDGE_URL", "http://127.0.0.1:5000")
    monkeypatch.setattr(settings, "BRIDGE_API_KEY", "test-key")

    md = MarketData()
    now_epoch = int(datetime.now(tz=BRT).timestamp())

    # 1) Caminho feliz 0ms via Bridge (sem tocar no yfinance)
    class FakeResp200:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get_ok(url, headers=None, params=None, timeout=None):
        if "/tick/" in url:
            return FakeResp200({"bid": 41.10, "ask": 41.14, "last": 41.12, "spread_pct": 0.00097, "volume_real": 9000, "time": now_epoch})
        if "/rates/" in url:
            base_ts = now_epoch - 100 * 86400
            return FakeResp200([
                {
                    "time": base_ts + i * 86400,
                    "open": 40.0 + i * 0.02,
                    "high": 40.5 + i * 0.02,
                    "low": 39.8 + i * 0.02,
                    "close": 40.2 + i * 0.02,
                    "volume": 100_000,
                }
                for i in range(70)
            ])
        raise AssertionError(f"URL inesperada: {url}")

    monkeypatch.setattr(md._bridge_session, "get", fake_get_ok)
    yf_spy = MagicMock(return_value=None)
    monkeypatch.setattr(md, "_get_price_yfinance", yf_spy)

    px = md.get_current_price("VALE3")
    assert px["source"] == "mt5_bridge"
    assert px["last"] == pytest.approx(41.12)
    yf_spy.assert_not_called()

    df = md.get_ohlcv("VALE3", period="3mo", interval="1d")
    assert len(df) == 70
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]

    # 2) Se a Bridge retornar 503 -> aplica backoff de 30s e cai suavemente para yfinance
    md.clear_cache()

    class FakeResp503:
        status_code = 503

        def json(self):
            return {}

    monkeypatch.setattr(md._bridge_session, "get", lambda *a, **kw: FakeResp503())
    monkeypatch.setattr(
        md,
        "_get_price_yfinance",
        lambda base: {
            "bid": 41.00,
            "ask": 41.05,
            "last": 41.02,
            "volume": 1000,
            "timestamp": datetime.now(tz=BRT).isoformat(),
            "source": "yfinance",
            "ticker": base,
            "is_stale": False,
        },
    )

    fallback_px = md.get_current_price("VALE3")
    assert fallback_px["source"] == "yfinance"
    assert fallback_px["last"] == pytest.approx(41.02)
    assert md._get_bridge_base_url() == ""  # em backoff


# =============================================================================
# 4. Escudo Tributário PF Anti-Day-Trade (Swing-Trade D+1 vs Stop-Loss Imediato)
# =============================================================================


def test_anti_day_trade_tax_shield_holds_same_day_profit_but_executes_stop_loss_and_d1(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "AVOID_DAY_TRADE_TAX", True)
    monkeypatch.setattr(settings, "OPENING_COOLDOWN_MINUTES", 0)
    monkeypatch.setattr(settings, "ENABLE_MTF_CONFIRMATION", False)

    db = DatabaseManager(tmp_path / "tax_shield.db")
    portfolio = Portfolio(initial_capital=10000.0)
    now_brt = datetime.now(BRT)

    # Posição aberta HOJE a R$ 20,00 e agora em lucro a R$ 22,00 com sinal técnico de venda
    same_day_pos = Position(
        ticker="WEGE3",
        quantity=10,
        entry_price=20.00,
        stop_loss=19.00,
        current_price=22.00,
        entry_time=now_brt,
    )
    portfolio.add_position(same_day_pos)

    tech_mock = MagicMock(spec=TechnicalAnalyzer)
    tech_mock.analyze.return_value = _bearish_tech(22.00)
    sent_mock = MagicMock(spec=SentimentAnalyzer)
    sent_mock.get_sentiment_for_ticker.return_value = _make_sentiment(-0.45, "NEGATIVO")
    md_mock = MagicMock()
    md_mock.get_current_price.return_value = {"last": 22.00, "bid": 21.99, "ask": 22.01, "is_stale": False}
    md_mock.is_quote_stale.return_value = False
    md_mock.get_ohlcv.return_value = _make_ohlcv_df(80, 22.00)

    dec_engine = DecisionEngine(
        technical=tech_mock,
        sentiment=sent_mock,
        risk_manager=RiskManager(),
        market_data=md_mock,
        portfolio=portfolio,
        db=db,
    )

    # 4a) Venda discricionária em lucro no mesmo dia -> retida como HOLD p/ proteger isenção R$ 20k PF
    hold_dec = dec_engine.evaluate("WEGE3", [])
    assert hold_dec.action == Action.HOLD
    assert "Anti-Day-Trade" in hold_dec.reasoning

    # 4b) Mesma posição aberta hoje cai abaixo do Stop-Loss (R$ 18,80 <= R$ 19,00) -> VENDA_EMERGENCIAL imediata!
    md_mock.get_current_price.return_value = {"last": 18.80, "bid": 18.79, "ask": 18.81, "is_stale": False}
    stop_dec = dec_engine.evaluate("WEGE3", [])
    assert stop_dec.action == Action.EMERGENCY_SELL
    assert stop_dec.quantity == 10

    # 4c) Posição aberta ONTEM (D-1) com lucro -> VENDA normal permitida (Swing-Trade isento até R$ 20k/mês)
    portfolio._positions[0].entry_time = now_brt - timedelta(days=1)
    md_mock.get_current_price.return_value = {"last": 22.00, "bid": 21.99, "ask": 22.01, "is_stale": False}
    swing_sell_dec = dec_engine.evaluate("WEGE3", [])
    assert swing_sell_dec.action == Action.SELL
    assert swing_sell_dec.quantity == 10


def test_partial_take_profit_same_day_locks_fee_adjusted_breakeven_without_selling(tmp_path, monkeypatch):
    """
    Se um ativo comprado hoje subir +16% no mesmo dia com AVOID_DAY_TRADE_TAX=True,
    o motor retém a venda parcial para D+1, mas eleva imediatamente o Stop-Loss para
    o breakeven líquido de taxas B3.
    """
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "tp_shield.db")
    monkeypatch.setattr(settings, "AVOID_DAY_TRADE_TAX", True)
    monkeypatch.setattr(settings, "B3_FEE_RATE", 0.0003)

    engine = CortexEngine(force_simulation=True)
    engine.settings.AVOID_DAY_TRADE_TAX = True
    engine.settings.B3_FEE_RATE = 0.0003
    engine.settings.WATCHLIST = []  # evita varredura externa
    engine.news_scraper = MagicMock()
    engine.news_scraper.fetch_all_news.return_value = []
    engine.telegram = MagicMock()
    engine.market_data = MagicMock()
    engine.market_data.update_prices.return_value = {"ITUB4": 23.50}
    engine.market_data.get_variation.return_value = None
    engine.market_data.get_ohlcv.return_value = None
    engine.market_data.get_prices_batch.return_value = {}
    engine.market_data.get_ohlcv_batch.return_value = {}
    engine.market_data.get_market_regime.return_value = {
        "benchmark": "BOVA11",
        "change_pct": 0.3,
        "is_bearish_crash": False,
    }
    engine.decision_engine = MagicMock()
    engine.decision_engine.evaluate.return_value = Decision(
        ticker="ITUB4",
        action=Action.HOLD,
        confidence=0.5,
        reasoning="Hold",
        technical_score=0.5,
        sentiment_score=0.0,
        quantity=10,
        price=23.50,
        stop_loss=20.03,
    )

    pos = Position(
        ticker="ITUB4",
        quantity=10,
        entry_price=20.00,
        stop_loss=19.00,
        current_price=23.50,  # +17.5% (>= +15% TP parcial)
        entry_time=datetime.now(BRT),
    )
    engine.portfolio = Portfolio(initial_capital=5000.0)
    engine.portfolio.add_position(pos)

    engine.broker = MagicMock()
    engine.broker.get_balance.return_value = 4800.0
    engine.broker.get_positions.return_value = [pos]

    engine._run_trading_cycle()

    # Não vendeu no mesmo dia, mas blindou o Stop-Loss acima de R$ 20,00 cobrindo taxas!
    engine.broker.sell.assert_not_called()
    updated_pos = engine.portfolio.get_position("ITUB4")
    assert updated_pos is not None
    assert updated_pos.quantity == 10
    assert updated_pos.stop_loss >= 20.03
    engine.broker.modify_stop_loss.assert_called_once_with("ITUB4", updated_pos.stop_loss)


# =============================================================================
# 5. Guarda de Abertura, Filtro de Regime BOVA11 e Confirmação Multi-Timeframe
# =============================================================================


def test_opening_cooldown_regime_crash_and_mtf_filters_block_entries(tmp_path, monkeypatch):
    db = DatabaseManager(tmp_path / "filters.db")
    portfolio = Portfolio(initial_capital=5000.0)
    tech_mock = MagicMock(spec=TechnicalAnalyzer)
    sent_mock = MagicMock(spec=SentimentAnalyzer)
    sent_mock.get_sentiment_for_ticker.return_value = _make_sentiment(0.4, "POSITIVO")
    md_mock = MagicMock()
    md_mock.get_current_price.return_value = {"last": 25.00, "bid": 24.99, "ask": 25.01, "is_stale": False}
    md_mock.is_quote_stale.return_value = False
    md_mock.get_ohlcv.return_value = _make_ohlcv_df(80, 25.00)

    dec_engine = DecisionEngine(
        technical=tech_mock,
        sentiment=sent_mock,
        risk_manager=RiskManager(),
        market_data=md_mock,
        portfolio=portfolio,
        db=db,
    )

    # 5a) Guarda de abertura ativa -> bloqueia BUY
    monkeypatch.setattr(settings, "OPENING_COOLDOWN_MINUTES", 15)
    monkeypatch.setattr(settings, "ENABLE_MTF_CONFIRMATION", False)
    tech_mock.analyze.return_value = _bullish_tech(25.00)
    sched_open = MagicMock()
    sched_open.is_opening_stabilization.return_value = True

    d_open = dec_engine.evaluate("BBAS3", [], scheduler=sched_open)
    assert d_open.action == Action.HOLD
    assert "Guarda de Abertura" in d_open.reasoning

    # 5b) Regime sistêmico em crash (BOVA11 -2.1%) -> bloqueia BUY
    monkeypatch.setattr(settings, "OPENING_COOLDOWN_MINUTES", 0)
    d_crash = dec_engine.evaluate(
        "BBAS3",
        [],
        market_regime={"benchmark": "BOVA11", "change_pct": -2.10, "is_bearish_crash": True},
    )
    assert d_crash.action == Action.HOLD
    assert "BOVA11" in d_crash.reasoning

    # 5c) Multi-Timeframe 60m em reversão baixista -> bloqueia BUY mesmo com 1d altista
    monkeypatch.setattr(settings, "ENABLE_MTF_CONFIRMATION", True)
    df_1d = _make_ohlcv_df(80, 25.00)
    df_60m = _make_ohlcv_df(50, 25.00)
    md_mock.get_ohlcv.side_effect = lambda ticker, period, interval: df_60m if interval == "60m" else df_1d
    tech_mock.analyze.side_effect = [
        _bullish_tech(25.00),  # 1d
        _bearish_tech(25.00),  # 60m
    ]

    d_mtf = dec_engine.evaluate(
        "BBAS3",
        [],
        ohlcv_df=df_1d,
        market_regime={"benchmark": "BOVA11", "change_pct": 0.25, "is_bearish_crash": False},
    )
    assert d_mtf.action == Action.HOLD
    assert "Multi-Timeframe" in d_mtf.reasoning


# =============================================================================
# 6. Emolumentos B3, Liquidação D+2 em Dias Úteis e Expurgo SQLite Seguro
# =============================================================================


def test_b3_fees_d2_settlement_and_sqlite_log_pruning(tmp_path, monkeypatch):
    db_file = tmp_path / "accounting.db"
    monkeypatch.setattr(settings, "DB_PATH", db_file)
    monkeypatch.setattr(settings, "B3_FEE_RATE", 0.0003)

    sched = MarketScheduler()
    # Sexta-feira 2026-11-06 -> D+2 dias úteis deve cair na Terça-feira 2026-11-10 (pulando sáb/dom)
    assert sched.add_business_days(date(2026, 11, 6), 2) == date(2026, 11, 10)
    # Quinta-feira 2026-04-02 (véspera da Sexta-Feira Santa 2026-04-03) -> Seg (D+1), Ter 2026-04-07 (D+2)
    assert sched.add_business_days(date(2026, 4, 2), 2) == date(2026, 4, 7)

    engine = CortexEngine(force_simulation=True)
    # Simula operação de Produção p/ testar _record_production_realized_pnl e get_production_summary
    engine.settings.SIMULATION_MODE = False
    engine.settings.B3_FEE_RATE = 0.0003
    engine.prod_portfolio = Portfolio(initial_capital=1000.0)
    engine.prod_portfolio.adjust_ai_capital(400.0)  # Cofre = 600, IA = 400

    # Venda de 100 ações compradas a R$ 10,00 e vendidas a R$ 11,00 -> lucro bruto R$ 100,00
    # Taxa B3 = (10 + 11) * 100 * 0.0003 = R$ 0,63 -> lucro líquido R$ 99,37
    engine.prod_portfolio.free_cash = 1100.0
    engine.prod_portfolio.ai_allocated_capital = 500.0
    net_pnl = engine._record_production_realized_pnl(
        ticker="PETR4",
        pnl=100.0,
        reason="Venda swing-trade",
        entry_price=10.0,
        exit_price=11.0,
        quantity=100,
    )
    assert net_pnl == pytest.approx(99.37)
    assert engine.prod_portfolio.ai_allocated_capital == pytest.approx(499.37)

    # Registra venda real hoje -> deve constar como D+2 em liquidação (não sacável ainda)
    now_iso = datetime.now(BRT).isoformat()
    engine.db.insert_trade(
        ticker="PETR4",
        action="SELL",
        quantity=100,
        price=11.0,
        total_value=1100.0,
        is_simulated=False,
        environment="production",
        timestamp=now_iso,
    )
    d2 = engine.db.get_d2_settlement_summary(scheduler=sched)
    assert d2["pending_sales_count"] == 1
    assert d2["unsettled_d2_cash"] == pytest.approx(round(1100.0 * (1.0 - 0.0003), 2))
    assert d2["next_settlement_date"] is not None

    prod_sum = engine.get_production_summary()
    assert prod_sum["unsettled_d2_cash"] == pytest.approx(d2["unsettled_d2_cash"])
    assert prod_sum["settled_withdrawable_cash"] == pytest.approx(
        max(0.0, prod_sum["free_cash"] - d2["unsettled_d2_cash"]),
        abs=0.02,
    )

    # Expurgo seletivo do SQLite: remove AGUARDAR antigo (>14d), mas preserva COMPRA/VENDA eternamente
    old_ts = (datetime.now(BRT) - timedelta(days=25)).isoformat()
    recent_ts = (datetime.now(BRT) - timedelta(days=2)).isoformat()
    engine.db.insert_decision("VALE3", "AGUARDAR", 0.30, timestamp=old_ts)
    engine.db.insert_decision("VALE3", "AGUARDAR", 0.35, timestamp=recent_ts)
    engine.db.insert_decision("PETR4", "COMPRA", 0.88, timestamp=old_ts)
    engine.db.insert_news(
        "Notícia antiga",
        "InfoMoney",
        "https://ex.com/old",
        published_at=(datetime.now(BRT) - timedelta(days=45)).isoformat(),
    )

    stats = engine.db.prune_old_operational_logs(keep_hold_days=14, keep_news_days=30, keep_health_days=14)
    assert stats["ai_decisions_hold"] >= 1
    assert stats["news_items"] >= 1

    remaining_decisions = engine.db._fetch_all("SELECT ticker, action, timestamp FROM ai_decisions")
    assert any(r["ticker"] == "PETR4" and r["action"] == "COMPRA" for r in remaining_decisions)
    assert any(r["ticker"] == "VALE3" and r["timestamp"] == recent_ts for r in remaining_decisions)
    assert not any(r["action"] == "AGUARDAR" and r["timestamp"] == old_ts for r in remaining_decisions)


def test_sync_portfolio_with_broker_preserves_d1_entry_time(tmp_path, monkeypatch):
    """
    Garante que ao reiniciar o serviço em D+1, a reconciliação com o broker MT5
    preserve `entry_time` original da posição aberta em D-1 (permitindo Swing-Trade normal).
    """
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "sync_entry_time.db")
    engine = CortexEngine(force_simulation=True)

    d1_dt = datetime.now(BRT) - timedelta(days=2)
    broker_pos = Position(
        ticker="PETR4F",
        quantity=15,
        entry_price=36.00,
        stop_loss=33.50,
        current_price=38.00,
        entry_time=d1_dt,
    )
    engine.broker = MagicMock()
    engine.broker.get_balance.return_value = 2000.0
    engine.broker.get_positions.return_value = [broker_pos]
    engine.portfolio = Portfolio(initial_capital=2540.0)

    engine._sync_portfolio_with_broker()

    synced = engine.portfolio.get_position("PETR4")
    assert synced is not None
    assert synced.entry_time.date() == d1_dt.date()
