"""
Gerenciamento de portfólio e capital operacional do Projeto Córtex.

Controla posições abertas, caixa livre, teto operacional da IA,
Cofre Reserva protegido, auto-compounding e cálculo de P&L de forma
thread-safe.
"""

from __future__ import annotations

from dataclasses import replace
import logging
import math
import threading
from typing import Any, Optional

from config.settings import settings
from models.data_models import PortfolioSummary, Position
from utils.helpers import normalize_b3_ticker

logger = logging.getLogger('cortex.portfolio')


class Portfolio:
    """Carteira thread-safe com suporte a teto operacional da IA e Cofre Reserva."""

    def __init__(
        self,
        initial_capital: float | None = None,
        is_simulated: bool | None = None,
    ) -> None:
        resolved_capital = float(
            settings.CAPITAL_INICIAL if initial_capital is None else initial_capital
        )
        if not math.isfinite(resolved_capital) or resolved_capital < 0:
            resolved_capital = 0.0
        self._lock = threading.RLock()
        self._is_simulated: bool | None = is_simulated
        self._manual_capital_delta_accum: float = 0.0
        self.initial_capital: float = resolved_capital
        self.free_cash: float = resolved_capital
        self.ai_allocated_capital: float = resolved_capital
        self.vault_reserve_cash: float = 0.0
        self.cumulative_realized_pnl: float = 0.0
        self._positions: list[Position] = []
        logger.info('Portfolio inicializado — capital: R$ %.2f', resolved_capital)

    def set_free_cash(self, amount: float) -> None:
        """Define o caixa livre de forma thread-safe truncando valores inválidos/negativos."""
        with self._lock:
            try:
                val = float(amount)
            except (TypeError, ValueError):
                val = 0.0
            self.free_cash = max(0.0, val) if math.isfinite(val) else 0.0

    @staticmethod
    def _normalize_ticker(ticker: str) -> str:
        return normalize_b3_ticker(ticker)

    @property
    def positions(self) -> list[Position]:
        """Retorna cópias rasas das posições abertas para leitura concorrente segura."""
        with self._lock:
            return [replace(p) for p in self._positions]

    @property
    def allocated_capital(self) -> float:
        """Custo total alocado nas posições abertas."""
        with self._lock:
            return sum(p.total_cost for p in self._positions)

    @property
    def positions_market_value(self) -> float:
        """Valor de mercado atual das posições abertas."""
        with self._lock:
            return sum(p.current_value for p in self._positions)

    @property
    def total_value(self) -> float:
        """Patrimônio total da conta (caixa livre + valor atual das posições)."""
        with self._lock:
            positions_value = sum(p.current_value for p in self._positions)
            return self.free_cash + positions_value

    @property
    def operational_value(self) -> float:
        """Patrimônio operacional da IA (excluindo saldo blindado no Cofre Reserva)."""
        with self._lock:
            return max(0.0, self.total_value - self.vault_reserve_cash)

    @property
    def total_pnl(self) -> float:
        """
        Lucro/prejuízo líquido operacional.

        Em Homologação (`vault_reserve_cash == 0`), equivale a `total_value - initial_capital`.
        Quando há Cofre Reserva (`vault_reserve_cash > 0`), compara o patrimônio operacional
        (`total_value - vault_reserve_cash`) contra a linha de base operacional (`initial_capital`)
        para evitar distorção artificial de P&L pelo saldo parado no cofre.
        """
        with self._lock:
            return self.operational_value - self.initial_capital

    @property
    def total_pnl_percent(self) -> float:
        """Retorno percentual operacional sobre a linha de base inicial."""
        with self._lock:
            if self.initial_capital <= 0:
                return 0.0
            return (self.total_pnl / self.initial_capital) * 100.0

    @property
    def buying_power(self) -> float:
        """
        Poder de compra efetivo disponível para novas entradas da IA.

        Retorna 0.0 se o teto operacional da IA for <= 0. Caso contrário,
        respeita simultaneamente o caixa livre fora do cofre e o saldo
        remanescente do teto operacional descontando posições já abertas.
        """
        with self._lock:
            if self.ai_allocated_capital <= 0:
                return 0.0
            usable_cash = max(0.0, self.free_cash - self.vault_reserve_cash)
            remaining_ceiling = max(0.0, self.ai_allocated_capital - self.allocated_capital)
            return min(usable_cash, remaining_ceiling)

    def add_position(self, position: Position, fee: float = 0.0) -> None:
        """Adiciona ou mescla uma posição na carteira de forma atômica."""
        if position.quantity <= 0 or position.entry_price <= 0:
            logger.warning(
                'Tentativa de adicionar posição inválida ignorada: %s (qty=%s, price=%s)',
                position.ticker, position.quantity, position.entry_price,
            )
            return

        resolved_fee = max(0.0, float(fee)) if (fee is not None and math.isfinite(float(fee))) else 0.0
        norm = self._normalize_ticker(position.ticker)
        cost = position.quantity * position.entry_price

        with self._lock:
            for pos in self._positions:
                if self._normalize_ticker(pos.ticker) == norm:
                    total_qty = pos.quantity + position.quantity
                    pos.entry_price = (
                        (pos.quantity * pos.entry_price) + cost
                    ) / total_qty
                    pos.quantity = total_qty
                    if position.current_price and position.current_price > 0:
                        pos.current_price = position.current_price
                    if position.stop_loss and position.stop_loss > 0:
                        pos.stop_loss = max(pos.stop_loss, position.stop_loss)
                    self.free_cash -= (cost + resolved_fee)
                    if resolved_fee > 0:
                        self.cumulative_realized_pnl -= resolved_fee
                        self.ai_allocated_capital = max(0.0, self.ai_allocated_capital - resolved_fee)
                    if self.free_cash < -1e-6:
                        logger.warning(
                            'Caixa livre ficou negativo ao mesclar %s (R$ %.2f) — truncando para R$ 0.00',
                            pos.ticker, self.free_cash,
                        )
                    self.free_cash = max(0.0, self.free_cash)
                    logger.info(
                        'Posição mesclada: %s — agora %d ações @ R$ %.2f (SL: R$ %.2f)',
                        pos.ticker, pos.quantity, pos.entry_price, pos.stop_loss,
                    )
                    return

            new_pos = replace(position)
            self._positions.append(new_pos)
            self.free_cash -= (cost + resolved_fee)
            if resolved_fee > 0:
                self.cumulative_realized_pnl -= resolved_fee
                self.ai_allocated_capital = max(0.0, self.ai_allocated_capital - resolved_fee)
            if self.free_cash < -1e-6:
                logger.warning(
                    'Caixa livre ficou negativo ao adicionar %s (R$ %.2f) — truncando para R$ 0.00',
                    new_pos.ticker, self.free_cash,
                )
            self.free_cash = max(0.0, self.free_cash)

        logger.info(
            'Posição adicionada: %s — %d ações @ R$ %.2f',
            position.ticker, position.quantity, position.entry_price,
        )

    def update_position_stop(
        self,
        ticker: str,
        new_sl: float,
        partial_exit_done: Optional[bool] = None,
    ) -> Optional[Position]:
        """Atualiza com segurança sob lock o stop_loss e/ou partial_exit_done de uma posição."""
        norm = self._normalize_ticker(ticker)
        with self._lock:
            for pos in self._positions:
                if self._normalize_ticker(pos.ticker) == norm:
                    if new_sl > 0:
                        pos.stop_loss = round(float(new_sl), 2)
                    if partial_exit_done is not None:
                        pos.partial_exit_done = bool(partial_exit_done)
                    return replace(pos)
        return None

    def remove_position(self, ticker: str, sell_price: float, fee: float = 0.0) -> Optional[Position]:
        """Remove integralmente uma posição e incorpora o resultado realizado (auto-compounding)."""
        resolved_fee = max(0.0, float(fee)) if (fee is not None and math.isfinite(float(fee))) else 0.0
        norm = self._normalize_ticker(ticker)
        with self._lock:
            for i, pos in enumerate(self._positions):
                if self._normalize_ticker(pos.ticker) == norm:
                    removed = self._positions.pop(i)
                    revenue = (sell_price * removed.quantity) - resolved_fee
                    pnl = ((sell_price - removed.entry_price) * removed.quantity) - resolved_fee
                    self.free_cash = max(0.0, self.free_cash + revenue)
                    self.cumulative_realized_pnl += pnl
                    self.ai_allocated_capital = max(0.0, self.ai_allocated_capital + pnl)
                    logger.info(
                        'Posição removida: %s — R$ %.2f → R$ %.2f | P&L: R$ %.2f | Nova base IA: R$ %.2f',
                        ticker, removed.entry_price, sell_price, pnl, self.ai_allocated_capital,
                    )
                    return replace(removed)
        return None

    def reduce_position(
        self,
        ticker: str,
        sell_price: float,
        quantity_to_sell: int,
        fee: float = 0.0,
    ) -> Optional[Position]:
        """Reduz parcialmente uma posição e incorpora o lucro/prejuízo proporcional."""
        resolved_fee = max(0.0, float(fee)) if (fee is not None and math.isfinite(float(fee))) else 0.0
        norm = self._normalize_ticker(ticker)
        with self._lock:
            if quantity_to_sell <= 0:
                return None
            for pos in self._positions:
                if self._normalize_ticker(pos.ticker) == norm:
                    if quantity_to_sell >= pos.quantity:
                        return self.remove_position(ticker, sell_price, fee=resolved_fee)

                    pos.quantity -= quantity_to_sell
                    revenue = (sell_price * quantity_to_sell) - resolved_fee
                    pnl = ((sell_price - pos.entry_price) * quantity_to_sell) - resolved_fee
                    self.free_cash = max(0.0, self.free_cash + revenue)
                    self.cumulative_realized_pnl += pnl
                    self.ai_allocated_capital = max(0.0, self.ai_allocated_capital + pnl)

                    logger.info(
                        'Posição reduzida: %s — Vendidas %d ações @ R$ %.2f | P&L: R$ %.2f | Base IA: R$ %.2f',
                        ticker, quantity_to_sell, sell_price, pnl, self.ai_allocated_capital,
                    )
                    return replace(pos)
        return None

    def _rebalance_baseline_after_capital_change(
        self,
        prev_op_eq: float,
        prev_initial: float,
        track_manual_delta: bool = True,
    ) -> None:
        """
        Ajusta `initial_capital` e `cumulative_realized_pnl` proporcionalmente em saques/reduções
        para impedir colapso de `initial_capital` a `0.0` quando `operational_value > 0`, mantendo
        `total_pnl == operational_value - initial_capital`.
        """
        unrealized_pnl = sum(p.pnl for p in self._positions)
        current_pnl = self.cumulative_realized_pnl + unrealized_pnl
        operational_eq = max(0.0, self.total_value - self.vault_reserve_cash)

        if track_manual_delta:
            self._manual_capital_delta_accum += (operational_eq - prev_op_eq)

        if operational_eq <= 0.0:
            self.initial_capital = 0.0
            self.cumulative_realized_pnl = -unrealized_pnl
            return

        if operational_eq < prev_op_eq - 1e-9 and prev_op_eq > 0.0:
            eff_prev_initial = prev_initial if prev_initial > 0.0 else max(0.01, prev_op_eq - current_pnl)
            if eff_prev_initial <= 0.0:
                eff_prev_initial = prev_op_eq
            retain_ratio = operational_eq / prev_op_eq
            new_initial = max(0.01, eff_prev_initial * retain_ratio)
            self.initial_capital = new_initial
            target_pnl = operational_eq - new_initial
            self.cumulative_realized_pnl = target_pnl - unrealized_pnl
        else:
            candidate_initial = operational_eq - current_pnl
            if candidate_initial <= 0.0:
                fallback_base = prev_initial if prev_initial > 0.0 else operational_eq
                self.initial_capital = max(0.01, min(operational_eq, fallback_base))
                self.cumulative_realized_pnl = (operational_eq - self.initial_capital) - unrealized_pnl
            else:
                self.initial_capital = candidate_initial

    def adjust_ai_capital(self, new_ai_capital: float) -> None:
        """Ajusta o teto operacional da IA e recalcula o Cofre Reserva mantendo o P&L real."""
        with self._lock:
            prev_op_eq = max(0.0, self.total_value - self.vault_reserve_cash)
            prev_initial = self.initial_capital
            self.ai_allocated_capital = max(0.0, float(new_ai_capital))
            cost_basis_equity = self.free_cash + self.allocated_capital
            self.vault_reserve_cash = min(
                self.free_cash,
                max(0.0, cost_basis_equity - self.ai_allocated_capital),
            )
            self._rebalance_baseline_after_capital_change(prev_op_eq, prev_initial, track_manual_delta=True)

    def deposit_to_vault(self, amount: float) -> float:
        """Transfere caixa livre desbloqueado para o Cofre Reserva protegido."""
        with self._lock:
            prev_op_eq = max(0.0, self.total_value - self.vault_reserve_cash)
            prev_initial = self.initial_capital
            avail = max(0.0, self.free_cash - self.vault_reserve_cash)
            transfer_amount = min(avail, max(0.0, float(amount)))
            self.vault_reserve_cash += transfer_amount
            self.ai_allocated_capital = max(0.0, self.ai_allocated_capital - transfer_amount)
            self._rebalance_baseline_after_capital_change(prev_op_eq, prev_initial, track_manual_delta=True)
            return transfer_amount

    def release_from_vault(self, amount: float) -> float:
        """Libera recursos do Cofre Reserva de volta para o teto operacional da IA."""
        with self._lock:
            prev_op_eq = max(0.0, self.total_value - self.vault_reserve_cash)
            prev_initial = self.initial_capital
            release_amount = min(self.vault_reserve_cash, max(0.0, float(amount)))
            self.vault_reserve_cash -= release_amount
            self.ai_allocated_capital += release_amount
            self._rebalance_baseline_after_capital_change(prev_op_eq, prev_initial, track_manual_delta=True)
            return release_amount

    def update_prices(self, prices: dict[str, Optional[float]]) -> None:
        """Atualiza cotações correntes das posições usando normalização de ticker B3."""
        if not prices:
            return
        normalized_map: dict[str, float] = {}
        for k, v in prices.items():
            if v is not None and v > 0:
                normalized_map[self._normalize_ticker(k)] = float(v)

        with self._lock:
            for pos in self._positions:
                norm = self._normalize_ticker(pos.ticker)
                price = prices.get(pos.ticker)
                if price is None or price <= 0:
                    price = normalized_map.get(norm)
                if price is not None and price > 0:
                    pos.current_price = float(price)

    def get_summary(self) -> PortfolioSummary:
        """Retorna snapshot consolidado da carteira."""
        with self._lock:
            sim_mode = bool(
                self._is_simulated if self._is_simulated is not None else settings.SIMULATION_MODE
            )
            return PortfolioSummary(
                total_value=self.total_value,
                free_cash=self.free_cash,
                allocated_capital=self.allocated_capital,
                positions=[replace(p) for p in self._positions],
                total_pnl=self.total_pnl,
                total_pnl_percent=self.total_pnl_percent,
                num_positions=len(self._positions),
                simulation_mode=sim_mode,
            )

    def get_capital_summary(self) -> dict[str, Any]:
        """Retorna resumo detalhado de alocação de capital e Cofre Reserva."""
        with self._lock:
            return {
                "total_equity": round(self.total_value, 2),
                "operational_equity": round(self.operational_value, 2),
                "ai_allocated_capital": round(self.ai_allocated_capital, 2),
                "vault_reserve_cash": round(self.vault_reserve_cash, 2),
                "free_cash": round(self.free_cash, 2),
                "allocated_capital": round(self.allocated_capital, 2),
                "buying_power": round(self.buying_power, 2),
                "cumulative_realized_pnl": round(self.cumulative_realized_pnl, 2),
                "positions_count": len(self._positions),
            }

    def find_position(self, ticker: str) -> Optional[Position]:
        """Busca posição aberta por ticker normalizado retornando cópia segura."""
        norm = self._normalize_ticker(ticker)
        if not norm:
            return None
        with self._lock:
            for pos in self._positions:
                if self._normalize_ticker(pos.ticker) == norm:
                    return replace(pos)
        return None

    def get_position(self, ticker: str) -> Optional[Position]:
        """Alias para find_position."""
        return self.find_position(ticker)

    def get_all_positions(self) -> list[Position]:
        """Retorna cópias rasas de todas as posições abertas."""
        return self.positions
