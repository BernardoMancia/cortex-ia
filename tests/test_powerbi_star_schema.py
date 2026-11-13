from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from fastapi.testclient import TestClient

from bi.metrics import (
    compute_curve_risk_metrics,
    compute_decisions_summary,
    compute_monthly_returns,
    compute_pnl_by_ticker_and_sector,
    compute_trade_metrics,
    enrich_equity_series,
)
from config.b3_universe import ALL_B3_TICKERS, SECTOR_MAP
from config.settings import settings
from domain.b3_rules import add_b3_business_days, get_brt_now
from security.bi_tokens import is_ip_allowed_by_cidr
from security.rate_limit import (
    bi_api_limiter,
    login_ip_limiter,
    mutation_api_limiter,
    step_up_limiter,
)
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection
from storage.wallets_repo import WalletsRepository
from web.server import create_web_app

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bootstrap_luke_client(app) -> tuple[TestClient, str, str]:
    login_ip_limiter.reset("testclient")
    step_up_limiter.reset("1:testclient")
    mutation_api_limiter.reset("mut:1:testclient")
    bi_api_limiter.reset("bi_sess:testclient")

    client = TestClient(app, headers={"User-Agent": "CortexBiSuite/1.0"})
    r_login = client.post("/api/auth/login", json={"username": "Luke", "password": "Luke"})
    assert r_login.status_code == 200
    csrf = r_login.json()["csrf_token"]

    r_fl = client.post(
        "/api/auth/first-login",
        json={"new_password": "Luke#BiMaster2026!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r_fl.status_code == 200
    csrf = r_fl.json().get("csrf_token", csrf)

    r_su = client.post(
        "/api/security/step-up",
        json={"password": "Luke#BiMaster2026!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert r_su.status_code == 200
    step_up = r_su.json()["step_up_token"]
    return client, csrf, step_up


def _seed_wallet_analytical_data(db: DatabaseConnection, user_id: int, wallet_id: int) -> None:
    now = get_brt_now()
    today_iso = now.date().isoformat()
    ts_iso = now.isoformat()
    settle_iso = add_b3_business_days(now.date(), 2).isoformat()

    curve_points = [
        ("2026-10-01", "2026-10-01T17:55:00-03:00", 100000.0, 100000.0, 80000.0, 20000.0, 0.0, 0.0, 0.0),
        ("2026-10-15", "2026-10-15T17:55:00-03:00", 105000.0, 103000.0, 73000.0, 30000.0, 2000.0, 5.0, 1.2),
        ("2026-10-28", "2026-10-28T17:55:00-03:00", 99750.0, 97750.0, 70000.0, 27750.0, 2000.0, -0.25, -1.8),
        (today_iso, ts_iso, 108500.0, 105500.0, 75500.0, 30000.0, 3000.0, 8.5, 1.5),
    ]

    with db.transaction() as conn:
        conn.execute(
            """
            UPDATE user_wallets
            SET initial_capital = 100000.0,
                free_cash = 75500.0,
                ai_allocated_capital = 90000.0,
                vault_reserve_cash = 3000.0
            WHERE id = ? AND user_id = ?
            """,
            (wallet_id, user_id),
        )
        for snap_dt, snap_ts, tot_eq, op_eq, fc, alloc, vault, pnl_p, dpnl_p in curve_points:
            conn.execute(
                """
                INSERT INTO equity_snapshots (
                    user_id, wallet_id, total_equity, operational_equity,
                    free_cash, allocated_capital, vault_reserve_cash, pnl_percent,
                    daily_pnl_percent, snapshot_date, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, wallet_id, tot_eq, op_eq, fc, alloc, vault, pnl_p, dpnl_p, snap_dt, snap_ts),
            )

        conn.execute(
            """
            INSERT INTO positions (
                user_id, wallet_id, ticker, quantity, entry_price, fiscal_avg_price,
                current_price, highest_price, stop_loss, partial_tp_taken, do_not_touch, opened_at, updated_at
            ) VALUES (?, ?, 'PETR4', 500, 36.00, 36.01, 40.00, 41.20, 35.50, 0, 1, '2026-10-05T11:10:00-03:00', ?)
            """,
            (user_id, wallet_id, ts_iso),
        )
        conn.execute(
            """
            INSERT INTO positions (
                user_id, wallet_id, ticker, quantity, entry_price, fiscal_avg_price,
                current_price, highest_price, stop_loss, partial_tp_taken, do_not_touch, opened_at, updated_at
            ) VALUES (?, ?, 'VALE3', 100, 62.00, 62.02, 100.00, 101.00, 90.00, 1, 0, '2026-10-12T14:20:00-03:00', ?)
            """,
            (user_id, wallet_id, ts_iso),
        )

        trades_seed = [
            ("PETR4", "BUY", 500, 36.00, 18000.0, 5.40, 0.0, 32.40, 0, "coid-buy-1", "Entrada quantitativa", "2026-10-05T11:10:00-03:00"),
            ("ITUB4", "BUY", 400, 30.00, 12000.0, 3.60, 0.0, 27.00, 0, "coid-buy-2", "Breakout bancario", "2026-10-10T10:30:00-03:00"),
            ("ITUB4", "SELL", 400, 35.00, 14000.0, 4.20, 1992.20, 31.50, 0, "coid-sell-1", "Alvo atingido", "2026-10-20T15:40:00-03:00"),
            ("BBDC4", "SELL", 300, 14.00, 4200.0, 1.26, -450.00, 14.10, 0, "coid-sell-2", "Stop tecnico", ts_iso),
        ]
        for tk, act, qty, px, tot_v, fee, rpnl, sl, is_dt, coid, reas, tts in trades_seed:
            conn.execute(
                """
                INSERT INTO trades (
                    user_id, wallet_id, ticker, action, quantity, price, total_value,
                    fee, realized_pnl, stop_loss, is_day_trade, client_order_id, reasoning, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, wallet_id, tk, act, qty, px, tot_v, fee, rpnl, sl, is_dt, coid, reas, tts),
            )

        decisions_seed = [
            ("PETR4", "BUY", 0.88, "STRONG_BUY", 0.65, "Fluxo institucional positivo", ts_iso),
            ("VALE3", "HOLD", 0.62, "NEUTRAL", 0.10, "Aguardando volume acima da media", ts_iso),
            ("BBDC4", "SELL", 0.79, "SELL", -0.45, "Perda de suporte", ts_iso),
            ("MARKET", "HOLD", 0.42, "NEUTRAL", 0.0, "Monitoramento BOVA11", ts_iso),
        ]
        for tk, act, conf, sig, sent, reas, dts in decisions_seed:
            conn.execute(
                """
                INSERT INTO ai_decisions (
                    user_id, wallet_id, ticker, action, confidence,
                    trend_signal, sentiment_score, reasoning, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, wallet_id, tk, act, conf, sig, sent, reas, dts),
            )

        conn.execute(
            """
            INSERT INTO d2_settlements (
                user_id, wallet_id, trade_date, settlement_date, net_sell_amount, settled, updated_at
            ) VALUES (?, ?, ?, ?, 4198.74, 0, ?)
            """,
            (user_id, wallet_id, today_iso, settle_iso, ts_iso),
        )
        conn.execute(
            """
            INSERT INTO capital_transactions (
                user_id, wallet_id, tx_type, amount, resulting_ai_capital, resulting_vault, details, timestamp
            ) VALUES (?, ?, 'VAULT_DEPOSIT', 3000.0, 90000.0, 3000.0, 'Reserva blindada mensal', ?)
            """,
            (user_id, wallet_id, ts_iso),
        )
        conn.execute(
            """
            INSERT INTO market_snapshots (
                ticker, price, bid, ask, spread_pct, volume, change_pct,
                rsi, macd_hist, atr, adx, trend_signal, mtf_bullish,
                sentiment_score, is_stale, updated_at
            ) VALUES ('BOVA11', 129.50, 129.48, 129.52, 0.03, 18500000.0, 0.85, 56.2, 0.32, 1.45, 27.5, 'BUY', 1, 0.25, 0, ?)
            """,
            (ts_iso,),
        )
        conn.execute(
            """
            INSERT INTO market_snapshots (
                ticker, price, bid, ask, spread_pct, volume, change_pct,
                rsi, macd_hist, atr, adx, trend_signal, mtf_bullish,
                sentiment_score, is_stale, updated_at
            ) VALUES ('PETR4', 40.00, 39.99, 40.01, 0.05, 42000000.0, 1.92, 61.4, 0.48, 0.92, 31.0, 'STRONG_BUY', 1, 0.55, 0, ?)
            """,
            (ts_iso,),
        )


def test_quantitative_metrics_math_and_powerbi_static_kit():
    raw_curve = [
        {"id": 1, "snapshot_date": "2026-10-01", "timestamp": "2026-10-01T17:55:00-03:00", "initial_capital": 100000.0, "total_equity": 100000.0, "operational_equity": 100000.0, "free_cash": 100000.0, "allocated_capital": 0.0, "vault_reserve_cash": 0.0, "pnl_percent": 0.0, "daily_pnl_percent": 0.0},
        {"id": 2, "snapshot_date": "2026-10-02", "timestamp": "2026-10-02T17:55:00-03:00", "initial_capital": 100000.0, "total_equity": 105000.0, "operational_equity": 105000.0, "free_cash": 80000.0, "allocated_capital": 25000.0, "vault_reserve_cash": 0.0, "pnl_percent": 5.0, "daily_pnl_percent": 5.0},
        {"id": 3, "snapshot_date": "2026-10-05", "timestamp": "2026-10-05T17:55:00-03:00", "initial_capital": 100000.0, "total_equity": 99750.0, "operational_equity": 99750.0, "free_cash": 80000.0, "allocated_capital": 19750.0, "vault_reserve_cash": 0.0, "pnl_percent": -0.25, "daily_pnl_percent": -5.0},
        {"id": 4, "snapshot_date": "2026-11-03", "timestamp": "2026-11-03T17:55:00-03:00", "initial_capital": 100000.0, "total_equity": 108000.0, "operational_equity": 108000.0, "free_cash": 83000.0, "allocated_capital": 25000.0, "vault_reserve_cash": 0.0, "pnl_percent": 8.0, "daily_pnl_percent": 8.27},
        {"id": 5, "snapshot_date": "2026-11-04", "timestamp": "2026-11-04T17:55:00-03:00", "initial_capital": 100000.0, "total_equity": 110000.0, "operational_equity": 110000.0, "free_cash": 85000.0, "allocated_capital": 25000.0, "vault_reserve_cash": 0.0, "pnl_percent": 10.0, "daily_pnl_percent": 1.85},
    ]
    trades = [
        {"ticker": "PETR4", "action": "BUY", "realized_pnl": 0.0, "fee": 5.0, "timestamp": "2026-10-02T11:00:00-03:00"},
        {"ticker": "ITUB4", "action": "SELL", "realized_pnl": 2000.0, "fee": 6.0, "timestamp": "2026-10-15T15:00:00-03:00"},
        {"ticker": "BBDC4", "action": "SELL", "realized_pnl": -500.0, "fee": 4.0, "timestamp": "2026-11-03T14:00:00-03:00"},
    ]
    positions = [
        {"ticker": "PETR4", "unrealized_pnl": 1200.0},
    ]
    decisions = [
        {"action": "BUY", "confidence": 0.90},
        {"action": "SELL", "confidence": 0.75},
        {"action": "HOLD", "confidence": 0.60},
        {"action": "HOLD", "confidence": 0.40},
    ]

    enriched = enrich_equity_series(raw_curve, trade_rows=trades, initial_capital=100000.0)
    assert len(enriched) == 5
    assert enriched[2]["high_water_mark"] == 105000.0
    assert enriched[2]["drawdown_pct"] == -5.0
    assert enriched[4]["drawdown_pct"] == 0.0

    risk = compute_curve_risk_metrics(enriched, initial_capital=100000.0)
    assert risk["max_drawdown_pct"] == -5.0
    assert risk["current_drawdown_pct"] == 0.0
    assert risk["sharpe_annualized"] > 0.0
    assert risk["sortino_annualized"] > 0.0
    assert risk["calmar_ratio"] > 0.0
    assert risk["volatility_annualized_pct"] > 0.0
    assert risk["positive_days_pct"] == 75.0

    t_metrics = compute_trade_metrics(trades)
    assert t_metrics["buy_trades"] == 1
    assert t_metrics["sell_trades"] == 2
    assert t_metrics["winning_sells"] == 1
    assert t_metrics["losing_sells"] == 1
    assert t_metrics["win_rate_pct"] == 50.0
    assert t_metrics["profit_factor"] == 4.0
    assert t_metrics["payoff_ratio"] == 4.0
    assert t_metrics["expectancy_brl"] == 750.0
    assert t_metrics["total_fees"] == 15.0

    monthly = compute_monthly_returns(enriched, trades)
    assert [m["year_month"] for m in monthly] == ["2026-10", "2026-11"]
    assert monthly[0]["pnl_brl"] == -250.0
    assert monthly[0]["return_pct"] == -0.25
    assert monthly[1]["pnl_brl"] == 10250.0
    assert monthly[1]["return_pct"] == 10.28

    by_tk, by_sec = compute_pnl_by_ticker_and_sector(trades, positions, SECTOR_MAP)
    tk_map = {item["ticker"]: item for item in by_tk}
    assert tk_map["ITUB4"]["total_pnl"] == 2000.0
    assert tk_map["PETR4"]["unrealized_pnl"] == 1200.0
    assert len(by_sec) >= 2

    dec_sum = compute_decisions_summary(decisions)
    assert dec_sum["total"] == 4
    assert dec_sum["buy_count"] == 1
    assert dec_sum["sell_count"] == 1
    assert dec_sum["hold_count"] == 2
    assert [b["count"] for b in dec_sum["confidence_buckets"]] == [1, 1, 1, 1]

    pbi_dir = PROJECT_ROOT / "powerbi"
    prod_pbids = json.loads((pbi_dir / "cortex_ia_prod.pbids").read_text(encoding="utf-8"))
    homolog_pbids = json.loads((pbi_dir / "cortex_ia_homolog.pbids").read_text(encoding="utf-8"))
    assert prod_pbids["connections"][0]["details"]["address"]["url"] == "http://localhost:8003/api/bi/v1/metadata"
    assert homolog_pbids["connections"][0]["details"]["address"]["url"] == "http://localhost:9003/api/bi/v1/metadata"

    pq_text = (pbi_dir / "queries_star_schema.pq").read_text(encoding="utf-8")
    assert "CortexFetch" in pq_text
    assert "Web.Contents" in pq_text
    assert "Authorization = \"Bearer \"" in pq_text

    dax_text = (pbi_dir / "dax_measures.dax").read_text(encoding="utf-8")
    measure_defs = [ln for ln in dax_text.splitlines() if ln.strip().endswith("=")]
    assert len(measure_defs) >= 25

    theme_json = json.loads((pbi_dir / "cortex_dark_theme.json").read_text(encoding="utf-8"))
    assert theme_json["background"] == "#0A0E17"
    assert (pbi_dir / "README_POWERBI.md").exists()


def test_bi_tokens_step_up_star_schema_endpoints_and_pbids_download(tmp_path):
    db = DatabaseConnection(tmp_path / "bi_star_schema.db")
    wallets_repo = WalletsRepository(db)
    ctype = "paper_b3" if settings.IS_SIMULATION else "clear_mt5"
    w1 = wallets_repo.create_wallet(
        user_id=1,
        name="Carteira Quant Principal",
        connector_type=ctype,
        connector_config={},
        initial_capital=100000.0,
        make_default=True,
    )
    w2 = wallets_repo.create_wallet(
        user_id=1,
        name="Carteira Empty Nova",
        connector_type=ctype,
        connector_config={},
        initial_capital=50000.0,
        make_default=False,
    )
    _seed_wallet_analytical_data(db, user_id=1, wallet_id=int(w1["id"]))

    app = create_web_app(orchestrator=None, db=db)
    luke_client, luke_csrf, luke_step_up = _bootstrap_luke_client(app)

    no_su = luke_client.post(
        "/api/bi/keys",
        json={"label": "Sem StepUp"},
        headers={"X-CSRF-Token": luke_csrf},
    )
    assert no_su.status_code == 403
    assert no_su.json()["detail"]["code"] == "STEP_UP_REQUIRED"

    created_res = luke_client.post(
        "/api/bi/keys",
        json={
            "label": "Power BI Institucional Luke",
            "wallet_id": None,
            "expires_in_days": 90,
            "allowed_cidr": "",
            "include_admin": True,
        },
        headers={"X-CSRF-Token": luke_csrf, "X-Step-Up-Token": luke_step_up},
    )
    assert created_res.status_code == 200
    created_data = created_res.json()
    raw_bi_token = created_data["raw_token"]
    assert raw_bi_token.startswith("ctx_bi_")
    assert len(raw_bi_token) > 36
    assert "token_sha256" not in created_data["key"]
    assert "raw_token" not in created_data["key"]

    listed = luke_client.get("/api/bi/keys")
    assert listed.status_code == 200
    keys_list = listed.json()["keys"]
    assert len(keys_list) == 1
    assert keys_list[0]["token_prefix"] == raw_bi_token[:14]
    assert "token_sha256" not in keys_list[0]
    assert "raw_token" not in keys_list[0]

    dl_resp = luke_client.get(
        "/api/bi/download/pbids",
        headers={"Host": "bi.cortex-local.test:8443"},
    )
    assert dl_resp.status_code == 200
    expected_fn = "cortex_ia_prod.pbids" if settings.IS_PRODUCTION else "cortex_ia_homolog.pbids"
    assert expected_fn in dl_resp.headers.get("Content-Disposition", "")
    dl_json = dl_resp.json()
    assert dl_json["version"] == "0.1"
    assert dl_json["connections"][0]["details"]["address"]["url"] == "http://bi.cortex-local.test:8443/api/bi/v1/metadata"

    pbi_client = TestClient(app, headers={"Authorization": f"Bearer {raw_bi_token}"})

    meta = pbi_client.get("/api/bi/v1/metadata")
    assert meta.status_code == 200
    meta_json = meta.json()
    assert meta_json["environment"] == settings.ENVIRONMENT_ID
    assert meta_json["is_simulation"] == settings.IS_SIMULATION
    assert meta_json["version"] == "0.2.0"
    assert "admin_platform_overview" in meta_json
    expected_wallets_total = len(wallets_repo.list_for_user(1))
    assert meta_json["admin_platform_overview"]["active_users_count"] >= 1
    assert meta_json["admin_platform_overview"]["active_wallets_count"] == expected_wallets_total

    post_on_bi = pbi_client.post("/api/bi/v1/metadata", json={})
    assert post_on_bi.status_code == 405

    table_endpoints = [
        "/api/bi/v1/dim_wallets",
        "/api/bi/v1/dim_assets_b3",
        "/api/bi/v1/dim_calendar_b3",
        "/api/bi/v1/fact_equity",
        "/api/bi/v1/fact_positions",
        "/api/bi/v1/fact_trades",
        "/api/bi/v1/fact_decisions",
        "/api/bi/v1/fact_cashflow_d2",
        "/api/bi/v1/fact_market_radar",
        "/api/bi/v1/kpi_summary",
    ]
    for ep in table_endpoints:
        env_resp = pbi_client.get(ep)
        assert env_resp.status_code == 200, ep
        env_body = env_resp.json()
        assert env_body["success"] is True
        assert env_body["environment"] == settings.ENVIRONMENT_ID
        assert env_body["is_simulation"] == settings.IS_SIMULATION
        assert isinstance(env_body["rows"], list)

        flat_resp = pbi_client.get(f"{ep}?flat=true")
        assert flat_resp.status_code == 200, ep
        flat_rows = flat_resp.json()
        assert isinstance(flat_rows, list)
        if flat_rows:
            assert flat_rows[0]["environment"] == settings.ENVIRONMENT_ID
            assert flat_rows[0]["is_simulation"] == settings.IS_SIMULATION

    assets_rows = pbi_client.get("/api/bi/v1/dim_assets_b3?flat=true").json()
    assert len(assets_rows) == len(ALL_B3_TICKERS) == 148
    assets_by_tk = {r["ticker"]: r for r in assets_rows}
    assert assets_by_tk["PETR4"]["standard_lot_size"] == 100
    assert assets_by_tk["PETR4"]["swing_20k_exempt_eligible"] is True
    assert assets_by_tk["BOVA11"]["standard_lot_size"] == 1
    assert assets_by_tk["BOVA11"]["swing_20k_exempt_eligible"] is False

    cal_rows = pbi_client.get("/api/bi/v1/dim_calendar_b3?days=60&flat=true").json()
    assert len(cal_rows) == 60
    sample_day = cal_rows[10]
    expected_d2 = add_b3_business_days(date.fromisoformat(sample_day["date"]), 2).isoformat()
    assert sample_day["d2_settlement_date"] == expected_d2

    cashflow_env = pbi_client.get("/api/bi/v1/fact_cashflow_d2").json()
    assert len(cashflow_env["d2_settlements"]) == 1
    assert len(cashflow_env["capital_transactions"]) == 1
    assert len(cashflow_env["tax_20k_summary"]) == expected_wallets_total

    bundle_w1 = pbi_client.get(f"/api/bi/v1/mobile_bundle?wallet_id={w1['id']}").json()
    required_bundle_keys = {
        "success",
        "schema_version",
        "environment",
        "environment_label",
        "is_production",
        "is_simulation",
        "generated_at",
        "market_open",
        "market_status",
        "bova11_change_pct",
        "regime_bearish_veto",
        "active_wallet",
        "wallets",
        "kpi_summary",
        "equity_series",
        "monthly_returns",
        "positions",
        "sector_exposure",
        "pnl_by_ticker",
        "pnl_by_sector",
        "cashflow_d2",
        "decisions_summary",
        "market_radar",
    }
    assert required_bundle_keys.issubset(bundle_w1.keys())
    assert bundle_w1["environment"] == settings.ENVIRONMENT_ID
    assert bundle_w1["is_simulation"] == settings.IS_SIMULATION
    assert bundle_w1["kpi_summary"]["max_drawdown_pct"] == -5.0
    assert len(bundle_w1["positions"]) == 2
    assert len(bundle_w1["sector_exposure"]) >= 1

    bundle_empty_w2 = pbi_client.get(f"/api/bi/v1/mobile_bundle?wallet_id={w2['id']}").json()
    assert len(bundle_empty_w2["equity_series"]) == 1
    assert bundle_empty_w2["equity_series"][0]["total_equity"] == 50000.0


def test_readonly_firewall_anti_idor_cidr_and_revocation(tmp_path):
    db = DatabaseConnection(tmp_path / "bi_security_firewall.db")
    wallets_repo = WalletsRepository(db)
    ctype = "paper_b3" if settings.IS_SIMULATION else "clear_mt5"
    luke_w1 = wallets_repo.create_wallet(
        user_id=1,
        name="Luke Carteira 1",
        connector_type=ctype,
        connector_config={},
        initial_capital=80000.0,
        make_default=True,
    )
    luke_w2 = wallets_repo.create_wallet(
        user_id=1,
        name="Luke Carteira 2",
        connector_type=ctype,
        connector_config={},
        initial_capital=40000.0,
        make_default=False,
    )

    app = create_web_app(orchestrator=None, db=db)
    luke_client, luke_csrf, luke_step_up = _bootstrap_luke_client(app)

    scoped_res = luke_client.post(
        "/api/bi/keys",
        json={
            "label": "Token Escopo Carteira 1",
            "wallet_id": int(luke_w1["id"]),
            "expires_in_days": 30,
            "allowed_cidr": "",
            "include_admin": False,
        },
        headers={"X-CSRF-Token": luke_csrf, "X-Step-Up-Token": luke_step_up},
    )
    assert scoped_res.status_code == 200
    scoped_key_id = int(scoped_res.json()["key"]["id"])
    scoped_token = scoped_res.json()["raw_token"]

    bi_headers = {"Authorization": f"Bearer {scoped_token}"}
    fw_client = TestClient(app, headers=bi_headers)

    assert fw_client.get("/api/status").status_code == 403
    assert fw_client.post(
        "/api/wallets",
        json={"name": "Tentativa Escrita", "connector_type": ctype},
    ).status_code == 403
    assert fw_client.post(
        "/api/capital/withdraw",
        json={"amount": 500.0, "mode": "instant"},
    ).status_code == 403
    assert fw_client.post("/api/system/panic", json={"scope": "wallet"}).status_code == 403
    assert fw_client.post("/api/system/panic/activate", json={"scope": "wallet"}).status_code == 403
    assert fw_client.get("/api/bi/keys").status_code == 403

    audit_repo = AuditRepository(db)
    events = audit_repo.list_events(limit=50)
    violation_events = [e for e in events if e["event_type"] == "BI_TOKEN_SCOPE_VIOLATION"]
    assert len(violation_events) >= 5
    assert audit_repo.verify_integrity()["valid"] is True

    meta_non_admin = fw_client.get("/api/bi/v1/metadata").json()
    assert "admin_platform_overview" not in meta_non_admin

    ok_w1 = fw_client.get(f"/api/bi/v1/fact_trades?wallet_id={luke_w1['id']}")
    assert ok_w1.status_code == 200
    blocked_w2 = fw_client.get(f"/api/bi/v1/fact_trades?wallet_id={luke_w2['id']}")
    assert blocked_w2.status_code == 403

    dim_w_scoped = fw_client.get("/api/bi/v1/dim_wallets?flat=true").json()
    assert [w["wallet_id"] for w in dim_w_scoped] == [int(luke_w1["id"])]

    create_marina = luke_client.post(
        "/api/admin/users",
        json={
            "username": "MarinaQuant",
            "temporary_password": "Temp#Marina2026!",
            "create_initial_wallet": True,
            "wallet_name": "Marina Swing B3",
            "connector_type": ctype,
            "initial_capital": 30000.0,
        },
        headers={"X-CSRF-Token": luke_csrf, "X-Step-Up-Token": luke_step_up},
    )
    assert create_marina.status_code in (200, 201)
    marina_uid = int(create_marina.json()["user"]["id"])

    step_up_limiter.reset(f"{marina_uid}:testclient")
    mutation_api_limiter.reset(f"mut:{marina_uid}:testclient")

    marina_client = TestClient(app, headers={"User-Agent": "MarinaBrowser/1.0"})
    m_login = marina_client.post(
        "/api/auth/login",
        json={"username": "MarinaQuant", "password": "Temp#Marina2026!"},
    )
    assert m_login.status_code == 200
    m_csrf = m_login.json()["csrf_token"]
    m_fl = marina_client.post(
        "/api/auth/first-login",
        json={"new_password": "Marina#Strong2026!"},
        headers={"X-CSRF-Token": m_csrf},
    )
    assert m_fl.status_code == 200
    m_csrf = m_fl.json().get("csrf_token", m_csrf)
    m_su = marina_client.post(
        "/api/security/step-up",
        json={"password": "Marina#Strong2026!"},
        headers={"X-CSRF-Token": m_csrf},
    )
    assert m_su.status_code == 200
    m_step_up = m_su.json()["step_up_token"]

    denied_admin_scope = marina_client.post(
        "/api/bi/keys",
        json={"label": "Tentativa Admin", "include_admin": True},
        headers={"X-CSRF-Token": m_csrf, "X-Step-Up-Token": m_step_up},
    )
    assert denied_admin_scope.status_code == 403

    denied_luke_wallet_key = marina_client.post(
        "/api/bi/keys",
        json={"label": "Tentativa IDOR Carteira Luke", "wallet_id": int(luke_w1["id"])},
        headers={"X-CSRF-Token": m_csrf, "X-Step-Up-Token": m_step_up},
    )
    assert denied_luke_wallet_key.status_code == 403

    marina_key_res = marina_client.post(
        "/api/bi/keys",
        json={"label": "Marina Power BI", "expires_in_days": 60},
        headers={"X-CSRF-Token": m_csrf, "X-Step-Up-Token": m_step_up},
    )
    assert marina_key_res.status_code == 200
    marina_bi_tok = marina_key_res.json()["raw_token"]

    marina_bi_client = TestClient(app, headers={"Authorization": f"Bearer {marina_bi_tok}"})
    marina_wallets = marina_bi_client.get("/api/bi/v1/dim_wallets?flat=true").json()
    assert len(marina_wallets) == 1
    assert marina_wallets[0]["user_id"] == marina_uid
    assert marina_bi_client.get(f"/api/bi/v1/fact_equity?wallet_id={luke_w1['id']}").status_code == 403
    assert marina_client.delete(
        f"/api/bi/keys/{scoped_key_id}",
        headers={"X-CSRF-Token": m_csrf},
    ).status_code == 404

    assert is_ip_allowed_by_cidr("10.200.50.12", "10.200.50.0/24") is True
    assert is_ip_allowed_by_cidr("192.168.0.5", "10.200.50.0/24") is False

    cidr_key_res = luke_client.post(
        "/api/bi/keys",
        json={
            "label": "Token Restrito VPN",
            "allowed_cidr": "10.200.50.0/24",
            "expires_in_days": 30,
        },
        headers={"X-CSRF-Token": luke_csrf, "X-Step-Up-Token": luke_step_up},
    )
    assert cidr_key_res.status_code == 200
    cidr_tok = cidr_key_res.json()["raw_token"]
    cidr_client = TestClient(app, headers={"Authorization": f"Bearer {cidr_tok}"})
    assert cidr_client.get("/api/bi/v1/metadata").status_code == 401

    rev_resp = luke_client.post(
        f"/api/bi/keys/{scoped_key_id}/revoke",
        json={},
        headers={"X-CSRF-Token": luke_csrf},
    )
    assert rev_resp.status_code == 200
    assert fw_client.get("/api/bi/v1/metadata").status_code == 401
    assert audit_repo.verify_integrity()["valid"] is True
