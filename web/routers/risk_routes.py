from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from config.settings import settings
from storage.audit_repo import AuditRepository
from storage.wallets_repo import WalletsRepository
from web.dependencies import (
    AuthContext,
    get_current_context,
    get_db,
    resolve_active_wallet,
)

router = APIRouter(prefix="/api/risk", tags=["risk"])


class RiskSettingsUpdatePayload(BaseModel):
    wallet_id: int | None = None
    stop_loss_pct: float | None = Field(default=None, ge=0.005, le=0.50)
    trailing_trigger_pct: float | None = Field(default=None, ge=0.005, le=0.30)
    trailing_distance_pct: float | None = Field(default=None, ge=0.003, le=0.20)
    protect_20k_exemption: bool | None = None
    avoid_day_trade_tax: bool | None = None
    telegram_chat_id: str | None = Field(default=None, max_length=48)


def _format_risk_settings(w: dict[str, Any]) -> dict[str, Any]:
    return {
        "wallet_id": int(w["id"]),
        "wallet_name": str(w.get("name", "")),
        "stop_loss_pct": float(w["stop_loss_pct"]),
        "trailing_trigger_pct": float(w["trailing_trigger_pct"]),
        "trailing_distance_pct": float(w["trailing_distance_pct"]),
        "protect_20k_exemption": bool(w["protect_20k_exemption"]),
        "avoid_day_trade_tax": bool(w["avoid_day_trade_tax"]),
        "automation_enabled": bool(w["automation_enabled"]),
        "operational_mode": str(w["operational_mode"]),
        "telegram_chat_id": str(w.get("telegram_chat_id", "")),
        "max_concentration": settings.MAX_CONCENTRATION,
        "max_sector_exposure": settings.MAX_SECTOR_EXPOSURE,
        "max_daily_loss_percent": settings.MAX_DAILY_LOSS_PERCENT,
        "max_risk_per_trade_percent": settings.MAX_RISK_PER_TRADE_PERCENT,
        "b3_swing_exemption_limit": settings.B3_SWING_EXEMPTION_LIMIT,
        "b3_swing_exemption_guard_buffer": settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
    }


@router.get("/settings")
def get_risk_settings(
    request: Request,
    wallet_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, wallet_id, db=db)
    assert wallet is not None
    return {
        "success": True,
        "wallet_id": int(wallet["id"]),
        "settings": _format_risk_settings(wallet),
    }


@router.post("/settings")
def update_risk_settings(
    payload: RiskSettingsUpdatePayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, payload.wallet_id, db=db)
    assert wallet is not None
    uid = int(ctx.user["id"])
    wid = int(wallet["id"])

    updated = WalletsRepository(db).update_risk_and_preferences(
        user_id=uid,
        wallet_id=wid,
        stop_loss_pct=payload.stop_loss_pct,
        trailing_trigger_pct=payload.trailing_trigger_pct,
        trailing_distance_pct=payload.trailing_distance_pct,
        protect_20k_exemption=payload.protect_20k_exemption,
        avoid_day_trade_tax=payload.avoid_day_trade_tax,
        telegram_chat_id=payload.telegram_chat_id,
    )
    AuditRepository(db).record_event(
        event_type="RISK_SETTINGS_UPDATED",
        actor_user_id=uid,
        target_wallet_id=wid,
        ip_address=ctx.ip_address,
        details={
            "stop_loss_pct": float(updated["stop_loss_pct"]),
            "trailing_trigger_pct": float(updated["trailing_trigger_pct"]),
            "trailing_distance_pct": float(updated["trailing_distance_pct"]),
            "protect_20k_exemption": bool(updated["protect_20k_exemption"]),
            "avoid_day_trade_tax": bool(updated["avoid_day_trade_tax"]),
        },
    )
    return {
        "success": True,
        "wallet": updated,
        "settings": _format_risk_settings(updated),
    }
