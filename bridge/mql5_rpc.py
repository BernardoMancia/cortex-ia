"""
Backend RPC baseado em arquivos atômicos (FILE_COMMON) para o MT5 Bridge — Córtex IA.

Fornece uma interface 100% compatível com o módulo C-extension `MetaTrader5`,
comunicando-se com o Expert Advisor `CortexBridgeEA.mq5` dentro do `terminal64.exe`
sem depender de Named Pipes Win32 (que sofrem timeout `-10005` sob Wine/Linux).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import time
from typing import Any
import uuid

logger = logging.getLogger("mt5_bridge.mql5_rpc")

_NON_INTRADAY_TIMEFRAMES = frozenset(
    {
        "D1",
        "1D",
        "16408",
        "W1",
        "1W",
        "1WK",
        "32769",
        "MN1",
        "MN",
        "1MO",
        "49153",
    }
)


class _AttrDict:
    """Wrapper leve que expõe chaves como atributos e implementa `_asdict()`."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def _asdict(self) -> dict[str, Any]:
        return dict(self._data)

    def __repr__(self) -> str:
        return f"_AttrDict({self._data!r})"


class MQL5FileRPCBackend:
    """Implementação drop-in da API Python `MetaTrader5` via `CortexBridgeEA`."""

    TIMEFRAME_M1: int = 1
    TIMEFRAME_M5: int = 5
    TIMEFRAME_M15: int = 15
    TIMEFRAME_M30: int = 30
    TIMEFRAME_H1: int = 16385
    TIMEFRAME_H4: int = 16388
    TIMEFRAME_D1: int = 16408
    TIMEFRAME_W1: int = 32769
    TIMEFRAME_MN1: int = 49153

    TRADE_ACTION_DEAL: int = 1
    TRADE_ACTION_PENDING: int = 5
    TRADE_ACTION_SLTP: int = 6
    TRADE_ACTION_MODIFY: int = 7
    TRADE_ACTION_REMOVE: int = 8

    ORDER_TYPE_BUY: int = 0
    ORDER_TYPE_SELL: int = 1

    ORDER_FILLING_FOK: int = 0
    ORDER_FILLING_IOC: int = 1
    ORDER_FILLING_RETURN: int = 2

    ORDER_TIME_GTC: int = 0
    ORDER_TIME_DAY: int = 1

    def __init__(self, rpc_dir: str | Path, default_timeout: float = 10.0) -> None:
        self._rpc_dir = Path(rpc_dir)
        self._default_timeout = max(0.5, float(default_timeout))
        self._last_error: tuple[int, str] = (1, "Success")
        self._last_gc_ts: float = 0.0
        self._rpc_dir.mkdir(parents=True, exist_ok=True)

    @property
    def rpc_dir(self) -> Path:
        return self._rpc_dir

    def last_error(self) -> tuple[int, str]:
        return self._last_error

    def _set_error(self, code: int, message: str) -> None:
        self._last_error = (int(code), str(message))

    def _gc_stale_files(self, max_age_sec: float = 60.0) -> None:
        now = time.time()
        if (now - self._last_gc_ts) < 30.0:
            return
        self._last_gc_ts = now
        try:
            for item in self._rpc_dir.iterdir():
                if item.name in ("heartbeat.json", "heartbeat.tmp"):
                    continue
                try:
                    if (now - item.stat().st_mtime) > max_age_sec:
                        item.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            pass

    def _read_json_file(self, path: Path, retries: int = 4) -> dict[str, Any] | None:
        for attempt in range(retries):
            try:
                raw = path.read_text(encoding="utf-8-sig").strip()
                if raw:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        return parsed
            except (OSError, json.JSONDecodeError):
                if attempt + 1 < retries:
                    time.sleep(0.008)
        return None

    def _read_fresh_heartbeat(self, max_age_sec: float = 15.0) -> dict[str, Any] | None:
        hb_path = self._rpc_dir / "heartbeat.json"
        for attempt in range(3):
            try:
                st = hb_path.stat()
            except OSError:
                if attempt + 1 < 3:
                    time.sleep(0.008)
                    continue
                return None
            if (time.time() - st.st_mtime) > max_age_sec:
                return None
            parsed = self._read_json_file(hb_path, retries=2)
            if parsed is not None:
                return parsed
            if attempt + 1 < 3:
                time.sleep(0.008)
        return None

    def _rpc(self, params: dict[str, Any], timeout: float | None = None) -> dict[str, Any] | None:
        self._rpc_dir.mkdir(parents=True, exist_ok=True)
        self._gc_stale_files()

        wait_sec = self._default_timeout if timeout is None else max(0.5, float(timeout))
        req_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        tmp_req = self._rpc_dir / f"req_{req_id}.tmp"
        final_req = self._rpc_dir / f"req_{req_id}.cmd"
        final_resp = self._rpc_dir / f"resp_{req_id}.json"

        lines: list[str] = []
        for k, v in params.items():
            if v is None:
                continue
            clean_k = str(k).strip().replace("=", "").replace("\r", "").replace("\n", "")
            clean_v = str(v).replace("\r", " ").replace("\n", " ").strip()
            lines.append(f"{clean_k}={clean_v}")

        try:
            tmp_req.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp_req, final_req)
        except OSError as exc:
            self._set_error(-10001, f"RPC write error: {exc}")
            tmp_req.unlink(missing_ok=True)
            return None

        deadline = time.monotonic() + wait_sec
        try:
            while time.monotonic() < deadline:
                if final_resp.exists():
                    payload = self._read_json_file(final_resp)
                    final_resp.unlink(missing_ok=True)
                    if payload is None:
                        self._set_error(-10002, "Invalid JSON from CortexBridgeEA")
                        return None
                    if not payload.get("ok", False):
                        self._set_error(
                            int(payload.get("error_code", -1) or -1),
                            str(payload.get("error", "MQL5 error")),
                        )
                        return None
                    self._set_error(1, "Success")
                    return payload
                time.sleep(0.008)
        finally:
            final_req.unlink(missing_ok=True)
            tmp_req.unlink(missing_ok=True)

        self._set_error(-10005, f"CortexBridgeEA RPC timeout ({wait_sec:.1f}s)")
        return None

    def initialize(self, *_args: Any, **_kwargs: Any) -> bool:
        hb = self._read_fresh_heartbeat(max_age_sec=5.0)
        if hb is not None:
            if bool(hb.get("connected", False)):
                self._set_error(1, "Success")
                return True
            self._set_error(-10004, "Terminal running, waiting broker login")
            return False
        resp = self._rpc({"cmd": "account"}, timeout=3.0)
        if resp is not None and bool(resp.get("connected", False)):
            self._set_error(1, "Success")
            return True
        if resp is not None:
            self._set_error(-10004, "Terminal running, waiting broker login")
        return False

    def login(self, *_args: Any, **_kwargs: Any) -> bool:
        return self.initialize()

    def shutdown(self) -> None:
        return None

    def heartbeat_info(self) -> dict[str, Any] | None:
        """Retorna o heartbeat mais recente do CortexBridgeEA sem bloquear."""
        return self._read_fresh_heartbeat(max_age_sec=15.0)

    def terminal_info(self) -> _AttrDict | None:
        hb = self._read_fresh_heartbeat(max_age_sec=15.0)
        if hb is None:
            hb = self._rpc({"cmd": "account"}, timeout=2.0)
        if hb is None:
            return None
        return _AttrDict(
            {
                "connected": bool(hb.get("terminal_connected", False)),
                "trade_allowed": bool(hb.get("trade_allowed", False)),
                "terminal_trade_allowed": bool(hb.get("terminal_trade_allowed", False)),
                "expert_trade_allowed": bool(hb.get("expert_trade_allowed", False)),
                "server": str(hb.get("server", "")),
                "tz_offset_sec": int(hb.get("tz_offset_sec", 0) or 0),
            }
        )

    def account_info(self) -> _AttrDict | None:
        hb = self._read_fresh_heartbeat(max_age_sec=1.5)
        if hb is None:
            hb = self._rpc({"cmd": "account"}, timeout=2.5)
        if hb is None or not bool(hb.get("connected", False)):
            if hb is not None:
                self._set_error(-10004, "Terminal running, waiting broker login")
            return None
        self._set_error(1, "Success")
        return _AttrDict(
            {
                "login": int(hb.get("login", 0) or 0),
                "server": str(hb.get("server", "")),
                "currency": str(hb.get("currency", "BRL") or "BRL"),
                "balance": float(hb.get("balance", 0.0) or 0.0),
                "equity": float(hb.get("equity", 0.0) or 0.0),
                "margin": float(hb.get("margin", 0.0) or 0.0),
                "margin_free": float(hb.get("margin_free", 0.0) or 0.0),
                "trade_allowed": bool(hb.get("account_trade_allowed", True)),
                "trade_expert": bool(hb.get("account_expert_allowed", True)),
                "tz_offset_sec": int(hb.get("tz_offset_sec", 0) or 0),
            }
        )

    def symbol_select(self, symbol: str, enable: bool = True) -> bool:
        if not enable:
            return True
        resp = self._rpc({"cmd": "symbol_select", "symbol": symbol.strip().upper()}, timeout=5.0)
        return resp is not None

    def symbol_info(self, symbol: str) -> _AttrDict | None:
        resp = self._rpc({"cmd": "symbol_info", "symbol": symbol.strip().upper()}, timeout=6.0)
        if resp is None or not isinstance(resp.get("data"), dict):
            return None
        return _AttrDict(resp["data"])

    def symbol_info_tick(self, symbol: str) -> _AttrDict | None:
        resp = self._rpc({"cmd": "tick", "symbol": symbol.strip().upper()}, timeout=6.0)
        if resp is None or not isinstance(resp.get("data"), dict):
            return None
        data = dict(resp["data"])
        tz_offset = int(resp.get("tz_offset_sec", data.get("tz_offset_sec", 0)) or 0)
        if tz_offset != 0:
            raw_time = int(data.get("time", 0) or 0)
            if raw_time > 0:
                data["time"] = raw_time - tz_offset
            raw_msc = int(data.get("time_msc", 0) or 0)
            if raw_msc > 0:
                data["time_msc"] = raw_msc - (tz_offset * 1000)
        data["tz_offset_sec"] = tz_offset
        return _AttrDict(data)

    def copy_rates_from_pos(
        self,
        symbol: str,
        timeframe: int | str,
        start_pos: int = 0,
        count: int = 120,
    ) -> list[dict[str, Any]] | None:
        safe_start = max(0, int(start_pos))
        tf_str = str(timeframe).strip()
        resp = self._rpc(
            {
                "cmd": "rates",
                "symbol": symbol.strip().upper(),
                "timeframe": tf_str,
                "start_pos": safe_start,
                "count": int(count),
            },
            timeout=10.0,
        )
        if resp is None or not isinstance(resp.get("rates"), list):
            return None

        tz_offset = int(resp.get("tz_offset_sec", 0) or 0)
        is_intraday = tf_str.upper() not in _NON_INTRADAY_TIMEFRAMES
        out: list[dict[str, Any]] = []
        for row in resp["rates"]:
            if not isinstance(row, dict):
                continue
            bar = dict(row)
            if is_intraday and tz_offset != 0:
                raw_time = int(bar.get("time", 0) or 0)
                if raw_time > 0:
                    bar["time"] = raw_time - tz_offset
            out.append(bar)
        return out

    def positions_get(
        self,
        symbol: str | None = None,
        ticket: int | None = None,
    ) -> list[_AttrDict] | None:
        params: dict[str, Any] = {"cmd": "positions"}
        if symbol:
            params["symbol"] = symbol.strip().upper()
        if ticket is not None and int(ticket) > 0:
            params["ticket"] = int(ticket)
        resp = self._rpc(params, timeout=6.0)
        if resp is None or not isinstance(resp.get("positions"), list):
            return None

        tz_offset = int(resp.get("tz_offset_sec", 0) or 0)
        out: list[_AttrDict] = []
        for item in resp["positions"]:
            if not isinstance(item, dict):
                continue
            pos_dict = dict(item)
            item_tz = int(pos_dict.get("tz_offset_sec", tz_offset) or 0)
            if item_tz != 0:
                raw_time = int(pos_dict.get("time", 0) or 0)
                if raw_time > 0:
                    pos_dict["time"] = raw_time - item_tz
            pos_dict["tz_offset_sec"] = item_tz
            out.append(_AttrDict(pos_dict))
        return out

    def order_check(self, request: dict[str, Any]) -> _AttrDict | None:
        params: dict[str, Any] = {"cmd": "order_check"}
        params.update(request)
        resp = self._rpc(params, timeout=10.0)
        if resp is None or not isinstance(resp.get("data"), dict):
            return None
        return _AttrDict(resp["data"])

    def order_send(self, request: dict[str, Any]) -> _AttrDict | None:
        params: dict[str, Any] = {"cmd": "order_send"}
        params.update(request)
        resp = self._rpc(params, timeout=15.0)
        if resp is None or not isinstance(resp.get("data"), dict):
            return None
        return _AttrDict(resp["data"])
