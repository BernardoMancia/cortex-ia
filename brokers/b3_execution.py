from __future__ import annotations

from typing import Any, Callable

from config.settings import settings
from domain.b3_rules import (
    get_brt_now,
    is_b3_unit_lot_asset,
    normalize_b3_ticker,
    split_b3_order_legs,
)
from domain.models import Order, OrderSide, OrderStatus

LegQuoteFn = Callable[[str], dict[str, Any] | None]
LegSubmitFn = Callable[[str, OrderSide, int, float, float, str, bool], dict[str, Any]]


def plan_safe_b3_sell_legs(
    symbol: str,
    quantity: int,
    raw_symbol_quantities: dict[str, int],
) -> tuple[list[tuple[str, int]], str]:
    base = normalize_b3_ticker(symbol)
    if not base or int(quantity) <= 0:
        return [], "Símbolo ou quantidade inválida para venda na B3."

    std_held = max(0, int(raw_symbol_quantities.get(base, 0)))
    frac_sym = f"{base}F"
    frac_held = max(0, int(raw_symbol_quantities.get(frac_sym, 0)))
    total_held = std_held + frac_held

    if total_held <= 0:
        return [], f"Venda bloqueada (Anti-Short B3): custódia zerada para {base}."

    safe_qty = min(int(quantity), total_held)
    if is_b3_unit_lot_asset(base):
        return [(base, safe_qty)], ""

    legs: list[tuple[str, int]] = []
    std_sell = min((safe_qty // 100) * 100, (std_held // 100) * 100)
    if std_sell > 0:
        legs.append((base, std_sell))

    rem = safe_qty - std_sell
    while rem > 0:
        chunk = min(rem, 99)
        legs.append((frac_sym, chunk))
        rem -= chunk

    return legs, ""


def execute_b3_legs(
    symbol: str,
    side: OrderSide | str,
    quantity: int,
    quote_fetcher: LegQuoteFn,
    leg_submitter: LegSubmitFn,
    expected_price: float = 0.0,
    stop_loss: float = 0.0,
    client_order_id: str = "",
    bypass_guards: bool = False,
    precomputed_legs: list[tuple[str, int]] | None = None,
) -> Order:
    clean_side = side if isinstance(side, OrderSide) else OrderSide(str(side).strip().upper())
    base = normalize_b3_ticker(symbol)
    now_iso = get_brt_now().isoformat()

    if not base or int(quantity) <= 0:
        return Order(
            ticker=base or str(symbol).strip().upper(),
            side=clean_side,
            quantity=0,
            price=0.0,
            status=OrderStatus.REJECTED,
            stop_loss=stop_loss,
            fee=0.0,
            client_order_id=client_order_id,
            message="Quantidade ou ativo inválido para execução B3.",
            timestamp=now_iso,
        )

    legs = (
        list(precomputed_legs)
        if precomputed_legs is not None
        else split_b3_order_legs(base, int(quantity))
    )
    if not legs:
        return Order(
            ticker=base,
            side=clean_side,
            quantity=0,
            price=0.0,
            status=OrderStatus.REJECTED,
            stop_loss=stop_loss,
            fee=0.0,
            client_order_id=client_order_id,
            message="Nenhuma perna válida gerada para o lote B3.",
            timestamp=now_iso,
        )

    requested_total = sum(int(q) for _, q in legs)
    total_filled_qty = 0
    total_notional = 0.0
    order_ids: list[str] = []
    leg_records: list[dict[str, Any]] = []
    messages: list[str] = []

    for idx, (leg_sym, leg_qty) in enumerate(legs, start=1):
        leg_cid = f"{client_order_id}_L{idx}" if client_order_id else ""
        tick = quote_fetcher(leg_sym)

        if tick is None:
            if not bypass_guards:
                msg = f"Cotação ao vivo indisponível para {leg_sym} na validação pré-ordem."
                messages.append(msg)
                leg_records.append(
                    {
                        "leg_index": idx,
                        "symbol": leg_sym,
                        "requested_qty": leg_qty,
                        "filled_qty": 0,
                        "price": 0.0,
                        "status": OrderStatus.REJECTED.value,
                        "client_order_id": leg_cid,
                        "message": msg,
                    }
                )
                continue
            bid = float(expected_price)
            ask = float(expected_price)
            last = float(expected_price)
        else:
            bid = float(tick.get("bid", 0.0) or 0.0)
            ask = float(tick.get("ask", 0.0) or 0.0)
            last = float(tick.get("last", 0.0) or 0.0)

        if clean_side == OrderSide.BUY:
            exec_ref = ask if ask > 0.0 else (last if last > 0.0 else float(expected_price))
        else:
            exec_ref = bid if bid > 0.0 else (last if last > 0.0 else float(expected_price))

        if not bypass_guards:
            if bid > 0.0 and ask > 0.0 and ask >= bid:
                mid = (ask + bid) / 2.0
                spread_pct = ((ask - bid) / mid) if mid > 0.0 else 0.0
                if spread_pct > settings.MAX_SPREAD_PERCENT:
                    msg = (
                        f"Spread Guard B3 bloqueou {leg_sym}: "
                        f"{spread_pct * 100.0:.2f}% > {settings.MAX_SPREAD_PERCENT * 100.0:.2f}%."
                    )
                    messages.append(msg)
                    leg_records.append(
                        {
                            "leg_index": idx,
                            "symbol": leg_sym,
                            "requested_qty": leg_qty,
                            "filled_qty": 0,
                            "price": 0.0,
                            "status": OrderStatus.REJECTED.value,
                            "client_order_id": leg_cid,
                            "message": msg,
                        }
                    )
                    continue

            if expected_price > 0.0 and exec_ref > 0.0:
                if clean_side == OrderSide.BUY:
                    adverse_slip = (exec_ref - float(expected_price)) / float(expected_price)
                else:
                    adverse_slip = (float(expected_price) - exec_ref) / float(expected_price)
                if adverse_slip > settings.MAX_SLIPPAGE_PERCENT:
                    msg = (
                        f"Slippage Guard B3 bloqueou {leg_sym}: "
                        f"{adverse_slip * 100.0:.2f}% > {settings.MAX_SLIPPAGE_PERCENT * 100.0:.2f}%."
                    )
                    messages.append(msg)
                    leg_records.append(
                        {
                            "leg_index": idx,
                            "symbol": leg_sym,
                            "requested_qty": leg_qty,
                            "filled_qty": 0,
                            "price": 0.0,
                            "status": OrderStatus.REJECTED.value,
                            "client_order_id": leg_cid,
                            "message": msg,
                        }
                    )
                    continue

        leg_resp = leg_submitter(
            leg_sym,
            clean_side,
            int(leg_qty),
            float(exec_ref),
            float(settings.MAX_SLIPPAGE_PERCENT),
            leg_cid,
            bool(bypass_guards),
        )

        filled_ok = bool(leg_resp.get("filled", False))
        f_qty = int(leg_resp.get("filled_qty", 0) or 0)
        f_px = float(leg_resp.get("fill_price", 0.0) or 0.0)
        if f_px <= 0.0 and filled_ok:
            f_px = float(exec_ref)
        oid = str(leg_resp.get("order_id", "") or "")
        leg_msg = str(leg_resp.get("message", "") or "")

        if filled_ok and f_qty > 0 and f_px > 0.0:
            total_filled_qty += f_qty
            total_notional += f_qty * f_px
            if oid:
                order_ids.append(oid)
            leg_status = OrderStatus.FILLED.value if f_qty >= leg_qty else OrderStatus.PARTIAL.value
            leg_records.append(
                {
                    "leg_index": idx,
                    "symbol": leg_sym,
                    "requested_qty": leg_qty,
                    "filled_qty": f_qty,
                    "price": round(f_px, 4),
                    "order_id": oid,
                    "status": leg_status,
                    "client_order_id": leg_cid,
                    "message": leg_msg or "Executada",
                }
            )
            if leg_msg:
                messages.append(f"{leg_sym}: {leg_msg}")
        else:
            err_msg = leg_msg or f"Falha na execução da perna {leg_sym}."
            messages.append(err_msg)
            leg_records.append(
                {
                    "leg_index": idx,
                    "symbol": leg_sym,
                    "requested_qty": leg_qty,
                    "filled_qty": 0,
                    "price": 0.0,
                    "order_id": oid,
                    "status": OrderStatus.REJECTED.value,
                    "client_order_id": leg_cid,
                    "message": err_msg,
                }
            )

    if total_filled_qty <= 0:
        return Order(
            ticker=base,
            side=clean_side,
            quantity=0,
            price=0.0,
            status=OrderStatus.REJECTED,
            stop_loss=stop_loss,
            fee=0.0,
            order_id=",".join(order_ids),
            client_order_id=client_order_id,
            message="; ".join(messages) or "Ordem rejeitada na corretora.",
            timestamp=now_iso,
            legs=leg_records,
        )

    avg_price = round(total_notional / total_filled_qty, 4)
    gross_value = round(total_filled_qty * avg_price, 2)
    b3_fee = round(gross_value * settings.B3_FEE_RATE, 4)
    final_status = (
        OrderStatus.FILLED if total_filled_qty >= requested_total else OrderStatus.PARTIAL
    )

    return Order(
        ticker=base,
        side=clean_side,
        quantity=total_filled_qty,
        price=avg_price,
        status=final_status,
        stop_loss=stop_loss,
        fee=b3_fee,
        order_id=",".join(order_ids),
        client_order_id=client_order_id,
        message="; ".join(messages) or "Ordem executada com sucesso na B3.",
        timestamp=now_iso,
        legs=leg_records,
    )
