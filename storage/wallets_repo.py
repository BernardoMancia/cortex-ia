from __future__ import annotations

from typing import Any

from config.settings import OWNER_USER_ID, settings
from domain.b3_rules import get_brt_now
from domain.models import WalletOperationalMode
from security.wallet_crypto import wallet_crypto
from storage.connection import DatabaseConnection


class WalletsRepository:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()

    def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT * FROM user_wallets
                WHERE user_id = ?
                ORDER BY is_default DESC, id ASC
                """,
                (int(user_id),),
            ).fetchall()
        return [self._sanitize_row(dict(r)) for r in rows]

    def get_owned_wallet(self, user_id: int, wallet_id: int) -> dict[str, Any] | None:
        with self.db.readonly() as conn:
            row = conn.execute(
                "SELECT * FROM user_wallets WHERE id = ? AND user_id = ?",
                (int(wallet_id), int(user_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_default_or_first_wallet(self, user_id: int) -> dict[str, Any] | None:
        with self.db.readonly() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_wallets
                WHERE user_id = ?
                ORDER BY is_default DESC, id ASC
                LIMIT 1
                """,
                (int(user_id),),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_all_executable_wallets_ordered(self) -> list[dict[str, Any]]:
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT w.*, u.username, u.is_active AS user_is_active
                FROM user_wallets w
                JOIN users u ON u.id = w.user_id
                WHERE u.is_active = 1
                ORDER BY CASE WHEN w.user_id = ? THEN 0 ELSE 1 END ASC, w.id ASC
                """,
                (OWNER_USER_ID,),
            ).fetchall()
        return [dict(r) for r in rows]

    def create_wallet(
        self,
        user_id: int,
        name: str,
        connector_type: str,
        connector_config: dict[str, Any] | None = None,
        initial_capital: float = 0.0,
        is_ready_connector: bool = False,
        make_default: bool = False,
    ) -> dict[str, Any]:
        clean_name = (name or "").strip()[:64]
        if len(clean_name) < 2:
            raise ValueError("Informe um nome válido para a carteira.")
        now_iso = get_brt_now().isoformat()
        cap = round(max(0.0, float(initial_capital)), 2)

        if settings.IS_SIMULATION:
            init_mode = WalletOperationalMode.ACTIVE.value
            auto_en = 1
            free_c = cap if cap > 0 else settings.DEFAULT_SIM_CAPITAL
            ai_c = free_c
            cap = free_c
        else:
            init_mode = (
                WalletOperationalMode.ACTIVE.value
                if is_ready_connector
                else WalletOperationalMode.PAUSED_UNCONFIGURED.value
            )
            auto_en = 1 if is_ready_connector else 0
            free_c = cap
            ai_c = 0.0

        with self.db.transaction() as conn:
            existing_count = conn.execute(
                "SELECT COUNT(*) AS cnt FROM user_wallets WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()["cnt"]
            is_def = 1 if (make_default or int(existing_count) == 0) else 0
            if is_def:
                conn.execute(
                    "UPDATE user_wallets SET is_default = 0 WHERE user_id = ?",
                    (int(user_id),),
                )
            cur = conn.execute(
                """
                INSERT INTO user_wallets (
                    user_id, name, connector_type, is_default, automation_enabled,
                    operational_mode, initial_capital, free_cash, ai_allocated_capital,
                    vault_reserve_cash, stop_loss_pct, trailing_trigger_pct,
                    trailing_distance_pct, protect_20k_exemption, avoid_day_trade_tax,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    clean_name,
                    connector_type.strip().lower(),
                    is_def,
                    auto_en,
                    init_mode,
                    cap,
                    free_c,
                    ai_c,
                    settings.STOP_LOSS_PERCENT,
                    settings.TRAILING_STOP_TRIGGER_PERCENT,
                    settings.TRAILING_STOP_DISTANCE_PERCENT,
                    int(settings.PROTECT_B3_20K_EXEMPTION),
                    int(settings.AVOID_DAY_TRADE_TAX),
                    now_iso,
                    now_iso,
                ),
            )
            wid = int(cur.lastrowid or 0)
            enc = wallet_crypto.encrypt_wallet_config(int(user_id), wid, connector_config or {})
            conn.execute(
                "UPDATE user_wallets SET encrypted_connector_config = ? WHERE id = ?",
                (enc, wid),
            )
        created = self.get_owned_wallet(user_id, wid)
        if created is None:
            raise RuntimeError("Falha ao criar carteira.")
        return self._sanitize_row(created)

    def decrypt_connector_config(self, user_id: int, wallet_id: int) -> dict[str, Any]:
        w = self.get_owned_wallet(user_id, wallet_id)
        if w is None:
            raise PermissionError("Carteira não pertence ao usuário autenticado (IDOR bloqueado).")
        blob = str(w.get("encrypted_connector_config") or "")
        return wallet_crypto.decrypt_wallet_config(int(user_id), int(wallet_id), blob)

    def update_connector_config(
        self,
        user_id: int,
        wallet_id: int,
        connector_config: dict[str, Any],
    ) -> None:
        w = self.get_owned_wallet(user_id, wallet_id)
        if w is None:
            raise PermissionError("Carteira não pertence ao usuário autenticado (IDOR bloqueado).")
        enc = wallet_crypto.encrypt_wallet_config(int(user_id), int(wallet_id), connector_config)
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE user_wallets
                SET encrypted_connector_config = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (enc, now_iso, int(wallet_id), int(user_id)),
            )

    def update_risk_and_preferences(
        self,
        user_id: int,
        wallet_id: int,
        stop_loss_pct: float | None = None,
        trailing_trigger_pct: float | None = None,
        trailing_distance_pct: float | None = None,
        protect_20k_exemption: bool | None = None,
        avoid_day_trade_tax: bool | None = None,
        automation_enabled: bool | None = None,
        operational_mode: str | None = None,
        telegram_chat_id: str | None = None,
    ) -> dict[str, Any]:
        w = self.get_owned_wallet(user_id, wallet_id)
        if w is None:
            raise PermissionError("Carteira não encontrada para este usuário.")

        sl = float(w["stop_loss_pct"]) if stop_loss_pct is None else max(0.01, min(0.50, float(stop_loss_pct)))
        tt = (
            float(w["trailing_trigger_pct"])
            if trailing_trigger_pct is None
            else max(0.005, min(0.30, float(trailing_trigger_pct)))
        )
        td = (
            float(w["trailing_distance_pct"])
            if trailing_distance_pct is None
            else max(0.005, min(0.20, float(trailing_distance_pct)))
        )
        if td >= tt:
            td = round(max(0.003, tt * 0.75), 4)

        p20 = int(w["protect_20k_exemption"]) if protect_20k_exemption is None else int(bool(protect_20k_exemption))
        adt = int(w["avoid_day_trade_tax"]) if avoid_day_trade_tax is None else int(bool(avoid_day_trade_tax))
        auto = int(w["automation_enabled"]) if automation_enabled is None else int(bool(automation_enabled))
        mode = str(w["operational_mode"]) if operational_mode is None else operational_mode.strip().upper()
        tg = str(w["telegram_chat_id"]) if telegram_chat_id is None else telegram_chat_id.strip()[:48]
        now_iso = get_brt_now().isoformat()

        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE user_wallets
                SET stop_loss_pct = ?, trailing_trigger_pct = ?, trailing_distance_pct = ?,
                    protect_20k_exemption = ?, avoid_day_trade_tax = ?, automation_enabled = ?,
                    operational_mode = ?, telegram_chat_id = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (sl, tt, td, p20, adt, auto, mode, tg, now_iso, int(wallet_id), int(user_id)),
            )
        updated = self.get_owned_wallet(user_id, wallet_id)
        return self._sanitize_row(updated or w)

    def set_wallet_panic(
        self,
        user_id: int,
        wallet_id: int,
        panic_until: str | None,
        reason: str,
    ) -> dict[str, Any]:
        w = self.get_owned_wallet(user_id, wallet_id)
        if w is None:
            raise PermissionError("Carteira não pertence ao usuário autenticado.")
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE user_wallets
                SET operational_mode = 'PANIC', panic_started_at = ?,
                    panic_until = ?, panic_reason = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (now_iso, panic_until, reason.strip()[:240], now_iso, int(wallet_id), int(user_id)),
            )
        return self._sanitize_row(self.get_owned_wallet(user_id, wallet_id) or w)

    def clear_wallet_panic(
        self,
        user_id: int,
        wallet_id: int,
        restore_mode: str = "ACTIVE",
    ) -> dict[str, Any]:
        w = self.get_owned_wallet(user_id, wallet_id)
        if w is None:
            raise PermissionError("Carteira não pertence ao usuário autenticado.")
        now_iso = get_brt_now().isoformat()
        safe_mode = restore_mode if restore_mode in ("ACTIVE", "OBSERVE_ONLY") else "ACTIVE"
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE user_wallets
                SET operational_mode = ?, panic_started_at = NULL,
                    panic_until = NULL, panic_reason = NULL,
                    consecutive_errors = 0, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (safe_mode, now_iso, int(wallet_id), int(user_id)),
            )
        return self._sanitize_row(self.get_owned_wallet(user_id, wallet_id) or w)

    def update_wallet_balances(
        self,
        user_id: int,
        wallet_id: int,
        free_cash: float,
        ai_allocated_capital: float,
        vault_reserve_cash: float,
        initial_capital: float | None = None,
    ) -> None:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            if initial_capital is not None:
                conn.execute(
                    """
                    UPDATE user_wallets
                    SET free_cash = ?, ai_allocated_capital = ?,
                        vault_reserve_cash = ?, initial_capital = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        round(float(free_cash), 2),
                        round(float(ai_allocated_capital), 2),
                        round(float(vault_reserve_cash), 2),
                        round(float(initial_capital), 2),
                        now_iso,
                        int(wallet_id),
                        int(user_id),
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE user_wallets
                    SET free_cash = ?, ai_allocated_capital = ?,
                        vault_reserve_cash = ?, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (
                        round(float(free_cash), 2),
                        round(float(ai_allocated_capital), 2),
                        round(float(vault_reserve_cash), 2),
                        now_iso,
                        int(wallet_id),
                        int(user_id),
                    ),
                )

    def record_wallet_error_strike(self, user_id: int, wallet_id: int, max_strikes: int = 3) -> int:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT consecutive_errors, operational_mode FROM user_wallets WHERE id = ? AND user_id = ?",
                (int(wallet_id), int(user_id)),
            ).fetchone()
            if row is None:
                return 0
            strikes = int(row["consecutive_errors"] or 0) + 1
            next_mode = str(row["operational_mode"])
            if strikes >= max_strikes and next_mode == "ACTIVE":
                next_mode = WalletOperationalMode.PAUSED_BROKER_ERROR.value
            conn.execute(
                """
                UPDATE user_wallets
                SET consecutive_errors = ?, operational_mode = ?, updated_at = ?
                WHERE id = ? AND user_id = ?
                """,
                (strikes, next_mode, now_iso, int(wallet_id), int(user_id)),
            )
            return strikes

    def reset_wallet_error_strikes(self, user_id: int, wallet_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE user_wallets SET consecutive_errors = 0 WHERE id = ? AND user_id = ?",
                (int(wallet_id), int(user_id)),
            )

    @staticmethod
    def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
        out = dict(row)
        out["has_encrypted_config"] = bool(out.pop("encrypted_connector_config", ""))
        return out
