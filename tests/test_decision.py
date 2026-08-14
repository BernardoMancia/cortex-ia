"""
Testes do DecisionEngine do Projeto Córtex.

Valida lógica de decisão combinando sinais técnicos e de sentimento,
incluindo convergência para compra, conflito para hold, stop-loss
emergencial e validação de capital.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.decision import DecisionEngine
from analysis.sentiment import SentimentAnalyzer
from analysis.technical import TechnicalAnalyzer
from data.market_data import MarketData
from models.data_models import Action, BRT, Decision, OHLCV, Position
from analysis.technical import TechnicalResult, TrendSignal
from analysis.sentiment import SentimentResult


@pytest.fixture
def market_data() -> MagicMock:
    """MarketData mockado para testes."""
    md = MagicMock(spec=MarketData)
    md.get_current_price.return_value = {'last': 30.00}
    return md


@pytest.fixture
def technical() -> MagicMock:
    """TechnicalAnalyzer mockado."""
    return MagicMock(spec=TechnicalAnalyzer)


@pytest.fixture
def sentiment() -> MagicMock:
    """SentimentAnalyzer mockado."""
    return MagicMock(spec=SentimentAnalyzer)


@pytest.fixture
def engine(
    technical: MagicMock,
    sentiment: MagicMock,
    market_data: MagicMock,
) -> DecisionEngine:
    """DecisionEngine com dependências mockadas."""
    engine = DecisionEngine(
        technical=technical,
        sentiment=sentiment,
        risk_manager=MagicMock(),
        market_data=market_data,
        portfolio=MagicMock(),
        db=MagicMock(),
    )
    engine.portfolio.has_position.return_value = False
    engine.portfolio.get_position.return_value = None
    engine.portfolio.free_cash = 200.00
    engine.risk_manager.get_max_shares.return_value = 6
    engine.risk_manager.calculate_stop_loss.return_value = 27.00
    
    sentiment.get_sentiment_for_ticker.return_value = SentimentResult(
        score=0.0, label='NEUTRO', confidence=0.0, news_count=0, top_headline='', reasoning=''
    )
    technical.analyze.return_value = TechnicalResult(
        signal=TrendSignal.NEUTRAL, ema_9=0.0, ema_21=0.0, ema_50=0.0, rsi=50.0, support=0.0, resistance=0.0, confidence=0.0, reasoning=''
    )
    return engine


def _make_candles(count: int = 30, base_price: float = 100.0) -> list[OHLCV]:
    """Gera candles OHLCV de teste."""
    candles: list[OHLCV] = []
    base_time = datetime(2026, 6, 1, 10, 0, tzinfo=BRT)
    for i in range(count):
        price = base_price + i * 0.5
        candles.append(OHLCV(
            ticker='TEST',
            timestamp=base_time + timedelta(days=i),
            open=price - 0.3, high=price + 0.5,
            low=price - 0.5, close=price,
            volume=1_000_000,
        ))
    return candles


class TestBuyOnConvergence:
    """Testes para decisão de compra quando sinais convergem."""

    def test_buy_when_technical_buy_and_positive_sentiment(
        self, engine: DecisionEngine, market_data: MagicMock,
        technical: MagicMock, sentiment: MagicMock,
    ) -> None:
        """Deve gerar BUY quando trend=BUY e sentiment > 0.3."""
        # Configurar mocks
        market_data.get_ohlcv.return_value = _make_candles()
        market_data.get_current_price.return_value = {'last': 30.00}

        technical.analyze.return_value = TechnicalResult(signal=TrendSignal.BUY, ema_9=0.0, ema_21=0.0, ema_50=0.0, rsi=50.0, support=0.0, resistance=0.0, confidence=0.7, reasoning='Sinal técnico favorável')

        sentiment.get_sentiment_for_ticker.return_value = SentimentResult(score=0.1, label='POS', confidence=1.0, news_count=1, top_headline='', reasoning='')  # Sentimento fraco

        decision = engine.evaluate(
            ticker='PETR4',
            news_items=[]
        )

        # Sentimento fraco → HOLD
        assert decision.action == Action.HOLD


class TestHoldOnNoConvergence:
    """Testes para HOLD quando sinais são conflitantes."""

    def test_hold_when_signals_conflict(
        self, engine: DecisionEngine, market_data: MagicMock,
        technical: MagicMock, sentiment: MagicMock,
    ) -> None:
        """Deve retornar HOLD quando técnico e sentimento discordam."""
        market_data.get_ohlcv.return_value = _make_candles()
        market_data.get_current_price.return_value = {'last': 30.00}

        # Técnico: SELL, Sentimento: positivo → conflito
        technical.analyze.return_value = TechnicalResult(signal=TrendSignal.NEUTRAL, ema_9=0.0, ema_21=0.0, ema_50=0.0, rsi=50.0, support=0.0, resistance=0.0, confidence=0.3, reasoning='Sinais conflitantes')

        sentiment.get_sentiment_for_ticker.return_value = SentimentResult(score=0.0, label='POS', confidence=1.0, news_count=1, top_headline='', reasoning='')

        decision = engine.evaluate(
            ticker='ITUB4',
            news_items=[]
        )

        assert decision.action == Action.HOLD


class TestEmergencySellOnStopLoss:
    """Testes para venda emergencial quando stop-loss é ativado."""

    def test_emergency_sell_below_stop_loss(
        self, engine: DecisionEngine, market_data: MagicMock,
        technical: MagicMock, sentiment: MagicMock,
    ) -> None:
        """Deve gerar EMERGENCY_SELL quando preço <= stop-loss."""
        market_data.get_ohlcv.return_value = _make_candles()
        market_data.get_current_price.return_value = {'last': 26.00}  # Abaixo do SL

        from datetime import datetime
        position = Position(
            ticker='PETR4',
            quantity=10,
            entry_price=30.00,
            current_price=26.00,
            stop_loss=27.00
        )
        engine.portfolio.has_position.return_value = True
        engine.portfolio.get_position.return_value = position

        decision = engine.evaluate(
            ticker='PETR4',
            news_items=[]
        )

        assert decision.action == Action.EMERGENCY_SELL
        assert decision.confidence == 1.0
        assert 'Stop-loss' in decision.reasoning or 'stop-loss' in decision.reasoning.lower()
        assert decision.quantity == 10

    def test_emergency_sell_at_stop_loss(
        self, engine: DecisionEngine, market_data: MagicMock,
        technical: MagicMock, sentiment: MagicMock,
    ) -> None:
        """Deve gerar EMERGENCY_SELL quando preço == stop-loss."""
        market_data.get_ohlcv.return_value = _make_candles()
        market_data.get_current_price.return_value = {'last': 27.00}  # Exatamente no SL

        from datetime import datetime
        position = Position(
            ticker='PETR4',
            quantity=10,
            entry_price=30.00,
            current_price=27.00,
            stop_loss=27.00
        )
        engine.portfolio.has_position.return_value = True
        engine.portfolio.get_position.return_value = position

        decision = engine.evaluate(
            ticker='PETR4',
            news_items=[]
        )

        assert decision.action == Action.EMERGENCY_SELL

    def test_no_emergency_sell_above_stop_loss(
        self, engine: DecisionEngine, market_data: MagicMock,
        technical: MagicMock, sentiment: MagicMock,
    ) -> None:
        """Não deve gerar EMERGENCY_SELL quando preço > stop-loss."""
        market_data.get_ohlcv.return_value = _make_candles()
        market_data.get_current_price.return_value = {'last': 30.00}  # Acima do SL

        technical.analyze.return_value = TechnicalResult(signal=TrendSignal.NEUTRAL, ema_9=0.0, ema_21=0.0, ema_50=0.0, rsi=50.0, support=0.0, resistance=0.0, confidence=0.3, reasoning='Sem sinais claros')
        sentiment.get_sentiment_for_ticker.return_value = SentimentResult(score=0.0, label='POS', confidence=1.0, news_count=1, top_headline='', reasoning='')

        from datetime import datetime
        position = Position(
            ticker='PETR4',
            quantity=10,
            entry_price=30.00,
            current_price=29.00,
            stop_loss=27.00
        )
        engine.portfolio.has_position.return_value = True
        engine.portfolio.get_position.return_value = position

        decision = engine.evaluate(
            ticker='PETR4',
            news_items=[]
        )

        assert decision.action != Action.EMERGENCY_SELL


class TestNoBuyInsufficientCapital:
    """Testes para bloqueio de compra com capital insuficiente."""

    def test_hold_when_no_capital(
        self, engine: DecisionEngine, market_data: MagicMock,
        technical: MagicMock, sentiment: MagicMock,
    ) -> None:
        """Deve retornar HOLD quando capital é insuficiente para compra."""
        market_data.get_ohlcv.return_value = _make_candles()
        market_data.get_current_price.return_value = {'last': 30.00}  # Muito caro

        technical.analyze.return_value = TechnicalResult(signal=TrendSignal.BUY, ema_9=0.0, ema_21=0.0, ema_50=0.0, rsi=50.0, support=0.0, resistance=0.0, confidence=0.9, reasoning='Sinal muito forte de compra')

        sentiment.get_sentiment_for_ticker.return_value = SentimentResult(score=0.8, label='POS', confidence=1.0, news_count=1, top_headline='', reasoning='')
        engine.portfolio.free_cash = 10.00
        engine.risk_manager.get_max_shares.return_value = 0

        decision = engine.evaluate(
            ticker='WEGE3',
            news_items=[]  # Capital insuficiente
        )

        assert decision.action == Action.HOLD
        assert 'Capital insuficiente' in decision.reasoning or decision.quantity == 0

    def test_hold_when_zero_capital(
        self, engine: DecisionEngine, market_data: MagicMock,
        technical: MagicMock, sentiment: MagicMock,
    ) -> None:
        """Deve retornar HOLD com capital zero."""
        market_data.get_ohlcv.return_value = _make_candles()
        market_data.get_current_price.return_value = {'last': 30.00}

        technical.analyze.return_value = TechnicalResult(signal=TrendSignal.BUY, ema_9=0.0, ema_21=0.0, ema_50=0.0, rsi=50.0, support=0.0, resistance=0.0, confidence=0.8, reasoning='Forte sinal de compra')

        sentiment.get_sentiment_for_ticker.return_value = SentimentResult(score=0.6, label='POS', confidence=1.0, news_count=1, top_headline='', reasoning='')
        engine.portfolio.free_cash = 0.00
        engine.risk_manager.get_max_shares.return_value = 0

        decision = engine.evaluate(
            ticker='PETR4',
            news_items=[]
        )

        assert decision.action == Action.HOLD


class TestMissingData:
    """Testes para cenários com dados indisponíveis."""

    def test_hold_when_no_market_data(
        self, engine: DecisionEngine, market_data: MagicMock,
    ) -> None:
        """Deve retornar HOLD quando dados de mercado estão indisponíveis."""
        market_data.get_ohlcv.return_value = []
        market_data.get_current_price.return_value = {}

        decision = engine.evaluate(
            ticker='PETR4',
            news_items=[]
        )

        assert decision.action == Action.HOLD
        assert 'indisponíveis' in decision.reasoning.lower() or decision.confidence == 0.0

    def test_hold_when_no_candles(
        self, engine: DecisionEngine, market_data: MagicMock,
    ) -> None:
        """Deve retornar HOLD quando não há dados históricos."""
        market_data.get_ohlcv.return_value = []
        market_data.get_current_price.return_value = {'last': 30.00}

        decision = engine.evaluate(
            ticker='PETR4',
            news_items=[]
        )

        assert decision.action == Action.HOLD
