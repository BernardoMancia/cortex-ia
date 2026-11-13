from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from security.key_manager import KeyManager, key_manager

GENESIS_HASH: str = "0" * 64


class AuditHashChain:
    def __init__(self, km: KeyManager | None = None) -> None:
        self._km = km or key_manager

    @staticmethod
    def canonicalize_details(details: dict[str, Any] | str | None) -> str:
        if details is None:
            return "{}"
        if isinstance(details, str):
            return details
        return json.dumps(details, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def compute_entry_hash(
        self,
        seq_id: int,
        timestamp: str,
        event_type: str,
        actor_user_id: int | None,
        target_wallet_id: int | None,
        ip_address: str,
        details_json: str,
        prev_hash: str,
    ) -> str:
        actor = "" if actor_user_id is None else str(int(actor_user_id))
        wallet = "" if target_wallet_id is None else str(int(target_wallet_id))
        canonical = (
            f"{int(seq_id)}|{timestamp}|{event_type}|{actor}|"
            f"{wallet}|{ip_address}|{prev_hash}|{details_json}"
        )
        return hmac.new(
            self._km.audit_key,
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def verify_chain(self, ordered_rows: list[dict[str, Any]]) -> dict[str, Any]:
        expected_prev = GENESIS_HASH
        checked = 0
        for row in ordered_rows:
            seq_id = int(row["id"])
            row_prev = str(row["prev_hash"])
            if not hmac.compare_digest(row_prev, expected_prev):
                return {
                    "valid": False,
                    "checked_count": checked,
                    "broken_at_id": seq_id,
                    "reason": "prev_hash_mismatch",
                }
            recomputed = self.compute_entry_hash(
                seq_id=seq_id,
                timestamp=str(row["timestamp"]),
                event_type=str(row["event_type"]),
                actor_user_id=row.get("actor_user_id"),
                target_wallet_id=row.get("target_wallet_id"),
                ip_address=str(row.get("ip_address") or ""),
                details_json=str(row.get("details_json") or "{}"),
                prev_hash=row_prev,
            )
            if not hmac.compare_digest(recomputed, str(row["entry_hash"])):
                return {
                    "valid": False,
                    "checked_count": checked,
                    "broken_at_id": seq_id,
                    "reason": "entry_hash_tampered",
                }
            expected_prev = str(row["entry_hash"])
            checked += 1
        return {
            "valid": True,
            "checked_count": checked,
            "broken_at_id": None,
            "head_hash": expected_prev,
        }


audit_chain = AuditHashChain()
