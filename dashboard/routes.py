import os
import json
import logging
import sqlite3
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import Any
import asyncio
import requests as sync_requests

logger = logging.getLogger('cortex.dashboard.routes')

router = APIRouter()

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "cortex.db")
LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "cortex.log")
SIMULATOR_STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "simulator_state.json")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@router.get("/api/status")
def get_status() -> dict[str, Any]:
    """Returns the current portfolio status and recent decisions."""
    status = {
        "balance": 0.0,
        "equity": 0.0,
        "positions": [],
        "recent_decisions": []
    }
    
    # Try reading from simulator_state.json
    if os.path.exists(SIMULATOR_STATE_PATH):
        try:
            with open(SIMULATOR_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
                status["balance"] = state.get("balance", 0.0)
                positions = state.get("positions", {})
                
                equity = status["balance"]
                for p in positions.values():
                    p_val = p.get("current_price", p.get("entry_price", 0.0)) * p.get("quantity", 0)
                    equity += p_val
                    status["positions"].append(p)
                status["equity"] = equity
        except Exception as exc:
            logger.warning('Falha ao ler simulator_state.json: %s', exc)

    # Read recent decisions from DB
    if os.path.exists(DB_PATH):
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
    """Fetches real account balance from MT5 Bridge."""
    try:
        resp = await asyncio.to_thread(sync_requests.get, "http://127.0.0.1:5000/account", timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            return {"status": "ok", "balance": data.get("balance", 0.0)}
    except Exception as e:
        logger.warning('Falha ao buscar saldo de produção: %s', e)
    return {"status": "error", "balance": 0.0}

@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await websocket.accept()
    
    # Initial read
    last_position = 0
    if os.path.exists(LOG_PATH):
        last_position = max(0, os.path.getsize(LOG_PATH) - 10000) # Read last 10KB
        
    try:
        while True:
            if os.path.exists(LOG_PATH):
                with open(LOG_PATH, "r", encoding="utf-8") as f:
                    f.seek(last_position)
                    new_lines = f.readlines()
                    last_position = f.tell()
                    
                    if new_lines:
                        for line in new_lines:
                            if line.strip():
                                await websocket.send_text(line.strip())
            
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass

# ── Endpoints de Desempenho e Histórico (Fase A) ──

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
        # Ordenar crescente para o gráfico
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
