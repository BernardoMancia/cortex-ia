"""
Broker MetaTrader 5 do Projeto Córtex.

Implementa a interface BrokerBase utilizando a API Python do MT5.
Funciona exclusivamente em Windows. Inclui auto-reconexão,
detecção correta de modo de preenchimento B3, roteamento multi-pernas
(lote padrão + fracionário), guarda de leilão/liquidez, cancelamento imediato de
ordens pendentes (10008) e tratamento completo de retcodes.
"""

from __future__ import annotations

from datetime import datetime
import logging
import platform
import threading
import time
from typing import Any, Optional
from zoneinfo import ZoneInfo

from broker.base import BrokerBase, Order, OrderStatus, OrderType, Position
from config.settings import settings
from utils.helpers import (
    format_b3_ticker_for_quantity,
    is_b3_unit_lot_asset,
    normalize_b3_ticker,
    split_b3_order_legs,
)

logger = logging.getLogger("cortex.broker.mt5")

BRT: ZoneInfo = ZoneInfo("America/Sao_Paulo")

_mt5_available: bool = False
try:
    if platform.system() == "Windows":
        import MetaTrader5 as mt5

        _mt5_available = True
    else:
        mt5 = None  # type: ignore[assignment]
except ImportError:
    mt5 = None  # type: ignore[assignment]

_MT5_RETCODE_MESSAGES: dict[int, str] = {
    10004: "Requote — preço mudou",
    10006: "Requisição rejeitada",
    10007: "Requisição cancelada pelo trader",
    10008: "Ordem colocada no livro",
    10009: "Ordem executada com sucesso",
    10010: "Ordem executada parcialmente",
    10011: "Erro no processamento da requisição",
    10012: "Requisição cancelada por timeout",
    10013: "Requisição inválida",
    10014: "Volume inválido na requisição",
    10015: "Preço inválido na requisição",
    10016: "Stops inválidos na requisição",
    10017: "Trading desabilitado",
    10018: "Mercado fechado",
    10019: "Capital insuficiente",
    10020: "Preços mudaram",
    10021: "Sem cotação para processar",
    10022: "Período de expiração inválido",
    10023: "Estado da ordem mudou",
    10024: "Muitas requisições",
    10025: "Sem mudanças na requisição",
    10026: "Autotrading desabilitado no servidor",
    10027: "Autotrading desabilitado no terminal",
    10028: "Requisição bloqueada para processamento",
    10029: "Ordem ou posição congelada",
    10030: "Tipo de preenchimento inválido",
    10031: "Sem conexão com o servidor",
    10032: "Operação permitida apenas para contas reais",
    10033: "Limite de ordens pendentes atingido",
    10034: "Limite de volume atingido",
    10035: "Tipo de ordem inválido ou proibido",
    10036: "Posição já fechada",
    10038: "Volume de fechamento excede posição atual",
    10039: "Ordem de fechamento já existe para esta posição",
    10040: "Limite de posições atingido",
    10041: "Requisição rejeitada — verificação de hedging pendente",
    10042: "Requisição rejeitada — limite de regra FIFO",
    10043: "Requisição rejeitada — regra de hedge-only",
}


def _retcode_to_message(retcode: int) -> str:
    """Converte retcode MT5 em mensagem legível."""
    return _MT5_RETCODE_MESSAGES.get(retcode, f"Código desconhecido: {retcode}")


def _safe_int(val: Any, default: int = 0) -> int:
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return int(val)
    return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    return default


class MT5Broker(BrokerBase):
    """
    Implementação de corretora via MetaTrader 5 nativo (Windows).

    Suporta auto-reconexão, detecção correta de filling mode B3,
    ordens multi-pernas (lote padrão 100 + fracionário 1..99) e magic number.
    """

    MAGIC_NUMBER: int = settings.mt5_magic
    MAX_RECONNECT_ATTEMPTS: int = 3
    RECONNECT_DELAY_SECONDS: float = 2.0

    def __init__(self) -> None:
        """Inicializa o broker MT5."""
        self._connected: bool = False
        self._lock: threading.Lock = threading.Lock()
        self.MAGIC_NUMBER = int(getattr(settings, "MT5_MAGIC", 234000))

        raw_login = str(getattr(settings, "mt5_login", "") or "").strip()
        if raw_login:
            try:
                self._login: int = int(raw_login)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    "MT5_LOGIN inválido. Defina um número de conta MT5 válido."
                ) from exc
        else:
            self._login = 0

        self._password: str = getattr(settings, "mt5_password", "") or ""
        self._server: str = getattr(settings, "mt5_server", "") or ""
        self._mt5_path: str = getattr(settings, "mt5_path", "") or ""
        logger.info(
            "MT5Broker criado — login: %s, server: %s",
            self._login or "IPC_ATIVO",
            self._server,
        )

    def _check_platform(self) -> None:
        """Verifica se o sistema operacional é Windows."""
        if platform.system() != "Windows":
            raise RuntimeError(
                f"MT5Broker requer Windows. Sistema detectado: {platform.system()}. "
                "Configure MT5_BRIDGE_URL ou use SimulatorBroker."
            )

    def _check_mt5_available(self) -> None:
        """Verifica se o módulo MetaTrader5 está instalado."""
        if not _mt5_available or mt5 is None:
            raise RuntimeError(
                "Módulo MetaTrader5 não instalado. Execute: pip install MetaTrader5"
            )

    def _ensure_connected(self) -> None:
        """Verifica conexão e tenta reconectar se necessário."""
        if self._connected:
            try:
                with self._lock:
                    info = mt5.account_info()
                if info is not None:
                    return
            except Exception:
                pass

        logger.warning("Conexão MT5 perdida — tentando reconectar...")
        for attempt in range(1, self.MAX_RECONNECT_ATTEMPTS + 1):
            try:
                if self.connect():
                    logger.info("Reconexão MT5 bem-sucedida na tentativa %d", attempt)
                    return
            except RuntimeError as exc:
                raise ConnectionError(str(exc)) from exc
            time.sleep(self.RECONNECT_DELAY_SECONDS)

        raise ConnectionError(
            f"Falha ao reconectar ao MT5 após {self.MAX_RECONNECT_ATTEMPTS} tentativas"
        )

    def _ensure_symbol_visible(self, symbol: str) -> bool:
        """Garante que o símbolo está selecionado e visível no Market Watch."""
        with self._lock:
            info = mt5.symbol_info(symbol)
            if info is None:
                if not mt5.symbol_select(symbol, True):
                    logger.error("Símbolo %s não encontrado no MT5", symbol)
                    return False
                info = mt5.symbol_info(symbol)
                if info is None:
                    logger.error("Símbolo %s indisponível após seleção", symbol)
                    return False

            if not info.visible:
                if not mt5.symbol_select(symbol, True):
                    logger.error("Falha ao selecionar símbolo %s no Market Watch", symbol)
                    return False
                time.sleep(0.2)

        return True

    def _get_symbol_digits(self, symbol: str) -> int:
        """Obtém casas decimais suportadas pelo símbolo (padrão 2 na B3)."""
        with self._lock:
            info = mt5.symbol_info(symbol)
        digits = getattr(info, "digits", None) if info is not None else None
        return int(digits) if isinstance(digits, int) and not isinstance(digits, bool) and digits >= 0 else 2

    def _detect_filling_mode(self, symbol: str) -> int:
        """
        Detecta o modo de preenchimento suportado pelo símbolo:
        - `trade_exemode == 2` (`SYMBOL_TRADE_EXECUTION_EXCHANGE`, B3): `ORDER_FILLING_RETURN (2)`
        - Bitmask `filling_mode`: IOC (`filling & 2`) -> FOK (`filling & 1`) -> RETURN (`2`).
        """
        default_return = _safe_int(getattr(mt5, "ORDER_FILLING_RETURN", 2), 2)
        ioc_val = _safe_int(getattr(mt5, "ORDER_FILLING_IOC", 1), 1)
        fok_val = _safe_int(getattr(mt5, "ORDER_FILLING_FOK", 0), 0)

        with self._lock:
            info = mt5.symbol_info(symbol)

        if info is None:
            logger.warning("Símbolo %s não encontrado — usando ORDER_FILLING_RETURN", symbol)
            return default_return

        trade_exemode = _safe_int(getattr(info, "trade_exemode", -1), -1)
        if trade_exemode == 2:
            return default_return

        filling = _safe_int(getattr(info, "filling_mode", 0), 0)
        if filling & 2:
            return ioc_val
        if filling & 1:
            return fok_val
        return default_return

    @staticmethod
    def _ensure_fractional_suffix(ticker: str, quantity: int = 1) -> str:
        """
        Formata o símbolo B3 adequado para a quantidade informada:
        - ETFs, FIIs e BDRs: sempre ticker-base sem 'F'.
        - Ações ON/PN e Units (ex: PETR4, TAEE11): 'F' para 1..99, sem 'F' para >= 100.
        """
        return format_b3_ticker_for_quantity(ticker, quantity=quantity)

    def _is_long_bot_position(self, pos: Any) -> bool:
        """Garante que apenas posições COMPRADAS (POSITION_TYPE_BUY == 0) deste robô sejam somadas."""
        if getattr(pos, "magic", None) != self.MAGIC_NUMBER:
            return False
        pos_type = _safe_int(getattr(pos, "type", 0), 0)
        if pos_type != 0:
            logger.critical(
                "ALERTA CRÍTICO: posição SHORT (type=%d) detectada com MAGIC_NUMBER=%d em %s (ticket=%s) — ignorando na custódia comprada!",
                pos_type,
                self.MAGIC_NUMBER,
                getattr(pos, "symbol", ""),
                getattr(pos, "ticket", ""),
            )
            return False
        return True

    def _get_matching_mt5_positions(self, ticker: str) -> list[Any]:
        """Retorna todas as posições long abertas deste robô correspondentes ao ticker-base."""
        norm = normalize_b3_ticker(ticker)
        if not norm:
            return []
        with self._lock:
            raw_positions = mt5.positions_get()
        if not raw_positions:
            return []
        return [
            pos
            for pos in raw_positions
            if self._is_long_bot_position(pos)
            and normalize_b3_ticker(str(getattr(pos, "symbol", ""))) == norm
        ]

    def _check_placed_order_deals(self, order_ticket: int) -> tuple[int, float]:
        """Verifica se uma ordem PLACED (10008) já teve negócio executado na B3."""
        if not order_ticket or not hasattr(mt5, "history_deals_get"):
            return 0, 0.0
        time.sleep(0.25)
        with self._lock:
            deals = mt5.history_deals_get(order=order_ticket)
        if not deals or not isinstance(deals, (list, tuple)):
            return 0, 0.0
        total_vol = 0.0
        total_val = 0.0
        for d in deals:
            vol = _safe_float(getattr(d, "volume", 0.0), 0.0)
            px = _safe_float(getattr(d, "price", 0.0), 0.0)
            if vol > 0:
                total_vol += vol
                total_val += vol * px
        if total_vol > 0:
            return int(round(total_vol)), round(total_val / total_vol, 2)
        return 0, 0.0

    def _cancel_resting_order(self, order_ticket: int, symbol: str = "") -> bool:
        """Envia cancelamento best-effort (TRADE_ACTION_REMOVE = 8) para ordem pendente 10008."""
        if not order_ticket or order_ticket <= 0 or mt5 is None:
            return False
        remove_action = _safe_int(getattr(mt5, "TRADE_ACTION_REMOVE", 8), 8)
        cancel_req: dict[str, Any] = {
            "action": remove_action,
            "order": int(order_ticket),
            "magic": self.MAGIC_NUMBER,
            "comment": "Cortex CANCEL_10008",
        }
        if symbol:
            cancel_req["symbol"] = symbol
        try:
            with self._lock:
                cancel_res = mt5.order_send(cancel_req)
        except Exception as exc:
            logger.warning(
                "Exceção ao cancelar ordem pendente MT5 %d (%s): %s",
                order_ticket,
                symbol,
                exc,
            )
            return False
        ret = _safe_int(getattr(cancel_res, "retcode", -1), -1) if cancel_res is not None else -1
        ok = ret in (10009, 10008)
        if ok:
            logger.info("Ordem pendente MT5 %d (%s) cancelada do livro B3.", order_ticket, symbol)
        else:
            logger.warning(
                "Não foi possível confirmar cancelamento da ordem MT5 %d (%s): %s",
                order_ticket,
                symbol,
                cancel_res,
            )
        return ok

    def connect(self) -> bool:
        """Conecta ao terminal MetaTrader 5 validando permissão de Algo Trading."""
        self._check_platform()
        self._check_mt5_available()

        with self._lock:
            init_kwargs: dict[str, Any] = {}
            if self._mt5_path:
                init_kwargs["path"] = self._mt5_path

            if not mt5.initialize(**init_kwargs):
                if not mt5.initialize():
                    error = mt5.last_error()
                    logger.error("Falha ao inicializar MT5: %s", error)
                    self._connected = False
                    return False

            if self._login and self._password and self._server:
                authorized = mt5.login(
                    login=self._login,
                    password=self._password,
                    server=self._server,
                )
                if not authorized:
                    error = mt5.last_error()
                    logger.error("Falha no login MT5: %s", error)
                    mt5.shutdown()
                    self._connected = False
                    return False

            account_info = mt5.account_info()
            if account_info is None:
                logger.error("Não foi possível obter informações da conta MT5")
                mt5.shutdown()
                self._connected = False
                return False

            term_fn = getattr(mt5, "terminal_info", None)
            term_info = term_fn() if callable(term_fn) else None
            if term_info is not None and getattr(term_info, "trade_allowed", True) is False:
                logger.error(
                    "MT5 conectado, porém negociação automatizada (Algo Trading) está desabilitada!"
                )
                mt5.shutdown()
                self._connected = False
                return False

            if (
                getattr(account_info, "trade_allowed", True) is False
                or getattr(account_info, "trade_expert", True) is False
            ):
                logger.error(
                    "MT5 conectado, porém a conta não permite negociação automatizada (Expert Advisors)!"
                )
                mt5.shutdown()
                self._connected = False
                return False

            self._connected = True
            logger.info(
                "MT5 conectado — conta: %d, servidor: %s, saldo: R$%.2f",
                account_info.login,
                account_info.server,
                account_info.balance,
            )
            return True

    def disconnect(self) -> None:
        """Encerra a conexão com o MetaTrader 5."""
        with self._lock:
            if _mt5_available and mt5 is not None:
                try:
                    mt5.shutdown()
                except Exception as exc:
                    logger.warning("Erro ao desconectar MT5: %s", exc)
            self._connected = False
            logger.info("MT5Broker desconectado")

    def get_balance(self) -> float:
        """Retorna o saldo em caixa da conta MT5 ou levanta ConnectionError se desconectado."""
        if not self._connected or mt5 is None:
            raise ConnectionError("MT5 desconectado ou indisponível")
        with self._lock:
            info = mt5.account_info()
        if info is None:
            raise ConnectionError("MT5 desconectado ou indisponível")
        return round(float(info.balance), 2)

    def get_equity(self) -> float:
        """Retorna o patrimônio total da conta MT5 ou levanta ConnectionError se desconectado."""
        if not self._connected or mt5 is None:
            raise ConnectionError("MT5 desconectado ou indisponível")
        with self._lock:
            info = mt5.account_info()
        if info is None:
            raise ConnectionError("MT5 desconectado ou indisponível")
        return round(float(info.equity), 2)

    def _execute_leg(
        self,
        symbol: str,
        quantity: int,
        price: float,
        order_type: OrderType,
        stop_loss: float = 0.0,
        position_ticket: int | None = None,
        align_to_book: bool = True,
    ) -> tuple[OrderStatus, int, float, int | None, str]:
        """Executa uma única perna de compra ou venda no MT5."""
        if not self._ensure_symbol_visible(symbol):
            return (
                OrderStatus.REJECTED,
                0,
                price,
                None,
                f"Símbolo {symbol} indisponível",
            )

        digits = self._get_symbol_digits(symbol)
        rounded_price = round(float(price), digits)
        rounded_sl = round(float(stop_loss), digits) if stop_loss and stop_loss > 0 else 0.0
        filling_mode = self._detect_filling_mode(symbol)
        max_spread = float(getattr(settings, "MAX_SPREAD_PERCENT", 0.008) or 0.0)

        if order_type == OrderType.BUY:
            with self._lock:
                tick = mt5.symbol_info_tick(symbol)
            if tick is not None:
                raw_bid = getattr(tick, "bid", None)
                raw_ask = getattr(tick, "ask", None)
                has_numeric_quotes = (
                    isinstance(raw_bid, (int, float))
                    and not isinstance(raw_bid, bool)
                    and isinstance(raw_ask, (int, float))
                    and not isinstance(raw_ask, bool)
                )
                if has_numeric_quotes:
                    bid = float(raw_bid)
                    ask = float(raw_ask)
                    if bid > 0.0 and ask > 0.0 and ask < bid:
                        msg = f"Livro em leilão (ask < bid) em {symbol}: bid=R${bid:.2f}, ask=R${ask:.2f}"
                        logger.warning("Ordem BUY bloqueada MT5 — %s", msg)
                        return OrderStatus.REJECTED, 0, rounded_price, None, msg
                    if bid <= 0.0 or ask <= 0.0:
                        msg = f"Livro sem liquidez (bid/ask zerado) em {symbol}: bid=R${bid:.2f}, ask=R${ask:.2f}"
                        logger.warning("Ordem BUY bloqueada MT5 — %s", msg)
                        return OrderStatus.REJECTED, 0, rounded_price, None, msg
                    if bid > 0.0 and ask > bid and max_spread > 0.0:
                        spread_pct = (ask - bid) / bid
                        if spread_pct > max_spread:
                            msg = (
                                f"Spread excessivo em {symbol}: {spread_pct * 100:.2f}% > "
                                f"máx {max_spread * 100:.2f}%"
                            )
                            logger.warning("Ordem BUY bloqueada pelo Spread Guard MT5 — %s", msg)
                            return OrderStatus.REJECTED, 0, rounded_price, None, msg
                    if align_to_book and ask > 0.0 and rounded_price > 0.0:
                        if abs(ask - rounded_price) / rounded_price <= max(max_spread, 0.01):
                            rounded_price = round(ask, digits)
        elif align_to_book:
            with self._lock:
                tick = mt5.symbol_info_tick(symbol)
            if tick is not None:
                bid = _safe_float(getattr(tick, "bid", 0.0), 0.0)
                ask = _safe_float(getattr(tick, "ask", 0.0), 0.0)
                if bid > 0.0 and (ask <= 0.0 or ask >= bid) and rounded_price > 0.0:
                    if abs(bid - rounded_price) / rounded_price <= max(max_spread, 0.01):
                        rounded_price = round(bid, digits)

        max_slip = float(getattr(settings, "MAX_SLIPPAGE_PERCENT", 0.003) or 0.003)
        tick_step = 10.0 ** (-digits) if digits > 0 else 0.01
        deviation_pts = max(2, min(50, int(round((max(0.01, rounded_price) * max_slip) / tick_step))))

        request: dict[str, Any] = {
            "action": _safe_int(getattr(mt5, "TRADE_ACTION_DEAL", 1), 1),
            "symbol": symbol,
            "volume": float(quantity),
            "type": (
                _safe_int(getattr(mt5, "ORDER_TYPE_BUY", 0), 0)
                if order_type == OrderType.BUY
                else _safe_int(getattr(mt5, "ORDER_TYPE_SELL", 1), 1)
            ),
            "price": rounded_price,
            "deviation": deviation_pts,
            "magic": self.MAGIC_NUMBER,
            "comment": "Cortex BUY" if order_type == OrderType.BUY else "Cortex SELL",
            "type_time": _safe_int(getattr(mt5, "ORDER_TIME_GTC", 0), 0),
            "type_filling": filling_mode,
        }
        if order_type == OrderType.BUY and rounded_sl > 0:
            request["sl"] = rounded_sl
        if position_ticket and position_ticket > 0:
            request["position"] = int(position_ticket)

        with self._lock:
            check_result = mt5.order_check(request)
            if check_result is None or check_result.retcode != 0:
                retcode = check_result.retcode if check_result else -1
                msg = f"Pré-validação falhou ({symbol}): {_retcode_to_message(retcode)}"
                logger.warning("Ordem %s rejeitada MT5 — %s", order_type.value, msg)
                return OrderStatus.REJECTED, 0, rounded_price, None, msg

            result = mt5.order_send(request)
            if result is None:
                error = mt5.last_error()
                msg = f"Erro ao enviar ordem ({symbol}): {error}"
                logger.error("Ordem %s falhou MT5 — %s", order_type.value, msg)
                return OrderStatus.REJECTED, 0, rounded_price, None, msg

        ret = int(result.retcode)
        ticket = _safe_int(getattr(result, "order", 0), 0) or None
        exec_price = _safe_float(getattr(result, "price", 0.0), 0.0) or rounded_price

        if ret == 10009:
            raw_vol = _safe_float(getattr(result, "volume", 0.0), 0.0)
            filled_qty = int(round(raw_vol)) if raw_vol > 0 else quantity
            logger.info(
                "%s MT5 executada: %s | %d × R$%.2f | Ticket: %s",
                order_type.value,
                symbol,
                filled_qty,
                exec_price,
                ticket,
            )
            return (
                OrderStatus.FILLED,
                filled_qty,
                exec_price,
                ticket,
                f"MT5 executada — {getattr(result, 'comment', '')}",
            )

        if ret == 10010:
            filled_qty = int(round(_safe_float(getattr(result, "volume", 0.0), 0.0)))
            if filled_qty > 0:
                if filled_qty < quantity and ticket is not None and ticket > 0:
                    self._cancel_resting_order(ticket, symbol)
                logger.warning(
                    "%s MT5 executada parcialmente: %s | %d/%d × R$%.2f | Ticket: %s",
                    order_type.value,
                    symbol,
                    filled_qty,
                    quantity,
                    exec_price,
                    ticket,
                )
                return (
                    OrderStatus.FILLED,
                    filled_qty,
                    exec_price,
                    ticket,
                    f"MT5 parcial ({filled_qty}/{quantity}) — {getattr(result, 'comment', '')}",
                )
            if ticket is not None and ticket > 0:
                self._cancel_resting_order(ticket, symbol)
            return (
                OrderStatus.REJECTED,
                0,
                rounded_price,
                ticket,
                "Ordem parcial sem volume preenchido",
            )

        if ret == 10008:
            deal_id = _safe_int(getattr(result, "deal", 0), 0)
            raw_vol = _safe_float(getattr(result, "volume", 0.0), 0.0)
            if deal_id > 0 and raw_vol > 0:
                filled_now = int(round(raw_vol))
                if filled_now < quantity and ticket is not None and ticket > 0:
                    self._cancel_resting_order(ticket, symbol)
                return (
                    OrderStatus.FILLED,
                    filled_now,
                    exec_price,
                    ticket,
                    f"MT5 executada — {getattr(result, 'comment', '')}",
                )

            filled_from_hist, hist_price = self._check_placed_order_deals(ticket or 0)
            if filled_from_hist >= quantity:
                return (
                    OrderStatus.FILLED,
                    filled_from_hist,
                    hist_price or exec_price,
                    ticket,
                    f"MT5 executada em bolsa — {getattr(result, 'comment', '')}",
                )

            cancelled = False
            if ticket is not None and ticket > 0:
                cancelled = self._cancel_resting_order(ticket, symbol)

            if filled_from_hist > 0:
                return (
                    OrderStatus.FILLED,
                    filled_from_hist,
                    hist_price or exec_price,
                    ticket,
                    f"MT5 parcial em bolsa — {getattr(result, 'comment', '')}",
                )

            if cancelled:
                return (
                    OrderStatus.REJECTED,
                    0,
                    rounded_price,
                    ticket,
                    f"Ordem pendente (10008) cancelada do livro B3 — {getattr(result, 'comment', '')}",
                )

            logger.info(
                "Ordem %s MT5 colocada no livro (pendente): %s | Ticket: %s",
                order_type.value,
                symbol,
                ticket,
            )
            return (
                OrderStatus.PENDING,
                0,
                rounded_price,
                ticket,
                f"MT5 pendente no livro — {getattr(result, 'comment', '')}",
            )

        msg = _retcode_to_message(ret)
        logger.warning(
            "Ordem %s MT5 rejeitada (%s) — retcode %d: %s",
            order_type.value,
            symbol,
            ret,
            msg,
        )
        return OrderStatus.REJECTED, 0, rounded_price, ticket, f"Retcode {ret}: {msg}"

    def buy(self, ticker: str, quantity: int, price: float, stop_loss: float) -> Order:
        """Envia ordem de compra via MT5 com suporte a lotes mistos B3."""
        now = datetime.now(tz=BRT)
        rounded_price = round(float(price), 2)
        rounded_sl = round(float(stop_loss), 2) if stop_loss and stop_loss > 0 else 0.0
        primary_symbol = self._ensure_fractional_suffix(ticker, max(1, int(quantity)))

        if quantity <= 0 or rounded_price <= 0:
            return Order(
                ticker=primary_symbol,
                order_type=OrderType.BUY,
                quantity=max(0, int(quantity)),
                price=max(0.0, rounded_price),
                stop_loss=rounded_sl,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment="Quantidade e preço devem ser maiores que zero",
            )

        try:
            self._ensure_connected()
        except ConnectionError as exc:
            return Order(
                ticker=primary_symbol,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=rounded_price,
                stop_loss=rounded_sl,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=f"Sem conexão MT5: {exc}",
            )

        legs = split_b3_order_legs(ticker, quantity)
        if not legs:
            return Order(
                ticker=primary_symbol,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=rounded_price,
                stop_loss=rounded_sl,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment="Ticker ou quantidade inválida",
            )

        total_filled = 0
        total_value = 0.0
        last_ticket: int | None = None
        any_pending = False
        comments: list[str] = []

        for leg_symbol, leg_qty in legs:
            st, filled_qty, exec_px, tkt, msg = self._execute_leg(
                symbol=leg_symbol,
                quantity=leg_qty,
                price=rounded_price,
                order_type=OrderType.BUY,
                stop_loss=rounded_sl,
            )
            if tkt is not None:
                last_ticket = tkt
            if msg:
                comments.append(msg)
            if st == OrderStatus.FILLED and filled_qty > 0:
                total_filled += filled_qty
                total_value += filled_qty * exec_px
            elif st == OrderStatus.PENDING:
                any_pending = True

        out_symbol = legs[0][0] if len(legs) == 1 else normalize_b3_ticker(ticker)
        if total_filled > 0:
            avg_px = round(total_value / total_filled, 2)
            return Order(
                ticker=out_symbol,
                order_type=OrderType.BUY,
                quantity=total_filled,
                price=avg_px,
                stop_loss=rounded_sl,
                status=OrderStatus.FILLED,
                ticket=last_ticket,
                timestamp=now,
                comment=" | ".join(comments),
            )

        if any_pending:
            return Order(
                ticker=out_symbol,
                order_type=OrderType.BUY,
                quantity=quantity,
                price=rounded_price,
                stop_loss=rounded_sl,
                status=OrderStatus.PENDING,
                ticket=last_ticket,
                timestamp=now,
                comment=" | ".join(comments),
            )

        return Order(
            ticker=out_symbol,
            order_type=OrderType.BUY,
            quantity=quantity,
            price=rounded_price,
            stop_loss=rounded_sl,
            status=OrderStatus.REJECTED,
            ticket=last_ticket,
            timestamp=now,
            comment=" | ".join(comments) or "Compra rejeitada",
        )

    def sell(self, ticker: str, quantity: int, price: float) -> Order:
        """Envia ordem de venda via MT5 operando nas posições long abertas com segurança de lote padrão."""
        now = datetime.now(tz=BRT)
        rounded_price = round(float(price), 2)
        primary_symbol = self._ensure_fractional_suffix(ticker, max(1, int(quantity)))

        if quantity <= 0 or rounded_price <= 0:
            return Order(
                ticker=primary_symbol,
                order_type=OrderType.SELL,
                quantity=max(0, int(quantity)),
                price=max(0.0, rounded_price),
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment="Quantidade e preço devem ser maiores que zero",
            )

        try:
            self._ensure_connected()
        except ConnectionError as exc:
            return Order(
                ticker=primary_symbol,
                order_type=OrderType.SELL,
                quantity=quantity,
                price=rounded_price,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=f"Sem conexão MT5: {exc}",
            )

        execution_plan: list[tuple[str, int, int | None]] = []
        std_lot_blocked = False
        matching = self._get_matching_mt5_positions(ticker)
        if matching:
            remaining = int(quantity)
            for pos in matching:
                if remaining <= 0:
                    break
                pos_sym = str(pos.symbol)
                pos_vol = int(round(float(pos.volume)))
                pos_ticket = int(pos.ticket)
                take = min(remaining, pos_vol)
                if take <= 0:
                    continue

                is_std_lot = (not pos_sym.endswith("F")) and (not is_b3_unit_lot_asset(pos_sym))
                if is_std_lot and take < pos_vol and (take % 100 != 0):
                    leg_take = (take // 100) * 100
                    std_lot_blocked = True
                    if leg_take <= 0:
                        continue
                    execution_plan.append((pos_sym, leg_take, pos_ticket))
                    remaining -= leg_take
                elif pos_sym.endswith("F") and take > 99:
                    rem_frac = take
                    while rem_frac > 0:
                        chunk = min(99, rem_frac)
                        execution_plan.append((pos_sym, chunk, pos_ticket))
                        rem_frac -= chunk
                    remaining -= take
                else:
                    execution_plan.append((pos_sym, take, pos_ticket))
                    remaining -= take

            if not execution_plan:
                msg = (
                    "Venda parcial fracionária incompatível com custódia em lote padrão B3 (múltiplo de 100 exigido)"
                    if std_lot_blocked
                    else f"Sem volume elegível para venda em {normalize_b3_ticker(ticker) or ticker}"
                )
                logger.warning("Venda rejeitada MT5 — %s", msg)
                return Order(
                    ticker=str(matching[0].symbol),
                    order_type=OrderType.SELL,
                    quantity=quantity,
                    price=rounded_price,
                    status=OrderStatus.REJECTED,
                    timestamp=now,
                    comment=msg,
                )
        else:
            for leg_sym, leg_qty in split_b3_order_legs(ticker, quantity):
                execution_plan.append((leg_sym, leg_qty, None))

        total_filled = 0
        total_value = 0.0
        last_ticket: int | None = None
        any_pending = False
        comments: list[str] = []
        if std_lot_blocked:
            comments.append(
                "Venda parcial fracionária incompatível com custódia em lote padrão B3 (múltiplo de 100 exigido)"
            )

        for leg_symbol, leg_qty, pos_ticket in execution_plan:
            st, filled_qty, exec_px, tkt, msg = self._execute_leg(
                symbol=leg_symbol,
                quantity=leg_qty,
                price=rounded_price,
                order_type=OrderType.SELL,
                position_ticket=pos_ticket,
                align_to_book=True,
            )
            if tkt is not None:
                last_ticket = tkt
            if msg:
                comments.append(msg)
            if st == OrderStatus.FILLED and filled_qty > 0:
                total_filled += filled_qty
                total_value += filled_qty * exec_px
            elif st == OrderStatus.PENDING:
                any_pending = True

        out_symbol = (
            execution_plan[0][0]
            if len(execution_plan) == 1
            else normalize_b3_ticker(ticker)
        )
        if total_filled > 0:
            avg_px = round(total_value / total_filled, 2)
            return Order(
                ticker=out_symbol,
                order_type=OrderType.SELL,
                quantity=total_filled,
                price=avg_px,
                status=OrderStatus.FILLED,
                ticket=last_ticket,
                timestamp=now,
                comment=" | ".join(comments),
            )

        if any_pending:
            return Order(
                ticker=out_symbol,
                order_type=OrderType.SELL,
                quantity=quantity,
                price=rounded_price,
                status=OrderStatus.PENDING,
                ticket=last_ticket,
                timestamp=now,
                comment=" | ".join(comments),
            )

        return Order(
            ticker=out_symbol,
            order_type=OrderType.SELL,
            quantity=quantity,
            price=rounded_price,
            status=OrderStatus.REJECTED,
            ticket=last_ticket,
            timestamp=now,
            comment=" | ".join(comments) or "Venda rejeitada",
        )

    def emergency_sell(self, ticker: str) -> Order:
        """Venda de emergência — liquida todas as posições long abertas com buffer comercializável."""
        now = datetime.now(tz=BRT)
        norm = normalize_b3_ticker(ticker) or ticker.strip().upper()

        try:
            self._ensure_connected()
        except ConnectionError as exc:
            return Order(
                ticker=norm,
                order_type=OrderType.SELL,
                quantity=0,
                price=0.0,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=f"Sem conexão MT5: {exc}",
            )

        matching = self._get_matching_mt5_positions(norm)
        if not matching:
            return Order(
                ticker=norm,
                order_type=OrderType.SELL,
                quantity=0,
                price=0.0,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment=f"Nenhuma posição aberta em {norm}",
            )

        max_slip = float(getattr(settings, "MAX_SLIPPAGE_PERCENT", 0.003) or 0.003)
        total_filled = 0
        total_value = 0.0
        last_ticket: int | None = None
        comments: list[str] = []

        for pos in matching:
            pos_symbol = str(pos.symbol)
            pos_qty = int(round(float(pos.volume)))
            if pos_qty <= 0:
                continue

            with self._lock:
                tick = mt5.symbol_info_tick(pos_symbol)
            bid = _safe_float(getattr(tick, "bid", 0.0), 0.0) if tick is not None else 0.0
            if bid <= 0:
                bid = _safe_float(
                    getattr(pos, "price_current", 0.0) or getattr(pos, "price_open", 0.0),
                    0.0,
                )
            if bid <= 0:
                comments.append(f"Cotação indisponível para {pos_symbol}")
                continue

            sell_px = max(0.01, round(bid * (1.0 - max(0.0, max_slip)), 2))

            logger.warning(
                "VENDA EMERGENCIAL MT5: %s | %d ações @ R$%.2f (bid=R$%.2f, ticket=%s)",
                pos_symbol,
                pos_qty,
                sell_px,
                bid,
                pos.ticket,
            )

            chunks = [pos_qty]
            if pos_symbol.endswith("F") and pos_qty > 99:
                chunks = []
                rem = pos_qty
                while rem > 0:
                    c = min(99, rem)
                    chunks.append(c)
                    rem -= c

            for chunk_qty in chunks:
                st, filled_qty, exec_px, tkt, msg = self._execute_leg(
                    symbol=pos_symbol,
                    quantity=chunk_qty,
                    price=sell_px,
                    order_type=OrderType.SELL,
                    position_ticket=int(pos.ticket),
                    align_to_book=False,
                )
                if tkt is not None:
                    last_ticket = tkt
                if msg:
                    comments.append(msg)
                if st == OrderStatus.FILLED and filled_qty > 0:
                    total_filled += filled_qty
                    total_value += filled_qty * exec_px

        out_symbol = matching[0].symbol if len(matching) == 1 else norm
        if total_filled > 0:
            avg_px = round(total_value / total_filled, 2)
            return Order(
                ticker=out_symbol,
                order_type=OrderType.SELL,
                quantity=total_filled,
                price=avg_px,
                status=OrderStatus.FILLED,
                ticket=last_ticket,
                timestamp=now,
                comment=" | ".join(comments),
            )

        return Order(
            ticker=out_symbol,
            order_type=OrderType.SELL,
            quantity=0,
            price=0.0,
            status=OrderStatus.REJECTED,
            ticket=last_ticket,
            timestamp=now,
            comment=" | ".join(comments) or f"Falha na liquidação emergencial de {norm}",
        )

    def get_positions(self) -> list[Position]:
        """Retorna todas as posições long abertas deste robô via MT5 ou levanta ConnectionError."""
        if not self._connected or mt5 is None:
            raise ConnectionError("MT5 desconectado ou indisponível")

        with self._lock:
            mt5_positions = mt5.positions_get()
        if mt5_positions is None:
            raise ConnectionError("MT5 desconectado ou indisponível")

        result: list[Position] = []
        for pos in mt5_positions:
            if not self._is_long_bot_position(pos):
                continue

            with self._lock:
                tick = mt5.symbol_info_tick(pos.symbol)
            tick_bid = _safe_float(getattr(tick, "bid", 0.0), 0.0) if tick is not None else 0.0
            current_price = tick_bid if tick_bid > 0.0 else float(pos.price_current)

            try:
                ts = datetime.fromtimestamp(pos.time, tz=BRT)
            except (OSError, ValueError):
                ts = datetime.now(tz=BRT)

            result.append(
                Position(
                    ticker=str(pos.symbol),
                    quantity=int(round(float(pos.volume))),
                    entry_price=float(pos.price_open),
                    current_price=current_price,
                    stop_loss=float(pos.sl),
                    ticket=int(pos.ticket),
                    timestamp=ts,
                )
            )
        return result

    def get_position(self, ticker: str) -> Optional[Position]:
        """Retorna a posição consolidada de um ativo específico."""
        norm = normalize_b3_ticker(ticker)
        if not norm:
            return None

        matches = [
            pos
            for pos in self.get_positions()
            if normalize_b3_ticker(pos.ticker) == norm
        ]
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]

        total_qty = sum(p.quantity for p in matches)
        if total_qty <= 0:
            return None
        avg_entry = sum(p.entry_price * p.quantity for p in matches) / total_qty
        avg_curr = sum(p.current_price * p.quantity for p in matches) / total_qty
        positive_sls = [p.stop_loss for p in matches if p.stop_loss > 0]
        sl = min(positive_sls) if positive_sls else 0.0
        earliest_ts = min(p.entry_time for p in matches)
        return Position(
            ticker=norm,
            quantity=total_qty,
            entry_price=round(avg_entry, 2),
            current_price=round(avg_curr, 2),
            stop_loss=round(sl, 2),
            ticket=matches[0].ticket,
            timestamp=earliest_ts,
        )

    def modify_stop_loss(self, ticker: str, new_sl: float) -> bool:
        """Modifica o stop-loss de todas as posições abertas do ativo via TRADE_ACTION_SLTP."""
        if new_sl <= 0:
            logger.warning("Stop-loss inválido para %s: R$%.2f", ticker, new_sl)
            return False

        try:
            self._ensure_connected()
        except ConnectionError:
            logger.error("Sem conexão para modificar SL de %s", ticker)
            return False

        matching = self._get_matching_mt5_positions(ticker)
        if not matching:
            logger.warning("Posição %s não encontrada no MT5 para modificar SL", ticker)
            return False

        all_ok = True
        for pos in matching:
            pos_symbol = str(pos.symbol)
            digits = self._get_symbol_digits(pos_symbol)
            with self._lock:
                sym_info = mt5.symbol_info(pos_symbol)
                tick = mt5.symbol_info_tick(pos_symbol)

            point = _safe_float(getattr(sym_info, "point", 0.0), 0.0) if sym_info is not None else 0.0
            if point <= 0.0:
                point = 10.0 ** (-digits) if digits > 0 else 0.01
            stops_level = (
                _safe_int(
                    getattr(sym_info, "trade_stops_level", getattr(sym_info, "stops_level", 0)),
                    0,
                )
                if sym_info is not None
                else 0
            )
            leg_bid = _safe_float(getattr(tick, "bid", 0.0), 0.0) if tick is not None else 0.0
            if leg_bid <= 0.0 and sym_info is not None:
                leg_bid = _safe_float(getattr(sym_info, "bid", 0.0), 0.0)

            rounded_sl = round(float(new_sl), digits)
            if leg_bid > 0.0:
                max_valid_sl = round(leg_bid - max(point, stops_level * point), digits)
                if max_valid_sl > 0.0 and rounded_sl > max_valid_sl:
                    rounded_sl = max_valid_sl

            request: dict[str, Any] = {
                "action": _safe_int(getattr(mt5, "TRADE_ACTION_SLTP", 6), 6),
                "symbol": pos_symbol,
                "position": int(pos.ticket),
                "sl": rounded_sl,
                "tp": _safe_float(getattr(pos, "tp", 0.0), 0.0),
                "magic": self.MAGIC_NUMBER,
                "comment": "Cortex SL_MODIFY",
            }

            with self._lock:
                result = mt5.order_send(request)
                err = mt5.last_error() if result is None else None

            if result is None:
                logger.error("Falha ao modificar SL de %s: %s", pos_symbol, err)
                all_ok = False
                continue

            if result.retcode in (10009, 10025):
                logger.info(
                    "Stop-loss modificado MT5: %s (ticket=%s) | R$%.2f → R$%.2f",
                    pos_symbol,
                    pos.ticket,
                    pos.sl,
                    rounded_sl,
                )
            else:
                msg = _retcode_to_message(result.retcode)
                logger.warning(
                    "Falha ao modificar SL de %s — retcode %d: %s",
                    pos_symbol,
                    result.retcode,
                    msg,
                )
                all_ok = False

        return all_ok

    @property
    def is_connected(self) -> bool:
        """Indica se o broker MT5 está conectado."""
        return self._connected

    def __repr__(self) -> str:
        """Representação textual do broker MT5."""
        return (
            f"MT5Broker(login={self._login}, "
            f"server='{self._server}', "
            f"connected={self._connected})"
        )
