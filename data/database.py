"""
Gerenciador de banco de dados SQLite do Projeto Córtex.

Fornece armazenamento persistente thread-safe para trades, scores de
sentimento, snapshots de mercado, relatórios diários, decisões da IA,
logs do Telegram e métricas de saúde do sistema.

Utiliza context managers para conexões e threading.Lock para
garantir acesso seguro em ambientes multi-thread.
"""

import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator

from utils.logger import get_logger

logger = get_logger(__name__)

_BRT = timezone(timedelta(hours=-3), name="BRT")

def _now_brt_iso() -> str:
    """Retorna timestamp atual em BRT no formato ISO 8601."""
    return datetime.now(tz=_BRT).isoformat()

def _today_brt_iso() -> str:
    """Retorna a data atual em BRT no formato ISO 8601 (só data)."""
    return datetime.now(tz=_BRT).date().isoformat()

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
    date                TEXT    NOT NULL UNIQUE,
    buys_count          INTEGER NOT NULL DEFAULT 0,
    sells_count         INTEGER NOT NULL DEFAULT 0,
    free_cash           REAL    NOT NULL DEFAULT 0,
    allocated_capital   REAL    NOT NULL DEFAULT 0,
    initial_capital     REAL    NOT NULL DEFAULT 0,
    total_equity        REAL    NOT NULL DEFAULT 0,
    pnl_percent         REAL    NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_decisions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    action          TEXT    NOT NULL,
    confidence      REAL    NOT NULL,
    trend_signal    TEXT,
    sentiment_score REAL,
    reasoning       TEXT,
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
    url             TEXT    NOT NULL UNIQUE,
    ticker          TEXT,
    sentiment       REAL,
    published_at    TEXT    NOT NULL,
    scraped_at      TEXT    NOT NULL
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
    user_id             INTEGER NOT NULL,
    ip_address          TEXT,
    user_agent          TEXT,
    created_at          TEXT    NOT NULL,
    last_active_at      TEXT    NOT NULL,
    expires_at          TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Índices para consultas frequentes
CREATE INDEX IF NOT EXISTS idx_users_username      ON users(username);
CREATE INDEX IF NOT EXISTS idx_sessions_token      ON sessions(session_token);
CREATE INDEX IF NOT EXISTS idx_sessions_user       ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_trades_ticker      ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp    ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_sentiment_ticker    ON sentiment_scores(ticker);
CREATE INDEX IF NOT EXISTS idx_sentiment_timestamp ON sentiment_scores(timestamp);
CREATE INDEX IF NOT EXISTS idx_snapshots_ticker    ON market_snapshots(ticker);
CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON market_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_ticker    ON ai_decisions(ticker);
CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON ai_decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_daily_reports_date  ON daily_reports(date);
CREATE INDEX IF NOT EXISTS idx_health_timestamp    ON system_health(timestamp);
CREATE INDEX IF NOT EXISTS idx_news_ticker         ON news_items(ticker);
CREATE INDEX IF NOT EXISTS idx_news_published      ON news_items(published_at);
"""

class DatabaseManager:
    """
    Gerenciador de banco de dados SQLite para o Projeto Córtex.

    Fornece operações CRUD thread-safe para todas as tabelas do sistema.
    As tabelas são criadas automaticamente na inicialização.

    Atributos:
        db_path: Caminho do arquivo SQLite.

    Exemplo::

        from data.database import DatabaseManager
        db = DatabaseManager()
        db.insert_trade(
            ticker='PETR4', action='BUY', quantity=5,
            price=38.50, total_value=192.50,
            stop_loss=34.65, reasoning='Tendência de alta confirmada'
        )
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        """
        Inicializa o gerenciador de banco de dados.

        Args:
            db_path: Caminho do arquivo SQLite.
                     Se None, usa o padrão de config/settings.
        """
        if db_path is None:
            from config.settings import settings
            self.db_path: Path = settings.DB_PATH
        else:
            self.db_path = Path(db_path)

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_database()

    def _init_database(self) -> None:
        """Cria as tabelas e índices no banco de dados."""
        try:
            with self._get_connection() as conn:
                conn.executescript(_CREATE_TABLES_SQL)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users")
                if cursor.fetchone()[0] == 0:
                    from auth import PasswordManager
                    p_hash, p_salt = PasswordManager.hash_password("Admin")
                    now_str = _now_brt_iso()
                    cursor.execute(
                        """
                        INSERT INTO users (username, password_hash, salt, must_change_password, created_at, updated_at)
                        VALUES (?, ?, ?, 1, ?, ?)
                        """,
                        ("Admin", p_hash, p_salt, now_str, now_str)
                    )
            logger.info("Banco de dados inicializado: %s", self.db_path)
        except sqlite3.Error as exc:
            logger.critical("Falha ao inicializar banco de dados: %s", exc)
            raise

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager para conexão com o banco de dados.

        Garante que a conexão seja fechada após o uso e que
        transações sejam commitadas ou revertidas corretamente.

        Yields:
            Conexão SQLite configurada com Row factory.
        """
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            detect_types=sqlite3.PARSE_DECLTYPES,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
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
        """
        Executa uma instrução SQL com lock e retorna o lastrowid.

        Args:
            sql: Instrução SQL com placeholders '?'.
            params: Parâmetros para os placeholders.

        Returns:
            ID da última linha inserida.
        """
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
        """
        Executa consulta SQL e retorna todas as linhas como dicts.

        Args:
            sql: Consulta SELECT com placeholders '?'.
            params: Parâmetros para os placeholders.

        Returns:
            Lista de dicionários com os resultados.
        """
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
        """
        Executa consulta SQL e retorna a primeira linha como dict.

        Args:
            sql: Consulta SELECT com placeholders '?'.
            params: Parâmetros para os placeholders.

        Returns:
            Dicionário com o resultado ou None se não encontrar.
        """
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
        is_simulated: bool = True,
        timestamp: str | None = None,
    ) -> int:
        """
        Registra uma operação de compra ou venda no banco de dados.

        Args:
            ticker: Código do ativo (ex: 'PETR4').
            action: Tipo da operação ('BUY' ou 'SELL').
            quantity: Quantidade de ações.
            price: Preço unitário da ação.
            total_value: Valor total da operação.
            stop_loss: Preço de stop loss (opcional).
            reasoning: Justificativa da decisão (opcional).
            is_simulated: Se True, operação simulada.
            timestamp: Timestamp ISO 8601. Se None, usa agora em BRT.

        Returns:
            ID do registro inserido.
        """
        action = action.upper().strip()
        if action not in ("BUY", "SELL"):
            raise ValueError(f"Ação inválida: '{action}'. Use 'BUY' ou 'SELL'.")

        ts = timestamp or _now_brt_iso()
        row_id = self._execute(
            """INSERT INTO trades
               (ticker, action, quantity, price, total_value, stop_loss,
                timestamp, reasoning, is_simulated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker.upper(), action, quantity, price, total_value,
             stop_loss, ts, reasoning, int(is_simulated)),
        )
        logger.info(
            "Trade registrado: %s %s x%d @ R$%.2f (ID: %d, simulado: %s)",
            action, ticker, quantity, price, row_id, is_simulated,
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
        """
        Registra um score de análise de sentimento.

        Args:
            ticker: Código do ativo.
            score: Score de sentimento (-1.0 a 1.0).
            source: Fonte da análise (ex: 'infomoney', 'google_news').
            headline: Título da notícia analisada (opcional).
            timestamp: Timestamp ISO 8601. Se None, usa agora em BRT.

        Returns:
            ID do registro inserido.
        """
        ts = timestamp or _now_brt_iso()
        row_id = self._execute(
            """INSERT INTO sentiment_scores
               (ticker, score, source, headline, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (ticker.upper(), score, source, headline, ts),
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
        """
        Registra um snapshot de dados de mercado.

        Args:
            ticker: Código do ativo.
            price: Preço atual.
            volume: Volume negociado (opcional).
            bid: Melhor oferta de compra (opcional).
            ask: Melhor oferta de venda (opcional).
            timestamp: Timestamp ISO 8601. Se None, usa agora em BRT.

        Returns:
            ID do registro inserido.
        """
        ts = timestamp or _now_brt_iso()
        return self._execute(
            """INSERT INTO market_snapshots
               (ticker, price, volume, bid, ask, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ticker.upper(), price, volume, bid, ask, ts),
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
    ) -> int:
        """
        Registra ou atualiza o relatório diário.

        Usa INSERT OR REPLACE para garantir um único registro por data.

        Args:
            report_date: Data do relatório. Se None, usa hoje em BRT.
            buys_count: Quantidade de compras no dia.
            sells_count: Quantidade de vendas no dia.
            free_cash: Capital livre disponível.
            allocated_capital: Capital alocado em posições.
            initial_capital: Capital inicial da conta.
            total_equity: Patrimônio total (caixa + posições).
            pnl_percent: Lucro/prejuízo percentual do dia.

        Returns:
            ID do registro inserido/atualizado.
        """
        if report_date is None:
            dt_str = _today_brt_iso()
        elif isinstance(report_date, date):
            dt_str = report_date.isoformat()
        else:
            dt_str = report_date

        row_id = self._execute(
            """INSERT OR REPLACE INTO daily_reports
               (date, buys_count, sells_count, free_cash, allocated_capital,
                initial_capital, total_equity, pnl_percent)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (dt_str, buys_count, sells_count, free_cash,
             allocated_capital, initial_capital, total_equity, pnl_percent),
        )
        logger.info(
            "Relatório diário registrado: %s | Compras: %d | Vendas: %d | P&L: %.2f%%",
            dt_str, buys_count, sells_count, pnl_percent,
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
    ) -> int:
        """
        Registra uma decisão tomada pela inteligência artificial.

        Args:
            ticker: Código do ativo.
            action: Ação decidida (ex: 'BUY', 'SELL', 'HOLD').
            confidence: Nível de confiança (0.0 a 1.0).
            trend_signal: Sinal da análise técnica (opcional).
            sentiment_score: Score de sentimento agregado (opcional).
            reasoning: Raciocínio da decisão (opcional).
            timestamp: Timestamp ISO 8601. Se None, usa agora em BRT.

        Returns:
            ID do registro inserido.
        """
        ts = timestamp or _now_brt_iso()
        row_id = self._execute(
            """INSERT INTO ai_decisions
               (ticker, action, confidence, trend_signal, sentiment_score,
                reasoning, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticker.upper(), action.upper(), confidence,
             trend_signal, sentiment_score, reasoning, ts),
        )
        logger.info(
            "Decisão IA registrada: %s %s confiança=%.2f (ID: %d)",
            action, ticker, confidence, row_id,
        )
        return row_id

    def insert_telegram_log(
        self,
        message_type: str,
        content: str,
        success: bool = True,
        sent_at: str | None = None,
    ) -> int:
        """
        Registra um log de mensagem do Telegram.

        Args:
            message_type: Tipo da mensagem (ex: 'trade_alert', 'daily_report').
            content: Conteúdo da mensagem enviada.
            success: Se o envio foi bem-sucedido.
            sent_at: Timestamp ISO 8601. Se None, usa agora em BRT.

        Returns:
            ID do registro inserido.
        """
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
        """
        Registra métricas de saúde do sistema.

        Args:
            cpu_percent: Uso de CPU em percentual.
            ram_percent: Uso de RAM em percentual.
            disk_percent: Uso de disco em percentual.
            timestamp: Timestamp ISO 8601. Se None, usa agora em BRT.

        Returns:
            ID do registro inserido.
        """
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
        Registra uma notícia coletada no banco de dados.

        Usa INSERT OR IGNORE para deduplicar por URL.

        Args:
            title: Título da notícia.
            source: Fonte da notícia (ex: 'Google News', 'InfoMoney').
            url: URL completa da notícia (unique).
            ticker: Ticker B3 associado (opcional).
            sentiment: Score de sentimento (-1.0 a 1.0, opcional).
            published_at: Timestamp de publicação ISO 8601. Se None, usa agora.

        Returns:
            ID do registro inserido ou None se duplicado.
        """
        pub_ts = published_at or _now_brt_iso()
        scraped_at = _now_brt_iso()
        try:
            row_id = self._execute(
                """INSERT OR IGNORE INTO news_items
                   (title, source, url, ticker, sentiment, published_at, scraped_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (title, source, url, ticker, sentiment, pub_ts, scraped_at),
            )
            if row_id:
                logger.debug(
                    "Notícia registrada: '%s' de %s (ID: %d)",
                    title[:60], source, row_id,
                )
            return row_id if row_id else None
        except Exception as exc:
            logger.debug("Notícia duplicada ou erro: %s", exc)
            return None

    def get_trades_today(self) -> list[dict[str, Any]]:
        """
        Retorna todos os trades realizados hoje (BRT).

        Returns:
            Lista de trades como dicionários.
        """
        today = _today_brt_iso()
        return self._fetch_all(
            "SELECT * FROM trades WHERE timestamp LIKE ? ORDER BY timestamp DESC",
            (f"{today}%",),
        )

    def get_trade_history(self, days: int = 30) -> list[dict[str, Any]]:
        """
        Retorna o histórico de trades dos últimos N dias.

        Args:
            days: Número de dias para consultar (padrão: 30).

        Returns:
            Lista de trades como dicionários, mais recentes primeiro.
        """
        cutoff = (datetime.now(tz=_BRT) - timedelta(days=days)).isoformat()
        return self._fetch_all(
            "SELECT * FROM trades WHERE timestamp >= ? ORDER BY timestamp DESC",
            (cutoff,),
        )

    def get_latest_sentiment(self, ticker: str) -> dict[str, Any] | None:
        """
        Retorna o score de sentimento mais recente de um ativo.

        Args:
            ticker: Código do ativo.

        Returns:
            Dicionário com os dados ou None se não encontrar.
        """
        return self._fetch_one(
            """SELECT * FROM sentiment_scores
               WHERE ticker = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (ticker.upper(),),
        )

    def get_sentiments_today(self, ticker: str | None = None) -> list[dict[str, Any]]:
        """
        Retorna scores de sentimento de hoje, opcionalmente filtrados por ticker.

        Args:
            ticker: Código do ativo (opcional). Se None, retorna todos.

        Returns:
            Lista de scores como dicionários.
        """
        today = _today_brt_iso()
        if ticker:
            return self._fetch_all(
                """SELECT * FROM sentiment_scores
                   WHERE ticker = ? AND timestamp LIKE ?
                   ORDER BY timestamp DESC""",
                (ticker.upper(), f"{today}%"),
            )
        return self._fetch_all(
            """SELECT * FROM sentiment_scores
               WHERE timestamp LIKE ?
               ORDER BY timestamp DESC""",
            (f"{today}%",),
        )

    def get_daily_report(self, report_date: str | date | None = None) -> dict[str, Any] | None:
        """
        Retorna o relatório diário para a data especificada.

        Args:
            report_date: Data do relatório. Se None, usa hoje em BRT.

        Returns:
            Dicionário com o relatório ou None se não encontrar.
        """
        if report_date is None:
            dt_str = _today_brt_iso()
        elif isinstance(report_date, date):
            dt_str = report_date.isoformat()
        else:
            dt_str = report_date

        return self._fetch_one(
            "SELECT * FROM daily_reports WHERE date = ?",
            (dt_str,),
        )

    def get_report_history(self, days: int = 30) -> list[dict[str, Any]]:
        """
        Retorna os relatórios diários dos últimos N dias.

        Args:
            days: Número de dias para consultar (padrão: 30).

        Returns:
            Lista de relatórios como dicionários, mais recentes primeiro.
        """
        cutoff = (datetime.now(tz=_BRT) - timedelta(days=days)).date().isoformat()
        return self._fetch_all(
            "SELECT * FROM daily_reports WHERE date >= ? ORDER BY date DESC",
            (cutoff,),
        )

    def get_decisions_today(self) -> list[dict[str, Any]]:
        """
        Retorna todas as decisões da IA tomadas hoje (BRT).

        Returns:
            Lista de decisões como dicionários.
        """
        today = _today_brt_iso()
        return self._fetch_all(
            "SELECT * FROM ai_decisions WHERE timestamp LIKE ? ORDER BY timestamp DESC",
            (f"{today}%",),
        )

    def get_latest_decision(self, ticker: str) -> dict[str, Any] | None:
        """
        Retorna a decisão mais recente da IA para um ativo.

        Args:
            ticker: Código do ativo.

        Returns:
            Dicionário com a decisão ou None se não encontrar.
        """
        return self._fetch_one(
            """SELECT * FROM ai_decisions
               WHERE ticker = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (ticker.upper(),),
        )

    def get_latest_snapshot(self, ticker: str) -> dict[str, Any] | None:
        """
        Retorna o snapshot de mercado mais recente de um ativo.

        Args:
            ticker: Código do ativo.

        Returns:
            Dicionário com o snapshot ou None se não encontrar.
        """
        return self._fetch_one(
            """SELECT * FROM market_snapshots
               WHERE ticker = ?
               ORDER BY timestamp DESC LIMIT 1""",
            (ticker.upper(),),
        )

    def get_latest_health(self) -> dict[str, Any] | None:
        """
        Retorna a métrica de saúde mais recente do sistema.

        Returns:
            Dicionário com as métricas ou None se não houver dados.
        """
        return self._fetch_one(
            "SELECT * FROM system_health ORDER BY timestamp DESC LIMIT 1",
        )

    def get_recent_news(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        Retorna as notícias mais recentes do banco.

        Args:
            limit: Número máximo de notícias a retornar.

        Returns:
            Lista de notícias como dicionários.
        """
        return self._fetch_all(
            "SELECT * FROM news_items ORDER BY published_at DESC LIMIT ?",
            (limit,),
        )

    def get_news_by_ticker(self, ticker: str, limit: int = 20) -> list[dict[str, Any]]:
        """
        Retorna notícias de um ticker específico.

        Args:
            ticker: Código do ativo.
            limit: Número máximo de resultados.

        Returns:
            Lista de notícias filtradas.
        """
        return self._fetch_all(
            """SELECT * FROM news_items
               WHERE ticker = ?
               ORDER BY published_at DESC LIMIT ?""",
            (ticker.upper(), limit),
        )

    def get_decisions_history(
        self, ticker: str | None = None, days: int = 30, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Retorna histórico de decisões da IA, opcionalmente por ticker.

        Args:
            ticker: Código do ativo (opcional — se None, retorna todos).
            days: Número de dias para consultar.
            limit: Número máximo de resultados.

        Returns:
            Lista de decisões como dicionários.
        """
        cutoff = (datetime.now(tz=_BRT) - timedelta(days=days)).isoformat()
        if ticker:
            return self._fetch_all(
                """SELECT * FROM ai_decisions
                   WHERE ticker = ? AND timestamp >= ?
                   ORDER BY timestamp DESC LIMIT ?""",
                (ticker.upper(), cutoff, limit),
            )
        return self._fetch_all(
            """SELECT * FROM ai_decisions
               WHERE timestamp >= ?
               ORDER BY timestamp DESC LIMIT ?""",
            (cutoff, limit),
        )

    def get_performance_metrics(self) -> dict[str, Any]:
        """
        Calcula métricas de desempenho do bot a partir do histórico de trades.

        Returns:
            Dicionário com win_rate, total_trades, total_buys, total_sells,
            avg_trade_value, recent_pnl.
        """
        trades = self._fetch_all(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 500",
        )
        reports = self._fetch_all(
            "SELECT * FROM daily_reports ORDER BY date DESC LIMIT 30",
        )

        total_trades = len(trades)
        total_buys = sum(1 for t in trades if t.get('action') == 'BUY')
        total_sells = sum(1 for t in trades if t.get('action') == 'SELL')

        pnl_values = [r.get('pnl_percent', 0.0) for r in reports if r.get('pnl_percent')]
        positive_days = sum(1 for p in pnl_values if p > 0)
        negative_days = sum(1 for p in pnl_values if p < 0)
        win_rate = (positive_days / len(pnl_values) * 100) if pnl_values else 0.0
        avg_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0

        max_drawdown = min(pnl_values) if pnl_values else 0.0

        return {
            'total_trades': total_trades,
            'total_buys': total_buys,
            'total_sells': total_sells,
            'days_tracked': len(pnl_values),
            'positive_days': positive_days,
            'negative_days': negative_days,
            'win_rate': round(win_rate, 1),
            'avg_daily_pnl': round(avg_pnl, 2),
            'max_drawdown': round(max_drawdown, 2),
        }

    def count_trades_today(self) -> dict[str, int]:
        """
        Conta compras e vendas realizadas hoje.

        Returns:
            Dicionário com chaves 'buys' e 'sells'.
        """
        today = _today_brt_iso()
        result = {"buys": 0, "sells": 0}

        rows = self._fetch_all(
            """SELECT action, COUNT(*) as cnt
               FROM trades
               WHERE timestamp LIKE ?
               GROUP BY action""",
            (f"{today}%",),
        )
        for row in rows:
            if row["action"] == "BUY":
                result["buys"] = row["cnt"]
            elif row["action"] == "SELL":
                result["sells"] = row["cnt"]

        return result

    def reset_all_data(self) -> None:
        """
        Limpa todos os dados de todas as tabelas para reset completo do sistema.
        """
        with self._lock:
            with self._get_connection() as conn:
                tables = [
                    "trades", "sentiment_scores", "market_snapshots",
                    "daily_reports", "ai_decisions", "telegram_logs",
                    "system_health", "news_items"
                ]
                for t in tables:
                    conn.execute(f"DELETE FROM {t};")
            logger.info("Todas as tabelas do banco de dados foram resetadas com sucesso.")

    def close(self) -> None:
        """
        Encerra o gerenciador de banco de dados.

        Atualmente é um no-op já que conexões são fechadas via context manager,
        mas mantido para compatibilidade futura com pools de conexão.
        """
        logger.info("DatabaseManager encerrado para: %s", self.db_path)

    def __repr__(self) -> str:
        return f"DatabaseManager(db_path={self.db_path})"

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username.strip(),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def create_user(self, username: str, password: str, must_change_password: bool = False) -> int:
        from auth import PasswordManager
        p_hash, p_salt = PasswordManager.hash_password(password)
        now_str = _now_brt_iso()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO users (username, password_hash, salt, must_change_password, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (username.strip(), p_hash, p_salt, 1 if must_change_password else 0, now_str, now_str)
            )
            conn.commit()
            return cursor.lastrowid

    def update_user_credentials(self, user_id: int, new_username: str, new_password: str) -> bool:
        from auth import PasswordManager
        p_hash, p_salt = PasswordManager.hash_password(new_password)
        now_str = _now_brt_iso()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE users
                SET username = ?, password_hash = ?, salt = ?, must_change_password = 0, updated_at = ?
                WHERE id = ?
                """,
                (new_username.strip(), p_hash, p_salt, now_str, user_id)
            )
            conn.commit()
            return cursor.rowcount > 0

    def reset_user_password(self, username: str, new_password: str, force_first_login: bool = True) -> bool:
        from auth import PasswordManager
        p_hash, p_salt = PasswordManager.hash_password(new_password)
        now_str = _now_brt_iso()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE users
                SET password_hash = ?, salt = ?, must_change_password = ?, failed_login_attempts = 0, locked_until = NULL, updated_at = ?
                WHERE username = ?
                """,
                (p_hash, p_salt, 1 if force_first_login else 0, now_str, username.strip())
            )
            conn.commit()
            return cursor.rowcount > 0

    def create_session(self, user_id: int, ip_address: str = "", user_agent: str = "", max_session_seconds: int = 28800) -> str:
        import secrets
        token = secrets.token_urlsafe(32)
        now = datetime.now(tz=_BRT)
        now_str = now.isoformat()
        expires_str = (now + timedelta(seconds=max_session_seconds)).isoformat()
        
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO sessions (session_token, user_id, ip_address, user_agent, created_at, last_active_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (token, user_id, ip_address, user_agent, now_str, now_str, expires_str)
            )
            conn.commit()
            return token

    def get_valid_session(self, token: str, max_idle_seconds: int = 900) -> dict[str, Any] | None:
        now = datetime.now(tz=_BRT)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sessions WHERE session_token = ?", (token,))
            row = cursor.fetchone()
            if not row:
                return None
            sess = dict(row)

            expires_at = datetime.fromisoformat(sess["expires_at"])
            if now > expires_at:
                self.revoke_session(token)
                return None

            last_active = datetime.fromisoformat(sess["last_active_at"])
            if (now - last_active).total_seconds() > max_idle_seconds:
                self.revoke_session(token)
                return None

            return sess

    def touch_session(self, token: str) -> None:
        now_str = _now_brt_iso()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE sessions SET last_active_at = ? WHERE session_token = ?", (now_str, token))
            conn.commit()

    def revoke_session(self, token: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE session_token = ?", (token,))
            conn.commit()
            return cursor.rowcount > 0

    def revoke_all_user_sessions(self, user_id: int) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
