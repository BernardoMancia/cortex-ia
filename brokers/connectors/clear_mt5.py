from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import requests

from brokers.b3_execution import execute_b3_legs, plan_safe_b3_sell_legs
from brokers.base import BrokerConnector
from config.settings import BRT, settings
from domain.b3_rules import get_brt_now, normalize_b3_ticker
from domain.models import (
    ConnectorImplementationStatus,
    MarketQuote,
    Order,
    OrderSide,
    OrderStatus,
    Position,
)
from observability.logger import get_logger

logger = get_logger("brokers.clear_mt5")


class ClearMT5Connector(BrokerConnector):
    connector_type: str = "clear_mt5"
    display_name: str = "Clear Investimentos — B3 (MT5)"
    implementation_status: ConnectorImplementationStatus = ConnectorImplementationStatus.READY

    def __init__(
        self,
        bridge_url: str | None = None,
        bridge_api_key: str | None = None,
        account_login: str = "",
        server: str = "",
        wallet_id: int = 0,
        timeout: float = 8.0,
    ) -> None:
        raw_url = (bridge_url if bridge_url is not None else settings.MT5_BRIDGE_URL).strip().rstrip("/")
        parsed = urlparse(raw_url)
        if parsed.scheme.lower() in ("http", "https") and parsed.hostname:
            self.bridge_url = raw_url
        else:
            self.bridge_url = ""
        self.bridge_api_key = (
            bridge_api_key if bridge_api_key is not None else settings.BRIDGE_API_KEY
        ).strip()
        self.expected_login = str(account_login or "").strip()
        self.expected_server = str(server or settings.MT5_SERVER or "ClearInvestimentos-CLEAR").strip()
        self.wallet_id = int(wallet_id)
        self.timeout = max(2.0, float(timeout))
        self._session = requests.Session()
        if self.bridge_api_key:
            self._session.headers.update({"X-Bridge-Key": self.bridge_api_key})

    def _request_json(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, dict[str, Any] | None, str]:
        if not self.bridge_url:
            return 0, None, "URL da MT5 Bridge não configurada para esta carteira."
        if not self.bridge_api_key:
            return 0, None, "Chave X-Bridge-Key ausente para autenticação na MT5 Bridge."
        url = f"{self.bridge_url}/{path.lstrip('/')}"
        wait_t = self.timeout if timeout is None else max(1.5, float(timeout))
        try:
            resp = self._session.request(
                method=method.upper(),
                url=url,
                json=json_body,
                params=params,
                timeout=wait_t,
                allow_redirects=False,
            )
            try:
                payload = resp.json()
                data = payload if isinstance(payload, dict) else None
            except ValueError:
                data = None
            if resp.status_code >= 400:
                detail = ""
                if data is not None:
                    detail = str(data.get("detail") or data.get("message") or data.get("error") or "")
                return resp.status_code, data, detail or f"HTTP {resp.status_code} na MT5 Bridge."
            return resp.status_code, data, ""
        except requests.RequestException as exc:
            return 0, None, f"Falha de comunicação com a MT5 Bridge: {exc.__class__.__name__}."

    def _login_matches(self, reported_login: str) -> bool:
        if not self.expected_login:
            return True
        rep = str(reported_login or "").strip()
        if not rep or rep == "0":
            return False
        return rep == self.expected_login

    def connect(self) -> bool:
        hc = self.health_check()
        return bool(hc.get("connected", False))

    def health_check(self) -> dict[str, Any]:
        code, data, err = self._request_json("GET", "/health", timeout=4.0)
        if code == 0 or data is None:
            return {
                "connected": False,
                "bridge_online": False,
                "trade_allowed": False,
                "server": "",
                "login": "",
                "message": err or "MT5 Bridge inacessível.",
            }
        reported_login = str(data.get("login", "") or "")
        broker_conn = bool(data.get("broker_connected", False))
        login_ok = self._login_matches(reported_login)
        connected = bool(code == 200 and broker_conn and login_ok)
        msg = "Conectado à Clear Investimentos (MT5)." if connected else (
            "Divergência de login MT5 na carteira." if (broker_conn and not login_ok) else (
                err or str(data.get("message") or "Aguardando autenticação do terminal MT5 na Clear.")
            )
        )
        return {
            "connected": connected,
            "bridge_online": bool(data.get("bridge_online", code == 200)),
            "trade_allowed": bool(data.get("trade_allowed", False)) and login_ok,
            "server": str(data.get("server", "") or ""),
            "login": reported_login,
            "message": msg,
        }

    def get_account_summary(self) -> dict[str, Any]:
        code, data, err = self._request_json("GET", "/account", timeout=5.0)
        if code != 200 or data is None:
            return {
                "connected": False,
                "balance": 0.0,
                "equity": 0.0,
                "free_margin": 0.0,
                "server": "",
                "login": "",
                "currency": "BRL",
                "message": err or "Conta MT5 indisponível.",
            }
        reported_login = str(data.get("login", "") or "")
        login_ok = self._login_matches(reported_login)
        is_conn = bool(data.get("connected", False)) and login_ok
        if not login_ok:
            return {
                "connected": False,
                "balance": 0.0,
                "equity": 0.0,
                "free_margin": 0.0,
                "server": str(data.get("server", "") or ""),
                "login": reported_login,
                "currency": "BRL",
                "message": "Login da conta MT5 ativa difere do login configurado na carteira.",
            }
        return {
            "connected": is_conn,
            "balance": round(float(data.get("balance", 0.0) or 0.0), 2),
            "equity": round(float(data.get("equity", 0.0) or 0.0), 2),
            "free_margin": round(float(data.get("free_margin", data.get("margin_free", 0.0)) or 0.0), 2),
            "margin": round(float(data.get("margin", 0.0) or 0.0), 2),
            "server": str(data.get("server", "") or ""),
            "login": reported_login,
            "currency": str(data.get("currency", "BRL") or "BRL"),
        }

    def _get_raw_positions(self) -> tuple[bool, list[dict[str, Any]]]:
        code, data, _ = self._request_json("GET", "/positions", timeout=6.0)
        if code != 200 or data is None or not isinstance(data.get("positions"), list):
            return False, []
        return True, [dict(p) for p in data["positions"] if isinstance(p, dict)]

    def get_broker_positions(self) -> list[Position]:
        ok, raw_list = self._get_raw_positions()
        if not ok:
            return []

        grouped: dict[str, dict[str, Any]] = {}
        for item in raw_list:
            pos_type = int(item.get("type", 0) or 0)
            if pos_type != 0:
                continue
            raw_sym = str(item.get("symbol", "") or "").strip().upper()
            base_tk = normalize_b3_ticker(raw_sym)
            vol = int(round(float(item.get("volume", 0.0) or 0.0)))
            if not base_tk or vol <= 0:
                continue

            p_open = float(item.get("price_open", 0.0) or 0.0)
            p_curr = float(item.get("price_current", 0.0) or p_open)
            sl = float(item.get("sl", 0.0) or 0.0)
            ticket = int(item.get("ticket", 0) or 0)
            epoch_sec = int(item.get("time", 0) or 0)
            opened_iso = (
                datetime.fromtimestamp(epoch_sec, tz=timezone.utc).astimezone(BRT).isoformat()
                if epoch_sec > 0
                else get_brt_now().isoformat()
            )
            is_standard = raw_sym == base_tk

            if base_tk not in grouped:
                grouped[base_tk] = {
                    "qty": vol,
                    "open_notional": vol * p_open,
                    "curr_notional": vol * p_curr,
                    "primary_ticket": ticket,
                    "primary_is_std": is_standard,
                    "primary_vol": vol,
                    "stop_loss": sl if sl > 0.0 else 0.0,
                    "opened_at": opened_iso,
                }
            else:
                g = grouped[base_tk]
                g["qty"] += vol
                g["open_notional"] += vol * p_open
                g["curr_notional"] += vol * p_curr
                if (is_standard and not g["primary_is_std"]) or (
                    is_standard == g["primary_is_std"] and vol > g["primary_vol"]
                ):
                    g["primary_ticket"] = ticket
                    g["primary_is_std"] = is_standard
                    g["primary_vol"] = vol
                if sl > 0.0:
                    if g["stop_loss"] <= 0.0 or is_standard:
                        g["stop_loss"] = sl
                if opened_iso and (not g["opened_at"] or opened_iso < g["opened_at"]):
                    g["opened_at"] = opened_iso

        positions: list[Position] = []
        for tk in sorted(grouped.keys()):
            g = grouped[tk]
            total_q = int(g["qty"])
            avg_open = round(g["open_notional"] / total_q, 4) if total_q > 0 else 0.0
            avg_curr = round(g["curr_notional"] / total_q, 4) if total_q > 0 else avg_open
            positions.append(
                Position(
                    ticker=tk,
                    quantity=total_q,
                    entry_price=avg_open,
                    current_price=avg_curr,
                    stop_loss=round(float(g["stop_loss"]), 4),
                    opened_at=str(g["opened_at"]),
                    highest_price=max(avg_open, avg_curr),
                    ticket=int(g["primary_ticket"]),
                    fiscal_avg_price=avg_open,
                )
            )
        return positions

    def _fetch_leg_tick(self, leg_symbol: str) -> dict[str, Any] | None:
        code, data, _ = self._request_json("GET", f"/tick/{leg_symbol.strip().upper()}", timeout=5.0)
        if code != 200 or data is None:
            return None
        return {
            "bid": float(data.get("bid", 0.0) or 0.0),
            "ask": float(data.get("ask", 0.0) or 0.0),
            "last": float(data.get("last", 0.0) or 0.0),
            "volume": float(data.get("volume", 0.0) or 0.0),
            "time": float(data.get("time", 0.0) or 0.0),
        }

    def get_quote(self, symbol: str) -> MarketQuote | None:
        clean_sym = symbol.strip().upper()
        tick = self._fetch_leg_tick(clean_sym)
        if tick is None:
            return None
        bid = float(tick["bid"])
        ask = float(tick["ask"])
        last = float(tick["last"])
        px = last if last > 0.0 else ((bid + ask) / 2.0 if (bid > 0.0 and ask > 0.0) else max(bid, ask))
        if px <= 0.0:
            return None
        mid = (ask + bid) / 2.0 if (bid > 0.0 and ask >= bid) else px
        spread_pct = ((ask - bid) / mid) if (bid > 0.0 and ask >= bid and mid > 0.0) else 0.0
        return MarketQuote(
            ticker=normalize_b3_ticker(clean_sym),
            price=round(px, 4),
            bid=round(bid, 4),
            ask=round(ask, 4),
            spread_pct=round(spread_pct, 6),
            volume=float(tick["volume"]),
            quote_ts=float(tick["time"]),
            source="mt5_bridge",
            is_stale=False,
        )

    def place_order(
        self,
        symbol: str,
        side: OrderSide,
        quantity: int,
        expected_price: float = 0.0,
        stop_loss: float = 0.0,
        client_order_id: str = "",
        bypass_guards: bool = False,
    ) -> Order:
        clean_side = side if isinstance(side, OrderSide) else OrderSide(str(side).strip().upper())
        base = normalize_b3_ticker(symbol)
        now_iso = get_brt_now().isoformat()

        hc = self.health_check()
        if not bool(hc.get("connected", False)):
            return Order(
                ticker=base,
                side=clean_side,
                quantity=0,
                price=0.0,
                status=OrderStatus.REJECTED,
                stop_loss=stop_loss,
                fee=0.0,
                client_order_id=client_order_id,
                message=str(hc.get("message") or "Corretora Clear desconectada."),
                timestamp=now_iso,
            )

        precomputed_legs: list[tuple[str, int]] | None = None
        if clean_side == OrderSide.SELL:
            ok_pos, raw_list = self._get_raw_positions()
            if not ok_pos:
                return Order(
                    ticker=base,
                    side=clean_side,
                    quantity=0,
                    price=0.0,
                    status=OrderStatus.REJECTED,
                    stop_loss=stop_loss,
                    fee=0.0,
                    client_order_id=client_order_id,
                    message="Venda bloqueada: impossível confirmar custódia real na Clear MT5.",
                    timestamp=now_iso,
                )
            raw_qtys: dict[str, int] = {}
            for item in raw_list:
                if int(item.get("type", 0) or 0) != 0:
                    continue
                s = str(item.get("symbol", "") or "").strip().upper()
                if normalize_b3_ticker(s) == base:
                    raw_qtys[s] = raw_qtys.get(s, 0) + int(round(float(item.get("volume", 0.0) or 0.0)))
            safe_legs, deny_reason = plan_safe_b3_sell_legs(base, int(quantity), raw_qtys)
            if not safe_legs:
                return Order(
                    ticker=base,
                    side=clean_side,
                    quantity=0,
                    price=0.0,
                    status=OrderStatus.REJECTED,
                    stop_loss=stop_loss,
                    fee=0.0,
                    client_order_id=client_order_id,
                    message=deny_reason,
                    timestamp=now_iso,
                )
            precomputed_legs = safe_legs

        def _submit_leg(
            leg_sym: str,
            leg_side: OrderSide,
            leg_qty: int,
            ref_price: float,
            max_slip_pct: float,
            leg_cid: str,
            is_bypass: bool,
        ) -> dict[str, Any]:
            body = {
                "symbol": leg_sym,
                "side": leg_side.value,
                "quantity": int(leg_qty),
                "price": round(float(ref_price), 4),
                "stop_loss": round(float(stop_loss), 4) if leg_side == OrderSide.BUY and stop_loss > 0 else 0.0,
                "max_slippage_pct": float(max_slip_pct),
                "client_order_id": leg_cid,
                "wallet_id": self.wallet_id,
                "is_emergency_liquidation": bool(is_bypass and leg_side == OrderSide.SELL),
                "magic": settings.MT5_MAGIC,
            }
            code, resp_data, err_msg = self._request_json(
                "POST",
                "/order",
                json_body=body,
                timeout=settings.WALLET_EXECUTION_TIMEOUT_SECONDS,
            )
            if code != 200 or resp_data is None:
                return {
                    "filled": False,
                    "filled_qty": 0,
                    "fill_price": 0.0,
                    "order_id": "",
                    "message": err_msg or "Falha HTTP ao enviar ordem para a MT5 Bridge.",
                }
            return {
                "filled": bool(resp_data.get("filled", False)),
                "filled_qty": int(resp_data.get("filled_qty", 0) or 0),
                "fill_price": float(resp_data.get("fill_price", 0.0) or 0.0),
                "order_id": str(resp_data.get("order_id", "") or ""),
                "message": str(resp_data.get("message", "") or ""),
            }

        order = execute_b3_legs(
            symbol=base,
            side=clean_side,
            quantity=int(quantity),
            quote_fetcher=self._fetch_leg_tick,
            leg_submitter=_submit_leg,
            expected_price=expected_price,
            stop_loss=stop_loss,
            client_order_id=client_order_id,
            bypass_guards=bypass_guards,
            precomputed_legs=precomputed_legs,
        )

        if (
            clean_side == OrderSide.BUY
            and stop_loss > 0.0
            and order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL)
        ):
            first_ticket = 0
            if order.order_id:
                head = order.order_id.split(",")[0].strip()
                if head.isdigit():
                    first_ticket = int(head)
            self.update_position_stop_loss(base, first_ticket, stop_loss)

        return order

    def update_position_stop_loss(
        self,
        symbol: str,
        ticket: int,
        stop_loss: float,
    ) -> bool:
        if stop_loss <= 0.0:
            return False
        base = normalize_b3_ticker(symbol)
        targets: list[tuple[str, int]] = []
        ok_pos, raw_list = self._get_raw_positions()
        if ok_pos:
            for item in raw_list:
                if int(item.get("type", 0) or 0) != 0:
                    continue
                s = str(item.get("symbol", "") or "").strip().upper()
                t = int(item.get("ticket", 0) or 0)
                if t > 0 and normalize_b3_ticker(s) == base:
                    targets.append((s, t))

        if not targets and int(ticket) > 0 and base:
            targets.append((base, int(ticket)))

        if not targets:
            return False

        all_ok = True
        for sym_leg, t_id in targets:
            code, data, _ = self._request_json(
                "POST",
                "/position/sltp",
                json_body={
                    "symbol": sym_leg,
                    "ticket": int(t_id),
                    "stop_loss": round(float(stop_loss), 4),
                    "take_profit": 0.0,
                },
                timeout=6.0,
            )
            if code != 200 or data is None or not bool(data.get("ok", False)):
                all_ok = False
        return all_ok

    def cancel_all_pending_orders(self) -> int:
        code, data, _ = self._request_json("POST", "/orders/cancel_all", json_body={}, timeout=8.0)
        if code != 200 or data is None:
            return 0
        return int(data.get("cancelled_count", 0) or 0)
