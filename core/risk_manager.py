"""
Gerenciador de risco do Projeto Córtex.

Responsável por cálculo de stop-loss, validação de ordens,
dimensionamento de posições e verificação de gatilhos de risco.
Este é o componente de MAIOR PRIORIDADE — executa antes de qualquer análise.

Funcionalidades:
- Stop-loss fixo na entrada
- Trailing stop-loss (move SL para cima quando posição lucra > 5%)
- Limite de concentração (máx 30% do portfólio por ativo)
- Circuit breaker de perda diária (suspende operações se P&L < -5%)
- Dimensionamento por confiança (alta confiança = mais ações)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from config.settings import settings
from models.data_models import BRT, Position

logger = logging.getLogger('cortex.risk_manager')

class RiskManager:
    """Gerenciador de risco para operações de trading da B3."""

    def __init__(
        self,
        stop_loss_percent: float = settings.STOP_LOSS_PERCENT,
        max_positions: int = getattr(settings, 'MAX_POSITIONS', 0),
        daily_loss_limit: float = getattr(settings, 'MAX_DAILY_LOSS_PERCENT', 0.03),
        min_shares: int = getattr(settings, 'min_quantity', 1),
        max_shares: int = getattr(settings, 'max_quantity', 50000),
        max_concentration: float = getattr(settings, 'MAX_CONCENTRATION', 0.25),
    ) -> None:
        """
        Inicializa o gerenciador de risco.

        Args:
            stop_loss_percent: Percentual de perda máxima tolerada (0.10 = 10%).
            max_positions: Limite de posições simultâneas (0 = ilimitado).
            daily_loss_limit: Limite de drawdown diário (0.03 = 3%).
            min_shares: Quantidade mínima por ordem.
            max_shares: Quantidade máxima por ordem.
            max_concentration: Concentração máxima por ativo (0.25 = 25%).
        """
        self.stop_loss_percent = stop_loss_percent
        self.max_positions = max_positions
        self.daily_loss_limit = -abs(daily_loss_limit)
        self.min_shares = min_shares
        self.max_shares = max_shares
        self.max_concentration = max_concentration
        self.trailing_stop_activation = getattr(settings, 'TRAILING_STOP_TRIGGER_PERCENT', 0.02)
        self.trailing_stop_distance = getattr(settings, 'TRAILING_STOP_DISTANCE_PERCENT', 0.015)

        self.MIN_SHARES = self.min_shares
        self.MAX_SHARES = self.max_shares
        self.MAX_CONCENTRATION = self.max_concentration
        self.DAILY_LOSS_LIMIT = self.daily_loss_limit
        self.TRAILING_STOP_ACTIVATION = self.trailing_stop_activation

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
        Verifica se alguma posição atingiu o alvo de lucro parcial (15%).
        Retorna posições que devem ter 50% do volume vendido.
        """
        triggered: list[Position] = []
        TAKE_PROFIT_ACTIVATION = 0.15

        for position in positions:
            if getattr(position, 'partial_exit_done', False):
                continue
            
            if position.current_price is None or position.current_price <= 0:
                continue

            gain_pct = (position.current_price - position.entry_price) / position.entry_price
            if gain_pct >= TAKE_PROFIT_ACTIVATION:
                triggered.append(position)
                logger.info(
                    '🎯 TAKE-PROFIT PARCIAL ATINGIDO: %s — lucro de +%.1f%%',
                    position.ticker, gain_pct * 100
                )

        return triggered

    def calculate_stop_loss(self, entry_price: float, atr: float | None = None) -> float:
        """
        Calcula o preço de stop-loss adaptativo.

        P_stop = max(
            entry_price - 1.5 * ATR (se disponível),
            entry_price * (1 - settings.STOP_LOSS_PERCENT)
        )

        Args:
            entry_price: Preço de entrada da posição.
            atr: Average True Range (opcional).

        Returns:
            Preço de stop-loss calculado.
        """
        fixed_stop = entry_price * (1.0 - self.stop_loss_percent)
        
        if atr and atr > 0:
            atr_stop = entry_price - (1.5 * atr)
            stop_price = max(atr_stop, fixed_stop)
            logger.debug(
                'Stop-loss adaptativo calculado: entrada R$ %.2f, ATR %.2f → stop R$ %.2f',
                entry_price, atr, stop_price
            )
        else:
            stop_price = fixed_stop
            logger.debug(
                'Stop-loss fixo calculado: entrada R$ %.2f → stop R$ %.2f (%.1f%%)',
                entry_price, stop_price, self.stop_loss_percent * 100,
            )
        return stop_price

    def update_trailing_stop(self, position: Position) -> bool:
        """
        Atualiza stop-loss com trailing stop quando posição tem lucro.

        Quando o preço atual sobe mais de TRAILING_STOP_ACTIVATION (5%) acima
        da entrada, o stop-loss é movido para cima para proteger lucros.

        O novo stop = max(stop_atual, preço_atual * (1 - stop_loss_percent))

        Args:
            position: Posição a avaliar.

        Returns:
            True se o stop-loss foi atualizado, False caso contrário.
        """
        if position.current_price is None or position.current_price <= 0:
            return False

        gain_pct = (position.current_price - position.entry_price) / position.entry_price

        if gain_pct < self.TRAILING_STOP_ACTIVATION:
            return False

        new_stop = position.current_price * (1.0 - self.stop_loss_percent)

        if new_stop > position.stop_loss:
            old_stop = position.stop_loss
            position.stop_loss = round(new_stop, 2)
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
        Verifica gatilhos de stop-loss para todas as posições.

        Esta é a verificação de MAIOR PRIORIDADE — executa antes de
        qualquer análise técnica ou de sentimento.

        Para cada posição, obtém o preço atual. Se o preço atual
        for menor ou igual ao stop-loss, a posição é adicionada
        à lista de gatilhos ativados. Também atualiza trailing stops.

        Args:
            positions: Lista de posições abertas.
            market_data: Instância do provedor de dados de mercado.

        Returns:
            Lista de posições cujo stop-loss foi ativado.
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

                if current_price is None:
                    current_price = position.current_price

                if current_price is None or current_price <= 0:
                    logger.warning(
                        'Preço indisponível para %s — não é possível verificar stop-loss',
                        position.ticker,
                    )
                    continue

                position.current_price = current_price

                self.update_trailing_stop(position)

                if current_price <= position.stop_loss:
                    triggered.append(position)
                    logger.warning(
                        '🚨 STOP-LOSS ATIVADO: %s — preço R$ %.2f <= SL R$ %.2f '
                        '(entrada R$ %.2f, perda %.1f%%)',
                        position.ticker,
                        current_price,
                        position.stop_loss,
                        position.entry_price,
                        ((current_price - position.entry_price) / position.entry_price) * 100,
                    )
                else:
                    distance_pct = (
                        (current_price - position.stop_loss) / position.stop_loss
                    ) * 100
                    logger.debug(
                        'Stop-loss OK: %s — preço R$ %.2f, SL R$ %.2f (distância: +%.1f%%)',
                        position.ticker,
                        current_price,
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
        Valida uma ordem antes da execução.

        Verificações realizadas:
        1. Circuit breaker não está ativo.
        2. Quantidade dentro do intervalo [1, 99] (mercado fracionário).
        3. Custo total (price * quantity) dentro do capital disponível.
        4. Concentração máxima por ativo não excede MAX_CONCENTRATION.
        5. Concentração setorial não excede settings.MAX_SECTOR_EXPOSURE.

        Args:
            ticker: Código do ativo.
            quantity: Quantidade de ações.
            price: Preço unitário.
            available_capital: Capital disponível para a operação.
            total_portfolio_value: Valor total do portfólio (para concentração).
            positions: Lista de posições atuais do portfólio (para concentração setorial).

        Returns:
            Tupla (is_valid, reason_if_invalid).
            Se válida: (True, 'Ordem válida').
            Se inválida: (False, 'motivo da rejeição').
        """
        if self._circuit_breaker_active:
            reason = (
                f'Circuit breaker ativo — operações suspensas '
                f'(perda diária: {self._daily_pnl*100:.1f}%)'
            )
            logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
            return False, reason

        if self.max_positions > 0 and positions is not None:
            is_new = not any(p.ticker.rstrip('Ff') == ticker.rstrip('Ff') for p in positions)
            if is_new and len(positions) >= self.max_positions:
                reason = f'Limite máximo de {self.max_positions} posições simultâneas atingido'
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
                f'(máximo para mercado fracionário)'
            )
            logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
            return False, reason

        total_cost = price * quantity
        if total_cost > available_capital:
            reason = (
                f'Capital insuficiente: R$ {total_cost:.2f} necessário, '
                f'R$ {available_capital:.2f} disponível'
            )
            logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
            return False, reason

        if total_portfolio_value > 0:
            concentration = total_cost / total_portfolio_value
            if concentration > self.MAX_CONCENTRATION:
                reason = (
                    f'Concentração excessiva: {concentration*100:.1f}% > '
                    f'{self.MAX_CONCENTRATION*100:.0f}% máximo permitido'
                )
                logger.warning('Ordem rejeitada para %s: %s', ticker, reason)
                return False, reason

            if positions is not None:
                sector = settings.SECTOR_MAP.get(ticker, 'Desconhecido')
                sector_exposure = total_cost
                for p in positions:
                    if settings.SECTOR_MAP.get(p.ticker, 'Desconhecido') == sector:
                        sector_exposure += (p.current_value or (p.quantity * p.entry_price))
                
                sector_concentration = sector_exposure / total_portfolio_value
                if sector_concentration > settings.MAX_SECTOR_EXPOSURE:
                    reason = (
                        f'Concentração setorial excessiva ({sector}): {sector_concentration*100:.1f}% > '
                        f'{settings.MAX_SECTOR_EXPOSURE*100:.0f}% máximo permitido'
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
        Calcula a quantidade máxima de ações que podem ser compradas.

        Leva em conta:
        - Capital disponível
        - Concentração máxima por ativo
        - Concentração setorial máxima
        - Confiança da decisão (modula tamanho: alta confiança = mais ações)
        - Limite do mercado fracionário (1-99)

        Args:
            price: Preço unitário da ação.
            available_capital: Capital disponível.
            confidence: Nível de confiança da decisão (0.0 a 1.0).
            total_portfolio_value: Valor total do portfólio.
            positions: Posições atuais para cálculo de setor.
            ticker: Ticker alvo (necessário se positions for passado).

        Returns:
            Quantidade máxima de ações (0 se insuficiente, máx 99).
        """
        if price <= 0 or available_capital <= 0:
            logger.debug(
                'Cálculo de max_shares: preço=%.2f, capital=%.2f → 0',
                price, available_capital,
            )
            return 0

        max_by_capital = int(available_capital / price)

        if total_portfolio_value > 0:
            max_by_concentration = int(
                (total_portfolio_value * self.MAX_CONCENTRATION) / price
            )
            
            max_by_sector = max_by_capital
            if positions is not None and ticker:
                sector = settings.SECTOR_MAP.get(ticker, 'Desconhecido')
                current_sector_exposure = 0.0
                for p in positions:
                    if settings.SECTOR_MAP.get(p.ticker, 'Desconhecido') == sector:
                        current_sector_exposure += (p.current_value or (p.quantity * p.entry_price))
                
                allowed_sector_capital = (settings.MAX_SECTOR_EXPOSURE * total_portfolio_value) - current_sector_exposure
                max_by_sector = int(max(0, allowed_sector_capital) / price)
                
            max_shares = min(max_by_capital, max_by_concentration, max_by_sector)
        else:
            max_shares = max_by_capital

        if confidence < 0.5:
            confidence_factor = 0.5
        elif confidence < 0.7:
            confidence_factor = 0.7
        else:
            confidence_factor = 1.0

        max_shares = int(max_shares * confidence_factor)

        result = max(0, min(max_shares, self.MAX_SHARES))

        logger.debug(
            'Max shares: preço R$ %.2f, capital R$ %.2f, confiança %.0f%% → %d ações '
            '(custo total: R$ %.2f)',
            price, available_capital, confidence * 100, result, price * result,
        )
        return result

    def update_daily_pnl(self, pnl_percent: float) -> None:
        """
        Atualiza o P&L diário e verifica circuit breaker.

        Args:
            pnl_percent: Variação percentual do portfólio no dia (ex: -0.03 = -3%).
        """
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
        """Reseta limites diários (chamado no início de cada dia)."""
        self._daily_pnl = 0.0
        self._circuit_breaker_active = False
        self._last_reset_date = datetime.now(BRT).strftime('%Y-%m-%d')
        logger.debug('Limites diários de risco resetados')

    @property
    def is_circuit_breaker_active(self) -> bool:
        """Retorna se o circuit breaker está ativo."""
        return self._circuit_breaker_active
