"""
Testes do TechnicalAnalyzer do Projeto Córtex.

Valida cálculos de EMA, RSI, MACD e geração de sinais
técnicos para cenários direcionais de alta, baixa e crash severo.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from analysis.technical import TechnicalAnalyzer, TrendSignal
from models.data_models import BRT, OHLCV


@pytest.fixture
def analyzer() -> TechnicalAnalyzer:
    """Instância do TechnicalAnalyzer para testes."""
    return TechnicalAnalyzer()


class TestSignalGeneration:
    """Testes para geração de sinais técnicos."""

    def test_strong_buy_signal(
        self, analyzer: TechnicalAnalyzer, sample_ohlcv_data: list[OHLCV]
    ) -> None:
        """Tendência de alta clara deve gerar sinal direcional de COMPRA (jamais NEUTRAL)."""
        signal = analyzer.analyze("PETR4", sample_ohlcv_data)
        assert signal.signal in (TrendSignal.BUY, TrendSignal.STRONG_BUY)
        assert signal.ema_9 > signal.ema_21
        assert signal.ema_21 > signal.ema_50

    def test_sell_signal_bearish(
        self, analyzer: TechnicalAnalyzer, bearish_ohlcv_data: list[OHLCV]
    ) -> None:
        """Tendência de baixa clara deve gerar sinal direcional de VENDA (jamais NEUTRAL)."""
        signal = analyzer.analyze("MGLU3", bearish_ohlcv_data)
        assert signal.signal in (TrendSignal.SELL, TrendSignal.STRONG_SELL)
        assert signal.ema_9 < signal.ema_21
        assert signal.reasoning != ""

    def test_panic_crash_oversold_still_generates_sell(self, analyzer: TechnicalAnalyzer) -> None:
        """Crash severo (RSI < 30 e abaixo da BB inferior) NÃO deve suprimir sinal de VENDA."""
        base_time = datetime(2026, 6, 1, 10, 0, tzinfo=BRT)
        candles: list[OHLCV] = []
        price = 100.0
        for i in range(70):
            if i < 50:
                price = 100.0 - (i * 0.2)
            else:
                price = price * 0.965  # queda abrupta contínua -> RSI profundamente abaixo de 30
            candles.append(
                OHLCV(
                    ticker="CRASH3",
                    timestamp=base_time + timedelta(days=i),
                    open=round(price * 1.01, 2),
                    high=round(price * 1.015, 2),
                    low=round(price * 0.985, 2),
                    close=round(price, 2),
                    volume=2_500_000,
                )
            )
        signal = analyzer.analyze("CRASH3", candles)
        assert signal.rsi < 30.0
        assert signal.signal in (TrendSignal.SELL, TrendSignal.STRONG_SELL)

    def test_signal_has_all_indicators(
        self, analyzer: TechnicalAnalyzer, sample_ohlcv_data: list[OHLCV]
    ) -> None:
        """Sinal deve conter todos os indicadores calculados."""
        signal = analyzer.analyze("PETR4", sample_ohlcv_data)
        assert signal.ema_9 > 0
        assert signal.ema_21 > 0
        assert signal.ema_50 > 0
        assert 0.0 <= signal.rsi <= 100.0
        assert signal.support > 0
        assert signal.resistance > 0
        assert 0.0 <= signal.confidence <= 1.0

    def test_signal_insufficient_data(self, analyzer: TechnicalAnalyzer) -> None:
        candles = [
            OHLCV(
                ticker="TEST",
                timestamp=datetime(2026, 1, 1, tzinfo=BRT),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.5,
                volume=1000,
            )
        ]
        with pytest.raises(ValueError):
            analyzer.analyze("TEST", candles)

    def test_signal_confidence_bounded(
        self, analyzer: TechnicalAnalyzer, sample_ohlcv_data: list[OHLCV]
    ) -> None:
        """Confiança deve estar entre 0 e 1."""
        signal = analyzer.analyze("PETR4", sample_ohlcv_data)
        assert 0.0 <= signal.confidence <= 1.0
