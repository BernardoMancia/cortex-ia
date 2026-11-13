from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from brokers.registry import is_connector_ready, list_available_connectors
from config.settings import settings
from domain.operational_guard import OperationalGuard
from storage.audit_repo import AuditRepository
from storage.state_repo import StateRepository
from storage.users_repo import UsersRepository
from storage.wallets_repo import WalletsRepository
from web.dependencies import (
    AuthContext,
    get_current_context,
    get_db,
    require_step_up,
    resolve_active_wallet,
    sanitize_wallet,
)

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


class CreateWalletPayload(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    connector_type: str = Field(..., min_length=2, max_length=32)
    connector_config: dict[str, Any] = Field(default_factory=dict)
    initial_capital: float = Field(default=0.0, ge=0.0)
    make_default: bool = False


class WalletModePayload(BaseModel):
    operational_mode: str | None = Field(default=None, max_length=32)
    automation_enabled: bool | None = None


class WalletCredentialsPayload(BaseModel):
    connector_config: dict[str, Any] = Field(default_factory=dict)


def _clean_connector_config(raw: dict[str, Any], is_luke_admin: bool) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for k, v in (raw or {}).items():
        key = str(k).strip()[:64]
        if not key:
            continue
        if key == "use_server_clear_bridge" and not is_luke_admin:
            continue
        if isinstance(v, (bool, int, float)):
            cleaned[key] = v
        elif isinstance(v, str):
            cleaned[key] = v.strip()[:512]
    return cleaned


@router.get("/catalog")
def get_wallets_catalog(
    _ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    return {
        "success": True,
        "connectors": list_available_connectors(),
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
    }


@router.get("")
def list_user_wallets(
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    uid = int(ctx.user["id"])
    wallets = WalletsRepository(db).list_for_user(uid)
    global_state = StateRepository(db).get_state(auto_expire_panic=True)
    active_wid = int(ctx.active_wallet["id"]) if ctx.active_wallet is not None else None

    enriched: list[dict[str, Any]] = []
    for w in wallets:
        gate = OperationalGuard.evaluate_wallet(global_state, w)
        item = dict(w)
        item["is_selected"] = active_wid == int(w["id"])
        item["connector_ready"] = is_connector_ready(str(w.get("connector_type", "")))
        item["gate"] = {
            "can_execute_orders": gate.can_execute_orders,
            "can_observe_signals": gate.can_observe_signals,
            "effective_mode": gate.effective_mode,
            "block_reason": gate.block_reason,
            "remaining_seconds": gate.remaining_seconds,
        }
        enriched.append(item)

    return {
        "success": True,
        "wallets": enriched,
        "active_wallet_id": active_wid,
    }


@router.post("")
def create_user_wallet(
    payload: CreateWalletPayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    uid = int(ctx.user["id"])
    ctype = payload.connector_type.strip().lower()

    catalog = list_available_connectors()
    known_types = {str(c["connector_type"]).lower() for c in catalog}
    if ctype not in known_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conector de corretora inválido ou não suportado no catálogo.",
        )

    clean_cfg = _clean_connector_config(payload.connector_config, ctx.is_luke_admin)
    ready_in_catalog = is_connector_ready(ctype)
    has_creds = bool(
        ctx.is_luke_admin
        or clean_cfg.get("bridge_url")
        or clean_cfg.get("mt5_login")
        or settings.IS_SIMULATION
    )

    wallets_repo = WalletsRepository(db)
    try:
        created = wallets_repo.create_wallet(
            user_id=uid,
            name=payload.name,
            connector_type=ctype,
            connector_config=clean_cfg,
            initial_capital=payload.initial_capital,
            is_ready_connector=bool(ready_in_catalog and has_creds),
            make_default=payload.make_default,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.make_default or ctx.active_wallet is None:
        UsersRepository(db).set_session_active_wallet(
            str(ctx.session.get("token_sha256", "")),
            uid,
            int(created["id"]),
        )

    AuditRepository(db).record_event(
        event_type="WALLET_CREATED",
        actor_user_id=uid,
        target_wallet_id=int(created["id"]),
        ip_address=ctx.ip_address,
        details={
            "name": created["name"],
            "connector_type": created["connector_type"],
            "operational_mode": created["operational_mode"],
        },
    )
    return {"success": True, "wallet": created}


@router.post("/{wallet_id}/select")
def select_active_wallet(
    wallet_id: int,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    UsersRepository(db).set_session_active_wallet(
        str(ctx.session.get("token_sha256", "")),
        uid,
        wid,
    )
    AuditRepository(db).record_event(
        event_type="WALLET_SELECTED",
        actor_user_id=uid,
        target_wallet_id=wid,
        ip_address=ctx.ip_address,
    )
    return {
        "success": True,
        "active_wallet_id": wid,
        "wallet": sanitize_wallet(wallet),
    }


@router.post("/{wallet_id}/mode")
def update_wallet_mode(
    wallet_id: int,
    payload: WalletModePayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    current_mode = str(wallet.get("operational_mode") or "").upper()
    if current_mode == "PANIC":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Carteira em Modo Pânico. Utilize /api/system/panic/clear para retomar com segurança.",
        )

    ctype = str(wallet.get("connector_type") or "")
    ready = is_connector_ready(ctype)
    target_mode: str | None = None
    if payload.operational_mode is not None:
        target_mode = payload.operational_mode.strip().upper()
        if target_mode not in ("ACTIVE", "OBSERVE_ONLY"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Modo operacional inválido. Use ACTIVE ou OBSERVE_ONLY.",
            )
        if target_mode == "ACTIVE" and not ready:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Este conector está em desenvolvimento (WIP) e suporta apenas OBSERVE_ONLY.",
            )

    if payload.automation_enabled is True and not ready:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Automação indisponível para conectores de corretora em desenvolvimento (WIP).",
        )

    updated = WalletsRepository(db).update_risk_and_preferences(
        user_id=uid,
        wallet_id=wid,
        automation_enabled=payload.automation_enabled,
        operational_mode=target_mode,
    )
    AuditRepository(db).record_event(
        event_type="WALLET_MODE_UPDATED",
        actor_user_id=uid,
        target_wallet_id=wid,
        ip_address=ctx.ip_address,
        details={
            "operational_mode": updated["operational_mode"],
            "automation_enabled": bool(updated["automation_enabled"]),
        },
    )
    return {"success": True, "wallet": updated}


@router.post("/{wallet_id}/credentials")
def update_wallet_credentials(
    wallet_id: int,
    payload: WalletCredentialsPayload,
    request: Request,
    ctx: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])
    wallets_repo = WalletsRepository(db)

    incoming = _clean_connector_config(payload.connector_config, ctx.is_luke_admin)
    existing = wallets_repo.decrypt_connector_config(uid, wid)
    merged = dict(existing)
    for k, v in incoming.items():
        if isinstance(v, str) and v == "" and k in merged:
            continue
        merged[k] = v

    wallets_repo.update_connector_config(uid, wid, merged)
    wallets_repo.reset_wallet_error_strikes(uid, wid)

    cur_mode = str(wallet.get("operational_mode") or "").upper()
    if cur_mode in ("PAUSED_UNCONFIGURED", "PAUSED_BROKER_ERROR") and is_connector_ready(
        str(wallet.get("connector_type") or "")
    ):
        wallets_repo.update_risk_and_preferences(
            user_id=uid,
            wallet_id=wid,
            operational_mode="OBSERVE_ONLY",
        )

    AuditRepository(db).record_event(
        event_type="WALLET_CREDENTIALS_UPDATED",
        actor_user_id=uid,
        target_wallet_id=wid,
        ip_address=ctx.ip_address,
        details={"updated_keys": sorted(list(incoming.keys()))},
    )
    refreshed = wallets_repo.get_owned_wallet(uid, wid) or wallet
    return {"success": True, "wallet": sanitize_wallet(refreshed)}
