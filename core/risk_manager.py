"""
Gerenciador de risco do Projeto Córtex.

Responsável por cálculo de stop-loss, validação de ordens,
dimensionamento de posições e verificação de gatilhos de risco.
Este é o componente de MAIOR PRIORIDADE — executa antes de qualquer análise.

Funcionalidades:
- Stop-loss adaptativo por ATR com piso de segurança e arredondamento B3 (R$ 0,01)
- Trailing stop-loss dinâmico (ativa após gatilho de lucro configurável)
- Realização parcial de lucro (+15%) apenas para posições com >= 2 ações
- Limite de concentração por ativo e exposição setorial real
- Circuit breaker de perda diária
- Dimensionamento modulado por confiança
"""

from __future__ import annotations

import logging
import math
from datetime import datetime
from typing import Any, Optional

from config.settings import settings
from models.data_models import BRT, Position
from utils.helpers import normalize_b3_ticker

logger = logging.getLogger('cortex.risk_manager')


class RiskManager:
    """Gerenciador de risco para operações de trading da B3."""

    def __init__(
        self,
        stop_loss_percent: float | None = None,
        max_positions: int | None = None,
        daily_loss_limit: float | None = None,
        min_shares: int | None = None,
        max_shares: int | None = None,
        max_concentration: float | None = None,
    ) -> None:
        """
        Inicializa o gerenciador de risco resolvendo defaults em tempo de execução.

        Args:
            stop_loss_percent: Percentual de perda máxima tolerada (0.10 = 10%).
            max_positions: Limite de posições simultâneas (0 = ilimitado).
            daily_loss_limit: Limite de drawdown diário (0.03 = 3%).
            min_shares: Quantidade mínima por ordem.
            max_shares: Quantidade máxima por ordem.
            max_concentration: Concentração máxima por ativo (0.25 = 25%).
        """
        resolved_sl = float(
            settings.STOP_LOSS_PERCENT if stop_loss_percent is None else stop_loss_percent
        )
        resolved_max_pos = int(
            getattr(settings, 'MAX_POSITIONS', 0) if max_positions is None else max_positions
        )
        resolved_daily_loss = float(
            getattr(settings, 'MAX_DAILY_LOSS_PERCENT', 0.03)
            if daily_loss_limit is None
            else daily_loss_limit
        )
        resolved_min_shares = int(
            getattr(settings, 'min_quantity', 1) if min_shares is None else min_shares
        )
        resolved_max_shares = int(
            getattr(settings, 'max_quantity', 50000) if max_shares is None else max_shares
        )
        resolved_concentration = float(
            getattr(settings, 'MAX_CONCENTRATION', 0.25)
            if max_concentration is None
            else max_concentration
        )

        self.stop_loss_percent: float = resolved_sl
        self.max_positions: int = resolved_max_pos
        self.daily_loss_limit: float = -abs(resolved_daily_loss)
        self.min_shares: int = resolved_min_shares
        self.max_shares: int = resolved_max_shares
        self.max_concentration: float = resolved_concentration
        self.trailing_stop_activation: float = float(
            getattr(settings, 'TRAILING_STOP_TRIGGER_PERCENT', 0.02)
        )
        self.trailing_stop_distance: float = float(
            getattr(settings, 'TRAILING_STOP_DISTANCE_PERCENT', 0.015)
        )
        if self.trailing_stop_distance >= self.trailing_stop_activation:
            self.trailing_stop_distance = round(
                max(0.005, self.trailing_stop_activation * 0.8), 4
            )

        self.MIN_SHARES: int = self.min_shares
        self.MAX_SHARES: int = self.max_shares
        self.MAX_CONCENTRATION: float = self.max_concentration
        self.DAILY_LOSS_LIMIT: float = self.daily_loss_limit
        self.TRAILING_STOP_ACTIVATION: float = self.trailing_stop_activation

        self._daily_pnl: float = 0.0
        self._circuit_breaker_active: bool = False
        self._last_reset_date: Optional[str] = None
        pos_desc = f"{self.max_positions}" if self.max_positions > 0 else "Ilimitadas"
        logger.info(
            'RiskManager inicializado — stop-loss: %.1f%%, max posições: %s, '
            'concentração max: %.0f%%, circuit breaker: %.1f%%',
            self.stop_loss_percent * 100,
            pos_desc,
            self.max_concentration * 100,
            self.daily_loss_limit * 100,
        )

    def check_take_profit_triggers(self, positions: list[Position]) -> list[Position]:
        """
        Verifica se alguma posição elegível (>= 2 ações) atingiu o alvo de lucro parcial (15%).
        Retorna posições que devem ter ~50% do volume vendido.
        """
        triggered: list[Position] = []
        take_profit_activation = 0.15

        for position in positions:
            if getattr(position, 'partial_exit_done', False):
                continue
            if position.quantity < 2:
                continue
            if (
                position.current_price is None
                or position.current_price <= 0
                or position.entry_price <= 0
            ):
                continue

            gain_pct = (position.current_price - position.entry_price) / position.entry_price
            if gain_pct >= take_profit_activation:
                triggered.append(position)
                logger.info(
                    '🎯 TAKE-PROFIT PARCIAL ATINGIDO: %s — lucro de +%.1f%% (qty=%d)',
                    position.ticker, gain_pct * 100, position.quantity,
                )

        return triggered

    def calculate_stop_loss(self, entry_price: float, atr: float | None = None) -> float:
        """
        Calcula o preço de stop-loss adaptativo arredondado ao tick da B3 (R$ 0,01).

        Garante distância mínima de segurança mesmo quando o ATR está muito comprimido.
        """
        if entry_price <= 0:
            return 0.0

        fixed_stop = entry_price * (1.0 - self.stop_loss_percent)

        if atr is not None and atr > 0:
            min_dist_pct = min(self.stop_loss_percent, max(0.01, self.trailing_stop_distance))
            min_distance_price = entry_price * (1.0 - min_dist_pct)
            atr_stop = min(entry_price - (1.5 * atr), min_distance_price)
            stop_price = max(atr_stop, fixed_stop)
            logger.debug(
                'Stop-loss adaptativo calculado: entrada R$ %.2f, ATR %.2f → stop R$ %.2f',
                entry_price, atr, stop_price,
            )
        else:
            stop_price = fixed_stop
            logger.debug(
                'Stop-loss fixo calculado: entrada R$ %.2f → stop R$ %.2f (%.1f%%)',
                entry_price, stop_price, self.stop_loss_percent * 100,
            )

        return round(max(0.0, stop_price), 2)

    def update_trailing_stop(self, position: Position) -> bool:
        """
        Atualiza stop-loss com trailing stop quando a posição supera o gatilho de lucro.
        """
        if (
            position.current_price is None
            or position.current_price <= 0
            or position.entry_price <= 0
        ):
            return False

        gain_pct = (position.current_price - position.entry_price) / position.entry_price

        if gain_pct < self.TRAILING_STOP_ACTIVATION:
            return False

        new_stop = round(position.current_price * (1.0 - self.trailing_stop_distance), 2)

        if new_stop > position.stop_loss:
            old_stop = position.stop_loss
            position.stop_loss = new_stop
            logger.info(
                '📈 TRAILING STOP atualizado: %s — R$ %.2f → R$ %.2f '
                '(preço atual: R$ %.2f, ganho: +%.1f%%)',
                position.ticker, old_stop, position.stop_loss,
                position.current_price, gain_pct * 100,
            )
            return True

        return False

    def check_stop_loss_triggers(
        self,
        positions: list[Position],
        market_data: object,
    ) -> list[Position]:
        """
        Verifica gatilhos de stop-loss para todas as posições (prioridade máxima).
        """
        triggered: list[Position] = []

        for position in positions:
            try:
                price_data = getattr(market_data, 'get_current_price', lambda t: None)(
                    position.ticker
                )

                if isinstance(price_data, dict):
                    current_price = price_data.get('last')
                else:
                    current_price = price_data

                if current_price is None or current_price <= 0:
                    current_price = position.current_price

                if current_price is None or current_price <= 0:
                    logger.warning(
                        'Preço indisponível para %s — não é possível verificar stop-loss',
                        position.ticker,
                    )
                    continue

                position.current_price = float(current_price)

                self.update_trailing_stop(position)

                if position.current_price <= position.stop_loss:
                    triggered.append(position)
                    loss_pct = (
                        ((position.current_price - position.entry_price) / position.entry_price) * 100
                        if position.entry_price > 0
                        else 0.0
                    )
                    logger.warning(
                        '🚨 STOP-LOSS ATIVADO: %s — preço R$ %.2f <= SL R$ %.2f '
                        '(entrada R$ %.2f, variação %.1f%%)',
                        position.ticker,
                        position.current_price,
                        position.stop_loss,
                        position.entry_price,
                        loss_pct,
                    )
                else:
                    distance_pct = (
                        ((position.current_price - position.stop_loss) / position.stop_loss) * 100
                    ) if (position.stop_loss and position.stop_loss > 0) else 0.0
                    logger.debug(
                        'Stop-loss OK: %s — preço R$ %.2f, SL R$ %.2f (distância: +%.1f%%)',
                        position.ticker,
                        position.current_price,
                        position.stop_loss,
                        distance_pct,
                    )

            except Exception as e:
                logger.error(
                    'Erro ao verificar stop-loss de %s: %s',
                    position.ticker, e,
                )

        if triggered:
            logger.warning(
                '⚠️ %d posição(ões) com stop-loss ativado: %s',
                len(triggered),
                ', '.join(p.ticker for p in triggered),
            )
        else:
            logger.debug(
                'Verificação de stop-loss concluída — %d posições OK',
                len(positions),
            )

        return triggered

    @staticmethod
    def _position_market_value(pos: Position) -> float:
        price = (
            pos.current_price
            if (getattr(pos, 'current_price', 0.0) and pos.current_price > 0)
            else pos.entry_price
        )
        return float(price) * int(pos.quantity)

    def validate_order(
        self,
        ticker: str,
        quantity: int,
        price: float,
        available_capital: float,
        total_portfolio_value: float = 0.0,
        positions: list[Position] | None = None,
    ) -> tuple[bool, str]:
        """
        Valida uma ordem antes da execução respeitando circuit breaker, capital,
        concentração por ativo e exposição setorial real.
        """
        if self._circuit_breaker_active:
            reason = (
                f'Circuit breaker ativo — operações suspensas '
                f'(perda diária: {self._daily_pnl*100:.1f}%)'
            )
            logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
            return False, reason

        base_ticker = normalize_b3_ticker(ticker)

        if self.max_positions > 0 and positions is not None:
            is_new = not any(normalize_b3_ticker(p.ticker) == base_ticker for p in positions)
            if is_new and len(positions) >= self.max_positions:
                reason = f'Limite máximo de {self.max_positions} posições simultâneas atingido'
                logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
                return False, reason

        if price <= 0:
            reason = f'Preço inválido: R$ {price:.2f}'
            logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
            return False, reason

        if quantity < self.MIN_SHARES:
            reason = (
                f'Quantidade inválida: {quantity} < {self.MIN_SHARES} '
                f'(mínimo permitido)'
            )
            logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
            return False, reason

        if quantity > self.MAX_SHARES:
            reason = (
                f'Quantidade inválida: {quantity} > {self.MAX_SHARES} '
                f'(máximo permitido)'
            )
            logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
            return False, reason

        total_cost = price * quantity
        if total_cost > available_capital + 1e-9:
            reason = (
                f'Capital insuficiente: R$ {total_cost:.2f} necessário, '
                f'R$ {available_capital:.2f} disponível'
            )
            logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
            return False, reason

        if total_portfolio_value > 0:
            existing_val = (
                sum(
                    self._position_market_value(p)
                    for p in positions
                    if normalize_b3_ticker(p.ticker) == base_ticker
                )
                if positions
                else 0.0
            )
            max_position_val = total_portfolio_value * self.max_concentration
            allow_small_acc_single = (
                existing_val == 0.0
                and quantity == 1
                and max_position_val < price <= available_capital
            )
            if not allow_small_acc_single and (total_cost + existing_val) > max_position_val + 1e-9:
                concentration = (total_cost + existing_val) / total_portfolio_value
                reason = (
                    f'Concentração excessiva: {concentration*100:.1f}% > '
                    f'{self.max_concentration*100:.0f}% máximo permitido'
                )
                logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
                return False, reason

            sector = settings.SECTOR_MAP.get(base_ticker) if base_ticker else None
            if sector is not None:
                max_sector = float(getattr(settings, 'MAX_SECTOR_EXPOSURE', 0.40))
                max_sector_val = total_portfolio_value * max_sector
                sector_val = (
                    sum(
                        self._position_market_value(p)
                        for p in positions
                        if settings.SECTOR_MAP.get(normalize_b3_ticker(p.ticker)) == sector
                    )
                    if positions
                    else 0.0
                )
                allow_small_sector_single = (
                    sector_val == 0.0
                    and quantity == 1
                    and max_sector_val < price <= available_capital
                )
                if not allow_small_sector_single and (total_cost + sector_val) > max_sector_val + 1e-9:
                    sector_concentration = (total_cost + sector_val) / total_portfolio_value
                    reason = (
                        f'Concentração setorial excessiva ({sector}): {sector_concentration*100:.1f}% > '
                        f'{max_sector*100:.0f}% máximo permitido'
                    )
                    logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
                    return False, reason

        logger.info(
            'Ordem validada: %s — %d ações @ R$ %.2f = R$ %.2f '
            '(capital disponível: R$ %.2f)',
            ticker, quantity, price, total_cost, available_capital,
        )
        return True, 'Ordem válida'

    def get_max_shares(
        self,
        price: float,
        available_capital: float,
        confidence: float = 1.0,
        total_portfolio_value: float = 0.0,
        positions: list[Position] | None = None,
        ticker: str = "",
    ) -> int:
        """
        Calcula a quantidade máxima de ações que podem ser compradas respeitando
        capital disponível, concentração individual, exposição setorial e confiança.
        """
        if price <= 0 or available_capital < price or self._circuit_breaker_active:
            logger.debug(
                'Cálculo de max_shares: preço=%.2f, capital=%.2f, cb=%s → 0',
                price, available_capital, self._circuit_breaker_active,
            )
            return 0

        base_ticker = normalize_b3_ticker(ticker)
        if self.max_positions > 0 and positions is not None and base_ticker:
            is_new = not any(normalize_b3_ticker(p.ticker) == base_ticker for p in positions)
            if is_new and len(positions) >= self.max_positions:
                return 0

        max_by_capital = int(available_capital // price)

        if total_portfolio_value > 0:
            existing_val = (
                sum(
                    self._position_market_value(p)
                    for p in positions
                    if base_ticker and normalize_b3_ticker(p.ticker) == base_ticker
                )
                if positions
                else 0.0
            )
            max_position_val = total_portfolio_value * self.max_concentration
            if existing_val == 0.0 and max_position_val < price <= available_capital:
                max_by_concentration = 1
            else:
                remaining_pos_val = max(0.0, max_position_val - existing_val)
                max_by_concentration = int((remaining_pos_val + 1e-9) // price)

            max_by_sector = max_by_capital
            sector = settings.SECTOR_MAP.get(base_ticker) if base_ticker else None
            if sector is not None:
                max_sector = float(getattr(settings, 'MAX_SECTOR_EXPOSURE', 0.40))
                max_sector_val = total_portfolio_value * max_sector
                sector_val = (
                    sum(
                        self._position_market_value(p)
                        for p in positions
                        if settings.SECTOR_MAP.get(normalize_b3_ticker(p.ticker)) == sector
                    )
                    if positions
                    else 0.0
                )
                if sector_val == 0.0 and max_sector_val < price <= available_capital:
                    max_by_sector = 1
                else:
                    allowed_sector_capital = max(0.0, max_sector_val - sector_val)
                    max_by_sector = int((allowed_sector_capital + 1e-9) // price)

            raw_max_shares = min(max_by_capital, max_by_concentration, max_by_sector)
        else:
            raw_max_shares = max_by_capital

        if raw_max_shares <= 0:
            return 0

        if confidence < 0.5:
            confidence_factor = 0.5
        elif confidence < 0.7:
            confidence_factor = 0.7
        else:
            confidence_factor = 1.0

        scaled_shares = int(raw_max_shares * confidence_factor)
        max_shares = max(1, scaled_shares) if confidence_factor >= 0.5 else scaled_shares

        result = max(0, min(max_shares, self.MAX_SHARES))

        logger.debug(
            'Max shares: preço R$ %.2f, capital R$ %.2f, confiança %.0f%% → %d ações '
            '(custo total: R$ %.2f)',
            price, available_capital, confidence * 100, result, price * result,
        )
        return result

    def update_daily_pnl(self, pnl_percent: float) -> None:
        """Atualiza o P&L diário e verifica o circuit breaker."""
        today = datetime.now(BRT).strftime('%Y-%m-%d')
        if self._last_reset_date != today:
            self._daily_pnl = 0.0
            self._circuit_breaker_active = False
            self._last_reset_date = today

        self._daily_pnl = pnl_percent

        if pnl_percent <= self.DAILY_LOSS_LIMIT and not self._circuit_breaker_active:
            self._circuit_breaker_active = True
            logger.warning(
                '🛑 CIRCUIT BREAKER ATIVADO — Perda diária de %.1f%% '
                'excede limite de %.1f%%. Operações suspensas até amanhã.',
                pnl_percent * 100,
                self.DAILY_LOSS_LIMIT * 100,
            )

    def reset_daily_limits(self) -> None:
        """Reseta limites diários (chamado no início de cada dia ou troca de ambiente)."""
        self._daily_pnl = 0.0
        self._circuit_breaker_active = False
        self._last_reset_date = datetime.now(BRT).strftime('%Y-%m-%d')
        logger.debug('Limites diários de risco resetados')

    @property
    def is_circuit_breaker_active(self) -> bool:
        """Retorna se o circuit breaker está ativo."""
        return self._circuit_breaker_active

    def update_parameters(
        self,
        stop_loss_percent: float | None = None,
        trailing_trigger_percent: float | None = None,
        trailing_distance_percent: float | None = None,
    ) -> None:
        """Atualiza dinamicamente parâmetros de risco garantindo `trailing_distance < trailing_trigger`."""
        if stop_loss_percent is not None:
            self.stop_loss_percent = max(0.01, min(0.50, float(stop_loss_percent)))
        if trailing_trigger_percent is not None:
            self.trailing_stop_activation = max(0.005, min(0.30, float(trailing_trigger_percent)))
            self.TRAILING_STOP_ACTIVATION = self.trailing_stop_activation
        if trailing_distance_percent is not None:
            self.trailing_stop_distance = max(0.005, min(0.20, float(trailing_distance_percent)))

        if self.trailing_stop_distance >= self.trailing_stop_activation:
            self.trailing_stop_distance = round(
                max(0.005, self.trailing_stop_activation * 0.8), 4
            )

        logger.info(
            "RiskManager parâmetros atualizados — SL: %.1f%%, Trailing Trigger: %.1f%%, Trailing Dist: %.2f%%",
            self.stop_loss_percent * 100,
            self.trailing_stop_activation * 100,
            self.trailing_stop_distance * 100,
        )

    def select_positions_for_liquidation(
        self,
        positions: list[Position],
        target_amount: float,
        market_data: Any = None,
    ) -> list[dict[str, Any]]:
        """
        Seleciona posições priorizando maior ganho percentual e menor volume necessário
        para levantar `target_amount`.
        """
        if target_amount <= 0 or not positions:
            return []

        scored_positions: list[tuple[float, float, Position]] = []
        for pos in positions:
            price = float(pos.current_price or 0.0)
            if price <= 0 and market_data is not None and hasattr(market_data, 'get_current_price'):
                try:
                    pdata = market_data.get_current_price(pos.ticker)
                    if isinstance(pdata, dict):
                        price = float(pdata.get('last', 0.0) or 0.0)
                    elif isinstance(pdata, (int, float)):
                        price = float(pdata)
                except Exception:
                    price = 0.0
            if price <= 0:
                price = float(pos.entry_price or 0.0)
            if price <= 0 or pos.quantity <= 0:
                continue
            gain_pct = (price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0.0
            scored_positions.append((gain_pct, price, pos))

        scored_positions.sort(key=lambda item: item[0], reverse=True)

        plan: list[dict[str, Any]] = []
        remaining_needed = target_amount

        for gain_pct, price, pos in scored_positions:
            if remaining_needed <= 0.01:
                break

            val_total_pos = price * pos.quantity
            if val_total_pos <= remaining_needed:
                qty_to_sell = pos.quantity
            else:
                qty_to_sell = min(pos.quantity, max(1, math.ceil(remaining_needed / price)))

            est_value = qty_to_sell * price
            remaining_needed -= est_value

            plan.append({
                "ticker": pos.ticker,
                "quantity": qty_to_sell,
                "price": price,
                "estimated_value": round(est_value, 2),
                "gain_pct": round(gain_pct * 100, 2),
                "is_full_exit": (qty_to_sell >= pos.quantity),
                "reason": "Smart Liquidation for Capital Management / Auto-Withdrawal",
            })

        return plan
