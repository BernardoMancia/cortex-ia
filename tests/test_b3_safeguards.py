from __future__ import annotations

from datetime import date, datetime

from config.settings import settings
from domain.b3_rules import (
    BRT,
    add_b3_business_days,
    is_b3_pf_20k_exempt_eligible_stock,
    is_b3_unit_lot_asset,
    split_b3_order_legs,
)
from domain.b3_tax_guard import B3TaxGuard
from domain.models import Position
from domain.operational_guard import OperationalGuard, resolve_panic_until
from quant.risk_manager import WalletRiskManager
from storage.capital_repo import CapitalRepository
from storage.connection import DatabaseConnection
from storage.state_repo import StateRepository


def test_b3_order_legs_units_etfs_and_stocks():
    assert split_b3_order_legs("PETR4", 237) == [("PETR4", 200), ("PETR4F", 37)]
    assert split_b3_order_legs("VALE3", 45) == [("VALE3F", 45)]
    assert split_b3_order_legs("TAEE11", 125) == [("TAEE11", 100), ("TAEE11F", 25)]
    assert is_b3_unit_lot_asset("BOVA11") is True
    assert split_b3_order_legs("BOVA11", 15) == [("BOVA11", 15)]
    assert is_b3_pf_20k_exempt_eligible_stock("WEGE3") is True
    assert is_b3_pf_20k_exempt_eligible_stock("TAEE11") is True
    assert is_b3_pf_20k_exempt_eligible_stock("BOVA11") is False


def test_b3_d2_settlement_and_multilateral_netting(tmp_path):
    friday = date(2026, 11, 13)
    settled_tue = add_b3_business_days(friday, 2)
    assert settled_tue == date(2026, 11, 17)

    db = DatabaseConnection(tmp_path / "d2.db")
    cap_repo = CapitalRepository(db)
    cap_repo.record_d2_net_activity(1, 1, "SELL", 15000.0, fee=4.5, trade_date_str="2026-11-13")
    cap_repo.record_d2_net_activity(1, 1, "BUY", 5000.0, fee=1.5, trade_date_str="2026-11-13")
    summary_same_day = cap_repo.get_d2_summary(1, 1, ref_date_str="2026-11-13")
    assert summary_same_day["pending_d2_receivable"] == 9994.0

    summary_after_d2 = cap_repo.get_d2_summary(1, 1, ref_date_str="2026-11-17")
    assert summary_after_d2["pending_d2_receivable"] == 0.0


def test_b3_20k_tax_guard_defers_discretionary_but_allows_stop_loss():
    guard = B3TaxGuard(exemption_limit=20000.0, guard_buffer=19500.0)
    defer, reason = guard.should_defer_discretionary_profit_sell(
        ticker="ITUB4",
        projected_sale_gross=2500.0,
        current_month_eligible_sales=18000.0,
        protect_enabled=True,
        is_stop_loss_or_emergency=False,
    )
    assert defer is True
    assert "20,000.00" in reason or "isencao" in reason.lower() or "isenção" in reason.lower()

    defer_sl, _ = guard.should_defer_discretionary_profit_sell(
        ticker="ITUB4",
        projected_sale_gross=2500.0,
        current_month_eligible_sales=18000.0,
        protect_enabled=True,
        is_stop_loss_or_emergency=True,
    )
    assert defer_sl is False


def test_boot_maintenance_and_timed_panic_guard(tmp_path):
    db = DatabaseConnection(tmp_path / "state.db")
    state_repo = StateRepository(db)
    boot_state = state_repo.boot_initialize()
    if settings.FORCE_MAINTENANCE_ON_BOOT:
        assert boot_state["global_mode"] == "MAINTENANCE"
        gate = OperationalGuard.evaluate_global(boot_state)
        assert gate.can_execute_orders is False
        assert gate.can_observe_signals is True
        unlocked = state_repo.unlock_maintenance("Luke", 1, "127.0.0.1")
        assert unlocked["global_mode"] == "ACTIVE"

    ref = datetime(2026, 11, 13, 14, 0, 0, tzinfo=BRT)
    until_iso = resolve_panic_until("30m", now=ref)
    assert until_iso is not None
    p_state = state_repo.set_global_panic(until_iso, "Teste Panico 30m", "Luke", 1)
    assert OperationalGuard.evaluate_global(p_state, now=ref).can_execute_orders is False
    after_exp = datetime(2026, 11, 13, 14, 31, 0, tzinfo=BRT)
    assert OperationalGuard.evaluate_global(p_state, now=after_exp).can_execute_orders is True


def test_do_not_touch_position_shield():
    rm = WalletRiskManager()
    protected_pos = Position(
        ticker="VALE3",
        quantity=100,
        entry_price=60.0,
        current_price=50.0,
        stop_loss=55.0,
        do_not_touch=True,
    )
    act, qty, _, _, reason = rm.evaluate_open_position_exit(
        pos=protected_pos,
        current_price=50.0,
        wallet_row={"stop_loss_pct": 0.10, "trailing_trigger_pct": 0.02, "trailing_distance_pct": 0.015},
    )
    assert act is None
    assert qty == 0
    assert "do_not_touch" in reason
