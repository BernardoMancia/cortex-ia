from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from config.settings import OWNER_USER_ID
from domain.b3_rules import BRT, get_brt_now
from security.bi_tokens import (
    generate_bi_token,
    hash_bi_token,
    is_bi_bearer_token,
    is_ip_allowed_by_cidr,
    validate_cidr_list,
)
from storage.connection import DatabaseConnection
from storage.users_repo import UsersRepository
from storage.wallets_repo import WalletsRepository


def _parse_iso_brt(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val))
        return dt.replace(tzinfo=BRT) if dt.tzinfo is None else dt.astimezone(BRT)
    except ValueError:
        return None


class BiTokensRepository:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()

    @staticmethod
    def _sanitize_key_row(row: dict[str, Any]) -> dict[str, Any]:
        now = get_brt_now()
        exp_dt = _parse_iso_brt(row.get("expires_at"))
        is_expired = exp_dt is not None and now >= exp_dt
        return {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "wallet_id": int(row["wallet_id"]) if row.get("wallet_id") is not None else None,
            "wallet_name": str(row.get("wallet_name") or ""),
            "label": str(row["label"]),
            "token_prefix": str(row["token_prefix"]),
            "allowed_cidr": str(row.get("allowed_cidr") or ""),
            "include_admin": bool(row.get("include_admin", 0)),
            "is_active": bool(row.get("is_active", 0)) and not is_expired,
            "is_expired": is_expired,
            "expires_at": row.get("expires_at"),
            "last_used_at": row.get("last_used_at"),
            "last_used_ip": str(row.get("last_used_ip") or ""),
            "created_at": str(row["created_at"]),
            "revoked_at": row.get("revoked_at"),
        }

    def create_key(
        self,
        user_id: int,
        label: str,
        wallet_id: int | None = None,
        expires_in_days: int | None = 90,
        allowed_cidr: str = "",
        include_admin: bool = False,
    ) -> dict[str, Any]:
        uid = int(user_id)
        clean_label = (label or "").strip()[:80]
        if len(clean_label) < 2:
            raise ValueError("Informe um nome ou rótulo válido para o token BI (mínimo 2 caracteres).")

        users_repo = UsersRepository(self.db)
        user = users_repo.get_by_id(uid)
        if user is None or not bool(user.get("is_active", 1)):
            raise PermissionError("Conta de usuário inativa ou inexistente.")

        scoped_wid: int | None = None
        wallet_name = ""
        if wallet_id is not None:
            owned = WalletsRepository(self.db).get_owned_wallet(uid, int(wallet_id))
            if owned is None:
                raise PermissionError("A carteira informada não pertence ao usuário autenticado (Anti-IDOR).")
            scoped_wid = int(owned["id"])
            wallet_name = str(owned.get("name") or "")

        if include_admin:
            if uid != OWNER_USER_ID or not UsersRepository.is_luke_owner(user):
                raise PermissionError("Escopo administrativo BI exclusivo da conta proprietária Luke.")

        clean_cidr = validate_cidr_list(allowed_cidr)
        now = get_brt_now()
        if expires_in_days is None or int(expires_in_days) <= 0:
            expires_at: str | None = None
        else:
            safe_days = min(3650, int(expires_in_days))
            expires_at = (now + timedelta(days=safe_days)).isoformat()

        raw_token, token_prefix, token_sha256 = generate_bi_token()
        now_iso = now.isoformat()

        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO bi_api_keys (
                    user_id, wallet_id, label, token_prefix, token_sha256,
                    allowed_cidr, include_admin, is_active, expires_at,
                    last_used_at, last_used_ip, created_at, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, '', ?, NULL)
                """,
                (
                    uid,
                    scoped_wid,
                    clean_label,
                    token_prefix,
                    token_sha256,
                    clean_cidr,
                    int(bool(include_admin)),
                    expires_at,
                    now_iso,
                ),
            )
            key_id = int(cur.lastrowid or 0)
            row = conn.execute(
                """
                SELECT id, user_id, wallet_id, label, token_prefix, allowed_cidr,
                       include_admin, is_active, expires_at, last_used_at,
                       last_used_ip, created_at, revoked_at
                FROM bi_api_keys
                WHERE id = ?
                """,
                (key_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Falha ao persistir chave BI somente-leitura.")

        item = self._sanitize_key_row({**dict(row), "wallet_name": wallet_name})
        item["raw_token"] = raw_token
        return item

    def list_keys_for_user(self, user_id: int) -> list[dict[str, Any]]:
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT k.id, k.user_id, k.wallet_id, COALESCE(w.name, '') AS wallet_name,
                       k.label, k.token_prefix, k.allowed_cidr, k.include_admin,
                       k.is_active, k.expires_at, k.last_used_at, k.last_used_ip,
                       k.created_at, k.revoked_at
                FROM bi_api_keys k
                LEFT JOIN user_wallets w ON w.id = k.wallet_id AND w.user_id = k.user_id
                WHERE k.user_id = ?
                ORDER BY k.id DESC
                """,
                (int(user_id),),
            ).fetchall()
        return [self._sanitize_key_row(dict(r)) for r in rows]

    def revoke_key(self, user_id: int, key_id: int) -> bool:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE bi_api_keys
                SET is_active = 0, revoked_at = COALESCE(revoked_at, ?)
                WHERE id = ? AND user_id = ? AND is_active = 1
                """,
                (now_iso, int(key_id), int(user_id)),
            )
            return int(cur.rowcount or 0) > 0

    def lookup_owner_for_audit(self, raw_token: str) -> tuple[int | None, int | None]:
        clean = (raw_token or "").strip()
        if not is_bi_bearer_token(clean):
            return None, None
        tok_hash = hash_bi_token(clean)
        with self.db.readonly() as conn:
            row = conn.execute(
                "SELECT user_id, wallet_id FROM bi_api_keys WHERE token_sha256 = ?",
                (tok_hash,),
            ).fetchone()
        if row is None:
            return None, None
        uid = int(row["user_id"])
        wid = int(row["wallet_id"]) if row["wallet_id"] is not None else None
        return uid, wid

    def authenticate_key(self, raw_token: str, client_ip: str) -> tuple[dict[str, Any] | None, str]:
        clean = (raw_token or "").strip()
        if not is_bi_bearer_token(clean):
            return None, "invalid_prefix"

        tok_hash = hash_bi_token(clean)
        now = get_brt_now()
        now_iso = now.isoformat()

        with self.db.readonly() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, wallet_id, label, token_prefix, allowed_cidr,
                       include_admin, is_active, expires_at, last_used_at,
                       last_used_ip, created_at, revoked_at
                FROM bi_api_keys
                WHERE token_sha256 = ?
                """,
                (tok_hash,),
            ).fetchone()

        if row is None:
            return None, "not_found"

        raw_dict = dict(row)
        if not bool(raw_dict.get("is_active", 0)) or raw_dict.get("revoked_at"):
            return None, "revoked"

        exp_dt = _parse_iso_brt(raw_dict.get("expires_at"))
        if exp_dt is not None and now >= exp_dt:
            return None, "expired"

        uid = int(raw_dict["user_id"])
        users_repo = UsersRepository(self.db)
        user = users_repo.get_by_id(uid)
        if user is None or not bool(user.get("is_active", 1)):
            return None, "user_inactive"
        if users_repo.is_invite_expired(user) or bool(user.get("must_change_password", 0)):
            return None, "password_gate_pending"

        cidr_val = str(raw_dict.get("allowed_cidr") or "")
        if not is_ip_allowed_by_cidr(client_ip, cidr_val):
            return None, "cidr_blocked"

        if raw_dict.get("wallet_id") is not None:
            owned = WalletsRepository(self.db).get_owned_wallet(uid, int(raw_dict["wallet_id"]))
            if owned is None:
                return None, "wallet_deleted"

        safe_ip = (client_ip or "")[:64]
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE bi_api_keys SET last_used_at = ?, last_used_ip = ? WHERE id = ?",
                (now_iso, safe_ip, int(raw_dict["id"])),
            )

        raw_dict["last_used_at"] = now_iso
        raw_dict["last_used_ip"] = safe_ip
        sanitized = self._sanitize_key_row(raw_dict)
        sanitized["user"] = user
        return sanitized, "ok"
