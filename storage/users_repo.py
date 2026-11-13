from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from config.settings import OWNER_USER_ID, OWNER_USERNAME, settings
from domain.b3_rules import BRT, get_brt_now
from security.password import password_security
from security.session_csrf import (
    compute_ua_fingerprint,
    derive_csrf_token,
    generate_session_token,
    hash_session_token,
    verify_session_fingerprint,
)
from storage.connection import DatabaseConnection


def _parse_iso(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(val)
        return dt.replace(tzinfo=BRT) if dt.tzinfo is None else dt.astimezone(BRT)
    except ValueError:
        return None


class UsersRepository:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()

    def get_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self.db.readonly() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return dict(row) if row is not None else None

    def get_by_username(self, username: str) -> dict[str, Any] | None:
        clean = (username or "").strip()
        if not clean:
            return None
        with self.db.readonly() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (clean,)).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def is_luke_owner(user: dict[str, Any] | None) -> bool:
        if not user:
            return False
        return (
            int(user.get("id", 0)) == OWNER_USER_ID
            and int(user.get("is_owner_admin", 0)) == 1
            and str(user.get("username", "")).strip().lower() == OWNER_USERNAME.lower()
        )

    def is_invite_expired(self, user: dict[str, Any]) -> bool:
        if not bool(user.get("must_change_password", 0)):
            return False
        exp = _parse_iso(user.get("invite_expires_at"))
        if exp is None:
            return False
        return get_brt_now() >= exp

    def is_user_locked(self, user: dict[str, Any]) -> tuple[bool, int]:
        if not bool(user.get("is_active", 1)):
            return True, 86400
        locked_dt = _parse_iso(user.get("locked_until"))
        if locked_dt is None:
            return False, 0
        rem = int((locked_dt - get_brt_now()).total_seconds())
        return (True, rem) if rem > 0 else (False, 0)

    def record_login_failure(self, user_id: int) -> tuple[int, int]:
        now = get_brt_now()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT failed_login_attempts FROM users WHERE id = ?",
                (int(user_id),),
            ).fetchone()
            if row is None:
                return 0, 0
            attempts = int(row["failed_login_attempts"] or 0) + 1
            lock_sec = 0
            locked_iso = None
            if attempts >= settings.AUTH_MAX_LOGIN_ATTEMPTS:
                lock_sec = settings.AUTH_LOCKOUT_SECONDS
                locked_iso = (now + timedelta(seconds=lock_sec)).isoformat()
            conn.execute(
                """
                UPDATE users
                SET failed_login_attempts = ?, locked_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (attempts, locked_iso, now.isoformat(), int(user_id)),
            )
            return attempts, lock_sec

    def record_login_success(self, user_id: int) -> None:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE users
                SET failed_login_attempts = 0, locked_until = NULL,
                    last_login_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now_iso, now_iso, int(user_id)),
            )

    def complete_first_login_password(self, user_id: int, new_password: str) -> None:
        pw_hash, salt = password_security.hash_password(new_password)
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, salt = ?, must_change_password = 0,
                    invite_expires_at = NULL, failed_login_attempts = 0,
                    locked_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (pw_hash, salt, now_iso, int(user_id)),
            )

    def create_standard_user(
        self,
        username: str,
        temporary_password: str,
        invite_hours: int | None = None,
    ) -> dict[str, Any]:
        clean = (username or "").strip()
        if len(clean) < 3 or len(clean) > 32:
            raise ValueError("O nome de usuário deve ter entre 3 e 32 caracteres.")
        if clean.lower() in (OWNER_USERNAME.lower(), "admin", "root", "system"):
            raise ValueError("Este nome de usuário é reservado e não pode ser utilizado.")
        if self.get_by_username(clean) is not None:
            raise ValueError("Já existe um usuário com este nome.")

        pw_hash, salt = password_security.hash_password(temporary_password)
        now = get_brt_now()
        exp_hours = int(invite_hours if invite_hours is not None else settings.INVITE_EXPIRY_HOURS)
        invite_exp = (now + timedelta(hours=max(1, exp_hours))).isoformat()

        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, salt, is_owner_admin, is_active,
                    must_change_password, invite_expires_at, created_at, updated_at
                ) VALUES (?, ?, ?, 0, 1, 1, ?, ?, ?)
                """,
                (clean, pw_hash, salt, invite_exp, now.isoformat(), now.isoformat()),
            )
            uid = int(cur.lastrowid or 0)
        created = self.get_by_id(uid)
        if created is None:
            raise RuntimeError("Falha ao carregar usuário recém-criado.")
        return created

    def list_all_users_for_luke(self) -> list[dict[str, Any]]:
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.is_owner_admin, u.is_active,
                       u.must_change_password, u.invite_expires_at, u.totp_enabled,
                       u.locked_until, u.last_login_at, u.created_at,
                       COUNT(w.id) AS wallets_count,
                       COALESCE(GROUP_CONCAT(w.connector_type, ', '), '') AS connectors
                FROM users u
                LEFT JOIN user_wallets w ON w.user_id = u.id
                GROUP BY u.id
                ORDER BY u.id ASC
                """
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["invite_expired"] = self.is_invite_expired(d)
            out.append(d)
        return out

    def set_user_active_status(self, target_user_id: int, is_active: bool) -> None:
        if int(target_user_id) == OWNER_USER_ID:
            raise ValueError("A conta principal Luke não pode ser desativada.")
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE users SET is_active = ?, locked_until = NULL, updated_at = ? WHERE id = ?",
                (int(bool(is_active)), now_iso, int(target_user_id)),
            )
            if not is_active:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(target_user_id),))

    def admin_reset_user_password(
        self,
        target_user_id: int,
        temporary_password: str,
        invite_hours: int | None = None,
    ) -> str:
        if int(target_user_id) == OWNER_USER_ID:
            raise ValueError("Use a aba Minha Segurança para alterar a senha da conta Luke.")
        pw_hash, salt = password_security.hash_password(temporary_password)
        now = get_brt_now()
        exp_hours = int(invite_hours if invite_hours is not None else settings.INVITE_EXPIRY_HOURS)
        invite_exp = (now + timedelta(hours=max(1, exp_hours))).isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE users
                SET password_hash = ?, salt = ?, must_change_password = 1,
                    invite_expires_at = ?, failed_login_attempts = 0,
                    locked_until = NULL, updated_at = ?
                WHERE id = ?
                """,
                (pw_hash, salt, invite_exp, now.isoformat(), int(target_user_id)),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (int(target_user_id),))
        return invite_exp

    def save_user_totp(
        self,
        user_id: int,
        encrypted_secret: str,
        recovery_codes_json: str,
        enabled: bool,
    ) -> None:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE users
                SET totp_enabled = ?, encrypted_totp_secret = ?,
                    recovery_codes_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (int(bool(enabled)), encrypted_secret, recovery_codes_json, now_iso, int(user_id)),
            )

    def update_recovery_codes(self, user_id: int, recovery_codes_json: str) -> None:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE users SET recovery_codes_json = ?, updated_at = ? WHERE id = ?",
                (recovery_codes_json, now_iso, int(user_id)),
            )

    def create_session(
        self,
        user_id: int,
        ip_address: str,
        user_agent: str,
        active_wallet_id: int | None = None,
    ) -> dict[str, str]:
        raw_token, tok_hash = generate_session_token()
        csrf_tok = derive_csrf_token(tok_hash)
        ua_fp = compute_ua_fingerprint(user_agent)
        ua_label = (user_agent or "Browser")[:120]
        now = get_brt_now()
        exp = now + timedelta(seconds=settings.AUTH_MAX_SESSION_SECONDS)

        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE expires_at <= ?",
                (now.isoformat(),),
            )
            existing = conn.execute(
                "SELECT id FROM sessions WHERE user_id = ? ORDER BY last_active_at ASC",
                (int(user_id),),
            ).fetchall()
            max_conc = max(1, int(settings.AUTH_MAX_CONCURRENT_SESSIONS))
            if len(existing) >= max_conc:
                to_prune = existing[: len(existing) - max_conc + 1]
                for old in to_prune:
                    conn.execute("DELETE FROM sessions WHERE id = ?", (int(old["id"]),))

            if active_wallet_id is None:
                def_w = conn.execute(
                    "SELECT id FROM user_wallets WHERE user_id = ? ORDER BY is_default DESC, id ASC LIMIT 1",
                    (int(user_id),),
                ).fetchone()
                active_wallet_id = int(def_w["id"]) if def_w is not None else None

            conn.execute(
                """
                INSERT INTO sessions (
                    token_sha256, csrf_token, user_id, active_wallet_id,
                    ip_address, ua_fingerprint, user_agent_label,
                    created_at, last_active_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tok_hash,
                    csrf_tok,
                    int(user_id),
                    active_wallet_id,
                    ip_address or "",
                    ua_fp,
                    ua_label,
                    now.isoformat(),
                    now.isoformat(),
                    exp.isoformat(),
                ),
            )
        return {
            "raw_token": raw_token,
            "token_sha256": tok_hash,
            "csrf_token": csrf_tok,
        }

    def validate_session(
        self,
        raw_token: str,
        current_user_agent: str,
        touch: bool = False,
    ) -> tuple[dict[str, Any] | None, str]:
        if not raw_token:
            return None, "missing"
        tok_hash = hash_session_token(raw_token)
        now = get_brt_now()

        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE token_sha256 = ?",
                (tok_hash,),
            ).fetchone()
            if row is None:
                return None, "not_found"
            sess = dict(row)

            exp_dt = _parse_iso(sess.get("expires_at"))
            last_dt = _parse_iso(sess.get("last_active_at"))
            if exp_dt is not None and now >= exp_dt:
                conn.execute("DELETE FROM sessions WHERE id = ?", (int(sess["id"]),))
                return None, "expired"
            if last_dt is not None and (now - last_dt).total_seconds() > settings.AUTH_MAX_IDLE_SECONDS:
                conn.execute("DELETE FROM sessions WHERE id = ?", (int(sess["id"]),))
                return None, "idle_timeout"

            if not verify_session_fingerprint(str(sess.get("ua_fingerprint", "")), current_user_agent):
                conn.execute("DELETE FROM sessions WHERE id = ?", (int(sess["id"]),))
                return None, "fingerprint_mismatch"

            if touch:
                conn.execute(
                    "UPDATE sessions SET last_active_at = ? WHERE id = ?",
                    (now.isoformat(), int(sess["id"])),
                )
                sess["last_active_at"] = now.isoformat()

        return sess, "ok"

    def set_session_active_wallet(self, token_sha256: str, user_id: int, wallet_id: int) -> bool:
        with self.db.transaction() as conn:
            owner_check = conn.execute(
                "SELECT id FROM user_wallets WHERE id = ? AND user_id = ?",
                (int(wallet_id), int(user_id)),
            ).fetchone()
            if owner_check is None:
                return False
            conn.execute(
                "UPDATE sessions SET active_wallet_id = ? WHERE token_sha256 = ? AND user_id = ?",
                (int(wallet_id), token_sha256, int(user_id)),
            )
            return True

    def list_user_sessions(self, user_id: int, current_token_hash: str = "") -> list[dict[str, Any]]:
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT id, token_sha256, ip_address, user_agent_label,
                       created_at, last_active_at, expires_at
                FROM sessions
                WHERE user_id = ?
                ORDER BY last_active_at DESC
                """,
                (int(user_id),),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["is_current"] = (d.pop("token_sha256", "") == current_token_hash)
            out.append(d)
        return out

    def revoke_session_by_raw_token(self, raw_token: str) -> None:
        if not raw_token:
            return
        tok_hash = hash_session_token(raw_token)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM sessions WHERE token_sha256 = ?", (tok_hash,))

    def revoke_all_user_sessions(self, user_id: int, keep_token_hash: str | None = None) -> int:
        with self.db.transaction() as conn:
            if keep_token_hash:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND token_sha256 != ?",
                    (int(user_id), keep_token_hash),
                )
            else:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE user_id = ?",
                    (int(user_id),),
                )
            return int(cur.rowcount or 0)
