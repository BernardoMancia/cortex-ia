"""Testes unitários para o backend MQL5FileRPCBackend e integração com FastAPI Bridge."""

from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Any

from fastapi.testclient import TestClient

import bridge.app as bridge_app
from bridge.mql5_rpc import MQL5FileRPCBackend


def _parse_cmd_file(cmd_path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in cmd_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _start_mock_ea(rpc_dir: Path, stop_evt: threading.Event, handler: Any) -> threading.Thread:
    """Simula o loop OnTimer() de CortexBridgeEA.mq5 em background."""

    def _loop() -> None:
        while not stop_evt.is_set():
            for cmd_file in list(rpc_dir.glob("req_*.cmd")):
                try:
                    params = _parse_cmd_file(cmd_file)
                except OSError:
                    continue
                req_id = cmd_file.stem.removeprefix("req_")
                cmd_file.unlink(missing_ok=True)
                resp_payload = handler(params)
                tmp_resp = rpc_dir / f"resp_{req_id}.tmp"
                final_resp = rpc_dir / f"resp_{req_id}.json"
                tmp_resp.write_text(json.dumps(resp_payload), encoding="utf-8")
                os.replace(tmp_resp, final_resp)
            time.sleep(0.005)

    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    return t


def test_mql5_rpc_heartbeat_and_account(tmp_path: Path) -> None:
    backend = MQL5FileRPCBackend(tmp_path, default_timeout=1.5)
    hb_path = tmp_path / "heartbeat.json"
    hb_path.write_text(
        json.dumps(
            {
                "ok": True,
                "connected": True,
                "terminal_connected": True,
                "trade_allowed": True,
                "terminal_trade_allowed": True,
                "expert_trade_allowed": True,
                "account_trade_allowed": True,
                "account_expert_allowed": True,
                "login": 12345678,
                "server": "ClearInvestimentos-PRD",
                "currency": "BRL",
                "balance": 1520.45,
                "equity": 1532.10,
                "margin": 0.0,
                "margin_free": 1520.45,
            }
        ),
        encoding="utf-8",
    )

    assert backend.initialize() is True
    term = backend.terminal_info()
    assert term is not None
    assert term.connected is True
    assert term.trade_allowed is True

    acc = backend.account_info()
    assert acc is not None
    assert acc.balance == 1520.45
    assert acc.equity == 1532.10
    assert acc.currency == "BRL"
    assert acc._asdict()["server"] == "ClearInvestimentos-PRD"


def test_mql5_rpc_endpoints_with_fastapi_bridge(tmp_path: Path, monkeypatch: Any) -> None:
    backend = MQL5FileRPCBackend(tmp_path, default_timeout=2.0)
    (tmp_path / "heartbeat.json").write_text(
        json.dumps(
            {
                "ok": True,
                "connected": True,
                "terminal_connected": True,
                "trade_allowed": True,
                "login": 1001,
                "server": "ClearInvestimentos-PRD",
                "currency": "BRL",
                "balance": 5000.0,
                "equity": 5050.0,
            }
        ),
        encoding="utf-8",
    )

    def _ea_handler(params: dict[str, str]) -> dict[str, Any]:
        cmd = params.get("cmd")
        sym = params.get("symbol", "")
        if cmd == "tick":
            return {
                "ok": True,
                "data": {
                    "time": 1700000000,
                    "bid": 38.49,
                    "ask": 38.51,
                    "last": 38.50,
                    "volume": 1200,
                    "time_msc": 1700000000123,
                    "flags": 6,
                    "volume_real": 1200.0,
                },
            }
        if cmd == "rates":
            return {
                "ok": True,
                "rates": [
                    {
                        "time": 1700000000,
                        "open": 38.10,
                        "high": 38.70,
                        "low": 38.00,
                        "close": 38.50,
                        "tick_volume": 450,
                        "spread": 2,
                        "real_volume": 98000,
                    }
                ],
            }
        if cmd == "positions":
            return {
                "ok": True,
                "positions": [
                    {
                        "ticket": 778899,
                        "symbol": sym or "PETR4",
                        "type": 0,
                        "magic": 234000,
                        "time": 1700000000,
                        "volume": 100.0,
                        "price_open": 38.00,
                        "price_current": 38.50,
                        "sl": 36.90,
                        "tp": 0.0,
                        "profit": 50.0,
                        "comment": "Cortex BUY",
                    }
                ],
            }
        if cmd == "symbol_info":
            return {
                "ok": True,
                "data": {
                    "name": sym,
                    "digits": 2,
                    "point": 0.01,
                    "bid": 38.49,
                    "ask": 38.51,
                    "last": 38.50,
                    "volume_min": 100.0,
                    "volume_max": 100000.0,
                    "volume_step": 100.0,
                    "filling_mode": 2,
                    "trade_mode": 4,
                },
            }
        if cmd == "order_check":
            return {
                "ok": True,
                "data": {
                    "retcode": 0,
                    "margin": 0.0,
                    "margin_free": 5000.0,
                    "margin_level": 0.0,
                    "comment": "Done",
                },
            }
        if cmd == "order_send":
            return {
                "ok": True,
                "data": {
                    "retcode": 10009,
                    "order": 998877,
                    "deal": 554433,
                    "volume": float(params.get("volume", "100")),
                    "price": float(params.get("price", "38.51")),
                    "comment": "Request executed",
                },
            }
        if cmd == "symbol_select":
            return {"ok": True}
        return {"ok": False, "error_code": 4000, "error": "Unknown"}

    stop_evt = threading.Event()
    worker = _start_mock_ea(tmp_path, stop_evt, _ea_handler)
    try:
        monkeypatch.setattr(bridge_app, "mt5", backend)
        monkeypatch.setenv("BRIDGE_API_KEY", "unit-test-key")
        headers = {"X-Bridge-Api-Key": "unit-test-key"}
        client = TestClient(bridge_app.app)

        r_health = client.get("/health", headers=headers)
        assert r_health.status_code == 200
        assert r_health.json()["connected"] is True
        assert r_health.json()["trade_allowed"] is True

        r_acc = client.get("/account", headers=headers)
        assert r_acc.status_code == 200
        assert r_acc.json() == {"balance": 5000.0, "equity": 5050.0}

        r_tick = client.get("/tick/PETR4", headers=headers)
        assert r_tick.status_code == 200
        assert r_tick.json()["last"] == 38.50
        assert r_tick.json()["spread_pct"] > 0

        r_rates = client.get("/rates/PETR4?timeframe=15m&count=5", headers=headers)
        assert r_rates.status_code == 200
        assert r_rates.json()[0]["volume"] == 98000

        r_pos = client.get("/positions", headers=headers)
        assert r_pos.status_code == 200
        assert r_pos.json()[0]["ticket"] == 778899

        r_chk = client.post(
            "/order_check",
            headers=headers,
            json={"action": 1, "symbol": "PETR4", "volume": 100, "type": 0, "price": 38.51},
        )
        assert r_chk.status_code == 200
        assert r_chk.json()["retcode"] == 0

        r_sl = client.post(
            "/modify_sl",
            headers=headers,
            json={"position": 778899, "symbol": "PETR4", "sl": 37.256},
        )
        assert r_sl.status_code == 200
        assert r_sl.json()["retcode"] == 10009
        assert r_sl.json()["sl"] == 37.26
    finally:
        stop_evt.set()
        worker.join(timeout=1.0)


def test_mql5_rpc_timeout_sets_last_error(tmp_path: Path) -> None:
    backend = MQL5FileRPCBackend(tmp_path, default_timeout=0.5)
    assert backend.symbol_info("VALE3") is None
    code, msg = backend.last_error()
    assert code == -10005
    assert "timeout" in msg.lower()


def test_mql5_rpc_health_waiting_broker_auth_non_blocking(tmp_path: Path, monkeypatch: Any) -> None:
    backend = MQL5FileRPCBackend(tmp_path, default_timeout=2.0)
    (tmp_path / "heartbeat.json").write_text(
        json.dumps(
            {
                "ok": True,
                "connected": False,
                "terminal_connected": False,
                "trade_allowed": False,
                "terminal_trade_allowed": True,
                "expert_trade_allowed": True,
                "login": 1001,
                "server": "ClearInvestimentos-CLEAR",
                "currency": "BRL",
                "balance": 0.0,
                "equity": 0.0,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bridge_app, "mt5", backend)
    monkeypatch.setenv("BRIDGE_API_KEY", "unit-test-key")
    headers = {"X-Bridge-Api-Key": "unit-test-key"}
    client = TestClient(bridge_app.app)

    t0 = time.monotonic()
    r_health = client.get("/health", headers=headers)
    elapsed = time.monotonic() - t0

    assert r_health.status_code == 200
    assert elapsed < 0.5
    body = r_health.json()
    assert body["status"] == "waiting_broker_auth"
    assert body["connected"] is False
    assert body["ea_alive"] is True
    assert body["expert_trade_allowed"] is True
    assert body["server"] == "ClearInvestimentos-CLEAR"
