from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from bi.pbids_builder import build_pbids_dict
from bi.star_schema import (
    _extract_market_runtime_state,
    build_dim_assets_b3,
    build_dim_calendar_b3,
    build_dim_wallets,
    build_fact_cashflow_d2,
    build_fact_decisions,
    build_fact_equity,
    build_fact_market_radar,
    build_fact_positions,
    build_fact_trades,
    build_kpi_summary,
    build_mobile_bundle,
)
from config.settings import settings
from domain.b3_rules import get_brt_now
from domain.operational_guard import OperationalGuard
from observability.health import SystemHealthMonitor
from security.bi_tokens import is_bi_bearer_token
from security.rate_limit import bi_api_limiter
from security.session_csrf import get_client_ip
from storage.audit_repo import AuditRepository
from storage.bi_tokens_repo import BiTokensRepository
from storage.connection import DatabaseConnection
from storage.state_repo import StateRepository
from storage.users_repo import UsersRepository
from storage.wallets_repo import WalletsRepository
from web.dependencies import (
    AuthContext,
    get_current_context,
    get_db,
    require_step_up,
)

router = APIRouter(prefix="/api/bi", tags=["powerbi"])


@dataclass
class BiReadContext:
    user: dict[str, Any]
    user_id: int
    is_luke_admin: bool
    include_admin: bool
    auth_mode: str
    scoped_wallet_id: int | None
    default_wallet: dict[str, Any] | None
    ip_address: str
    db: DatabaseConnection
    bi_key_id: int | None = None


class CreateBiKeyPayload(BaseModel):
    label: str = Field(..., min_length=2, max_length=80)
    wallet_id: int | None = Field(default=None)
    expires_in_days: int | None = Field(default=90, ge=0, le=3650)
    allowed_cidr: str = Field(default="", max_length=512)
    include_admin: bool = Field(default=False)


def get_bi_read_context(request: Request) -> BiReadContext:
    if request.method.upper() != "GET":
        raise HTTPException(
            status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
            detail="Endpoints analíticos BI aceitam exclusivamente método GET somente-leitura.",
        )

    db = get_db(request)
    ip_address = get_client_ip(request)
    auth_hdr = (request.headers.get("Authorization") or "").strip()
    bearer_tok = auth_hdr[7:].strip() if auth_hdr.lower().startswith("bearer ") else ""

    if is_bi_bearer_token(bearer_tok):
        rate_key = f"bi_tok:{bearer_tok[:14]}:{ip_address}"
        locked, retry_sec = bi_api_limiter.hit(rate_key)
        if locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Limite de consultas da API BI atingido. Aguarde {retry_sec}s.",
            )

        bi_repo = BiTokensRepository(db)
        key_data, reason = bi_repo.authenticate_key(bearer_tok, ip_address)
        if key_data is None:
            actor_uid, target_wid = bi_repo.lookup_owner_for_audit(bearer_tok)
            AuditRepository(db).record_event(
                event_type="BI_TOKEN_AUTH_FAILED",
                actor_user_id=actor_uid,
                target_wallet_id=target_wid,
                ip_address=ip_address,
                details={
                    "path": request.url.path,
                    "reason": reason,
                    "token_prefix": bearer_tok[:14],
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token BI inválido, revogado, expirado ou bloqueado por política CIDR.",
            )

        user = key_data["user"]
        uid = int(user["id"])
        scoped_wid = int(key_data["wallet_id"]) if key_data.get("wallet_id") is not None else None
        wallets_repo = WalletsRepository(db)

        raw_q_wid = request.query_params.get("wallet_id")
        if raw_q_wid is not None and str(raw_q_wid).strip() != "":
            try:
                req_wid = int(str(raw_q_wid).strip())
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Parâmetro wallet_id inválido.",
                ) from exc

            if scoped_wid is not None and req_wid != scoped_wid:
                AuditRepository(db).record_event(
                    event_type="BI_TOKEN_WALLET_IDOR_BLOCKED",
                    actor_user_id=uid,
                    target_wallet_id=req_wid,
                    ip_address=ip_address,
                    details={
                        "key_id": int(key_data["id"]),
                        "scoped_wallet_id": scoped_wid,
                        "requested_wallet_id": req_wid,
                        "path": request.url.path,
                    },
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Este Token BI está restrito a outra carteira específica (Anti-IDOR).",
                )

            if wallets_repo.get_owned_wallet(uid, req_wid) is None:
                AuditRepository(db).record_event(
                    event_type="IDOR_BLOCKED",
                    actor_user_id=uid,
                    target_wallet_id=req_wid,
                    ip_address=ip_address,
                    details={"requested_wallet_id": req_wid, "path": request.url.path},
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Acesso negado à carteira solicitada (Anti-IDOR).",
                )

        default_wallet = (
            wallets_repo.get_owned_wallet(uid, scoped_wid)
            if scoped_wid is not None
            else wallets_repo.get_default_or_first_wallet(uid)
        )
        is_luke = UsersRepository.is_luke_owner(user)
        return BiReadContext(
            user=user,
            user_id=uid,
            is_luke_admin=is_luke,
            include_admin=bool(key_data.get("include_admin", False)) and is_luke,
            auth_mode="bi_token",
            scoped_wallet_id=scoped_wid,
            default_wallet=default_wallet,
            ip_address=ip_address,
            db=db,
            bi_key_id=int(key_data["id"]),
        )

    rate_key = f"bi_sess:{ip_address}"
    locked, retry_sec = bi_api_limiter.hit(rate_key)
    if locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Limite de consultas da API BI atingido. Aguarde {retry_sec}s.",
        )

    sess_ctx: AuthContext = get_current_context(request)
    req_inc_adm = str(request.query_params.get("include_admin") or "").strip().lower() in ("1", "true", "yes")
    return BiReadContext(
        user=sess_ctx.user,
        user_id=int(sess_ctx.user["id"]),
        is_luke_admin=sess_ctx.is_luke_admin,
        include_admin=bool(sess_ctx.is_luke_admin and req_inc_adm),
        auth_mode="session",
        scoped_wallet_id=None,
        default_wallet=sess_ctx.active_wallet,
        ip_address=sess_ctx.ip_address,
        db=sess_ctx.db or db,
        bi_key_id=None,
    )


def _resolve_bi_wallet_ids(
    bi_ctx: BiReadContext,
    wallet_id: int | None,
) -> list[int]:
    owned_wallets = WalletsRepository(bi_ctx.db).list_for_user(bi_ctx.user_id)
    owned_ids = [int(w["id"]) for w in owned_wallets]
    owned_set = set(owned_ids)

    if wallet_id is not None:
        req_wid = int(wallet_id)
        if bi_ctx.scoped_wallet_id is not None and req_wid != bi_ctx.scoped_wallet_id:
            AuditRepository(bi_ctx.db).record_event(
                event_type="BI_TOKEN_WALLET_IDOR_BLOCKED",
                actor_user_id=bi_ctx.user_id,
                target_wallet_id=req_wid,
                ip_address=bi_ctx.ip_address,
                details={
                    "scoped_wallet_id": bi_ctx.scoped_wallet_id,
                    "requested_wallet_id": req_wid,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Este Token BI está restrito a outra carteira específica (Anti-IDOR).",
            )
        if req_wid not in owned_set:
            AuditRepository(bi_ctx.db).record_event(
                event_type="IDOR_BLOCKED",
                actor_user_id=bi_ctx.user_id,
                target_wallet_id=req_wid,
                ip_address=bi_ctx.ip_address,
                details={"requested_wallet_id": req_wid},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acesso negado à carteira solicitada (Anti-IDOR).",
            )
        return [req_wid]

    if bi_ctx.scoped_wallet_id is not None:
        return [bi_ctx.scoped_wallet_id] if bi_ctx.scoped_wallet_id in owned_set else []

    return owned_ids


def _resolve_bi_single_wallet(
    bi_ctx: BiReadContext,
    wallet_id: int | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    wallets_repo = WalletsRepository(bi_ctx.db)
    owned_wallets = wallets_repo.list_for_user(bi_ctx.user_id)
    owned_map = {int(w["id"]): w for w in owned_wallets}

    if bi_ctx.scoped_wallet_id is not None:
        visible_wallets = [w for w in owned_wallets if int(w["id"]) == bi_ctx.scoped_wallet_id]
    else:
        visible_wallets = owned_wallets

    if wallet_id is not None:
        req_wid = int(wallet_id)
        if bi_ctx.scoped_wallet_id is not None and req_wid != bi_ctx.scoped_wallet_id:
            AuditRepository(bi_ctx.db).record_event(
                event_type="BI_TOKEN_WALLET_IDOR_BLOCKED",
                actor_user_id=bi_ctx.user_id,
                target_wallet_id=req_wid,
                ip_address=bi_ctx.ip_address,
                details={
                    "scoped_wallet_id": bi_ctx.scoped_wallet_id,
                    "requested_wallet_id": req_wid,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Este Token BI está restrito a outra carteira específica (Anti-IDOR).",
            )
        selected = owned_map.get(req_wid)
        if selected is None:
            AuditRepository(bi_ctx.db).record_event(
                event_type="IDOR_BLOCKED",
                actor_user_id=bi_ctx.user_id,
                target_wallet_id=req_wid,
                ip_address=bi_ctx.ip_address,
                details={"requested_wallet_id": req_wid},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acesso negado à carteira solicitada (Anti-IDOR).",
            )
        return selected, visible_wallets

    if bi_ctx.scoped_wallet_id is not None:
        return owned_map.get(bi_ctx.scoped_wallet_id), visible_wallets

    if bi_ctx.default_wallet is not None:
        def_id = int(bi_ctx.default_wallet["id"])
        if def_id in owned_map:
            return owned_map[def_id], visible_wallets

    fallback = wallets_repo.get_default_or_first_wallet(bi_ctx.user_id)
    return fallback, visible_wallets


def _wrap_table_response(rows: list[dict[str, Any]], flat: bool) -> list[dict[str, Any]] | dict[str, Any]:
    if flat:
        return rows
    return {
        "success": True,
        "schema_version": "1.0",
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
        "row_count": len(rows),
        "rows": rows,
    }


def _build_admin_platform_overview(db: DatabaseConnection) -> dict[str, Any]:
    with db.readonly() as conn:
        active_users_row = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_active = 1").fetchone()
        total_users_row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        wallets_row = conn.execute("SELECT COUNT(*) AS c FROM user_wallets").fetchone()
        auto_wallets_row = conn.execute(
            "SELECT COUNT(*) AS c FROM user_wallets WHERE automation_enabled = 1"
        ).fetchone()
        active_bi_keys_row = conn.execute(
            "SELECT COUNT(*) AS c FROM bi_api_keys WHERE is_active = 1"
        ).fetchone()

    global_state = StateRepository(db).get_state(auto_expire_panic=True)
    global_gate = OperationalGuard.evaluate_global(global_state, now=get_brt_now())
    health = SystemHealthMonitor.collect_metrics()

    return {
        "active_users_count": int(active_users_row["c"]) if active_users_row else 0,
        "total_users_count": int(total_users_row["c"]) if total_users_row else 0,
        "active_wallets_count": int(wallets_row["c"]) if wallets_row else 0,
        "automated_wallets_count": int(auto_wallets_row["c"]) if auto_wallets_row else 0,
        "active_bi_keys_count": int(active_bi_keys_row["c"]) if active_bi_keys_row else 0,
        "global_operational_mode": global_gate.effective_mode,
        "maintenance_mode": bool(global_state.get("maintenance_mode", False)),
        "panic_mode": bool(global_state.get("panic_mode", False)),
        "cpu_percent": float(health.get("cpu_percent", 0.0)),
        "ram_percent": float(health.get("ram_percent", 0.0)),
        "disk_percent": float(health.get("disk_percent", 0.0)),
        "uptime_seconds": float(health.get("uptime_seconds", 0.0)),
    }


@router.get("/keys")
def list_bi_keys(
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    keys = BiTokensRepository(db).list_keys_for_user(int(ctx.user["id"]))
    return {
        "success": True,
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
        "can_include_admin": ctx.is_luke_admin,
        "keys": keys,
    }


@router.post("/keys")
def create_bi_key(
    payload: CreateBiKeyPayload,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
    _step_up: AuthContext = Depends(require_step_up),
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    bi_repo = BiTokensRepository(db)
    try:
        created = bi_repo.create_key(
            user_id=int(ctx.user["id"]),
            label=payload.label,
            wallet_id=payload.wallet_id,
            expires_in_days=payload.expires_in_days,
            allowed_cidr=payload.allowed_cidr,
            include_admin=payload.include_admin,
        )
    except PermissionError as exc:
        AuditRepository(db).record_event(
            event_type="BI_TOKEN_CREATE_DENIED",
            actor_user_id=int(ctx.user["id"]),
            target_wallet_id=payload.wallet_id,
            ip_address=ctx.ip_address,
            details={"reason": str(exc)},
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    raw_token = str(created.pop("raw_token"))
    AuditRepository(db).record_event(
        event_type="BI_TOKEN_CREATED",
        actor_user_id=int(ctx.user["id"]),
        target_wallet_id=created.get("wallet_id"),
        ip_address=ctx.ip_address,
        details={
            "key_id": int(created["id"]),
            "label": str(created["label"]),
            "token_prefix": str(created["token_prefix"]),
            "wallet_id": created.get("wallet_id"),
            "expires_at": created.get("expires_at"),
            "allowed_cidr": str(created.get("allowed_cidr") or ""),
            "include_admin": bool(created.get("include_admin", False)),
        },
    )
    return {
        "success": True,
        "key": created,
        "raw_token": raw_token,
    }


def _execute_revoke_bi_key(
    key_id: int,
    request: Request,
    ctx: AuthContext,
) -> dict[str, Any]:
    db = ctx.db or get_db(request)
    revoked = BiTokensRepository(db).revoke_key(int(ctx.user["id"]), int(key_id))
    if not revoked:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token BI não encontrado ou já revogado.",
        )
    AuditRepository(db).record_event(
        event_type="BI_TOKEN_REVOKED",
        actor_user_id=int(ctx.user["id"]),
        ip_address=ctx.ip_address,
        details={"key_id": int(key_id)},
    )
    return {
        "success": True,
        "revoked_key_id": int(key_id),
    }


@router.delete("/keys/{key_id}")
def delete_bi_key(
    key_id: int,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    return _execute_revoke_bi_key(key_id=key_id, request=request, ctx=ctx)


@router.post("/keys/{key_id}/revoke")
def revoke_bi_key_post(
    key_id: int,
    request: Request,
    ctx: AuthContext = Depends(get_current_context),
) -> dict[str, Any]:
    return _execute_revoke_bi_key(key_id=key_id, request=request, ctx=ctx)


@router.get("/download/pbids")
def download_pbids_connector(
    request: Request,
    _ctx: AuthContext = Depends(get_current_context),
) -> Response:
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    scheme = forwarded_proto if forwarded_proto in ("http", "https") else request.url.scheme
    host = (request.headers.get("host") or f"localhost:{settings.DASHBOARD_PORT}").strip()
    base_url = f"{scheme}://{host}"

    pbids_dict = build_pbids_dict(base_url=base_url, environment_id=settings.ENVIRONMENT_ID)
    env_suffix = "prod" if settings.IS_PRODUCTION else "homolog"
    filename = f"cortex_ia_{env_suffix}.pbids"
    body_bytes = (json.dumps(pbids_dict, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    return Response(
        content=body_bytes,
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/v1/metadata")
def bi_v1_metadata(
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> dict[str, Any]:
    tables = [
        {
            "name": "dim_wallets",
            "kind": "dimension",
            "endpoint": "/api/bi/v1/dim_wallets",
            "primary_key": "wallet_id",
        },
        {
            "name": "dim_assets_b3",
            "kind": "dimension",
            "endpoint": "/api/bi/v1/dim_assets_b3",
            "primary_key": "ticker",
        },
        {
            "name": "dim_calendar_b3",
            "kind": "dimension",
            "endpoint": "/api/bi/v1/dim_calendar_b3",
            "primary_key": "date",
        },
        {
            "name": "fact_equity",
            "kind": "fact",
            "endpoint": "/api/bi/v1/fact_equity",
            "foreign_keys": ["wallet_id", "date"],
        },
        {
            "name": "fact_positions",
            "kind": "fact",
            "endpoint": "/api/bi/v1/fact_positions",
            "foreign_keys": ["wallet_id", "ticker"],
        },
        {
            "name": "fact_trades",
            "kind": "fact",
            "endpoint": "/api/bi/v1/fact_trades",
            "foreign_keys": ["wallet_id", "ticker", "date"],
        },
        {
            "name": "fact_decisions",
            "kind": "fact",
            "endpoint": "/api/bi/v1/fact_decisions",
            "foreign_keys": ["wallet_id", "ticker", "date"],
        },
        {
            "name": "fact_cashflow_d2",
            "kind": "fact",
            "endpoint": "/api/bi/v1/fact_cashflow_d2",
            "foreign_keys": ["wallet_id", "date"],
        },
        {
            "name": "fact_market_radar",
            "kind": "fact",
            "endpoint": "/api/bi/v1/fact_market_radar",
            "foreign_keys": ["ticker"],
        },
        {
            "name": "kpi_summary",
            "kind": "kpi",
            "endpoint": "/api/bi/v1/kpi_summary",
            "foreign_keys": ["wallet_id"],
        },
        {
            "name": "mobile_bundle",
            "kind": "bundle",
            "endpoint": "/api/bi/v1/mobile_bundle",
            "foreign_keys": ["wallet_id"],
        },
    ]

    payload: dict[str, Any] = {
        "success": True,
        "schema_version": "1.0",
        "project": "Córtex IA",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT_ID,
        "environment_label": settings.ENVIRONMENT_LABEL,
        "is_production": settings.IS_PRODUCTION,
        "is_simulation": settings.IS_SIMULATION,
        "generated_at": get_brt_now().isoformat(),
        "auth_mode": bi_ctx.auth_mode,
        "scoped_wallet_id": bi_ctx.scoped_wallet_id,
        "user": {
            "id": bi_ctx.user_id,
            "username": str(bi_ctx.user.get("username") or ""),
        },
        "tables": tables,
    }
    if bi_ctx.include_admin and bi_ctx.is_luke_admin:
        payload["admin_platform_overview"] = _build_admin_platform_overview(bi_ctx.db)
    return payload


@router.get("/v1/dim_wallets")
def bi_v1_dim_wallets(
    wallet_id: int | None = Query(default=None),
    flat: bool = Query(default=False),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    wids = _resolve_bi_wallet_ids(bi_ctx, wallet_id)
    rows = build_dim_wallets(bi_ctx.db, bi_ctx.user_id, allowed_wallet_ids=wids)
    return _wrap_table_response(rows, flat=flat)


@router.get("/v1/dim_assets_b3")
def bi_v1_dim_assets_b3(
    flat: bool = Query(default=False),
    _bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    rows = build_dim_assets_b3()
    return _wrap_table_response(rows, flat=flat)


@router.get("/v1/dim_calendar_b3")
def bi_v1_dim_calendar_b3(
    days: int = Query(default=730, ge=30, le=3650),
    flat: bool = Query(default=False),
    _bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    rows = build_dim_calendar_b3(days=days)
    return _wrap_table_response(rows, flat=flat)


@router.get("/v1/fact_equity")
def bi_v1_fact_equity(
    wallet_id: int | None = Query(default=None),
    days: int = Query(default=730, ge=1, le=3650),
    flat: bool = Query(default=False),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    wids = _resolve_bi_wallet_ids(bi_ctx, wallet_id)
    rows = build_fact_equity(bi_ctx.db, bi_ctx.user_id, wallet_ids=wids, days=days)
    return _wrap_table_response(rows, flat=flat)


@router.get("/v1/fact_positions")
def bi_v1_fact_positions(
    request: Request,
    wallet_id: int | None = Query(default=None),
    flat: bool = Query(default=False),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    wids = _resolve_bi_wallet_ids(bi_ctx, wallet_id)
    _mkt_open, _bova_pct, _veto, live_quotes = _extract_market_runtime_state(request, [])
    rows = build_fact_positions(
        bi_ctx.db,
        bi_ctx.user_id,
        wallet_ids=wids,
        live_quotes=live_quotes,
    )
    return _wrap_table_response(rows, flat=flat)


@router.get("/v1/fact_trades")
def bi_v1_fact_trades(
    wallet_id: int | None = Query(default=None),
    days: int = Query(default=730, ge=1, le=3650),
    flat: bool = Query(default=False),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    wids = _resolve_bi_wallet_ids(bi_ctx, wallet_id)
    rows = build_fact_trades(bi_ctx.db, bi_ctx.user_id, wallet_ids=wids, days=days)
    return _wrap_table_response(rows, flat=flat)


@router.get("/v1/fact_decisions")
def bi_v1_fact_decisions(
    wallet_id: int | None = Query(default=None),
    days: int = Query(default=180, ge=1, le=3650),
    flat: bool = Query(default=False),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    wids = _resolve_bi_wallet_ids(bi_ctx, wallet_id)
    rows = build_fact_decisions(bi_ctx.db, bi_ctx.user_id, wallet_ids=wids, days=days)
    return _wrap_table_response(rows, flat=flat)


@router.get("/v1/fact_cashflow_d2")
def bi_v1_fact_cashflow_d2(
    wallet_id: int | None = Query(default=None),
    days: int = Query(default=365, ge=1, le=3650),
    flat: bool = Query(default=False),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    wids = _resolve_bi_wallet_ids(bi_ctx, wallet_id)
    payload = build_fact_cashflow_d2(bi_ctx.db, bi_ctx.user_id, wallet_ids=wids, days=days)
    rows = payload["rows"]
    if flat:
        return rows
    return {
        "success": True,
        "schema_version": "1.0",
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
        "row_count": len(rows),
        "rows": rows,
        "d2_settlements": payload["d2_settlements"],
        "capital_transactions": payload["capital_transactions"],
        "withdrawal_goals": payload["withdrawal_goals"],
        "tax_20k_summary": payload["tax_20k_summary"],
    }


@router.get("/v1/fact_market_radar")
def bi_v1_fact_market_radar(
    flat: bool = Query(default=False),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    rows = build_fact_market_radar(bi_ctx.db)
    return _wrap_table_response(rows, flat=flat)


@router.get("/v1/kpi_summary")
def bi_v1_kpi_summary(
    request: Request,
    wallet_id: int | None = Query(default=None),
    days: int = Query(default=365, ge=7, le=3650),
    flat: bool = Query(default=False),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> list[dict[str, Any]] | dict[str, Any]:
    selected_wallet, _visible = _resolve_bi_single_wallet(bi_ctx, wallet_id)
    radar = build_fact_market_radar(bi_ctx.db)
    _mkt_open, bova11_pct, _veto, live_quotes = _extract_market_runtime_state(request, radar)
    sel_wid = int(selected_wallet["id"]) if selected_wallet is not None else None
    kpi = build_kpi_summary(
        db=bi_ctx.db,
        user_id=bi_ctx.user_id,
        wallet_id=sel_wid,
        live_quotes=live_quotes,
        bova11_change_pct=bova11_pct,
        days=days,
    )
    if flat:
        return [kpi]
    return {
        "success": True,
        "schema_version": "1.0",
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
        "row_count": 1,
        "rows": [kpi],
        "kpi_summary": kpi,
        **kpi,
    }


@router.get("/v1/mobile_bundle")
def bi_v1_mobile_bundle(
    request: Request,
    wallet_id: int | None = Query(default=None),
    days: int = Query(default=180, ge=7, le=3650),
    bi_ctx: BiReadContext = Depends(get_bi_read_context),
) -> dict[str, Any]:
    selected_wallet, visible_wallets = _resolve_bi_single_wallet(bi_ctx, wallet_id)
    return build_mobile_bundle(
        request=request,
        db=bi_ctx.db,
        user_id=bi_ctx.user_id,
        selected_wallet=selected_wallet,
        all_user_wallets=visible_wallets,
        days=days,
    )
