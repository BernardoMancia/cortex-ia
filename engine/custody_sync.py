from __future__ import annotations

from datetime import timedelta
from typing import Any

from brokers.base import BrokerConnector
from brokers.registry import is_connector_ready
from config.settings import OWNER_USER_ID, settings
from domain.b3_rules import get_brt_now, normalize_b3_ticker
from domain.models import Position, PreFlightReport
from observability.logger import get_logger
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection
from storage.trading_repo import TradingRepository
from storage.wallets_repo import WalletsRepository
from wallets.capital_service import WalletCapitalService
from wallets.runtime_registry import get_wallet_lock, resolve_connector_for_wallet

logger = get_logger("engine.custody_sync")


class CustodyReconciler:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()
        self.audit_repo = AuditRepository(self.db)
        self.wallets_repo = WalletsRepository(self.db)
        self.trading_repo = TradingRepository(self.db)
        self.capital_service = WalletCapitalService(self.db)

    @staticmethod
    def _check_clock_sanity() -> bool:
        now_brt = get_brt_now()
        if now_brt.tzinfo is None or now_brt.year < 2025:
            return False
        offset = now_brt.utcoffset()
        return offset == timedelta(hours=-3)

    def run_preflight_check(self, wallet_row: dict[str, Any] | None = None) -> PreFlightReport:
        issues: list[str] = []

        try:
            integrity = self.audit_repo.verify_integrity()
            audit_ok = bool(integrity.get("valid", False))
        except Exception as exc:
            audit_ok = False
            issues.append(f"Erro ao verificar cadeia de auditoria: {exc}")

        if not audit_ok and not issues:
            issues.append("Integridade da cadeia criptográfica de auditoria violada.")

        clock_ok = self._check_clock_sanity()
        if not clock_ok:
            issues.append("Relógio do servidor fora do fuso horário BRT (America/Sao_Paulo UTC-3).")

        target_wallet = wallet_row or self.wallets_repo.get_default_or_first_wallet(OWNER_USER_ID)
        if target_wallet is None:
            issues.append("Carteira padrão da conta Luke não encontrada.")

        if settings.IS_SIMULATION:
            db_ok = True
            try:
                with self.db.readonly() as conn:
                    conn.execute("SELECT 1").fetchone()
            except Exception as exc:
                db_ok = False
                issues.append(f"Banco SQLite inacessível: {exc}")

            sim_balance = 0.0
            pos_count = 0
            sim_ready_wallet = False
            if target_wallet is not None:
                uid = int(target_wallet["user_id"])
                wid = int(target_wallet["id"])
                ctype = str(target_wallet.get("connector_type") or "")
                sim_ready_wallet = is_connector_ready(ctype) or ctype == "paper_b3"
                if not sim_ready_wallet:
                    issues.append(f"Conector simulado '{ctype}' não está pronto.")
                sim_balance = float(target_wallet.get("free_cash", 0.0) or 0.0)
                pos_count = len(self.trading_repo.list_positions(uid, wid))

            ready = bool(db_ok and audit_ok and clock_ok and sim_ready_wallet and not issues)
            return PreFlightReport(
                ready=ready,
                bridge_online=True,
                broker_authenticated=True,
                account_server="B3-Simulation",
                broker_balance=round(sim_balance, 2),
                custody_synced=bool(db_ok and target_wallet is not None),
                audit_chain_intact=audit_ok,
                clock_synced=clock_ok,
                issues=issues,
                positions_found=pos_count,
            )

        if target_wallet is None:
            return PreFlightReport(
                ready=False,
                bridge_online=False,
                broker_authenticated=False,
                account_server="",
                broker_balance=0.0,
                custody_synced=False,
                audit_chain_intact=audit_ok,
                clock_synced=clock_ok,
                issues=issues,
                positions_found=0,
            )

        connector = resolve_connector_for_wallet(target_wallet, wallets_repo=self.wallets_repo)
        try:
            hc = connector.health_check()
        except Exception as exc:
            hc = {"connected": False, "bridge_online": False, "message": str(exc)}

        bridge_online = bool(hc.get("bridge_online", False))
        if not bridge_online:
            issues.append(str(hc.get("message") or "MT5 Bridge offline ou inacessível."))

        try:
            acc = connector.get_account_summary() if bridge_online else {"connected": False}
        except Exception as exc:
            acc = {"connected": False, "message": str(exc)}

        broker_authenticated = bool(hc.get("connected", False) and acc.get("connected", False))
        if bridge_online and not broker_authenticated:
            issues.append(
                str(
                    acc.get("message")
                    or hc.get("message")
                    or "Conta Clear não autenticada no terminal MetaTrader 5."
                )
            )

        account_server = str(acc.get("server") or hc.get("server") or settings.MT5_SERVER)
        broker_balance = float(acc.get("balance", 0.0) or 0.0)
        custody_synced = False
        positions_found = 0

        if broker_authenticated:
            try:
                sync_res = self.reconcile_wallet_custody(target_wallet, connector)
                custody_synced = bool(sync_res.get("synced", False))
                positions_found = int(sync_res.get("positions_found", 0) or 0)
                if not custody_synced:
                    issues.append("Falha ao sincronizar custódia e caixa com a Clear.")
            except Exception as exc:
                custody_synced = False
                issues.append(f"Erro na reconciliação de custódia Clear: {exc}")

        ready = bool(
            bridge_online
            and broker_authenticated
            and custody_synced
            and audit_ok
            and clock_ok
            and not issues
        )
        return PreFlightReport(
            ready=ready,
            bridge_online=bridge_online,
            broker_authenticated=broker_authenticated,
            account_server=account_server,
            broker_balance=round(broker_balance, 2),
            custody_synced=custody_synced,
            audit_chain_intact=audit_ok,
            clock_synced=clock_ok,
            issues=issues,
            positions_found=positions_found,
        )

    def reconcile_wallet_custody(
        self,
        wallet_row: dict[str, Any],
        connector: BrokerConnector,
    ) -> dict[str, Any]:
        user_id = int(wallet_row["user_id"])
        wallet_id = int(wallet_row["id"])
        stop_loss_pct = max(
            0.01,
            min(0.50, float(wallet_row.get("stop_loss_pct") or settings.STOP_LOSS_PERCENT)),
        )

        with get_wallet_lock(wallet_id):
            prior_local_list = self.trading_repo.list_positions(user_id, wallet_id)
            prior_local_map: dict[str, Position] = {
                normalize_b3_ticker(p.ticker): p
                for p in prior_local_list
                if normalize_b3_ticker(p.ticker)
            }

            acc_summary = connector.get_account_summary()
            explicitly_connected = isinstance(acc_summary, dict) and (acc_summary.get("connected") is True)

            cash_sync: dict[str, Any] = {}
            if settings.IS_PRODUCTION:
                cash_sync = self.capital_service.sync_with_broker(user_id, wallet_id, connector)

            raw_broker_positions = connector.get_broker_positions()
            broker_map: dict[str, Position] = {}
            for bp in raw_broker_positions:
                tk = normalize_b3_ticker(bp.ticker)
                if not tk or int(bp.quantity) <= 0:
                    continue
                if tk not in broker_map:
                    broker_map[tk] = bp
                else:
                    prev = broker_map[tk]
                    merged_qty = prev.quantity + int(bp.quantity)
                    w_entry = round(
                        ((prev.quantity * prev.entry_price) + (int(bp.quantity) * bp.entry_price))
                        / merged_qty,
                        4,
                    )
                    w_curr = round(
                        ((prev.quantity * prev.current_price) + (int(bp.quantity) * bp.current_price))
                        / merged_qty,
                        4,
                    )
                    broker_map[tk] = Position(
                        ticker=tk,
                        quantity=merged_qty,
                        entry_price=w_entry,
                        current_price=w_curr,
                        stop_loss=bp.stop_loss if bp.stop_loss > 0.0 else prev.stop_loss,
                        opened_at=prev.opened_at or bp.opened_at,
                        highest_price=max(prev.highest_price, bp.highest_price, w_curr),
                        ticket=prev.ticket or bp.ticket,
                        fiscal_avg_price=w_entry,
                    )

            added_count = 0
            updated_count = 0
            now_iso = get_brt_now().isoformat()

            for tk, bp in broker_map.items():
                loc = prior_local_map.get(tk)
                entry_px = round(float(bp.entry_price), 4)
                curr_px = round(float(bp.current_price if bp.current_price > 0.0 else entry_px), 4)
                default_sl = round(entry_px * (1.0 - stop_loss_pct), 2)

                if loc is not None:
                    updated_count += 1
                    preserved_sl = (
                        loc.stop_loss
                        if loc.stop_loss > 0.0
                        else (bp.stop_loss if bp.stop_loss > 0.0 else default_sl)
                    )
                    preserved_fiscal = (
                        loc.fiscal_avg_price
                        if loc.fiscal_avg_price > 0.0
                        else entry_px
                    )
                    merged = Position(
                        ticker=tk,
                        quantity=int(bp.quantity),
                        entry_price=entry_px,
                        current_price=curr_px,
                        stop_loss=round(float(preserved_sl), 4),
                        opened_at=loc.opened_at or bp.opened_at or now_iso,
                        highest_price=round(max(loc.highest_price, bp.highest_price, entry_px, curr_px), 4),
                        partial_tp_taken=bool(loc.partial_tp_taken),
                        do_not_touch=bool(loc.do_not_touch),
                        ticket=int(bp.ticket if bp.ticket > 0 else loc.ticket),
                        fiscal_avg_price=round(float(preserved_fiscal), 4),
                    )
                else:
                    added_count += 1
                    disc_sl = bp.stop_loss if bp.stop_loss > 0.0 else default_sl
                    merged = Position(
                        ticker=tk,
                        quantity=int(bp.quantity),
                        entry_price=entry_px,
                        current_price=curr_px,
                        stop_loss=round(float(disc_sl), 4),
                        opened_at=bp.opened_at or now_iso,
                        highest_price=round(max(bp.highest_price, entry_px, curr_px), 4),
                        partial_tp_taken=False,
                        do_not_touch=False,
                        ticket=int(bp.ticket),
                        fiscal_avg_price=entry_px,
                    )
                self.trading_repo.upsert_position(user_id, wallet_id, merged)

            removed_count = 0
            if explicitly_connected:
                stale_tickers = set(prior_local_map.keys()) - set(broker_map.keys())
                for stale_tk in stale_tickers:
                    self.trading_repo.delete_position(user_id, wallet_id, stale_tk)
                    removed_count += 1
            else:
                for tk, loc_pos in prior_local_map.items():
                    if tk not in broker_map:
                        self.trading_repo.upsert_position(user_id, wallet_id, loc_pos)

            logger.info(
                "Custódia reconciliada na carteira %d: %d ativas (+%d novas, ~%d mescladas, -%d removidas, connected=%s)",
                wallet_id,
                len(broker_map),
                added_count,
                updated_count,
                removed_count,
                explicitly_connected,
            )
            return {
                "synced": True,
                "connected": explicitly_connected,
                "positions_found": len(broker_map),
                "added": added_count,
                "updated": updated_count,
                "removed": removed_count,
                "cash_sync": cash_sync,
            }
