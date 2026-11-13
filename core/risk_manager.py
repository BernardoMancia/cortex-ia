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
from datetime import date, datetime
from typing import Any, Optional

from config.settings import settings
from models.data_models import BRT, Position
from utils.helpers import is_b3_unit_lot_asset, normalize_b3_ticker

logger = logging.getLogger('cortex.risk_manager')


def _finite_float(value: Any, default: float) -> float:
    """Converte `value` em float finito ou retorna `default`."""
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else default
    except (ValueError, TypeError):
        return default


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
        raw_sl = settings.STOP_LOSS_PERCENT if stop_loss_percent is None else stop_loss_percent
        resolved_sl = _finite_float(raw_sl, 0.10)
        if resolved_sl <= 0:
            resolved_sl = 0.10
        resolved_sl = max(0.01, min(0.50, resolved_sl))

        raw_max_pos = getattr(settings, 'MAX_POSITIONS', 0) if max_positions is None else max_positions
        try:
            resolved_max_pos = max(0, int(raw_max_pos))
        except (ValueError, TypeError):
            resolved_max_pos = 0

        raw_daily_loss = (
            getattr(settings, 'MAX_DAILY_LOSS_PERCENT', 0.03)
            if daily_loss_limit is None
            else daily_loss_limit
        )
        resolved_daily_loss = abs(_finite_float(raw_daily_loss, 0.03))
        if resolved_daily_loss <= 0:
            resolved_daily_loss = 0.03
        resolved_daily_loss = max(0.001, min(1.0, resolved_daily_loss))

        raw_min_shares = getattr(settings, 'min_quantity', 1) if min_shares is None else min_shares
        try:
            resolved_min_shares = max(1, int(raw_min_shares))
        except (ValueError, TypeError):
            resolved_min_shares = 1

        raw_max_shares = getattr(settings, 'max_quantity', 50000) if max_shares is None else max_shares
        try:
            resolved_max_shares = max(resolved_min_shares, int(raw_max_shares))
        except (ValueError, TypeError):
            resolved_max_shares = max(resolved_min_shares, 50000)

        raw_conc = (
            getattr(settings, 'MAX_CONCENTRATION', 0.25)
            if max_concentration is None
            else max_concentration
        )
        resolved_concentration = _finite_float(raw_conc, 0.25)
        if resolved_concentration <= 0:
            resolved_concentration = 0.25
        resolved_concentration = max(0.01, min(1.0, resolved_concentration))

        raw_trig = _finite_float(getattr(settings, 'TRAILING_STOP_TRIGGER_PERCENT', 0.02), 0.02)
        raw_dist = _finite_float(getattr(settings, 'TRAILING_STOP_DISTANCE_PERCENT', 0.015), 0.015)

        self.stop_loss_percent: float = resolved_sl
        self.max_positions: int = resolved_max_pos
        self.daily_loss_limit: float = -abs(resolved_daily_loss)
        self.min_shares: int = resolved_min_shares
        self.max_shares: int = resolved_max_shares
        self.max_concentration: float = resolved_concentration
        self.trailing_stop_activation: float = max(0.005, min(0.30, raw_trig if raw_trig > 0 else 0.02))
        self.trailing_stop_distance: float = max(0.005, min(0.20, raw_dist if raw_dist > 0 else 0.015))
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
        self._last_reset_date: Optional[date] = None
        pos_desc = f"{self.max_positions}" if self.max_positions > 0 else "Ilimitadas"
        logger.info(
            'RiskManager inicializado — stop-loss: %.1f%%, max posições: %s, '
            'concentração max: %.0f%%, circuit breaker: %.1f%%',
            self.stop_loss_percent * 100,
            pos_desc,
            self.max_concentration * 100,
            self.daily_loss_limit * 100,
        )

    def calculate_partial_tp_quantity(
        self,
        position: Position,
        simulation_mode: bool | None = None,
    ) -> int:
        """
        Calcula a quantidade para realização parcial (~50%) respeitando a granularidade
        de lotes padrão (`100x`) e fracionários (`1..99`) da B3 em Produção.

        Em Produção (`not simulation_mode`), se o ativo operar em lote padrão de 100 e a
        custódia for exatamente 1 lote padrão (`100 <= qty < 200` com `qty % 100 == 0`),
        retorna `0` para sinalizar proteção de breakeven sem dividir o lote em mercado
        fracionário separado.
        """
        qty = int(getattr(position, 'quantity', 0) or 0)
        if qty < 2:
            return 0

        is_sim = bool(
            settings.SIMULATION_MODE if simulation_mode is None else simulation_mode
        )
        if is_sim or is_b3_unit_lot_asset(position.ticker) or qty < 100:
            return max(1, qty // 2)

        frac_rem = qty % 100
        target_half = qty // 2
        return (target_half // 100) * 100 + min(frac_rem, target_half % 100)

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
        if not math.isfinite(float(entry_price)) or entry_price <= 0:
            return 0.0

        fixed_stop = entry_price * (1.0 - self.stop_loss_percent)

        if atr is not None and math.isfinite(float(atr)) and atr > 0:
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

        rounded_stop = round(max(0.0, stop_price), 2)
        if entry_price > 0.01:
            rounded_stop = min(rounded_stop, round(entry_price - 0.01, 2))
        return max(0.0, rounded_stop)

    def update_trailing_stop(self, position: Position) -> bool:
        """
        Atualiza stop-loss com trailing stop quando a posição supera o gatilho de lucro.
        Garante que `new_stop` fique estritamente ao menos R$ 0,01 abaixo de `current_price`
        para jamais disparar auto-stop imediato em penny stocks (`[RSK-02]`).
        """
        if (
            position.current_price is None
            or not math.isfinite(float(position.current_price))
            or position.current_price <= 0
            or not math.isfinite(float(position.entry_price))
            or position.entry_price <= 0
        ):
            return False

        gain_pct = (position.current_price - position.entry_price) / position.entry_price

        if gain_pct < self.TRAILING_STOP_ACTIVATION:
            return False

        raw_stop = math.floor(
            position.current_price * (1.0 - self.trailing_stop_distance) * 100.0 + 1e-9
        ) / 100.0
        new_stop = round(min(raw_stop, round(position.current_price - 0.01, 2)), 2)
        if new_stop <= 0:
            return False

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

    @staticmethod
    def _normalize_reset_date(val: Any) -> Optional[date]:
        """Converte `date`, `datetime` ou string ISO em `date` BRT."""
        if val is None:
            return None
        if isinstance(val, datetime):
            return val.astimezone(BRT).date() if val.tzinfo else val.date()
        if isinstance(val, date):
            return val
        if isinstance(val, str):
            try:
                return date.fromisoformat(val.strip()[:10])
            except ValueError:
                return None
        return None

    def restore_daily_state(
        self,
        daily_pnl: float,
        circuit_breaker_active: bool,
        reset_date: Any = None,
    ) -> None:
        """Restaura estado diário de P&L e Circuit Breaker ao alternar entre ambientes."""
        self._daily_pnl = _finite_float(daily_pnl, 0.0)
        self._circuit_breaker_active = bool(circuit_breaker_active)
        self._last_reset_date = self._normalize_reset_date(reset_date) or datetime.now(BRT).date()

    def update_daily_pnl(self, pnl_percent: float) -> None:
        """Atualiza o P&L diário e verifica o circuit breaker."""
        today = datetime.now(BRT).date()
        if self._normalize_reset_date(self._last_reset_date) != today:
            self._daily_pnl = 0.0
            self._circuit_breaker_active = False
            self._last_reset_date = today

        safe_pnl = _finite_float(pnl_percent, 0.0)
        self._daily_pnl = safe_pnl

        if safe_pnl <= self.DAILY_LOSS_LIMIT and not self._circuit_breaker_active:
            self._circuit_breaker_active = True
            logger.warning(
                '🛑 CIRCUIT BREAKER ATIVADO — Perda diária de %.1f%% '
                'excede limite de %.1f%%. Operações suspensas até amanhã.',
                safe_pnl * 100,
                self.DAILY_LOSS_LIMIT * 100,
            )

    def reset_daily_limits(self) -> None:
        """Reseta limites diários (chamado no início de cada dia ou troca de ambiente)."""
        self._daily_pnl = 0.0
        self._circuit_breaker_active = False
        self._last_reset_date = datetime.now(BRT).date()
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
            cand_sl = _finite_float(stop_loss_percent, self.stop_loss_percent)
            self.stop_loss_percent = max(0.01, min(0.50, cand_sl))
        if trailing_trigger_percent is not None:
            cand_trig = _finite_float(trailing_trigger_percent, self.trailing_stop_activation)
            self.trailing_stop_activation = max(0.005, min(0.30, cand_trig))
            self.TRAILING_STOP_ACTIVATION = self.trailing_stop_activation
        if trailing_distance_percent is not None:
            cand_dist = _finite_float(trailing_distance_percent, self.trailing_stop_distance)
            self.trailing_stop_distance = max(0.005, min(0.20, cand_dist))

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
        exclude_same_day_profit: bool = False,
        simulation_mode: bool | None = None,
    ) -> list[dict[str, Any]]:
        """
        Seleciona posições priorizando posições `D+1+` (quando `AVOID_DAY_TRADE_TAX=True`),
        maior ganho percentual e menor volume necessário para levantar `target_amount`,
        descontando emolumentos B3 e respeitando lotes padrão (`100x`) vs fracionários em Produção.
        """
        if not math.isfinite(float(target_amount)) or target_amount <= 0 or not positions:
            return []

        is_sim = bool(
            settings.SIMULATION_MODE if simulation_mode is None else simulation_mode
        )
        fee_rate = (
            max(0.0, _finite_float(getattr(settings, "B3_FEE_RATE", 0.0003), 0.0003))
            if not is_sim
            else 0.0
        )
        avoid_dt = bool(getattr(settings, "AVOID_DAY_TRADE_TAX", True))
        today_brt = datetime.now(BRT).date()

        scored_positions: list[tuple[int, float, float, Position]] = []
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
            if not math.isfinite(price) or price <= 0 or pos.quantity <= 0:
                continue

            gain_pct = (price - pos.entry_price) / pos.entry_price if pos.entry_price > 0 else 0.0
            entry_dt = getattr(pos, 'entry_time', None) or getattr(pos, 'timestamp', None)
            if isinstance(entry_dt, datetime):
                pos_date = entry_dt.astimezone(BRT).date() if entry_dt.tzinfo else entry_dt.date()
                is_same_day = (pos_date == today_brt)
            else:
                is_same_day = False

            if exclude_same_day_profit and avoid_dt and is_same_day and gain_pct > 0:
                continue

            day_trade_bucket = 1 if (avoid_dt and is_same_day) else 0
            scored_positions.append((day_trade_bucket, gain_pct, price, pos))

        scored_positions.sort(key=lambda item: (item[0], -item[1]))

        plan: list[dict[str, Any]] = []
        remaining_needed = float(target_amount)

        for _, gain_pct, price, pos in scored_positions:
            if remaining_needed <= 0.01:
                break

            net_unit_price = price * (1.0 - fee_rate) if fee_rate > 0 else price
            if net_unit_price <= 0:
                continue

            val_total_pos = net_unit_price * pos.quantity
            if val_total_pos <= remaining_needed:
                qty_to_sell = pos.quantity
            else:
                raw_qty = min(
                    pos.quantity,
                    max(1, math.ceil((remaining_needed - 1e-9) / net_unit_price)),
                )
                if (
                    not is_sim
                    and not is_b3_unit_lot_asset(pos.ticker)
                    and pos.quantity >= 100
                    and (raw_qty % 100) > (pos.quantity % 100)
                ):
                    qty_to_sell = min(pos.quantity, ((raw_qty // 100) + 1) * 100)
                else:
                    qty_to_sell = raw_qty

            est_value = qty_to_sell * net_unit_price
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
