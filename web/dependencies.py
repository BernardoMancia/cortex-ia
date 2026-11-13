from __future__ import annotations

from dataclasses import dataclass, field
import hmac
from typing import Any

from fastapi import Depends, HTTPException, Request, status

from security.bi_tokens import is_bi_bearer_token
from security.rate_limit import mutation_api_limiter
from security.session_csrf import (
    derive_csrf_token,
    enforce_csrf_protection,
    extract_session_credentials,
    get_client_ip,
)
from security.step_up import step_up_manager
from storage.audit_repo import AuditRepository
from storage.bi_tokens_repo import BiTokensRepository
from storage.connection import DatabaseConnection
from storage.users_repo import UsersRepository
from storage.wallets_repo import WalletsRepository


@dataclass
class AuthContext:
    user: dict[str, Any]
    session: dict[str, Any]
    csrf_token: str
    active_wallet: dict[str, Any] | None
    is_luke_admin: bool
    ip_address: str
    db: DatabaseConnection | None = field(default=None, repr=False)
    raw_token: str = field(default="", repr=False)


def get_db(request: Request) -> DatabaseConnection:
    db_inst = getattr(request.app.state, "db", None)
    if isinstance(db_inst, DatabaseConnection):
        return db_inst
    created = DatabaseConnection()
    request.app.state.db = created
    return created


def sanitize_wallet(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    if "encrypted_connector_config" in out:
        enc_val = out.pop("encrypted_connector_config", "")
        out["has_encrypted_config"] = bool(enc_val)
    elif "has_encrypted_config" not in out:
        out["has_encrypted_config"] = False
    return out


def verify_csrf_header(expected_csrf: str, provided_csrf: str | None) -> None:
    clean_expected = (expected_csrf or "").strip()
    clean_provided = (provided_csrf or "").strip()
    if not clean_expected or not clean_provided or not hmac.compare_digest(clean_expected, clean_provided):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token CSRF obrigatório ausente ou inválido.",
        )


def _build_auth_context(request: Request, enforce_password_gate: bool = True) -> AuthContext:
    db = get_db(request)
    users_repo = UsersRepository(db)
    wallets_repo = WalletsRepository(db)
    audit_repo = AuditRepository(db)

    ip_address = get_client_ip(request)
    user_agent = request.headers.get("User-Agent", "")
    auth_hdr = (request.headers.get("Authorization") or "").strip()
    bearer_tok = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else ""
    raw_token, _is_bearer = extract_session_credentials(request)

    if is_bi_bearer_token(raw_token) or is_bi_bearer_token(bearer_tok):
        offending_tok = bearer_tok if is_bi_bearer_token(bearer_tok) else (raw_token or "")
        actor_uid, target_wid = BiTokensRepository(db).lookup_owner_for_audit(offending_tok)
        audit_repo.record_event(
            event_type="BI_TOKEN_SCOPE_VIOLATION",
            actor_user_id=actor_uid,
            target_wallet_id=target_wid,
            ip_address=ip_address,
            details={
                "path": request.url.path,
                "method": request.method.upper(),
                "token_prefix": offending_tok[:14],
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tokens BI possuem acesso estritamente somente-leitura em /api/bi/v1/*.",
        )

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticação necessária.",
        )

    touch = request.query_params.get("passive") != "1"
    session, reason = users_repo.validate_session(raw_token, user_agent, touch=touch)
    if session is None:
        if reason == "fingerprint_mismatch":
            audit_repo.record_event(
                event_type="SESSION_FINGERPRINT_MISMATCH",
                ip_address=ip_address,
                details={
                    "path": request.url.path,
                    "user_agent": user_agent[:120],
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Sessão invalidada por divergência de dispositivo ou navegador.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão inválida ou expirada.",
        )

    uid = int(session["user_id"])
    user = users_repo.get_by_id(uid)
    if user is None or not bool(user.get("is_active", 1)):
        users_repo.revoke_session_by_raw_token(raw_token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Conta inativa ou revogada.",
        )

    if users_repo.is_invite_expired(user):
        users_repo.revoke_session_by_raw_token(raw_token)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha temporária expirada. Solicite novo convite ao administrador Luke.",
        )

    csrf_token = derive_csrf_token(
        str(session.get("token_sha256", "")),
        str(session.get("csrf_token", "")),
    )

    if request.method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
        enforce_csrf_protection(request, session, is_bearer_auth=False)
        verify_csrf_header(csrf_token, request.headers.get("X-CSRF-Token"))
        locked, retry_sec = mutation_api_limiter.hit(f"mut:{uid}:{ip_address}")
        if locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Limite de requisições de escrita atingido. Aguarde {retry_sec}s.",
            )

    if enforce_password_gate and bool(user.get("must_change_password", 0)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "MUST_CHANGE_PASSWORD",
                "message": "Alteração obrigatória de senha pendente no primeiro acesso.",
            },
        )

    sess_wid = session.get("active_wallet_id")
    active_wallet = wallets_repo.get_owned_wallet(uid, int(sess_wid)) if sess_wid is not None else None
    if active_wallet is None:
        active_wallet = wallets_repo.get_default_or_first_wallet(uid)
        if active_wallet is not None:
            users_repo.set_session_active_wallet(
                str(session["token_sha256"]),
                uid,
                int(active_wallet["id"]),
            )
            session["active_wallet_id"] = int(active_wallet["id"])

    return AuthContext(
        user=user,
        session=session,
        csrf_token=csrf_token,
        active_wallet=sanitize_wallet(active_wallet),
        is_luke_admin=UsersRepository.is_luke_owner(user),
        ip_address=ip_address,
        db=db,
        raw_token=raw_token,
    )


def get_auth_context_allow_first_login(request: Request) -> AuthContext:
    return _build_auth_context(request, enforce_password_gate=False)


def get_current_context(request: Request) -> AuthContext:
    return _build_auth_context(request, enforce_password_gate=True)


def require_luke_admin(
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> AuthContext:
    if not UsersRepository.is_luke_owner(ctx.user):
        db = ctx.db or get_db(request)
        AuditRepository(db).record_event(
            event_type="UNAUTHORIZED_ADMIN_ATTEMPT",
            actor_user_id=int(ctx.user["id"]),
            ip_address=ctx.ip_address,
            details={
                "path": request.url.path,
                "method": request.method.upper(),
                "username": str(ctx.user.get("username", "")),
            },
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Acesso exclusivo da conta administrativa Luke.",
        )
    return ctx


def require_step_up(
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> AuthContext:
    token = (request.headers.get("X-Step-Up-Token") or "").strip()
    valid = step_up_manager.verify_token(
        token=token,
        user_id=int(ctx.user["id"]),
        session_token_hash=str(ctx.session.get("token_sha256", "")),
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "STEP_UP_REQUIRED",
                "message": "Confirmação de segurança Step-Up necessária.",
            },
        )
    return ctx


def resolve_active_wallet(
    ctx: AuthContext,
    wallet_id: int | None = None,
    db: DatabaseConnection | None = None,
    require_exists: bool = True,
) -> dict[str, Any] | None:
    target_db = db or ctx.db or DatabaseConnection()
    wallets_repo = WalletsRepository(target_db)
    uid = int(ctx.user["id"])

    if wallet_id is not None:
        owned = wallets_repo.get_owned_wallet(uid, int(wallet_id))
        if owned is None:
            AuditRepository(target_db).record_event(
                event_type="IDOR_BLOCKED",
                actor_user_id=uid,
                target_wallet_id=int(wallet_id),
                ip_address=ctx.ip_address,
                details={"requested_wallet_id": int(wallet_id)},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acesso negado à carteira solicitada (Anti-IDOR).",
            )
        return owned

    if ctx.active_wallet is not None:
        owned = wallets_repo.get_owned_wallet(uid, int(ctx.active_wallet["id"]))
        if owned is not None:
            return owned

    fallback = wallets_repo.get_default_or_first_wallet(uid)
    if fallback is None and require_exists:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhuma carteira configurada para este usuário.",
        )
    return fallback
