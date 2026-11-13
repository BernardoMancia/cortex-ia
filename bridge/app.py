"""
MT5 Bridge API — Córtex IA.

Serviço FastAPI isolado para comunicação IPC com o terminal MetaTrader 5,
com autenticação por chave de API, serialização thread-safe de chamadas C-extension
e suporte completo à execução na B3 (seleção automática de símbolos, cancelamento de ordens
pendentes e modificação de SL/TP).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hmac
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

try:
    from dotenv import load_dotenv

    _local_env = Path(__file__).resolve().parent / ".env"
    _root_env = Path(__file__).resolve().parent.parent / ".env"
    if _local_env.exists():
        load_dotenv(dotenv_path=_local_env)
    elif _root_env.exists():
        load_dotenv(dotenv_path=_root_env)
except ImportError:
    pass

_rpc_dir_env = os.getenv("CORTEX_MQL5_RPC_DIR", "").strip()
if _rpc_dir_env:
    from bridge.mql5_rpc import MQL5FileRPCBackend

    mt5 = MQL5FileRPCBackend(_rpc_dir_env)  # type: ignore[assignment]
else:
    try:
        import MetaTrader5 as mt5
    except ImportError:
        mt5 = None  # type: ignore[assignment]

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s",
)
logger = logging.getLogger("mt5_bridge")

_mt5_lock = threading.Lock()
_bg_connect_lock = threading.Lock()
_bg_connect_running = False
_last_bg_connect_ts = 0.0
_LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


def _safe_int(val: Any, default: int = 0) -> int:
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return int(val)
    return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    if isinstance(val, bool):
        return default
    if isinstance(val, (int, float)):
        return float(val)
    return default


def _safe_bool(val: Any, default: bool = True) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    return default


def _ensure_mt5_module() -> None:
    """Garante que o pacote MetaTrader5 esteja disponível no ambiente."""
    if mt5 is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Módulo MetaTrader5 não disponível neste sistema operacional.",
        )


def _require_connected_broker() -> None:
    """
    Fast-fail HTTP 503 sem adquirir `_mt5_lock` quando o heartbeat vivo do EA
    (ou o último erro IPC `-10004`) indica que o terminal aguarda login na corretora.
    """
    _ensure_mt5_module()
    hb_fn = getattr(mt5, "heartbeat_info", None)
    hb_raw = hb_fn() if callable(hb_fn) else None
    if isinstance(hb_raw, dict):
        if not bool(hb_raw.get("connected", False)):
            _trigger_background_connect(min_interval=12.0)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MT5 aguardando autenticação na corretora (waiting_broker_auth).",
            )
        return
    if type(mt5).__name__ == "MQL5FileRPCBackend" and hb_raw is None:
        _trigger_background_connect(min_interval=12.0)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CortexBridgeEA iniciando ou aguardando heartbeat recente.",
        )

    err_fn = getattr(mt5, "last_error", None)
    err = err_fn() if callable(err_fn) else None
    if isinstance(err, tuple) and len(err) >= 1 and err[0] == -10004:
        _trigger_background_connect(min_interval=12.0)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MT5 aguardando autenticação na corretora (waiting_broker_auth).",
        )


def _raise_if_rpc_unavailable() -> None:
    """Converte erros de autenticação/timeout RPC (-10004/-10005/-10001) em HTTP 503."""
    err_fn = getattr(mt5, "last_error", None)
    err = err_fn() if callable(err_fn) else None
    if isinstance(err, tuple) and len(err) >= 1 and err[0] in (-10004, -10005, -10001):
        _trigger_background_connect(min_interval=12.0)
        detail = (
            "MT5 aguardando autenticação na corretora (waiting_broker_auth)."
            if err[0] == -10004
            else f"MT5 indisponível via RPC: {err}"
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )


def verify_bridge_key(request: Request) -> None:
    """Valida o header X-Bridge-Api-Key em todos os endpoints da Bridge."""
    configured_key = os.getenv("BRIDGE_API_KEY", "").strip()
    provided_key = (request.headers.get("X-Bridge-Api-Key") or "").strip()
    client_host = request.client.host if request.client else ""

    if configured_key:
        if not provided_key or not hmac.compare_digest(
            provided_key.encode("utf-8"),
            configured_key.encode("utf-8"),
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Acesso negado: X-Bridge-Api-Key inválida ou ausente.",
            )
        return

    if client_host not in _LOCAL_HOSTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Acesso remoto bloqueado: configure a variável de ambiente "
                "BRIDGE_API_KEY para conexões fora de localhost."
            ),
        )


def _connect_mt5_from_env() -> bool:
    """Inicializa e autentica no terminal MT5 usando exclusivamente variáveis locais."""
    if mt5 is None:
        logger.warning("Pacote MetaTrader5 indisponível neste ambiente.")
        return False

    mt5_path = os.getenv("MT5_PATH", "").strip()
    mt5_login_raw = os.getenv("MT5_LOGIN", "").strip()
    mt5_password = os.getenv("MT5_PASSWORD", "")
    mt5_server = os.getenv("MT5_SERVER", "").strip()

    try:
        login_val = int(mt5_login_raw) if mt5_login_raw else 0
    except ValueError:
        login_val = 0

    with _mt5_lock:
        initialized = False
        if mt5_path and os.path.isfile(mt5_path):
            init_kwargs: dict[str, Any] = {"path": mt5_path, "timeout": 15000}
            if login_val and mt5_password and mt5_server:
                init_kwargs["login"] = login_val
                init_kwargs["password"] = mt5_password
                init_kwargs["server"] = mt5_server
            if mt5.initialize(**init_kwargs) or mt5.initialize(path=mt5_path, timeout=15000):
                initialized = True
        elif mt5.initialize():
            initialized = True

        if initialized:
            info = mt5.account_info()
            if login_val and mt5_password and mt5_server:
                current_login = int(getattr(info, "login", 0) or 0) if info is not None else 0
                if info is None or (current_login != 0 and current_login != login_val):
                    try:
                        mt5.login(
                            login=login_val,
                            password=mt5_password,
                            server=mt5_server,
                        )
                        info = mt5.account_info()
                    except Exception as exc:
                        logger.warning("Aviso na autenticação explícita do MT5: %s", exc)

            if info is not None:
                logger.info(
                    "MT5 conectado e autenticado com sucesso (servidor: %s).",
                    getattr(info, "server", "desconhecido"),
                )
                return True
            logger.info("MT5 inicializado via IPC nativo (aguardando autenticação da conta).")
            return True

        logger.warning(
            "MT5 não inicializado automaticamente (%s). Verifique se o terminal está aberto.",
            mt5.last_error(),
        )
        return False


def _trigger_background_connect(min_interval: float = 12.0) -> None:
    """Dispara reconexão IPC em thread daemon sem bloquear o event loop HTTP."""
    global _bg_connect_running, _last_bg_connect_ts
    now = time.monotonic()
    with _bg_connect_lock:
        if _bg_connect_running or (min_interval > 0.0 and (now - _last_bg_connect_ts) < min_interval):
            return
        _bg_connect_running = True
        _last_bg_connect_ts = now

    def _worker() -> None:
        global _bg_connect_running
        try:
            _connect_mt5_from_env()
        finally:
            with _bg_connect_lock:
                _bg_connect_running = False

    threading.Thread(target=_worker, name="mt5-ipc-connect", daemon=True).start()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if not os.getenv("BRIDGE_API_KEY", "").strip():
        logger.warning(
            "AVISO DE SEGURANÇA: BRIDGE_API_KEY não definida. "
            "Apenas conexões locais (127.0.0.1/::1) serão aceitas."
        )
    logger.info("Iniciando conexão com o MetaTrader 5...")
    mt5_path = os.getenv("MT5_PATH", "").strip()
    if _rpc_dir_env or callable(getattr(mt5, "heartbeat_info", None)) or (mt5_path and os.path.isfile(mt5_path)):
        _trigger_background_connect(min_interval=0.0)
    else:
        await asyncio.to_thread(_connect_mt5_from_env)
    yield
    if mt5 is not None and _mt5_lock.acquire(timeout=2.0):
        try:
            mt5.shutdown()
        finally:
            _mt5_lock.release()
    logger.info("MT5 Bridge encerrada.")


app = FastAPI(
    title="MT5 Bridge API — Córtex IA",
    lifespan=lifespan,
    dependencies=[Depends(verify_bridge_key)],
)


class ConnectRequest(BaseModel):
    """Payload vazio/ignorado — credenciais e binário vêm exclusivamente do ambiente local."""

    model_config = ConfigDict(extra="ignore")


class OrderRequest(BaseModel):
    """Payload de validação, envio e cancelamento de ordem MT5."""

    action: int
    symbol: str = ""
    volume: float = 0.0
    type: int = 0
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    deviation: int = 20
    order: int = 0
    position: int = 0
    magic: int = 234000
    comment: str = ""
    type_time: int = 0
    type_filling: int = 2


class ModifySLRequest(BaseModel):
    """Payload para modificação de Stop-Loss / Take-Profit de uma posição aberta."""

    position: int
    symbol: str
    sl: float
    tp: float = 0.0
    magic: int = 234000


def _build_mt5_order_dict(req: OrderRequest) -> dict[str, Any]:
    """Constrói o dicionário compatível com mt5.order_check / mt5.order_send."""
    sltp_action = _safe_int(getattr(mt5, "TRADE_ACTION_SLTP", 6), 6)
    remove_action = _safe_int(getattr(mt5, "TRADE_ACTION_REMOVE", 8), 8)

    if req.action == sltp_action:
        return {
            "action": req.action,
            "symbol": req.symbol,
            "position": int(req.position),
            "sl": float(req.sl),
            "tp": float(req.tp),
            "magic": int(req.magic),
            "comment": req.comment or "Cortex SL_MODIFY",
        }

    if req.action == remove_action:
        remove_payload: dict[str, Any] = {
            "action": req.action,
            "order": int(req.order),
            "magic": int(req.magic),
            "comment": req.comment or "Cortex ORDER_CANCEL",
        }
        if req.symbol:
            remove_payload["symbol"] = req.symbol
        return remove_payload

    payload = req.model_dump(exclude={"position", "order"})
    if req.position > 0:
        payload["position"] = int(req.position)
    if req.order > 0:
        payload["order"] = int(req.order)
    return payload


@app.get("/")
@app.get("/health")
def health_check() -> dict[str, Any]:
    """Retorna apenas o estado operacional da conexão sem expor dados sensíveis da conta."""
    if mt5 is None:
        return {
            "status": "unavailable",
            "connected": False,
            "terminal_connected": False,
            "trade_allowed": False,
            "message": "Pacote MetaTrader5 indisponível.",
        }

    if not _mt5_lock.acquire(timeout=0.5):
        return {
            "status": "connecting",
            "connected": False,
            "terminal_connected": False,
            "trade_allowed": False,
            "message": "Inicializando conexão IPC com MetaTrader 5...",
        }
    try:
        info = mt5.account_info()
        term_fn = getattr(mt5, "terminal_info", None)
        term_info = term_fn() if callable(term_fn) else None
    finally:
        _mt5_lock.release()

    if info is None:
        mt5_path = os.getenv("MT5_PATH", "").strip()
        if _rpc_dir_env or callable(getattr(mt5, "heartbeat_info", None)) or (mt5_path and os.path.isfile(mt5_path)):
            _trigger_background_connect(min_interval=12.0)
        elif _connect_mt5_from_env():
            with _mt5_lock:
                info = mt5.account_info()
                term_fn = getattr(mt5, "terminal_info", None)
                term_info = term_fn() if callable(term_fn) else None

    if info is None:
        hb_fn = getattr(mt5, "heartbeat_info", None)
        hb_raw = hb_fn() if callable(hb_fn) else None
        if isinstance(hb_raw, dict):
            return {
                "status": "waiting_broker_auth",
                "connected": False,
                "ea_alive": True,
                "terminal_connected": bool(hb_raw.get("terminal_connected", False)),
                "trade_allowed": False,
                "expert_trade_allowed": bool(hb_raw.get("expert_trade_allowed", False)),
                "server": str(hb_raw.get("server", "")),
                "message": (
                    "Terminal MT5 e CortexBridgeEA ativos; aguardando autenticação na corretora "
                    "(verifique MT5_LOGIN/MT5_PASSWORD/MT5_SERVER)."
                ),
            }
        return {
            "status": "waiting_mt5",
            "connected": False,
            "terminal_connected": _safe_bool(getattr(term_info, "connected", False), False) if term_info else False,
            "trade_allowed": False,
            "message": "MetaTrader 5 não conectado.",
        }
    return {
        "status": "connected",
        "connected": True,
        "terminal_connected": _safe_bool(getattr(term_info, "connected", True), True) if term_info else True,
        "trade_allowed": _safe_bool(getattr(term_info, "trade_allowed", True), True) if term_info else True,
    }


@app.post("/connect")
def connect_mt5(_req: ConnectRequest | None = Body(default=None)) -> dict[str, Any]:
    """Inicializa e autentica no MT5 usando estritamente a configuração local do servidor."""
    _ensure_mt5_module()
    ok = _connect_mt5_from_env()
    if not ok:
        with _mt5_lock:
            err = mt5.last_error()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Falha ao inicializar/conectar MT5: {err}",
        )

    with _mt5_lock:
        info = mt5.account_info()
        term_fn = getattr(mt5, "terminal_info", None)
        term_info = term_fn() if callable(term_fn) else None

    if info is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Informações da conta MT5 indisponíveis.",
        )

    trade_allowed = (
        (_safe_bool(getattr(term_info, "trade_allowed", True), True) if term_info is not None else True)
        and _safe_bool(getattr(info, "trade_allowed", True), True)
        and _safe_bool(getattr(info, "trade_expert", True), True)
    )

    return {
        "status": "connected",
        "connected": True,
        "trade_allowed": bool(trade_allowed),
        "balance": float(info.balance),
        "equity": float(info.equity),
        "currency": str(info.currency),
        "tz_offset_sec": _safe_int(getattr(info, "tz_offset_sec", 0), 0),
    }


@app.get("/account")
def get_account() -> dict[str, float]:
    """Retorna saldo e patrimônio atual da conta MT5."""
    _require_connected_broker()
    with _mt5_lock:
        info = mt5.account_info()
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MT5 desconectado ou sem resposta.",
        )
    return {
        "balance": float(info.balance),
        "equity": float(info.equity),
    }


@app.post("/order_check")
def check_order(req: OrderRequest) -> dict[str, Any]:
    """Pré-valida uma ordem no MT5 garantindo visibilidade do ativo no Market Watch."""
    _ensure_mt5_module()
    payload = _build_mt5_order_dict(req)
    with _mt5_lock:
        if req.symbol:
            mt5.symbol_select(req.symbol, True)
        result = mt5.order_check(payload)
        if result is None:
            err = mt5.last_error()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Check failed: {err}",
            )
    return {
        "retcode": int(result.retcode),
        "margin": float(result.margin),
        "margin_free": float(result.margin_free),
        "margin_level": float(result.margin_level),
        "comment": str(result.comment),
    }


@app.post("/order_send")
def send_order(req: OrderRequest) -> dict[str, Any]:
    """Envia ou cancela uma ordem no MT5 garantindo seleção prévia do símbolo quando informado."""
    _ensure_mt5_module()
    payload = _build_mt5_order_dict(req)
    with _mt5_lock:
        if req.symbol:
            mt5.symbol_select(req.symbol, True)
        result = mt5.order_send(payload)
        if result is None:
            err = mt5.last_error()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Send failed: {err}",
            )
    return {
        "retcode": int(result.retcode),
        "order": int(getattr(result, "order", 0) or 0),
        "deal": int(getattr(result, "deal", 0) or 0),
        "volume": float(getattr(result, "volume", 0.0) or 0.0),
        "price": float(getattr(result, "price", 0.0) or 0.0),
        "comment": str(getattr(result, "comment", "") or ""),
    }


@app.post("/modify_sl")
def modify_stop_loss(req: ModifySLRequest) -> dict[str, Any]:
    """Modifica o Stop-Loss / Take-Profit de uma posição aberta via TRADE_ACTION_SLTP."""
    _ensure_mt5_module()
    with _mt5_lock:
        mt5.symbol_select(req.symbol, True)
        sym_info = mt5.symbol_info(req.symbol)
        digits = (
            _safe_int(getattr(sym_info, "digits", None), 2)
            if sym_info is not None and getattr(sym_info, "digits", None) is not None
            else 2
        )
        rounded_sl = round(float(req.sl), digits)
        if sym_info is not None:
            sym_bid = _safe_float(getattr(sym_info, "bid", 0.0), 0.0)
            point = _safe_float(getattr(sym_info, "point", 0.0), 0.0)
            if point <= 0.0:
                point = 10.0 ** (-digits) if digits > 0 else 0.01
            stops_lvl = _safe_int(
                getattr(sym_info, "stops_level", getattr(sym_info, "trade_stops_level", 0)),
                0,
            )
            if sym_bid > 0.0:
                max_valid_sl = round(sym_bid - max(point, stops_lvl * point), digits)
                if max_valid_sl > 0.0 and rounded_sl > max_valid_sl:
                    rounded_sl = max_valid_sl

        tp_val = float(req.tp)
        if tp_val == 0.0 and req.position > 0:
            existing = mt5.positions_get(ticket=req.position)
            if existing:
                tp_val = _safe_float(getattr(existing[0], "tp", 0.0), 0.0)
        rounded_tp = round(tp_val, digits) if tp_val > 0 else 0.0

        sltp_payload = {
            "action": _safe_int(getattr(mt5, "TRADE_ACTION_SLTP", 6), 6),
            "symbol": req.symbol,
            "position": int(req.position),
            "sl": rounded_sl,
            "tp": rounded_tp,
            "magic": int(req.magic),
            "comment": "Cortex SL_MODIFY",
        }
        result = mt5.order_send(sltp_payload)
        if result is None:
            err = mt5.last_error()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Modify SL failed: {err}",
            )

    return {
        "retcode": int(result.retcode),
        "order": int(result.order),
        "sl": rounded_sl,
        "tp": rounded_tp,
        "comment": str(result.comment),
    }


@app.get("/positions")
def get_positions() -> list[dict[str, Any]]:
    """Retorna todas as posições abertas no terminal MT5."""
    _require_connected_broker()
    with _mt5_lock:
        pos = mt5.positions_get()
    if pos is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MT5 desconectado ou sem resposta.",
        )
    return [p._asdict() for p in pos]


@app.get("/symbol_info/{symbol}")
def get_symbol_info(symbol: str) -> dict[str, Any]:
    """Retorna metadados completos de negociação de um símbolo B3."""
    _require_connected_broker()
    with _mt5_lock:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
        if info is None:
            _raise_if_rpc_unavailable()
    if info is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Symbol not found",
        )
    return info._asdict()


@app.get("/tick/{symbol}")
def get_tick(symbol: str) -> dict[str, Any]:
    """Retorna o último tick (bid/ask/last/spread_pct/book_valid) de um símbolo em tempo real."""
    _require_connected_broker()
    with _mt5_lock:
        mt5.symbol_select(symbol, True)
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            _raise_if_rpc_unavailable()
    if tick is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tick not found",
        )
    data = tick._asdict() if hasattr(tick, "_asdict") else dict(vars(tick))
    bid = float(data.get("bid", 0.0) or 0.0)
    ask = float(data.get("ask", 0.0) or 0.0)
    data["spread_pct"] = round((ask - bid) / bid, 6) if (bid > 0.0 and ask > bid) else 0.0
    data["book_valid"] = bool(bid > 0.0 and ask >= bid)
    data["tz_offset_sec"] = _safe_int(
        data.get("tz_offset_sec", getattr(tick, "tz_offset_sec", 0)),
        0,
    )
    return data


def _resolve_mt5_timeframe(tf: str) -> int:
    """Converte string/código de timeframe para constante MT5 ou levanta HTTP 400."""
    norm = (tf or "D1").strip().upper()
    m1 = _safe_int(getattr(mt5, "TIMEFRAME_M1", 1), 1)
    m5 = _safe_int(getattr(mt5, "TIMEFRAME_M5", 5), 5)
    m15 = _safe_int(getattr(mt5, "TIMEFRAME_M15", 15), 15)
    m30 = _safe_int(getattr(mt5, "TIMEFRAME_M30", 30), 30)
    h1 = _safe_int(getattr(mt5, "TIMEFRAME_H1", 16385), 16385)
    h4 = _safe_int(getattr(mt5, "TIMEFRAME_H4", 16388), 16388)
    d1 = _safe_int(getattr(mt5, "TIMEFRAME_D1", 16408), 16408)
    w1 = _safe_int(getattr(mt5, "TIMEFRAME_W1", 32769), 32769)
    mn1 = _safe_int(getattr(mt5, "TIMEFRAME_MN1", 49153), 49153)

    mapping = {
        "1": m1,
        "1M": m1,
        "M1": m1,
        "5": m5,
        "5M": m5,
        "M5": m5,
        "15": m15,
        "15M": m15,
        "M15": m15,
        "30": m30,
        "30M": m30,
        "M30": m30,
        "60": h1,
        "60M": h1,
        "1H": h1,
        "H1": h1,
        "16385": h1,
        "240": h4,
        "4H": h4,
        "H4": h4,
        "16388": h4,
        "1D": d1,
        "D1": d1,
        "16408": d1,
        "1W": w1,
        "W1": w1,
        "1WK": w1,
        "32769": w1,
        "MN": mn1,
        "MN1": mn1,
        "1MO": mn1,
        "49153": mn1,
    }
    if norm not in mapping:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Timeframe inválido: '{tf}'. Valores aceitos: "
                "M1, M5, M15, M30, H1, H4, D1, W1, MN1."
            ),
        )
    return int(mapping[norm])


@app.get("/rates/{symbol}")
def get_rates(
    symbol: str,
    timeframe: str = "D1",
    count: int = 120,
    start_pos: int = 0,
) -> list[dict[str, Any]]:
    """Retorna candles históricos/intraday OHLCV direto da B3 via MT5 (zero atraso)."""
    _require_connected_broker()
    capped_count = max(1, min(int(count), 1000))
    safe_start = max(0, int(start_pos))
    tf_const = _resolve_mt5_timeframe(timeframe)
    with _mt5_lock:
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, tf_const, safe_start, capped_count)
        if rates is None:
            _raise_if_rpc_unavailable()
    if rates is None or len(rates) == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rates not found for {symbol} ({timeframe})",
        )

    out: list[dict[str, Any]] = []
    for row in rates:
        if isinstance(row, dict):
            item = row
        elif hasattr(row, "dtype") and getattr(row.dtype, "names", None):
            item = {name: row[name] for name in row.dtype.names}
        else:
            item = {
                "time": getattr(row, "time", 0),
                "open": getattr(row, "open", 0.0),
                "high": getattr(row, "high", 0.0),
                "low": getattr(row, "low", 0.0),
                "close": getattr(row, "close", 0.0),
                "tick_volume": getattr(row, "tick_volume", 0),
                "real_volume": getattr(row, "real_volume", 0),
            }
        real_vol = int(item.get("real_volume", 0) or 0)
        tick_vol = int(item.get("tick_volume", 0) or 0)
        out.append(
            {
                "time": int(item.get("time", 0) or 0),
                "open": float(item.get("open", 0.0) or 0.0),
                "high": float(item.get("high", 0.0) or 0.0),
                "low": float(item.get("low", 0.0) or 0.0),
                "close": float(item.get("close", 0.0) or 0.0),
                "tick_volume": tick_vol,
                "real_volume": real_vol,
                "volume": real_vol if real_vol > 0 else tick_vol,
            }
        )
    return out


@app.post("/symbol_select/{symbol}")
def select_symbol(symbol: str) -> dict[str, str]:
    """Habilita o símbolo no Market Watch do MT5."""
    _ensure_mt5_module()
    with _mt5_lock:
        ok = mt5.symbol_select(symbol, True)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not select symbol",
        )
    return {"status": "success"}


if __name__ == "__main__":
    import uvicorn

    bridge_host = os.getenv("BRIDGE_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        bridge_port = int(os.getenv("BRIDGE_PORT", "5000"))
    except ValueError:
        bridge_port = 5000

    uvicorn.run(app, host=bridge_host, port=bridge_port)
