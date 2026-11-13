from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import sys
import threading
from typing import Generator

from config.settings import OWNER_USER_ID, OWNER_USERNAME, settings
from domain.b3_rules import get_brt_now
from security.audit_chain import GENESIS_HASH, audit_chain
from security.password import password_security
from security.wallet_crypto import wallet_crypto

_PATH_LOCKS: dict[Path, threading.RLock] = {}
_INITIALIZED: set[Path] = set()
_GUARD = threading.Lock()

SCHEMA_DDL: str = """
CREATE TABLE IF NOT EXISTS users (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    username                TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash           TEXT    NOT NULL,
    salt                    TEXT    NOT NULL,
    is_owner_admin          INTEGER NOT NULL DEFAULT 0,
    is_active               INTEGER NOT NULL DEFAULT 1,
    must_change_password    INTEGER NOT NULL DEFAULT 1,
    invite_expires_at       TEXT,
    totp_enabled            INTEGER NOT NULL DEFAULT 0,
    encrypted_totp_secret   TEXT    NOT NULL DEFAULT '',
    recovery_codes_json     TEXT    NOT NULL DEFAULT '[]',
    failed_login_attempts   INTEGER NOT NULL DEFAULT 0,
    locked_until            TEXT,
    last_login_at           TEXT,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    CHECK (is_owner_admin IN (0, 1)),
    CHECK (is_owner_admin = 0 OR (id = 1 AND lower(username) = 'luke')),
    CHECK (id = 1 OR lower(username) != 'luke')
);

CREATE TRIGGER IF NOT EXISTS trg_protect_luke_owner
BEFORE DELETE ON users
FOR EACH ROW WHEN OLD.id = 1
BEGIN
    SELECT RAISE(ABORT, 'Luke owner account cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    token_sha256        TEXT    NOT NULL UNIQUE,
    csrf_token          TEXT    NOT NULL,
    user_id             INTEGER NOT NULL,
    active_wallet_id    INTEGER,
    ip_address          TEXT    NOT NULL DEFAULT '',
    ua_fingerprint      TEXT    NOT NULL DEFAULT '',
    user_agent_label    TEXT    NOT NULL DEFAULT '',
    created_at          TEXT    NOT NULL,
    last_active_at      TEXT    NOT NULL,
    expires_at          TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS user_wallets (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                     INTEGER NOT NULL,
    name                        TEXT    NOT NULL,
    connector_type              TEXT    NOT NULL,
    is_default                  INTEGER NOT NULL DEFAULT 0,
    automation_enabled          INTEGER NOT NULL DEFAULT 0,
    operational_mode            TEXT    NOT NULL DEFAULT 'PAUSED_UNCONFIGURED',
    panic_started_at            TEXT,
    panic_until                 TEXT,
    panic_reason                TEXT,
    initial_capital             REAL    NOT NULL DEFAULT 0.0,
    free_cash                   REAL    NOT NULL DEFAULT 0.0,
    ai_allocated_capital        REAL    NOT NULL DEFAULT 0.0,
    vault_reserve_cash          REAL    NOT NULL DEFAULT 0.0,
    stop_loss_pct               REAL    NOT NULL DEFAULT 0.10,
    trailing_trigger_pct        REAL    NOT NULL DEFAULT 0.02,
    trailing_distance_pct       REAL    NOT NULL DEFAULT 0.015,
    protect_20k_exemption       INTEGER NOT NULL DEFAULT 1,
    avoid_day_trade_tax         INTEGER NOT NULL DEFAULT 1,
    telegram_chat_id            TEXT    NOT NULL DEFAULT '',
    encrypted_connector_config  TEXT    NOT NULL DEFAULT '',
    consecutive_errors          INTEGER NOT NULL DEFAULT 0,
    created_at                  TEXT    NOT NULL,
    updated_at                  TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_wallets_user ON user_wallets(user_id);

CREATE TABLE IF NOT EXISTS global_operational_state (
    id                  INTEGER PRIMARY KEY CHECK (id = 1),
    global_mode         TEXT    NOT NULL DEFAULT 'ACTIVE',
    maintenance_reason  TEXT    NOT NULL DEFAULT '',
    panic_started_at    TEXT,
    panic_until         TEXT,
    panic_reason        TEXT,
    boot_count          INTEGER NOT NULL DEFAULT 0,
    last_boot_at        TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    updated_by          TEXT    NOT NULL DEFAULT 'system'
);

CREATE TABLE IF NOT EXISTS security_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT    NOT NULL,
    event_type      TEXT    NOT NULL,
    actor_user_id   INTEGER,
    target_wallet_id INTEGER,
    ip_address      TEXT    NOT NULL DEFAULT '',
    details_json    TEXT    NOT NULL DEFAULT '{}',
    prev_hash       TEXT    NOT NULL,
    entry_hash      TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON security_audit_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_wallet ON security_audit_log(target_wallet_id, timestamp DESC);

CREATE TRIGGER IF NOT EXISTS trg_audit_no_update
BEFORE UPDATE ON security_audit_log
BEGIN
    SELECT RAISE(ABORT, 'Audit log is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trg_audit_no_delete
BEFORE DELETE ON security_audit_log
BEGIN
    SELECT RAISE(ABORT, 'Audit log is immutable');
END;

CREATE TABLE IF NOT EXISTS market_snapshots (
    ticker          TEXT    PRIMARY KEY,
    price           REAL    NOT NULL,
    bid             REAL    NOT NULL DEFAULT 0.0,
    ask             REAL    NOT NULL DEFAULT 0.0,
    spread_pct      REAL    NOT NULL DEFAULT 0.0,
    volume          REAL    NOT NULL DEFAULT 0.0,
    change_pct      REAL    NOT NULL DEFAULT 0.0,
    rsi             REAL    NOT NULL DEFAULT 50.0,
    macd_hist       REAL    NOT NULL DEFAULT 0.0,
    atr             REAL    NOT NULL DEFAULT 0.0,
    adx             REAL    NOT NULL DEFAULT 0.0,
    trend_signal    TEXT    NOT NULL DEFAULT 'NEUTRAL',
    mtf_bullish     INTEGER NOT NULL DEFAULT 1,
    sentiment_score REAL    NOT NULL DEFAULT 0.0,
    is_stale        INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS news_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    summary         TEXT    NOT NULL DEFAULT '',
    source          TEXT    NOT NULL,
    url             TEXT    NOT NULL,
    ticker          TEXT    NOT NULL DEFAULT '',
    sentiment       REAL    NOT NULL DEFAULT 0.0,
    published_at    TEXT    NOT NULL,
    scraped_at      TEXT    NOT NULL,
    UNIQUE(url, ticker)
);
CREATE INDEX IF NOT EXISTS idx_news_pub ON news_items(published_at DESC);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    ticker          TEXT    PRIMARY KEY,
    score           REAL    NOT NULL,
    label           TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    news_count      INTEGER NOT NULL DEFAULT 0,
    top_headline    TEXT    NOT NULL DEFAULT '',
    reasoning       TEXT    NOT NULL DEFAULT '',
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS system_health (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cpu_percent     REAL    NOT NULL,
    ram_percent     REAL    NOT NULL,
    disk_percent    REAL    NOT NULL,
    timestamp       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    wallet_id           INTEGER NOT NULL,
    ticker              TEXT    NOT NULL,
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    entry_price         REAL    NOT NULL CHECK (entry_price > 0),
    current_price       REAL    NOT NULL CHECK (current_price > 0),
    stop_loss           REAL    NOT NULL DEFAULT 0.0,
    highest_price       REAL    NOT NULL DEFAULT 0.0,
    fiscal_avg_price    REAL    NOT NULL DEFAULT 0.0,
    partial_tp_taken    INTEGER NOT NULL DEFAULT 0,
    do_not_touch        INTEGER NOT NULL DEFAULT 0,
    ticket              INTEGER NOT NULL DEFAULT 0,
    opened_at           TEXT    NOT NULL,
    updated_at          TEXT    NOT NULL,
    UNIQUE(wallet_id, ticker),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(wallet_id) REFERENCES user_wallets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_positions_owner ON positions(user_id, wallet_id);

CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    wallet_id           INTEGER NOT NULL,
    ticker              TEXT    NOT NULL,
    action              TEXT    NOT NULL CHECK (action IN ('BUY', 'SELL')),
    quantity            INTEGER NOT NULL CHECK (quantity > 0),
    price               REAL    NOT NULL CHECK (price > 0),
    total_value         REAL    NOT NULL,
    fee                 REAL    NOT NULL DEFAULT 0.0,
    realized_pnl        REAL    NOT NULL DEFAULT 0.0,
    stop_loss           REAL    NOT NULL DEFAULT 0.0,
    is_day_trade        INTEGER NOT NULL DEFAULT 0,
    client_order_id     TEXT    NOT NULL DEFAULT '',
    reasoning           TEXT    NOT NULL DEFAULT '',
    timestamp           TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(wallet_id) REFERENCES user_wallets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_trades_wallet_ts ON trades(wallet_id, user_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS ai_decisions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    wallet_id           INTEGER NOT NULL,
    ticker              TEXT    NOT NULL,
    action              TEXT    NOT NULL,
    confidence          REAL    NOT NULL,
    trend_signal        TEXT    NOT NULL DEFAULT 'NEUTRAL',
    sentiment_score     REAL    NOT NULL DEFAULT 0.0,
    reasoning           TEXT    NOT NULL DEFAULT '',
    timestamp           TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(wallet_id) REFERENCES user_wallets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_decisions_wallet_ts ON ai_decisions(wallet_id, user_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    wallet_id           INTEGER NOT NULL,
    total_equity        REAL    NOT NULL,
    operational_equity  REAL    NOT NULL,
    free_cash           REAL    NOT NULL,
    allocated_capital   REAL    NOT NULL,
    vault_reserve_cash  REAL    NOT NULL DEFAULT 0.0,
    pnl_percent         REAL    NOT NULL DEFAULT 0.0,
    daily_pnl_percent   REAL    NOT NULL DEFAULT 0.0,
    snapshot_date       TEXT    NOT NULL,
    timestamp           TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(wallet_id) REFERENCES user_wallets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_equity_wallet_ts ON equity_snapshots(wallet_id, user_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS capital_transactions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 INTEGER NOT NULL,
    wallet_id               INTEGER NOT NULL,
    tx_type                 TEXT    NOT NULL,
    amount                  REAL    NOT NULL,
    resulting_ai_capital    REAL    NOT NULL,
    resulting_vault         REAL    NOT NULL,
    details                 TEXT    NOT NULL DEFAULT '',
    timestamp               TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(wallet_id) REFERENCES user_wallets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_cap_tx_wallet ON capital_transactions(wallet_id, user_id, timestamp DESC);

CREATE TABLE IF NOT EXISTS d2_settlements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    wallet_id       INTEGER NOT NULL,
    trade_date      TEXT    NOT NULL,
    settlement_date TEXT    NOT NULL,
    net_sell_amount REAL    NOT NULL DEFAULT 0.0,
    settled         INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL,
    UNIQUE(wallet_id, trade_date),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(wallet_id) REFERENCES user_wallets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS withdrawal_goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    wallet_id       INTEGER NOT NULL,
    amount          REAL    NOT NULL,
    mode            TEXT    NOT NULL CHECK (mode IN ('instant', 'on_profit')),
    target_equity   REAL,
    status          TEXT    NOT NULL DEFAULT 'ACTIVE',
    created_at      TEXT    NOT NULL,
    completed_at    TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(wallet_id) REFERENCES user_wallets(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bi_api_keys (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    wallet_id       INTEGER,
    label           TEXT    NOT NULL,
    token_prefix    TEXT    NOT NULL,
    token_sha256    TEXT    NOT NULL UNIQUE,
    allowed_cidr    TEXT    NOT NULL DEFAULT '',
    include_admin   INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1,
    expires_at      TEXT,
    last_used_at    TEXT,
    last_used_ip    TEXT    NOT NULL DEFAULT '',
    created_at      TEXT    NOT NULL,
    revoked_at      TEXT,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(wallet_id) REFERENCES user_wallets(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_bi_keys_user ON bi_api_keys(user_id, is_active);
"""


class DatabaseConnection:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path: Path = Path(db_path).resolve() if db_path is not None else settings.DB_PATH.resolve()
        with _GUARD:
            if self.db_path not in _PATH_LOCKS:
                _PATH_LOCKS[self.db_path] = threading.RLock()
            self._lock = _PATH_LOCKS[self.db_path]
        self.ensure_initialized()

    def _raw_connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            conn = self._raw_connect()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    @contextmanager
    def readonly(self) -> Generator[sqlite3.Connection, None, None]:
        with self._lock:
            conn = self._raw_connect()
            try:
                yield conn
            finally:
                conn.close()

    def ensure_initialized(self) -> None:
        with _GUARD:
            if self.db_path in _INITIALIZED and self.db_path.exists():
                return
        with self.transaction() as conn:
            conn.executescript(SCHEMA_DDL)
            self._seed_luke_and_default_state(conn)
        if sys.platform != "win32" and self.db_path.exists():
            try:
                os.chmod(self.db_path, 0o600)
            except OSError:
                pass
        with _GUARD:
            _INITIALIZED.add(self.db_path)

    def _seed_luke_and_default_state(self, conn: sqlite3.Connection) -> None:
        now_iso = get_brt_now().isoformat()
        row = conn.execute("SELECT id FROM users WHERE id = ?", (OWNER_USER_ID,)).fetchone()
        if row is None:
            pw_hash, salt = password_security.hash_password(settings.LUKE_BOOTSTRAP_PASSWORD)
            conn.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, salt, is_owner_admin,
                    is_active, must_change_password, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 1, 1, 1, ?, ?)
                """,
                (OWNER_USER_ID, OWNER_USERNAME, pw_hash, salt, now_iso, now_iso),
            )
            self._append_raw_audit(
                conn,
                now_iso,
                "OWNER_LUKE_PROVISIONED",
                OWNER_USER_ID,
                None,
                "system",
                {"username": OWNER_USERNAME, "environment": settings.ENVIRONMENT_ID},
            )

        w_row = conn.execute(
            "SELECT id FROM user_wallets WHERE user_id = ? LIMIT 1",
            (OWNER_USER_ID,),
        ).fetchone()
        if w_row is None:
            if settings.IS_PRODUCTION:
                cur = conn.execute(
                    """
                    INSERT INTO user_wallets (
                        user_id, name, connector_type, is_default, automation_enabled,
                        operational_mode, initial_capital, free_cash, ai_allocated_capital,
                        vault_reserve_cash, stop_loss_pct, trailing_trigger_pct,
                        trailing_distance_pct, protect_20k_exemption, avoid_day_trade_tax,
                        created_at, updated_at
                    ) VALUES (?, ?, 'clear_mt5', 1, 1, 'ACTIVE', 0.0, 0.0, 0.0, 0.0, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        OWNER_USER_ID,
                        "Clear Investimentos — B3",
                        settings.STOP_LOSS_PERCENT,
                        settings.TRAILING_STOP_TRIGGER_PERCENT,
                        settings.TRAILING_STOP_DISTANCE_PERCENT,
                        int(settings.PROTECT_B3_20K_EXEMPTION),
                        int(settings.AVOID_DAY_TRADE_TAX),
                        now_iso,
                        now_iso,
                    ),
                )
                wid = int(cur.lastrowid or 1)
                enc_cfg = wallet_crypto.encrypt_wallet_config(
                    OWNER_USER_ID,
                    wid,
                    {
                        "use_server_clear_bridge": True,
                        "bridge_url": settings.MT5_BRIDGE_URL,
                        "server": settings.MT5_SERVER,
                        "login": settings.MT5_LOGIN,
                    },
                )
                conn.execute(
                    "UPDATE user_wallets SET encrypted_connector_config = ? WHERE id = ?",
                    (enc_cfg, wid),
                )
            else:
                sim_cap = float(settings.DEFAULT_SIM_CAPITAL)
                cur = conn.execute(
                    """
                    INSERT INTO user_wallets (
                        user_id, name, connector_type, is_default, automation_enabled,
                        operational_mode, initial_capital, free_cash, ai_allocated_capital,
                        vault_reserve_cash, stop_loss_pct, trailing_trigger_pct,
                        trailing_distance_pct, protect_20k_exemption, avoid_day_trade_tax,
                        created_at, updated_at
                    ) VALUES (?, ?, 'paper_b3', 1, 1, 'ACTIVE', ?, ?, ?, 0.0, ?, ?, ?, 1, 1, ?, ?)
                    """,
                    (
                        OWNER_USER_ID,
                        "Carteira Simulada B3 — Luke",
                        sim_cap,
                        sim_cap,
                        sim_cap,
                        settings.STOP_LOSS_PERCENT,
                        settings.TRAILING_STOP_TRIGGER_PERCENT,
                        settings.TRAILING_STOP_DISTANCE_PERCENT,
                        now_iso,
                        now_iso,
                    ),
                )
                wid = int(cur.lastrowid or 1)
                enc_cfg = wallet_crypto.encrypt_wallet_config(
                    OWNER_USER_ID,
                    wid,
                    {"paper_engine": "b3_realistic_v1"},
                )
                conn.execute(
                    "UPDATE user_wallets SET encrypted_connector_config = ? WHERE id = ?",
                    (enc_cfg, wid),
                )

        st_row = conn.execute("SELECT id FROM global_operational_state WHERE id = 1").fetchone()
        if st_row is None:
            init_mode = "MAINTENANCE" if settings.FORCE_MAINTENANCE_ON_BOOT else "ACTIVE"
            init_reason = (
                "Modo Manutenção Pós-Boot ativo na Produção. Aguardando liberação pelo Luke."
                if settings.FORCE_MAINTENANCE_ON_BOOT
                else ""
            )
            conn.execute(
                """
                INSERT INTO global_operational_state (
                    id, global_mode, maintenance_reason, boot_count,
                    last_boot_at, updated_at, updated_by
                ) VALUES (1, ?, ?, 1, ?, ?, 'system')
                """,
                (init_mode, init_reason, now_iso, now_iso),
            )

    @staticmethod
    def _append_raw_audit(
        conn: sqlite3.Connection,
        ts: str,
        event_type: str,
        actor_user_id: int | None,
        target_wallet_id: int | None,
        ip_address: str,
        details: dict,
    ) -> None:
        last = conn.execute(
            "SELECT id, entry_hash FROM security_audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = str(last["entry_hash"]) if last is not None else GENESIS_HASH
        next_id = (int(last["id"]) + 1) if last is not None else 1
        details_json = audit_chain.canonicalize_details(details)
        entry_hash = audit_chain.compute_entry_hash(
            seq_id=next_id,
            timestamp=ts,
            event_type=event_type,
            actor_user_id=actor_user_id,
            target_wallet_id=target_wallet_id,
            ip_address=ip_address,
            details_json=details_json,
            prev_hash=prev_hash,
        )
        conn.execute(
            """
            INSERT INTO security_audit_log (
                id, timestamp, event_type, actor_user_id, target_wallet_id,
                ip_address, details_json, prev_hash, entry_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                next_id,
                ts,
                event_type,
                actor_user_id,
                target_wallet_id,
                ip_address,
                details_json,
                prev_hash,
                entry_hash,
            ),
        )
