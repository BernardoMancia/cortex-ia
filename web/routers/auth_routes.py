from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from config.settings import settings
from security.password import password_security
from security.rate_limit import login_ip_limiter
from security.session_csrf import get_client_ip
from security.step_up import step_up_manager
from security.totp_mfa import totp_manager
from security.wallet_crypto import wallet_crypto
from storage.audit_repo import AuditRepository
from storage.users_repo import UsersRepository
from storage.wallets_repo import WalletsRepository
from web.dependencies import (
    AuthContext,
    get_auth_context_allow_first_login,
    get_db,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginPayload(BaseModel):
    username: str = Field(default="", max_length=64)
    password: str = Field(default="", max_length=256)
    totp_code: str | None = Field(default=None, max_length=16)
    recovery_code: str | None = Field(default=None, max_length=32)


class FirstLoginPayload(BaseModel):
    new_password: str = Field(..., min_length=1, max_length=256)
    confirm_password: str | None = Field(default=None, max_length=256)
    current_password: str | None = Field(default=None, max_length=256)


def _public_user(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(user["id"]),
        "username": str(user["username"]),
        "is_owner_admin": UsersRepository.is_luke_owner(user),
        "totp_enabled": bool(user.get("totp_enabled", 0)),
        "must_change_password": bool(user.get("must_change_password", 0)),
        "last_login_at": user.get("last_login_at"),
    }


def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key="cortex_session",
        value=raw_token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="strict",
        max_age=settings.AUTH_MAX_SESSION_SECONDS,
        path="/",
    )


@router.post("/login")
def login_endpoint(
    payload: LoginPayload,
    request: Request,
    response: Response,
) -> dict[str, Any]:
    db = get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)
    ip = get_client_ip(request)
    ip_key = f"login_ip:{ip}"

    locked_ip, retry_ip = login_ip_limiter.check(ip_key)
    if locked_ip:
        audit_repo.record_event(
            event_type="LOGIN_IP_RATE_LIMITED",
            ip_address=ip,
            details={"retry_seconds": retry_ip},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Muitas tentativas deste IP. Aguarde {retry_ip}s.",
        )

    clean_username = (payload.username or "").strip()
    user = users_repo.get_by_username(clean_username)
    if user is None:
        password_security.dummy_verify(payload.password)
        login_ip_limiter.hit(ip_key)
        audit_repo.record_event(
            event_type="LOGIN_FAILED_UNKNOWN_USER",
            ip_address=ip,
            details={"username": clean_username[:32]},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha incorretos.",
        )

    uid = int(user["id"])
    if not bool(user.get("is_active", 1)):
        password_security.dummy_verify(payload.password)
        login_ip_limiter.hit(ip_key)
        audit_repo.record_event(
            event_type="LOGIN_BLOCKED_INACTIVE",
            actor_user_id=uid,
            ip_address=ip,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta desativada pelo administrador.",
        )

    locked_user, retry_user = users_repo.is_user_locked(user)
    if locked_user:
        password_security.dummy_verify(payload.password)
        audit_repo.record_event(
            event_type="LOGIN_BLOCKED_LOCKED",
            actor_user_id=uid,
            ip_address=ip,
            details={"retry_seconds": retry_user},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Conta temporariamente bloqueada por excesso de tentativas. Aguarde {retry_user}s.",
        )

    if users_repo.is_invite_expired(user):
        password_security.dummy_verify(payload.password)
        audit_repo.record_event(
            event_type="LOGIN_INVITE_EXPIRED",
            actor_user_id=uid,
            ip_address=ip,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Senha temporária de 24h expirada. Solicite redefinição ao administrador Luke.",
        )

    pwd_ok = password_security.verify_password(
        payload.password,
        str(user.get("password_hash", "")),
        str(user.get("salt", "")),
    )
    if not pwd_ok:
        attempts, lock_sec = users_repo.record_login_failure(uid)
        login_ip_limiter.hit(ip_key)
        audit_repo.record_event(
            event_type="LOGIN_FAILED_BAD_PASSWORD",
            actor_user_id=uid,
            ip_address=ip,
            details={"attempts": attempts, "locked_seconds": lock_sec},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha incorretos.",
        )

    if bool(user.get("totp_enabled", 0)):
        totp_code = (payload.totp_code or "").strip()
        rec_code = (payload.recovery_code or "").strip()
        if not totp_code and not rec_code:
            return {
                "success": False,
                "token": None,
                "csrf_token": None,
                "must_change_password": False,
                "totp_required": True,
                "message": "Informe o código 2FA do autenticador ou um código de recuperação.",
                "user": _public_user(user),
            }

        if totp_code:
            try:
                secret_b32 = wallet_crypto.decrypt_totp_secret(
                    uid,
                    str(user.get("encrypted_totp_secret", "")),
                )
            except Exception:
                secret_b32 = ""
            if not secret_b32 or not totp_manager.verify_code(uid, secret_b32, totp_code):
                users_repo.record_login_failure(uid)
                login_ip_limiter.hit(ip_key)
                audit_repo.record_event(
                    event_type="LOGIN_FAILED_BAD_TOTP",
                    actor_user_id=uid,
                    ip_address=ip,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Código 2FA inválido ou já utilizado.",
                )
        else:
            matched, updated_json = totp_manager.consume_recovery_code(
                rec_code,
                str(user.get("recovery_codes_json") or "[]"),
            )
            if not matched:
                users_repo.record_login_failure(uid)
                login_ip_limiter.hit(ip_key)
                audit_repo.record_event(
                    event_type="LOGIN_FAILED_BAD_RECOVERY_CODE",
                    actor_user_id=uid,
                    ip_address=ip,
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Código de recuperação inválido ou já utilizado.",
                )
            users_repo.update_recovery_codes(uid, updated_json)
            audit_repo.record_event(
                event_type="LOGIN_RECOVERY_CODE_CONSUMED",
                actor_user_id=uid,
                ip_address=ip,
            )

    users_repo.record_login_success(uid)
    login_ip_limiter.reset(ip_key)
    sess = users_repo.create_session(
        user_id=uid,
        ip_address=ip,
        user_agent=request.headers.get("User-Agent", ""),
    )
    _set_session_cookie(response, sess["raw_token"])

    fresh_user = users_repo.get_by_id(uid) or user
    audit_repo.record_event(
        event_type="LOGIN_SUCCESS",
        actor_user_id=uid,
        ip_address=ip,
        details={"must_change_password": bool(fresh_user.get("must_change_password", 0))},
    )
    return {
        "success": True,
        "token": sess["raw_token"],
        "csrf_token": sess["csrf_token"],
        "must_change_password": bool(fresh_user.get("must_change_password", 0)),
        "totp_required": False,
        "message": "Autenticado com sucesso.",
        "user": _public_user(fresh_user),
    }


@router.post("/first-login")
def first_login_endpoint(
    payload: FirstLoginPayload,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context_allow_first_login),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)
    uid = int(ctx.user["id"])

    if payload.confirm_password is not None and payload.new_password != payload.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A confirmação de senha não coincide.",
        )

    if payload.current_password:
        cur_ok = password_security.verify_password(
            payload.current_password,
            str(ctx.user.get("password_hash", "")),
            str(ctx.user.get("salt", "")),
        )
        if not cur_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Senha atual incorreta.",
            )

    if password_security.verify_password(
        payload.new_password,
        str(ctx.user.get("password_hash", "")),
        str(ctx.user.get("salt", "")),
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A nova senha deve ser diferente da senha temporária inicial.",
        )

    valid_policy, policy_msg = password_security.validate_password_policy(
        payload.new_password,
    )
    if not valid_policy:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=policy_msg,
        )

    users_repo.complete_first_login_password(uid, payload.new_password)
    users_repo.revoke_all_user_sessions(uid)
    step_up_manager.revoke_for_user(uid)

    active_wid = int(ctx.active_wallet["id"]) if ctx.active_wallet is not None else None
    new_sess = users_repo.create_session(
        user_id=uid,
        ip_address=ctx.ip_address,
        user_agent=request.headers.get("User-Agent", ""),
        active_wallet_id=active_wid,
    )
    _set_session_cookie(response, new_sess["raw_token"])

    updated_user = users_repo.get_by_id(uid) or ctx.user
    audit_repo.record_event(
        event_type="FIRST_LOGIN_COMPLETED",
        actor_user_id=uid,
        ip_address=ctx.ip_address,
        details={"username": str(updated_user.get("username", ""))},
    )
    return {
        "success": True,
        "token": new_sess["raw_token"],
        "csrf_token": new_sess["csrf_token"],
        "must_change_password": False,
        "message": "Senha definitiva configurada com sucesso.",
        "user": _public_user(updated_user),
    }


@router.post("/logout")
def logout_endpoint(
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(get_auth_context_allow_first_login),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)
    uid = int(ctx.user["id"])

    users_repo.revoke_session_by_raw_token(ctx.raw_token)
    step_up_manager.revoke_for_user(uid)
    response.delete_cookie(key="cortex_session", path="/", samesite="strict")

    audit_repo.record_event(
        event_type="LOGOUT",
        actor_user_id=uid,
        ip_address=ctx.ip_address,
    )
    return {"success": True}


@router.get("/me")
def me_endpoint(
    request: Request,
    ctx: AuthContext = Depends(get_auth_context_allow_first_login),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallets_repo = WalletsRepository(db)
    uid = int(ctx.user["id"])
    pub_user = _public_user(ctx.user)
    wallets = wallets_repo.list_for_user(uid)

    return {
        "success": True,
        "id": pub_user["id"],
        "username": pub_user["username"],
        "csrf_token": ctx.csrf_token,
        "must_change_password": pub_user["must_change_password"],
        "is_owner_admin": pub_user["is_owner_admin"],
        "totp_enabled": pub_user["totp_enabled"],
        "user": pub_user,
        "active_wallet": ctx.active_wallet,
        "wallets": wallets,
        "environment": settings.ENVIRONMENT_ID,
        "environment_label": settings.ENVIRONMENT_LABEL,
        "is_production": settings.IS_PRODUCTION,
        "is_simulation": settings.IS_SIMULATION,
    }


@router.get("/heartbeat")
@router.post("/heartbeat")
def heartbeat_endpoint(
    ctx: AuthContext = Depends(get_auth_context_allow_first_login),
) -> dict[str, Any]:
    return {
        "success": True,
        "authenticated": True,
        "csrf_token": ctx.csrf_token,
        "expires_at": ctx.session.get("expires_at"),
    }
