import os
import threading
from pathlib import Path

from data.database import DatabaseManager


def test_database_creation_and_indexes(tmp_path: Path):
    db_file = tmp_path / "creation.db"
    db = DatabaseManager(str(db_file))
    assert os.path.exists(db_file)

    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        assert "trades" in tables
        assert "sentiment_scores" in tables
        assert "ai_decisions" in tables
        assert "capital_transactions" in tables
        assert "users" in tables

        cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
        indexes = {row[0] for row in cur.fetchall()}
        assert "idx_trades_ticker" in indexes
        assert "idx_trades_timestamp" in indexes
        assert "idx_ai_decisions_ts" in indexes


def test_database_concurrent_writes(tmp_path: Path):
    db_file = tmp_path / "concurrent.db"
    db = DatabaseManager(str(db_file))
    errors = []

    def worker(w_id: int):
        try:
            for i in range(15):
                db.insert_trade(
                    ticker=f"TICK{w_id}",
                    action="BUY" if i % 2 == 0 else "SELL",
                    quantity=10,
                    price=25.50 + i,
                    total_value=(25.50 + i) * 10,
                    reasoning=f"Worker {w_id} trade {i}",
                )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    trades = db.get_trade_history(days=30)
    assert len(trades) == 75


def test_database_user_management_and_no_default_respawn(tmp_path: Path):
    db_file = tmp_path / "users.db"
    db = DatabaseManager(str(db_file))
    user = db.get_user_by_username("Admin")
    assert user is not None
    assert user["must_change_password"] == 1

    # Após renomear Admin no primeiro acesso, nova instância de DatabaseManager NÃO deve recriar Admin
    assert db.update_user_credentials(user["id"], "TraderPro", "SenhaForte@2026") is True
    import data.database as db_mod
    db_mod._INITIALIZED_PATHS.discard(db_file.resolve())

    db2 = DatabaseManager(str(db_file))
    assert db2.get_user_by_username("Admin") is None
    assert db2.get_user_by_username("Luke Arwolf") is None
    u2 = db2.get_user_by_username("TraderPro")
    assert u2 is not None
    assert u2["must_change_password"] == 0


def test_ai_decisions_strict_environment_isolation(tmp_path: Path):
    db_file = tmp_path / "decisions.db"
    db = DatabaseManager(str(db_file))

    db.insert_decision(
        ticker="PETR4",
        action="BUY",
        confidence=0.85,
        reasoning="Simulação buy",
        environment="homologation",
        is_simulated=True,
    )
    db.insert_decision(
        ticker="VALE3",
        action="SELL",
        confidence=0.92,
        reasoning="Produção sell",
        environment="production",
        is_simulated=False,
    )

    sim_decisions = db.get_recent_decisions(environment="homologation")
    prod_decisions = db.get_recent_decisions(environment="production")
    all_decisions = db.get_recent_decisions()

    assert len(sim_decisions) == 1
    assert sim_decisions[0]["ticker"] == "PETR4"
    assert sim_decisions[0]["environment"] == "homologation"
    assert sim_decisions[0]["is_simulated"] == 1

    assert len(prod_decisions) == 1
    assert prod_decisions[0]["ticker"] == "VALE3"
    assert prod_decisions[0]["environment"] == "production"
    assert prod_decisions[0]["is_simulated"] == 0

    assert len(all_decisions) == 2


def test_capital_transactions_and_reset_strict_environment_isolation(tmp_path: Path):
    db_file = tmp_path / "txs.db"
    db = DatabaseManager(str(db_file))

    db.record_capital_transaction(
        tx_type="APORTE",
        amount=5000.0,
        resulting_ai_capital=5000.0,
        resulting_vault=0.0,
        details="Aporte virtual de teste",
        environment="homologation",
        is_simulated=True,
    )

    db.record_capital_transaction(
        tx_type="RESGATE_IMEDIATO",
        amount=10.0,
        resulting_ai_capital=10.0,
        resulting_vault=10.0,
        details="Resgate real para cofre",
        environment="production",
        is_simulated=False,
    )

    gid = db.create_withdrawal_goal(
        amount=25.0, mode="on_profit", target_equity=125.0, base_equity=100.0, notes="Meta real"
    )

    # Resetar homologação jamais deve apagar dados ou metas de produção!
    db.reset_operational_data(environment="homologation")

    sim_txs = db.get_capital_transactions(environment="homologation")
    prod_txs = db.get_capital_transactions(environment="production")

    assert len(sim_txs) == 0
    assert len(prod_txs) == 1
    assert prod_txs[0]["tx_type"] == "RESGATE_IMEDIATO"
    assert db.get_active_withdrawal_goal()["id"] == gid
