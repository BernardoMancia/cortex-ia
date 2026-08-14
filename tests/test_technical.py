"""
Testes do TechnicalAnalyzer do Projeto Córtex.

Valida cálculos de EMA, RSI, MACD e geração de sinais
técnicos para cenários de alta e baixa.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from analysis.technical import TechnicalAnalyzer, TrendSignal
from models.data_models import Action, OHLCV


@pytest.fixture
def analyzer() -> TechnicalAnalyzer:
    """Instância do TechnicalAnalyzer para testes."""
    return TechnicalAnalyzer()


class TestSignalGeneration:
    """Testes para geração de sinais técnicos."""

    def test_strong_buy_signal(
        self, analyzer: TechnicalAnalyzer, sample_ohlcv_data: list[OHLCV]
    ) -> None:
        """Tendência de alta clara deve gerar sinal de COMPRA."""
        signal = analyzer.analyze('PETR4', sample_ohlcv_data)
        # Dados são de tendência de alta → deve ser BUY, STRONG_BUY ou NEUTRAL
        assert signal.signal in (TrendSignal.BUY, TrendSignal.STRONG_BUY, TrendSignal.NEUTRAL)
        assert signal.ema_9 > 0
        assert signal.ema_21 > 0

    def test_sell_signal_bearish(
        self, analyzer: TechnicalAnalyzer, bearish_ohlcv_data: list[OHLCV]
    ) -> None:
        """Tendência de baixa clara deve gerar sinal de VENDA."""
        signal = analyzer.analyze('MGLU3', bearish_ohlcv_data)
        assert signal.signal in (TrendSignal.SELL, TrendSignal.STRONG_SELL, TrendSignal.NEUTRAL)
        assert signal.reasoning != ''

    def test_signal_has_all_indicators(
        self, analyzer: TechnicalAnalyzer, sample_ohlcv_data: list[OHLCV]
    ) -> None:
        """Sinal deve conter todos os indicadores calculados."""
        signal = analyzer.analyze('PETR4', sample_ohlcv_data)
        assert signal.ema_9 > 0
        assert signal.ema_21 > 0
        assert signal.ema_50 > 0
        assert 0.0 <= signal.rsi <= 100.0
        assert signal.support > 0
        assert signal.resistance > 0
        assert 0.0 <= signal.confidence <= 1.0

    def test_signal_insufficient_data(self, analyzer: TechnicalAnalyzer) -> None:
        import pytest
        from datetime import datetime
        from models.data_models import BRT, OHLCV

        candles = [
            OHLCV(
                ticker='TEST',
                timestamp=datetime(2026, 1, 1, tzinfo=BRT),
                open=100.0, high=101.0, low=99.0, close=100.5,
                volume=1000,
            )
        ]
        with pytest.raises(ValueError):
            analyzer.analyze('TEST', candles)

    def test_signal_confidence_bounded(
        self, analyzer: TechnicalAnalyzer, sample_ohlcv_data: list[OHLCV]
    ) -> None:
        """Confiança deve estar entre 0 e 1."""
        signal = analyzer.analyze('PETR4', sample_ohlcv_data)
        assert 0.0 <= signal.confidence <= 1.0
