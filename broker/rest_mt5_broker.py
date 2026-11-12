"""
Broker MetaTrader 5 do Projeto Córtex (Via Bridge REST).

Implementa a interface BrokerBase comunicando-se de forma autenticada com a API REST
do Bridge MT5. Suporta ordens multi-pernas B3, preenchimento dinâmico, reconciliação
pós-timeout e modificação real de Stop-Loss.
"""

from __future__ import annotations

from datetime import datetime
import logging
import threading
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

from broker.base import BrokerBase, Order, OrderStatus, OrderType, Position
from config.settings import settings
from utils.helpers import (
    format_b3_ticker_for_quantity,
    normalize_b3_ticker,
    split_b3_order_legs,
)

logger = logging.getLogger("cortex.broker.rest_mt5")
BRT = ZoneInfo("America/Sao_Paulo")


class RestMT5Broker(BrokerBase):
    """Broker MT5 que se comunica via HTTP autenticado com o serviço Bridge."""

    MAGIC_NUMBER: int = settings.mt5_magic

    def __init__(self, base_url: str | None = None) -> None:
        resolved = (
            base_url
            or getattr(settings, "MT5_BRIDGE_URL", "")
            or "http://127.0.0.1:5000"
        )
        self._base_url: str = resolved.strip().rstrip("/")
        self._connected: bool = False
        self._lock = threading.Lock()
        self._session = requests.Session()
        self.MAGIC_NUMBER = int(getattr(settings, "MT5_MAGIC", 234000))

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        api_key = getattr(settings, "BRIDGE_API_KEY", "").strip()
        if api_key:
            headers["X-Bridge-Api-Key"] = api_key
        return headers

    def _api_call(
        self,
        endpoint: str,
        data: dict[str, Any] | None = None,
        method: str = "POST",
        raise_on_timeout: bool = False,
    ) -> Any:
        url = f"{self._base_url}/{endpoint.lstrip('/')}"
        try:
            if method.upper() == "GET":
                resp = self._session.get(url, headers=self._headers(), timeout=10)
            else:
                resp = self._session.post(
                    url,
                    json=data if data is not None else {},
                    headers=self._headers(),
                    timeout=10,
                )
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            logger.error("Timeout na chamada ao MT5 Bridge (%s %s)", method, endpoint)
            if raise_on_timeout:
                raise
            return None
        except Exception as exc:
            logger.error("API Bridge falhou (%s %s): %s", method, endpoint, exc)
            return None

    @staticmethod
    def _fractional_ticker(ticker: str, quantity: int = 1) -> str:
        """Retorna símbolo B3 adequado respeitando ETFs, BDRs, Units e lotes."""
        return format_b3_ticker_for_quantity(ticker, quantity=quantity)

    @staticmethod
    def _compute_deviation_points(price: float) -> int:
        """Calcula desvio máximo em pontos (centavos na B3) com base em MAX_SLIPPAGE_PERCENT."""
        max_slip = float(getattr(settings, "MAX_SLIPPAGE_PERCENT", 0.003) or 0.003)
        if max_slip <= 0:
            return 5
        pts = int(round((max(0.01, float(price)) * max_slip) / 0.01))
        return max(2, min(50, pts))

    def get_tick(self, ticker: str) -> dict[str, Any] | None:
        """Obtém cotação em tempo real (bid/ask/last/spread_pct) via Bridge."""
        base = normalize_b3_ticker(ticker) or ticker.strip().upper()
        unit_sym = format_b3_ticker_for_quantity(base, 1)
        candidates = [base] if unit_sym == base else [base, unit_sym]
        for sym in candidates:
            res = self._api_call(f"tick/{sym}", method="GET")
            if isinstance(res, dict) and (
                float(res.get("last", 0.0) or 0.0) > 0
                or float(res.get("bid", 0.0) or 0.0) > 0
            ):
                return res
        return None

    def get_rates(
        self,
        ticker: str,
        timeframe: str = "D1",
        count: int = 120,
    ) -> list[dict[str, Any]] | None:
        """Obtém candles OHLCV históricos/intraday direto da B3 via Bridge."""
        base = normalize_b3_ticker(ticker) or ticker.strip().upper()
        unit_sym = format_b3_ticker_for_quantity(base, 1)
        candidates = [base] if unit_sym == base else [base, unit_sym]
        for sym in candidates:
            res = self._api_call(
                f"rates/{sym}?timeframe={timeframe}&count={int(count)}",
                method="GET",
            )
            if isinstance(res, list) and len(res) > 0:
                return res
        return None

    def _detect_filling_mode(self, symbol: str) -> int:
        """Detecta o type_filling suportado pelo símbolo via /symbol_info/{symbol}."""
        info = self._api_call(f"symbol_info/{symbol}", method="GET")
        if isinstance(info, dict):
            filling = int(info.get("filling_mode", 0) or 0)
            if filling == 0 or (filling & 4):
                return 2  # ORDER_FILLING_RETURN (padrão B3)
            if filling & 2:
                return 1  # ORDER_FILLING_IOC
            if filling & 1:
                return 0  # ORDER_FILLING_FOK
        return 2  # ORDER_FILLING_RETURN

    def _get_raw_matching_positions(self, ticker: str) -> list[dict[str, Any]]:
        """Busca posições brutas da Bridge filtradas por magic number e ticker-base."""
        norm = normalize_b3_ticker(ticker)
        if not norm:
            return []
        res = self._api_call("positions", method="GET")
        if not isinstance(res, list):
            return []
        return [
            p
            for p in res
            if isinstance(p, dict)
            and p.get("magic") == self.MAGIC_NUMBER
            and normalize_b3_ticker(str(p.get("symbol", ""))) == norm
        ]

    def _get_open_symbol_qty(self, symbol: str) -> tuple[int, float]:
        """Obtém quantidade aberta e preço médio atual de um símbolo específico."""
        res = self._api_call("positions", method="GET")
        if not isinstance(res, list):
            return 0, 0.0
        qty = 0
        val = 0.0
        for p in res:
            if (
                isinstance(p, dict)
                and p.get("magic") == self.MAGIC_NUMBER
                and str(p.get("symbol", "")).upper() == symbol.upper()
            ):
                v = int(round(float(p.get("volume", 0) or 0)))
                px = float(p.get("price_open", 0.0) or 0.0)
                qty += v
                val += v * px
        return qty, (round(val / qty, 2) if qty > 0 else 0.0)

    def connect(self) -> bool:
        """Conecta ao Bridge MT5 sem trafegar senhas em texto plano."""
        with self._lock:
            res = self._api_call("connect", {})
            if isinstance(res, dict) and res.get("status") == "connected":
                self._connected = True
                logger.info(
                    "Conectado ao MT5 Bridge. Saldo: R$%.2f",
                    float(res.get("balance", 0.0) or 0.0),
                )
                return True
            self._connected = False
            return False

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
            self._session.close()
            self._session = requests.Session()
        logger.info("RestMT5Broker desconectado")

    def get_balance(self) -> float:
        res = self._api_call("account", method="GET")
        if isinstance(res, dict):
            return round(float(res.get("balance", 0.0) or 0.0), 2)
        return 0.0

    def get_equity(self) -> float:
        res = self._api_call("account", method="GET")
        if isinstance(res, dict):
            return round(float(res.get("equity", 0.0) or 0.0), 2)
        return 0.0

    def _execute_leg(
        self,
        symbol: str,
        quantity: int,
        price: float,
        order_type: OrderType,
        stop_loss: float = 0.0,
        position_ticket: int | None = None,
    ) -> tuple[OrderStatus, int, float, int | None, str]:
        """Executa uma perna via Bridge com reconciliação automática em caso de timeout."""
        rounded_price = round(float(price), 2)
        rounded_sl = round(float(stop_loss), 2) if stop_loss and stop_loss > 0 else 0.0
        filling_mode = self._detect_filling_mode(symbol)

        if order_type == OrderType.BUY:
            tick = self._api_call(f"tick/{symbol}", method="GET")
            if isinstance(tick, dict):
                bid = float(tick.get("bid", 0.0) or 0.0)
                ask = float(tick.get("ask", 0.0) or 0.0)
                max_spread = float(getattr(settings, "MAX_SPREAD_PERCENT", 0.008) or 0.0)
                if bid > 0.0 and ask > bid and max_spread > 0.0:
                    spread_pct = (ask - bid) / bid
                    if spread_pct > max_spread:
                        msg = (
                            f"Spread excessivo em {symbol}: {spread_pct * 100:.2f}% > "
                            f"máx {max_spread * 100:.2f}%"
                        )
                        logger.warning("Compra bloqueada pelo Spread Guard — %s", msg)
                        return OrderStatus.REJECTED, 0, rounded_price, None, msg
                if ask > 0.0 and rounded_price > 0.0:
                    if abs(ask - rounded_price) / rounded_price <= max(max_spread, 0.01):
                        rounded_price = round(ask, 2)

        deviation_pts = self._compute_deviation_points(rounded_price)

        req: dict[str, Any] = {
            "action": 1,
            "symbol": symbol,
            "volume": float(quantity),
            "type": 0 if order_type == OrderType.BUY else 1,
            "price": rounded_price,
            "sl": rounded_sl if order_type == OrderType.BUY else 0.0,
            "tp": 0.0,
            "deviation": deviation_pts,
            "magic": self.MAGIC_NUMBER,
            "comment": "Cortex BUY" if order_type == OrderType.BUY else "Cortex SELL",
            "type_time": 0,
            "type_filling": filling_mode,
        }
        if position_ticket and position_ticket > 0:
            req["position"] = int(position_ticket)

        if order_type == OrderType.BUY:
            check = self._api_call("order_check", req)
            if not isinstance(check, dict) or check.get("retcode") != 0:
                comment = (
                    check.get("comment", "order_check falhou")
                    if isinstance(check, dict)
                    else "Bridge indisponível em order_check"
                )
                return OrderStatus.REJECTED, 0, rounded_price, None, str(comment)

        before_qty, _ = self._get_open_symbol_qty(symbol)

        try:
            res = self._api_call("order_send", req, raise_on_timeout=True)
        except requests.exceptions.Timeout:
            logger.warning(
                "Reconciliando posições em %s após timeout no envio da ordem...",
                symbol,
            )
            after_qty, open_px = self._get_open_symbol_qty(symbol)
            if order_type == OrderType.BUY and after_qty > before_qty:
                delta = after_qty - before_qty
                return (
                    OrderStatus.FILLED,
                    delta,
                    open_px or rounded_price,
                    None,
                    "Ordem reconciliada após timeout HTTP",
                )
            if order_type == OrderType.SELL and after_qty < before_qty:
                delta = before_qty - after_qty
                return (
                    OrderStatus.FILLED,
                    delta,
                    rounded_price,
                    None,
                    "Venda reconciliada após timeout HTTP",
                )
            return (
                OrderStatus.REJECTED,
                0,
                rounded_price,
                None,
                "Timeout HTTP sem execução confirmada em /positions",
            )

        if not isinstance(res, dict):
            return OrderStatus.REJECTED, 0, rounded_price, None, "Sem resposta em order_send"

        ret = int(res.get("retcode", -1))
        ticket = int(res.get("order", 0) or 0) or None
        exec_price = float(res.get("price", 0.0) or 0.0) or rounded_price
        comment = str(res.get("comment", ""))

        if ret == 10009:
            raw_vol = float(res.get("volume", 0.0) or 0.0)
            filled = int(round(raw_vol)) if raw_vol > 0 else quantity
            return OrderStatus.FILLED, filled, exec_price, ticket, comment

        if ret == 10010:
            filled = int(round(float(res.get("volume", 0.0) or 0.0)))
            if filled > 0:
                return OrderStatus.FILLED, filled, exec_price, ticket, f"Parcial: {comment}"
            return OrderStatus.REJECTED, 0, rounded_price, ticket, "Parcial com volume zero"

        if ret == 10008:
            deal = int(res.get("deal", 0) or 0)
            raw_vol = float(res.get("volume", 0.0) or 0.0)
            if deal > 0 and raw_vol > 0:
                return OrderStatus.FILLED, int(round(raw_vol)), exec_price, ticket, comment
            after_qty, open_px = self._get_open_symbol_qty(symbol)
            if order_type == OrderType.BUY and after_qty > before_qty:
                return (
                    OrderStatus.FILLED,
                    after_qty - before_qty,
                    open_px or exec_price,
                    ticket,
                    comment,
                )
            if order_type == OrderType.SELL and after_qty < before_qty:
                return (
                    OrderStatus.FILLED,
                    before_qty - after_qty,
                    exec_price,
                    ticket,
                    comment,
                )
            return OrderStatus.PENDING, 0, rounded_price, ticket, f"Pendente: {comment}"

        return OrderStatus.REJECTED, 0, rounded_price, ticket, f"Retcode {ret}: {comment}"

    def buy(self, ticker: str, quantity: int, price: float, stop_loss: float) -> Order:
        now = datetime.now(tz=BRT)
        rounded_price = round(float(price), 2)
        rounded_sl = round(float(stop_loss), 2) if stop_loss and stop_loss > 0 else 0.0
        primary = self._fractional_ticker(ticker, max(1, int(quantity)))

        if quantity <= 0 or rounded_price <= 0:
            return Order(
                primary,
                OrderType.BUY,
                max(0, int(quantity)),
                max(0.0, rounded_price),
                rounded_sl,
                OrderStatus.REJECTED,
                timestamp=now,
                comment="Quantidade e preço devem ser maiores que zero",
            )

        legs = split_b3_order_legs(ticker, quantity)
        if not legs:
            return Order(
                primary,
                OrderType.BUY,
                quantity,
                rounded_price,
                rounded_sl,
                OrderStatus.REJECTED,
                timestamp=now,
                comment="Ticker inválido",
            )

        total_filled = 0
        total_val = 0.0
        last_ticket: int | None = None
        any_pending = False
        comments: list[str] = []

        for leg_sym, leg_qty in legs:
            st, filled, px, tkt, msg = self._execute_leg(
                symbol=leg_sym,
                quantity=leg_qty,
                price=rounded_price,
                order_type=OrderType.BUY,
                stop_loss=rounded_sl,
            )
            if tkt is not None:
                last_ticket = tkt
            if msg:
                comments.append(msg)
            if st == OrderStatus.FILLED and filled > 0:
                total_filled += filled
                total_val += filled * px
            elif st == OrderStatus.PENDING:
                any_pending = True

        out_sym = legs[0][0] if len(legs) == 1 else normalize_b3_ticker(ticker)
        if total_filled > 0:
            return Order(
                out_sym,
                OrderType.BUY,
                total_filled,
                round(total_val / total_filled, 2),
                rounded_sl,
                OrderStatus.FILLED,
                last_ticket,
                now,
                " | ".join(comments),
            )
        if any_pending:
            return Order(
                out_sym,
                OrderType.BUY,
                quantity,
                rounded_price,
                rounded_sl,
                OrderStatus.PENDING,
                last_ticket,
                now,
                " | ".join(comments),
            )
        return Order(
            out_sym,
            OrderType.BUY,
            quantity,
            rounded_price,
            rounded_sl,
            OrderStatus.REJECTED,
            last_ticket,
            now,
            " | ".join(comments) or "Compra rejeitada",
        )

    def sell(self, ticker: str, quantity: int, price: float) -> Order:
        now = datetime.now(tz=BRT)
        rounded_price = round(float(price), 2)
        primary = self._fractional_ticker(ticker, max(1, int(quantity)))

        if quantity <= 0 or rounded_price <= 0:
            return Order(
                primary,
                OrderType.SELL,
                max(0, int(quantity)),
                max(0.0, rounded_price),
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment="Quantidade e preço devem ser maiores que zero",
            )

        plan: list[tuple[str, int, int | None]] = []
        raw_matching = self._get_raw_matching_positions(ticker)
        if raw_matching:
            remaining = int(quantity)
            for p in raw_matching:
                if remaining <= 0:
                    break
                p_sym = str(p.get("symbol", ""))
                p_vol = int(round(float(p.get("volume", 0) or 0)))
                p_tkt = int(p.get("ticket", 0) or 0) or None
                take = min(remaining, p_vol)
                if take <= 0:
                    continue
                if not p_sym.endswith("F") and take < 100 and take != p_vol:
                    for sub_sym, sub_qty in split_b3_order_legs(ticker, take):
                        plan.append((sub_sym, sub_qty, None))
                elif p_sym.endswith("F") and take > 99:
                    rem_frac = take
                    while rem_frac > 0:
                        c = min(99, rem_frac)
                        plan.append((p_sym, c, p_tkt))
                        rem_frac -= c
                else:
                    plan.append((p_sym, take, p_tkt))
                remaining -= take
            if remaining > 0:
                for sub_sym, sub_qty in split_b3_order_legs(ticker, remaining):
                    plan.append((sub_sym, sub_qty, None))
        else:
            for leg_sym, leg_qty in split_b3_order_legs(ticker, quantity):
                plan.append((leg_sym, leg_qty, None))

        total_filled = 0
        total_val = 0.0
        last_ticket: int | None = None
        any_pending = False
        comments: list[str] = []

        for leg_sym, leg_qty, pos_tkt in plan:
            st, filled, px, tkt, msg = self._execute_leg(
                symbol=leg_sym,
                quantity=leg_qty,
                price=rounded_price,
                order_type=OrderType.SELL,
                position_ticket=pos_tkt,
            )
            if tkt is not None:
                last_ticket = tkt
            if msg:
                comments.append(msg)
            if st == OrderStatus.FILLED and filled > 0:
                total_filled += filled
                total_val += filled * px
            elif st == OrderStatus.PENDING:
                any_pending = True

        out_sym = plan[0][0] if len(plan) == 1 else normalize_b3_ticker(ticker)
        if total_filled > 0:
            return Order(
                out_sym,
                OrderType.SELL,
                total_filled,
                round(total_val / total_filled, 2),
                status=OrderStatus.FILLED,
                ticket=last_ticket,
                timestamp=now,
                comment=" | ".join(comments),
            )
        if any_pending:
            return Order(
                out_sym,
                OrderType.SELL,
                quantity,
                rounded_price,
                status=OrderStatus.PENDING,
                ticket=last_ticket,
                timestamp=now,
                comment=" | ".join(comments),
            )
        return Order(
            out_sym,
            OrderType.SELL,
            quantity,
            rounded_price,
            status=OrderStatus.REJECTED,
            ticket=last_ticket,
            timestamp=now,
            comment=" | ".join(comments) or "Venda rejeitada",
        )

    def emergency_sell(self, ticker: str) -> Order:
        """Venda de emergência — liquida todas as posições abertas do ativo."""
        now = datetime.now(tz=BRT)
        norm = normalize_b3_ticker(ticker) or ticker.strip().upper()
        raw_matching = self._get_raw_matching_positions(norm)
        if not raw_matching:
            logger.warning("emergency_sell: sem posição aberta para %s", norm)
            return Order(
                norm,
                OrderType.SELL,
                0,
                0.0,
                status=OrderStatus.REJECTED,
                timestamp=now,
                comment="Sem posição aberta",
            )

        total_filled = 0
        total_val = 0.0
        last_ticket: int | None = None
        comments: list[str] = []

        for p in raw_matching:
            sym = str(p.get("symbol", ""))
            qty = int(round(float(p.get("volume", 0) or 0)))
            tkt = int(p.get("ticket", 0) or 0) or None
            if qty <= 0:
                continue

            tick = self._api_call(f"tick/{sym}", method="GET")
            bid = float(tick.get("bid", 0.0) or 0.0) if isinstance(tick, dict) else 0.0
            if bid <= 0:
                bid = float(p.get("price_current", 0.0) or p.get("price_open", 0.0) or 0.0)
            if bid <= 0:
                comments.append(f"Sem cotação para {sym}")
                continue

            chunks = [qty]
            if sym.endswith("F") and qty > 99:
                chunks = []
                rem = qty
                while rem > 0:
                    c = min(99, rem)
                    chunks.append(c)
                    rem -= c

            for chunk_qty in chunks:
                st, filled, px, ord_tkt, msg = self._execute_leg(
                    symbol=sym,
                    quantity=chunk_qty,
                    price=bid,
                    order_type=OrderType.SELL,
                    position_ticket=tkt,
                )
                if ord_tkt is not None:
                    last_ticket = ord_tkt
                if msg:
                    comments.append(msg)
                if st == OrderStatus.FILLED and filled > 0:
                    total_filled += filled
                    total_val += filled * px

        out_sym = str(raw_matching[0].get("symbol", norm)) if len(raw_matching) == 1 else norm
        if total_filled > 0:
            return Order(
                out_sym,
                OrderType.SELL,
                total_filled,
                round(total_val / total_filled, 2),
                status=OrderStatus.FILLED,
                ticket=last_ticket,
                timestamp=now,
                comment=" | ".join(comments),
            )

        return Order(
            out_sym,
            OrderType.SELL,
            0,
            0.0,
            status=OrderStatus.REJECTED,
            ticket=last_ticket,
            timestamp=now,
            comment=" | ".join(comments) or "Falha na venda emergencial",
        )

    def get_positions(self) -> list[Position]:
        res = self._api_call("positions", method="GET")
        if not res:
            return []
        if not isinstance(res, list):
            logger.warning("get_positions: resposta inesperada do bridge: %s", type(res))
            return []

        positions: list[Position] = []
        for p in res:
            if not isinstance(p, dict) or p.get("magic") != self.MAGIC_NUMBER:
                continue

            raw_time = p.get("time")
            ts: datetime
            if isinstance(raw_time, (int, float)) and raw_time > 0:
                try:
                    ts = datetime.fromtimestamp(raw_time, tz=BRT)
                except (OSError, ValueError):
                    ts = datetime.now(tz=BRT)
            elif isinstance(raw_time, str) and raw_time:
                try:
                    ts = datetime.fromisoformat(raw_time)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=BRT)
                except ValueError:
                    ts = datetime.now(tz=BRT)
            else:
                ts = datetime.now(tz=BRT)

            positions.append(
                Position(
                    ticker=str(p.get("symbol", "")),
                    quantity=int(round(float(p.get("volume", 0) or 0))),
                    entry_price=float(p.get("price_open", 0.0) or 0.0),
                    current_price=float(p.get("price_current", 0.0) or 0.0),
                    stop_loss=float(p.get("sl", 0.0) or 0.0),
                    ticket=int(p.get("ticket", 0) or 0),
                    timestamp=ts,
                )
            )
        return positions

    def get_position(self, ticker: str) -> Optional[Position]:
        norm = normalize_b3_ticker(ticker)
        if not norm:
            return None
        matches = [
            p for p in self.get_positions() if normalize_b3_ticker(p.ticker) == norm
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
        pos_sls = [p.stop_loss for p in matches if p.stop_loss > 0]
        return Position(
            ticker=norm,
            quantity=total_qty,
            entry_price=round(avg_entry, 2),
            current_price=round(avg_curr, 2),
            stop_loss=round(min(pos_sls), 2) if pos_sls else 0.0,
            ticket=matches[0].ticket,
            timestamp=min(p.entry_time for p in matches),
        )

    def modify_stop_loss(self, ticker: str, new_sl: float) -> bool:
        """Modifica o Stop-Loss de todas as posições abertas do ativo via POST /modify_sl."""
        if new_sl <= 0:
            logger.warning("Stop-loss inválido para %s: R$%.2f", ticker, new_sl)
            return False

        raw_matching = self._get_raw_matching_positions(ticker)
        if not raw_matching:
            logger.warning("modify_stop_loss: nenhuma posição aberta para %s", ticker)
            return False

        rounded_sl = round(float(new_sl), 2)
        all_ok = True
        for p in raw_matching:
            ticket = int(p.get("ticket", 0) or 0)
            symbol = str(p.get("symbol", ""))
            if ticket <= 0 or not symbol:
                all_ok = False
                continue

            payload = {
                "position": ticket,
                "symbol": symbol,
                "sl": rounded_sl,
                "tp": float(p.get("tp", 0.0) or 0.0),
                "magic": self.MAGIC_NUMBER,
            }
            res = self._api_call("modify_sl", payload, method="POST")
            if isinstance(res, dict) and res.get("retcode") in (10009, 10025):
                logger.info(
                    "Stop-loss atualizado via Bridge: %s (ticket=%d) → R$%.2f",
                    symbol,
                    ticket,
                    rounded_sl,
                )
            else:
                logger.warning(
                    "Falha ao atualizar SL via Bridge para %s (ticket=%d): %s",
                    symbol,
                    ticket,
                    res,
                )
                all_ok = False

        return all_ok

    @property
    def is_connected(self) -> bool:
        return self._connected
