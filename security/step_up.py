from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

from config.settings import settings
from security.key_manager import KeyManager, key_manager


class StepUpAuthManager:
    def __init__(self, km: KeyManager | None = None, ttl_seconds: int | None = None) -> None:
        self._km = km or key_manager
        self.ttl_seconds = int(ttl_seconds if ttl_seconds is not None else settings.STEP_UP_TTL_SECONDS)
        self._lock = threading.Lock()
        self._grants: dict[str, tuple[int, str, float]] = {}

    def _prune_locked(self, now: float) -> None:
        expired = [tok for tok, (_, _, exp) in self._grants.items() if exp <= now]
        for tok in expired:
            self._grants.pop(tok, None)

    def issue_token(self, user_id: int, session_token_hash: str) -> dict[str, int | str]:
        nonce = secrets.token_urlsafe(24)
        exp = time.time() + self.ttl_seconds
        payload = f"{int(user_id)}:{session_token_hash}:{int(exp)}:{nonce}"
        sig = hmac.new(self._km.csrf_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        full_token = f"{nonce}.{sig[:32]}"
        token_hash = hashlib.sha256(full_token.encode("utf-8")).hexdigest()
        with self._lock:
            self._prune_locked(time.time())
            self._grants[token_hash] = (int(user_id), session_token_hash, exp)
        return {
            "step_up_token": full_token,
            "expires_in": self.ttl_seconds,
        }

    def verify_token(self, token: str, user_id: int, session_token_hash: str) -> bool:
        clean = (token or "").strip()
        if not clean:
            return False
        token_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
        now = time.time()
        with self._lock:
            self._prune_locked(now)
            grant = self._grants.get(token_hash)
            if grant is None:
                return False
            g_uid, g_sess_hash, g_exp = grant
            if g_exp <= now:
                self._grants.pop(token_hash, None)
                return False
            return g_uid == int(user_id) and hmac.compare_digest(g_sess_hash, session_token_hash)

    def revoke_for_user(self, user_id: int) -> None:
        with self._lock:
            to_drop = [k for k, (uid, _, _) in self._grants.items() if uid == int(user_id)]
            for k in to_drop:
                self._grants.pop(k, None)


step_up_manager = StepUpAuthManager()
