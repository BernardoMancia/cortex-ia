"""
Simulador de corretora (paper trading) do Projeto Córtex.

Implementa a interface BrokerBase mantendo estado interno em memória
com persistência atômica em JSON para sobreviver a reinicializações. Totalmente
thread-safe e aderente às regras de lotes da B3.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
from typing import Any, Optional
from zoneinfo import ZoneInfo

from broker.base import BrokerBase, Order, OrderStatus, OrderType, Position
from config.settings import settings
from utils.helpers import format_b3_ticker_for_quantity, normalize_b3_ticker

logger = logging.getLogger("cortex.broker.simulator")

BRT: ZoneInfo = ZoneInfo("America/Sao_Paulo")


class SimulatorBroker(BrokerBase):
    """
    Corretora simulada para paper trading.

    Mantém saldo e posições em memória com persistência atômica em JSON.
    Respeita regras de símbolos da B3 (ETFs/BDRs unitários, lotes padrão e fracionários).
    Thread-safe via threading.Lock sem reter lock durante I/O de rede.
    """

    def __init__(
        self,
        initial_balance: float | None = None,
        state_path: Path | str | None = None,
    ) -> None:
        """
        Inicializa o simulador resolvendo defaults em tempo de execução.

        Args:
            initial_balance: Capital inicial em R$ (default: settings.CAPITAL_INICIAL).
            state_path: Caminho do arquivo JSON de persistência (default: settings.simulator_state_path).
        """
        resolved_balance = (
            float(settings.CAPITAL_INICIAL)
            if initial_balance is None
            else float(initial_balance)
        )
        resolved_path = (
            Path(state_path)
            if state_path is not None
            else Path(settings.simulator_state_path)
        )

        self._initial_balance: float = resolved_balance
        self._balance: float = resolved_balance
        self._positions: dict[str, dict[str, Any]] = {}
        self._next_ticket: int = 1
        self._connected: bool = False
        self._lock: threading.Lock = threading.Lock()
        self._state_path: Path = resolved_path
        self._market_data: Any = None

        logger.info(
            "SimulatorBroker criado — capital inicial: R$%.2f, state: %s",
            self._initial_balance,
            self._state_path,
        )

    def _get_market_data(self) -> Any:
        """Retorna instância de MarketData (lazy import para evitar ciclo)."""
        if self._market_data is None:
            from data.market_data import MarketData

            self._market_data = MarketData()
        return self._market_data

    @staticmethod
    def _ensure_fractional_suffix(ticker: str, quantity: int = 1) -> str:
        """Formata símbolo B3 adequado para a quantidade informada."""
        return format_b3_ticker_for_quantity(ticker, quantity=quantity)

    def _find_position_key(self, ticker: str) -> str | None:
        """Localiza a chave de uma posição aberta pelo ticker-base normalizado (chamado sob lock)."""
        raw_upper = ticker.strip().upper()
        if raw_upper in self._positions:
            return raw_upper
        norm = normalize_b3_ticker(ticker)
        if not norm:
            return None
        for key in self._positions:
            if normalize_b3_ticker(key) == norm:
                return key
        return None

    @staticmethod
    def _validate_quantity(quantity: int) -> None:
        """Valida que a quantidade esteja dentro do limite operacional permitido."""
        min_qty = getattr(settings, "min_quantity", 1)
        max_qty = getattr(settings, "max_quantity", 50000)
        if not isinstance(quantity, int) or quantity < min_qty or quantity > max_qty:
            raise ValueError(
                f"Quantidade inválida: {quantity}. "
                f"Permitido de {min_qty} a {max_qty} ações."
            )

    def _generate_ticket(self) -> int:
        """Gera próximo número de ticket sequencial (chamado sob lock)."""
        ticket = self._next_ticket
        self._next_ticket += 1
        return ticket

    def _save_state(self) -> None:
        """Persiste o estado atual atomicamente em arquivo JSON (chamado sob lock)."""
        state: dict[str, Any] = {
            "balance": round(self._balance, 2),
            "initial_balance": round(self._initial_balance, 2),
            "next_ticket": self._next_ticket,
            "positions": {},
        }
        for ticker, pos_data in self._positions.items():
            state["positions"][ticker] = {
                "ticker": pos_data["ticker"],
                "quantity": int(pos_data["quantity"]),
                "entry_price": float(pos_data["entry_price"]),
                "stop_loss": float(pos_data["stop_loss"]),
                "ticket": int(pos_data["ticket"]),
                "timestamp": str(pos_data["timestamp"]),
                "partial_exit_done": bool(pos_data.get("partial_exit_done", False)),
            }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(self._state_path.parent),
                suffix=".tmp",
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, str(self._state_path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.debug("Estado salvo em %s", self._state_path)
        except OSError as exc:
            logger.error("Falha ao salvar estado do simulador: %s", exc)

    def _load_state(self) -> bool:
        """Carrega estado persistido do arquivo JSON (chamado sob lock)."""
        if not self._state_path.exists():
            logger.info("Nenhum estado anterior encontrado — iniciando limpo")
            return False

        try:
            with open(self._state_path, "r", encoding="utf-8") as f:
                state: dict[str, Any] = json.load(f)

            self._balance = float(state.get("balance", self._initial_balance))
            self._initial_balance = float(
                state.get("initial_balance", self._initial_balance)
            )
            self._next_ticket = int(state.get("next_ticket", 1))

            self._positions = {}
            raw_positions = state.get("positions", {})
            items = (
                raw_positions.items()
                if isinstance(raw_positions, dict)
                else [(p.get("ticker", ""), p) for p in raw_positions if isinstance(p, dict)]
            )
            for raw_key, pos_data in items:
                qty = int(pos_data["quantity"])
                if qty <= 0:
                    continue
                raw_ticker = str(pos_data.get("ticker") or raw_key)
                sym = format_b3_ticker_for_quantity(
                    raw_ticker,
                    quantity=max(1, qty),
                )
                if not sym:
                    continue
                base_norm = normalize_b3_ticker(sym) or sym
                entry_px = float(pos_data["entry_price"])
                sl_px = float(pos_data.get("stop_loss", 0.0))
                tkt = int(pos_data.get("ticket", 0))
                ts_str = str(pos_data.get("timestamp", datetime.now(tz=BRT).isoformat()))
                p_done = bool(pos_data.get("partial_exit_done", False))

                existing_key = next(
                    (k for k in self._positions if (normalize_b3_ticker(k) or k) == base_norm),
                    None,
                )
                if existing_key is not None:
                    prev = self._positions.pop(existing_key)
                    prev_qty = int(prev["quantity"])
                    merged_qty = prev_qty + qty
                    merged_sym = (
                        format_b3_ticker_for_quantity(base_norm, quantity=max(1, merged_qty))
                        or sym
                    )
                    merged_entry = round(
                        ((float(prev["entry_price"]) * prev_qty) + (entry_px * qty))
                        / merged_qty,
                        2,
                    )
                    pos_sls = [s for s in (float(prev["stop_loss"]), sl_px) if s > 0]
                    merged_sl = min(pos_sls) if pos_sls else 0.0
                    pos_tkts = [t for t in (int(prev["ticket"]), tkt) if t > 0]
                    merged_tkt = min(pos_tkts) if pos_tkts else 0
                    merged_ts = min(str(prev["timestamp"]), ts_str)
                    self._positions[merged_sym] = {
                        "ticker": merged_sym,
                        "quantity": merged_qty,
                        "entry_price": merged_entry,
                        "stop_loss": merged_sl,
                        "ticket": merged_tkt,
                        "timestamp": merged_ts,
                        "partial_exit_done": bool(
                            prev.get("partial_exit_done", False) or p_done
                        ),
                    }
                else:
                    self._positions[sym] = {
                        "ticker": sym,
                        "quantity": qty,
                        "entry_price": entry_px,
                        "stop_loss": sl_px,
                        "ticket": tkt,
                        "timestamp": ts_str,
                        "partial_exit_done": p_done,
                    }

            logger.info(
                "Estado restaurado — saldo: R$%.2f, posições: %d, próximo ticket: %d",
                self._balance,
                len(self._positions),
                self._next_ticket,
            )
            return True
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Estado corrompido, iniciando limpo: %s", exc)
            self._balance = self._initial_balance
            self._positions = {}
            self._next_ticket = 1
            return False

    def _get_current_price(self, ticker: str) -> float:
        """Obtém preço de mercado atual via MarketData usando ticker-base normalizado."""
        base_ticker = normalize_b3_ticker(ticker)
        if not base_ticker:
            return 0.0
        try:
            md = self._get_market_data()
            price_info = md.get_current_price(base_ticker)
            price = price_info.get("last", 0.0) if isinstance(price_info, dict) else 0.0
            if price and price > 0:
                return float(price)
        except Exception as exc:
            logger.warning("Falha ao obter preço de %s: %s", base_ticker, exc)
        return 0.0

    def connect(self) -> bool:
        """Conecta o simulador — carrega estado persistido."""
        with self._lock:
            self._load_state()
            self._connected = True
            logger.info("SimulatorBroker conectado — saldo: R$%.2f", self._balance)
            return True

    def disconnect(self) -> None:
        """Desconecta o simulador — salva estado final."""
        with self._lock:
            self._save_state()
            self._connected = False
            logger.info("SimulatorBroker desconectado")

    def reset_state(self, new_balance: float = 100000.00) -> None:
        """Reseta o estado do simulador para o saldo inicial especificado."""
        with self._lock:
            self._initial_balance = max(0.0, float(new_balance))
            self._balance = self._initial_balance
            self._positions.clear()
            self._next_ticket = 1
            self._save_state()
            logger.info(
                "SimulatorBroker resetado com sucesso: Saldo R$ %.2f",
                self._initial_balance,
            )

    @property
    def balance(self) -> float:
        """Propriedade para leitura do saldo."""
        with self._lock:
            return round(self._balance, 2)

    @balance.setter
    def balance(self, value: float) -> None:
        """Propriedade para ajuste direto do saldo."""
        with self._lock:
            self._balance = max(0.0, float(value))
            self._save_state()

    def get_balance(self) -> float:
        """Retorna o saldo em caixa disponível."""
        with self._lock:
            return round(self._balance, 2)

    def get_equity(self) -> float:
        """
        Retorna patrimônio total (caixa + valor de mercado das posições).

        Copia o estado sob lock e busca cotações de mercado fora do lock
        para nunca bloquear threads concorrentes durante I/O de rede.
        """
        with self._lock:
            balance_snapshot = self._balance
            positions_snapshot = [
                (ticker, dict(pos_data))
                for ticker, pos_data in self._positions.items()
            ]

        positions_value = 0.0
        for ticker, pos_data in positions_snapshot:
            current_price = self._get_current_price(ticker)
            if current_price > 0:
                positions_value += current_price * pos_data["quantity"]
            else:
                positions_value += pos_data["entry_price"] * pos_data["quantity"]

        return round(balance_snapshot + positions_value, 2)

    def buy(self, ticker: str, quantity: int, price: float, stop_loss: float) -> Order:
        """Executa ordem de compra simulada."""
        symbol = format_b3_ticker_for_quantity(ticker, max(1, int(quantity)))
        now = datetime.now(tz=BRT)

        if price <= 0:
            msg = "Preço unitário deve ser maior que zero"
            logger.warning("Compra rejeitada — %s", msg)
            return Order(
                ticker=symbol or ticker,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=price,
                stop_loss=stop_loss,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=msg,
            )

        try:
            self._validate_quantity(quantity)
        except ValueError as exc:
            logger.warning("Compra rejeitada — %s", exc)
            return Order(
                ticker=symbol,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=price,
                stop_loss=stop_loss,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=str(exc),
            )

        rounded_price = round(float(price), 2)
        if rounded_price <= 0:
            msg = "Preço unitário arredondado deve ser maior que zero"
            logger.warning("Compra rejeitada — %s", msg)
            return Order(
                ticker=symbol or ticker,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=0.0,
                stop_loss=stop_loss,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=msg,
            )

        if stop_loss is not None and float(stop_loss) < 0:
            msg = f"Stop-loss inválido: R${float(stop_loss):.2f}"
            logger.warning("Compra rejeitada — %s", msg)
            return Order(
                ticker=symbol,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=rounded_price,
                stop_loss=0.0,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=msg,
            )

        rounded_sl = round(float(stop_loss), 2) if stop_loss and stop_loss > 0 else 0.0
        if rounded_sl >= rounded_price:
            msg = (
                f"Stop-loss inválido: R${rounded_sl:.2f} deve ser estritamente menor "
                f"que o preço de compra R${rounded_price:.2f}"
            )
            logger.warning("Compra rejeitada — %s", msg)
            return Order(
                ticker=symbol,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=rounded_price,
                stop_loss=rounded_sl,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=msg,
            )

        total_cost = round(quantity * rounded_price, 2)

        with self._lock:
            if total_cost > self._balance:
                msg = (
                    f"Capital insuficiente: R${total_cost:.2f} necessário, "
                    f"R${self._balance:.2f} disponível"
                )
                logger.warning("Compra rejeitada — %s", msg)
                return Order(
                    ticker=symbol,
                    order_type=OrderType.BUY,
                    quantity=quantity,
                    price=rounded_price,
                    stop_loss=rounded_sl,
                    status=OrderStatus.REJECTED,
                    timestamp=now,
                    comment=msg,
                )

            existing_key = self._find_position_key(symbol)
            if existing_key is not None:
                msg = f"Já existe posição aberta em {existing_key}"
                logger.warning("Compra rejeitada — %s", msg)
                return Order(
                    ticker=symbol,
                    order_type=OrderType.BUY,
                    quantity=quantity,
                    price=rounded_price,
                    stop_loss=rounded_sl,
                    status=OrderStatus.REJECTED,
                    timestamp=now,
                    comment=msg,
                )

            ticket = self._generate_ticket()
            self._balance = round(self._balance - total_cost, 2)
            self._positions[symbol] = {
                "ticker": symbol,
                "quantity": quantity,
                "entry_price": rounded_price,
                "stop_loss": rounded_sl,
                "ticket": ticket,
                "timestamp": now.isoformat(),
                "partial_exit_done": False,
            }
            self._save_state()

            logger.info(
                "COMPRA executada: %s | %d × R$%.2f = R$%.2f | SL: R$%.2f | Ticket: %d",
                symbol,
                quantity,
                rounded_price,
                total_cost,
                rounded_sl,
                ticket,
            )

            return Order(
                ticker=symbol,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=rounded_price,
                stop_loss=rounded_sl,
                status=OrderStatus.FILLED,
                ticket=ticket,
                timestamp=now,
                comment=f"Compra simulada executada — total: R${total_cost:.2f}",
            )

    def sell(self, ticker: str, quantity: int, price: float) -> Order:
        """Executa ordem de venda simulada."""
        now = datetime.now(tz=BRT)
        fallback_symbol = format_b3_ticker_for_quantity(ticker, max(1, int(quantity))) or ticker

        with self._lock:
            if quantity <= 0 or price <= 0:
                logger.warning(
                    "Venda rejeitada — quantidade ou preço inválido: qty=%s, price=%s",
                    quantity,
                    price,
                )
                return Order(
                    fallback_symbol,
                    OrderType.SELL,
                    quantity,
                    price,
                    status=OrderStatus.REJECTED,
                    timestamp=now,
                    comment="Quantidade e preço devem ser maiores que zero",
                )

            pos_key = self._find_position_key(ticker)
            if pos_key is None:
                msg = f"Nenhuma posição aberta em {normalize_b3_ticker(ticker) or ticker}"
                logger.warning("Venda rejeitada — %s", msg)
                return Order(
                    ticker=fallback_symbol,
                    order_type=OrderType.SELL,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.REJECTED,
                    timestamp=now,
                    comment=msg,
                )

            pos_data = self._positions[pos_key]
            if quantity > pos_data["quantity"]:
                msg = (
                    f"Quantidade solicitada ({quantity}) excede posição "
                    f"({pos_data['quantity']}) em {pos_key}"
                )
                logger.warning("Venda rejeitada — %s", msg)
                return Order(
                    ticker=pos_key,
                    order_type=OrderType.SELL,
                    quantity=quantity,
                    price=price,
                    status=OrderStatus.REJECTED,
                    timestamp=now,
                    comment=msg,
                )

            rounded_price = round(float(price), 2)
            ticket = self._generate_ticket()
            total_revenue = round(quantity * rounded_price, 2)
            self._balance = round(self._balance + total_revenue, 2)

            pnl = round((rounded_price - pos_data["entry_price"]) * quantity, 2)

            if quantity >= pos_data["quantity"]:
                del self._positions[pos_key]
                executed_symbol = pos_key
            else:
                new_qty = pos_data["quantity"] - quantity
                new_sym = format_b3_ticker_for_quantity(pos_key, new_qty)
                pos_data["quantity"] = new_qty
                pos_data["ticker"] = new_sym
                pos_data["partial_exit_done"] = True
                if new_sym != pos_key:
                    del self._positions[pos_key]
                    self._positions[new_sym] = pos_data
                executed_symbol = format_b3_ticker_for_quantity(pos_key, quantity)

            self._save_state()

            logger.info(
                "VENDA executada: %s | %d × R$%.2f = R$%.2f | P&L: R$%+.2f | Ticket: %d",
                executed_symbol,
                quantity,
                rounded_price,
                total_revenue,
                pnl,
                ticket,
            )

            return Order(
                ticker=executed_symbol,
                order_type=OrderType.SELL,
                quantity=quantity,
                price=rounded_price,
                status=OrderStatus.FILLED,
                ticket=ticket,
                timestamp=now,
                comment=f"Venda simulada executada — receita: R${total_revenue:.2f}, P&L: R${pnl:+.2f}",
            )

    def emergency_sell(self, ticker: str) -> Order:
        """Venda de emergência — liquida toda a posição a preço de mercado."""
        with self._lock:
            pos_key = self._find_position_key(ticker)
            if pos_key is None:
                sym = normalize_b3_ticker(ticker) or ticker
                return Order(
                    ticker=sym,
                    order_type=OrderType.SELL,
                    quantity=0,
                    price=0.0,
                    status=OrderStatus.REJECTED,
                    timestamp=datetime.now(tz=BRT),
                    comment=f"Nenhuma posição aberta em {sym} para venda emergencial",
                )
            pos_snapshot = dict(self._positions[pos_key])
            quantity = int(pos_snapshot["quantity"])

        market_price = self._get_current_price(pos_key)
        if market_price <= 0:
            market_price = float(pos_snapshot["entry_price"])
            logger.warning(
                "Preço de mercado indisponível para %s — usando preço de entrada R$%.2f",
                pos_key,
                market_price,
            )

        logger.warning(
            "VENDA EMERGENCIAL: %s | %d ações @ R$%.2f",
            pos_key,
            quantity,
            market_price,
        )
        return self.sell(pos_key, quantity, market_price)

    def get_positions(self) -> list[Position]:
        """Retorna todas as posições abertas como objetos Position."""
        with self._lock:
            positions_copy = {k: dict(v) for k, v in self._positions.items()}

        result: list[Position] = []
        for ticker, pos_data in positions_copy.items():
            current_price = self._get_current_price(ticker)
            if current_price <= 0:
                current_price = float(pos_data["entry_price"])

            try:
                ts = datetime.fromisoformat(pos_data["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=BRT)
            except (ValueError, TypeError):
                ts = datetime.now(tz=BRT)

            result.append(
                Position(
                    ticker=pos_data["ticker"],
                    quantity=int(pos_data["quantity"]),
                    entry_price=float(pos_data["entry_price"]),
                    current_price=current_price,
                    stop_loss=float(pos_data["stop_loss"]),
                    ticket=int(pos_data["ticket"]),
                    partial_exit_done=bool(pos_data.get("partial_exit_done", False)),
                    timestamp=ts,
                )
            )
        return result

    def get_position(self, ticker: str) -> Optional[Position]:
        """Retorna a posição de um ativo específico pelo ticker-base."""
        norm = normalize_b3_ticker(ticker)
        if not norm:
            return None
        for pos in self.get_positions():
            if normalize_b3_ticker(pos.ticker) == norm:
                return pos
        return None

    def modify_stop_loss(self, ticker: str, new_sl: float) -> bool:
        """Modifica o stop-loss de uma posição aberta."""
        if new_sl <= 0:
            logger.warning("Stop-loss inválido: R$%.2f — deve ser > 0", new_sl)
            return False

        rounded_sl = round(float(new_sl), 2)
        with self._lock:
            pos_key = self._find_position_key(ticker)
            if pos_key is None:
                logger.warning("Posição %s não encontrada para modificar SL", ticker)
                return False

            old_sl = self._positions[pos_key]["stop_loss"]
            self._positions[pos_key]["stop_loss"] = rounded_sl
            self._save_state()

            logger.info(
                "Stop-loss modificado: %s | R$%.2f → R$%.2f",
                pos_key,
                old_sl,
                rounded_sl,
            )
            return True

    def set_partial_exit_done(self, ticker: str, done: bool = True) -> bool:
        """Persiste o marcador de saída parcial (`partial_exit_done`) de uma posição aberta."""
        with self._lock:
            pos_key = self._find_position_key(ticker)
            if pos_key is None:
                return False
            self._positions[pos_key]["partial_exit_done"] = bool(done)
            self._save_state()
            return True

    def set_initial_capital(self, new_capital: float, reset_positions: bool = False) -> None:
        """
        Redefine o capital inicial da simulação.

        Quando reset_positions=False, aplica apenas o delta ao saldo disponível,
        preservando integralmente o P&L realizado acumulado e posições abertas.
        """
        with self._lock:
            new_cap = max(1.0, float(new_capital))
            if reset_positions:
                self._initial_balance = new_cap
                self._balance = new_cap
                self._positions.clear()
                self._next_ticket = 1
            else:
                delta = new_cap - self._initial_balance
                self._initial_balance = new_cap
                self._balance = round(max(0.0, self._balance + delta), 2)
            self._save_state()
            logger.info(
                "SimulatorBroker capital inicial redefinido: R$%.2f (saldo=R$%.2f, reset_pos=%s)",
                self._initial_balance,
                self._balance,
                reset_positions,
            )

    def reset(self) -> None:
        """Reseta o simulador ao estado inicial."""
        with self._lock:
            self._balance = self._initial_balance
            self._positions.clear()
            self._next_ticket = 1
            self._save_state()
            logger.info("Simulador resetado — capital: R$%.2f", self._initial_balance)

    @property
    def is_connected(self) -> bool:
        """Indica se o simulador está conectado."""
        return self._connected

    def __repr__(self) -> str:
        """Representação textual do simulador."""
        return (
            f"SimulatorBroker(balance=R${self._balance:.2f}, "
            f"positions={len(self._positions)}, "
            f"connected={self._connected})"
        )
