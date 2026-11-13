from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from config.settings import settings
from storage.audit_repo import AuditRepository
from storage.capital_repo import CapitalRepository
from storage.wallets_repo import WalletsRepository
from wallets.capital_service import WalletCapitalService
from wallets.runtime_registry import resolve_connector_for_wallet
from web.dependencies import (
    AuthContext,
    get_current_context,
    get_db,
    require_step_up,
    resolve_active_wallet,
)

router = APIRouter(prefix="/api/capital", tags=["capital"])


class AiCeilingPayload(BaseModel):
    wallet_id: int | None = None
    ceiling_brl: float | None = Field(default=None, ge=0.0)
    ceiling: float | None = Field(default=None, ge=0.0)
    amount: float | None = Field(default=None, ge=0.0)


class VaultTransferPayload(BaseModel):
    wallet_id: int | None = None
    amount: float = Field(..., gt=0.0)


class WithdrawRequestPayload(BaseModel):
    wallet_id: int | None = None
    amount: float = Field(..., gt=0.0)
    mode: str = Field(default="instant", max_length=24)


class WithdrawCancelPayload(BaseModel):
    wallet_id: int | None = None
    goal_id: int | None = None


class SyncBrokerPayload(BaseModel):
    wallet_id: int | None = None


def _risk_params(wallet: dict[str, Any]) -> dict[str, Any]:
    return {
        "stop_loss_pct": float(wallet["stop_loss_pct"]),
        "trailing_trigger_pct": float(wallet["trailing_trigger_pct"]),
        "trailing_distance_pct": float(wallet["trailing_distance_pct"]),
        "protect_20k_exemption": bool(wallet["protect_20k_exemption"]),
        "avoid_day_trade_tax": bool(wallet["avoid_day_trade_tax"]),
    }


@router.get("/summary")
def get_capital_summary_endpoint(
    request: Request,
    wallet_id: int | None = Query(default=None),
    probe_broker: bool = Query(default=False),
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    connector = None
    if probe_broker:
        try:
            connector = resolve_connector_for_wallet(wallet, WalletsRepository(db))
        except Exception:
            connector = None

    service = WalletCapitalService(db)
    summary = service.get_capital_summary(uid, wid, connector=connector)
    summary["wallet_name"] = str(wallet.get("name", ""))
    summary["connector_type"] = str(wallet.get("connector_type", ""))
    summary["is_simulation"] = settings.IS_SIMULATION

    return {
        "success": True,
        "capital": summary,
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
        "risk_params": _risk_params(wallet),
    }


@router.post("/ai-ceiling")
def set_ai_ceiling_endpoint(
    payload: AiCeilingPayload,
    request: Request,
    ctx: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, payload.wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    target = payload.ceiling_brl
    if target is None:
        target = payload.ceiling
    if target is None:
        target = payload.amount
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Informe o valor do teto operacional da IA.",
        )

    service = WalletCapitalService(db)
    try:
        summary = service.set_ai_allocation_ceiling(uid, wid, float(target))
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "success": True,
        "capital": summary,
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
    }


@router.post("/vault/deposit")
def deposit_to_vault_endpoint(
    payload: VaultTransferPayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, payload.wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    service = WalletCapitalService(db)
    try:
        summary = service.deposit_to_vault(uid, wid, payload.amount)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "success": True,
        "capital": summary,
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
    }


@router.post("/vault/release")
def release_from_vault_endpoint(
    payload: VaultTransferPayload,
    request: Request,
    ctx: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, payload.wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    service = WalletCapitalService(db)
    try:
        summary = service.release_from_vault(uid, wid, payload.amount)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "success": True,
        "capital": summary,
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
    }


@router.post("/withdraw")
def withdraw_capital_endpoint(
    payload: WithdrawRequestPayload,
    request: Request,
    ctx: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, payload.wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    wallets_repo = WalletsRepository(db)
    connector = resolve_connector_for_wallet(wallet, wallets_repo)
    service = WalletCapitalService(db)
    try:
        result = service.request_withdrawal_liquidation(
            user_id=uid,
            wallet_id=wid,
            amount=payload.amount,
            mode=payload.mode,
            connector=connector,
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {
        "success": True,
        **result,
        "capital": result.get("summary"),
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
    }


@router.post("/withdraw/cancel")
def cancel_withdraw_goal_endpoint(
    request: Request,
    payload: WithdrawCancelPayload | None = None,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wid_arg = payload.wallet_id if payload else None
    wallet = resolve_active_wallet(ctx, wid_arg, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    goal_id = payload.goal_id if payload else None
    if goal_id is None:
        active_goal = CapitalRepository(db).get_active_withdrawal_goal(uid, wid)
        if active_goal is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Nenhuma meta de resgate ativa encontrada nesta carteira.",
            )
        goal_id = int(active_goal["id"])

    service = WalletCapitalService(db)
    service.cancel_withdrawal_goal(uid, wid, int(goal_id))
    return {
        "success": True,
        "cancelled_goal_id": int(goal_id),
        "capital": service.get_capital_summary(uid, wid),
    }


@router.post("/sync-broker")
def sync_broker_capital_endpoint(
    request: Request,
    payload: SyncBrokerPayload | None = None,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wid_arg = payload.wallet_id if payload else None
    wallet = resolve_active_wallet(ctx, wid_arg, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    wallets_repo = WalletsRepository(db)
    connector = resolve_connector_for_wallet(wallet, wallets_repo)
    service = WalletCapitalService(db)

    try:
        sync_res = service.sync_with_broker(uid, wid, connector)
    except Exception as exc:
        sync_res = {
            "connected": False,
            "synced": False,
            "message": f"Falha de comunicação com a corretora: {exc}",
        }

    AuditRepository(db).record_event(
        event_type="CAPITAL_BROKER_SYNC",
        actor_user_id=uid,
        target_wallet_id=wid,
        ip_address=ctx.ip_address,
        details={
            "connected": bool(sync_res.get("connected", False)),
            "synced": bool(sync_res.get("synced", False)),
        },
    )
    summary = service.get_capital_summary(uid, wid, connector=connector)
    return {
        "success": True,
        "sync": sync_res,
        "capital": summary,
    }
