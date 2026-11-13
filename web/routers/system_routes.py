from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from config.settings import settings
from domain.b3_rules import get_brt_now
from domain.operational_guard import OperationalGuard, parse_iso_brt
from engine.custody_sync import CustodyReconciler
from engine.panic_service import PanicService
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection
from storage.state_repo import StateRepository
from storage.wallets_repo import WalletsRepository
from web.dependencies import (
    AuthContext,
    get_current_context,
    get_db,
    require_luke_admin,
    require_step_up,
    resolve_active_wallet,
    sanitize_wallet,
)

router = APIRouter(prefix="/api/system", tags=["system"])


class MaintenanceUnlockPayload(BaseModel):
    force: bool = False


class MaintenanceEnterPayload(BaseModel):
    reason: str = Field(
        default="Modo Manutenção ativado manualmente pelo administrador Luke.",
        max_length=240,
    )


class PanicActivatePayload(BaseModel):
    scope: str = Field(default="wallet", max_length=16)
    wallet_id: int | None = None
    duration_preset: str = Field(default="indefinite", max_length=24)
    custom_minutes: int | None = Field(default=None, ge=1, le=43200)
    reason: str = Field(default="Botão de Pânico acionado manualmente.", max_length=240)
    liquidate_positions: bool = False


class PanicClearPayload(BaseModel):
    scope: str = Field(default="wallet", max_length=16)
    wallet_id: int | None = None
    restore_mode: str = Field(default="ACTIVE", max_length=24)


def _get_panic_service(request: Request, db: DatabaseConnection) -> PanicService:
    svc = getattr(request.app.state, "panic_service", None)
    if isinstance(svc, PanicService):
        return svc
    orch = getattr(request.app.state, "orchestrator", None)
    if orch is not None and isinstance(getattr(orch, "panic_service", None), PanicService):
        return orch.panic_service
    created = PanicService(db=db)
    request.app.state.panic_service = created
    return created


def _get_custody_reconciler(request: Request, db: DatabaseConnection) -> CustodyReconciler:
    rec = getattr(request.app.state, "custody_reconciler", None)
    if isinstance(rec, CustodyReconciler):
        return rec
    orch = getattr(request.app.state, "orchestrator", None)
    if orch is not None and isinstance(getattr(orch, "custody_reconciler", None), CustodyReconciler):
        return orch.custody_reconciler
    created = CustodyReconciler(db=db)
    request.app.state.custody_reconciler = created
    return created


@router.get("/state")
def get_system_state_endpoint(
    request: Request,
    wallet_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    state_repo = StateRepository(db)
    wallets_repo = WalletsRepository(db)

    global_state = state_repo.get_state(auto_expire_panic=True)
    now = get_brt_now()
    global_gate = OperationalGuard.evaluate_global(global_state, now=now)

    wallet = resolve_active_wallet(ctx, wallet_id, db=db, require_exists=False)
    wallet_gate_dict: dict[str, Any] | None = None
    if wallet is not None:
        if str(wallet.get("operational_mode") or "").upper() == "PANIC":
            until_dt = parse_iso_brt(wallet.get("panic_until"))
            if until_dt is not None and now >= until_dt:
                wallet = wallets_repo.clear_wallet_panic(
                    int(ctx.user["id"]),
                    int(wallet["id"]),
                    restore_mode="ACTIVE",
                )
        w_gate = OperationalGuard.evaluate_wallet(global_state, wallet, now=now)
        wallet_gate_dict = {
            "can_execute_orders": w_gate.can_execute_orders,
            "can_observe_signals": w_gate.can_observe_signals,
            "effective_mode": w_gate.effective_mode,
            "block_reason": w_gate.block_reason,
            "remaining_seconds": w_gate.remaining_seconds,
        }

    return {
        "success": True,
        "environment": settings.ENVIRONMENT_ID,
        "environment_label": settings.ENVIRONMENT_LABEL,
        "is_production": settings.IS_PRODUCTION,
        "is_simulation": settings.IS_SIMULATION,
        "global_state": global_state,
        "global_gate": {
            "can_execute_orders": global_gate.can_execute_orders,
            "can_observe_signals": global_gate.can_observe_signals,
            "effective_mode": global_gate.effective_mode,
            "block_reason": global_gate.block_reason,
            "remaining_seconds": global_gate.remaining_seconds,
        },
        "wallet_gate": wallet_gate_dict,
        "active_wallet": sanitize_wallet(wallet),
    }


@router.get("/preflight")
def system_preflight_endpoint(
    request: Request,
    ctx: AuthContext = Depends(require_luke_admin),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    reconciler = _get_custody_reconciler(request, db)
    report = reconciler.run_preflight_check().to_dict()

    AuditRepository(db).record_event(
        event_type="PREFLIGHT_CHECK_RUN",
        actor_user_id=int(ctx.user["id"]),
        ip_address=ctx.ip_address,
        details={"ready": report["ready"], "issues_count": len(report["issues"])},
    )
    return {
        "success": True,
        "preflight": report,
        **report,
    }


@router.post("/maintenance/unlock")
def maintenance_unlock_endpoint(
    request: Request,
    payload: MaintenanceUnlockPayload | None = None,
    ctx: AuthContext = Depends(require_luke_admin),
    _step_up: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    force = bool(payload.force) if payload is not None else False
    reconciler = _get_custody_reconciler(request, db)
    report = reconciler.run_preflight_check().to_dict()

    if not report["ready"] and not force:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PREFLIGHT_FAILED",
                "message": "Pre-Flight Check reprovado. Corrija as pendências ou autorize liberação forçada.",
                "preflight": report,
            },
        )

    new_state = StateRepository(db).unlock_maintenance(
        actor_username=str(ctx.user["username"]),
        actor_user_id=int(ctx.user["id"]),
        ip_address=ctx.ip_address,
    )
    if force and not report["ready"]:
        AuditRepository(db).record_event(
            event_type="MAINTENANCE_FORCE_UNLOCKED",
            actor_user_id=int(ctx.user["id"]),
            ip_address=ctx.ip_address,
            details={"issues": report["issues"]},
        )

    orch = getattr(request.app.state, "orchestrator", None)
    if orch is not None and hasattr(orch, "trigger_immediate_cycle"):
        try:
            orch.trigger_immediate_cycle()
        except Exception:
            pass

    return {
        "success": True,
        "global_state": new_state,
        "preflight": report,
    }


@router.post("/maintenance/enter")
def maintenance_enter_endpoint(
    request: Request,
    payload: MaintenanceEnterPayload | None = None,
    ctx: AuthContext = Depends(require_luke_admin),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    reason = (
        payload.reason
        if payload is not None
        else "Modo Manutenção ativado manualmente pelo administrador Luke."
    )
    new_state = StateRepository(db).enter_maintenance(
        reason=reason,
        actor_username=str(ctx.user["username"]),
        actor_user_id=int(ctx.user["id"]),
        ip_address=ctx.ip_address,
    )
    return {
        "success": True,
        "global_state": new_state,
    }


@router.post("/panic/activate")
def panic_activate_endpoint(
    payload: PanicActivatePayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    scope = (payload.scope or "wallet").strip().lower()
    if scope not in ("wallet", "global"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Escopo inválido. Escolha 'wallet' ou 'global'.",
        )

    if scope == "global":
        require_luke_admin(request, ctx)

    if payload.liquidate_positions:
        require_step_up(request, ctx)

    panic_service = _get_panic_service(request, db)
    uid = int(ctx.user["id"])
    username = str(ctx.user["username"])

    if scope == "global":
        res = panic_service.activate_global_panic(
            actor_user_id=uid,
            actor_username=username,
            duration_preset=payload.duration_preset,
            custom_minutes=payload.custom_minutes,
            reason=payload.reason,
            liquidate_positions=payload.liquidate_positions,
            ip_address=ctx.ip_address,
        )
        return {
            "success": True,
            "scope": "global",
            **res,
        }

    wallet = resolve_active_wallet(ctx, payload.wallet_id, db=db)
    assert wallet is not None
    res = panic_service.activate_wallet_panic(
        user_id=uid,
        wallet_id=int(wallet["id"]),
        actor_username=username,
        duration_preset=payload.duration_preset,
        custom_minutes=payload.custom_minutes,
        reason=payload.reason,
        liquidate_positions=payload.liquidate_positions,
        ip_address=ctx.ip_address,
    )
    return {
        "success": True,
        "scope": "wallet",
        **res,
    }


@router.post("/panic/clear")
def panic_clear_endpoint(
    payload: PanicClearPayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    scope = (payload.scope or "wallet").strip().lower()
    if scope not in ("wallet", "global"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Escopo inválido. Escolha 'wallet' ou 'global'.",
        )

    if scope == "global":
        require_luke_admin(request, ctx)

    if settings.IS_PRODUCTION:
        require_step_up(request, ctx)

    panic_service = _get_panic_service(request, db)
    uid = int(ctx.user["id"])
    username = str(ctx.user["username"])

    if scope == "global":
        res = panic_service.clear_global_panic(
            actor_user_id=uid,
            actor_username=username,
            ip_address=ctx.ip_address,
        )
        return {
            "success": True,
            "scope": "global",
            **res,
        }

    wallet = resolve_active_wallet(ctx, payload.wallet_id, db=db)
    assert wallet is not None
    res = panic_service.clear_wallet_panic(
        user_id=uid,
        wallet_id=int(wallet["id"]),
        actor_username=username,
        restore_mode=payload.restore_mode,
        ip_address=ctx.ip_address,
    )
    return {
        "success": True,
        "scope": "wallet",
        **res,
    }
