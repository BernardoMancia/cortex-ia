from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from security.password import password_security
from security.rate_limit import step_up_limiter
from security.step_up import step_up_manager
from security.totp_mfa import render_qr_svg_offline, totp_manager
from security.wallet_crypto import wallet_crypto
from storage.audit_repo import AuditRepository
from storage.users_repo import UsersRepository
from web.dependencies import (
    AuthContext,
    get_current_context,
    get_db,
    require_step_up,
)

router = APIRouter(prefix="/api/security", tags=["security"])


class TotpEnablePayload(BaseModel):
    code: str = Field(default="", max_length=16)
    totp_code: str = Field(default="", max_length=16)


class PasswordChangePayload(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=1, max_length=256)
    confirm_password: str | None = Field(default=None, max_length=256)
    totp_code: str | None = Field(default=None, max_length=16)


class StepUpPayload(BaseModel):
    password: str = Field(..., min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, max_length=16)
    recovery_code: str | None = Field(default=None, max_length=32)


@router.get("/totp/setup")
@router.post("/totp/setup")
def totp_setup_endpoint(
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)
    uid = int(ctx.user["id"])

    if bool(ctx.user.get("totp_enabled", 0)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O 2FA TOTP já está ativo. Desative-o antes de gerar uma nova chave.",
        )

    secret = totp_manager.generate_secret()
    otpauth_uri = totp_manager.build_provisioning_uri(secret, str(ctx.user["username"]))
    qr_svg = render_qr_svg_offline(otpauth_uri)
    recovery_codes, recovery_codes_json = totp_manager.generate_recovery_codes()
    encrypted_secret = wallet_crypto.encrypt_totp_secret(uid, secret)

    users_repo.save_user_totp(
        user_id=uid,
        encrypted_secret=encrypted_secret,
        recovery_codes_json=recovery_codes_json,
        enabled=False,
    )
    audit_repo.record_event(
        event_type="TOTP_SETUP_INITIATED",
        actor_user_id=uid,
        ip_address=ctx.ip_address,
    )
    return {
        "success": True,
        "secret": secret,
        "otpauth_uri": otpauth_uri,
        "qr_svg": qr_svg,
        "recovery_codes": recovery_codes,
    }


@router.post("/totp/enable")
def totp_enable_endpoint(
    payload: TotpEnablePayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)
    uid = int(ctx.user["id"])

    fresh_user = users_repo.get_by_id(uid)
    if fresh_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    enc_secret = str(fresh_user.get("encrypted_totp_secret") or "")
    rec_json = str(fresh_user.get("recovery_codes_json") or "[]")
    if not enc_secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inicie a configuração do 2FA primeiro em /api/security/totp/setup.",
        )

    clean_code = (payload.code or payload.totp_code or "").strip()
    try:
        secret = wallet_crypto.decrypt_totp_secret(uid, enc_secret)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Chave TOTP pendente inválida. Gere um novo QR Code.",
        ) from exc

    if not totp_manager.verify_code(uid, secret, clean_code):
        audit_repo.record_event(
            event_type="TOTP_ENABLE_FAILED",
            actor_user_id=uid,
            ip_address=ctx.ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Código TOTP de 6 dígitos inválido. Verifique o relógio do autenticador.",
        )

    users_repo.save_user_totp(
        user_id=uid,
        encrypted_secret=enc_secret,
        recovery_codes_json=rec_json,
        enabled=True,
    )
    audit_repo.record_event(
        event_type="TOTP_ENABLED",
        actor_user_id=uid,
        ip_address=ctx.ip_address,
    )
    return {"success": True, "totp_enabled": True}


@router.post("/totp/disable")
def totp_disable_endpoint(
    request: Request,
    ctx: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)
    uid = int(ctx.user["id"])

    users_repo.save_user_totp(
        user_id=uid,
        encrypted_secret="",
        recovery_codes_json="[]",
        enabled=False,
    )
    audit_repo.record_event(
        event_type="TOTP_DISABLED",
        actor_user_id=uid,
        ip_address=ctx.ip_address,
    )
    return {"success": True, "totp_enabled": False}


@router.post("/password/change")
def change_password_endpoint(
    payload: PasswordChangePayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)
    uid = int(ctx.user["id"])

    if payload.confirm_password is not None and payload.new_password != payload.confirm_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A confirmação da nova senha não coincide.",
        )

    cur_ok = password_security.verify_password(
        payload.current_password,
        str(ctx.user.get("password_hash", "")),
        str(ctx.user.get("salt", "")),
    )
    if not cur_ok:
        audit_repo.record_event(
            event_type="PASSWORD_CHANGE_FAILED_CURRENT",
            actor_user_id=uid,
            ip_address=ctx.ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha atual incorreta.",
        )

    if bool(ctx.user.get("totp_enabled", 0)):
        step_tok = (request.headers.get("X-Step-Up-Token") or "").strip()
        has_valid_step_up = step_up_manager.verify_token(
            step_tok,
            uid,
            str(ctx.session.get("token_sha256", "")),
        )
        if not has_valid_step_up:
            totp_code = (payload.totp_code or "").strip()
            try:
                secret = wallet_crypto.decrypt_totp_secret(
                    uid,
                    str(ctx.user.get("encrypted_totp_secret", "")),
                )
            except Exception:
                secret = ""
            if not secret or not totp_manager.verify_code(uid, secret, totp_code):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Código 2FA TOTP obrigatório para alterar a senha.",
                )

    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A nova senha deve ser diferente da senha atual.",
        )

    ok_policy, msg_policy = password_security.validate_password_policy(
        payload.new_password,
    )
    if not ok_policy:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg_policy)

    users_repo.complete_first_login_password(uid, payload.new_password)
    revoked = users_repo.revoke_all_user_sessions(
        uid,
        keep_token_hash=str(ctx.session.get("token_sha256", "")),
    )
    step_up_manager.revoke_for_user(uid)

    audit_repo.record_event(
        event_type="PASSWORD_CHANGED",
        actor_user_id=uid,
        ip_address=ctx.ip_address,
        details={"other_sessions_revoked": revoked},
    )
    return {
        "success": True,
        "revoked_other_sessions": revoked,
        "message": "Senha atualizada com sucesso.",
    }


@router.post("/step-up")
def step_up_endpoint(
    payload: StepUpPayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)
    uid = int(ctx.user["id"])
    lim_key = f"stepup:{uid}:{ctx.ip_address}"

    locked, retry_s = step_up_limiter.check(lim_key)
    if locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Confirmação Step-Up bloqueada temporariamente. Aguarde {retry_s}s.",
        )

    pwd_ok = password_security.verify_password(
        payload.password,
        str(ctx.user.get("password_hash", "")),
        str(ctx.user.get("salt", "")),
    )
    if not pwd_ok:
        step_up_limiter.hit(lim_key)
        audit_repo.record_event(
            event_type="STEP_UP_FAILED_PASSWORD",
            actor_user_id=uid,
            ip_address=ctx.ip_address,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Senha incorreta na confirmação Step-Up.",
        )

    if bool(ctx.user.get("totp_enabled", 0)):
        totp_code = (payload.totp_code or "").strip()
        rec_code = (payload.recovery_code or "").strip()
        verified_2fa = False
        if totp_code:
            try:
                secret = wallet_crypto.decrypt_totp_secret(
                    uid,
                    str(ctx.user.get("encrypted_totp_secret", "")),
                )
            except Exception:
                secret = ""
            verified_2fa = bool(secret and totp_manager.verify_code(uid, secret, totp_code))
        elif rec_code:
            matched, updated_json = totp_manager.consume_recovery_code(
                rec_code,
                str(ctx.user.get("recovery_codes_json") or "[]"),
            )
            if matched:
                users_repo.update_recovery_codes(uid, updated_json)
                verified_2fa = True

        if not verified_2fa:
            step_up_limiter.hit(lim_key)
            audit_repo.record_event(
                event_type="STEP_UP_FAILED_2FA",
                actor_user_id=uid,
                ip_address=ctx.ip_address,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Código 2FA obrigatório ou inválido na confirmação Step-Up.",
            )

    step_up_limiter.reset(lim_key)
    grant = step_up_manager.issue_token(
        user_id=uid,
        session_token_hash=str(ctx.session.get("token_sha256", "")),
    )
    audit_repo.record_event(
        event_type="STEP_UP_GRANTED",
        actor_user_id=uid,
        ip_address=ctx.ip_address,
        details={"expires_in": grant["expires_in"]},
    )
    return {
        "success": True,
        "step_up_token": grant["step_up_token"],
        "expires_in": grant["expires_in"],
    }


@router.get("/sessions")
def list_sessions_endpoint(
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    sessions = UsersRepository(db).list_user_sessions(
        user_id=int(ctx.user["id"]),
        current_token_hash=str(ctx.session.get("token_sha256", "")),
    )
    return {"success": True, "sessions": sessions}


@router.post("/sessions/revoke-others")
def revoke_other_sessions_endpoint(
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    uid = int(ctx.user["id"])
    revoked = UsersRepository(db).revoke_all_user_sessions(
        user_id=uid,
        keep_token_hash=str(ctx.session.get("token_sha256", "")),
    )
    AuditRepository(db).record_event(
        event_type="OTHER_SESSIONS_REVOKED",
        actor_user_id=uid,
        ip_address=ctx.ip_address,
        details={"revoked_count": revoked},
    )
    return {"success": True, "revoked_count": revoked}
