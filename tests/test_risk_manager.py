"""
Testes do RiskManager do Projeto Córtex.

Valida cálculos de stop-loss, validação de ordens,
dimensionamento de posições e gatilhos de risco.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.risk_manager import RiskManager
from models.data_models import Position


class TestCalculateStopLoss:
    """Testes para o cálculo de stop-loss."""

    def test_calculate_stop_loss_standard(self) -> None:
        """Stop-loss deve ser entry_price * 0.90 (10% abaixo)."""
        rm = RiskManager(stop_loss_percent=0.10)
        entry_price = 100.00
        stop_loss = rm.calculate_stop_loss(entry_price)
        assert stop_loss == pytest.approx(90.00)

    def test_calculate_stop_loss_precise(self) -> None:
        """Stop-loss para preço fracionário deve ser preciso."""
        rm = RiskManager(stop_loss_percent=0.10)
        entry_price = 32.75
        stop_loss = rm.calculate_stop_loss(entry_price)
        assert stop_loss == pytest.approx(32.75 * 0.90)

    def test_calculate_stop_loss_custom_percent(self) -> None:
        """Stop-loss com percentual customizado."""
        rm = RiskManager(stop_loss_percent=0.05)
        entry_price = 50.00
        stop_loss = rm.calculate_stop_loss(entry_price)
        assert stop_loss == pytest.approx(47.50)

    def test_calculate_stop_loss_zero_price(self) -> None:
        """Stop-loss com preço zero deve retornar zero."""
        rm = RiskManager()
        assert rm.calculate_stop_loss(0.0) == pytest.approx(0.0)


class TestCheckStopLossTriggers:
    """Testes para verificação de gatilhos de stop-loss."""

    def test_stop_loss_trigger_below_threshold(self) -> None:
        """Posição com preço abaixo do SL deve ser disparada."""
        rm = RiskManager()
        position = Position(
            ticker='PETR4',
            quantity=10,
            entry_price=30.00,
            stop_loss=27.00,
            current_price=26.50,
        )
        market_data = MagicMock()
        market_data.get_current_price.return_value = 26.50

        triggered = rm.check_stop_loss_triggers([position], market_data)

        assert len(triggered) == 1
        assert triggered[0].ticker == 'PETR4'

    def test_stop_loss_trigger_at_threshold(self) -> None:
        """Posição com preço exatamente no SL deve ser disparada."""
        rm = RiskManager()
        position = Position(
            ticker='VALE3',
            quantity=5,
            entry_price=60.00,
            stop_loss=54.00,
            current_price=54.00,
        )
        market_data = MagicMock()
        market_data.get_current_price.return_value = 54.00

        triggered = rm.check_stop_loss_triggers([position], market_data)

        assert len(triggered) == 1
        assert triggered[0].ticker == 'VALE3'

    def test_stop_loss_not_triggered_above_threshold(self) -> None:
        """Posição com preço acima do SL não deve ser disparada."""
        rm = RiskManager()
        position = Position(
            ticker='ITUB4',
            quantity=8,
            entry_price=25.00,
            stop_loss=22.50,
            current_price=24.00,
        )
        market_data = MagicMock()
        market_data.get_current_price.return_value = 24.00

        triggered = rm.check_stop_loss_triggers([position], market_data)

        assert len(triggered) == 0

    def test_stop_loss_multiple_positions_mixed(self) -> None:
        """Apenas posições com SL ativado devem ser retornadas."""
        rm = RiskManager()
        positions = [
            Position(ticker='PETR4', quantity=10, entry_price=30.00,
                     stop_loss=27.00, current_price=26.00),
            Position(ticker='VALE3', quantity=5, entry_price=60.00,
                     stop_loss=54.00, current_price=55.00),
            Position(ticker='ITUB4', quantity=8, entry_price=25.00,
                     stop_loss=22.50, current_price=20.00),
        ]
        market_data = MagicMock()
        market_data.get_current_price.side_effect = [26.00, 55.00, 20.00]

        triggered = rm.check_stop_loss_triggers(positions, market_data)

        assert len(triggered) == 2
        tickers = [p.ticker for p in triggered]
        assert 'PETR4' in tickers
        assert 'ITUB4' in tickers
        assert 'VALE3' not in tickers

    def test_stop_loss_empty_positions(self) -> None:
        """Lista vazia de posições deve retornar lista vazia."""
        rm = RiskManager()
        market_data = MagicMock()
        triggered = rm.check_stop_loss_triggers([], market_data)
        assert triggered == []

    def test_stop_loss_price_unavailable_uses_cached(self) -> None:
        """Quando preço indisponível, usa preço em cache da posição."""
        rm = RiskManager()
        position = Position(
            ticker='PETR4',
            quantity=10,
            entry_price=30.00,
            stop_loss=27.00,
            current_price=26.00,  # Preço em cache abaixo do SL
        )
        market_data = MagicMock()
        market_data.get_current_price.return_value = None

        triggered = rm.check_stop_loss_triggers([position], market_data)

        assert len(triggered) == 1


class TestValidateOrder:
    """Testes para validação de ordens."""

    def test_validate_order_valid(self) -> None:
        """Ordem válida deve ser aprovada."""
        rm = RiskManager()
        is_valid, reason = rm.validate_order(
            ticker='PETR4',
            quantity=5,
            price=30.00,
            available_capital=200.00,
        )
        assert is_valid is True
        assert reason == 'Ordem válida'

    def test_validate_order_max_fractional(self) -> None:
        """Ordem com 99 ações (máximo fracionário) deve ser válida."""
        rm = RiskManager()
        is_valid, reason = rm.validate_order(
            ticker='ABEV3',
            quantity=99,
            price=1.50,
            available_capital=200.00,
        )
        assert is_valid is True

    def test_validate_order_min_fractional(self) -> None:
        """Ordem com 1 ação (mínimo) deve ser válida."""
        rm = RiskManager()
        is_valid, reason = rm.validate_order(
            ticker='WEGE3',
            quantity=1,
            price=40.00,
            available_capital=200.00,
        )
        assert is_valid is True

    def test_validate_order_insufficient_capital(self) -> None:
        """Ordem com capital insuficiente deve ser rejeitada."""
        rm = RiskManager()
        is_valid, reason = rm.validate_order(
            ticker='VALE3',
            quantity=10,
            price=60.00,
            available_capital=200.00,
        )
        assert is_valid is False
        assert 'Capital insuficiente' in reason

    def test_validate_order_quantity_above_max(self) -> None:
        """Quantidade acima de 99 deve ser rejeitada."""
        rm = RiskManager()
        is_valid, reason = rm.validate_order(
            ticker='PETR4',
            quantity=100,
            price=30.00,
            available_capital=5000.00,
        )
        assert is_valid is False
        assert '100' in reason

    def test_validate_order_quantity_below_min(self) -> None:
        """Quantidade abaixo de 1 deve ser rejeitada."""
        rm = RiskManager()
        is_valid, reason = rm.validate_order(
            ticker='PETR4',
            quantity=0,
            price=30.00,
            available_capital=200.00,
        )
        assert is_valid is False
        assert '0' in reason

    def test_validate_order_negative_quantity(self) -> None:
        """Quantidade negativa deve ser rejeitada."""
        rm = RiskManager()
        is_valid, reason = rm.validate_order(
            ticker='PETR4',
            quantity=-5,
            price=30.00,
            available_capital=200.00,
        )
        assert is_valid is False

    def test_validate_order_exact_capital(self) -> None:
        """Ordem que usa todo o capital disponível deve ser válida."""
        rm = RiskManager()
        is_valid, reason = rm.validate_order(
            ticker='PETR4',
            quantity=5,
            price=40.00,
            available_capital=200.00,
        )
        assert is_valid is True


class TestGetMaxShares:
    """Testes para cálculo de quantidade máxima de ações."""

    def test_get_max_shares_standard(self) -> None:
        """Cálculo padrão de max shares."""
        rm = RiskManager()
        max_shares = rm.get_max_shares(price=30.00, available_capital=200.00)
        assert max_shares == 6  # 200 / 30 = 6.66 → 6

    def test_get_max_shares_limited_by_99(self) -> None:
        """Max shares deve ser limitado a 99."""
        rm = RiskManager()
        max_shares = rm.get_max_shares(price=1.00, available_capital=500.00)
        assert max_shares == 99

    def test_get_max_shares_insufficient_capital(self) -> None:
        """Capital insuficiente deve retornar 0."""
        rm = RiskManager()
        max_shares = rm.get_max_shares(price=300.00, available_capital=200.00)
        assert max_shares == 0

    def test_get_max_shares_zero_price(self) -> None:
        """Preço zero deve retornar 0."""
        rm = RiskManager()
        max_shares = rm.get_max_shares(price=0.0, available_capital=200.00)
        assert max_shares == 0

    def test_get_max_shares_zero_capital(self) -> None:
        """Capital zero deve retornar 0."""
        rm = RiskManager()
        max_shares = rm.get_max_shares(price=30.00, available_capital=0.0)
        assert max_shares == 0

    def test_get_max_shares_exact_fit(self) -> None:
        """Quando capital divide exatamente pelo preço."""
        rm = RiskManager()
        max_shares = rm.get_max_shares(price=20.00, available_capital=200.00)
        assert max_shares == 10

    def test_get_max_shares_one_share(self) -> None:
        """Capital suficiente para exatamente 1 ação."""
        rm = RiskManager()
        max_shares = rm.get_max_shares(price=199.00, available_capital=200.00)
        assert max_shares == 1
