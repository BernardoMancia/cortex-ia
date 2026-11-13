from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
import pytest

from domain.financial_firewall import FinancialFirewall
from notifications.telegram_client import TelegramClient
from security.key_manager import KeyManager
from security.net_guard import parse_xml_safely, validate_external_https_url
from security.password import PasswordSecurity
from security.session_csrf import verify_csrf_header
from security.step_up import StepUpAuthManager
from security.totp_mfa import TOTPManager, render_qr_svg_offline
from security.wallet_crypto import WalletCryptoVault
from storage.audit_repo import AuditRepository
from storage.connection import DatabaseConnection
from storage.users_repo import UsersRepository
from web.server import create_web_app


def test_pbkdf2_totp_qr_recovery_and_stepup(tmp_path):
    km = KeyManager(keys_dir=tmp_path / "keys")
    pw = PasswordSecurity(km)
    ok_strength, _ = pw.validate_password_policy("fraca")
    assert ok_strength is False
    ok_strong, _ = pw.validate_password_policy("Cortex#Forte2026!")
    assert ok_strong is True

    h, salt = pw.hash_password("Cortex#Forte2026!")
    assert pw.verify_password("Cortex#Forte2026!", h, salt) is True
    assert pw.verify_password("Errada#2026!", h, salt) is False

    totp = TOTPManager()
    secret = totp.generate_secret()
    uri = totp.build_provisioning_uri(secret, "Luke")
    svg = render_qr_svg_offline(uri)
    assert svg.startswith("<svg")
    assert "</svg>" in svg

    code = totp.current_code(secret, at_time=1700000000.0)
    assert totp.verify_code(1, secret, code, at_time=1700000000.0) is True
    assert totp.verify_code(1, secret, code, at_time=1700000000.0) is False

    plain_codes, stored_json = totp.generate_recovery_codes(count=4)
    assert len(plain_codes) == 4
    consumed, rem_json = totp.consume_recovery_code(plain_codes[0], stored_json)
    assert consumed is True
    re_consumed, _ = totp.consume_recovery_code(plain_codes[0], rem_json)
    assert re_consumed is False

    su = StepUpAuthManager(km=km, ttl_seconds=180)
    grant = su.issue_token(1, "sess_hash_abc")
    tok = str(grant["step_up_token"])
    assert su.verify_token(tok, 1, "sess_hash_abc") is True
    assert su.verify_token(tok, 2, "sess_hash_abc") is False
    assert su.verify_token(tok, 1, "other_session") is False


def test_sha256_session_storage_and_ua_hijack_revocation(tmp_path):
    db = DatabaseConnection(tmp_path / "sess.db")
    users = UsersRepository(db)
    sess_info = users.create_session(1, "127.0.0.1", "Mozilla/5.0 Chrome/130")
    raw_tok = sess_info["raw_token"]

    with db.readonly() as conn:
        row = conn.execute("SELECT token_sha256 FROM sessions LIMIT 1").fetchone()
    assert row is not None
    assert row["token_sha256"] != raw_tok
    assert row["token_sha256"] == sess_info["token_sha256"]

    valid_sess, status = users.validate_session(raw_tok, "Mozilla/5.0 Chrome/130")
    assert valid_sess is not None and status == "ok"

    hijack_sess, h_status = users.validate_session(raw_tok, "curl/8.4.0 Attacker")
    assert hijack_sess is None and h_status == "fingerprint_mismatch"

    after_revoke, _ = users.validate_session(raw_tok, "Mozilla/5.0 Chrome/130")
    assert after_revoke is None

    assert verify_csrf_header(sess_info["csrf_token"], sess_info["csrf_token"]) is True
    assert verify_csrf_header(sess_info["csrf_token"], None) is False
    assert verify_csrf_header(sess_info["csrf_token"], "forged_csrf") is False


def test_aes256_gcm_hkdf_cross_user_aad_rejection(tmp_path):
    km = KeyManager(keys_dir=tmp_path / "keys_crypto")
    wc = WalletCryptoVault(km)
    blob = wc.encrypt_wallet_config(1, 1, {"login": "998877", "secret": "ClearTopSecret"})
    assert "ClearTopSecret" not in blob

    decrypted = wc.decrypt_wallet_config(1, 1, blob)
    assert decrypted["secret"] == "ClearTopSecret"

    with pytest.raises(Exception):
        wc.decrypt_wallet_config(2, 1, blob)

    with pytest.raises(Exception):
        wc.decrypt_wallet_config(1, 2, blob)


def test_immutable_hmac_audit_ledger(tmp_path):
    db = DatabaseConnection(tmp_path / "audit.db")
    audit = AuditRepository(db)
    audit.record_event("TEST_SECURITY_EVENT", actor_user_id=1, details={"action": "check"})
    verdict = audit.verify_integrity()
    assert verdict["valid"] is True
    assert verdict["checked_count"] >= 2

    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute("DELETE FROM security_audit_log WHERE id = 1")

    with pytest.raises(sqlite3.IntegrityError):
        with db.transaction() as conn:
            conn.execute("UPDATE security_audit_log SET event_type = 'TAMPERED' WHERE id = 1")


def test_financial_firewall_and_ssrf_xxe_telegram_guards():
    fw = FinancialFirewall(max_single_order_notional=50000.0, max_orders_per_minute=3)
    assert fw.validate_order("BTCUSD", "BUY", 1, 100.0, wallet_id=1).allowed is False
    assert fw.validate_order("PETR4", "BUY", 1000, 100.0, wallet_id=1).allowed is False
    assert fw.validate_order("PETR4", "BUY", 100, 35.0, wallet_id=1, wallet_ceiling=2000.0).allowed is False

    assert fw.validate_order("PETR4", "BUY", 10, 35.0, wallet_id=7).allowed is True
    assert fw.validate_order("VALE3", "BUY", 10, 60.0, wallet_id=7).allowed is True
    assert fw.validate_order("WEGE3", "BUY", 10, 40.0, wallet_id=7).allowed is True
    flood = fw.validate_order("ITUB4", "BUY", 10, 30.0, wallet_id=7)
    assert flood.allowed is False
    assert flood.trigger_panic is True

    with pytest.raises(ValueError):
        validate_external_https_url("http://infomoney.com.br/feed")
    with pytest.raises(ValueError):
        validate_external_https_url("https://127.0.0.1/admin")
    with pytest.raises(ValueError):
        validate_external_https_url("https://169.254.169.254/latest/meta-data")
    with pytest.raises(ValueError):
        parse_xml_safely(b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><rss/>')

    tg = TelegramClient(token="123:abc", admin_chat_id="777000", admin_user_ids=frozenset({777000}))
    assert tg.is_authorized_luke_sender(
        {"message": {"from": {"id": 777000, "is_bot": False}, "chat": {"id": 777000, "type": "private"}}}
    ) is True
    assert tg.is_authorized_luke_sender(
        {"message": {"from": {"id": 999999, "is_bot": False}, "chat": {"id": 777000, "type": "private"}}}
    ) is False
    assert tg.is_authorized_luke_sender(
        {"channel_post": {"chat": {"id": 777000, "type": "channel"}}}
    ) is False


def test_web_hardening_docs_disabled_csp_and_body_limit(tmp_path):
    db = DatabaseConnection(tmp_path / "web_sec.db")
    app = create_web_app(orchestrator=None, db=db)
    client = TestClient(app)

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404

    login_page = client.get("/login")
    assert login_page.status_code == 200
    csp = login_page.headers.get("content-security-policy", "")
    assert "script-src 'self'" in csp
    assert "jsdelivr" not in csp
    assert login_page.headers.get("x-frame-options") == "DENY"

    huge_payload = "x" * 75000
    too_large = client.post(
        "/api/auth/login",
        content=huge_payload,
        headers={"Content-Type": "application/json"},
    )
    assert too_large.status_code == 413
