from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import re
import threading
import time
from typing import Any

B3_SYMBOL_REGEX = re.compile(r"^[A-Z][A-Z0-9]{3}[0-9]{1,2}F?$")


@dataclass(frozen=True)
class FirewallVerdict:
    allowed: bool
    reason: str
    trigger_panic: bool = False


class FinancialFirewall:
    def __init__(
        self,
        max_single_order_notional: float = 250000.0,
        max_orders_per_minute: int = 12,
        idempotency_ttl_seconds: float = 900.0,
    ) -> None:
        self.max_single_order_notional = max(100.0, float(max_single_order_notional))
        self.max_orders_per_minute = max(2, int(max_orders_per_minute))
        self.idempotency_ttl_seconds = max(60.0, float(idempotency_ttl_seconds))
        self._lock = threading.Lock()
        self._wallet_order_timestamps: dict[int, list[float]] = {}
        self._global_order_timestamps: list[float] = []
        self._idempotency_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    @staticmethod
    def generate_client_order_id(
        wallet_id: int,
        cycle_id: str,
        symbol: str,
        side: str,
        quantity: int,
    ) -> str:
        raw = f"{wallet_id}:{cycle_id}:{symbol.strip().upper()}:{side.strip().upper()}:{int(quantity)}"
        return "cx_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    def _prune_locked(self, now: float) -> None:
        cutoff = now - 60.0
        self._global_order_timestamps = [t for t in self._global_order_timestamps if t >= cutoff]
        empty_wallets = []
        for wid, hist in self._wallet_order_timestamps.items():
            fresh = [t for t in hist if t >= cutoff]
            if fresh:
                self._wallet_order_timestamps[wid] = fresh
            else:
                empty_wallets.append(wid)
        for wid in empty_wallets:
            self._wallet_order_timestamps.pop(wid, None)

        idem_cutoff = now - self.idempotency_ttl_seconds
        stale_keys = [k for k, (ts, _) in self._idempotency_cache.items() if ts < idem_cutoff]
        for k in stale_keys:
            self._idempotency_cache.pop(k, None)

    def get_cached_execution(self, client_order_id: str) -> dict[str, Any] | None:
        if not client_order_id:
            return None
        with self._lock:
            now = time.monotonic()
            self._prune_locked(now)
            item = self._idempotency_cache.get(client_order_id)
            return dict(item[1]) if item is not None else None

    def record_execution(self, client_order_id: str, receipt: dict[str, Any]) -> None:
        if not client_order_id:
            return
        with self._lock:
            now = time.monotonic()
            self._prune_locked(now)
            self._idempotency_cache[client_order_id] = (now, dict(receipt))

    def validate_order(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        wallet_id: int = 0,
        wallet_ceiling: float | None = None,
        is_emergency_liquidation: bool = False,
    ) -> FirewallVerdict:
        clean_sym = (symbol or "").strip().upper()
        if not B3_SYMBOL_REGEX.match(clean_sym):
            return FirewallVerdict(False, f"Símbolo fora do padrão B3 bloqueado pelo Firewall: '{symbol}'.")

        clean_side = (side or "").strip().upper()
        if clean_side not in ("BUY", "SELL"):
            return FirewallVerdict(False, f"Lado de ordem inválido: '{side}'.")

        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity <= 0 or quantity > 500000:
            return FirewallVerdict(False, f"Quantidade fora dos limites de segurança: {quantity}.")

        if not math.isfinite(price) or (price <= 0.0 and not is_emergency_liquidation):
            return FirewallVerdict(False, f"Preço inválido bloqueado pelo Firewall: {price}.")

        notional = float(quantity) * max(0.0, float(price))
        if clean_side == "BUY":
            if notional > self.max_single_order_notional:
                return FirewallVerdict(
                    False,
                    f"Ordem Fat-Finger bloqueada (R$ {notional:,.2f} > limite R$ {self.max_single_order_notional:,.2f}).",
                )
            if wallet_ceiling is not None and wallet_ceiling >= 0.0 and notional > (wallet_ceiling + 0.05):
                return FirewallVerdict(
                    False,
                    f"Ordem excede o teto disponível da carteira (R$ {notional:,.2f} > R$ {wallet_ceiling:,.2f}).",
                )

        if not is_emergency_liquidation and clean_side == "BUY":
            with self._lock:
                now = time.monotonic()
                self._prune_locked(now)
                w_hist = self._wallet_order_timestamps.get(wallet_id, [])
                if len(w_hist) >= self.max_orders_per_minute:
                    return FirewallVerdict(
                        allowed=False,
                        reason=f"Anomalia de frequência detectada ({len(w_hist)} ordens/min na carteira {wallet_id}). Pânico automático acionado!",
                        trigger_panic=True,
                    )
                if len(self._global_order_timestamps) >= (self.max_orders_per_minute * 3):
                    return FirewallVerdict(
                        allowed=False,
                        reason="Rajada global anômala de ordens bloqueada pelo Firewall Financeiro.",
                        trigger_panic=True,
                    )
                w_hist.append(now)
                self._wallet_order_timestamps[wallet_id] = w_hist
                self._global_order_timestamps.append(now)

        return FirewallVerdict(True, "OK")
