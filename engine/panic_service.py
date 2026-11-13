from __future__ import annotations

import time
from typing import Any

from brokers.registry import is_connector_ready
from config.settings import OWNER_USER_ID, settings
from domain.b3_rules import get_brt_now
from domain.financial_firewall import FinancialFirewall
from domain.models import OrderSide, OrderStatus
from domain.operational_guard import resolve_panic_until
from notifications.telegram_client import TelegramClient
from observability.logger import get_logger
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection
from storage.state_repo import StateRepository
from storage.trading_repo import TradingRepository
from storage.wallets_repo import WalletsRepository
from wallets.portfolio_manager import WalletPortfolioManager
from wallets.runtime_registry import get_wallet_lock, resolve_connector_for_wallet

logger = get_logger("engine.panic")


class PanicService:
    def __init__(
        self,
        db: DatabaseConnection | None = None,
        telegram: TelegramClient | None = None,
    ) -> None:
        self.db = db or DatabaseConnection()
        self.state_repo = StateRepository(self.db)
        self.wallets_repo = WalletsRepository(self.db)
        self.trading_repo = TradingRepository(self.db)
        self.audit_repo = AuditRepository(self.db)
        self.portfolio_manager = WalletPortfolioManager(self.db)
        self.telegram = telegram or TelegramClient()

    def _liquidate_non_protected_positions(
        self,
        user_id: int,
        wallet_id: int,
        wallet_row: dict[str, Any],
        reason: str,
        ip_address: str,
    ) -> tuple[int, list[str], list[str], list[dict[str, str]]]:
        cancelled_orders = 0
        liquidated_tickers: list[str] = []
        skipped_protected: list[str] = []
        failures: list[dict[str, str]] = []

        ctype = str(wallet_row.get("connector_type") or "")
        if not is_connector_ready(ctype):
            return cancelled_orders, liquidated_tickers, skipped_protected, failures

        try:
            connector = resolve_connector_for_wallet(wallet_row, wallets_repo=self.wallets_repo)
        except Exception as exc:
            logger.error("Falha ao resolver conector da carteira %d no pânico: %s", wallet_id, exc)
            return cancelled_orders, liquidated_tickers, skipped_protected, failures

        try:
            cancelled_orders = int(connector.cancel_all_pending_orders() or 0)
        except Exception as exc:
            logger.warning("Falha ao cancelar ordens pendentes na carteira %d: %s", wallet_id, exc)

        positions = self.trading_repo.list_positions(user_id, wallet_id)
        today_str = get_brt_now().date().isoformat()
        cycle_tag = f"panic_{int(time.time() * 1000)}"

        for pos in positions:
            if pos.do_not_touch:
                skipped_protected.append(pos.ticker)
                continue
            if pos.quantity <= 0:
                continue

            coid = FinancialFirewall.generate_client_order_id(
                wallet_id=wallet_id,
                cycle_id=cycle_tag,
                symbol=pos.ticker,
                side=OrderSide.SELL.value,
                quantity=pos.quantity,
            )
            ref_px = pos.current_price if pos.current_price > 0.0 else pos.entry_price
            try:
                order = connector.place_order(
                    symbol=pos.ticker,
                    side=OrderSide.SELL,
                    quantity=pos.quantity,
                    expected_price=ref_px,
                    stop_loss=0.0,
                    client_order_id=coid,
                    bypass_guards=True,
                )
            except Exception as exc:
                failures.append({"ticker": pos.ticker, "error": str(exc)})
                continue

            if order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and order.quantity > 0:
                order.client_order_id = order.client_order_id or coid
                same_day = (pos.opened_at[:10] == today_str) or self.trading_repo.has_same_day_action(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    ticker=pos.ticker,
                    action="BUY",
                    day_iso_prefix=today_str,
                )
                pnl = self.portfolio_manager.apply_filled_sell(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    order=order,
                    reasoning=f"Liquidação de emergência por Botão de Pânico: {reason}",
                    is_day_trade=same_day,
                )
                liquidated_tickers.append(pos.ticker)
                self.audit_repo.record_event(
                    event_type="WALLET_PANIC_LIQUIDATION",
                    actor_user_id=user_id,
                    target_wallet_id=wallet_id,
                    ip_address=ip_address,
                    details={
                        "ticker": pos.ticker,
                        "quantity": order.quantity,
                        "price": order.price,
                        "realized_pnl": pnl,
                        "order_id": order.order_id,
                    },
                )
            else:
                failures.append(
                    {
                        "ticker": pos.ticker,
                        "error": order.message or "Ordem de liquidação não executada.",
                    }
                )

        return cancelled_orders, liquidated_tickers, skipped_protected, failures

    def activate_wallet_panic(
        self,
        user_id: int,
        wallet_id: int,
        actor_username: str,
        duration_preset: str = "indefinite",
        custom_minutes: int | None = None,
        reason: str = "",
        liquidate_positions: bool = False,
        ip_address: str = "",
    ) -> dict[str, Any]:
        wallet_row = self.wallets_repo.get_owned_wallet(user_id, wallet_id)
        if wallet_row is None:
            raise PermissionError("Carteira não pertence ao usuário autenticado.")

        panic_until = resolve_panic_until(duration_preset=duration_preset, custom_minutes=custom_minutes)
        clean_reason = (reason or f"Botão de Pânico acionado por {actor_username}.").strip()[:240]

        lock = get_wallet_lock(wallet_id)
        acquired = lock.acquire(timeout=15.0)
        try:
            updated_wallet = self.wallets_repo.set_wallet_panic(
                user_id=user_id,
                wallet_id=wallet_id,
                panic_until=panic_until,
                reason=clean_reason,
            )

            cancelled_orders = 0
            liquidated_tickers: list[str] = []
            skipped_protected: list[str] = []
            failures: list[dict[str, str]] = []

            if liquidate_positions:
                (
                    cancelled_orders,
                    liquidated_tickers,
                    skipped_protected,
                    failures,
                ) = self._liquidate_non_protected_positions(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    wallet_row=wallet_row,
                    reason=clean_reason,
                    ip_address=ip_address,
                )
            else:
                ctype = str(wallet_row.get("connector_type") or "")
                if is_connector_ready(ctype):
                    try:
                        connector = resolve_connector_for_wallet(
                            wallet_row,
                            wallets_repo=self.wallets_repo,
                        )
                        cancelled_orders = int(connector.cancel_all_pending_orders() or 0)
                    except Exception as exc:
                        logger.warning(
                            "Falha ao cancelar ordens pendentes no pânico da carteira %d: %s",
                            wallet_id,
                            exc,
                        )
        finally:
            if acquired:
                lock.release()

        self.audit_repo.record_event(
            event_type="WALLET_PANIC_ACTIVATED",
            actor_user_id=user_id,
            target_wallet_id=wallet_id,
            ip_address=ip_address,
            details={
                "actor": actor_username,
                "duration_preset": duration_preset,
                "panic_until": panic_until or "indefinite",
                "reason": clean_reason,
                "liquidate_positions": bool(liquidate_positions),
                "cancelled_orders": cancelled_orders,
                "liquidated_tickers": liquidated_tickers,
                "skipped_protected": skipped_protected,
            },
        )

        alert_msg = (
            f"[{settings.ENVIRONMENT_LABEL}] PÂNICO DE CARTEIRA ATIVADO\n"
            f"Carteira: {wallet_row.get('name')} (#{wallet_id})\n"
            f"Acionado por: {actor_username}\n"
            f"Até: {panic_until or 'indefinido'}\n"
            f"Motivo: {clean_reason}"
        )
        if liquidate_positions:
            alert_msg += f"\nLiquidadas: {', '.join(liquidated_tickers) if liquidated_tickers else 'nenhuma'}"
        self.telegram.send_wallet_notification(str(wallet_row.get("telegram_chat_id") or ""), alert_msg)
        if int(user_id) == OWNER_USER_ID:
            self.telegram.send_admin_alert(alert_msg)

        return {
            "wallet": updated_wallet,
            "panic_until": panic_until,
            "cancelled_orders": cancelled_orders,
            "liquidated_tickers": liquidated_tickers,
            "skipped_protected": skipped_protected,
            "liquidation_failures": failures,
        }

    def clear_wallet_panic(
        self,
        user_id: int,
        wallet_id: int,
        actor_username: str,
        restore_mode: str = "ACTIVE",
        ip_address: str = "",
    ) -> dict[str, Any]:
        lock = get_wallet_lock(wallet_id)
        acquired = lock.acquire(timeout=10.0)
        try:
            updated_wallet = self.wallets_repo.clear_wallet_panic(
                user_id=user_id,
                wallet_id=wallet_id,
                restore_mode=restore_mode,
            )
        finally:
            if acquired:
                lock.release()

        self.audit_repo.record_event(
            event_type="WALLET_PANIC_CLEARED",
            actor_user_id=user_id,
            target_wallet_id=wallet_id,
            ip_address=ip_address,
            details={
                "actor": actor_username,
                "restore_mode": updated_wallet.get("operational_mode", restore_mode),
            },
        )

        msg = (
            f"[{settings.ENVIRONMENT_LABEL}] Pânico encerrado na carteira "
            f"{updated_wallet.get('name')} (#{wallet_id}) por {actor_username}. "
            f"Modo restaurado: {updated_wallet.get('operational_mode')}."
        )
        self.telegram.send_wallet_notification(str(updated_wallet.get("telegram_chat_id") or ""), msg)
        if int(user_id) == OWNER_USER_ID:
            self.telegram.send_admin_alert(msg)

        return {
            "wallet": updated_wallet,
            "restored_mode": updated_wallet.get("operational_mode", restore_mode),
        }

    def activate_global_panic(
        self,
        actor_user_id: int,
        actor_username: str,
        duration_preset: str = "indefinite",
        custom_minutes: int | None = None,
        reason: str = "",
        liquidate_positions: bool = False,
        ip_address: str = "",
    ) -> dict[str, Any]:
        panic_until = resolve_panic_until(duration_preset=duration_preset, custom_minutes=custom_minutes)
        clean_reason = (reason or f"Pânico Global acionado por {actor_username}.").strip()[:240]

        global_state = self.state_repo.set_global_panic(
            panic_until=panic_until,
            reason=clean_reason,
            actor_username=actor_username,
            actor_user_id=actor_user_id,
            ip_address=ip_address,
        )

        all_wallets = self.wallets_repo.list_all_executable_wallets_ordered()
        total_cancelled = 0
        liquidated_tickers: list[str] = []
        skipped_protected: list[str] = []
        failures: list[dict[str, str]] = []

        for w in all_wallets:
            uid = int(w["user_id"])
            wid = int(w["id"])
            ctype = str(w.get("connector_type") or "")
            if not is_connector_ready(ctype):
                continue

            w_lock = get_wallet_lock(wid)
            got_lock = w_lock.acquire(timeout=12.0)
            try:
                if liquidate_positions and uid == OWNER_USER_ID:
                    canc, liq, prot, fail = self._liquidate_non_protected_positions(
                        user_id=uid,
                        wallet_id=wid,
                        wallet_row=w,
                        reason=clean_reason,
                        ip_address=ip_address,
                    )
                    total_cancelled += canc
                    liquidated_tickers.extend(liq)
                    skipped_protected.extend(prot)
                    failures.extend(fail)
                else:
                    try:
                        conn = resolve_connector_for_wallet(w, wallets_repo=self.wallets_repo)
                        total_cancelled += int(conn.cancel_all_pending_orders() or 0)
                    except Exception as exc:
                        logger.warning(
                            "Falha ao cancelar ordens na carteira %d durante pânico global: %s",
                            wid,
                            exc,
                        )
            finally:
                if got_lock:
                    w_lock.release()

        urgent_msg = (
            f"ALERTA CRÍTICO — PÂNICO GLOBAL ATIVADO ({settings.ENVIRONMENT_LABEL})\n"
            f"Autor: {actor_username}\n"
            f"Vigência: {panic_until or 'indefinido'}\n"
            f"Motivo: {clean_reason}\n"
            f"Ordens pendentes canceladas: {total_cancelled}"
        )
        if liquidate_positions:
            urgent_msg += (
                f"\nPosições Luke liquidadas: "
                f"{', '.join(liquidated_tickers) if liquidated_tickers else 'nenhuma'}"
            )
        self.telegram.send_admin_alert(urgent_msg)

        return {
            "global_state": global_state,
            "panic_until": panic_until,
            "cancelled_orders": total_cancelled,
            "liquidated_tickers": liquidated_tickers,
            "skipped_protected": skipped_protected,
            "liquidation_failures": failures,
        }

    def clear_global_panic(
        self,
        actor_user_id: int,
        actor_username: str,
        ip_address: str = "",
    ) -> dict[str, Any]:
        state = self.state_repo.clear_global_panic(
            actor_username=actor_username,
            actor_user_id=actor_user_id,
            ip_address=ip_address,
        )
        self.telegram.send_admin_alert(
            f"[{settings.ENVIRONMENT_LABEL}] Pânico Global encerrado por {actor_username}. "
            f"Estado atual: {state.get('global_mode')}."
        )
        return {"global_state": state}
