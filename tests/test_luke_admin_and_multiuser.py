from __future__ import annotations

from datetime import timedelta
import sqlite3

from fastapi.testclient import TestClient
import pytest

from domain.b3_rules import get_brt_now
from security.rate_limit import login_ip_limiter, mutation_api_limiter, step_up_limiter
from storage.connection import DatabaseConnection
from storage.users_repo import UsersRepository
from web.server import create_web_app


def test_sqlite_ddl_forbids_any_second_admin_or_luke_impersonation(tmp_path):
    db = DatabaseConnection(tmp_path / "ddl_luke.db")
    now_iso = get_brt_now().isoformat()

    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    username, password_hash, salt, is_owner_admin, is_active,
                    must_change_password, created_at, updated_at
                ) VALUES ('IntruderAdmin', 'h', 's', 1, 1, 0, ?, ?)
                """,
                (now_iso, now_iso),
            )

    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute("DELETE FROM users WHERE id = 1")


def test_luke_exclusive_admin_panel_multiuser_and_anti_idor(tmp_path):
    login_ip_limiter.reset("testclient")
    step_up_limiter.reset("1:testclient")
    mutation_api_limiter.reset("1")

    db = DatabaseConnection(tmp_path / "multiuser.db")
    app = create_web_app(orchestrator=None, db=db)
    ua = {"User-Agent": "CortexPytest/1.0"}

    luke_client = TestClient(app, headers=ua)
    r1 = luke_client.post("/api/auth/login", json={"username": "Luke", "password": "Luke"})
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["must_change_password"] is True
    luke_csrf = d1["csrf_token"]

    r_fl = luke_client.post(
        "/api/auth/first-login",
        json={"new_password": "Luke#Master2026!"},
        headers={"X-CSRF-Token": luke_csrf},
    )
    assert r_fl.status_code == 200
    luke_csrf = r_fl.json().get("csrf_token", luke_csrf)

    me_luke = luke_client.get("/api/auth/me").json()
    assert me_luke["user"]["username"] == "Luke"
    assert me_luke["user"]["is_owner_admin"] is True

    st_luke = luke_client.get("/api/status")
    assert st_luke.status_code == 200

    adm_users = luke_client.get("/api/admin/users")
    assert adm_users.status_code == 200

    su_resp = luke_client.post(
        "/api/security/step-up",
        json={"password": "Luke#Master2026!"},
        headers={"X-CSRF-Token": luke_csrf},
    )
    assert su_resp.status_code == 200
    step_up_tok = su_resp.json()["step_up_token"]

    create_u = luke_client.post(
        "/api/admin/users",
        json={"username": "CarlosTrader", "temporary_password": "Temp#User2026!"},
        headers={"X-CSRF-Token": luke_csrf, "X-Step-Up-Token": step_up_tok},
    )
    assert create_u.status_code in (200, 201)

    carlos_client = TestClient(app, headers={"User-Agent": "CarlosBrowser/1.0"})
    rc1 = carlos_client.post(
        "/api/auth/login",
        json={"username": "CarlosTrader", "password": "Temp#User2026!"},
    )
    assert rc1.status_code == 200
    c_csrf = rc1.json()["csrf_token"]

    rc_fl = carlos_client.post(
        "/api/auth/first-login",
        json={"new_password": "Carlos#Strong2026!"},
        headers={"X-CSRF-Token": c_csrf},
    )
    assert rc_fl.status_code == 200
    c_csrf = rc_fl.json().get("csrf_token", c_csrf)

    me_carlos = carlos_client.get("/api/auth/me").json()
    assert me_carlos["user"]["is_owner_admin"] is False

    assert carlos_client.get("/api/admin/users").status_code == 403
    assert carlos_client.get("/api/admin/audit/verify").status_code == 403
    assert carlos_client.get("/api/logs").status_code == 403
    assert carlos_client.get("/api/system/preflight").status_code == 403

    idor_attempt = carlos_client.post(
        "/api/wallets/1/select",
        headers={"X-CSRF-Token": c_csrf},
    )
    assert idor_attempt.status_code in (403, 404)


def test_expired_24h_temporary_invite_blocks_login(tmp_path):
    login_ip_limiter.reset("testclient")
    db = DatabaseConnection(tmp_path / "invite_exp.db")
    users = UsersRepository(db)
    u = users.create_standard_user("ConvidadoExpirado", "Temp#Pass2026!", invite_hours=1)
    past_iso = (get_brt_now() - timedelta(hours=2)).isoformat()
    with db.transaction() as conn:
        conn.execute("UPDATE users SET invite_expires_at = ? WHERE id = ?", (past_iso, u["id"]))

    app = create_web_app(orchestrator=None, db=db)
    client = TestClient(app)
    resp = client.post(
        "/api/auth/login",
        json={"username": "ConvidadoExpirado", "password": "Temp#Pass2026!"},
    )
    assert resp.status_code == 403
