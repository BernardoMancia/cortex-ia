from __future__ import annotations

from typing import Any

from config.settings import settings
from domain.b3_rules import get_brt_now
from domain.models import GlobalOperationalMode
from domain.operational_guard import parse_iso_brt
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection


class StateRepository:
    def __init__(
        self,
        db: DatabaseConnection | None = None,
        audit_repo: AuditRepository | None = None,
    ) -> None:
        self.db = db or DatabaseConnection()
        self.audit = audit_repo or AuditRepository(self.db)

    def boot_initialize(self) -> dict[str, Any]:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM global_operational_state WHERE id = 1").fetchone()
            boot_count = (int(row["boot_count"]) + 1) if row is not None else 1
            if settings.FORCE_MAINTENANCE_ON_BOOT:
                reason = (
                    "Reinício do serviço na Produção detectado. "
                    "Sistema bloqueado em Modo Manutenção Pós-Boot até liberação manual pelo Luke."
                )
                conn.execute(
                    """
                    UPDATE global_operational_state
                    SET global_mode = ?, maintenance_reason = ?, boot_count = ?,
                        last_boot_at = ?, updated_at = ?, updated_by = 'system_boot'
                    WHERE id = 1
                    """,
                    (
                        GlobalOperationalMode.MAINTENANCE.value,
                        reason,
                        boot_count,
                        now_iso,
                        now_iso,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE global_operational_state
                    SET boot_count = ?, last_boot_at = ?, updated_at = ?
                    WHERE id = 1
                    """,
                    (boot_count, now_iso, now_iso),
                )

        if settings.FORCE_MAINTENANCE_ON_BOOT:
            self.audit.record_event(
                event_type="BOOT_MAINTENANCE_LOCK",
                actor_user_id=None,
                ip_address="system",
                details={
                    "environment": settings.ENVIRONMENT_ID,
                    "boot_count": boot_count,
                },
            )
        return self.get_state(auto_expire_panic=True)

    def get_state(self, auto_expire_panic: bool = True) -> dict[str, Any]:
        with self.db.readonly() as conn:
            row = conn.execute("SELECT * FROM global_operational_state WHERE id = 1").fetchone()
        if row is None:
            return {
                "global_mode": GlobalOperationalMode.MAINTENANCE.value
                if settings.FORCE_MAINTENANCE_ON_BOOT
                else GlobalOperationalMode.ACTIVE.value,
                "maintenance_reason": "",
                "panic_started_at": None,
                "panic_until": None,
                "panic_reason": None,
                "boot_count": 1,
                "last_boot_at": get_brt_now().isoformat(),
                "updated_at": get_brt_now().isoformat(),
                "updated_by": "system",
            }
        state = dict(row)
        if auto_expire_panic and str(state.get("global_mode")) == GlobalOperationalMode.PANIC.value:
            until_dt = parse_iso_brt(state.get("panic_until"))
            now = get_brt_now()
            if until_dt is not None and now >= until_dt:
                now_iso = now.isoformat()
                with self.db.transaction() as conn:
                    conn.execute(
                        """
                        UPDATE global_operational_state
                        SET global_mode = 'ACTIVE', panic_started_at = NULL,
                            panic_until = NULL, panic_reason = NULL,
                            updated_at = ?, updated_by = 'timer'
                        WHERE id = 1 AND global_mode = 'PANIC'
                        """,
                        (now_iso,),
                    )
                self.audit.record_event(
                    event_type="GLOBAL_PANIC_AUTO_EXPIRED",
                    ip_address="system",
                    details={"expired_at": now_iso},
                )
                return self.get_state(auto_expire_panic=False)
        return state

    def unlock_maintenance(
        self,
        actor_username: str,
        actor_user_id: int,
        ip_address: str = "",
    ) -> dict[str, Any]:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE global_operational_state
                SET global_mode = 'ACTIVE', maintenance_reason = '',
                    panic_started_at = NULL, panic_until = NULL, panic_reason = NULL,
                    updated_at = ?, updated_by = ?
                WHERE id = 1
                """,
                (now_iso, actor_username),
            )
        self.audit.record_event(
            event_type="MAINTENANCE_UNLOCKED",
            actor_user_id=actor_user_id,
            ip_address=ip_address,
            details={"actor": actor_username},
        )
        return self.get_state(auto_expire_panic=False)

    def enter_maintenance(
        self,
        reason: str,
        actor_username: str,
        actor_user_id: int | None = None,
        ip_address: str = "",
    ) -> dict[str, Any]:
        now_iso = get_brt_now().isoformat()
        clean_reason = (reason or "Modo Manutenção ativado manualmente.").strip()[:240]
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE global_operational_state
                SET global_mode = 'MAINTENANCE', maintenance_reason = ?,
                    updated_at = ?, updated_by = ?
                WHERE id = 1
                """,
                (clean_reason, now_iso, actor_username),
            )
        self.audit.record_event(
            event_type="MAINTENANCE_ENTERED",
            actor_user_id=actor_user_id,
            ip_address=ip_address,
            details={"actor": actor_username, "reason": clean_reason},
        )
        return self.get_state(auto_expire_panic=False)

    def set_global_panic(
        self,
        panic_until: str | None,
        reason: str,
        actor_username: str,
        actor_user_id: int | None = None,
        ip_address: str = "",
    ) -> dict[str, Any]:
        now_iso = get_brt_now().isoformat()
        clean_reason = (reason or "Botão de Pânico Global acionado.").strip()[:240]
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE global_operational_state
                SET global_mode = 'PANIC', panic_started_at = ?,
                    panic_until = ?, panic_reason = ?,
                    updated_at = ?, updated_by = ?
                WHERE id = 1
                """,
                (now_iso, panic_until, clean_reason, now_iso, actor_username),
            )
        self.audit.record_event(
            event_type="GLOBAL_PANIC_ACTIVATED",
            actor_user_id=actor_user_id,
            ip_address=ip_address,
            details={
                "actor": actor_username,
                "panic_until": panic_until or "indefinite",
                "reason": clean_reason,
            },
        )
        return self.get_state(auto_expire_panic=False)

    def clear_global_panic(
        self,
        actor_username: str,
        actor_user_id: int | None = None,
        ip_address: str = "",
    ) -> dict[str, Any]:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE global_operational_state
                SET global_mode = 'ACTIVE', panic_started_at = NULL,
                    panic_until = NULL, panic_reason = NULL,
                    updated_at = ?, updated_by = ?
                WHERE id = 1
                """,
                (now_iso, actor_username),
            )
        self.audit.record_event(
            event_type="GLOBAL_PANIC_CLEARED",
            actor_user_id=actor_user_id,
            ip_address=ip_address,
            details={"actor": actor_username},
        )
        return self.get_state(auto_expire_panic=False)
