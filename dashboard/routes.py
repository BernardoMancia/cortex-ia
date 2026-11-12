"""
Rotas HTTP e WebSocket do Dashboard Córtex IA.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from typing import Any, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel

from auth import (
    MAX_IDLE_SECONDS,
    MAX_SESSION_SECONDS,
    PasswordManager,
    derive_csrf_token,
    extract_session_token,
    get_client_ip,
    get_current_user,
    get_current_user_interactive,
    is_origin_allowed,
    rate_limiter,
)
from broker.simulator import SimulatorBroker
from config.settings import settings
from data.database import DatabaseManager
from portfolio import Portfolio

logger = logging.getLogger("cortex.dashboard.routes")

router = APIRouter()


def _get_simulator_state_path() -> str:
    return str(settings.simulator_state_path)


def _read_simulator_state() -> dict[str, Any]:
    state_path = _get_simulator_state_path()
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
                balance = float(state.get("balance", 0.0))
                raw_pos = state.get("positions", {})
                positions = (
                    list(raw_pos.values())
                    if isinstance(raw_pos, dict)
                    else (raw_pos or [])
                )
                return {"balance": balance, "positions": positions}
        except Exception:
            pass
    return {"balance": float(getattr(settings, "CAPITAL_INICIAL", 1000000.0)), "positions": []}


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    forwarded_proto = (request.headers.get("X-Forwarded-Proto") or "").strip().lower()
    is_secure = bool(
        settings.COOKIE_SECURE
        or request.url.scheme == "https"
        or forwarded_proto == "https"
    )
    max_age = int(getattr(settings, "AUTH_MAX_SESSION_SECONDS", MAX_SESSION_SECONDS))
    response.set_cookie(
        key="cortex_session",
        value=token,
        max_age=max_age,
        httponly=True,
        samesite="strict",
        secure=is_secure,
        path="/",
    )


def _check_user_lockout(db: DatabaseManager, user: Optional[dict[str, Any]]) -> tuple[bool, int]:
    """Verifica lockout persistido por usuário no banco de dados (se disponível)."""
    if not user or not hasattr(db, "is_user_locked"):
        return False, 0
    try:
        res = db.is_user_locked(user)
    except TypeError:
        try:
            res = db.is_user_locked(user["username"])
        except Exception:
            return False, 0
    if isinstance(res, tuple) and len(res) == 2:
        return bool(res[0]), int(res[1] or 0)
    if isinstance(res, bool):
        return res, int(getattr(settings, "AUTH_LOCKOUT_SECONDS", 900)) if res else 0
    return False, 0


def _record_user_login_failure(db: DatabaseManager, user: Optional[dict[str, Any]]) -> None:
    if not user or not hasattr(db, "record_user_login_failure"):
        return
    try:
        db.record_user_login_failure(user["id"])
    except Exception:
        try:
            db.record_user_login_failure(user["username"])
        except Exception:
            pass


def _clear_user_login_failures(db: DatabaseManager, user: Optional[dict[str, Any]]) -> None:
    if not user:
        return
    for method_name in ("reset_user_login_failures", "clear_user_login_failures"):
        fn = getattr(db, method_name, None)
        if callable(fn):
            try:
                fn(user["id"])
                return
            except Exception:
                try:
                    fn(user["username"])
                    return
                except Exception:
                    pass


def _validate_ws_session(db: DatabaseManager, token: str) -> bool:
    idle_limit = int(getattr(settings, "AUTH_MAX_IDLE_SECONDS", MAX_IDLE_SECONDS))
    try:
        session = db.get_valid_session(token, max_idle_seconds=idle_limit, touch=False)
    except TypeError:
        session = db.get_valid_session(token, max_idle_seconds=idle_limit)
    return session is not None


class LoginRequest(BaseModel):
    username: str
    password: str


class FirstLoginRequest(BaseModel):
    current_username: str
    current_password: str
    new_username: str
    new_password: str


@router.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, response: Response):
    ip = get_client_ip(request)
    is_locked, remaining = rate_limiter.is_locked(ip)
    if is_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Muitas tentativas falhas. Bloqueado temporariamente por {remaining} segundos.",
        )

    db = DatabaseManager()
    user = db.get_user_by_username(req.username)
    user_locked, user_rem = _check_user_lockout(db, user)
    if user_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Conta bloqueada temporariamente por tentativas excessivas ({max(1, user_rem)}s).",
        )

    if not user:
        PasswordManager.dummy_verify(req.password)
        _, lockout = rate_limiter.record_failure(ip)
        if lockout > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Limite de tentativas excedido. Bloqueado por {max(1, lockout // 60)} minutos.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha incorretos.",
        )

    valid = PasswordManager.verify_password(req.password, user["password_hash"], user["salt"])
    if not valid:
        _record_user_login_failure(db, user)
        _, lockout = rate_limiter.record_failure(ip)
        if lockout > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Limite de tentativas excedido. Bloqueado por {max(1, lockout // 60)} minutos.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha incorretos.",
        )

    rate_limiter.reset(ip)
    _clear_user_login_failures(db, user)

    if user["must_change_password"]:
        return {
            "success": True,
            "must_change_password": True,
            "username": user["username"],
            "message": "Primeiro acesso detectado. É obrigatório alterar o usuário e a senha.",
        }

    session_ttl = int(getattr(settings, "AUTH_MAX_SESSION_SECONDS", MAX_SESSION_SECONDS))
    token = db.create_session(
        user_id=user["id"],
        ip_address=ip,
        user_agent=request.headers.get("User-Agent", ""),
        max_session_seconds=session_ttl,
    )
    _set_session_cookie(response, request, token)
    session_row = db.get_valid_session(token, touch=False)
    csrf_token = derive_csrf_token(token, session_row)

    return {
        "success": True,
        "must_change_password": False,
        "csrf_token": csrf_token,
        "user": {
            "id": user["id"],
            "username": user["username"],
        },
    }


@router.post("/api/auth/first-login")
def auth_first_login(req: FirstLoginRequest, request: Request, response: Response):
    ip = get_client_ip(request)
    is_locked, remaining = rate_limiter.is_locked(ip)
    if is_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Muitas tentativas falhas. Bloqueado temporariamente por {remaining} segundos.",
        )

    db = DatabaseManager()
    user = db.get_user_by_username(req.current_username)
    user_locked, user_rem = _check_user_lockout(db, user)
    if user_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Conta bloqueada temporariamente ({max(1, user_rem)}s).",
        )

    if not user:
        PasswordManager.dummy_verify(req.current_password)
        _, lockout = rate_limiter.record_failure(ip)
        if lockout > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Limite de tentativas excedido. Bloqueado por {max(1, lockout // 60)} minutos.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas.",
        )

    valid_pw = PasswordManager.verify_password(
        req.current_password,
        user["password_hash"],
        user["salt"],
    )
    if not valid_pw or not user["must_change_password"]:
        if not valid_pw:
            _record_user_login_failure(db, user)
        _, lockout = rate_limiter.record_failure(ip)
        if lockout > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Limite de tentativas excedido. Bloqueado por {max(1, lockout // 60)} minutos.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas.",
        )

    new_user = req.new_username.strip()
    new_pass = req.new_password.strip()

    if len(new_user) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O nome de usuário deve ter pelo menos 3 caracteres.",
        )

    if len(new_pass) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A nova senha deve ter pelo menos 8 caracteres.",
        )

    if new_user.lower() == "admin" or new_pass.lower() == "admin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Você não pode utilizar o usuário ou a senha padrão de fábrica.",
        )

    if new_user.lower() != user["username"].lower():
        existing = db.get_user_by_username(new_user)
        if existing and existing["id"] != user["id"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Este nome de usuário já está em uso.",
            )

    rate_limiter.reset(ip)
    _clear_user_login_failures(db, user)
    db.update_user_credentials(user["id"], new_user, new_pass)
    db.revoke_all_user_sessions(user["id"])

    session_ttl = int(getattr(settings, "AUTH_MAX_SESSION_SECONDS", MAX_SESSION_SECONDS))
    token = db.create_session(
        user_id=user["id"],
        ip_address=ip,
        user_agent=request.headers.get("User-Agent", ""),
        max_session_seconds=session_ttl,
    )
    _set_session_cookie(response, request, token)
    session_row = db.get_valid_session(token, touch=False)
    csrf_token = derive_csrf_token(token, session_row)

    return {
        "success": True,
        "csrf_token": csrf_token,
        "user": {
            "id": user["id"],
            "username": new_user,
        },
        "message": "Credenciais atualizadas com sucesso!",
    }


@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    origin_hdr = request.headers.get("Origin", "")
    if origin_hdr and not is_origin_allowed(origin_hdr, request.headers.get("host", "")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Origem não autorizada.",
        )
    token = extract_session_token(request)
    if token:
        db = DatabaseManager()
        db.revoke_session(token)
    response.delete_cookie(key="cortex_session", path="/", samesite="strict")
    return {"success": True, "message": "Logout realizado com sucesso."}


@router.get("/api/auth/me")
def auth_me(user: dict[str, Any] = Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": user["created_at"],
        "csrf_token": user.get("csrf_token", ""),
    }


@router.post("/api/auth/heartbeat")
def auth_heartbeat(_user: dict[str, Any] = Depends(get_current_user_interactive)):
    return {
        "status": "active",
        "max_idle_seconds": int(getattr(settings, "AUTH_MAX_IDLE_SECONDS", MAX_IDLE_SECONDS)),
    }


@router.get("/api/status")
def get_status(
    request: Request,
    _user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = DatabaseManager()
    settings.reload_dynamic_config(db=db)
    is_sim = settings.SIMULATION_MODE
    curr_env = "homologation" if is_sim else "production"

    status_payload: dict[str, Any] = {
        "balance": 0.0,
        "equity": 0.0,
        "positions": [],
        "recent_decisions": [],
        "market_status": "DESCONHECIDO",
        "watchlist": [],
        "environment": curr_env,
        "is_simulation": is_sim,
    }

    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    engine = getattr(dashboard_state, "_engine", None) if dashboard_state else None

    if engine is not None:
        if is_sim and getattr(engine, "sim_portfolio", None):
            summ = engine.sim_portfolio.get_capital_summary()
            status_payload["balance"] = summ["free_cash"]
            status_payload["equity"] = summ["total_equity"]
            status_payload["positions"] = [
                {
                    "ticker": p.ticker,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "stop_loss": p.stop_loss,
                    "pnl": p.pnl,
                    "pnl_percent": p.pnl_percent,
                }
                for p in engine.sim_portfolio.positions
            ]
        elif not is_sim and getattr(engine, "prod_portfolio", None):
            summ = (
                engine.get_production_summary()
                if hasattr(engine, "get_production_summary")
                else engine.prod_portfolio.get_capital_summary()
            )
            status_payload["balance"] = summ.get("ai_allocated_capital", summ.get("free_cash", 0.0))
            status_payload["equity"] = summ.get("total_equity", 0.0)
            status_payload["positions"] = [
                {
                    "ticker": p.ticker,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "stop_loss": p.stop_loss,
                    "pnl": p.pnl,
                    "pnl_percent": p.pnl_percent,
                }
                for p in engine.prod_portfolio.positions
            ]

        live = dashboard_state.get_state() if dashboard_state else {}
        status_payload["market_status"] = live.get("market_status", "DESCONHECIDO")
        status_payload["watchlist"] = live.get("watchlist", [])
        status_payload["recent_decisions"] = live.get("recent_decisions", [])
    elif dashboard_state is not None:
        live = dashboard_state.get_state()
        port = live.get("portfolio", {})
        if port and port.get("simulation_mode", is_sim) == is_sim:
            status_payload["balance"] = port.get("free_cash", 0.0)
            status_payload["equity"] = port.get("total_value", 0.0)
            status_payload["positions"] = port.get("positions", [])
        status_payload["market_status"] = live.get("market_status", "DESCONHECIDO")
        status_payload["watchlist"] = live.get("watchlist", [])
        status_payload["recent_decisions"] = live.get("recent_decisions", [])
    else:
        if is_sim:
            sim_state = _read_simulator_state()
            status_payload["balance"] = sim_state["balance"]
            equity = sim_state["balance"]
            for p in sim_state["positions"]:
                if isinstance(p, dict):
                    p_val = float(p.get("current_price", p.get("entry_price", 0.0))) * int(
                        p.get("quantity", 0)
                    )
                    equity += p_val
                    status_payload["positions"].append(p)
            status_payload["equity"] = round(equity, 2)
        else:
            broker_balance = float(db.get_config("prod_broker_account_balance", "0.00") or 0.0)
            prod_ai_cap = float(db.get_config("prod_ai_allocated_capital", "0.00") or 0.0)
            prod_ai_cap = max(0.0, min(prod_ai_cap, broker_balance))
            status_payload["balance"] = round(prod_ai_cap, 2)
            status_payload["equity"] = round(broker_balance, 2)
            status_payload["positions"] = []

    decisions_filtered = []
    if status_payload["recent_decisions"]:
        for d in status_payload["recent_decisions"]:
            if not isinstance(d, dict):
                continue
            d_env = d.get("environment")
            if d_env == curr_env:
                decisions_filtered.append(d)
            elif d_env is None and "is_simulated" in d and bool(d["is_simulated"]) == is_sim:
                decisions_filtered.append(d)

    if not decisions_filtered:
        decisions_filtered = db.get_recent_decisions(limit=20, environment=curr_env)
    status_payload["recent_decisions"] = decisions_filtered

    return status_payload


@router.get("/api/decisions")
def get_decisions(
    limit: int = 50,
    environment: Optional[str] = None,
    _user: dict[str, Any] = Depends(get_current_user),
):
    db = DatabaseManager()
    settings.reload_dynamic_config(db=db)
    env = (
        environment.strip().lower()
        if environment
        else ("homologation" if settings.SIMULATION_MODE else "production")
    )
    if env not in ("homologation", "production"):
        raise HTTPException(status_code=400, detail="Ambiente inválido.")
    capped_limit = max(1, min(int(limit), 500))
    decisions = db.get_recent_decisions(limit=capped_limit, environment=env)
    return {
        "success": True,
        "decisions": decisions,
        "count": len(decisions),
        "environment": env,
    }


@router.get("/api/trades")
def get_trades(
    limit: int = 50,
    days: int = 365,
    environment: Optional[str] = None,
    _user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = DatabaseManager()
    settings.reload_dynamic_config(db=db)
    env = (
        environment.strip().lower()
        if environment
        else ("homologation" if settings.SIMULATION_MODE else "production")
    )
    if env not in ("homologation", "production"):
        raise HTTPException(status_code=400, detail="Ambiente inválido.")
    is_sim = env == "homologation"
    capped_limit = max(1, min(int(limit), 500))

    try:
        try:
            trades_raw = db.get_trade_history(
                days=max(1, int(days)),
                environment=env,
                limit=capped_limit,
            )
        except TypeError:
            try:
                trades_raw = db.get_trade_history(
                    days=max(1, int(days)),
                    environment=env,
                )
            except TypeError:
                trades_raw = db.get_trade_history(days=max(1, int(days)))

        trades_list = [
            t
            for t in trades_raw
            if isinstance(t, dict)
            and bool(t.get("is_simulated", is_sim)) == is_sim
        ][:capped_limit]
        return {
            "success": True,
            "trades": trades_list,
            "count": len(trades_list),
            "environment": env,
        }
    except Exception as exc:
        logger.warning("Falha ao ler trades do DB: %s", exc)
        return {"success": False, "trades": [], "count": 0, "environment": env}


@router.get("/api/equity_curve")
def get_equity_curve(
    days: int = 30,
    limit: Optional[int] = None,
    environment: Optional[str] = None,
    _user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = DatabaseManager()
    settings.reload_dynamic_config(db=db)
    env = (
        environment.strip().lower()
        if environment
        else ("homologation" if settings.SIMULATION_MODE else "production")
    )
    if env not in ("homologation", "production"):
        raise HTTPException(status_code=400, detail="Ambiente inválido.")
    effective_days = max(1, min(int(limit if limit is not None else days), 365))

    try:
        try:
            reports = db.get_daily_reports(
                days=effective_days,
                environment=env,
                latest=True,
            )
        except TypeError:
            reports = db.get_daily_reports(days=effective_days)
            is_sim = env == "homologation"
            reports = [
                r
                for r in reports
                if isinstance(r, dict)
                and (
                    r.get("environment", env) == env
                    and bool(r.get("is_simulated", is_sim)) == is_sim
                )
            ]

        curve_list = sorted(
            [dict(r) for r in reports if isinstance(r, dict)],
            key=lambda item: str(item.get("date", "")),
        )
        return {
            "success": True,
            "equity_curve": curve_list,
            "count": len(curve_list),
            "environment": env,
        }
    except Exception as exc:
        logger.warning("Falha ao ler relatórios diários do DB: %s", exc)
        return {"success": False, "equity_curve": [], "count": 0, "environment": env}


@router.get("/api/news")
def get_news(
    limit: int = 20,
    _user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    db = DatabaseManager()
    capped_limit = max(1, min(int(limit), 100))
    news_list = db.get_recent_news(limit=capped_limit)
    if not news_list:
        try:
            from data.news_scraper import NewsScraper

            scraper = NewsScraper()
            items = scraper.fetch_all_news()
            for item in items:
                for ticker in (item.tickers_mentioned or [None]):
                    db.insert_news(
                        title=item.title,
                        source=item.source,
                        url=item.url,
                        ticker=ticker,
                        published_at=(
                            item.published_at.isoformat() if item.published_at else None
                        ),
                    )
            news_list = db.get_recent_news(limit=capped_limit)
        except Exception as exc:
            logger.warning("Falha ao coletar notícias sob demanda: %s", exc)
    return {"success": True, "news": news_list, "count": len(news_list)}


@router.get("/api/logs")
def get_logs(
    limit: int = 150,
    _user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    from utils.logger import get_recent_logs

    capped_limit = max(1, min(int(limit), 400))
    lines = get_recent_logs(limit=capped_limit)
    return {"success": True, "logs": lines, "count": len(lines)}


class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        from utils.logger import add_log_listener

        add_log_listener(self._handle_log_record)

    def _handle_log_record(self, message: str) -> None:
        if not self.active_connections or not self.loop:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self.loop)
        except Exception:
            pass

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_text(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()


@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    origin_hdr = websocket.headers.get("origin", "")
    host_hdr = websocket.headers.get("host", "")
    if origin_hdr and not is_origin_allowed(origin_hdr, host_hdr):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Aceita exclusivamente cookie HttpOnly ou Authorization header (nunca ?token= na URL)
    token = websocket.cookies.get("cortex_session")
    if not token:
        auth_hdr = websocket.headers.get("authorization") or ""
        if auth_hdr.startswith("Bearer "):
            token = auth_hdr[7:].strip()

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    db = DatabaseManager()
    is_valid = await asyncio.to_thread(_validate_ws_session, db, token)
    if not is_valid:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket)
    try:
        from utils.logger import get_recent_logs

        initial_logs = get_recent_logs(limit=60)
        for log_line in initial_logs:
            await websocket.send_text(log_line)

        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
            except asyncio.TimeoutError:
                pass

            still_valid = await asyncio.to_thread(_validate_ws_session, db, token)
            if not still_valid:
                manager.disconnect(websocket)
                await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
                return
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


class AdjustAllocationRequest(BaseModel):
    ai_capital: float


class WithdrawalGoalRequest(BaseModel):
    amount: float
    mode: str = "on_profit"
    target_equity: Optional[float] = None


class RiskConfigRequest(BaseModel):
    stop_loss_percent: Optional[float] = None
    trailing_trigger_percent: Optional[float] = None
    trailing_distance_percent: Optional[float] = None


class EnvironmentRequest(BaseModel):
    environment: str


class SimulatorCapitalRequest(BaseModel):
    capital: float
    reset_positions: bool = False


@router.get("/api/capital/summary")
def get_capital_summary(
    request: Request,
    _user: dict[str, Any] = Depends(get_current_user),
):
    db = DatabaseManager()
    settings.reload_dynamic_config(db=db)
    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    engine = getattr(dashboard_state, "_engine", None) if dashboard_state else None

    is_sim = settings.SIMULATION_MODE

    if engine and hasattr(engine, "get_simulation_summary"):
        sim_summary = engine.get_simulation_summary()
    else:
        sim_state = _read_simulator_state()
        balance = float(
            sim_state.get(
                "balance",
                float(db.get_config("sim_initial_capital", str(settings.CAPITAL_INICIAL)) or settings.CAPITAL_INICIAL),
            )
        )
        pos_list = sim_state.get("positions", [])
        allocated = sum(
            float(p.get("current_price", p.get("entry_price", 0.0))) * int(p.get("quantity", 0))
            for p in pos_list
            if isinstance(p, dict)
        )
        total_eq = balance + allocated
        sim_summary = {
            "total_equity": round(total_eq, 2),
            "ai_allocated_capital": round(total_eq, 2),
            "vault_reserve_cash": 0.0,
            "free_cash": round(balance, 2),
            "allocated_capital": round(allocated, 2),
            "buying_power": round(balance, 2),
            "cumulative_realized_pnl": 0.0,
            "positions_count": len(pos_list),
        }

    if engine and hasattr(engine, "get_production_summary"):
        prod_summary = engine.get_production_summary()
    else:
        broker_balance = float(db.get_config("prod_broker_account_balance", "0.00") or 0.0)
        prod_ai_cap = float(db.get_config("prod_ai_allocated_capital", "0.00") or 0.0)
        prod_ai_cap = max(0.0, min(prod_ai_cap, broker_balance))
        prod_vault = max(0.0, broker_balance - prod_ai_cap)
        d2_info = db.get_d2_settlement_summary() if hasattr(db, "get_d2_settlement_summary") else {}
        d2_unsettled = float(d2_info.get("unsettled_d2_cash", 0.0) or 0.0)

        prod_summary = {
            "total_equity": round(broker_balance, 2),
            "ai_allocated_capital": round(prod_ai_cap, 2),
            "vault_reserve_cash": round(prod_vault, 2),
            "unsettled_d2_cash": round(d2_unsettled, 2),
            "settled_withdrawable_cash": round(max(0.0, prod_vault - d2_unsettled), 2),
            "next_d2_settlement_date": d2_info.get("next_settlement_date"),
            "free_cash": round(broker_balance, 2),
            "allocated_capital": 0.0,
            "buying_power": round(prod_ai_cap, 2),
            "cumulative_realized_pnl": 0.0,
            "positions_count": 0,
            "broker_balance": round(broker_balance, 2),
            "broker_connected": False,
        }

    active_summary = sim_summary if is_sim else prod_summary
    goal = db.get_active_withdrawal_goal()

    return {
        "success": True,
        "capital": active_summary,
        "simulation": sim_summary,
        "production": prod_summary,
        "active": active_summary,
        "active_goal": goal,
        "is_simulation": is_sim,
        "environment": "homologation" if is_sim else "production",
        "risk_params": {
            "stop_loss_percent": round(settings.STOP_LOSS_PERCENT * 100, 1),
            "trailing_trigger_percent": round(settings.TRAILING_STOP_TRIGGER_PERCENT * 100, 1),
            "trailing_distance_percent": round(settings.TRAILING_STOP_DISTANCE_PERCENT * 100, 1),
        },
    }


@router.get("/api/capital/transactions")
def get_capital_transactions(
    limit: int = 50,
    environment: Optional[str] = None,
    _user: dict[str, Any] = Depends(get_current_user),
):
    db = DatabaseManager()
    settings.reload_dynamic_config(db=db)
    is_sim = settings.SIMULATION_MODE
    current_env = "homologation" if is_sim else "production"
    target_env = environment.strip().lower() if environment else current_env
    capped_limit = max(1, min(int(limit), 500))
    txs = db.get_capital_transactions(limit=capped_limit, environment=target_env)
    return {"success": True, "transactions": txs, "environment": target_env}


@router.post("/api/capital/adjust-allocation")
def adjust_capital_allocation(
    req: AdjustAllocationRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user_interactive),
):
    if math.isnan(req.ai_capital) or math.isinf(req.ai_capital) or req.ai_capital < 0:
        return {"success": False, "message": "Valor de capital inválido."}

    new_cap = round(max(0.0, float(req.ai_capital)), 2)
    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    engine = getattr(dashboard_state, "_engine", None) if dashboard_state else None

    if engine is not None and hasattr(engine, "adjust_production_ceiling"):
        return engine.adjust_production_ceiling(new_cap, user["username"])

    db = DatabaseManager()
    if engine is not None and hasattr(engine, "get_production_summary"):
        prod_summary = engine.get_production_summary()
        total_equity = float(
            prod_summary.get("total_equity", prod_summary.get("broker_balance", 0.0)) or 0.0
        )
    else:
        total_equity = float(db.get_config("prod_broker_account_balance", "0.00") or 0.0)

    if new_cap > total_equity + 1e-9:
        return {
            "success": False,
            "message": (
                f"O teto de capital da IA (R$ {new_cap:.2f}) não pode ser maior que "
                f"o patrimônio na Clear (R$ {total_equity:.2f})."
            ),
        }

    vault = round(max(0.0, total_equity - new_cap), 2)
    db.set_config("prod_ai_allocated_capital", str(new_cap))
    db.set_config("prod_vault_reserve_cash", str(vault))

    if engine is not None and getattr(engine, "prod_portfolio", None):
        engine.prod_portfolio.adjust_ai_capital(new_cap)
        engine.prod_portfolio.vault_reserve_cash = vault

    db.record_capital_transaction(
        tx_type="AJUSTE_MANUAL",
        amount=new_cap,
        resulting_ai_capital=new_cap,
        resulting_vault=vault,
        details=f"Ajuste manual de teto da IA por {user['username']}",
        environment="production",
        is_simulated=False,
    )

    if engine is not None and hasattr(engine, "get_production_summary"):
        summary = engine.get_production_summary()
        return {
            "success": True,
            "capital": summary,
            "message": f"Teto de capital da IA ajustado para R$ {new_cap:.2f}.",
        }
    return {
        "success": True,
        "message": f"Teto de capital da IA ajustado para R$ {new_cap:.2f}.",
    }


@router.post("/api/capital/withdrawal-goal")
def create_withdrawal_goal(
    req: WithdrawalGoalRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user_interactive),
):
    db = DatabaseManager()
    amount = round(max(1.0, float(req.amount)), 2)
    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    engine = getattr(dashboard_state, "_engine", None) if dashboard_state else None

    if req.mode == "instant":
        if engine and hasattr(engine, "execute_smart_withdrawal"):
            return engine.execute_smart_withdrawal(amount, mode="instant")

        broker_balance = float(db.get_config("prod_broker_account_balance", "0.00") or 0.0)
        curr_ai_cap = float(db.get_config("prod_ai_allocated_capital", "0.00") or 0.0)
        new_ai_cap = round(max(0.0, curr_ai_cap - amount), 2)
        new_vault = round(max(0.0, broker_balance - new_ai_cap), 2)
        db.set_config("prod_ai_allocated_capital", str(new_ai_cap))
        db.set_config("prod_vault_reserve_cash", str(new_vault))
        db.record_capital_transaction(
            "RESGATE_IMEDIATO",
            amount,
            new_ai_cap,
            new_vault,
            f"Saque imediato por {user['username']}",
            environment="production",
            is_simulated=False,
        )
        return {
            "success": True,
            "message": f"R$ {amount:.2f} transferido para o cofre seguro",
        }

    if engine and hasattr(engine, "get_production_summary"):
        prod_sum = engine.get_production_summary()
        total_eq = float(prod_sum.get("total_equity", 0.0) or 0.0)
        curr_ai_cap = float(prod_sum.get("ai_allocated_capital", 0.0) or 0.0)
    else:
        total_eq = float(db.get_config("prod_broker_account_balance", "0.00") or 0.0)
        curr_ai_cap = float(db.get_config("prod_ai_allocated_capital", "0.00") or 0.0)
        curr_ai_cap = min(curr_ai_cap, total_eq)

    base_equity = curr_ai_cap if curr_ai_cap > 0 else total_eq
    target_eq = (
        float(req.target_equity)
        if req.target_equity and req.target_equity > base_equity
        else base_equity + amount
    )

    gid = db.create_withdrawal_goal(
        amount=amount,
        mode="on_profit",
        target_equity=target_eq,
        base_equity=base_equity,
        notes=(
            f"Meta de lucro: R$ {amount:.2f} acima do teto da IA "
            f"(Alvo: R$ {target_eq:.2f}) criada por {user['username']}"
        ),
    )

    return {
        "success": True,
        "goal_id": gid,
        "amount": amount,
        "base_equity": round(base_equity, 2),
        "target_equity": round(target_eq, 2),
        "message": (
            f"Meta de saque de lucro de R$ {amount:.2f} configurada para "
            f"quando o patrimônio atingir R$ {target_eq:.2f}"
        ),
    }


@router.post("/api/capital/withdrawal-goal/cancel")
def cancel_withdrawal_goal(_user: dict[str, Any] = Depends(get_current_user_interactive)):
    db = DatabaseManager()
    cancelled = db.cancel_active_withdrawal_goal()
    return {"success": True, "cancelled": cancelled}


def _normalize_risk_pct_input(raw_value: float, min_dec: float, max_dec: float) -> float:
    """
    Normaliza percentual de risco enviado por sliders (0.5..25.0 -> 0.005..0.25)
    ou fração decimal direta (< 0.5).
    """
    val = float(raw_value)
    if val >= 0.5:
        val = val / 100.0
    return max(min_dec, min(max_dec, val))


@router.post("/api/config/risk")
def update_risk_config(
    req: RiskConfigRequest,
    request: Request,
    _user: dict[str, Any] = Depends(get_current_user_interactive),
):
    db = DatabaseManager()
    settings.reload_dynamic_config(db=db)

    new_sl = settings.STOP_LOSS_PERCENT
    new_trig = settings.TRAILING_STOP_TRIGGER_PERCENT
    new_dist = settings.TRAILING_STOP_DISTANCE_PERCENT

    if req.stop_loss_percent is not None:
        new_sl = _normalize_risk_pct_input(req.stop_loss_percent, 0.005, 0.50)

    if req.trailing_trigger_percent is not None:
        new_trig = _normalize_risk_pct_input(req.trailing_trigger_percent, 0.005, 0.30)

    if req.trailing_distance_percent is not None:
        new_dist = _normalize_risk_pct_input(req.trailing_distance_percent, 0.005, 0.20)

    if new_dist >= new_trig:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "A distância do Trailing Stop deve ser estritamente menor "
                "que o gatilho de ativação."
            ),
        )

    settings.STOP_LOSS_PERCENT = new_sl
    settings.TRAILING_STOP_TRIGGER_PERCENT = new_trig
    settings.TRAILING_STOP_DISTANCE_PERCENT = new_dist

    db.set_config("stop_loss_percent", str(new_sl))
    db.set_config("trailing_trigger_percent", str(new_trig))
    db.set_config("trailing_distance_percent", str(new_dist))

    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    if (
        dashboard_state
        and getattr(dashboard_state, "_engine", None)
        and getattr(dashboard_state._engine, "risk_manager", None)
    ):
        dashboard_state._engine.risk_manager.update_parameters(
            stop_loss_percent=settings.STOP_LOSS_PERCENT,
            trailing_trigger_percent=settings.TRAILING_STOP_TRIGGER_PERCENT,
            trailing_distance_percent=settings.TRAILING_STOP_DISTANCE_PERCENT,
        )

    return {
        "success": True,
        "stop_loss_percent": round(settings.STOP_LOSS_PERCENT * 100, 1),
        "trailing_trigger_percent": round(settings.TRAILING_STOP_TRIGGER_PERCENT * 100, 1),
        "trailing_distance_percent": round(settings.TRAILING_STOP_DISTANCE_PERCENT * 100, 1),
        "message": "Parâmetros de risco atualizados com sucesso",
    }


@router.post("/api/config/environment")
def update_environment(
    req: EnvironmentRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user_interactive),
):
    target_env = req.environment.strip().lower()
    if target_env not in ("homologation", "production"):
        raise HTTPException(
            status_code=400,
            detail="Ambiente inválido. Escolha 'homologation' ou 'production'.",
        )

    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    if dashboard_state and getattr(dashboard_state, "_engine", None):
        ok = dashboard_state._engine.switch_environment(
            target_env,
            user_name=user.get("username", "Usuário"),
        )
        if not ok:
            raise HTTPException(
                status_code=500,
                detail="Falha ao alternar ambiente de execução.",
            )
    else:
        db = DatabaseManager()
        db.set_config("environment", target_env)
        settings.SIMULATION_MODE = target_env == "homologation"
        settings.BROKER_MODE = "simulator" if settings.SIMULATION_MODE else "mt5"

    return {
        "success": True,
        "environment": target_env,
        "message": f"Ambiente alternado com sucesso para {target_env.upper()}",
    }


@router.post("/api/config/simulator/capital")
def set_simulator_capital(
    req: SimulatorCapitalRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user_interactive),
):
    if math.isnan(req.capital) or math.isinf(req.capital) or req.capital <= 0:
        raise HTTPException(status_code=400, detail="Capital inicial inválido.")

    cap = round(max(1.0, float(req.capital)), 2)
    db = DatabaseManager()
    db.set_config("sim_initial_capital", str(cap))
    settings.CAPITAL_INICIAL = cap

    if req.reset_positions:
        db.reset_operational_data(environment="homologation")

    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    engine = getattr(dashboard_state, "_engine", None) if dashboard_state else None
    if engine is not None:
        if hasattr(engine, "reset_homologation"):
            engine.reset_homologation(cap, reset_positions=req.reset_positions)
        else:
            sim_broker = getattr(engine, "sim_broker", None) or (
                engine.broker if isinstance(getattr(engine, "broker", None), SimulatorBroker) else None
            )
            if isinstance(sim_broker, SimulatorBroker):
                sim_broker.set_initial_capital(cap, reset_positions=req.reset_positions)
            sim_port = getattr(engine, "sim_portfolio", None)
            if sim_port is not None:
                if req.reset_positions:
                    engine.sim_portfolio = Portfolio(initial_capital=cap)
                else:
                    sim_port.initial_capital = cap
                    sim_port.adjust_ai_capital(cap)
    else:
        sim = SimulatorBroker(initial_balance=cap)
        if not req.reset_positions:
            sim.connect()
        sim.set_initial_capital(cap, reset_positions=req.reset_positions)

    db.record_capital_transaction(
        "APORTE",
        cap,
        cap,
        0.0,
        f"Capital de simulação redefinido por {user['username']}",
        environment="homologation",
        is_simulated=True,
    )
    return {
        "success": True,
        "capital": cap,
        "message": f"Capital inicial do simulador definido para R$ {cap:.2f}",
    }


@router.post("/api/config/simulator/reset")
def reset_simulator(
    request: Request,
    user: dict[str, Any] = Depends(get_current_user_interactive),
):
    db = DatabaseManager()
    db.reset_operational_data(environment="homologation")

    cap = float(
        db.get_config("sim_initial_capital", str(settings.CAPITAL_INICIAL))
        or settings.CAPITAL_INICIAL
    )
    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    engine = getattr(dashboard_state, "_engine", None) if dashboard_state else None

    if engine is not None and hasattr(engine, "reset_homologation"):
        engine.reset_homologation(cap, reset_positions=True)
    elif engine is not None:
        sim_broker = getattr(engine, "sim_broker", None) or (
            engine.broker if isinstance(getattr(engine, "broker", None), SimulatorBroker) else None
        )
        if isinstance(sim_broker, SimulatorBroker):
            sim_broker.set_initial_capital(cap, reset_positions=True)
        if hasattr(engine, "sim_portfolio"):
            engine.sim_portfolio = Portfolio(initial_capital=cap)
    else:
        sim = SimulatorBroker(initial_balance=cap)
        sim.set_initial_capital(cap, reset_positions=True)

    db.record_capital_transaction(
        "AJUSTE_MANUAL",
        cap,
        cap,
        0.0,
        f"Simulação resetada do zero por {user['username']}",
        environment="homologation",
        is_simulated=True,
    )
    return {
        "success": True,
        "capital": cap,
        "message": "Simulação e histórico de homologação resetados com sucesso!",
    }


class GuideChatRequest(BaseModel):
    message: str


@router.get("/api/guide/glossary")
def get_guide_glossary(_user: dict[str, Any] = Depends(get_current_user)):
    from core.knowledge_base import AssistantKnowledgeBase

    return {
        "success": True,
        "glossary": AssistantKnowledgeBase.get_all_glossary(),
    }


@router.post("/api/guide/chat")
def guide_chat(
    req: GuideChatRequest,
    _user: dict[str, Any] = Depends(get_current_user_interactive),
):
    if not req.message or not req.message.strip():
        raise HTTPException(status_code=400, detail="Mensagem não pode ser vazia.")
    from core.knowledge_base import AssistantKnowledgeBase

    result = AssistantKnowledgeBase.answer_question(req.message)
    return {
        "success": True,
        "answer": result["answer"],
        "suggestions": result.get("suggestions", []),
    }


class BrokerBalanceRequest(BaseModel):
    balance: float


@router.post("/api/config/production/broker_balance")
def set_production_broker_balance(
    req: BrokerBalanceRequest,
    request: Request,
    user: dict[str, Any] = Depends(get_current_user_interactive),
):
    if math.isnan(req.balance) or math.isinf(req.balance) or req.balance < 0:
        raise HTTPException(status_code=400, detail="Saldo inválido.")

    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    engine = getattr(dashboard_state, "_engine", None) if dashboard_state else None

    if (
        engine is not None
        and getattr(engine, "prod_broker", None) is not None
        and getattr(engine.prod_broker, "is_connected", False)
    ):
        return {
            "success": False,
            "message": (
                "Conexão real com MT5 ativa — o saldo é sincronizado diretamente "
                "da corretora e não pode ser sobrescrito manualmente."
            ),
        }

    db = DatabaseManager()
    bal = round(max(0.0, float(req.balance)), 2)
    curr_ai_cap = max(
        0.0,
        float(db.get_config("prod_ai_allocated_capital", "0.00") or 0.0),
    )
    # NUNCA aumenta automaticamente o teto da IA: preserva o menor valor e envia excedente ao cofre
    clamped_ai_cap = round(min(curr_ai_cap, bal), 2)
    new_vault = round(max(0.0, bal - clamped_ai_cap), 2)

    db.set_config("prod_broker_account_balance", str(bal))
    db.set_config("prod_ai_allocated_capital", str(clamped_ai_cap))
    db.set_config("prod_vault_reserve_cash", str(new_vault))

    if engine is not None and getattr(engine, "prod_portfolio", None) is not None:
        engine.prod_portfolio.free_cash = bal
        engine.prod_portfolio.ai_allocated_capital = clamped_ai_cap
        engine.prod_portfolio.vault_reserve_cash = new_vault

    db.record_capital_transaction(
        tx_type="AJUSTE_MANUAL",
        amount=bal,
        resulting_ai_capital=clamped_ai_cap,
        resulting_vault=new_vault,
        details=f"Saldo manual de referência atualizado por {user['username']}",
        environment="production",
        is_simulated=False,
    )

    return {
        "success": True,
        "broker_balance": bal,
        "ai_capital": clamped_ai_cap,
        "vault_reserve_cash": new_vault,
        "message": (
            f"Saldo de referência atualizado para R$ {bal:,.2f} "
            f"(Teto IA mantido em R$ {clamped_ai_cap:,.2f} | Cofre: R$ {new_vault:,.2f})."
        ),
    }
