"""
Gerenciador de banco de dados SQLite do Projeto Córtex.

Fornece armazenamento persistente thread-safe para trades, scores de
sentimento, snapshots de mercado, relatórios diários, decisões da IA,
logs do Telegram, métricas de saúde, autenticação e gestão de capital.

Utiliza inicialização singleton por caminho de banco e RLock compartilhado
por arquivo para eliminar contenção e tempestades de DDL em ciclos rápidos.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Generator

from utils.helpers import BRT
from utils.logger import get_logger

logger = get_logger(__name__)

_INITIALIZED_PATHS: set[Path] = set()
_PATH_LOCKS: dict[Path, threading.RLock] = {}
_INIT_GUARD = threading.Lock()


def _now_brt_iso() -> str:
    """Retorna timestamp atual em BRT no formato ISO 8601."""
    return datetime.now(tz=BRT).isoformat()


def _today_brt_iso() -> str:
    """Retorna a data atual em BRT no formato ISO 8601 (só data)."""
    return datetime.now(tz=BRT).date().isoformat()


def _resolve_env_and_sim(
    is_simulated: bool | None,
    environment: str | None,
    default_simulated: bool = True,
) -> tuple[str, int]:
    """Sincroniza de forma determinística o par (environment, is_simulated)."""
    if environment is not None and str(environment).strip():
        env_clean = str(environment).strip().lower()
        if env_clean not in ("homologation", "production"):
            env_clean = "homologation" if (is_simulated if is_simulated is not None else default_simulated) else "production"
        sim_int = 0 if env_clean == "production" else 1
        return env_clean, sim_int

    if is_simulated is not None:
        sim_bool = bool(is_simulated)
        return ("homologation" if sim_bool else "production"), int(sim_bool)

    return ("homologation" if default_simulated else "production"), int(default_simulated)


def _build_env_filters(
    is_simulated: bool | None,
    environment: str | None,
) -> tuple[list[str], list[Any]]:
    """Constrói cláusulas SQL opcionais para filtro de ambiente."""
    clauses: list[str] = []
    params: list[Any] = []
    if environment is not None and str(environment).strip():
        clauses.append("environment = ?")
        params.append(str(environment).strip().lower())
    if is_simulated is not None:
        clauses.append("is_simulated = ?")
        params.append(int(bool(is_simulated)))
    return clauses, params


_CREATE_TABLES_SQL: str = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    action          TEXT    NOT NULL CHECK (action IN ('BUY', 'SELL')),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    price           REAL    NOT NULL CHECK (price > 0),
    total_value     REAL    NOT NULL,
    stop_loss       REAL,
    timestamp       TEXT    NOT NULL,
    reasoning       TEXT,
    environment     TEXT    NOT NULL DEFAULT 'homologation',
    is_simulated    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS sentiment_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    score           REAL    NOT NULL,
    source          TEXT    NOT NULL,
    headline        TEXT,
    timestamp       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    price           REAL    NOT NULL,
    volume          REAL,
    bid             REAL,
    ask             REAL,
    timestamp       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    date                TEXT    NOT NULL,
    buys_count          INTEGER NOT NULL DEFAULT 0,
    sells_count         INTEGER NOT NULL DEFAULT 0,
    free_cash           REAL    NOT NULL DEFAULT 0,
    allocated_capital   REAL    NOT NULL DEFAULT 0,
    initial_capital     REAL    NOT NULL DEFAULT 0,
    total_equity        REAL    NOT NULL DEFAULT 0,
    pnl_percent         REAL    NOT NULL DEFAULT 0,
    daily_pnl_percent   REAL    NOT NULL DEFAULT 0.0,
    environment         TEXT    NOT NULL DEFAULT 'homologation',
    is_simulated        INTEGER NOT NULL DEFAULT 1,
    UNIQUE(date, environment)
);

CREATE TABLE IF NOT EXISTS ai_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    trend_signal    TEXT,
    sentiment_score REAL,
    reasoning       TEXT,
    environment     TEXT    NOT NULL DEFAULT 'homologation',
    is_simulated    INTEGER NOT NULL DEFAULT 1,
    timestamp       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    message_type    TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    sent_at         TEXT    NOT NULL,
    success         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS system_health (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cpu_percent     REAL    NOT NULL,
    ram_percent     REAL    NOT NULL,
    disk_percent    REAL    NOT NULL,
    timestamp       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS news_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    source          TEXT    NOT NULL,
    url             TEXT    NOT NULL,
    ticker          TEXT,
    sentiment       REAL,
    published_at    TEXT    NOT NULL,
    scraped_at      TEXT    NOT NULL,
    UNIQUE(url, ticker)
);

CREATE TABLE IF NOT EXISTS users (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    username                TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    password_hash           TEXT    NOT NULL,
    salt                    TEXT    NOT NULL,
    must_change_password    INTEGER NOT NULL DEFAULT 1,
    failed_login_attempts   INTEGER NOT NULL DEFAULT 0,
    locked_until            TEXT,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_token       TEXT    NOT NULL UNIQUE,
    csrf_token          TEXT    NOT NULL DEFAULT '',
    user_id             INTEGER NOT NULL,
    ip_address          TEXT,
    user_agent          TEXT,
    created_at          TEXT    NOT NULL,
    last_active_at      TEXT    NOT NULL,
    expires_at          TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS system_config (
    key             TEXT    PRIMARY KEY,
    value           TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS withdrawal_goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    amount          REAL    NOT NULL,
    mode            TEXT    NOT NULL CHECK (mode IN ('instant', 'on_profit')),
    target_equity   REAL    NOT NULL,
    base_equity     REAL    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'achieved', 'cancelled', 'completed')),
    environment     TEXT    NOT NULL DEFAULT 'production',
    created_at      TEXT    NOT NULL,
    completed_at    TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS capital_transactions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp               TEXT    NOT NULL,
    tx_type                 TEXT    NOT NULL CHECK (tx_type IN ('APORTE', 'RESGATE_IMEDIATO', 'SAQUE_META_LUCRO', 'REINVESTIMENTO_LUCRO', 'AJUSTE_MANUAL')),
    amount                  REAL    NOT NULL,
    resulting_ai_capital    REAL    NOT NULL,
    resulting_vault         REAL    NOT NULL,
    environment             TEXT    NOT NULL DEFAULT 'homologation',
    is_simulated            INTEGER NOT NULL DEFAULT 1,
    details                 TEXT
);
"""

_CREATE_INDEXES_SQL: str = """
CREATE INDEX IF NOT EXISTS idx_sessions_user            ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_trades_ticker            ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp         ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_sim_ts            ON trades(is_simulated, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_ticker         ON sentiment_scores(ticker);
CREATE INDEX IF NOT EXISTS idx_sentiment_ts             ON sentiment_scores(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker         ON market_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp      ON market_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker         ON ai_decisions(ticker);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_ts          ON ai_decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_ai_decisions_env_id      ON ai_decisions(environment, id DESC);
CREATE INDEX IF NOT EXISTS idx_daily_reports_date       ON daily_reports(date);
CREATE INDEX IF NOT EXISTS idx_daily_reports_env_date   ON daily_reports(environment, date DESC);
CREATE INDEX IF NOT EXISTS idx_system_health_ts         ON system_health(timestamp);
CREATE INDEX IF NOT EXISTS idx_news_ticker              ON news_items(ticker);
CREATE INDEX IF NOT EXISTS idx_news_published           ON news_items(published_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_url_null_tk  ON news_items(url) WHERE ticker IS NULL;
CREATE INDEX IF NOT EXISTS idx_withdrawal_status        ON withdrawal_goals(status, environment);
CREATE INDEX IF NOT EXISTS idx_capital_tx_timestamp     ON capital_transactions(timestamp);
CREATE INDEX IF NOT EXISTS idx_capital_tx_env_id        ON capital_transactions(environment, id DESC);
"""

_DROP_DUPLICATE_INDEXES_SQL: str = """
DROP INDEX IF EXISTS idx_decisions_timestamp;
DROP INDEX IF EXISTS idx_sentiment_timestamp;
DROP INDEX IF EXISTS idx_health_timestamp;
DROP INDEX IF EXISTS idx_config_key;
DROP INDEX IF EXISTS idx_users_username;
DROP INDEX IF EXISTS idx_sessions_token;
"""


class DatabaseManager:
    """
    Gerenciador de banco de dados SQLite para o Projeto Córtex.

    Fornece operações CRUD thread-safe para todas as tabelas do sistema.
    As tabelas e migrações são inicializadas uma única vez por arquivo SQLite.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        """
        Inicializa o gerenciador de banco de dados.

        Args:
            db_path: Caminho do arquivo SQLite.
                     Se None, resolve dinamicamente settings.DB_PATH.
        """
        if db_path is None:
            from config.settings import settings
            self.db_path: Path = Path(settings.DB_PATH)
        else:
            self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_path = self.db_path.resolve()

        with _INIT_GUARD:
            lock = _PATH_LOCKS.get(resolved_path)
            if lock is None:
                lock = threading.RLock()
                _PATH_LOCKS[resolved_path] = lock
            self._lock: threading.RLock = lock

        with self._lock:
            file_missing_or_empty = (
                not resolved_path.exists()
                or resolved_path.stat().st_size == 0
            )
            with _INIT_GUARD:
                needs_init = (resolved_path not in _INITIALIZED_PATHS) or file_missing_or_empty

            if needs_init:
                self._initialize_schema()
                with _INIT_GUARD:
                    _INITIALIZED_PATHS.add(resolved_path)

    @staticmethod
    def _has_single_column_unique(cursor: sqlite3.Cursor, table_name: str, column_name: str) -> bool:
        """Verifica se a tabela possui constraint/índice UNIQUE não-parcial apenas sobre `column_name`."""
        cursor.execute(f"PRAGMA index_list({table_name})")
        indexes = cursor.fetchall()
        for idx in indexes:
            is_unique = int(idx["unique"]) == 1
            is_partial = int(idx["partial"]) == 1 if "partial" in idx.keys() else False
            if not is_unique or is_partial:
                continue
            idx_name = idx["name"]
            cursor.execute(f"PRAGMA index_info('{idx_name}')")
            cols = [r["name"] for r in cursor.fetchall()]
            if cols == [column_name]:
                return True
        return False

    @staticmethod
    def _has_composite_unique(cursor: sqlite3.Cursor, table_name: str, expected_cols: list[str]) -> bool:
        """Verifica se a tabela possui constraint/índice UNIQUE exatamente sobre `expected_cols`."""
        cursor.execute(f"PRAGMA index_list({table_name})")
        for idx in cursor.fetchall():
            if int(idx["unique"]) != 1:
                continue
            idx_name = idx["name"]
            cursor.execute(f"PRAGMA index_info('{idx_name}')")
            cols = [r["name"] for r in cursor.fetchall()]
            if cols == expected_cols:
                return True
        return False

    def _initialize_schema(self) -> None:
        """Cria tabelas, ativa WAL persistente, executa migrações idempotentes e aplica seed inicial único."""
        try:
            with self._get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.executescript(_CREATE_TABLES_SQL)
                cursor = conn.cursor()

                # 1. Migrações em trades
                cursor.execute("PRAGMA table_info(trades)")
                trade_cols = {c["name"] for c in cursor.fetchall()}
                if "is_simulated" not in trade_cols:
                    cursor.execute("ALTER TABLE trades ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 1")
                if "environment" not in trade_cols:
                    cursor.execute("ALTER TABLE trades ADD COLUMN environment TEXT NOT NULL DEFAULT 'homologation'")
                    cursor.execute(
                        "UPDATE trades SET environment = CASE WHEN is_simulated = 0 THEN 'production' ELSE 'homologation' END"
                    )

                # 2. Migrações em ai_decisions
                cursor.execute("PRAGMA table_info(ai_decisions)")
                ai_cols = {c["name"] for c in cursor.fetchall()}
                if "environment" not in ai_cols:
                    cursor.execute("ALTER TABLE ai_decisions ADD COLUMN environment TEXT NOT NULL DEFAULT 'homologation'")
                if "is_simulated" not in ai_cols:
                    cursor.execute("ALTER TABLE ai_decisions ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 1")

                # 3. Migrações em capital_transactions
                cursor.execute("PRAGMA table_info(capital_transactions)")
                tx_cols = {c["name"] for c in cursor.fetchall()}
                if "environment" not in tx_cols:
                    cursor.execute("ALTER TABLE capital_transactions ADD COLUMN environment TEXT NOT NULL DEFAULT 'homologation'")
                if "is_simulated" not in tx_cols:
                    cursor.execute("ALTER TABLE capital_transactions ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 1")

                # 4. Migrações em withdrawal_goals
                cursor.execute("PRAGMA table_info(withdrawal_goals)")
                wg_cols = {c["name"] for c in cursor.fetchall()}
                if "environment" not in wg_cols:
                    cursor.execute("ALTER TABLE withdrawal_goals ADD COLUMN environment TEXT NOT NULL DEFAULT 'production'")

                # 5. Migrações em sessions (CSRF token)
                cursor.execute("PRAGMA table_info(sessions)")
                sess_cols = {c["name"] for c in cursor.fetchall()}
                if "csrf_token" not in sess_cols:
                    cursor.execute("ALTER TABLE sessions ADD COLUMN csrf_token TEXT NOT NULL DEFAULT ''")

                # 6. Migração de daily_reports para UNIQUE(date, environment) + colunas completas
                cursor.execute("PRAGMA table_info(daily_reports)")
                dr_cols = {c["name"] for c in cursor.fetchall()}
                needs_dr_rebuild = (
                    self._has_single_column_unique(cursor, "daily_reports", "date")
                    or not self._has_composite_unique(cursor, "daily_reports", ["date", "environment"])
                )
                if needs_dr_rebuild:
                    daily_pnl_expr = "COALESCE(daily_pnl_percent, pnl_percent, 0.0)" if "daily_pnl_percent" in dr_cols else "COALESCE(pnl_percent, 0.0)"
                    env_expr = "COALESCE(environment, 'homologation')" if "environment" in dr_cols else "'homologation'"
                    sim_expr = "COALESCE(is_simulated, 1)" if "is_simulated" in dr_cols else "1"
                    cursor.execute("DROP TABLE IF EXISTS daily_reports_v2")
                    cursor.execute(
                        """
                        CREATE TABLE daily_reports_v2 (
                            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                            date                TEXT    NOT NULL,
                            buys_count          INTEGER NOT NULL DEFAULT 0,
                            sells_count         INTEGER NOT NULL DEFAULT 0,
                            free_cash           REAL    NOT NULL DEFAULT 0,
                            allocated_capital   REAL    NOT NULL DEFAULT 0,
                            initial_capital     REAL    NOT NULL DEFAULT 0,
                            total_equity        REAL    NOT NULL DEFAULT 0,
                            pnl_percent         REAL    NOT NULL DEFAULT 0,
                            daily_pnl_percent   REAL    NOT NULL DEFAULT 0.0,
                            environment         TEXT    NOT NULL DEFAULT 'homologation',
                            is_simulated        INTEGER NOT NULL DEFAULT 1,
                            UNIQUE(date, environment)
                        )
                        """
                    )
                    cursor.execute(
                        f"""
                        INSERT OR IGNORE INTO daily_reports_v2 (
                            id, date, buys_count, sells_count, free_cash, allocated_capital,
                            initial_capital, total_equity, pnl_percent, daily_pnl_percent,
                            environment, is_simulated
                        )
                        SELECT
                            id, date, buys_count, sells_count, free_cash, allocated_capital,
                            initial_capital, total_equity, pnl_percent,
                            {daily_pnl_expr}, {env_expr}, {sim_expr}
                        FROM daily_reports
                        """
                    )
                    cursor.execute("DROP TABLE daily_reports")
                    cursor.execute("ALTER TABLE daily_reports_v2 RENAME TO daily_reports")
                else:
                    if "daily_pnl_percent" not in dr_cols:
                        cursor.execute("ALTER TABLE daily_reports ADD COLUMN daily_pnl_percent REAL NOT NULL DEFAULT 0.0")
                    if "environment" not in dr_cols:
                        cursor.execute("ALTER TABLE daily_reports ADD COLUMN environment TEXT NOT NULL DEFAULT 'homologation'")
                    if "is_simulated" not in dr_cols:
                        cursor.execute("ALTER TABLE daily_reports ADD COLUMN is_simulated INTEGER NOT NULL DEFAULT 1")

                # 7. Migração de news_items para UNIQUE(url, ticker)
                needs_news_rebuild = (
                    self._has_single_column_unique(cursor, "news_items", "url")
                    or not self._has_composite_unique(cursor, "news_items", ["url", "ticker"])
                )
                if needs_news_rebuild:
                    cursor.execute("DROP TABLE IF EXISTS news_items_v2")
                    cursor.execute(
                        """
                        CREATE TABLE news_items_v2 (
                            id              INTEGER PRIMARY KEY AUTOINCREMENT,
                            title           TEXT    NOT NULL,
                            source          TEXT    NOT NULL,
                            url             TEXT    NOT NULL,
                            ticker          TEXT,
                            sentiment       REAL,
                            published_at    TEXT    NOT NULL,
                            scraped_at      TEXT    NOT NULL,
                            UNIQUE(url, ticker)
                        )
                        """
                    )
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO news_items_v2 (
                            id, title, source, url, ticker, sentiment, published_at, scraped_at
                        )
                        SELECT id, title, source, url, ticker, sentiment, published_at, scraped_at
                        FROM news_items
                        """
                    )
                    cursor.execute("DROP TABLE news_items")
                    cursor.execute("ALTER TABLE news_items_v2 RENAME TO news_items")

                # 8. Limpeza de índices duplicados e criação dos índices otimizados
                conn.executescript(_DROP_DUPLICATE_INDEXES_SQL)
                conn.executescript(_CREATE_INDEXES_SQL)

                # 9. Seed inicial de usuário SOMENTE quando a tabela users estiver vazia
                cursor.execute("SELECT COUNT(*) AS cnt FROM users")
                row = cursor.fetchone()
                user_count = int(row["cnt"]) if row else 0
                if user_count == 0:
                    from auth import PasswordManager
                    initial_password = os.getenv("INITIAL_ADMIN_PASSWORD", "Admin")
                    admin_hash, admin_salt = PasswordManager.hash_password(initial_password)
                    now_str = _now_brt_iso()
                    cursor.execute(
                        """
                        INSERT INTO users (username, password_hash, salt, must_change_password, created_at, updated_at)
                        VALUES (?, ?, ?, 1, ?, ?)
                        """,
                        ("Admin", admin_hash, admin_salt, now_str, now_str),
                    )

            logger.info("Banco de dados inicializado: %s", self.db_path)
        except sqlite3.Error as exc:
            logger.critical("Falha ao inicializar banco de dados: %s", exc)
            raise

    _init_database = _initialize_schema

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager para conexão leve com o banco de dados SQLite (WAL já ativado no schema).
        """
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        try:
            yield conn
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> int:
        """Executa uma instrução SQL com lock compartilhado e retorna o lastrowid."""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.execute(sql, params)
                    return cursor.lastrowid or 0
            except sqlite3.Error as exc:
                logger.error("Erro ao executar SQL: %s | Params: %s | Erro: %s", sql, params, exc)
                raise

    def _fetch_all(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[dict[str, Any]]:
        """Executa consulta SQL com lock compartilhado e retorna todas as linhas como dicts."""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.execute(sql, params)
                    rows = cursor.fetchall()
                    return [dict(row) for row in rows]
            except sqlite3.Error as exc:
                logger.error("Erro na consulta SQL: %s | Params: %s | Erro: %s", sql, params, exc)
                return []

    def _fetch_one(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> dict[str, Any] | None:
        """Executa consulta SQL com lock compartilhado e retorna a primeira linha como dict."""
        with self._lock:
            try:
                with self._get_connection() as conn:
                    cursor = conn.execute(sql, params)
                    row = cursor.fetchone()
                    return dict(row) if row else None
            except sqlite3.Error as exc:
                logger.error("Erro na consulta SQL: %s | Params: %s | Erro: %s", sql, params, exc)
                return None

    def insert_trade(
        self,
        ticker: str,
        action: str,
        quantity: int,
        price: float,
        total_value: float,
        stop_loss: float | None = None,
        reasoning: str | None = None,
        is_simulated: bool | None = None,
        timestamp: str | None = None,
        environment: str | None = None,
    ) -> int:
        """
        Registra uma operação de compra ou venda no banco de dados mantendo
        `environment` e `is_simulated` sincronizados.
        """
        action_map = {
            "BUY": "BUY",
            "COMPRA": "BUY",
            "SELL": "SELL",
            "VENDA": "SELL",
            "EMERGENCY_SELL": "SELL",
            "VENDA_EMERGENCIAL": "SELL",
        }
        action_clean = action_map.get(str(action).upper().strip())
        if not action_clean:
            raise ValueError(f"Ação inválida: '{action}'. Use 'BUY' ou 'SELL'.")

        env_clean, sim_int = _resolve_env_and_sim(is_simulated, environment, default_simulated=True)
        ts = timestamp or _now_brt_iso()
        row_id = self._execute(
            """INSERT INTO trades
               (ticker, action, quantity, price, total_value, stop_loss,
                timestamp, reasoning, environment, is_simulated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker.strip().upper(),
                action_clean,
                quantity,
                price,
                total_value,
                stop_loss,
                ts,
                reasoning,
                env_clean,
                sim_int,
            ),
        )
        logger.info(
            "Trade registrado: %s %s x%d @ R$%.2f (ID: %d, env: %s)",
            action_clean, ticker, quantity, price, row_id, env_clean,
        )
        return row_id

    def insert_sentiment(
        self,
        ticker: str,
        score: float,
        source: str,
        headline: str | None = None,
        timestamp: str | None = None,
    ) -> int:
        """Registra um score de análise de sentimento."""
        ts = timestamp or _now_brt_iso()
        row_id = self._execute(
            """INSERT INTO sentiment_scores
               (ticker, score, source, headline, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker.strip().upper(), score, source, headline, ts),
        )
        logger.debug(
            "Sentimento registrado: %s score=%.3f fonte=%s (ID: %d)",
            ticker, score, source, row_id,
        )
        return row_id

    def insert_snapshot(
        self,
        ticker: str,
        price: float,
        volume: float | None = None,
        bid: float | None = None,
        ask: float | None = None,
        timestamp: str | None = None,
    ) -> int:
        """Registra um snapshot de dados de mercado."""
        ts = timestamp or _now_brt_iso()
        return self._execute(
            """INSERT INTO market_snapshots
               (ticker, price, volume, bid, ask, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker.strip().upper(), price, volume, bid, ask, ts),
        )

    def insert_daily_report(
        self,
        report_date: str | date | None = None,
        buys_count: int = 0,
        sells_count: int = 0,
        free_cash: float = 0.0,
        allocated_capital: float = 0.0,
        initial_capital: float = 0.0,
        total_equity: float = 0.0,
        pnl_percent: float = 0.0,
        daily_pnl_percent: float | None = None,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> int:
        """
        Registra ou atualiza o relatório diário isolado por `(date, environment)`.
        """
        if report_date is None:
            dt_str = _today_brt_iso()
        elif isinstance(report_date, date):
            dt_str = report_date.isoformat()
        else:
            dt_str = str(report_date)

        if is_simulated is None and environment is None:
            from config.settings import settings
            default_sim = bool(getattr(settings, "SIMULATION_MODE", True))
        else:
            default_sim = True
        env_clean, sim_int = _resolve_env_and_sim(is_simulated, environment, default_simulated=default_sim)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                if daily_pnl_percent is None:
                    cursor.execute(
                        """
                        SELECT total_equity FROM daily_reports
                        WHERE environment = ? AND date < ?
                        ORDER BY date DESC LIMIT 1
                        """,
                        (env_clean, dt_str),
                    )
                    prev_row = cursor.fetchone()
                    prev_eq = float(prev_row["total_equity"]) if prev_row and prev_row["total_equity"] else 0.0
                    if prev_eq > 0.0 and total_equity > 0.0:
                        computed_daily_pnl = ((total_equity - prev_eq) / prev_eq) * 100.0
                    elif initial_capital > 0.0 and total_equity > 0.0 and pnl_percent == 0.0:
                        computed_daily_pnl = ((total_equity - initial_capital) / initial_capital) * 100.0
                    else:
                        computed_daily_pnl = float(pnl_percent)
                else:
                    computed_daily_pnl = float(daily_pnl_percent)

                cursor.execute(
                    """
                    INSERT INTO daily_reports (
                        date, buys_count, sells_count, free_cash, allocated_capital,
                        initial_capital, total_equity, pnl_percent, daily_pnl_percent,
                        environment, is_simulated
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, environment) DO UPDATE SET
                        buys_count = excluded.buys_count,
                        sells_count = excluded.sells_count,
                        free_cash = excluded.free_cash,
                        allocated_capital = excluded.allocated_capital,
                        initial_capital = excluded.initial_capital,
                        total_equity = excluded.total_equity,
                        pnl_percent = excluded.pnl_percent,
                        daily_pnl_percent = excluded.daily_pnl_percent,
                        is_simulated = excluded.is_simulated
                    """,
                    (
                        dt_str,
                        buys_count,
                        sells_count,
                        free_cash,
                        allocated_capital,
                        initial_capital,
                        total_equity,
                        pnl_percent,
                        computed_daily_pnl,
                        env_clean,
                        sim_int,
                    ),
                )
                row_id = cursor.lastrowid or 0
                if not row_id:
                    cursor.execute(
                        "SELECT id FROM daily_reports WHERE date = ? AND environment = ?",
                        (dt_str, env_clean),
                    )
                    existing = cursor.fetchone()
                    row_id = int(existing["id"]) if existing else 0

        logger.info(
            "Relatório diário registrado: %s [%s] | Compras: %d | Vendas: %d | P&L acum: %.2f%% | P&L dia: %.2f%%",
            dt_str, env_clean, buys_count, sells_count, pnl_percent, computed_daily_pnl,
        )
        return row_id

    def insert_decision(
        self,
        ticker: str,
        action: str,
        confidence: float,
        trend_signal: str | None = None,
        sentiment_score: float | None = None,
        reasoning: str | None = None,
        timestamp: str | None = None,
        environment: str | None = "homologation",
        is_simulated: bool | None = None,
    ) -> int:
        """Registra uma decisão tomada pela IA com isolamento de ambiente."""
        env_clean, sim_int = _resolve_env_and_sim(is_simulated, environment, default_simulated=True)
        ts = timestamp or _now_brt_iso()
        row_id = self._execute(
            """INSERT INTO ai_decisions
               (ticker, action, confidence, trend_signal, sentiment_score,
                reasoning, environment, is_simulated, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                ticker.strip().upper(),
                action.strip().upper(),
                confidence,
                trend_signal,
                sentiment_score,
                reasoning,
                env_clean,
                sim_int,
                ts,
            ),
        )
        logger.info(
            "Decisão IA registrada: %s %s [%s] confiança=%.2f (ID: %d)",
            action, ticker, env_clean.upper(), confidence, row_id,
        )
        return row_id

    def insert_telegram_log(
        self,
        message_type: str,
        content: str,
        success: bool = True,
        sent_at: str | None = None,
    ) -> int:
        """Registra um log de mensagem do Telegram."""
        ts = sent_at or _now_brt_iso()
        return self._execute(
            """INSERT INTO telegram_logs
               (message_type, content, sent_at, success)
               VALUES (?, ?, ?, ?)""",
            (message_type, content, ts, int(success)),
        )

    def insert_health(
        self,
        cpu_percent: float,
        ram_percent: float,
        disk_percent: float,
        timestamp: str | None = None,
    ) -> int:
        """Registra métricas de saúde do sistema."""
        ts = timestamp or _now_brt_iso()
        return self._execute(
            """INSERT INTO system_health
               (cpu_percent, ram_percent, disk_percent, timestamp)
               VALUES (?, ?, ?, ?)""",
            (cpu_percent, ram_percent, disk_percent, ts),
        )

    def insert_news(
        self,
        title: str,
        source: str,
        url: str,
        ticker: str | None = None,
        sentiment: float | None = None,
        published_at: str | None = None,
    ) -> int | None:
        """
        Registra uma notícia coletada deduplicando por `(url, ticker)`.
        """
        pub_ts = published_at or _now_brt_iso()
        scraped_at = _now_brt_iso()
        clean_ticker = ticker.strip().upper() if ticker and ticker.strip() else None
        try:
            with self._lock:
                with self._get_connection() as conn:
                    cursor = conn.execute(
                        """INSERT OR IGNORE INTO news_items
                           (title, source, url, ticker, sentiment, published_at, scraped_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (title, source, url, clean_ticker, sentiment, pub_ts, scraped_at),
                    )
                    if cursor.rowcount and cursor.rowcount > 0:
                        row_id = cursor.lastrowid or 0
                        logger.debug(
                            "Notícia registrada: '%s' de %s (ID: %d, ticker: %s)",
                            title[:60], source, row_id, clean_ticker,
                        )
                        return row_id if row_id else None
                    return None
        except Exception as exc:
            logger.debug("Notícia duplicada ou erro: %s", exc)
            return None

    def get_trades_today(
        self,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna todos os trades realizados hoje (BRT), opcionalmente filtrados por ambiente."""
        today = _today_brt_iso()
        clauses = ["timestamp LIKE ?"]
        params: list[Any] = [f"{today}%"]
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        clauses.extend(env_clauses)
        params.extend(env_params)
        sql = f"SELECT * FROM trades WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC"
        return self._fetch_all(sql, tuple(params))

    def get_trade_history(
        self,
        days: int = 30,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna o histórico de trades dos últimos N dias, opcionalmente filtrado por ambiente."""
        cutoff = (datetime.now(tz=BRT) - timedelta(days=days)).isoformat()
        clauses = ["timestamp >= ?"]
        params: list[Any] = [cutoff]
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        clauses.extend(env_clauses)
        params.extend(env_params)
        sql = f"SELECT * FROM trades WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC"
        return self._fetch_all(sql, tuple(params))

    def get_latest_sentiment(self, ticker: str) -> dict[str, Any] | None:
        """Retorna o score de sentimento mais recente de um ativo."""
        return self._fetch_one(
            """SELECT * FROM sentiment_scores
               WHERE ticker = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (ticker.strip().upper(),),
        )

    def get_sentiments_today(self, ticker: str | None = None) -> list[dict[str, Any]]:
        """Retorna scores de sentimento de hoje, opcionalmente filtrados por ticker."""
        today = _today_brt_iso()
        if ticker:
            return self._fetch_all(
                """SELECT * FROM sentiment_scores
                   WHERE ticker = ? AND timestamp LIKE ?
                   ORDER BY timestamp DESC""",
                (ticker.strip().upper(), f"{today}%"),
            )
        return self._fetch_all(
            """SELECT * FROM sentiment_scores
               WHERE timestamp LIKE ?
               ORDER BY timestamp DESC""",
            (f"{today}%",),
        )

    def get_daily_report(
        self,
        report_date: str | date | None = None,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> dict[str, Any] | None:
        """Retorna o relatório diário para a data e ambiente especificados."""
        if report_date is None:
            dt_str = _today_brt_iso()
        elif isinstance(report_date, date):
            dt_str = report_date.isoformat()
        else:
            dt_str = str(report_date)

        clauses = ["date = ?"]
        params: list[Any] = [dt_str]
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        clauses.extend(env_clauses)
        params.extend(env_params)
        sql = f"SELECT * FROM daily_reports WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT 1"
        return self._fetch_one(sql, tuple(params))

    def get_daily_reports(
        self,
        days: int = 30,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Retorna os últimos N relatórios diários ordenados cronologicamente (ASC).
        """
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        where = f"WHERE {' AND '.join(env_clauses)}" if env_clauses else ""
        sql = f"""
            SELECT * FROM (
                SELECT * FROM daily_reports
                {where}
                ORDER BY date DESC, id DESC
                LIMIT ?
            ) sub
            ORDER BY date ASC, id ASC
        """
        params = tuple(env_params + [max(1, int(days))])
        return self._fetch_all(sql, params)

    def get_report_history(
        self,
        days: int = 30,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna os relatórios diários dos últimos N dias (mais recentes primeiro)."""
        cutoff = (datetime.now(tz=BRT) - timedelta(days=days)).date().isoformat()
        clauses = ["date >= ?"]
        params: list[Any] = [cutoff]
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        clauses.extend(env_clauses)
        params.extend(env_params)
        sql = f"SELECT * FROM daily_reports WHERE {' AND '.join(clauses)} ORDER BY date DESC"
        return self._fetch_all(sql, tuple(params))

    def get_decisions_today(
        self,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna todas as decisões da IA tomadas hoje (BRT), isoladas por ambiente se especificado."""
        today = _today_brt_iso()
        clauses = ["timestamp LIKE ?"]
        params: list[Any] = [f"{today}%"]
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        clauses.extend(env_clauses)
        params.extend(env_params)
        sql = f"SELECT * FROM ai_decisions WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC"
        return self._fetch_all(sql, tuple(params))

    def get_recent_decisions(
        self,
        limit: int = 50,
        environment: str | None = None,
        is_simulated: bool | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna as decisões mais recentes da IA, isoladas por ambiente se especificado."""
        clauses, params = _build_env_filters(is_simulated, environment)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM ai_decisions {where} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self._fetch_all(sql, tuple(params))

    def get_latest_decision(
        self,
        ticker: str,
        environment: str | None = None,
        is_simulated: bool | None = None,
    ) -> dict[str, Any] | None:
        """Retorna a decisão mais recente da IA para um ativo."""
        clauses = ["ticker = ?"]
        params: list[Any] = [ticker.strip().upper()]
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        clauses.extend(env_clauses)
        params.extend(env_params)
        sql = f"SELECT * FROM ai_decisions WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC, id DESC LIMIT 1"
        return self._fetch_one(sql, tuple(params))

    def get_latest_snapshot(self, ticker: str) -> dict[str, Any] | None:
        """Retorna o snapshot de mercado mais recente de um ativo."""
        return self._fetch_one(
            """SELECT * FROM market_snapshots
               WHERE ticker = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (ticker.strip().upper(),),
        )

    def reset_operational_data(self, environment: str = "homologation") -> None:
        """
        Reseta dados operacionais estritamente do ambiente alvo, preservando
        o outro ambiente, usuários, sessões e metas de saque de produção.
        """
        env_clean = (environment or "homologation").strip().lower()
        if env_clean not in ("homologation", "production"):
            raise ValueError(f"Ambiente inválido para reset: {environment}")
        sim_int = 1 if env_clean == "homologation" else 0

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "DELETE FROM trades WHERE environment = ? OR is_simulated = ?",
                    (env_clean, sim_int),
                )
                cursor.execute(
                    "DELETE FROM daily_reports WHERE environment = ? OR is_simulated = ?",
                    (env_clean, sim_int),
                )
                cursor.execute(
                    "DELETE FROM ai_decisions WHERE environment = ? OR is_simulated = ?",
                    (env_clean, sim_int),
                )
                cursor.execute(
                    "DELETE FROM capital_transactions WHERE environment = ? OR is_simulated = ?",
                    (env_clean, sim_int),
                )
                if env_clean == "production":
                    cursor.execute(
                        "DELETE FROM withdrawal_goals WHERE environment = 'production'"
                    )
            logger.info(
                "Dados operacionais do ambiente '%s' resetados com sucesso (isolamento preservado).",
                env_clean,
            )

    def get_latest_health(self) -> dict[str, Any] | None:
        """Retorna a métrica de saúde mais recente do sistema."""
        return self._fetch_one(
            "SELECT * FROM system_health ORDER BY timestamp DESC LIMIT 1",
        )

    def get_recent_news(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retorna as notícias mais recentes do banco."""
        return self._fetch_all(
            "SELECT * FROM news_items ORDER BY published_at DESC LIMIT ?",
            (limit,),
        )

    def get_news_by_ticker(self, ticker: str, limit: int = 20) -> list[dict[str, Any]]:
        """Retorna notícias de um ticker específico."""
        return self._fetch_all(
            """SELECT * FROM news_items
               WHERE ticker = ?
               ORDER BY published_at DESC LIMIT ?""",
            (ticker.strip().upper(), limit),
        )

    def get_decisions_history(
        self,
        ticker: str | None = None,
        days: int = 30,
        limit: int = 100,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retorna histórico de decisões da IA, opcionalmente filtrado por ticker e ambiente."""
        cutoff = (datetime.now(tz=BRT) - timedelta(days=days)).isoformat()
        clauses = ["timestamp >= ?"]
        params: list[Any] = [cutoff]
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.strip().upper())
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        clauses.extend(env_clauses)
        params.extend(env_params)
        params.append(limit)
        sql = f"SELECT * FROM ai_decisions WHERE {' AND '.join(clauses)} ORDER BY timestamp DESC LIMIT ?"
        return self._fetch_all(sql, tuple(params))

    def get_performance_summary(
        self,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        """Alias para get_performance_metrics com suporte a filtros de ambiente."""
        return self.get_performance_metrics(is_simulated=is_simulated, environment=environment)

    def get_performance_metrics(
        self,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> dict[str, Any]:
        """
        Calcula métricas reais de desempenho:
        - `win_rate` baseado no P&L realizado dos trades encerrados (com fallback para retornos diários).
        - `max_drawdown` calculado pico-a-vale (peak-to-trough) sobre `total_equity`.
        """
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        where = f"WHERE {' AND '.join(env_clauses)}" if env_clauses else ""

        trades_asc = self._fetch_all(
            f"""
            SELECT * FROM (
                SELECT * FROM trades {where} ORDER BY timestamp DESC, id DESC LIMIT 1000
            ) sub
            ORDER BY timestamp ASC, id ASC
            """,
            tuple(env_params),
        )
        reports = self.get_daily_reports(days=365, is_simulated=is_simulated, environment=environment)

        total_trades = len(trades_asc)
        total_buys = sum(1 for t in trades_asc if t.get("action") == "BUY")
        total_sells = sum(1 for t in trades_asc if t.get("action") == "SELL")

        # Cálculo de P&L realizado por venda (custo médio móvel por ativo)
        avg_cost: dict[str, float] = {}
        held_qty: dict[str, int] = {}
        closed_pnls: list[float] = []
        for tr in trades_asc:
            tk = str(tr.get("ticker") or "").upper()
            act = str(tr.get("action") or "").upper()
            qty = int(tr.get("quantity") or 0)
            px = float(tr.get("price") or 0.0)
            if not tk or qty <= 0 or px <= 0.0:
                continue
            if act == "BUY":
                prev_q = held_qty.get(tk, 0)
                prev_c = avg_cost.get(tk, 0.0)
                new_q = prev_q + qty
                avg_cost[tk] = ((prev_q * prev_c) + (qty * px)) / new_q
                held_qty[tk] = new_q
            elif act == "SELL":
                prev_q = held_qty.get(tk, 0)
                if prev_q > 0 and tk in avg_cost:
                    matched_q = min(prev_q, qty)
                    closed_pnls.append((px - avg_cost[tk]) * matched_q)
                    rem_q = prev_q - matched_q
                    held_qty[tk] = rem_q
                    if rem_q == 0:
                        avg_cost.pop(tk, None)

        # Retornos diários reais e drawdown pico-a-vale sobre total_equity
        daily_returns: list[float] = []
        peak_equity = 0.0
        max_drawdown = 0.0

        for idx, rep in enumerate(reports):
            eq = float(rep.get("total_equity") or 0.0)
            init_cap = float(rep.get("initial_capital") or 0.0)

            if idx == 0:
                if init_cap > 0.0:
                    peak_equity = max(peak_equity, init_cap)
                raw_daily = rep.get("daily_pnl_percent")
                if raw_daily is not None and float(raw_daily) != 0.0:
                    daily_ret = float(raw_daily)
                elif init_cap > 0.0 and eq > 0.0:
                    daily_ret = ((eq - init_cap) / init_cap) * 100.0
                else:
                    daily_ret = float(rep.get("pnl_percent") or 0.0)
            else:
                prev_eq = float(reports[idx - 1].get("total_equity") or 0.0)
                if prev_eq > 0.0 and eq > 0.0:
                    daily_ret = ((eq - prev_eq) / prev_eq) * 100.0
                elif rep.get("daily_pnl_percent") is not None:
                    daily_ret = float(rep["daily_pnl_percent"])
                else:
                    daily_ret = float(rep.get("pnl_percent") or 0.0)

            daily_returns.append(daily_ret)

            if eq > 0.0:
                if eq > peak_equity:
                    peak_equity = eq
                if peak_equity > 0.0:
                    dd_pct = ((eq - peak_equity) / peak_equity) * 100.0
                    if dd_pct < max_drawdown:
                        max_drawdown = dd_pct

        positive_days = sum(1 for r in daily_returns if r > 0.0)
        negative_days = sum(1 for r in daily_returns if r < 0.0)
        avg_pnl = (sum(daily_returns) / len(daily_returns)) if daily_returns else 0.0

        if closed_pnls:
            wins = sum(1 for p in closed_pnls if p > 0.0)
            win_rate = (wins / len(closed_pnls)) * 100.0
        elif daily_returns:
            win_rate = (positive_days / len(daily_returns)) * 100.0
        else:
            win_rate = 0.0

        return {
            "total_trades": total_trades,
            "total_buys": total_buys,
            "total_sells": total_sells,
            "days_tracked": len(daily_returns),
            "positive_days": positive_days,
            "negative_days": negative_days,
            "win_rate": round(win_rate, 1),
            "avg_daily_pnl": round(avg_pnl, 2),
            "max_drawdown": round(max_drawdown, 2),
        }

    def count_trades_today(
        self,
        is_simulated: bool | None = None,
        environment: str | None = None,
    ) -> dict[str, int]:
        """Conta compras e vendas realizadas hoje, opcionalmente filtradas por ambiente."""
        today = _today_brt_iso()
        result = {"buys": 0, "sells": 0}
        clauses = ["timestamp LIKE ?"]
        params: list[Any] = [f"{today}%"]
        env_clauses, env_params = _build_env_filters(is_simulated, environment)
        clauses.extend(env_clauses)
        params.extend(env_params)

        rows = self._fetch_all(
            f"""SELECT action, COUNT(*) AS cnt
                FROM trades
                WHERE {' AND '.join(clauses)}
                GROUP BY action""",
            tuple(params),
        )
        for row in rows:
            if row["action"] == "BUY":
                result["buys"] = int(row["cnt"])
            elif row["action"] == "SELL":
                result["sells"] = int(row["cnt"])

        return result

    def reset_all_data(self) -> None:
        """Limpa todas as tabelas operacionais para reset completo (preservando usuários)."""
        with self._lock:
            with self._get_connection() as conn:
                tables = [
                    "trades",
                    "sentiment_scores",
                    "market_snapshots",
                    "daily_reports",
                    "ai_decisions",
                    "telegram_logs",
                    "system_health",
                    "news_items",
                    "capital_transactions",
                    "withdrawal_goals",
                ]
                for table in tables:
                    conn.execute(f"DELETE FROM {table};")
            logger.info("Todas as tabelas operacionais do banco foram resetadas com sucesso.")

    def prune_old_operational_logs(
        self,
        keep_hold_days: int = 14,
        keep_news_days: int = 30,
        keep_health_days: int = 14,
    ) -> dict[str, int]:
        """
        Expurga registros transitórios antigos sem jamais apagar trades, relatórios diários,
        transações de capital ou decisões acionáveis (COMPRA / VENDA / VENDA_EMERGENCIAL).
        """
        now = datetime.now(tz=BRT)
        hold_cutoff = (now - timedelta(days=max(1, int(keep_hold_days)))).isoformat()
        news_cutoff = (now - timedelta(days=max(1, int(keep_news_days)))).isoformat()
        health_cutoff = (now - timedelta(days=max(1, int(keep_health_days)))).isoformat()
        now_iso = now.isoformat()

        stats = {
            "ai_decisions_hold": 0,
            "news_items": 0,
            "sentiment_scores": 0,
            "telegram_logs": 0,
            "system_health": 0,
            "market_snapshots": 0,
            "expired_sessions": 0,
        }
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    """
                    DELETE FROM ai_decisions
                    WHERE action IN ('AGUARDAR', 'HOLD')
                      AND timestamp < ?
                    """,
                    (hold_cutoff,),
                )
                stats["ai_decisions_hold"] = max(0, cur.rowcount)

                cur = conn.execute(
                    "DELETE FROM news_items WHERE COALESCE(published_at, scraped_at, '') < ? AND COALESCE(published_at, scraped_at, '') != ''",
                    (news_cutoff,),
                )
                stats["news_items"] = max(0, cur.rowcount)

                cur = conn.execute(
                    "DELETE FROM sentiment_scores WHERE timestamp < ?",
                    (news_cutoff,),
                )
                stats["sentiment_scores"] = max(0, cur.rowcount)

                cur = conn.execute(
                    "DELETE FROM telegram_logs WHERE sent_at < ?",
                    (health_cutoff,),
                )
                stats["telegram_logs"] = max(0, cur.rowcount)

                cur = conn.execute(
                    "DELETE FROM system_health WHERE timestamp < ?",
                    (health_cutoff,),
                )
                stats["system_health"] = max(0, cur.rowcount)

                cur = conn.execute(
                    "DELETE FROM market_snapshots WHERE timestamp < ?",
                    (health_cutoff,),
                )
                stats["market_snapshots"] = max(0, cur.rowcount)

                cur = conn.execute(
                    "DELETE FROM sessions WHERE expires_at < ?",
                    (now_iso,),
                )
                stats["expired_sessions"] = max(0, cur.rowcount)

                conn.execute("PRAGMA optimize;")

        total_deleted = sum(stats.values())
        if total_deleted > 0:
            logger.info("Manutenção SQLite concluída — registros expurgados: %s", stats)
        return stats

    def get_d2_settlement_summary(
        self,
        scheduler: Any = None,
        reference_date: Any = None,
    ) -> dict[str, Any]:
        """
        Calcula o financeiro líquido de vendas reais na B3 ainda em liquidação física/financeira
        (D+2 dias úteis) aplicando compensação multilateral diária (Vendas Líquidas - Compras Brutas
        no mesmo pregão T), permitindo distinguir caixa livre p/ novas compras de saldo efetivamente
        sacável via PIX/TED.
        """
        from config.settings import settings

        if scheduler is None:
            from core.scheduler import MarketScheduler
            scheduler = MarketScheduler()

        today = (
            reference_date
            if reference_date is not None
            else datetime.now(tz=BRT).date()
        )
        lookback_iso = (
            datetime.combine(today, datetime.min.time(), tzinfo=BRT) - timedelta(days=12)
        ).isoformat()

        rows = self._fetch_all(
            """
            SELECT timestamp, action, total_value, price, quantity
            FROM trades
            WHERE is_simulated = 0
              AND action IN ('SELL', 'VENDA', 'VENDA_EMERGENCIAL', 'EMERGENCY_SELL', 'BUY', 'COMPRA')
              AND timestamp >= ?
            ORDER BY timestamp ASC, id ASC
            """,
            (lookback_iso,),
        )

        fee_rate = max(0.0, float(getattr(settings, "B3_FEE_RATE", 0.0003) or 0.0))
        daily_buckets: dict[Any, dict[str, Any]] = {}

        for row in rows:
            raw_ts = str(row.get("timestamp") or "").strip()
            if not raw_ts:
                continue
            try:
                dt = datetime.fromisoformat(raw_ts)
                trade_date = dt.astimezone(BRT).date() if dt.tzinfo else dt.date()
            except ValueError:
                continue

            settlement_date = scheduler.add_business_days(trade_date, 2)
            if settlement_date <= today:
                continue

            gross_val = float(
                row.get("total_value")
                or (float(row.get("price") or 0.0) * int(row.get("quantity") or 0))
            )
            if gross_val <= 0:
                continue

            bucket = daily_buckets.setdefault(
                trade_date,
                {
                    "settlement_date": settlement_date,
                    "sells_net": 0.0,
                    "buys_gross": 0.0,
                    "sell_count": 0,
                },
            )
            act_upper = str(row.get("action") or "").strip().upper()
            if act_upper in ("SELL", "VENDA", "VENDA_EMERGENCIAL", "EMERGENCY_SELL"):
                bucket["sells_net"] += gross_val * (1.0 - fee_rate)
                bucket["sell_count"] += 1
            elif act_upper in ("BUY", "COMPRA"):
                bucket["buys_gross"] += gross_val

        unsettled = 0.0
        pending_count = 0
        earliest_settlement: Any = None

        for trade_date in sorted(daily_buckets.keys()):
            bucket = daily_buckets[trade_date]
            daily_net_sell = max(0.0, float(bucket["sells_net"]) - float(bucket["buys_gross"]))
            if daily_net_sell > 0.0:
                unsettled += daily_net_sell
                pending_count += int(bucket["sell_count"])
                settlement_date = bucket["settlement_date"]
                if earliest_settlement is None or settlement_date < earliest_settlement:
                    earliest_settlement = settlement_date

        return {
            "unsettled_d2_cash": round(unsettled, 2),
            "pending_sales_count": pending_count,
            "next_settlement_date": earliest_settlement.isoformat() if earliest_settlement else None,
        }

    def close(self) -> None:
        """Encerra o gerenciador de banco de dados (no-op seguro para conexões por contexto)."""
        logger.info("DatabaseManager encerrado para: %s", self.db_path)

    def __repr__(self) -> str:
        return f"DatabaseManager(db_path={self.db_path})"

    # =========================================================================
    # Autenticação, Sessões e Proteção contra Brute-Force por Usuário
    # =========================================================================

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM users WHERE username = ?",
                    (username.strip(),),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM users WHERE id = ?",
                    (user_id,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def is_user_locked(self, user: dict[str, Any] | None) -> bool:
        """Verifica se o usuário está temporariamente bloqueado por tentativas falhas."""
        if not user:
            return False
        locked_until_raw = user.get("locked_until")
        if not locked_until_raw:
            return False
        try:
            locked_dt = datetime.fromisoformat(str(locked_until_raw))
            if locked_dt.tzinfo is None:
                locked_dt = locked_dt.replace(tzinfo=BRT)
            if datetime.now(tz=BRT) < locked_dt:
                return True
        except ValueError:
            return False

        uid = user.get("id")
        if isinstance(uid, int):
            self.reset_user_login_failures(uid)
        return False

    def record_user_login_failure(
        self,
        user_id: int,
        max_attempts: int = 5,
        lockout_seconds: int = 900,
    ) -> None:
        """Incrementa contador de falhas de login do usuário e aplica bloqueio se atingir limite."""
        now = datetime.now(tz=BRT)
        now_str = now.isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT failed_login_attempts, locked_until FROM users WHERE id = ?",
                    (user_id,),
                )
                row = cursor.fetchone()
                if not row:
                    return

                prev_attempts = int(row["failed_login_attempts"] or 0)
                prev_locked = row["locked_until"]
                if prev_locked:
                    try:
                        locked_dt = datetime.fromisoformat(str(prev_locked))
                        if locked_dt.tzinfo is None:
                            locked_dt = locked_dt.replace(tzinfo=BRT)
                        if now >= locked_dt:
                            prev_attempts = 0
                    except ValueError:
                        prev_attempts = 0

                attempts = prev_attempts + 1
                locked_until = (
                    (now + timedelta(seconds=lockout_seconds)).isoformat()
                    if attempts >= max(1, max_attempts)
                    else None
                )
                conn.execute(
                    """
                    UPDATE users
                    SET failed_login_attempts = ?, locked_until = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (attempts, locked_until, now_str, user_id),
                )

    def reset_user_login_failures(self, user_id: int) -> None:
        """Zera falhas de login e remove bloqueio temporário do usuário."""
        now_str = _now_brt_iso()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    UPDATE users
                    SET failed_login_attempts = 0, locked_until = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (now_str, user_id),
                )

    def create_user(self, username: str, password: str, must_change_password: bool = False) -> int:
        from auth import PasswordManager
        p_hash, p_salt = PasswordManager.hash_password(password)
        now_str = _now_brt_iso()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO users (username, password_hash, salt, must_change_password, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (username.strip(), p_hash, p_salt, 1 if must_change_password else 0, now_str, now_str),
                )
                return cursor.lastrowid or 0

    def update_user_credentials(self, user_id: int, new_username: str, new_password: str) -> bool:
        from auth import PasswordManager
        p_hash, p_salt = PasswordManager.hash_password(new_password)
        now_str = _now_brt_iso()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE users
                    SET username = ?, password_hash = ?, salt = ?, must_change_password = 0,
                        failed_login_attempts = 0, locked_until = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (new_username.strip(), p_hash, p_salt, now_str, user_id),
                )
                return cursor.rowcount > 0

    def reset_user_password(self, username: str, new_password: str, force_first_login: bool = True) -> bool:
        from auth import PasswordManager
        p_hash, p_salt = PasswordManager.hash_password(new_password)
        now_str = _now_brt_iso()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE users
                    SET password_hash = ?, salt = ?, must_change_password = ?,
                        failed_login_attempts = 0, locked_until = NULL, updated_at = ?
                    WHERE username = ?
                    """,
                    (p_hash, p_salt, 1 if force_first_login else 0, now_str, username.strip()),
                )
                return cursor.rowcount > 0

    def create_session(
        self,
        user_id: int,
        ip_address: str = "",
        user_agent: str = "",
        max_session_seconds: int = 28800,
    ) -> str:
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        now = datetime.now(tz=BRT)
        now_str = now.isoformat()
        expires_str = (now + timedelta(seconds=max_session_seconds)).isoformat()

        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO sessions (
                        session_token, csrf_token, user_id, ip_address, user_agent,
                        created_at, last_active_at, expires_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (token, csrf_token, user_id, ip_address, user_agent, now_str, now_str, expires_str),
                )
        return token

    def get_valid_session(
        self,
        token: str,
        max_idle_seconds: int = 900,
        touch: bool = False,
    ) -> dict[str, Any] | None:
        """
        Retorna dados da sessão se válida (não expirada e dentro do limite de ociosidade).
        Atualiza `last_active_at` apenas quando `touch=True` para que polling passivo
        não neutralize o timeout de inatividade.
        """
        if not token:
            return None

        now = datetime.now(tz=BRT)
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT * FROM sessions WHERE session_token = ?",
                    (token,),
                )
                row = cursor.fetchone()
                if not row:
                    return None
                sess = dict(row)

                expires_at = datetime.fromisoformat(sess["expires_at"])
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=BRT)

                last_active = datetime.fromisoformat(sess["last_active_at"])
                if last_active.tzinfo is None:
                    last_active = last_active.replace(tzinfo=BRT)

                if now > expires_at or (now - last_active).total_seconds() > max_idle_seconds:
                    conn.execute("DELETE FROM sessions WHERE session_token = ?", (token,))
                    return None

                if touch:
                    now_str = now.isoformat()
                    conn.execute(
                        "UPDATE sessions SET last_active_at = ? WHERE session_token = ?",
                        (now_str, token),
                    )
                    sess["last_active_at"] = now_str

                if not sess.get("csrf_token"):
                    csrf = secrets.token_urlsafe(32)
                    conn.execute(
                        "UPDATE sessions SET csrf_token = ? WHERE session_token = ?",
                        (csrf, token),
                    )
                    sess["csrf_token"] = csrf

                return sess

    def touch_session(self, token: str) -> None:
        now_str = _now_brt_iso()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE sessions SET last_active_at = ? WHERE session_token = ?",
                    (now_str, token),
                )

    def revoke_session(self, token: str) -> bool:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM sessions WHERE session_token = ?",
                    (token,),
                )
                return cursor.rowcount > 0

    def revoke_all_user_sessions(self, user_id: int) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    # =========================================================================
    # Configurações Dinâmicas, Metas de Saque e Transações de Capital
    # =========================================================================

    def get_config(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT value FROM system_config WHERE key = ?",
                    (key.strip(),),
                )
                row = cursor.fetchone()
                if row:
                    return str(row["value"])
                return default

    def set_config(self, key: str, value: Any) -> None:
        now_str = _now_brt_iso()
        val_str = str(value)
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO system_config (key, value, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                    """,
                    (key.strip(), val_str, now_str),
                )

    def get_all_configs(self) -> dict[str, str]:
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute("SELECT key, value FROM system_config")
                return {row["key"]: row["value"] for row in cursor.fetchall()}

    def create_withdrawal_goal(
        self,
        amount: float,
        mode: str,
        target_equity: float,
        base_equity: float,
        notes: str = "",
        environment: str = "production",
    ) -> int:
        now_str = _now_brt_iso()
        env_clean = (environment or "production").strip().lower()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE withdrawal_goals SET status = 'cancelled' WHERE status = 'pending' AND environment = ?",
                    (env_clean,),
                )
                cursor = conn.execute(
                    """
                    INSERT INTO withdrawal_goals (
                        amount, mode, target_equity, base_equity, status, environment, created_at, notes
                    )
                    VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (amount, mode, target_equity, base_equity, env_clean, now_str, notes),
                )
                return cursor.lastrowid or 0

    def get_active_withdrawal_goal(self, environment: str = "production") -> dict[str, Any] | None:
        env_clean = (environment or "production").strip().lower()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT * FROM withdrawal_goals
                    WHERE status = 'pending' AND environment = ?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (env_clean,),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def cancel_active_withdrawal_goal(self, environment: str | None = None) -> bool:
        with self._lock:
            with self._get_connection() as conn:
                if environment:
                    cursor = conn.execute(
                        "UPDATE withdrawal_goals SET status = 'cancelled' WHERE status = 'pending' AND environment = ?",
                        (environment.strip().lower(),),
                    )
                else:
                    cursor = conn.execute(
                        "UPDATE withdrawal_goals SET status = 'cancelled' WHERE status = 'pending'",
                    )
                return cursor.rowcount > 0

    def complete_withdrawal_goal(self, goal_id: int, notes: str = "") -> bool:
        now_str = _now_brt_iso()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    UPDATE withdrawal_goals
                    SET status = 'completed', completed_at = ?,
                        notes = CASE WHEN ? != '' THEN ? ELSE notes END
                    WHERE id = ?
                    """,
                    (now_str, notes, notes, goal_id),
                )
                return cursor.rowcount > 0

    def record_capital_transaction(
        self,
        tx_type: str,
        amount: float,
        resulting_ai_capital: float,
        resulting_vault: float,
        details: str = "",
        environment: str | None = "homologation",
        is_simulated: bool | None = None,
    ) -> int:
        now_str = _now_brt_iso()
        env_clean, sim_int = _resolve_env_and_sim(is_simulated, environment, default_simulated=True)
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO capital_transactions (
                        timestamp, tx_type, amount, resulting_ai_capital,
                        resulting_vault, environment, is_simulated, details
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_str,
                        tx_type,
                        amount,
                        resulting_ai_capital,
                        resulting_vault,
                        env_clean,
                        sim_int,
                        details,
                    ),
                )
                return cursor.lastrowid or 0

    def get_capital_transactions(
        self,
        limit: int = 50,
        environment: str | None = None,
        is_simulated: bool | None = None,
    ) -> list[dict[str, Any]]:
        clauses, params = _build_env_filters(is_simulated, environment)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM capital_transactions {where} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self._fetch_all(sql, tuple(params))
