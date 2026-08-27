import os
import time
import hmac
import hashlib
import secrets
import threading
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from fastapi import Request, HTTPException, status

MAX_IDLE_SECONDS = int(os.getenv("AUTH_MAX_IDLE_SECONDS", "900"))
MAX_SESSION_SECONDS = int(os.getenv("AUTH_MAX_SESSION_SECONDS", "28800"))
MAX_LOGIN_ATTEMPTS = int(os.getenv("AUTH_MAX_LOGIN_ATTEMPTS", "5"))
LOCKOUT_SECONDS = int(os.getenv("AUTH_LOCKOUT_SECONDS", "900"))
PBKDF2_ITERATIONS = 100000

class PasswordManager:
    @staticmethod
    def hash_password(password: str) -> tuple[str, str]:
        salt = secrets.token_bytes(32)
        key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            PBKDF2_ITERATIONS
        )
        return key.hex(), salt.hex()

    @staticmethod
    def verify_password(password: str, expected_hash_hex: str, salt_hex: str) -> bool:
        try:
            salt = bytes.fromhex(salt_hex)
            expected_key = bytes.fromhex(expected_hash_hex)
            computed_key = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                salt,
                PBKDF2_ITERATIONS
            )
            return hmac.compare_digest(computed_key, expected_key)
        except Exception:
            return False

class RateLimiter:
    def __init__(self, max_attempts: int = MAX_LOGIN_ATTEMPTS, lockout_seconds: int = LOCKOUT_SECONDS):
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, ip: str) -> tuple[bool, int]:
        with self._lock:
            now = time.time()
            if ip in self._locked_until:
                remaining = int(self._locked_until[ip] - now)
                if remaining > 0:
                    return True, remaining
                else:
                    del self._locked_until[ip]
                    if ip in self._failures:
                        del self._failures[ip]
            return False, 0

    def record_failure(self, ip: str) -> tuple[int, int]:
        with self._lock:
            now = time.time()
            history = self._failures.get(ip, [])
            history = [t for t in history if now - t < 60]
            history.append(now)
            self._failures[ip] = history

            if len(history) >= self.max_attempts:
                self._locked_until[ip] = now + self.lockout_seconds
                return len(history), self.lockout_seconds
            return len(history), 0

    def reset(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)

rate_limiter = RateLimiter()

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "127.0.0.1"

def extract_session_token(request: Request) -> Optional[str]:
    token = request.cookies.get("cortex_session")
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None

def get_current_user(request: Request) -> dict[str, Any]:
    from data.database import DatabaseManager
    token = extract_session_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado. Por favor faça login."
        )

    db = DatabaseManager()
    session = db.get_valid_session(token, max_idle_seconds=MAX_IDLE_SECONDS)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão expirada ou inválida."
        )

    user = db.get_user_by_id(session["user_id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado."
        )

    db.touch_session(token)
    return user

def get_current_user_optional(request: Request) -> Optional[dict[str, Any]]:
    try:
        return get_current_user(request)
    except Exception:
        return None
