from __future__ import annotations

import asyncio
from datetime import datetime, time as dt_time, timedelta
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel

from config.settings import settings
from domain.b3_rules import (
    BRT,
    get_ash_wednesday,
    get_brt_now,
    is_b3_business_day,
    normalize_b3_ticker,
)
from domain.b3_tax_guard import B3TaxGuard
from domain.operational_guard import OperationalGuard
from observability.health import SystemHealthMonitor
from observability.logger import (
    get_recent_admin_logs,
    register_log_listener,
    scrub_secrets,
    unregister_log_listener,
)
from quant.knowledge_base import get_guide_payload
from security.session_csrf import is_origin_allowed
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection
from storage.market_hub_repo import MarketHubRepository
from storage.state_repo import StateRepository
from storage.trading_repo import TradingRepository
from storage.users_repo import UsersRepository
from wallets.portfolio_manager import WalletPortfolioManager
from web.dependencies import (
    AuthContext,
    get_current_context,
    get_db,
    require_luke_admin,
    resolve_active_wallet,
    sanitize_wallet,
)

router = APIRouter(tags=["cockpit"])


class PositionProtectPayload(BaseModel):
    do_not_touch: bool | None = None
    wallet_id: int | None = None


def _compute_b3_open_fallback() -> tuple[bool, bool]:
    now = get_brt_now()
    if not is_b3_business_day(now.date()):
        return False, False
    open_t = dt_time(13, 0) if now.date() == get_ash_wednesday(now.year) else dt_time(10, 0)
    close_t = dt_time(17, 55)
    is_open = open_t <= now.time() <= close_t
    if not is_open:
        return False, False
    open_dt = datetime.combine(now.date(), open_t, tzinfo=BRT)
    in_cooldown = (now - open_dt).total_seconds() < (settings.OPENING_COOLDOWN_MINUTES * 60)
    return True, in_cooldown


def _get_hub_snapshot(request: Request) -> Any:
    orch = getattr(request.app.state, "orchestrator", None)
    if orch is None:
        return None
    hub = getattr(orch, "market_hub", None) or getattr(orch, "hub", None)
    if hub is not None and hasattr(hub, "get_latest_snapshot"):
        try:
            return hub.get_latest_snapshot()
        except Exception:
            return None
    return None


@router.get("/api/status")
def get_cockpit_status(
    request: Request,
    wallet_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    uid = int(ctx.user["id"])
    state_repo = StateRepository(db)
    trading_repo = TradingRepository(db)
    market_repo = MarketHubRepository(db)

    global_state = state_repo.get_state(auto_expire_panic=True)
    global_gate = OperationalGuard.evaluate_global(global_state)

    hub_snap = _get_hub_snapshot(request)
    if hub_snap is not None:
        market_open = bool(hub_snap.market_open)
        cooldown_active = bool(hub_snap.opening_cooldown_active)
        bova11_change_pct = round(float(hub_snap.bova11_change_pct), 2)
        regime_bearish_veto = bool(hub_snap.regime_bearish_veto)
        live_quotes = dict(hub_snap.quotes)
    else:
        market_open, cooldown_active = _compute_b3_open_fallback()
        bova11_change_pct = 0.0
        regime_bearish_veto = False
        live_quotes = None

    market_status = "ABERTO" if market_open else "FECHADO"
    wallet = resolve_active_wallet(ctx, wallet_id, db=db, require_exists=False)

    if wallet is not None:
        wid = int(wallet["id"])
        pm = WalletPortfolioManager(db)
        port_snap = pm.get_portfolio_snapshot(uid, wid, quotes=live_quotes)
        w_gate = OperationalGuard.evaluate_wallet(global_state, wallet)
        wallet_gate_dict = {
            "can_execute_orders": w_gate.can_execute_orders,
            "can_observe_signals": w_gate.can_observe_signals,
            "effective_mode": w_gate.effective_mode,
            "block_reason": w_gate.block_reason,
            "remaining_seconds": w_gate.remaining_seconds,
        }
        raw_decisions = trading_repo.list_decisions(uid, wid, limit=25)
        recent_decisions = [
            {**d, "environment": settings.ENVIRONMENT_ID}
            for d in raw_decisions
        ]
        month_sells = trading_repo.get_month_sell_trades(uid, wid)
        tax_summary = B3TaxGuard(
            exemption_limit=settings.B3_SWING_EXEMPTION_LIMIT,
            guard_buffer=settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
        ).summarize_month(month_sells).to_dict()
        trade_stats = trading_repo.get_trade_stats(uid, wid)
        balance_val = (
            float(port_snap["ai_allocated_capital"])
            if settings.IS_PRODUCTION
            else float(port_snap["free_cash"])
        )
        equity_val = float(port_snap["total_equity"])
        positions_list = port_snap["positions"]
    else:
        port_snap = {
            "wallet_id": None,
            "initial_capital": 0.0,
            "free_cash": 0.0,
            "vault_reserve_cash": 0.0,
            "ai_allocated_capital": 0.0,
            "allocated_capital": 0.0,
            "invested_value": 0.0,
            "buying_power": 0.0,
            "operational_equity": 0.0,
            "total_equity": 0.0,
            "unrealized_pnl": 0.0,
            "realized_pnl_total": 0.0,
            "pnl_percent": 0.0,
            "daily_pnl_percent": 0.0,
            "win_rate": 0.0,
            "total_trades": 0,
            "positions": [],
        }
        wallet_gate_dict = None
        recent_decisions = []
        tax_summary = B3TaxGuard(
            exemption_limit=settings.B3_SWING_EXEMPTION_LIMIT,
            guard_buffer=settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
        ).summarize_month([]).to_dict()
        trade_stats = {
            "total_trades": 0,
            "sell_trades": 0,
            "winning_sells": 0,
            "win_rate": 0.0,
            "realized_pnl_total": 0.0,
            "total_fees": 0.0,
        }
        balance_val = 0.0
        equity_val = 0.0
        positions_list = []

    market_radar = market_repo.get_market_snapshots()
    if bova11_change_pct == 0.0:
        for item in market_radar:
            if item.get("ticker") == "BOVA11":
                bova11_change_pct = round(float(item.get("change_pct") or 0.0), 2)
                break

    headlines = market_repo.get_recent_news(limit=25)
    sentiments = market_repo.get_all_sentiment_scores()
    health = SystemHealthMonitor.collect_metrics()

    orch = getattr(request.app.state, "orchestrator", None)
    engine_metrics = orch.get_metrics() if (orch is not None and hasattr(orch, "get_metrics")) else None

    return {
        "success": True,
        "version": settings.VERSION,
        "balance": round(balance_val, 2),
        "equity": round(equity_val, 2),
        "positions": positions_list,
        "recent_decisions": recent_decisions,
        "market_status": market_status,
        "market_open": market_open,
        "opening_cooldown_active": cooldown_active,
        "bova11_change_pct": bova11_change_pct,
        "regime_bearish_veto": regime_bearish_veto,
        "environment": settings.ENVIRONMENT_ID,
        "environment_label": settings.ENVIRONMENT_LABEL,
        "is_production": settings.IS_PRODUCTION,
        "is_simulation": settings.IS_SIMULATION,
        "portfolio": port_snap,
        "operational_equity": port_snap["operational_equity"],
        "total_equity": port_snap["total_equity"],
        "free_cash": port_snap["free_cash"],
        "vault_reserve_cash": port_snap["vault_reserve_cash"],
        "ai_allocated_capital": port_snap["ai_allocated_capital"],
        "allocated_capital": port_snap["allocated_capital"],
        "buying_power": port_snap["buying_power"],
        "unrealized_pnl": port_snap["unrealized_pnl"],
        "pnl_percent": port_snap["pnl_percent"],
        "daily_pnl_percent": port_snap["daily_pnl_percent"],
        "trade_stats": trade_stats,
        "tax_summary": tax_summary,
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
        "market_radar": market_radar[:80],
        "headlines": headlines,
        "sentiments": sentiments,
        "health": health,
        "engine_metrics": engine_metrics,
    }


@router.get("/api/trades")
def get_trades_endpoint(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
    days: int | None = Query(default=None, ge=1, le=3650),
    wallet_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, wallet_id, db=db, require_exists=False)
    if wallet is None:
        return {"success": True, "trades": [], "stats": {}}

    uid = int(ctx.user["id"])
    wid = int(wallet["id"])
    repo = TradingRepository(db)
    rows = repo.list_trades(uid, wid, limit=limit)

    if days is not None:
        cutoff_date = (get_brt_now() - timedelta(days=int(days))).date().isoformat()
        rows = [r for r in rows if str(r.get("timestamp") or "")[:10] >= cutoff_date]

    formatted = [{**r, "environment": settings.ENVIRONMENT_ID} for r in rows]
    return {
        "success": True,
        "trades": formatted,
        "stats": repo.get_trade_stats(uid, wid),
    }


@router.get("/api/equity_curve")
def get_equity_curve_endpoint(
    request: Request,
    limit: int = Query(default=300, ge=1, le=1000),
    days: int | None = Query(default=None, ge=1, le=3650),
    wallet_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, wallet_id, db=db, require_exists=False)
    if wallet is None:
        return {"success": True, "equity_curve": []}

    uid = int(ctx.user["id"])
    wid = int(wallet["id"])
    init_cap = float(wallet.get("initial_capital") or 0.0)
    repo = TradingRepository(db)
    rows = repo.list_equity_curve(uid, wid, limit=limit)

    if days is not None:
        cutoff_date = (get_brt_now() - timedelta(days=int(days))).date().isoformat()
        rows = [r for r in rows if str(r.get("snapshot_date") or "")[:10] >= cutoff_date]

    trades = repo.list_trades(uid, wid, limit=500)
    day_counts: dict[str, tuple[int, int]] = {}
    for tr in trades:
        d_key = str(tr.get("timestamp") or "")[:10]
        if not d_key:
            continue
        b_cnt, s_cnt = day_counts.get(d_key, (0, 0))
        if str(tr.get("action") or "").upper() == "BUY":
            day_counts[d_key] = (b_cnt + 1, s_cnt)
        elif str(tr.get("action") or "").upper() == "SELL":
            day_counts[d_key] = (b_cnt, s_cnt + 1)

    curve: list[dict[str, Any]] = []
    for r in rows:
        snap_date = str(r.get("snapshot_date") or str(r.get("timestamp") or "")[:10])
        b_cnt, s_cnt = day_counts.get(snap_date, (0, 0))
        curve.append(
            {
                **r,
                "date": snap_date,
                "buys_count": b_cnt,
                "sells_count": s_cnt,
                "initial_capital": round(init_cap, 2),
                "environment": settings.ENVIRONMENT_ID,
            }
        )

    return {"success": True, "equity_curve": curve}


@router.get("/api/decisions")
def get_decisions_endpoint(
    request: Request,
    limit: int = Query(default=50, ge=1, le=250),
    wallet_id: int | None = Query(default=None),
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wallet = resolve_active_wallet(ctx, wallet_id, db=db, require_exists=False)
    if wallet is None:
        return {"success": True, "decisions": []}

    rows = TradingRepository(db).list_decisions(
        int(ctx.user["id"]),
        int(wallet["id"]),
        limit=limit,
    )
    return {
        "success": True,
        "decisions": [{**r, "environment": settings.ENVIRONMENT_ID} for r in rows],
    }


@router.post("/api/positions/{ticker}/protect")
def toggle_position_protection_endpoint(
    ticker: str,
    request: Request,
    payload: PositionProtectPayload | None = None,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    wid_arg = payload.wallet_id if payload else None
    wallet = resolve_active_wallet(ctx, wid_arg, db=db)
    assert wallet is not None

    uid = int(ctx.user["id"])
    wid = int(wallet["id"])
    clean_tk = normalize_b3_ticker(ticker)
    if not clean_tk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ticker B3 inválido.",
        )

    repo = TradingRepository(db)
    pos = repo.get_position(uid, wid, clean_tk)
    if pos is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Posição não encontrada nesta carteira.",
        )

    desired = (
        (not pos.do_not_touch)
        if (payload is None or payload.do_not_touch is None)
        else bool(payload.do_not_touch)
    )
    repo.set_do_not_touch(uid, wid, clean_tk, desired)

    AuditRepository(db).record_event(
        event_type="POSITION_PROTECTION_UPDATED",
        actor_user_id=uid,
        target_wallet_id=wid,
        ip_address=ctx.ip_address,
        details={"ticker": clean_tk, "do_not_touch": desired},
    )
    return {
        "success": True,
        "ticker": clean_tk,
        "do_not_touch": desired,
    }


@router.get("/api/guide")
def get_guide_endpoint(
    _ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    payload = get_guide_payload()
    return {
        "success": True,
        **payload,
    }


@router.get("/api/logs")
def get_admin_logs_endpoint(
    limit: int = Query(default=200, ge=1, le=500),
    _ctx: AuthContext = Depends(require_luke_admin),
) -> dict[str, Any]:
    lines = [scrub_secrets(ln) for ln in get_recent_admin_logs(limit=limit)]
    return {"success": True, "logs": lines}


@router.websocket("/ws/logs")
async def websocket_admin_logs(websocket: WebSocket) -> None:
    db_inst = getattr(websocket.app.state, "db", None)
    db = db_inst if isinstance(db_inst, DatabaseConnection) else DatabaseConnection()
    users_repo = UsersRepository(db)
    audit_repo = AuditRepository(db)

    origin = websocket.headers.get("Origin", "")
    host = websocket.headers.get("host", "")
    if origin and not is_origin_allowed(origin, host):
        await websocket.close(code=1008)
        return

    raw_token = (websocket.cookies.get("cortex_session") or "").strip()
    if not raw_token:
        auth_hdr = (websocket.headers.get("Authorization") or "").strip()
        if auth_hdr.startswith("Bearer "):
            raw_token = auth_hdr[7:].strip()

    if not raw_token:
        await websocket.close(code=1008)
        return

    user_agent = websocket.headers.get("User-Agent", "")
    ip_addr = websocket.client.host if websocket.client else "127.0.0.1"
    session, reason = users_repo.validate_session(raw_token, user_agent, touch=False)
    if session is None:
        if reason == "fingerprint_mismatch":
            audit_repo.record_event(
                event_type="SESSION_FINGERPRINT_MISMATCH",
                ip_address=ip_addr,
                details={"path": "/ws/logs"},
            )
        await websocket.close(code=1008)
        return

    user = users_repo.get_by_id(int(session["user_id"]))
    if (
        user is None
        or not bool(user.get("is_active", 1))
        or bool(user.get("must_change_password", 0))
    ):
        await websocket.close(code=1008)
        return

    if not UsersRepository.is_luke_owner(user):
        audit_repo.record_event(
            event_type="UNAUTHORIZED_ADMIN_ATTEMPT",
            actor_user_id=int(user["id"]),
            ip_address=ip_addr,
            details={"path": "/ws/logs", "username": str(user.get("username", ""))},
        )
        await websocket.close(code=1008)
        return

    await websocket.accept()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=250)

    def _on_log_line(line: str) -> None:
        clean = scrub_secrets(line)

        def _enqueue() -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(clean)
            except asyncio.QueueFull:
                pass

        try:
            loop.call_soon_threadsafe(_enqueue)
        except RuntimeError:
            pass

    register_log_listener(_on_log_line)
    try:
        initial = [scrub_secrets(ln) for ln in get_recent_admin_logs(limit=120)]
        await websocket.send_json({"type": "snapshot", "logs": initial})
        while True:
            try:
                next_line = await asyncio.wait_for(queue.get(), timeout=25.0)
                await websocket.send_json({"type": "line", "line": next_line})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        unregister_log_listener(_on_log_line)
