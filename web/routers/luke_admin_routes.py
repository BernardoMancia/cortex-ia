from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from brokers.registry import is_connector_ready, list_available_connectors
from security.password import password_security
from storage.audit_repo import AuditRepository
from storage.users_repo import UsersRepository
from storage.wallets_repo import WalletsRepository
from web.dependencies import (
    AuthContext,
    get_db,
    require_luke_admin,
    require_step_up,
)

router = APIRouter(
    prefix="/api/admin",
    tags=["luke_admin"],
    dependencies=[Depends(require_luke_admin)],
)


class AdminCreateUserPayload(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    temporary_password: str | None = Field(default=None, max_length=128)
    invite_hours: int | None = Field(default=None, ge=1, le=168)
    create_initial_wallet: bool = False
    wallet_name: str | None = Field(default=None, max_length=64)
    connector_type: str | None = Field(default=None, max_length=32)
    initial_capital: float = Field(default=0.0, ge=0.0)


class AdminToggleActivePayload(BaseModel):
    is_active: bool | None = None


class AdminResetPasswordPayload(BaseModel):
    temporary_password: str | None = Field(default=None, max_length=128)
    invite_hours: int | None = Field(default=None, ge=1, le=168)


@router.get("/users")
def admin_list_users(
    request: Request,
    ctx: AuthContext = Depends(require_luke_admin),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users = UsersRepository(db).list_all_users_for_luke()
    return {"success": True, "users": users}


@router.post("/users")
def admin_create_user(
    payload: AdminCreateUserPayload,
    request: Request,
    ctx: AuthContext = Depends(require_luke_admin),
    _step_up: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    wallets_repo = WalletsRepository(db)
    audit_repo = AuditRepository(db)

    raw_temp = (payload.temporary_password or "").strip()
    if raw_temp:
        ok_pwd, msg_pwd = password_security.validate_password_policy(raw_temp)
        if not ok_pwd:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg_pwd)
        temp_password = raw_temp
    else:
        temp_password = password_security.generate_temporary_password(14)

    try:
        created = users_repo.create_standard_user(
            username=payload.username,
            temporary_password=temp_password,
            invite_hours=payload.invite_hours,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    initial_wallet: dict[str, Any] | None = None
    if payload.create_initial_wallet or payload.connector_type:
        ctype = (payload.connector_type or "clear_mt5").strip().lower()
        catalog_types = {c["connector_type"] for c in list_available_connectors()}
        if ctype not in catalog_types:
            ctype = "clear_mt5"
        w_name = (payload.wallet_name or f"Carteira {created['username']}").strip()
        initial_wallet = wallets_repo.create_wallet(
            user_id=int(created["id"]),
            name=w_name,
            connector_type=ctype,
            connector_config={},
            initial_capital=payload.initial_capital,
            is_ready_connector=False,
            make_default=True,
        )

    audit_repo.record_event(
        event_type="ADMIN_USER_CREATED",
        actor_user_id=int(ctx.user["id"]),
        ip_address=ctx.ip_address,
        details={
            "created_user_id": int(created["id"]),
            "username": str(created["username"]),
            "invite_expires_at": created.get("invite_expires_at"),
            "initial_wallet_created": initial_wallet is not None,
        },
    )
    return {
        "success": True,
        "user": {
            "id": int(created["id"]),
            "username": str(created["username"]),
            "is_active": bool(created.get("is_active", 1)),
            "must_change_password": bool(created.get("must_change_password", 1)),
            "invite_expires_at": created.get("invite_expires_at"),
        },
        "temporary_password": temp_password,
        "invite_expires_at": created.get("invite_expires_at"),
        "initial_wallet": initial_wallet,
    }


@router.post("/users/{target_user_id}/toggle-active")
def admin_toggle_user_active(
    target_user_id: int,
    request: Request,
    payload: AdminToggleActivePayload | None = None,
    ctx: AuthContext = Depends(require_luke_admin),
    _step_up: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)

    target = users_repo.get_by_id(target_user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    desired = (
        (not bool(target.get("is_active", 1)))
        if (payload is None or payload.is_active is None)
        else bool(payload.is_active)
    )
    try:
        users_repo.set_user_active_status(target_user_id, desired)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit_repo.record_event(
        event_type="ADMIN_USER_ACTIVE_TOGGLED",
        actor_user_id=int(ctx.user["id"]),
        ip_address=ctx.ip_address,
        details={
            "target_user_id": target_user_id,
            "username": str(target.get("username", "")),
            "is_active": desired,
        },
    )
    return {
        "success": True,
        "user_id": target_user_id,
        "is_active": desired,
    }


@router.post("/users/{target_user_id}/reset-password")
def admin_reset_user_password(
    target_user_id: int,
    request: Request,
    payload: AdminResetPasswordPayload | None = None,
    ctx: AuthContext = Depends(require_luke_admin),
    _step_up: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)

    target = users_repo.get_by_id(target_user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    raw_temp = (payload.temporary_password or "").strip() if payload else ""
    if raw_temp:
        ok_pwd, msg_pwd = password_security.validate_password_policy(raw_temp)
        if not ok_pwd:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg_pwd)
        temp_pwd = raw_temp
    else:
        temp_pwd = password_security.generate_temporary_password(14)

    inv_hours = payload.invite_hours if payload else None
    try:
        invite_exp = users_repo.admin_reset_user_password(
            target_user_id=target_user_id,
            temporary_password=temp_pwd,
            invite_hours=inv_hours,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    audit_repo.record_event(
        event_type="ADMIN_USER_PASSWORD_RESET",
        actor_user_id=int(ctx.user["id"]),
        ip_address=ctx.ip_address,
        details={
            "target_user_id": target_user_id,
            "username": str(target.get("username", "")),
            "invite_expires_at": invite_exp,
        },
    )
    return {
        "success": True,
        "user_id": target_user_id,
        "username": str(target.get("username", "")),
        "temporary_password": temp_pwd,
        "invite_expires_at": invite_exp,
    }


@router.post("/users/{target_user_id}/reset-2fa")
def admin_reset_user_2fa(
    target_user_id: int,
    request: Request,
    ctx: AuthContext = Depends(require_luke_admin),
    _step_up: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)

    target = users_repo.get_by_id(target_user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    users_repo.save_user_totp(
        user_id=target_user_id,
        encrypted_secret="",
        recovery_codes_json="[]",
        enabled=False,
    )
    if target_user_id != int(ctx.user["id"]):
        users_repo.revoke_all_user_sessions(target_user_id)

    audit_repo.record_event(
        event_type="ADMIN_USER_2FA_RESET",
        actor_user_id=int(ctx.user["id"]),
        ip_address=ctx.ip_address,
        details={
            "target_user_id": target_user_id,
            "username": str(target.get("username", "")),
        },
    )
    return {
        "success": True,
        "user_id": target_user_id,
        "totp_enabled": False,
    }


@router.get("/audit/verify")
def admin_verify_audit_chain(
    request: Request,
    ctx: AuthContext = Depends(require_luke_admin),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    result = AuditRepository(db).verify_integrity()
    return {
        "success": True,
        "integrity": result,
        **result,
    }


@router.get("/audit/events")
def admin_list_audit_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    wallet_id: int | None = Query(default=None),
    actor_user_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(require_luke_admin),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    events = AuditRepository(db).list_events(
        limit=limit,
        wallet_id=wallet_id,
        actor_user_id=actor_user_id,
    )
    return {"success": True, "events": events}
