import os
import json
import logging
import sqlite3
import asyncio
from typing import Any, Optional
import requests as sync_requests
from pydantic import BaseModel
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request, Response, HTTPException, status, Depends
from auth import (
    get_current_user,
    get_current_user_optional,
    rate_limiter,
    get_client_ip,
    extract_session_token,
    PasswordManager,
    MAX_SESSION_SECONDS,
    MAX_IDLE_SECONDS,
)
from data.database import DatabaseManager

logger = logging.getLogger('cortex.dashboard.routes')

router = APIRouter()

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cortex.db")
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "cortex.log")
SIMULATOR_STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "simulator_state.json")
VPS_LOG_PATH = "/LOGS-PROJETOS/cortex-ia/systemd-out.log"
VPS_DB_LOG_PATH = "/LOGS-PROJETOS/cortex-ia/logs.db"

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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
            detail=f"Muitas tentativas falhas. Bloqueado temporariamente por {remaining} segundos."
        )

    db = DatabaseManager()
    user = db.get_user_by_username(req.username)
    if not user:
        attempts, lockout = rate_limiter.record_failure(ip)
        if lockout > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Limite de tentativas excedido. Bloqueado por {lockout // 60} minutos."
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha incorretos."
        )

    valid = PasswordManager.verify_password(req.password, user["password_hash"], user["salt"])
    if not valid:
        attempts, lockout = rate_limiter.record_failure(ip)
        if lockout > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Limite de tentativas excedido. Bloqueado por {lockout // 60} minutos."
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha incorretos."
        )

    rate_limiter.reset(ip)

    if user["must_change_password"]:
        return {
            "success": True,
            "must_change_password": True,
            "username": user["username"],
            "message": "Primeiro acesso detectado. É obrigatório alterar o usuário e a senha."
        }

    token = db.create_session(
        user_id=user["id"],
        ip_address=ip,
        user_agent=request.headers.get("User-Agent", ""),
        max_session_seconds=MAX_SESSION_SECONDS
    )

    response.set_cookie(
        key="cortex_session",
        value=token,
        max_age=MAX_SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        path="/"
    )

    return {
        "success": True,
        "must_change_password": False,
        "token": token,
        "user": {
            "id": user["id"],
            "username": user["username"]
        }
    }

@router.post("/api/auth/first-login")
def auth_first_login(req: FirstLoginRequest, request: Request, response: Response):
    ip = get_client_ip(request)
    db = DatabaseManager()
    user = db.get_user_by_username(req.current_username)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas.")

    if not PasswordManager.verify_password(req.current_password, user["password_hash"], user["salt"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Senha atual incorreta.")

    if not user["must_change_password"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Este usuário já concluiu o primeiro acesso.")

    new_user = req.new_username.strip()
    new_pass = req.new_password.strip()

    if len(new_user) < 3:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="O nome de usuário deve ter pelo menos 3 caracteres.")

    if len(new_pass) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A nova senha deve ter pelo menos 6 caracteres.")

    if new_user.lower() == "admin" and new_pass.lower() == "admin":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Você não pode utilizar as credenciais padrão de fábrica.")

    if new_user.lower() != user["username"].lower():
        existing = db.get_user_by_username(new_user)
        if existing and existing["id"] != user["id"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Este nome de usuário já está em uso.")

    db.update_user_credentials(user["id"], new_user, new_pass)
    db.revoke_all_user_sessions(user["id"])

    token = db.create_session(
        user_id=user["id"],
        ip_address=ip,
        user_agent=request.headers.get("User-Agent", ""),
        max_session_seconds=MAX_SESSION_SECONDS
    )

    response.set_cookie(
        key="cortex_session",
        value=token,
        max_age=MAX_SESSION_SECONDS,
        httponly=True,
        samesite="strict",
        path="/"
    )

    return {
        "success": True,
        "token": token,
        "user": {
            "id": user["id"],
            "username": new_user
        },
        "message": "Credenciais atualizadas com sucesso!"
    }

@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    token = extract_session_token(request)
    if token:
        db = DatabaseManager()
        db.revoke_session(token)
    response.delete_cookie(key="cortex_session", path="/")
    return {"success": True, "message": "Logout realizado com sucesso."}

@router.get("/api/auth/me")
def auth_me(user: dict[str, Any] = Depends(get_current_user)):
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": user["created_at"]
    }

@router.post("/api/auth/heartbeat")
def auth_heartbeat(user: dict[str, Any] = Depends(get_current_user)):
    return {"status": "active", "max_idle_seconds": MAX_IDLE_SECONDS}

@router.get("/api/status")
def get_status(request: Request, user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    status = {
        "balance": 0.0,
        "equity": 0.0,
        "positions": [],
        "recent_decisions": [],
        "market_status": "DESCONHECIDO",
        "watchlist": [],
    }

    dashboard_state = getattr(request.app.state, "dashboard_state", None)
    if dashboard_state is not None:
        try:
            live = dashboard_state.get_state()
            port = live.get("portfolio", {})
            if port:
                status["balance"] = port.get("free_cash", 0.0)
                status["equity"] = port.get("total_value", 0.0)
                status["positions"] = port.get("positions", [])
            status["market_status"] = live.get("market_status", "DESCONHECIDO")
            status["watchlist"] = live.get("watchlist", [])
            status["recent_decisions"] = live.get("recent_decisions", [])
        except Exception as exc:
            logger.warning("Falha ao ler dashboard_state em memória: %s", exc)

    if status["equity"] == 0.0 and os.path.exists(SIMULATOR_STATE_PATH):
        try:
            with open(SIMULATOR_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
                status["balance"] = state.get("balance", 0.0)
                positions = state.get("positions", {})
                
                equity = status["balance"]
                if not status["positions"]:
                    for p in positions.values():
                        p_val = p.get("current_price", p.get("entry_price", 0.0)) * p.get("quantity", 0)
                        equity += p_val
                        status["positions"].append(p)
                status["equity"] = equity
        except Exception as exc:
            logger.warning('Falha ao ler simulator_state.json: %s', exc)

    if not status["recent_decisions"] and os.path.exists(DB_PATH):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ticker, action, confidence, reasoning, timestamp 
                FROM ai_decisions 
                ORDER BY timestamp DESC 
                LIMIT 20
            """)
            rows = cursor.fetchall()
            for row in rows:
                status["recent_decisions"].append(dict(row))
        except Exception as exc:
            logger.warning('Falha ao ler decisões do DB: %s', exc)
        finally:
            if conn is not None:
                conn.close()

    return status

@router.get("/api/production_balance")
async def get_production_balance(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
    sim_mode = os.getenv("SIMULATION_MODE", "true").lower() in ("true", "1", "yes", "sim")
    if sim_mode:
        return {"status": "simulator", "balance": 0.0}

    try:
        resp = await asyncio.to_thread(sync_requests.get, "http://127.0.0.1:5000/account", timeout=1)
        if resp.status_code == 200:
            data = resp.json()
            return {"status": "live", "balance": data.get("balance", 0.0), "equity": data.get("equity", 0.0)}
    except Exception:
        pass
    return {"status": "offline", "balance": 0.0}

@router.get("/api/trades")
def get_trades(limit: int = 50, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    if not os.path.exists(DB_PATH):
        return []
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, ticker, action, quantity, price, total_value, stop_loss, timestamp, reasoning, is_simulated
            FROM trades 
            ORDER BY timestamp DESC 
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning('Falha ao ler trades do DB: %s', exc)
        return []
    finally:
        if conn is not None:
            conn.close()

@router.get("/api/equity_curve")
def get_equity_curve(limit: int = 30, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    if not os.path.exists(DB_PATH):
        return []
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, total_equity, free_cash, allocated_capital, pnl_percent 
            FROM daily_reports 
            ORDER BY date ASC 
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning('Falha ao ler relatórios diários do DB: %s', exc)
        return []
    finally:
        if conn is not None:
            conn.close()

@router.get("/api/news")
def get_news(limit: int = 20, user: dict[str, Any] = Depends(get_current_user)) -> list[dict[str, Any]]:
    if not os.path.exists(DB_PATH):
        return []
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, title, source, url, ticker, sentiment, published_at, scraped_at
            FROM news_items
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning('Falha ao ler notícias do DB: %s', exc)
        return []
    finally:
        if conn is not None:
            conn.close()

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    token = websocket.cookies.get("cortex_session")
    if not token and "token" in websocket.query_params:
        token = websocket.query_params["token"]

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    db = DatabaseManager()
    session = db.get_valid_session(token, max_idle_seconds=MAX_IDLE_SECONDS)
    if not session:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)
