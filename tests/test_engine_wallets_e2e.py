from __future__ import annotations

import numpy as np
import pandas as pd

from config.settings import settings
from domain.b3_rules import get_brt_now
from domain.models import (
    MarketCycleSnapshot,
    MarketQuote,
    OrderSide,
    OrderStatus,
    Position,
    SentimentVector,
    TechnicalVector,
    TrendSignal,
)
from engine.panic_service import PanicService
from engine.wallet_cycle import WalletCycleRunner
from observability.auto_backup import EncryptedBackupService
from storage.connection import DatabaseConnection
from storage.state_repo import StateRepository
from storage.trading_repo import TradingRepository
from storage.users_repo import UsersRepository
from storage.wallets_repo import WalletsRepository


def _synthetic_snapshot() -> MarketCycleSnapshot:
    now = get_brt_now()
    q = MarketQuote(
        ticker="WEGE3",
        price=42.0,
        bid=41.98,
        ask=42.02,
        spread_pct=0.0009,
        volume=5000000.0,
        change_pct=1.8,
        quote_ts=now.timestamp(),
        source="synthetic",
        is_stale=False,
    )
    tv = TechnicalVector(
        ticker="WEGE3",
        signal=TrendSignal.STRONG_BUY,
        ema_9=41.5,
        ema_21=40.8,
        ema_50=39.5,
        rsi=58.0,
        macd_hist=0.35,
        atr=0.8,
        adx=28.0,
        rel_vol=1.4,
        bb_lower=39.0,
        bb_upper=44.0,
        support=39.5,
        resistance=44.5,
        mtf_bullish=True,
        confidence=0.85,
        reasoning="Tendencia de alta alinhada.",
    )
    sv = SentimentVector(ticker="WEGE3", score=0.45, label="POSITIVO", confidence=0.8, news_count=2)
    return MarketCycleSnapshot(
        cycle_id="test_cyc_001",
        generated_at=now,
        market_open=True,
        opening_cooldown_active=False,
        bova11_change_pct=0.6,
        regime_bearish_veto=False,
        quotes={"WEGE3": q},
        technicals={"WEGE3": tv},
        sentiments={"WEGE3": sv, "MARKET": sv},
        headlines=[],
    )


def test_encrypted_backup_snapshot_verification(tmp_path):
    db = DatabaseConnection(tmp_path / "bkp_src.db")
    assert db.db_path.exists()
    bkp_svc = EncryptedBackupService(db_path=db.db_path, backups_dir=tmp_path / "bkps")
    enc_file = bkp_svc.create_verified_encrypted_snapshot(tag="pytest", keep_last=5)
    assert enc_file is not None
    assert enc_file.exists()
    raw = enc_file.read_bytes()
    assert raw.startswith(b"CXBK1")


def test_panic_service_timed_and_indefinite_and_wallet_bulkhead(tmp_path):
    db = DatabaseConnection(tmp_path / "panic_e2e.db")
    panic_svc = PanicService(db=db)
    wallets_repo = WalletsRepository(db)
    state_repo = StateRepository(db)

    res = panic_svc.activate_wallet_panic(
        user_id=1,
        wallet_id=1,
        actor_username="Luke",
        duration_preset="15m",
        reason="Teste parada 15m",
        liquidate_positions=False,
    )
    assert res is not None
    w = wallets_repo.get_owned_wallet(1, 1)
    assert w is not None
    assert w["operational_mode"] == "PANIC"
    assert w["panic_until"] is not None

    panic_svc.clear_wallet_panic(1, 1, "Luke", restore_mode="OBSERVE_ONLY")
    w_obs = wallets_repo.get_owned_wallet(1, 1)
    assert w_obs is not None
    assert w_obs["operational_mode"] == "OBSERVE_ONLY"

    state_repo.unlock_maintenance("Luke", 1)
    runner = WalletCycleRunner(db=db, panic_service=panic_svc)
    snap = _synthetic_snapshot()
    cycle_res = runner.run_wallet_cycle(w_obs, snap, state_repo.get_state())
    assert cycle_res is not None
    assert TradingRepository(db).list_positions(1, 1) == []

    users_repo = UsersRepository(db)
    u2 = users_repo.create_standard_user("Investidor2", "Senha#Forte2026!")
    assert u2["id"] == 2
    ordered = wallets_repo.list_all_executable_wallets_ordered()
    assert ordered[0]["user_id"] == 1


def test_environment_specific_execution_and_backtest(tmp_path):
    if settings.IS_PRODUCTION:
        from brokers.registry import build_wallet_connector, list_available_connectors

        catalog = list_available_connectors()
        types = {c["connector_type"]: c["status"] for c in catalog}
        assert types.get("clear_mt5") == "ready"
        assert types.get("xp_mt5") == "work_in_progress"
        wip_conn = build_wallet_connector({"id": 9, "user_id": 2, "connector_type": "xp_mt5"}, {})
        wip_order = wip_conn.place_order("PETR4", OrderSide.BUY, 100, expected_price=36.0)
        assert wip_order.status == OrderStatus.REJECTED
    else:
        from backtest.runner import BacktestRunner
        from brokers.connectors.paper_b3 import PaperB3Connector

        paper = PaperB3Connector({"id": 1, "user_id": 1, "free_cash": 100000.0, "vault_reserve_cash": 0.0})
        fill = paper.place_order("PETR4", OrderSide.BUY, 135, expected_price=36.0)
        assert fill.status == OrderStatus.FILLED
        assert fill.quantity == 135
        assert len(fill.legs) == 2

        dates = pd.date_range("2026-01-02", periods=90, freq="B")
        base_px = np.linspace(30.0, 38.0, len(dates))
        df = pd.DataFrame(
            {
                "Open": base_px * 0.995,
                "High": base_px * 1.01,
                "Low": base_px * 0.99,
                "Close": base_px,
                "Volume": np.full(len(dates), 2_000_000.0),
            },
            index=dates,
        )
        bt = BacktestRunner(initial_capital=50000.0).run_ticker_backtest("WEGE3", df_daily=df)
        assert bt["status"] == "completed"
        assert bt["initial_capital"] == 50000.0
