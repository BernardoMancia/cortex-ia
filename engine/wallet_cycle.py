from __future__ import annotations

import threading
import time
from typing import Any

from brokers.base import BrokerConnector
from brokers.registry import is_connector_ready
from config.settings import OWNER_USER_ID, settings
from domain.b3_rules import format_brl, get_brt_now, is_b3_pf_20k_exempt_eligible_stock
from domain.b3_tax_guard import B3TaxGuard
from domain.financial_firewall import FinancialFirewall
from domain.models import (
    GlobalOperationalMode,
    MarketCycleSnapshot,
    OrderSide,
    OrderStatus,
    WalletOperationalMode,
)
from domain.operational_guard import OperationalGuard, parse_iso_brt
from engine.panic_service import PanicService
from notifications.telegram_client import TelegramClient
from observability.logger import get_logger
from quant.decision import DecisionEngine
from quant.risk_manager import WalletRiskManager
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection
from storage.trading_repo import TradingRepository
from storage.wallets_repo import WalletsRepository
from wallets.capital_service import WalletCapitalService
from wallets.portfolio_manager import WalletPortfolioManager
from wallets.runtime_registry import get_wallet_lock, resolve_connector_for_wallet

logger = get_logger("engine.wallet_cycle")


class WalletCycleRunner:
    def __init__(
        self,
        db: DatabaseConnection | None = None,
        firewall: FinancialFirewall | None = None,
        tax_guard: B3TaxGuard | None = None,
        panic_service: PanicService | None = None,
        telegram: TelegramClient | None = None,
    ) -> None:
        self.db = db or DatabaseConnection()
        self.wallets_repo = WalletsRepository(self.db)
        self.trading_repo = TradingRepository(self.db)
        self.audit_repo = AuditRepository(self.db)
        self.portfolio_manager = WalletPortfolioManager(self.db)
        self.capital_service = WalletCapitalService(self.db)
        self.decision_engine = DecisionEngine()
        self.risk_manager = WalletRiskManager()
        self.firewall = firewall or FinancialFirewall(
            max_single_order_notional=settings.MAX_SINGLE_ORDER_NOTIONAL,
            max_orders_per_minute=settings.MAX_ORDERS_PER_MINUTE,
        )
        self.tax_guard = tax_guard or B3TaxGuard(
            exemption_limit=settings.B3_SWING_EXEMPTION_LIMIT,
            guard_buffer=settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
        )
        self.telegram = telegram or TelegramClient()
        self.panic_service = panic_service or PanicService(db=self.db, telegram=self.telegram)
        self._snapshot_throttle_lock = threading.Lock()
        self._last_snapshot_mono: dict[int, float] = {}

    def _should_record_equity_snapshot(self, wallet_id: int, min_interval_sec: float = 60.0) -> bool:
        now_m = time.monotonic()
        with self._snapshot_throttle_lock:
            prev = self._last_snapshot_mono.get(wallet_id)
            if prev is not None and (now_m - prev) < min_interval_sec:
                return False
            self._last_snapshot_mono[wallet_id] = now_m
            return True

    def _notify_trade(
        self,
        user_id: int,
        wallet_row: dict[str, Any],
        message: str,
    ) -> None:
        chat_id = str(wallet_row.get("telegram_chat_id") or "").strip()
        if chat_id:
            self.telegram.send_wallet_notification(chat_id, message)
        elif int(user_id) == OWNER_USER_ID:
            self.telegram.send_admin_alert(message)

    def _execute_sell_order(
        self,
        user_id: int,
        wallet_id: int,
        wallet_row: dict[str, Any],
        connector: BrokerConnector,
        cycle_id: str,
        ticker: str,
        quantity: int,
        ref_price: float,
        reasoning: str,
        bought_same_day: bool,
        is_emergency: bool = False,
    ) -> tuple[bool, float]:
        coid = self.firewall.generate_client_order_id(
            wallet_id=wallet_id,
            cycle_id=cycle_id,
            symbol=ticker,
            side=OrderSide.SELL.value,
            quantity=quantity,
        )
        if (
            self.firewall.get_cached_execution(coid) is not None
            or self.trading_repo.find_trade_by_client_order_id(user_id, wallet_id, coid) is not None
        ):
            return False, 0.0

        verdict = self.firewall.validate_order(
            symbol=ticker,
            side=OrderSide.SELL.value,
            quantity=quantity,
            price=ref_price,
            wallet_id=wallet_id,
            is_emergency_liquidation=is_emergency,
        )
        if not verdict.allowed:
            logger.warning("Firewall bloqueou venda em %s (carteira %d): %s", ticker, wallet_id, verdict.reason)
            return False, 0.0

        order = connector.place_order(
            symbol=ticker,
            side=OrderSide.SELL,
            quantity=quantity,
            expected_price=ref_price,
            stop_loss=0.0,
            client_order_id=coid,
            bypass_guards=is_emergency,
        )
        if order.status not in (OrderStatus.FILLED, OrderStatus.PARTIAL) or order.quantity <= 0:
            logger.warning(
                "Venda rejeitada na corretora para %s (carteira %d): %s",
                ticker,
                wallet_id,
                order.message,
            )
            return False, 0.0

        order.client_order_id = order.client_order_id or coid
        self.firewall.record_execution(
            coid,
            {
                "order_id": order.order_id,
                "status": order.status.value,
                "quantity": order.quantity,
                "price": order.price,
            },
        )
        pnl = self.portfolio_manager.apply_filled_sell(
            user_id=user_id,
            wallet_id=wallet_id,
            order=order,
            reasoning=reasoning,
            is_day_trade=bought_same_day,
        )
        self._notify_trade(
            user_id,
            wallet_row,
            (
                f"[{settings.ENVIRONMENT_LABEL}] VENDA EXECUTADA — {ticker}\n"
                f"Carteira: {wallet_row.get('name')}\n"
                f"Qtd: {order.quantity} @ {format_brl(order.price)}\n"
                f"Resultado líquido: {format_brl(pnl)}\n"
                f"Motivo: {reasoning[:180]}"
            ),
        )
        return True, round(order.quantity * order.price, 2)

    def _run_wallet_step_inner(
        self,
        wallet_row: dict[str, Any],
        snapshot: MarketCycleSnapshot,
        global_state: dict[str, Any],
    ) -> dict[str, Any]:
        user_id = int(wallet_row["user_id"])
        wallet_id = int(wallet_row["id"])
        actor_name = str(wallet_row.get("username") or f"user_{user_id}")
        auto_panic_reason: str | None = None

        w_lock = get_wallet_lock(wallet_id)
        acquired = w_lock.acquire(timeout=max(1.5, settings.WALLET_EXECUTION_TIMEOUT_SECONDS - 0.5))
        if not acquired:
            raise TimeoutError(f"Não foi possível adquirir o lock da carteira {wallet_id} dentro do tempo limite.")

        buys_executed = 0
        sells_executed = 0
        decisions_logged = 0

        try:
            fresh_wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id) or dict(wallet_row)
            if "username" not in fresh_wallet and "username" in wallet_row:
                fresh_wallet["username"] = wallet_row["username"]

            if str(fresh_wallet.get("operational_mode") or "").upper() == WalletOperationalMode.PANIC.value:
                until_dt = parse_iso_brt(fresh_wallet.get("panic_until"))
                if until_dt is not None and get_brt_now() >= until_dt:
                    self.wallets_repo.clear_wallet_panic(user_id, wallet_id, restore_mode="ACTIVE")
                    self.audit_repo.record_event(
                        event_type="WALLET_PANIC_AUTO_EXPIRED",
                        actor_user_id=user_id,
                        target_wallet_id=wallet_id,
                        ip_address="system",
                        details={"expired_at": get_brt_now().isoformat()},
                    )
                    fresh_wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id) or fresh_wallet

            gate = OperationalGuard.evaluate_wallet(global_state, fresh_wallet)

            ctype = str(fresh_wallet.get("connector_type") or "")
            connector: BrokerConnector | None = None
            if is_connector_ready(ctype):
                connector = resolve_connector_for_wallet(fresh_wallet, wallets_repo=self.wallets_repo)

            portfolio_snap = self.portfolio_manager.get_portfolio_snapshot(
                user_id=user_id,
                wallet_id=wallet_id,
                quotes=snapshot.quotes,
            )

            if connector is not None and gate.can_execute_orders and snapshot.market_open:
                self.capital_service.check_and_trigger_profit_goal(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    current_equity=float(portfolio_snap["operational_equity"]),
                    connector=connector,
                )

            if self._should_record_equity_snapshot(wallet_id):
                self.trading_repo.record_equity_snapshot(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    total_equity=float(portfolio_snap["total_equity"]),
                    operational_equity=float(portfolio_snap["operational_equity"]),
                    free_cash=float(portfolio_snap["free_cash"]),
                    allocated_capital=float(portfolio_snap["allocated_capital"]),
                    vault_reserve_cash=float(portfolio_snap["vault_reserve_cash"]),
                    pnl_percent=float(portfolio_snap["pnl_percent"]),
                    daily_pnl_percent=float(portfolio_snap["daily_pnl_percent"]),
                )

            positions = self.trading_repo.list_positions(user_id, wallet_id)
            month_sells = self.trading_repo.get_month_sell_trades(user_id, wallet_id)
            running_month_eligible_sales = self.tax_guard.summarize_month(
                month_sells
            ).gross_exempt_eligible_sales

            protect_20k = bool(fresh_wallet.get("protect_20k_exemption", settings.PROTECT_B3_20K_EXEMPTION))
            avoid_day_trade = bool(fresh_wallet.get("avoid_day_trade_tax", settings.AVOID_DAY_TRADE_TAX))
            today_str = get_brt_now().date().isoformat()

            for pos in list(positions):
                quote_obj = snapshot.quotes.get(pos.ticker)
                curr_px = (
                    float(quote_obj.price)
                    if (quote_obj is not None and quote_obj.price > 0.0)
                    else float(pos.current_price)
                )
                if curr_px <= 0.0:
                    continue

                bought_same_day = (pos.opened_at[:10] == today_str) or self.trading_repo.has_same_day_action(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    ticker=pos.ticker,
                    action="BUY",
                    day_iso_prefix=today_str,
                )

                prev_stop = float(pos.stop_loss)
                (
                    exit_action,
                    sell_qty,
                    new_stop,
                    next_partial_tp_taken,
                    exit_reason,
                ) = self.risk_manager.evaluate_open_position_exit(
                    pos=pos,
                    current_price=curr_px,
                    wallet_row=fresh_wallet,
                    bought_same_day=bought_same_day,
                )

                if new_stop > (prev_stop + 1e-6) and exit_action is None:
                    pos.stop_loss = new_stop
                    pos.current_price = curr_px
                    pos.highest_price = max(pos.highest_price, curr_px)
                    self.trading_repo.upsert_position(user_id, wallet_id, pos)
                    if connector is not None and gate.can_execute_orders:
                        try:
                            connector.update_position_stop_loss(pos.ticker, pos.ticket, new_stop)
                        except Exception as exc:
                            logger.warning(
                                "Falha ao atualizar stop nativo de %s na carteira %d: %s",
                                pos.ticker,
                                wallet_id,
                                exc,
                            )

                if not (gate.can_execute_orders and snapshot.market_open and connector is not None):
                    continue

                if exit_action == "STOP_LOSS" and sell_qty > 0 and not pos.do_not_touch:
                    ok_sl, gross_sl = self._execute_sell_order(
                        user_id=user_id,
                        wallet_id=wallet_id,
                        wallet_row=fresh_wallet,
                        connector=connector,
                        cycle_id=f"{snapshot.cycle_id}_sl",
                        ticker=pos.ticker,
                        quantity=sell_qty,
                        ref_price=curr_px,
                        reasoning=exit_reason,
                        bought_same_day=bought_same_day,
                        is_emergency=True,
                    )
                    if ok_sl:
                        sells_executed += 1
                        if not bought_same_day and is_b3_pf_20k_exempt_eligible_stock(pos.ticker):
                            running_month_eligible_sales += gross_sl

                elif exit_action == "PARTIAL_TP" and sell_qty > 0 and not pos.do_not_touch:
                    projected_gross = round(sell_qty * curr_px, 2)
                    defer, defer_reason = self.tax_guard.should_defer_discretionary_profit_sell(
                        ticker=pos.ticker,
                        projected_sale_gross=projected_gross,
                        current_month_eligible_sales=running_month_eligible_sales,
                        protect_enabled=protect_20k,
                        is_stop_loss_or_emergency=False,
                    )
                    if defer:
                        self.trading_repo.record_decision(
                            user_id=user_id,
                            wallet_id=wallet_id,
                            ticker=pos.ticker,
                            action="HOLD",
                            confidence=0.80,
                            trend_signal="NEUTRAL",
                            sentiment_score=0.0,
                            reasoning=defer_reason,
                        )
                        decisions_logged += 1
                    else:
                        ok_tp, gross_tp = self._execute_sell_order(
                            user_id=user_id,
                            wallet_id=wallet_id,
                            wallet_row=fresh_wallet,
                            connector=connector,
                            cycle_id=f"{snapshot.cycle_id}_ptp",
                            ticker=pos.ticker,
                            quantity=sell_qty,
                            ref_price=curr_px,
                            reasoning=exit_reason,
                            bought_same_day=bought_same_day,
                            is_emergency=False,
                        )
                        if ok_tp:
                            sells_executed += 1
                            if not bought_same_day and is_b3_pf_20k_exempt_eligible_stock(pos.ticker):
                                running_month_eligible_sales += gross_tp
                            rem_pos = self.trading_repo.get_position(user_id, wallet_id, pos.ticker)
                            if rem_pos is not None:
                                rem_pos.partial_tp_taken = bool(next_partial_tp_taken or True)
                                rem_pos.stop_loss = max(rem_pos.stop_loss, new_stop)
                                self.trading_repo.upsert_position(user_id, wallet_id, rem_pos)
                                try:
                                    connector.update_position_stop_loss(
                                        rem_pos.ticker,
                                        rem_pos.ticket,
                                        rem_pos.stop_loss,
                                    )
                                except Exception:
                                    pass

            positions = self.trading_repo.list_positions(user_id, wallet_id)
            owned_map = {p.ticker: p for p in positions}
            candidates = self.decision_engine.evaluate_universe(
                snapshot=snapshot,
                owned_tickers=set(owned_map.keys()),
            )

            if gate.can_observe_signals:
                actionable = [c for c in candidates if c.action in ("BUY", "SELL")]
                to_log = actionable[:6] if actionable else candidates[:2]
                mode_tag = ""
                if not gate.can_execute_orders:
                    mode_tag = (
                        "[MANUTENÇÃO] "
                        if gate.effective_mode == GlobalOperationalMode.MAINTENANCE.value
                        else "[MODO OBSERVAÇÃO] "
                    )
                for cand in to_log:
                    self.trading_repo.record_decision(
                        user_id=user_id,
                        wallet_id=wallet_id,
                        ticker=cand.ticker,
                        action=cand.action,
                        confidence=cand.confidence,
                        trend_signal=cand.trend_signal,
                        sentiment_score=cand.sentiment_score,
                        reasoning=f"{mode_tag}{cand.reasoning}",
                    )
                    decisions_logged += 1

            if gate.can_execute_orders and snapshot.market_open and connector is not None:
                for cand in candidates:
                    if cand.action != "SELL":
                        continue
                    held = owned_map.get(cand.ticker)
                    if held is None or held.do_not_touch or held.quantity <= 0:
                        continue

                    bought_same_day = (held.opened_at[:10] == today_str) or self.trading_repo.has_same_day_action(
                        user_id=user_id,
                        wallet_id=wallet_id,
                        ticker=held.ticker,
                        action="BUY",
                        day_iso_prefix=today_str,
                    )
                    if avoid_day_trade and bought_same_day:
                        continue

                    q_sell = snapshot.quotes.get(held.ticker)
                    sell_px = (
                        float(q_sell.bid if q_sell.bid > 0.0 else q_sell.price)
                        if q_sell is not None and q_sell.price > 0.0
                        else float(cand.price)
                    )
                    if sell_px <= 0.0:
                        continue

                    proj_gross = round(held.quantity * sell_px, 2)
                    defer, _ = self.tax_guard.should_defer_discretionary_profit_sell(
                        ticker=held.ticker,
                        projected_sale_gross=proj_gross,
                        current_month_eligible_sales=running_month_eligible_sales,
                        protect_enabled=protect_20k,
                        is_stop_loss_or_emergency=False,
                    )
                    if defer:
                        continue

                    ok_sig_sell, gross_sig_sell = self._execute_sell_order(
                        user_id=user_id,
                        wallet_id=wallet_id,
                        wallet_row=fresh_wallet,
                        connector=connector,
                        cycle_id=snapshot.cycle_id,
                        ticker=held.ticker,
                        quantity=held.quantity,
                        ref_price=sell_px,
                        reasoning=cand.reasoning,
                        bought_same_day=bought_same_day,
                        is_emergency=False,
                    )
                    if ok_sig_sell:
                        sells_executed += 1
                        owned_map.pop(held.ticker, None)
                        if not bought_same_day and is_b3_pf_20k_exempt_eligible_stock(held.ticker):
                            running_month_eligible_sales += gross_sig_sell

                fresh_wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id) or fresh_wallet
                portfolio_snap = self.portfolio_manager.get_portfolio_snapshot(
                    user_id=user_id,
                    wallet_id=wallet_id,
                    quotes=snapshot.quotes,
                )
                positions = self.trading_repo.list_positions(user_id, wallet_id)

                for cand in candidates:
                    if cand.action != "BUY":
                        continue
                    q_buy = snapshot.quotes.get(cand.ticker)
                    if q_buy is not None and q_buy.is_stale:
                        continue

                    if avoid_day_trade and self.trading_repo.has_same_day_action(
                        user_id=user_id,
                        wallet_id=wallet_id,
                        ticker=cand.ticker,
                        action="SELL",
                        day_iso_prefix=today_str,
                    ):
                        continue

                    buy_px = (
                        float(q_buy.ask if q_buy.ask > 0.0 else q_buy.price)
                        if q_buy is not None and q_buy.price > 0.0
                        else float(cand.price)
                    )
                    if buy_px <= 0.0:
                        continue

                    available_ai_cash = float(portfolio_snap.get("available_for_ai", 0.0) or 0.0)
                    operational_eq = float(portfolio_snap.get("operational_equity", 0.0) or 0.0)
                    daily_pnl_pct = float(portfolio_snap.get("daily_pnl_percent", 0.0) or 0.0)
                    stop_loss = (
                        float(cand.suggested_stop_loss)
                        if cand.suggested_stop_loss > 0.0
                        else round(buy_px * (1.0 - float(fresh_wallet.get("stop_loss_pct") or settings.STOP_LOSS_PERCENT)), 2)
                    )

                    qty, _ = self.risk_manager.compute_buy_quantity(
                        ticker=cand.ticker,
                        price=buy_px,
                        stop_loss=stop_loss,
                        operational_equity=operational_eq,
                        available_ai_cash=available_ai_cash,
                        positions=positions,
                        wallet_row=fresh_wallet,
                        daily_pnl_pct=daily_pnl_pct,
                    )
                    if qty <= 0:
                        continue

                    coid = self.firewall.generate_client_order_id(
                        wallet_id=wallet_id,
                        cycle_id=snapshot.cycle_id,
                        symbol=cand.ticker,
                        side=OrderSide.BUY.value,
                        quantity=qty,
                    )
                    if (
                        self.firewall.get_cached_execution(coid) is not None
                        or self.trading_repo.find_trade_by_client_order_id(user_id, wallet_id, coid) is not None
                    ):
                        continue

                    verdict = self.firewall.validate_order(
                        symbol=cand.ticker,
                        side=OrderSide.BUY.value,
                        quantity=qty,
                        price=buy_px,
                        wallet_id=wallet_id,
                        wallet_ceiling=available_ai_cash,
                        is_emergency_liquidation=False,
                    )
                    if not verdict.allowed:
                        self.audit_repo.record_event(
                            event_type="FIREWALL_ORDER_BLOCKED",
                            actor_user_id=user_id,
                            target_wallet_id=wallet_id,
                            ip_address="engine",
                            details={
                                "ticker": cand.ticker,
                                "quantity": qty,
                                "price": buy_px,
                                "reason": verdict.reason,
                                "trigger_panic": verdict.trigger_panic,
                            },
                        )
                        if verdict.trigger_panic:
                            auto_panic_reason = verdict.reason
                            break
                        continue

                    order = connector.place_order(
                        symbol=cand.ticker,
                        side=OrderSide.BUY,
                        quantity=qty,
                        expected_price=buy_px,
                        stop_loss=stop_loss,
                        client_order_id=coid,
                        bypass_guards=False,
                    )
                    if order.status in (OrderStatus.FILLED, OrderStatus.PARTIAL) and order.quantity > 0:
                        order.client_order_id = order.client_order_id or coid
                        self.firewall.record_execution(
                            coid,
                            {
                                "order_id": order.order_id,
                                "status": order.status.value,
                                "quantity": order.quantity,
                                "price": order.price,
                            },
                        )
                        self.portfolio_manager.apply_filled_buy(
                            user_id=user_id,
                            wallet_id=wallet_id,
                            order=order,
                            stop_loss=stop_loss,
                            reasoning=cand.reasoning,
                        )
                        buys_executed += 1
                        self._notify_trade(
                            user_id,
                            fresh_wallet,
                            (
                                f"[{settings.ENVIRONMENT_LABEL}] COMPRA EXECUTADA — {cand.ticker}\n"
                                f"Carteira: {fresh_wallet.get('name')}\n"
                                f"Qtd: {order.quantity} @ {format_brl(order.price)} (Stop: {format_brl(stop_loss)})\n"
                                f"Motivo: {cand.reasoning[:180]}"
                            ),
                        )
                        fresh_wallet = self.wallets_repo.get_owned_wallet(user_id, wallet_id) or fresh_wallet
                        portfolio_snap = self.portfolio_manager.get_portfolio_snapshot(
                            user_id=user_id,
                            wallet_id=wallet_id,
                            quotes=snapshot.quotes,
                        )
                        positions = self.trading_repo.list_positions(user_id, wallet_id)
        finally:
            w_lock.release()

        if auto_panic_reason:
            self.panic_service.activate_wallet_panic(
                user_id=user_id,
                wallet_id=wallet_id,
                actor_username="FinancialFirewall",
                duration_preset="1h",
                reason=auto_panic_reason,
                liquidate_positions=False,
                ip_address="firewall",
            )

        return {
            "ok": True,
            "wallet_id": wallet_id,
            "user_id": user_id,
            "username": actor_name,
            "effective_mode": "PANIC" if auto_panic_reason else gate.effective_mode,
            "buys_executed": buys_executed,
            "sells_executed": sells_executed,
            "decisions_logged": decisions_logged,
            "auto_panic_triggered": bool(auto_panic_reason),
        }

    def run_wallet_cycle(
        self,
        wallet_row: dict[str, Any],
        snapshot: MarketCycleSnapshot,
        global_state: dict[str, Any],
    ) -> dict[str, Any]:
        user_id = int(wallet_row["user_id"])
        wallet_id = int(wallet_row["id"])
        timeout_sec = max(2.0, float(settings.WALLET_EXECUTION_TIMEOUT_SECONDS))

        result_holder: list[dict[str, Any]] = []
        error_holder: list[BaseException] = []

        def _worker() -> None:
            try:
                res = self._run_wallet_step_inner(wallet_row, snapshot, global_state)
                result_holder.append(res)
            except BaseException as exc:
                error_holder.append(exc)

        thread = threading.Thread(
            target=_worker,
            name=f"cortex-wallet-{wallet_id}",
            daemon=True,
        )
        thread.start()
        thread.join(timeout=timeout_sec)

        try:
            if thread.is_alive():
                raise TimeoutError(
                    f"Bulkhead timeout ({timeout_sec:.1f}s) excedido na carteira {wallet_id}."
                )
            if error_holder:
                err = error_holder[0]
                if isinstance(err, Exception):
                    raise err
                raise RuntimeError(str(err))

            self.wallets_repo.reset_wallet_error_strikes(user_id, wallet_id)
            return result_holder[0]
        except Exception as exc:
            strikes = self.wallets_repo.record_wallet_error_strike(user_id, wallet_id)
            logger.error(
                "Erro isolado pelo Bulkhead na carteira %d (strike %d): %s",
                wallet_id,
                strikes,
                exc,
            )
            if strikes >= 3:
                self.audit_repo.record_event(
                    event_type="WALLET_PAUSED_BROKER_ERRORS",
                    actor_user_id=user_id,
                    target_wallet_id=wallet_id,
                    ip_address="engine",
                    details={"consecutive_errors": strikes, "last_error": str(exc)[:240]},
                )
            return {
                "ok": False,
                "wallet_id": wallet_id,
                "user_id": user_id,
                "error": str(exc),
                "consecutive_errors": strikes,
            }
