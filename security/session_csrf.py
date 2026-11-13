from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from config.settings import settings
from security.key_manager import key_manager


def hash_session_token(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def generate_session_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(32)
    return raw, hash_session_token(raw)


def compute_ua_fingerprint(user_agent: str) -> str:
    norm = (user_agent or "unknown").strip()[:256]
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def verify_session_fingerprint(stored_fingerprint: str, current_user_agent: str) -> bool:
    if not stored_fingerprint:
        return True
    current_fp = compute_ua_fingerprint(current_user_agent)
    return hmac.compare_digest(stored_fingerprint, current_fp)


def derive_csrf_token(session_token_hash: str, stored_csrf: str = "") -> str:
    if stored_csrf:
        return stored_csrf
    return hmac.new(
        key_manager.csrf_key,
        session_token_hash.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_csrf_header(expected_csrf: str, provided_csrf: str | None) -> bool:
    clean_expected = (expected_csrf or "").strip()
    clean_provided = (provided_csrf or "").strip()
    if not clean_expected or not clean_provided:
        return False
    return hmac.compare_digest(clean_expected, clean_provided)


def get_client_ip(request: Request) -> str:
    direct = request.client.host if request.client else "127.0.0.1"
    if direct in ("127.0.0.1", "::1"):
        real_ip = (request.headers.get("X-Real-IP") or "").strip()
        if real_ip:
            return real_ip
        forwarded = (request.headers.get("X-Forwarded-For") or "").strip()
        if forwarded:
            hops = [h.strip() for h in forwarded.split(",") if h.strip()]
            if hops:
                return hops[-1]
    return direct


def extract_session_credentials(request: Request) -> tuple[str | None, bool]:
    cookie_tok = request.cookies.get("cortex_session")
    if cookie_tok:
        return cookie_tok.strip(), False
    auth_hdr = (request.headers.get("Authorization") or "").strip()
    if auth_hdr.startswith("Bearer "):
        return auth_hdr[7:].strip(), True
    return None, False


def is_origin_allowed(origin_or_referer: str, host_header: str = "") -> bool:
    raw = (origin_or_referer or "").strip()
    if not raw:
        return True
    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return False
    candidate = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    allowed = [o.lower() for o in settings.ALLOWED_ORIGINS if o]
    if candidate in allowed:
        return True
    clean_host = (host_header or "").strip().lower()
    if clean_host and parsed.netloc.lower() == clean_host:
        return True
    return False


def enforce_csrf_protection(
    request: Request,
    session_row: dict[str, Any],
    is_bearer_auth: bool = False,
) -> None:
    if request.method.upper() not in ("POST", "PUT", "DELETE", "PATCH"):
        return

    sec_fetch_site = (request.headers.get("Sec-Fetch-Site") or "").strip().lower()
    if sec_fetch_site and sec_fetch_site not in ("same-origin", "none"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Requisição cross-site bloqueada pela blindagem CSRF.",
        )

    host_hdr = request.headers.get("host", "")
    origin_hdr = request.headers.get("Origin", "")
    referer_hdr = request.headers.get("Referer", "")

    if origin_hdr and not is_origin_allowed(origin_hdr, host_hdr):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origin não autorizado (CSRF).",
        )
    if not origin_hdr and referer_hdr and not is_origin_allowed(referer_hdr, host_hdr):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Referer não autorizado (CSRF).",
        )

    expected_csrf = derive_csrf_token(
        str(session_row.get("token_sha256", "")),
        str(session_row.get("csrf_token", "")),
    )
    provided_csrf = (request.headers.get("X-CSRF-Token") or "").strip()

    if not is_bearer_auth:
        if not provided_csrf or not hmac.compare_digest(provided_csrf, expected_csrf):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token CSRF obrigatório ausente ou inválido.",
            )
    elif provided_csrf and not hmac.compare_digest(provided_csrf, expected_csrf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token CSRF inválido.",
        )
