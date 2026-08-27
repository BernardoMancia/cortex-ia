import os
import json
import logging
import sqlite3
import asyncio
from typing import Any
import requests as sync_requests
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request

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

@router.get("/api/status")
def get_status(request: Request) -> dict[str, Any]:
    """Returns the current portfolio status, watchlist and recent decisions."""
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
async def get_production_balance() -> dict[str, Any]:
    """Fetches real account balance from MT5 Bridge if in production."""
    sim_mode = os.getenv("SIMULATION_MODE", "true").lower() in ("true", "1", "yes", "sim")
    if sim_mode:
        return {"status": "simulator", "balance": 0.0}

    try:
        resp = await asyncio.to_thread(sync_requests.get, "http://127.0.0.1:5000/account", timeout=1)
        if resp.status_code == 200:
            data = resp.json()
            return {"status": "ok", "balance": data.get("balance", 0.0)}
    except Exception as e:
        logger.debug('Falha ao buscar saldo de produção MT5: %s', e)
    return {"status": "offline", "balance": 0.0}

def _resolve_log_source() -> tuple[str | None, str]:
    """Retorna (caminho_arquivo, tipo) para streaming de logs."""
    if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 0:
        return LOG_PATH, "file"
    if os.path.exists(VPS_LOG_PATH) and os.path.getsize(VPS_LOG_PATH) > 0:
        return VPS_LOG_PATH, "file"
    if os.path.exists(VPS_DB_LOG_PATH):
        return VPS_DB_LOG_PATH, "sqlite"
    return None, "none"

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    
    log_src, src_type = _resolve_log_source()
    last_position = 0
    last_db_id = 0

    if src_type == "file" and log_src:
        try:
            last_position = max(0, os.path.getsize(log_src) - 10000)
        except OSError:
            last_position = 0
        
    try:
        while True:
            log_src, src_type = _resolve_log_source()
            if src_type == "file" and log_src and os.path.exists(log_src):
                with open(log_src, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(last_position)
                    new_lines = f.readlines()
                    last_position = f.tell()
                    
                    for line in new_lines:
                        if line.strip():
                            await websocket.send_text(line.strip())
            elif src_type == "sqlite" and log_src and os.path.exists(log_src):
                try:
                    conn = sqlite3.connect(log_src)
                    cursor = conn.cursor()
                    if last_db_id == 0:
                        cursor.execute("SELECT id, timestamp, level, source, message FROM logs ORDER BY id DESC LIMIT 20")
                        rows = cursor.fetchall()[::-1]
                    else:
                        cursor.execute("SELECT id, timestamp, level, source, message FROM logs WHERE id > ? ORDER BY id ASC LIMIT 50", (last_db_id,))
                        rows = cursor.fetchall()
                    
                    for r in rows:
                        last_db_id = max(last_db_id, r[0])
                        formatted = f"[{r[1][:19]}] [{r[2]}] [{r[3]}] {r[4]}"
                        await websocket.send_text(formatted)
                    conn.close()
                except Exception as exc:
                    logger.debug("Erro ao consultar logs SQLite: %s", exc)
            
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass

@router.get("/api/trades")
def get_trades(days: int = 30) -> dict[str, Any]:
    """Retorna o histórico de trades dos últimos dias."""
    from data.database import DatabaseManager
    db = DatabaseManager()
    try:
        trades = db.get_trade_history(days=days)
        return {"status": "ok", "trades": trades}
    except Exception as exc:
        logger.error("Erro ao buscar trades: %s", exc)
        return {"status": "error", "message": str(exc)}

@router.get("/api/equity_curve")
def get_equity_curve(days: int = 30) -> dict[str, Any]:
    """Retorna a curva de patrimônio e dados diários."""
    from data.database import DatabaseManager
    db = DatabaseManager()
    try:
        reports = db.get_report_history(days=days)
        reports.sort(key=lambda r: r['date'])
        return {"status": "ok", "curve": reports}
    except Exception as exc:
        logger.error("Erro ao buscar equity curve: %s", exc)
        return {"status": "error", "message": str(exc)}

@router.get("/api/performance")
def get_performance() -> dict[str, Any]:
    """Retorna métricas consolidadas de performance."""
    from data.database import DatabaseManager
    db = DatabaseManager()
    try:
        metrics = db.get_performance_metrics()
        return {"status": "ok", "metrics": metrics}
    except Exception as exc:
        logger.error("Erro ao buscar performance: %s", exc)
        return {"status": "error", "message": str(exc)}

@router.get("/api/news")
def get_news(limit: int = 50, ticker: str | None = None) -> dict[str, Any]:
    """Retorna últimas notícias, opcionalmente filtradas por ticker."""
    from data.database import DatabaseManager
    db = DatabaseManager()
    try:
        if ticker:
            news = db.get_news_by_ticker(ticker, limit)
        else:
            news = db.get_recent_news(limit)
        return {"status": "ok", "news": news}
    except Exception as exc:
        logger.error("Erro ao buscar notícias: %s", exc)
        return {"status": "error", "message": str(exc)}

@router.get("/api/decisions/{ticker}")
def get_decisions(ticker: str, days: int = 30, limit: int = 100) -> dict[str, Any]:
    """Retorna histórico de decisões para um ticker específico."""
    from data.database import DatabaseManager
    db = DatabaseManager()
    try:
        decisions = db.get_decisions_history(ticker=ticker, days=days, limit=limit)
        return {"status": "ok", "decisions": decisions}
    except Exception as exc:
        logger.error("Erro ao buscar decisões para %s: %s", ticker, exc)
        return {"status": "error", "message": str(exc)}
