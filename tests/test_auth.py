import pytest
import time
from fastapi.testclient import TestClient
from auth import PasswordManager, RateLimiter, MAX_IDLE_SECONDS
from data.database import DatabaseManager
from dashboard.app import app

@pytest.fixture
def temp_db(tmp_path):
    db_file = tmp_path / "test_auth.db"
    db = DatabaseManager(db_path=str(db_file))
    return db

class TestPasswordManager:
    def test_hash_and_verify(self):
        p_hash, salt = PasswordManager.hash_password("MinhaSenhaForte123!")
        assert len(p_hash) == 64
        assert len(salt) == 64
        assert PasswordManager.verify_password("MinhaSenhaForte123!", p_hash, salt) is True
        assert PasswordManager.verify_password("SenhaErrada", p_hash, salt) is False

    def test_unique_salts(self):
        h1, s1 = PasswordManager.hash_password("mesmasenha")
        h2, s2 = PasswordManager.hash_password("mesmasenha")
        assert s1 != s2
        assert h1 != h2

class TestRateLimiter:
    def test_lockout_after_max_attempts(self):
        rl = RateLimiter(max_attempts=3, lockout_seconds=10)
        ip = "192.168.1.100"
        
        att, lock = rl.record_failure(ip)
        assert att == 1 and lock == 0
        assert rl.is_locked(ip)[0] is False

        att, lock = rl.record_failure(ip)
        assert att == 2 and lock == 0
        assert rl.is_locked(ip)[0] is False

        att, lock = rl.record_failure(ip)
        assert att == 3 and lock == 10
        is_locked, rem = rl.is_locked(ip)
        assert is_locked is True
        assert rem > 0

    def test_reset_limiter(self):
        rl = RateLimiter(max_attempts=3, lockout_seconds=10)
        ip = "192.168.1.101"
        rl.record_failure(ip)
        rl.reset(ip)
        assert rl.is_locked(ip)[0] is False

class TestDatabaseAuth:
    def test_default_admin_seeded(self, temp_db):
        user = temp_db.get_user_by_username("Admin")
        assert user is not None
        assert user["must_change_password"] == 1
        assert PasswordManager.verify_password("Admin", user["password_hash"], user["salt"]) is True

    def test_sql_injection_prevention(self, temp_db):
        user = temp_db.get_user_by_username("' OR '1'='1")
        assert user is None

        user = temp_db.get_user_by_username("Admin' --")
        assert user is None

    def test_update_credentials_first_login(self, temp_db):
        user = temp_db.get_user_by_username("Admin")
        ok = temp_db.update_user_credentials(user["id"], "trader_pro", "NovaSenhaSuperSecreta@2026")
        assert ok is True

        old_user = temp_db.get_user_by_username("Admin")
        assert old_user is None

        updated_user = temp_db.get_user_by_username("trader_pro")
        assert updated_user is not None
        assert updated_user["must_change_password"] == 0
        assert PasswordManager.verify_password("NovaSenhaSuperSecreta@2026", updated_user["password_hash"], updated_user["salt"]) is True

    def test_sessions_and_idle_timeout(self, temp_db):
        user = temp_db.get_user_by_username("Admin")
        token = temp_db.create_session(user["id"], ip_address="127.0.0.1", max_session_seconds=3600)
        assert token is not None

        sess = temp_db.get_valid_session(token, max_idle_seconds=60)
        assert sess is not None

        temp_db.revoke_session(token)
        sess_revoked = temp_db.get_valid_session(token, max_idle_seconds=60)
        assert sess_revoked is None

class TestAuthApiFlow:
    def test_full_login_and_first_access_flow(self):
        db = DatabaseManager()
        if not db.get_user_by_username("Admin"):
            db.create_user("Admin", "Admin", must_change_password=True)
        else:
            db.reset_user_password("Admin", "Admin", force_first_login=True)

        client = TestClient(app)

        res_unauth = client.get("/api/status")
        assert res_unauth.status_code == 401

        res_wrong = client.post("/api/auth/login", json={"username": "Admin", "password": "wrongpassword"})
        assert res_wrong.status_code == 401

        res_admin = client.post("/api/auth/login", json={"username": "Admin", "password": "Admin"})
        assert res_admin.status_code == 200
        data_admin = res_admin.json()
        assert data_admin["must_change_password"] is True

        res_first = client.post("/api/auth/first-login", json={
            "current_username": "Admin",
            "current_password": "Admin",
            "new_username": f"user_test_{int(time.time())}",
            "new_password": "MinhaSenhaForte@2026"
        })
        assert res_first.status_code == 200
        assert "cortex_session" in client.cookies

        res_status = client.get("/api/status")
        assert res_status.status_code == 200

        res_logout = client.post("/api/auth/logout")
        assert res_logout.status_code == 200
