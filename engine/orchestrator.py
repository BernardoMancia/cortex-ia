from __future__ import annotations

import threading
import time
from typing import Any

from config.settings import settings
from domain.b3_rules import get_brt_now
from domain.b3_tax_guard import B3TaxGuard
from domain.financial_firewall import FinancialFirewall
from domain.models import GlobalOperationalMode
from engine.custody_sync import CustodyReconciler
from engine.panic_service import PanicService
from engine.scheduler import CycleScheduler
from engine.telegram_commands import TelegramCommandRouter
from engine.wallet_cycle import WalletCycleRunner
from market.hub import MarketIntelligenceHub
from notifications.telegram_client import TelegramClient
from observability.auto_backup import EncryptedBackupService
from observability.health import SystemHealthMonitor
from observability.logger import get_logger
from security.fs_guard import enforce_secure_permissions
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection
from storage.market_hub_repo import MarketHubRepository
from storage.state_repo import StateRepository
from storage.trading_repo import TradingRepository
from storage.wallets_repo import WalletsRepository

logger = get_logger("engine.orchestrator")


class CortexOrchestrator:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()
        self.state_repo = StateRepository(self.db)
        self.wallets_repo = WalletsRepository(self.db)
        self.trading_repo = TradingRepository(self.db)
        self.market_repo = MarketHubRepository(self.db)
        self.audit_repo = AuditRepository(self.db)

        self.firewall = FinancialFirewall(
            max_single_order_notional=settings.MAX_SINGLE_ORDER_NOTIONAL,
            max_orders_per_minute=settings.MAX_ORDERS_PER_MINUTE,
        )
        self.tax_guard = B3TaxGuard(
            exemption_limit=settings.B3_SWING_EXEMPTION_LIMIT,
            guard_buffer=settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
        )
        self.telegram = TelegramClient()
        self.panic_service = PanicService(db=self.db, telegram=self.telegram)
        self.custody_reconciler = CustodyReconciler(db=self.db)
        self.market_hub = MarketIntelligenceHub(db=self.db)
        self.wallet_runner = WalletCycleRunner(
            db=self.db,
            firewall=self.firewall,
            tax_guard=self.tax_guard,
            panic_service=self.panic_service,
            telegram=self.telegram,
        )
        self.telegram_router = TelegramCommandRouter(
            db=self.db,
            panic_service=self.panic_service,
        )
        self.scheduler = CycleScheduler()
        self.backup_service = EncryptedBackupService(
            db_path=settings.DB_PATH,
            backups_dir=settings.BACKUPS_DIR,
        )

        self._stop_event = threading.Event()
        self._state_lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._force_next_refresh: bool = False

        self._last_health_mono: float = 0.0
        self._last_telegram_poll_mono: float = 0.0
        self._last_backup_date: str = ""
        self._last_prune_date: str = ""
        self._last_backup_at: str = ""

        self._cycles_completed: int = 0
        self._last_cycle_id: str = ""
        self._last_cycle_at: str = ""
        self._last_cycle_duration_ms: float = 0.0
        self._next_cycle_at: str = ""
        self._wallets_processed: int = 0
        self._last_error: str = ""

    @property
    def is_running(self) -> bool:
        t = self._thread
        return bool(t is not None and t.is_alive() and not self._stop_event.is_set())

    def start(self) -> None:
        with self._state_lock:
            if self.is_running:
                return
            self._stop_event.clear()

        enforce_secure_permissions(
            data_dir=settings.DATA_DIR,
            keys_dir=settings.KEYS_DIR,
            backups_dir=settings.BACKUPS_DIR,
            project_root=settings.PROJECT_ROOT,
        )
        boot_state = self.state_repo.boot_initialize()
        logger.info(
            "Córtex IA v%s iniciado (%s) — modo global pós-boot: %s",
            settings.VERSION,
            settings.ENVIRONMENT_LABEL,
            boot_state.get("global_mode"),
        )

        if self.telegram.enabled:
            mode_str = str(boot_state.get("global_mode") or "")
            boot_msg = (
                f"CÓRTEX IA v{settings.VERSION} INICIADO ({settings.ENVIRONMENT_LABEL})\n"
                f"Modo Global: {mode_str}"
            )
            if mode_str == GlobalOperationalMode.MAINTENANCE.value:
                boot_msg += (
                    "\nBloqueio Pós-Boot ativo na Produção. "
                    "Acesse o Dashboard Web com 2FA Step-Up para executar o Pre-Flight e liberar ordens."
                )
            self.telegram.send_admin_alert(boot_msg)

        with self._state_lock:
            self._thread = threading.Thread(
                target=self._loop,
                name="cortex-orchestrator",
                daemon=True,
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self.scheduler.interrupt()
        worker = self._thread
        if worker is not None and worker.is_alive() and worker is not threading.current_thread():
            worker.join(timeout=max(5.0, settings.WALLET_EXECUTION_TIMEOUT_SECONDS + 4.0))

        snap_file = self.backup_service.create_verified_encrypted_snapshot(tag="shutdown")
        if snap_file is not None:
            with self._state_lock:
                self._last_backup_at = get_brt_now().isoformat()

        self.telegram.close()
        logger.info("Córtex IA orquestrador encerrado com segurança.")

    def trigger_immediate_cycle(self) -> None:
        with self._state_lock:
            self._force_next_refresh = True
        self.scheduler.trigger_immediate()

    def _collect_held_extra_tickers(self, wallets: list[dict[str, Any]]) -> set[str]:
        held: set[str] = set()
        for w in wallets:
            positions = self.trading_repo.list_positions(int(w["user_id"]), int(w["id"]))
            for p in positions:
                if p.ticker:
                    held.add(p.ticker)
        return held

    def _run_periodic_housekeeping(self) -> None:
        now_m = time.monotonic()
        if (now_m - self._last_health_mono) >= float(settings.HEALTH_CHECK_INTERVAL):
            self._last_health_mono = now_m
            metrics = SystemHealthMonitor.collect_metrics()
            try:
                self.market_repo.record_health(
                    cpu_percent=float(metrics["cpu_percent"]),
                    ram_percent=float(metrics["ram_percent"]),
                    disk_percent=float(metrics["disk_percent"]),
                )
            except Exception as exc:
                logger.warning("Falha ao gravar métricas de saúde: %s", exc)

        if self.telegram.enabled and (now_m - self._last_telegram_poll_mono) >= 4.0:
            self._last_telegram_poll_mono = now_m
            try:
                self.telegram.poll_authorized_commands(self.telegram_router.handle_command)
            except Exception as exc:
                logger.warning("Falha ao consultar comandos Telegram: %s", exc)

        today_iso = get_brt_now().date().isoformat()
        if self._last_backup_date != today_iso:
            self._last_backup_date = today_iso
            out = self.backup_service.create_verified_encrypted_snapshot(tag="daily")
            if out is not None:
                with self._state_lock:
                    self._last_backup_at = get_brt_now().isoformat()

        if self._last_prune_date != today_iso:
            self._last_prune_date = today_iso
            try:
                self.market_repo.prune_old_records(retention_days=settings.DECISION_RETENTION_DAYS)
            except Exception as exc:
                logger.warning("Falha ao expurgar registros antigos: %s", exc)

    def run_single_cycle(self, force_refresh: bool = False) -> dict[str, Any]:
        t0 = time.monotonic()
        wallets = self.wallets_repo.list_all_executable_wallets_ordered()
        extra_tickers = self._collect_held_extra_tickers(wallets)

        snapshot = self.market_hub.refresh_cycle(
            extra_tickers=extra_tickers,
            force=force_refresh,
        )

        wallet_results: list[dict[str, Any]] = []
        for w in wallets:
            if self._stop_event.is_set():
                break
            global_state = self.state_repo.get_state(auto_expire_panic=True)
            res = self.wallet_runner.run_wallet_cycle(
                wallet_row=w,
                snapshot=snapshot,
                global_state=global_state,
            )
            wallet_results.append(res)

        elapsed_ms = round((time.monotonic() - t0) * 1000.0, 2)
        now_brt = get_brt_now()
        nxt_dt = self.scheduler.next_run_at(market_open=snapshot.market_open, now=now_brt)

        with self._state_lock:
            self._cycles_completed += 1
            self._last_cycle_id = snapshot.cycle_id
            self._last_cycle_at = now_brt.isoformat()
            self._last_cycle_duration_ms = elapsed_ms
            self._next_cycle_at = nxt_dt.isoformat()
            self._wallets_processed = len(wallet_results)
            self._last_error = ""

        return {
            "cycle_id": snapshot.cycle_id,
            "market_open": snapshot.market_open,
            "wallets_processed": len(wallet_results),
            "duration_ms": elapsed_ms,
            "results": wallet_results,
        }

    def _wait_interruptible_with_telegram_polling(self, market_open: bool) -> bool:
        total_wait = self.scheduler.compute_sleep_seconds(market_open=market_open)
        deadline = time.monotonic() + total_wait
        while not self._stop_event.is_set():
            if self.scheduler._wake_event.is_set():
                self.scheduler._wake_event.clear()
                return not self._stop_event.is_set()
            rem = deadline - time.monotonic()
            if rem <= 0.0:
                return not self._stop_event.is_set()
            step = min(4.0, rem)
            woken = self.scheduler._wake_event.wait(timeout=step)
            if woken:
                self.scheduler._wake_event.clear()
                return not self._stop_event.is_set()
            if not self._stop_event.is_set():
                self._run_periodic_housekeeping()
        return False

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            market_open_hint = False
            try:
                self._run_periodic_housekeeping()
                with self._state_lock:
                    force_flag = self._force_next_refresh
                    self._force_next_refresh = False
                cycle_out = self.run_single_cycle(force_refresh=force_flag)
                market_open_hint = bool(cycle_out.get("market_open", False))
            except Exception as exc:
                logger.error("Erro no ciclo principal do CortexOrchestrator: %s", exc)
                with self._state_lock:
                    self._last_error = str(exc)
                try:
                    market_open_hint, _ = self.market_hub.is_b3_session_open()
                except Exception:
                    market_open_hint = False

            if self._stop_event.is_set():
                break
            if not self._wait_interruptible_with_telegram_polling(market_open=market_open_hint):
                break

    def get_metrics(self) -> dict[str, Any]:
        global_state = self.state_repo.get_state(auto_expire_panic=True)
        latest_snap = self.market_hub.get_latest_snapshot()
        is_open, cooldown = self.market_hub.is_b3_session_open()
        health = SystemHealthMonitor.collect_metrics()

        with self._state_lock:
            return {
                "running": self.is_running,
                "environment": settings.ENVIRONMENT_ID,
                "environment_label": settings.ENVIRONMENT_LABEL,
                "version": settings.VERSION,
                "global_mode": str(global_state.get("global_mode") or GlobalOperationalMode.ACTIVE.value),
                "global_state": global_state,
                "cycles_completed": self._cycles_completed,
                "last_cycle_id": self._last_cycle_id,
                "last_cycle_at": self._last_cycle_at,
                "last_cycle_duration_ms": self._last_cycle_duration_ms,
                "next_cycle_at": self._next_cycle_at,
                "wallets_processed": self._wallets_processed,
                "last_backup_at": self._last_backup_at,
                "last_error": self._last_error,
                "market_open": latest_snap.market_open if latest_snap is not None else is_open,
                "opening_cooldown_active": (
                    latest_snap.opening_cooldown_active if latest_snap is not None else cooldown
                ),
                "bova11_change_pct": latest_snap.bova11_change_pct if latest_snap is not None else 0.0,
                "regime_bearish_veto": latest_snap.regime_bearish_veto if latest_snap is not None else False,
                "quotes_count": len(latest_snap.quotes) if latest_snap is not None else 0,
                "health": health,
            }
