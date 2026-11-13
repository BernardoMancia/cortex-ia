from __future__ import annotations

from typing import Any

from config.settings import settings
from domain.b3_rules import get_brt_now, normalize_b3_ticker
from domain.b3_tax_guard import B3TaxGuard
from domain.models import MarketQuote, Order, Position
from storage.capital_repo import CapitalRepository
from storage.connection import DatabaseConnection
from storage.trading_repo import TradingRepository
from storage.wallets_repo import WalletsRepository
from wallets.runtime_registry import get_wallet_lock


class WalletPortfolioManager:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()
        self.wallets_repo = WalletsRepository(self.db)
        self.trading_repo = TradingRepository(self.db)
        self.capital_repo = CapitalRepository(self.db)
        self.tax_guard = B3TaxGuard(
            exemption_limit=settings.B3_SWING_EXEMPTION_LIMIT,
            guard_buffer=settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
        )

    def reconcile_positions_with_broker(
        self,
        user_id: int,
        wallet_id: int,
        broker_positions: list[Position],
        default_stop_loss_pct: float = 0.10,
    ) -> dict[str, Any]:
        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")

            sl_pct = max(0.01, min(0.50, float(default_stop_loss_pct)))
            local_map = {
                normalize_b3_ticker(p.ticker): p
                for p in self.trading_repo.list_positions(user_id, wallet_id)
            }
            broker_map = {
                normalize_b3_ticker(bp.ticker): bp
                for bp in broker_positions
                if normalize_b3_ticker(bp.ticker) and int(bp.quantity) > 0
            }

            for tk, bp in broker_map.items():
                loc = local_map.get(tk)
                cur_px = bp.current_price if bp.current_price > 0.0 else bp.entry_price
                eff_sl = (
                    bp.stop_loss
                    if bp.stop_loss > 0.0
                    else (
                        loc.stop_loss
                        if (loc is not None and loc.stop_loss > 0.0)
                        else round(bp.entry_price * (1.0 - sl_pct), 4)
                    )
                )
                fiscal_avg = (
                    loc.fiscal_avg_price
                    if (loc is not None and loc.fiscal_avg_price > 0.0 and loc.quantity == bp.quantity)
                    else bp.entry_price
                )
                merged = Position(
                    ticker=tk,
                    quantity=int(bp.quantity),
                    entry_price=round(float(bp.entry_price), 4),
                    current_price=round(float(cur_px), 4),
                    stop_loss=round(float(eff_sl), 4),
                    opened_at=(loc.opened_at if (loc is not None and loc.opened_at) else bp.opened_at),
                    highest_price=round(
                        max(
                            float(bp.highest_price),
                            float(cur_px),
                            float(loc.highest_price) if loc is not None else 0.0,
                        ),
                        4,
                    ),
                    partial_tp_taken=bool(loc.partial_tp_taken) if loc is not None else False,
                    do_not_touch=bool(loc.do_not_touch) if loc is not None else False,
                    ticket=int(bp.ticket if bp.ticket > 0 else (loc.ticket if loc is not None else 0)),
                    fiscal_avg_price=round(float(fiscal_avg), 4),
                )
                self.trading_repo.upsert_position(user_id, wallet_id, merged)

            removed_ghosts = 0
            for tk in set(local_map.keys()) - set(broker_map.keys()):
                self.trading_repo.delete_position(user_id, wallet_id, tk)
                removed_ghosts += 1

            return {
                "synced_count": len(broker_map),
                "removed_ghosts": removed_ghosts,
            }

    def get_portfolio_snapshot(
        self,
        user_id: int,
        wallet_id: int,
        quotes: dict[str, MarketQuote] | None = None,
    ) -> dict[str, Any]:
        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")

            positions = self.trading_repo.list_positions(user_id, wallet_id)
            if quotes:
                for pos in positions:
                    q = quotes.get(pos.ticker) or quotes.get(normalize_b3_ticker(pos.ticker))
                    if q is not None and float(q.price) > 0.0:
                        new_px = round(float(q.price), 4)
                        changed = False
                        if abs(pos.current_price - new_px) > 1e-6:
                            pos.current_price = new_px
                            changed = True
                        if new_px > pos.highest_price:
                            pos.highest_price = new_px
                            changed = True
                        if changed:
                            self.trading_repo.upsert_position(user_id, wallet_id, pos)

            invested_value = round(sum(p.market_value for p in positions), 2)
            cost_basis_total = round(sum(p.cost_basis for p in positions), 2)
            unrealized_pnl = round(sum(p.pnl for p in positions), 2)

            free_cash = round(float(wallet.get("free_cash") or 0.0), 2)
            vault_reserve_cash = round(float(wallet.get("vault_reserve_cash") or 0.0), 2)
            initial_capital = round(float(wallet.get("initial_capital") or 0.0), 2)
            ai_allocated_capital = round(float(wallet.get("ai_allocated_capital") or 0.0), 2)

            if (
                settings.IS_PRODUCTION
                and initial_capital <= 0.0
                and ai_allocated_capital <= 0.0
                and free_cash > 0.0
            ):
                initial_capital = round(free_cash + invested_value, 2)
                ai_allocated_capital = initial_capital
                self.wallets_repo.update_wallet_balances(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    free_cash=free_cash,
                    ai_allocated_capital=ai_allocated_capital,
                    vault_reserve_cash=vault_reserve_cash,
                    initial_capital=initial_capital,
                )

            if ai_allocated_capital > 0.0:
                available_for_ai = round(
                    max(0.0, min(free_cash, ai_allocated_capital - invested_value)),
                    2,
                )
            elif settings.IS_SIMULATION:
                available_for_ai = round(max(0.0, free_cash), 2)
            else:
                available_for_ai = 0.0

            operational_equity = round(free_cash + invested_value, 2)
            total_equity = round(free_cash + vault_reserve_cash + invested_value, 2)

            pnl_percent = (
                round(((total_equity - initial_capital) / initial_capital) * 100.0, 2)
                if initial_capital > 0.0
                else 0.0
            )
            day_base = self.trading_repo.get_day_baseline_operational_equity(user_id, wallet_id)
            if day_base is not None and day_base > 0.0:
                daily_pnl_percent = round(((operational_equity - day_base) / day_base) * 100.0, 2)
            elif initial_capital > 0.0:
                daily_pnl_percent = round(
                    ((operational_equity - initial_capital) / initial_capital) * 100.0,
                    2,
                )
            else:
                daily_pnl_percent = 0.0

            stats = self.trading_repo.get_trade_stats(user_id, wallet_id)

            return {
                "wallet_id": int(wallet_id),
                "user_id": int(user_id),
                "wallet_name": str(wallet.get("name") or ""),
                "connector_type": str(wallet.get("connector_type") or ""),
                "operational_mode": str(wallet.get("operational_mode") or "ACTIVE"),
                "automation_enabled": bool(wallet.get("automation_enabled", 0)),
                "initial_capital": initial_capital,
                "free_cash": free_cash,
                "vault_reserve_cash": vault_reserve_cash,
                "ai_allocated_capital": ai_allocated_capital,
                "allocated_capital": invested_value,
                "invested_value": invested_value,
                "cost_basis_total": cost_basis_total,
                "available_for_ai": available_for_ai,
                "buying_power": available_for_ai,
                "operational_equity": operational_equity,
                "total_equity": total_equity,
                "unrealized_pnl": unrealized_pnl,
                "realized_pnl_total": stats["realized_pnl_total"],
                "pnl_percent": pnl_percent,
                "daily_pnl_percent": daily_pnl_percent,
                "win_rate": stats["win_rate"],
                "total_trades": stats["total_trades"],
                "positions": [p.to_dict() for p in positions],
            }

    def apply_filled_buy(
        self,
        user_id: int,
        wallet_id: int,
        order: Order,
        stop_loss: float,
        reasoning: str,
    ) -> Position:
        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")
            if int(order.quantity) <= 0 or float(order.price) <= 0.0:
                raise ValueError("Ordem de compra inválida (quantidade ou preço não positivo).")

            clean_tk = normalize_b3_ticker(order.ticker)
            if order.client_order_id:
                dup = self.trading_repo.find_trade_by_client_order_id(
                    user_id,
                    wallet_id,
                    order.client_order_id,
                )
                if dup is not None:
                    existing_dup = self.trading_repo.get_position(user_id, wallet_id, clean_tk)
                    if existing_dup is not None:
                        return existing_dup

            existing = self.trading_repo.get_position(user_id, wallet_id, clean_tk)
            fee = (
                round(float(order.fee), 4)
                if float(order.fee) > 0.0
                else round(order.total_value * settings.B3_FEE_RATE, 4)
            )
            cash_spent = round(order.total_value + fee, 2)
            new_free_cash = round(max(0.0, float(wallet.get("free_cash") or 0.0) - cash_spent), 2)
            ai_cap = float(wallet.get("ai_allocated_capital") or 0.0)
            vault_cash = float(wallet.get("vault_reserve_cash") or 0.0)

            parsed_ticket = 0
            if order.order_id:
                head = str(order.order_id).split(",")[0].strip()
                if head.isdigit():
                    parsed_ticket = int(head)

            now_iso = order.timestamp or get_brt_now().isoformat()
            eff_sl = round(
                float(stop_loss if stop_loss > 0.0 else order.stop_loss),
                4,
            )

            if existing is None:
                fiscal_avg = B3TaxGuard.compute_fiscal_avg_price(
                    prev_qty=0,
                    prev_fiscal_avg=0.0,
                    buy_qty=int(order.quantity),
                    buy_price=float(order.price),
                    b3_fee_rate=settings.B3_FEE_RATE,
                )
                pos = Position(
                    ticker=clean_tk,
                    quantity=int(order.quantity),
                    entry_price=round(float(order.price), 4),
                    current_price=round(float(order.price), 4),
                    stop_loss=eff_sl,
                    opened_at=now_iso,
                    highest_price=round(float(order.price), 4),
                    partial_tp_taken=False,
                    do_not_touch=False,
                    ticket=parsed_ticket,
                    fiscal_avg_price=fiscal_avg,
                )
            else:
                new_qty = existing.quantity + int(order.quantity)
                weighted_entry = round(
                    ((existing.quantity * existing.entry_price) + (int(order.quantity) * float(order.price)))
                    / new_qty,
                    4,
                )
                fiscal_avg = B3TaxGuard.compute_fiscal_avg_price(
                    prev_qty=existing.quantity,
                    prev_fiscal_avg=existing.fiscal_avg_price,
                    buy_qty=int(order.quantity),
                    buy_price=float(order.price),
                    b3_fee_rate=settings.B3_FEE_RATE,
                )
                pos = Position(
                    ticker=clean_tk,
                    quantity=new_qty,
                    entry_price=weighted_entry,
                    current_price=round(float(order.price), 4),
                    stop_loss=eff_sl if eff_sl > 0.0 else existing.stop_loss,
                    opened_at=existing.opened_at or now_iso,
                    highest_price=max(existing.highest_price, round(float(order.price), 4)),
                    partial_tp_taken=existing.partial_tp_taken,
                    do_not_touch=existing.do_not_touch,
                    ticket=existing.ticket or parsed_ticket,
                    fiscal_avg_price=fiscal_avg,
                )

            same_day_sell = self.trading_repo.has_same_day_action(
                user_id=user_id,
                wallet_id=wallet_id,
                ticker=clean_tk,
                action="SELL",
                day_iso_prefix=now_iso[:10],
            )

            self.wallets_repo.update_wallet_balances(
                user_id=user_id,
                wallet_id=wallet_id,
                free_cash=new_free_cash,
                ai_allocated_capital=ai_cap,
                vault_reserve_cash=vault_cash,
            )
            self.trading_repo.upsert_position(user_id, wallet_id, pos)
            self.trading_repo.record_trade(
                user_id=user_id,
                wallet_id=wallet_id,
                ticker=clean_tk,
                action="BUY",
                quantity=int(order.quantity),
                price=float(order.price),
                fee=fee,
                realized_pnl=0.0,
                stop_loss=pos.stop_loss,
                is_day_trade=same_day_sell,
                client_order_id=order.client_order_id,
                reasoning=reasoning,
                timestamp=now_iso,
            )
            self.capital_repo.record_d2_net_activity(
                user_id=user_id,
                wallet_id=wallet_id,
                action="BUY",
                gross_amount=order.total_value,
                fee=fee,
                trade_date_str=now_iso[:10],
            )
            return pos

    def apply_filled_sell(
        self,
        user_id: int,
        wallet_id: int,
        order: Order,
        reasoning: str,
        is_day_trade: bool = False,
    ) -> float:
        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")
            if int(order.quantity) <= 0 or float(order.price) <= 0.0:
                raise ValueError("Ordem de venda inválida (quantidade ou preço não positivo).")

            if order.client_order_id:
                dup = self.trading_repo.find_trade_by_client_order_id(
                    user_id,
                    wallet_id,
                    order.client_order_id,
                )
                if dup is not None:
                    return round(float(dup.get("realized_pnl") or 0.0), 2)

            clean_tk = normalize_b3_ticker(order.ticker)
            pos = self.trading_repo.get_position(user_id, wallet_id, clean_tk)
            sold_qty = min(int(order.quantity), pos.quantity) if pos is not None else int(order.quantity)
            gross_sale = round(sold_qty * float(order.price), 2)
            fee = (
                round(float(order.fee), 4)
                if float(order.fee) > 0.0
                else round(gross_sale * settings.B3_FEE_RATE, 4)
            )
            fiscal_basis = (
                pos.fiscal_avg_price
                if (pos is not None and pos.fiscal_avg_price > 0.0)
                else (pos.entry_price if pos is not None else float(order.price))
            )
            realized_pnl = round((float(order.price) - fiscal_basis) * sold_qty - fee, 2)

            now_iso = order.timestamp or get_brt_now().isoformat()
            actual_day_trade = bool(
                is_day_trade
                or self.trading_repo.has_same_day_action(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    ticker=clean_tk,
                    action="BUY",
                    day_iso_prefix=now_iso[:10],
                )
            )

            if pos is not None:
                rem_qty = pos.quantity - sold_qty
                if rem_qty <= 0:
                    self.trading_repo.delete_position(user_id, wallet_id, clean_tk)
                else:
                    pos.quantity = rem_qty
                    pos.current_price = round(float(order.price), 4)
                    pos.highest_price = max(pos.highest_price, round(float(order.price), 4))
                    self.trading_repo.upsert_position(user_id, wallet_id, pos)

            net_proceeds = round(gross_sale - fee, 2)
            new_free_cash = round(max(0.0, float(wallet.get("free_cash") or 0.0) + net_proceeds), 2)
            ai_cap = float(wallet.get("ai_allocated_capital") or 0.0)
            vault_cash = float(wallet.get("vault_reserve_cash") or 0.0)

            self.wallets_repo.update_wallet_balances(
                user_id=user_id,
                wallet_id=wallet_id,
                free_cash=new_free_cash,
                ai_allocated_capital=ai_cap,
                vault_reserve_cash=vault_cash,
            )
            self.trading_repo.record_trade(
                user_id=user_id,
                wallet_id=wallet_id,
                ticker=clean_tk,
                action="SELL",
                quantity=sold_qty,
                price=float(order.price),
                fee=fee,
                realized_pnl=realized_pnl,
                stop_loss=pos.stop_loss if pos is not None else 0.0,
                is_day_trade=actual_day_trade,
                client_order_id=order.client_order_id,
                reasoning=reasoning,
                timestamp=now_iso,
            )
            self.capital_repo.record_d2_net_activity(
                user_id=user_id,
                wallet_id=wallet_id,
                action="SELL",
                gross_amount=gross_sale,
                fee=fee,
                trade_date_str=now_iso[:10],
            )
            return realized_pnl
