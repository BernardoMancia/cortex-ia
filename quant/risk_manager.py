from __future__ import annotations

import math
from typing import Any

from config.settings import settings
from domain.b3_rules import normalize_b3_ticker
from domain.models import Position


def _safe_float(val: object, default: float) -> float:
    try:
        num = float(val)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError):
        return default


class WalletRiskManager:
    def compute_buy_quantity(
        self,
        ticker: str,
        price: float,
        stop_loss: float,
        operational_equity: float,
        available_ai_cash: float,
        positions: list[Position],
        wallet_row: dict[str, Any],
        daily_pnl_pct: float = 0.0,
    ) -> tuple[int, str]:
        norm_tk = normalize_b3_ticker(ticker)
        if not norm_tk or not math.isfinite(price) or price <= 0.0:
            return (0, "Preço inválido para dimensionamento de compra.")

        if not math.isfinite(operational_equity) or operational_equity <= 0.0:
            return (0, "Patrimônio operacional insuficiente para novas alocações.")

        if not math.isfinite(available_ai_cash) or available_ai_cash <= 0.0:
            return (0, "Caixa alocado à IA esgotado.")

        circuit_limit = -(settings.MAX_DAILY_LOSS_PERCENT * 100.0)
        if _safe_float(daily_pnl_pct, 0.0) <= circuit_limit:
            return (
                0,
                f"Circuit breaker diário ativo ({daily_pnl_pct:.2f}% <= {circuit_limit:.2f}%).",
            )

        existing_asset_value = 0.0
        sector = settings.SECTOR_MAP.get(norm_tk, "Outros")
        existing_sector_value = 0.0

        for pos in positions:
            pos_tk = normalize_b3_ticker(pos.ticker)
            if pos_tk == norm_tk:
                if pos.do_not_touch:
                    return (0, f"Posição em {norm_tk} está marcada como protegida (do_not_touch).")
                existing_asset_value += pos.market_value
            pos_sector = settings.SECTOR_MAP.get(pos_tk, "Outros")
            if pos_sector == sector:
                existing_sector_value += pos.market_value

        max_asset_value = operational_equity * settings.MAX_CONCENTRATION
        asset_headroom = max_asset_value - existing_asset_value
        if asset_headroom < price:
            return (
                0,
                f"Limite máximo de concentração por ativo ({settings.MAX_CONCENTRATION * 100:.0f}%) atingido em {norm_tk}.",
            )

        max_sector_value = operational_equity * settings.MAX_SECTOR_EXPOSURE
        sector_headroom = max_sector_value - existing_sector_value
        if sector_headroom < price:
            return (
                0,
                f"Limite máximo de exposição setorial ({settings.MAX_SECTOR_EXPOSURE * 100:.0f}%) atingido em {sector}.",
            )

        wallet_sl_pct = _safe_float(
            wallet_row.get("stop_loss_pct") if wallet_row else None,
            settings.STOP_LOSS_PERCENT,
        )
        if wallet_sl_pct <= 0.0 or wallet_sl_pct >= 0.90:
            wallet_sl_pct = settings.STOP_LOSS_PERCENT

        eff_stop = (
            stop_loss
            if (math.isfinite(stop_loss) and 0.0 < stop_loss < price)
            else max(0.01, round(price * (1.0 - wallet_sl_pct), 2))
        )

        risk_budget = operational_equity * settings.MAX_RISK_PER_TRADE_PERCENT
        per_unit_risk = max(0.05, price - eff_stop)
        qty_by_risk = int(math.floor(risk_budget / per_unit_risk))

        qty_by_concentration = int(math.floor(asset_headroom / price))
        qty_by_sector = int(math.floor(sector_headroom / price))

        cost_factor = 1.0 + settings.B3_FEE_RATE + 0.002
        usable_cash = available_ai_cash / cost_factor
        qty_by_cash = int(math.floor(usable_cash / price))

        qty_by_notional = (
            int(math.floor(settings.MAX_SINGLE_ORDER_NOTIONAL / price))
            if settings.MAX_SINGLE_ORDER_NOTIONAL > 0.0
            else qty_by_risk
        )

        final_qty = min(
            qty_by_risk,
            qty_by_concentration,
            qty_by_sector,
            qty_by_cash,
            qty_by_notional,
        )

        if final_qty < 1:
            if qty_by_cash < 1:
                return (
                    0,
                    f"Saldo disponível da IA insuficiente para adquirir 1 unidade de {norm_tk} com margem de custos.",
                )
            return (
                0,
                f"Quantidade calculada pelo gerenciamento de risco inferior a 1 unidade para {norm_tk}.",
            )

        return (
            final_qty,
            f"Quantidade aprovada: {final_qty} un. de {norm_tk} (setor {sector}, risco máx. R$ {risk_budget:.2f}).",
        )

    def evaluate_open_position_exit(
        self,
        pos: Position,
        current_price: float,
        wallet_row: dict[str, Any],
        bought_same_day: bool = False,
    ) -> tuple[str | None, int, float, bool, str]:
        if pos.do_not_touch:
            return (
                None,
                0,
                pos.stop_loss,
                pos.partial_tp_taken,
                "Posição manual protegida (do_not_touch).",
            )

        if not math.isfinite(current_price) or current_price <= 0.0:
            return (
                None,
                0,
                pos.stop_loss,
                pos.partial_tp_taken,
                "Cotação atual inválida para avaliação de saída.",
            )

        row = wallet_row or {}
        base_sl_pct = _safe_float(row.get("stop_loss_pct"), settings.STOP_LOSS_PERCENT)
        trigger_pct = _safe_float(row.get("trailing_trigger_pct"), settings.TRAILING_STOP_TRIGGER_PERCENT)
        distance_pct = _safe_float(row.get("trailing_distance_pct"), settings.TRAILING_STOP_DISTANCE_PERCENT)

        if trigger_pct <= 0.0:
            trigger_pct = settings.TRAILING_STOP_TRIGGER_PERCENT
        if distance_pct <= 0.0 or distance_pct >= trigger_pct:
            distance_pct = min(settings.TRAILING_STOP_DISTANCE_PERCENT, round(trigger_pct * 0.75, 4))

        highest_price = max(
            _safe_float(pos.highest_price, 0.0),
            _safe_float(pos.entry_price, current_price),
            current_price,
        )

        updated_stop = (
            max(0.01, round(pos.stop_loss, 2))
            if pos.stop_loss > 0.0
            else max(0.01, round(pos.entry_price * (1.0 - base_sl_pct), 2))
        )

        if pos.entry_price > 0.0 and ((highest_price - pos.entry_price) / pos.entry_price) >= trigger_pct:
            trail_stop = max(0.01, round(highest_price * (1.0 - distance_pct), 2))
            updated_stop = max(updated_stop, trail_stop)

        if current_price <= updated_stop and updated_stop > 0.0:
            return (
                "STOP_LOSS",
                pos.quantity,
                updated_stop,
                pos.partial_tp_taken,
                "Stop-Loss atingido.",
            )

        if (
            pos.entry_price > 0.0
            and ((current_price - pos.entry_price) / pos.entry_price) >= 0.15
            and not pos.partial_tp_taken
            and pos.quantity >= 2
        ):
            raw_avoid_dt = row.get("avoid_day_trade_tax", 1)
            avoid_dt = True if raw_avoid_dt is None else bool(raw_avoid_dt)
            if bought_same_day and avoid_dt:
                deferred_stop = max(updated_stop, max(0.01, round(pos.entry_price, 2)))
                return (
                    None,
                    0,
                    deferred_stop,
                    False,
                    "Realização parcial adiada para evitar alíquota de Day-Trade (20%).",
                )
            be_stop = max(updated_stop, max(0.01, round(pos.entry_price * 1.005, 2)))
            return (
                "PARTIAL_TP",
                pos.quantity // 2,
                be_stop,
                True,
                "Realização parcial de lucro (+15%) com stop no breakeven.",
            )

        if updated_stop > pos.stop_loss:
            return (
                None,
                0,
                updated_stop,
                pos.partial_tp_taken,
                f"Stop móvel elevado para R$ {updated_stop:.2f}.",
            )

        return (
            None,
            0,
            updated_stop,
            pos.partial_tp_taken,
            "Posição mantida sob monitoramento de risco.",
        )
