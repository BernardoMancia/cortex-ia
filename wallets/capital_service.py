from __future__ import annotations

import math
import time
from typing import Any

from brokers.base import BrokerConnector
from config.settings import settings
from domain.b3_tax_guard import B3TaxGuard
from domain.models import OrderSide, OrderStatus
from storage.audit_repo import AuditRepository
from storage.capital_repo import CapitalRepository
from storage.connection import DatabaseConnection
from storage.trading_repo import TradingRepository
from storage.wallets_repo import WalletsRepository
from wallets.portfolio_manager import WalletPortfolioManager
from wallets.runtime_registry import get_wallet_lock


class WalletCapitalService:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()
        self.wallets_repo = WalletsRepository(self.db)
        self.trading_repo = TradingRepository(self.db)
        self.capital_repo = CapitalRepository(self.db)
        self.audit_repo = AuditRepository(self.db)
        self.portfolio_manager = WalletPortfolioManager(self.db)
        self.tax_guard = B3TaxGuard(
            exemption_limit=settings.B3_SWING_EXEMPTION_LIMIT,
            guard_buffer=settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
        )

    def sync_with_broker(
        self,
        user_id: int,
        wallet_id: int,
        connector: BrokerConnector,
    ) -> dict[str, Any]:
        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")

            acc = connector.get_account_summary()
            if not bool(acc.get("connected", False)):
                return {
                    "connected": False,
                    "synced": False,
                    "message": str(acc.get("message") or "Corretora desconectada no momento."),
                }

            broker_positions = connector.get_broker_positions()
            pos_sync = self.portfolio_manager.reconcile_positions_with_broker(
                user_id=user_id,
                wallet_id=wallet_id,
                broker_positions=broker_positions,
                default_stop_loss_pct=float(wallet.get("stop_loss_pct") or settings.STOP_LOSS_PERCENT),
            )

            balance = max(0.0, float(acc.get("balance", 0.0) or 0.0))
            free_margin = max(0.0, float(acc.get("free_margin", 0.0) or 0.0))
            margin_used = max(0.0, float(acc.get("margin", 0.0) or 0.0))
            raw_cash = free_margin if (free_margin > 0.0 or margin_used > 0.0) else balance

            vault_cash = round(
                min(raw_cash, max(0.0, float(wallet.get("vault_reserve_cash") or 0.0))),
                2,
            )
            new_free_cash = round(max(0.0, raw_cash - vault_cash), 2)

            invested_val = round(
                sum(p.market_value for p in self.trading_repo.list_positions(user_id, wallet_id)),
                2,
            )
            init_cap = round(float(wallet.get("initial_capital") or 0.0), 2)
            ai_cap = round(float(wallet.get("ai_allocated_capital") or 0.0), 2)
            real_total = round(raw_cash + invested_val, 2)

            if init_cap <= 0.0 and (balance > 0.0 or real_total > 0.0):
                init_cap = real_total if real_total > 0.0 else round(balance, 2)
                if ai_cap <= 0.0:
                    ai_cap = init_cap
                self.wallets_repo.update_wallet_balances(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    free_cash=new_free_cash,
                    ai_allocated_capital=ai_cap,
                    vault_reserve_cash=vault_cash,
                    initial_capital=init_cap,
                )
            else:
                self.wallets_repo.update_wallet_balances(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    free_cash=new_free_cash,
                    ai_allocated_capital=ai_cap,
                    vault_reserve_cash=vault_cash,
                )

            self.wallets_repo.reset_wallet_error_strikes(user_id, wallet_id)
            return {
                "connected": True,
                "synced": True,
                "broker_balance": round(balance, 2),
                "broker_free_margin": round(free_margin, 2),
                "free_cash": new_free_cash,
                "vault_reserve_cash": vault_cash,
                "ai_allocated_capital": ai_cap,
                "initial_capital": init_cap,
                "positions_synced": pos_sync["synced_count"],
                "removed_ghosts": pos_sync["removed_ghosts"],
                "server": str(acc.get("server", "") or ""),
                "login": str(acc.get("login", "") or ""),
            }

    def set_ai_allocation_ceiling(
        self,
        user_id: int,
        wallet_id: int,
        ceiling_brl: float,
    ) -> dict[str, Any]:
        if not math.isfinite(ceiling_brl) or float(ceiling_brl) < 0.0:
            raise ValueError("Informe um teto operacional válido (maior ou igual a zero).")

        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")

            new_ceiling = round(float(ceiling_brl), 2)
            free_cash = round(float(wallet.get("free_cash") or 0.0), 2)
            vault_cash = round(float(wallet.get("vault_reserve_cash") or 0.0), 2)
            init_cap = float(wallet.get("initial_capital") or 0.0)

            if init_cap <= 0.0 and (free_cash + vault_cash) > 0.0:
                self.wallets_repo.update_wallet_balances(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    free_cash=free_cash,
                    ai_allocated_capital=new_ceiling,
                    vault_reserve_cash=vault_cash,
                    initial_capital=round(free_cash + vault_cash, 2),
                )
            else:
                self.wallets_repo.update_wallet_balances(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    free_cash=free_cash,
                    ai_allocated_capital=new_ceiling,
                    vault_reserve_cash=vault_cash,
                )

            self.capital_repo.record_transaction(
                user_id=user_id,
                wallet_id=wallet_id,
                tx_type="SET_AI_CEILING",
                amount=new_ceiling,
                resulting_ai_capital=new_ceiling,
                resulting_vault=vault_cash,
                details=f"Teto de capital da IA definido em R$ {new_ceiling:,.2f}.",
            )
            self.audit_repo.record_event(
                event_type="SET_AI_CEILING",
                actor_user_id=user_id,
                target_wallet_id=wallet_id,
                details={"ceiling_brl": new_ceiling},
            )
            return self.get_capital_summary(user_id, wallet_id)

    def deposit_to_vault(
        self,
        user_id: int,
        wallet_id: int,
        amount: float,
    ) -> dict[str, Any]:
        if not math.isfinite(amount) or float(amount) <= 0.0:
            raise ValueError("Informe um valor positivo para proteger no Cofre de Reserva.")

        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")

            free_cash = round(float(wallet.get("free_cash") or 0.0), 2)
            req_amt = round(float(amount), 2)
            if req_amt > (free_cash + 1e-6):
                raise ValueError(
                    f"Saldo livre insuficiente na carteira (disponível: R$ {free_cash:,.2f})."
                )

            actual = min(req_amt, free_cash)
            new_free = round(max(0.0, free_cash - actual), 2)
            new_vault = round(float(wallet.get("vault_reserve_cash") or 0.0) + actual, 2)
            ai_cap = round(float(wallet.get("ai_allocated_capital") or 0.0), 2)

            self.wallets_repo.update_wallet_balances(
                user_id=user_id,
                wallet_id=wallet_id,
                free_cash=new_free,
                ai_allocated_capital=ai_cap,
                vault_reserve_cash=new_vault,
            )
            self.capital_repo.record_transaction(
                user_id=user_id,
                wallet_id=wallet_id,
                tx_type="VAULT_LOCK",
                amount=actual,
                resulting_ai_capital=ai_cap,
                resulting_vault=new_vault,
                details=f"Caixa real blindado no Cofre de Reserva: R$ {actual:,.2f}.",
            )
            self.audit_repo.record_event(
                event_type="VAULT_LOCK",
                actor_user_id=user_id,
                target_wallet_id=wallet_id,
                details={"amount": actual, "vault_reserve_cash": new_vault},
            )
            return self.get_capital_summary(user_id, wallet_id)

    def release_from_vault(
        self,
        user_id: int,
        wallet_id: int,
        amount: float,
    ) -> dict[str, Any]:
        if not math.isfinite(amount) or float(amount) <= 0.0:
            raise ValueError("Informe um valor positivo para liberar do Cofre de Reserva.")

        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")

            vault_cash = round(float(wallet.get("vault_reserve_cash") or 0.0), 2)
            req_amt = round(float(amount), 2)
            if req_amt > (vault_cash + 1e-6):
                raise ValueError(
                    f"Saldo insuficiente no Cofre de Reserva (disponível: R$ {vault_cash:,.2f})."
                )

            actual = min(req_amt, vault_cash)
            new_vault = round(max(0.0, vault_cash - actual), 2)
            new_free = round(float(wallet.get("free_cash") or 0.0) + actual, 2)
            ai_cap = round(float(wallet.get("ai_allocated_capital") or 0.0), 2)

            self.wallets_repo.update_wallet_balances(
                user_id=user_id,
                wallet_id=wallet_id,
                free_cash=new_free,
                ai_allocated_capital=ai_cap,
                vault_reserve_cash=new_vault,
            )
            self.capital_repo.record_transaction(
                user_id=user_id,
                wallet_id=wallet_id,
                tx_type="VAULT_RELEASE",
                amount=actual,
                resulting_ai_capital=ai_cap,
                resulting_vault=new_vault,
                details=f"Saldo liberado do Cofre para Caixa Livre: R$ {actual:,.2f}.",
            )
            self.audit_repo.record_event(
                event_type="VAULT_RELEASE",
                actor_user_id=user_id,
                target_wallet_id=wallet_id,
                details={"amount": actual, "vault_reserve_cash": new_vault},
            )
            return self.get_capital_summary(user_id, wallet_id)

    def request_withdrawal_liquidation(
        self,
        user_id: int,
        wallet_id: int,
        amount: float,
        mode: str,
        connector: BrokerConnector,
    ) -> dict[str, Any]:
        if not math.isfinite(amount) or float(amount) <= 0.0:
            raise ValueError("Informe um valor positivo para liquidação de resgate.")
        clean_mode = (mode or "instant").strip().lower()
        if clean_mode not in ("instant", "on_profit"):
            raise ValueError("Modo inválido. Escolha 'instant' ou 'on_profit'.")

        req_amt = round(float(amount), 2)
        with get_wallet_lock(wallet_id):
            snap = self.portfolio_manager.get_portfolio_snapshot(user_id, wallet_id)

            if clean_mode == "on_profit":
                target_eq = round(float(snap["operational_equity"]) + req_amt, 2)
                goal = self.capital_repo.create_withdrawal_goal(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    amount=req_amt,
                    mode="on_profit",
                    target_equity=target_eq,
                )
                self.capital_repo.record_transaction(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    tx_type="WITHDRAWAL_GOAL_SET",
                    amount=req_amt,
                    resulting_ai_capital=float(snap["ai_allocated_capital"]),
                    resulting_vault=float(snap["vault_reserve_cash"]),
                    details=(
                        f"Meta de resgate no lucro agendada: R$ {req_amt:,.2f} "
                        f"ao atingir R$ {target_eq:,.2f}."
                    ),
                )
                self.audit_repo.record_event(
                    event_type="WITHDRAWAL_GOAL_SET",
                    actor_user_id=user_id,
                    target_wallet_id=wallet_id,
                    details={"amount": req_amt, "target_equity": target_eq},
                )
                return {
                    "mode": "on_profit",
                    "goal": goal,
                    "summary": self.get_capital_summary(user_id, wallet_id, connector=connector),
                }

            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")

            free_cash = float(wallet.get("free_cash") or 0.0)
            needed_from_positions = max(0.0, req_amt - free_cash)
            sold_orders: list[dict[str, Any]] = []

            if needed_from_positions > 0.01:
                positions = [
                    p
                    for p in self.trading_repo.list_positions(user_id, wallet_id)
                    if not p.do_not_touch and p.quantity > 0
                ]
                liq_total = sum(p.market_value for p in positions if p.market_value > 0.0)
                if liq_total > 0.0:
                    ratio = min(1.0, (needed_from_positions * 1.005) / liq_total)
                    rem_needed = needed_from_positions
                    ts_seed = int(time.time())
                    for idx, pos in enumerate(positions):
                        if rem_needed <= 0.01:
                            break
                        est_px = pos.current_price if pos.current_price > 0.0 else pos.entry_price
                        if est_px <= 0.0:
                            continue
                        prop_qty = int(math.ceil((pos.market_value * ratio) / est_px))
                        sell_qty = max(1, min(pos.quantity, prop_qty))
                        cid = f"wd_{wallet_id}_{ts_seed}_{pos.ticker}_{idx}"
                        order = connector.place_order(
                            symbol=pos.ticker,
                            side=OrderSide.SELL,
                            quantity=sell_qty,
                            expected_price=est_px,
                            client_order_id=cid,
                            bypass_guards=True,
                        )
                        if order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and order.quantity > 0:
                            pnl = self.portfolio_manager.apply_filled_sell(
                                user_id=user_id,
                                wallet_id=wallet_id,
                                order=order,
                                reasoning=f"Liquidação de custódia para resgate de R$ {req_amt:,.2f}.",
                            )
                            net_got = max(0.0, order.total_value - order.fee)
                            rem_needed = max(0.0, rem_needed - net_got)
                            sold_orders.append(
                                {
                                    "ticker": pos.ticker,
                                    "quantity": order.quantity,
                                    "price": order.price,
                                    "realized_pnl": pnl,
                                }
                            )

            fresh_wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if fresh_wallet is None:
                raise RuntimeError("Carteira indisponível após liquidação.")

            updated_free = float(fresh_wallet.get("free_cash") or 0.0)
            shielded = round(min(req_amt, updated_free), 2)
            final_free = round(max(0.0, updated_free - shielded), 2)
            final_vault = round(float(fresh_wallet.get("vault_reserve_cash") or 0.0) + shielded, 2)
            prev_ai_cap = float(fresh_wallet.get("ai_allocated_capital") or 0.0)
            final_ai_cap = round(max(0.0, prev_ai_cap - shielded), 2) if prev_ai_cap > 0.0 else 0.0

            self.wallets_repo.update_wallet_balances(
                user_id=user_id,
                wallet_id=wallet_id,
                free_cash=final_free,
                ai_allocated_capital=final_ai_cap,
                vault_reserve_cash=final_vault,
            )
            self.capital_repo.record_transaction(
                user_id=user_id,
                wallet_id=wallet_id,
                tx_type="WITHDRAWAL_LIQUIDATION",
                amount=shielded,
                resulting_ai_capital=final_ai_cap,
                resulting_vault=final_vault,
                details=(
                    f"Resgate preparado: R$ {shielded:,.2f} isolados no Cofre "
                    f"(liquidação B3 D+2 para PIX na corretora)."
                ),
            )
            self.audit_repo.record_event(
                event_type="WITHDRAWAL_LIQUIDATION",
                actor_user_id=user_id,
                target_wallet_id=wallet_id,
                details={
                    "requested": req_amt,
                    "shielded": shielded,
                    "sold_positions": len(sold_orders),
                },
            )
            return {
                "mode": "instant",
                "requested_amount": req_amt,
                "shielded_amount": shielded,
                "sold_orders": sold_orders,
                "summary": self.get_capital_summary(user_id, wallet_id, connector=connector),
            }

    def check_and_trigger_profit_goal(
        self,
        user_id: int,
        wallet_id: int,
        current_equity: float,
        connector: BrokerConnector,
    ) -> bool:
        with get_wallet_lock(wallet_id):
            goal = self.capital_repo.get_active_withdrawal_goal(user_id, wallet_id)
            if goal is None or str(goal.get("mode") or "") != "on_profit":
                return False
            target_eq = float(goal.get("target_equity") or 0.0)
            amt = float(goal.get("amount") or 0.0)
            if target_eq <= 0.0 or amt <= 0.0 or float(current_equity) < target_eq:
                return False
            goal_id = int(goal["id"])
            self.capital_repo.complete_withdrawal_goal(
                user_id=user_id,
                wallet_id=wallet_id,
                goal_id=goal_id,
                status="COMPLETED",
            )
            self.request_withdrawal_liquidation(
                user_id=user_id,
                wallet_id=wallet_id,
                amount=amt,
                mode="instant",
                connector=connector,
            )
            return True

    def cancel_withdrawal_goal(self, user_id: int, wallet_id: int, goal_id: int) -> None:
        with get_wallet_lock(wallet_id):
            wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
            if wallet is None:
                raise PermissionError("Carteira não pertence ao usuário autenticado.")
            self.capital_repo.complete_withdrawal_goal(
                user_id=user_id,
                wallet_id=wallet_id,
                goal_id=int(goal_id),
                status="CANCELLED",
            )
            self.audit_repo.record_event(
                event_type="WITHDRAWAL_GOAL_CANCELLED",
                actor_user_id=user_id,
                target_wallet_id=wallet_id,
                details={"goal_id": int(goal_id)},
            )

    def get_capital_summary(
        self,
        user_id: int,
        wallet_id: int,
        connector: BrokerConnector | None = None,
    ) -> dict[str, Any]:
        with get_wallet_lock(wallet_id):
            snap = self.portfolio_manager.get_portfolio_snapshot(user_id, wallet_id)
            d2 = self.capital_repo.get_d2_summary(user_id, wallet_id)
            active_goal = self.capital_repo.get_active_withdrawal_goal(user_id, wallet_id)
            month_sells = self.trading_repo.get_month_sell_trades(user_id, wallet_id)
            tax_20k = self.tax_guard.summarize_month(month_sells).to_dict()
            txs = self.capital_repo.list_transactions(user_id, wallet_id, limit=40)

            d2_pending = round(float(d2["pending_d2_receivable"]), 2)
            total_liquid_cash = round(float(snap["free_cash"]) + float(snap["vault_reserve_cash"]), 2)
            withdrawable_now = round(max(0.0, total_liquid_cash - d2_pending), 2)
            next_d2_date = next(
                (
                    str(item["settlement_date"])
                    for item in d2["schedule"]
                    if float(item.get("net_sell_amount") or 0.0) > 0.0
                ),
                None,
            )

            broker_connected: bool | None = None
            if connector is not None:
                broker_connected = bool(connector.health_check().get("connected", False))

            return {
                "wallet_id": int(wallet_id),
                "free_cash": snap["free_cash"],
                "allocated_capital": snap["invested_value"],
                "invested_value": snap["invested_value"],
                "ai_allocated_capital": snap["ai_allocated_capital"],
                "available_for_ai": snap["available_for_ai"],
                "buying_power": snap["available_for_ai"],
                "vault_reserve_cash": snap["vault_reserve_cash"],
                "initial_capital": snap["initial_capital"],
                "total_equity": snap["total_equity"],
                "operational_equity": snap["operational_equity"],
                "unrealized_pnl": snap["unrealized_pnl"],
                "cumulative_realized_pnl": snap["realized_pnl_total"],
                "realized_pnl_total": snap["realized_pnl_total"],
                "withdrawable_now": withdrawable_now,
                "settled_withdrawable_cash": withdrawable_now,
                "d2_pending_total": d2_pending,
                "unsettled_d2_cash": d2_pending,
                "next_d2_settlement_date": next_d2_date,
                "d2_schedule": d2["schedule"],
                "active_goal": active_goal,
                "tax_20k": tax_20k,
                "transactions": txs,
                "broker_connected": broker_connected,
                "is_simulation": False,
            }
