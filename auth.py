"""
Módulo de autenticação, gerenciamento de senhas, rate-limiting e proteção CSRF
do Dashboard Córtex IA.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Any, Optional
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from config.settings import settings

MAX_IDLE_SECONDS: int = int(os.getenv("AUTH_MAX_IDLE_SECONDS", str(settings.AUTH_MAX_IDLE_SECONDS)))
MAX_SESSION_SECONDS: int = int(os.getenv("AUTH_MAX_SESSION_SECONDS", str(settings.AUTH_MAX_SESSION_SECONDS)))
MAX_LOGIN_ATTEMPTS: int = int(os.getenv("AUTH_MAX_LOGIN_ATTEMPTS", str(settings.AUTH_MAX_LOGIN_ATTEMPTS)))
LOCKOUT_SECONDS: int = int(os.getenv("AUTH_LOCKOUT_SECONDS", str(settings.AUTH_LOCKOUT_SECONDS)))

PBKDF2_ITERATIONS: int = 600_000
LEGACY_PBKDF2_ITERATIONS: int = 100_000

_DUMMY_SALT: bytes = secrets.token_bytes(32)
_DUMMY_KEY: bytes = hashlib.pbkdf2_hmac(
    "sha256",
    b"cortex_dummy_timing_guard_password",
    _DUMMY_SALT,
    PBKDF2_ITERATIONS,
)
_CSRF_SECRET: bytes = (
    os.getenv("CORTEX_CSRF_SECRET", "").strip()
    or f"cortex-csrf-v1:{settings.DB_PATH}"
).encode("utf-8")


class PasswordManager:
    """Utilitário criptográfico PBKDF2-HMAC-SHA256 com retrocompatibilidade transparente."""

    @staticmethod
    def hash_password(password: str) -> tuple[str, str]:
        """Gera hash hexadecimal de 64 caracteres (32 bytes) e salt de 64 caracteres."""
        salt = secrets.token_bytes(32)
        key = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            PBKDF2_ITERATIONS,
        )
        return key.hex(), salt.hex()

    @staticmethod
    def verify_password(password: str, expected_hash_hex: str, salt_hex: str) -> bool:
        """
        Verifica senha em tempo constante suportando:
        - Formato prefixado 'pbkdf2_sha256$<iters>$<hex>'
        - Hashes de 600.000 iterações (padrão atual)
        - Hashes legados de 100.000 iterações
        """
        try:
            if not expected_hash_hex or not salt_hex:
                return False
            salt = bytes.fromhex(salt_hex)
            pwd_bytes = password.encode("utf-8")

            if expected_hash_hex.startswith("pbkdf2_sha256$"):
                parts = expected_hash_hex.split("$", 2)
                if len(parts) != 3:
                    return False
                iters = int(parts[1])
                expected_key = bytes.fromhex(parts[2])
                computed = hashlib.pbkdf2_hmac("sha256", pwd_bytes, salt, iters)
                return hmac.compare_digest(computed, expected_key)

            expected_key = bytes.fromhex(expected_hash_hex)
            computed_current = hashlib.pbkdf2_hmac(
                "sha256",
                pwd_bytes,
                salt,
                PBKDF2_ITERATIONS,
            )
            if hmac.compare_digest(computed_current, expected_key):
                return True

            if PBKDF2_ITERATIONS != LEGACY_PBKDF2_ITERATIONS:
                computed_legacy = hashlib.pbkdf2_hmac(
                    "sha256",
                    pwd_bytes,
                    salt,
                    LEGACY_PBKDF2_ITERATIONS,
                )
                return hmac.compare_digest(computed_legacy, expected_key)

            return False
        except Exception:
            return False

    @staticmethod
    def dummy_verify(password: str) -> None:
        """Executa PBKDF2 descartável para mitigar enumeração de usuários por timing."""
        computed = hashlib.pbkdf2_hmac(
            "sha256",
            (password or "").encode("utf-8"),
            _DUMMY_SALT,
            PBKDF2_ITERATIONS,
        )
        hmac.compare_digest(computed, _DUMMY_KEY)


class RateLimiter:
    """Controlador thread-safe de tentativas de login por IP com limpeza periódica."""

    def __init__(
        self,
        max_attempts: int = MAX_LOGIN_ATTEMPTS,
        lockout_seconds: int = LOCKOUT_SECONDS,
        window_seconds: int = 900,
    ) -> None:
        self.max_attempts = max_attempts
        self.lockout_seconds = lockout_seconds
        self.window_seconds = window_seconds
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._last_prune: float = time.time()

    def _prune_expired(self, now: float) -> None:
        """Remove registros expirados periodicamente evitando crescimento de memória."""
        if (now - self._last_prune) < 120.0 and len(self._failures) < 512:
            return
        self._last_prune = now
        expired_locks = [ip for ip, ts in self._locked_until.items() if ts <= now]
        for ip in expired_locks:
            self._locked_until.pop(ip, None)
            self._failures.pop(ip, None)

        stale_ips = []
        for ip, hist in self._failures.items():
            fresh = [t for t in hist if (now - t) < self.window_seconds]
            if fresh:
                self._failures[ip] = fresh
            elif ip not in self._locked_until:
                stale_ips.append(ip)
        for ip in stale_ips:
            self._failures.pop(ip, None)

    def is_locked(self, ip: str) -> tuple[bool, int]:
        with self._lock:
            now = time.time()
            self._prune_expired(now)
            if ip in self._locked_until:
                remaining = int(self._locked_until[ip] - now)
                if remaining > 0:
                    return True, remaining
                del self._locked_until[ip]
                self._failures.pop(ip, None)
            return False, 0

    def record_failure(self, ip: str) -> tuple[int, int]:
        with self._lock:
            now = time.time()
            self._prune_expired(now)
            history = self._failures.get(ip, [])
            history = [t for t in history if (now - t) < self.window_seconds]
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
    """
    Obtém o IP real do cliente de forma resistente a spoofing de X-Forwarded-For.
    Quando atrás de proxy reverso local (127.0.0.1 / ::1), prioriza X-Real-IP
    ou o último salto confiável de X-Forwarded-For.
    """
    direct_ip = request.client.host if request.client else "127.0.0.1"
    if direct_ip in ("127.0.0.1", "::1"):
        real_ip = (request.headers.get("X-Real-IP") or "").strip()
        if real_ip:
            return real_ip
        forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
        if forwarded:
            hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
            if hops:
                return hops[-1]
    return direct_ip


def extract_session_token(request: Request) -> Optional[str]:
    """Extrai o token de sessão a partir do cookie HttpOnly ou header Bearer."""
    token = request.cookies.get("cortex_session")
    if token:
        return token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    return None


def derive_csrf_token(session_token: str, session: Optional[dict[str, Any]] = None) -> str:
    """Obtém ou deriva deterministicamente o token CSRF vinculado à sessão atual."""
    if session and session.get("csrf_token"):
        return str(session["csrf_token"])
    return hmac.new(
        _CSRF_SECRET,
        session_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def is_origin_allowed(origin_or_referer: str, host_header: str = "") -> bool:
    """Verifica se Origin/Referer pertence às origens permitidas ou ao próprio Host."""
    raw = (origin_or_referer or "").strip()
    if not raw:
        return True
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return False
    candidate_origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    raw_origins = getattr(settings, "ALLOWED_ORIGINS", "")
    if isinstance(raw_origins, (list, tuple, set)):
        origin_items = [str(o) for o in raw_origins]
    else:
        origin_items = str(raw_origins or "").split(",")

    allowed_list = [
        o.strip().rstrip("/").lower()
        for o in origin_items
        if o.strip()
    ]
    if candidate_origin in allowed_list:
        return True

    clean_host = (host_header or "").strip().lower()
    if clean_host and parsed.netloc.lower() == clean_host:
        return True

    return False


def validate_csrf_for_mutation(
    request: Request,
    session_token: str,
    session: Optional[dict[str, Any]] = None,
) -> None:
    """Valida Origin/Referer, Sec-Fetch-Site e X-CSRF-Token em rotas mutáveis."""
    if request.method.upper() not in ("POST", "PUT", "DELETE", "PATCH"):
        return

    sec_fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if sec_fetch_site and sec_fetch_site not in ("same-origin", "none"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requisição cross-site bloqueada pela proteção CSRF.",
        )

    host_hdr = request.headers.get("host", "")
    origin_hdr = request.headers.get("Origin", "")
    referer_hdr = request.headers.get("Referer", "")

    if origin_hdr:
        if not is_origin_allowed(origin_hdr, host_hdr):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Origem não autorizada (CSRF).",
            )
    elif referer_hdr:
        if not is_origin_allowed(referer_hdr, host_hdr):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Referer não autorizado (CSRF).",
            )

    expected_csrf = derive_csrf_token(session_token, session)
    provided_csrf = (request.headers.get("X-CSRF-Token") or "").strip()

    if provided_csrf:
        if not hmac.compare_digest(provided_csrf, expected_csrf):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token CSRF inválido.",
            )
        return

    # Clientes de navegador sempre enviam Sec-Fetch-Mode / Sec-Fetch-Site: exige X-CSRF-Token
    auth_hdr = request.headers.get("Authorization") or ""
    is_browser_fetch = bool(sec_fetch_site or request.headers.get("Sec-Fetch-Mode"))
    if is_browser_fetch and not auth_hdr.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token CSRF ausente.",
        )


def _fetch_session(db: Any, token: str, touch: bool) -> Optional[dict[str, Any]]:
    """Consulta sessão válida controlando explicitamente a atualização de last_active_at."""
    idle_limit = int(getattr(settings, "AUTH_MAX_IDLE_SECONDS", MAX_IDLE_SECONDS))
    try:
        session = db.get_valid_session(token, max_idle_seconds=idle_limit, touch=touch)
    except TypeError:
        session = db.get_valid_session(token, max_idle_seconds=idle_limit)
        if touch and session is not None and hasattr(db, "touch_session"):
            db.touch_session(token)
    else:
        if touch and session is not None and hasattr(db, "touch_session"):
            db.touch_session(token)
    return session


def get_current_user(request: Request) -> dict[str, Any]:
    """
    Dependência para rotas GET / polling em background.
    NÃO renova last_active_at para preservar o timeout de inatividade real do usuário.
    """
    from data.database import DatabaseManager

    token = extract_session_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado. Por favor faça login.",
        )

    db = DatabaseManager()
    session = _fetch_session(db, token, touch=False)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão expirada ou inválida.",
        )

    user = db.get_user_by_id(session["user_id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado.",
        )

    user_out = dict(user)
    user_out["csrf_token"] = derive_csrf_token(token, session)
    return user_out


def get_current_user_interactive(request: Request) -> dict[str, Any]:
    """
    Dependência para ações interativas do usuário (/api/auth/heartbeat e rotas POST/PUT/DELETE).
    Valida proteção CSRF e renova last_active_at da sessão.
    """
    from data.database import DatabaseManager

    token = extract_session_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Não autenticado. Por favor faça login.",
        )

    db = DatabaseManager()
    session = _fetch_session(db, token, touch=True)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão expirada ou inválida.",
        )

    validate_csrf_for_mutation(request, token, session)

    user = db.get_user_by_id(session["user_id"])
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado.",
        )

    user_out = dict(user)
    user_out["csrf_token"] = derive_csrf_token(token, session)
    return user_out


verify_csrf_and_touch = get_current_user_interactive


def get_current_user_optional(request: Request) -> Optional[dict[str, Any]]:
    try:
        return get_current_user(request)
    except Exception:
        return None
