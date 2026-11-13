from __future__ import annotations

from contextlib import asynccontextmanager
import hmac
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from domain.financial_firewall import FinancialFirewall
from infra.mt5_bridge.mql5_rpc import MQL5FileRPCBackend

logger = logging.getLogger("cortex.mt5_bridge.api")


class _BridgeIpLockout:
    def __init__(
        self,
        max_failures: int = 5,
        window_sec: float = 900.0,
        lockout_sec: float = 900.0,
    ) -> None:
        self._max_failures = max(2, int(max_failures))
        self._window_sec = max(30.0, float(window_sec))
        self._lockout_sec = max(60.0, float(lockout_sec))
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}

    def is_locked(self, ip: str) -> tuple[bool, int]:
        with self._lock:
            now = time.monotonic()
            exp = self._locked_until.get(ip)
            if exp is not None:
                if exp > now:
                    return True, int(exp - now)
                self._locked_until.pop(ip, None)
            return False, 0

    def record_failure(self, ip: str) -> tuple[bool, int]:
        with self._lock:
            now = time.monotonic()
            hist = [t for t in self._failures.get(ip, []) if (now - t) < self._window_sec]
            hist.append(now)
            self._failures[ip] = hist
            if len(hist) >= self._max_failures:
                exp = now + self._lockout_sec
                self._locked_until[ip] = exp
                return True, int(self._lockout_sec)
            return False, 0

    def reset(self, ip: str) -> None:
        with self._lock:
            self._failures.pop(ip, None)
            self._locked_until.pop(ip, None)


def _resolve_rpc_dir() -> Path:
    raw = os.getenv("CORTEX_MQL5_RPC_DIR", "").strip()
    if raw:
        return Path(raw)
    default_wine = Path("/root/.wine/drive_c/users/root/AppData/Roaming/MetaQuotes/Terminal/Common/Files/cortex_rpc")
    if os.name != "nt" and default_wine.parent.exists():
        return default_wine
    return Path(os.getenv("CORTEX_DATA_DIR", "data")) / "cortex_rpc"


def _is_strict_auth() -> bool:
    raw = os.getenv("BRIDGE_STRICT_AUTH", "true").strip().lower()
    return raw not in ("false", "0", "no", "nao")


def _get_bridge_key() -> str:
    return os.getenv("BRIDGE_API_KEY", "").strip()


rpc_backend = MQL5FileRPCBackend(rpc_dir=_resolve_rpc_dir())
auth_lockout = _BridgeIpLockout(max_failures=5, window_sec=900.0, lockout_sec=900.0)
bridge_firewall = FinancialFirewall(
    max_single_order_notional=float(os.getenv("MAX_SINGLE_ORDER_NOTIONAL", "250000.0") or 250000.0),
    max_orders_per_minute=int(os.getenv("MAX_ORDERS_PER_MINUTE", "12") or 12),
    idempotency_ttl_seconds=900.0,
)
_order_mutex = threading.Lock()


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    key = _get_bridge_key()
    if _is_strict_auth() and len(key) < 32:
        logger.critical("MT5 Bridge em modo Fail-Closed: BRIDGE_API_KEY ausente ou menor que 32 bytes.")
    rpc_backend.initialize()
    yield
    rpc_backend.shutdown()


app = FastAPI(
    title="Cortex IA MT5 Bridge",
    version="1.0.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=_lifespan,
)


def require_bridge_auth(request: Request) -> None:
    client_ip = request.client.host if request.client else "unknown"
    locked, retry_after = auth_lockout.is_locked(client_ip)
    if locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"IP temporariamente bloqueado por falhas de autenticação ({retry_after}s).",
        )

    expected = _get_bridge_key()
    strict = _is_strict_auth()
    if strict and len(expected) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MT5 Bridge bloqueada (Fail-Closed): BRIDGE_API_KEY deve ter ao menos 32 caracteres.",
        )
    if not expected and not strict:
        return

    provided = (
        request.headers.get("X-Bridge-Key")
        or request.headers.get("X-Bridge-Api-Key")
        or ""
    ).strip()
    if not provided or not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        now_locked, wait_s = auth_lockout.record_failure(client_ip)
        if now_locked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"IP bloqueado após tentativas inválidas ({wait_s}s).",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Bridge-Key inválida ou ausente.",
        )

    auth_lockout.reset(client_ip)


class BridgeOrderRequest(BaseModel):
    symbol: str = Field(..., min_length=4, max_length=12)
    side: str = Field(..., min_length=3, max_length=4)
    quantity: int = Field(..., gt=0, le=500000)
    price: float = Field(default=0.0, ge=0.0)
    stop_loss: float = Field(default=0.0, ge=0.0)
    take_profit: float = Field(default=0.0, ge=0.0)
    max_slippage_pct: float = Field(default=0.003, ge=0.0, le=0.10)
    client_order_id: str = Field(default="", max_length=80)
    wallet_id: int = Field(default=0, ge=0)
    wallet_ceiling: float | None = Field(default=None)
    is_emergency_liquidation: bool = Field(default=False)
    magic: int = Field(default=234000)
    comment: str = Field(default="CortexIA", max_length=31)


class BridgeSLTPRequest(BaseModel):
    symbol: str = Field(..., min_length=4, max_length=12)
    ticket: int = Field(..., ge=0)
    stop_loss: float = Field(..., ge=0.0)
    take_profit: float = Field(default=0.0, ge=0.0)


class BridgeCancelRequest(BaseModel):
    symbol: str | None = Field(default=None, max_length=12)


def _select_filling_mode(sym_info: Any) -> int:
    fmask = int(getattr(sym_info, "filling_mode", 0) or 0)
    if fmask & 2:
        return MQL5FileRPCBackend.ORDER_FILLING_IOC
    if fmask & 1:
        return MQL5FileRPCBackend.ORDER_FILLING_FOK
    return MQL5FileRPCBackend.ORDER_FILLING_RETURN


@app.get("/health", dependencies=[Depends(require_bridge_auth)])
def get_health() -> dict[str, Any]:
    hb = rpc_backend.heartbeat_info()
    if hb is None:
        acc = rpc_backend.account_info()
        err_code, err_msg = rpc_backend.last_error()
        if acc is None:
            return {
                "status": "degraded",
                "bridge_online": True,
                "mt5_initialized": False,
                "broker_connected": False,
                "trade_allowed": False,
                "server": "",
                "login": 0,
                "last_error": [err_code, err_msg],
                "message": err_msg,
            }
        return {
            "status": "ok",
            "bridge_online": True,
            "mt5_initialized": True,
            "broker_connected": True,
            "trade_allowed": bool(acc.trade_allowed and acc.trade_expert),
            "server": str(acc.server),
            "login": int(acc.login),
            "last_error": [1, "Success"],
            "message": "OK",
        }

    broker_conn = bool(hb.get("connected", False))
    trade_ok = bool(hb.get("trade_allowed", False))
    return {
        "status": "ok" if broker_conn else "degraded",
        "bridge_online": True,
        "mt5_initialized": True,
        "broker_connected": broker_conn,
        "trade_allowed": trade_ok,
        "server": str(hb.get("server", "") or ""),
        "login": int(hb.get("login", 0) or 0),
        "last_error": list(rpc_backend.last_error()),
        "message": "OK" if broker_conn else "Aguardando login na corretora MT5.",
    }


@app.get("/account", dependencies=[Depends(require_bridge_auth)])
def get_account() -> dict[str, Any]:
    acc = rpc_backend.account_info()
    if acc is None:
        _, err_msg = rpc_backend.last_error()
        return {
            "connected": False,
            "login": "",
            "server": "",
            "currency": "BRL",
            "balance": 0.0,
            "equity": 0.0,
            "margin": 0.0,
            "free_margin": 0.0,
            "trade_allowed": False,
            "message": err_msg,
        }
    d = acc._asdict()
    return {
        "connected": True,
        "login": str(d.get("login", "") or ""),
        "server": str(d.get("server", "") or ""),
        "currency": str(d.get("currency", "BRL") or "BRL"),
        "balance": round(float(d.get("balance", 0.0) or 0.0), 2),
        "equity": round(float(d.get("equity", 0.0) or 0.0), 2),
        "margin": round(float(d.get("margin", 0.0) or 0.0), 2),
        "free_margin": round(float(d.get("margin_free", 0.0) or 0.0), 2),
        "trade_allowed": bool(d.get("trade_allowed", True) and d.get("trade_expert", True)),
    }


@app.get("/positions", dependencies=[Depends(require_bridge_auth)])
def get_positions(symbol: str | None = Query(default=None)) -> dict[str, Any]:
    raw = rpc_backend.positions_get(symbol=symbol)
    if raw is None:
        _, err_msg = rpc_backend.last_error()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Falha ao consultar posições no MT5: {err_msg}",
        )
    return {
        "connected": True,
        "positions": [p._asdict() for p in raw],
    }


@app.get("/orders", dependencies=[Depends(require_bridge_auth)])
def get_orders(symbol: str | None = Query(default=None)) -> dict[str, Any]:
    raw = rpc_backend.orders_get(symbol=symbol)
    if raw is None:
        return {"connected": False, "orders": []}
    return {
        "connected": True,
        "orders": [o._asdict() for o in raw],
    }


@app.get("/tick/{symbol}", dependencies=[Depends(require_bridge_auth)])
def get_tick(symbol: str) -> dict[str, Any]:
    clean_sym = symbol.strip().upper()
    tick = rpc_backend.symbol_info_tick(clean_sym)
    if tick is None:
        _, err_msg = rpc_backend.last_error()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tick indisponível para {clean_sym}: {err_msg}",
        )
    d = tick._asdict()
    bid = float(d.get("bid", 0.0) or 0.0)
    ask = float(d.get("ask", 0.0) or 0.0)
    last = float(d.get("last", 0.0) or 0.0)
    mid = (ask + bid) / 2.0 if (bid > 0.0 and ask >= bid) else last
    spread_pct = ((ask - bid) / mid) if (bid > 0.0 and ask >= bid and mid > 0.0) else 0.0
    return {
        "symbol": clean_sym,
        "bid": bid,
        "ask": ask,
        "last": last,
        "volume": float(d.get("volume_real", 0.0) or d.get("volume", 0.0) or 0.0),
        "time": int(d.get("time", 0) or 0),
        "spread_pct": round(spread_pct, 6),
    }


@app.get("/ohlcv/{symbol}", dependencies=[Depends(require_bridge_auth)])
def get_ohlcv(
    symbol: str,
    timeframe: str = Query(default="D1"),
    count: int = Query(default=120, ge=1, le=1000),
    start_pos: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    clean_sym = symbol.strip().upper()
    bars = rpc_backend.copy_rates_from_pos(
        symbol=clean_sym,
        timeframe=timeframe,
        start_pos=start_pos,
        count=count,
    )
    if bars is None:
        _, err_msg = rpc_backend.last_error()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Candles indisponíveis para {clean_sym} ({timeframe}): {err_msg}",
        )
    return {
        "symbol": clean_sym,
        "timeframe": timeframe.strip().upper(),
        "bars": bars,
    }


@app.post("/order", dependencies=[Depends(require_bridge_auth)])
def post_order(req: BridgeOrderRequest) -> dict[str, Any]:
    clean_sym = req.symbol.strip().upper()
    clean_side = req.side.strip().upper()
    cid = req.client_order_id.strip()

    with _order_mutex:
        if cid:
            cached = bridge_firewall.get_cached_execution(cid)
            if cached is not None:
                replay = dict(cached)
                replay["idempotent_replay"] = True
                return replay

        tick = rpc_backend.symbol_info_tick(clean_sym)
        sym_info = rpc_backend.symbol_info(clean_sym)
        live_px = float(req.price)
        if tick is not None:
            td = tick._asdict()
            if clean_side == "BUY":
                live_px = float(td.get("ask") or td.get("last") or live_px or 0.0)
            else:
                live_px = float(td.get("bid") or td.get("last") or live_px or 0.0)

        verdict = bridge_firewall.validate_order(
            symbol=clean_sym,
            side=clean_side,
            quantity=int(req.quantity),
            price=live_px,
            wallet_id=int(req.wallet_id),
            wallet_ceiling=req.wallet_ceiling,
            is_emergency_liquidation=bool(req.is_emergency_liquidation),
        )
        if not verdict.allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=verdict.reason,
            )

        if sym_info is None:
            _, err_msg = rpc_backend.last_error()
            return {
                "ok": False,
                "filled": False,
                "status": "REJECTED",
                "filled_qty": 0,
                "fill_price": 0.0,
                "order_id": "",
                "retcode": -1,
                "message": f"Símbolo {clean_sym} indisponível no MT5: {err_msg}",
            }

        digits = int(getattr(sym_info, "digits", 2) or 2)
        point = float(getattr(sym_info, "point", 0.01) or 0.01)
        deviation_pts = (
            max(5, int(round((live_px * float(req.max_slippage_pct)) / point)))
            if point > 0.0 and live_px > 0.0
            else 20
        )
        mt5_order_type = (
            MQL5FileRPCBackend.ORDER_TYPE_BUY
            if clean_side == "BUY"
            else MQL5FileRPCBackend.ORDER_TYPE_SELL
        )
        filling = _select_filling_mode(sym_info)

        mt5_req: dict[str, Any] = {
            "action": MQL5FileRPCBackend.TRADE_ACTION_DEAL,
            "symbol": clean_sym,
            "volume": float(req.quantity),
            "type": mt5_order_type,
            "price": round(live_px, digits),
            "sl": round(float(req.stop_loss), digits) if req.stop_loss > 0.0 else 0.0,
            "tp": round(float(req.take_profit), digits) if req.take_profit > 0.0 else 0.0,
            "deviation": deviation_pts,
            "magic": int(req.magic),
            "comment": (cid[:28] if cid else req.comment[:28]) or "CortexIA",
            "type_time": MQL5FileRPCBackend.ORDER_TIME_DAY,
            "type_filling": filling,
        }

        res = rpc_backend.order_send(mt5_req)
        if res is None:
            err_code, err_msg = rpc_backend.last_error()
            return {
                "ok": False,
                "filled": False,
                "status": "REJECTED",
                "filled_qty": 0,
                "fill_price": 0.0,
                "order_id": "",
                "retcode": err_code,
                "message": err_msg,
            }

        rd = res._asdict()
        retcode = int(rd.get("retcode", 0) or 0)
        filled_vol = int(round(float(rd.get("volume", 0.0) or 0.0)))
        fill_px = float(rd.get("price", 0.0) or 0.0) or live_px
        order_ticket = str(rd.get("order") or rd.get("deal") or "")
        comment_out = str(rd.get("comment") or "")

        is_filled = retcode in (10008, 10009, 10010) and filled_vol > 0
        if not is_filled and retcode in (10008, 10009) and filled_vol == 0:
            is_filled = True
            filled_vol = int(req.quantity)

        receipt = {
            "ok": is_filled,
            "filled": is_filled,
            "status": (
                "FILLED"
                if (is_filled and filled_vol >= int(req.quantity))
                else ("PARTIAL" if is_filled else "REJECTED")
            ),
            "filled_qty": filled_vol if is_filled else 0,
            "fill_price": round(fill_px, 4) if is_filled else 0.0,
            "order_id": order_ticket,
            "deal_id": str(rd.get("deal", "") or ""),
            "retcode": retcode,
            "message": comment_out or ("Executada" if is_filled else f"MT5 retcode {retcode}"),
        }
        if is_filled and cid:
            bridge_firewall.record_execution(cid, receipt)
        return receipt


@app.post("/position/sltp", dependencies=[Depends(require_bridge_auth)])
def post_position_sltp(req: BridgeSLTPRequest) -> dict[str, Any]:
    return rpc_backend.modify_position_sltp(
        symbol=req.symbol,
        ticket=req.ticket,
        sl=req.stop_loss,
        tp=req.take_profit,
    )


@app.post("/orders/cancel_all", dependencies=[Depends(require_bridge_auth)])
def post_cancel_all(req: BridgeCancelRequest | None = None) -> dict[str, Any]:
    sym = req.symbol if req is not None else None
    cancelled = rpc_backend.cancel_all_orders(symbol=sym)
    return {
        "ok": True,
        "cancelled_count": cancelled,
    }


def main() -> None:
    import uvicorn

    host = os.getenv("BRIDGE_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("BRIDGE_PORT", "5000") or 5000)
    uvicorn.run(app, host=host, port=port, access_log=False)


if __name__ == "__main__":
    main()
