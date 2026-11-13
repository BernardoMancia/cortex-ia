from __future__ import annotations

import json
from typing import Any

from domain.b3_rules import get_brt_now
from security.audit_chain import GENESIS_HASH, audit_chain
from storage.connection import DatabaseConnection


class AuditRepository:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()

    def record_event(
        self,
        event_type: str,
        actor_user_id: int | None = None,
        target_wallet_id: int | None = None,
        ip_address: str = "",
        details: dict[str, Any] | None = None,
    ) -> int:
        ts = get_brt_now().isoformat()
        details_json = audit_chain.canonicalize_details(details or {})
        with self.db.transaction() as conn:
            last = conn.execute(
                "SELECT id, entry_hash FROM security_audit_log ORDER BY id DESC LIMIT 1"
            ).fetchone()
            prev_hash = str(last["entry_hash"]) if last is not None else GENESIS_HASH
            seq_id = (int(last["id"]) + 1) if last is not None else 1
            entry_hash = audit_chain.compute_entry_hash(
                seq_id=seq_id,
                timestamp=ts,
                event_type=event_type,
                actor_user_id=actor_user_id,
                target_wallet_id=target_wallet_id,
                ip_address=ip_address,
                details_json=details_json,
                prev_hash=prev_hash,
            )
            conn.execute(
                """
                INSERT INTO security_audit_log (
                    id, timestamp, event_type, actor_user_id, target_wallet_id,
                    ip_address, details_json, prev_hash, entry_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seq_id,
                    ts,
                    event_type,
                    actor_user_id,
                    target_wallet_id,
                    ip_address or "",
                    details_json,
                    prev_hash,
                    entry_hash,
                ),
            )
            return seq_id

    def verify_integrity(self) -> dict[str, Any]:
        with self.db.readonly() as conn:
            rows = conn.execute(
                "SELECT * FROM security_audit_log ORDER BY id ASC"
            ).fetchall()
        return audit_chain.verify_chain([dict(r) for r in rows])

    def list_events(
        self,
        limit: int = 100,
        wallet_id: int | None = None,
        actor_user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 500))
        clauses: list[str] = []
        params: list[Any] = []
        if wallet_id is not None:
            clauses.append("target_wallet_id = ?")
            params.append(int(wallet_id))
        if actor_user_id is not None:
            clauses.append("actor_user_id = ?")
            params.append(int(actor_user_id))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(capped)
        with self.db.readonly() as conn:
            rows = conn.execute(
                f"SELECT * FROM security_audit_log {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            try:
                item["details"] = json.loads(item.get("details_json") or "{}")
            except Exception:
                item["details"] = {}
            out.append(item)
        return out
