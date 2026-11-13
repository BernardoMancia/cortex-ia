from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta
from typing import Any

from config.b3_universe import (
    ALL_B3_TICKERS,
    B3_EQUITY_UNITS,
    B3_ETF_FII_TICKERS,
    B3_PORTFOLIOS,
    SECTOR_MAP,
    TICKER_ALIASES,
)
from config.settings import settings
from domain.b3_rules import (
    BRT,
    add_b3_business_days,
    get_ash_wednesday,
    get_brt_now,
    is_b3_business_day,
    is_b3_pf_20k_exempt_eligible_stock,
    is_b3_unit_lot_asset,
    normalize_b3_ticker,
)
from domain.b3_tax_guard import B3TaxGuard
from storage.connection import DatabaseConnection
from storage.market_hub_repo import MarketHubRepository
from storage.trading_repo import TradingRepository
from storage.wallets_repo import WalletsRepository

from bi.metrics import (
    compute_curve_risk_metrics,
    compute_decisions_summary,
    compute_monthly_returns,
    compute_pnl_by_ticker_and_sector,
    compute_trade_metrics,
    enrich_equity_series,
)

_MONTHS_PT: dict[int, str] = {
    1: "Janeiro",
    2: "Fevereiro",
    3: "Março",
    4: "Abril",
    5: "Maio",
    6: "Junho",
    7: "Julho",
    8: "Agosto",
    9: "Setembro",
    10: "Outubro",
    11: "Novembro",
    12: "Dezembro",
}

_WEEKDAYS_PT: dict[int, str] = {
    1: "Segunda-feira",
    2: "Terça-feira",
    3: "Quarta-feira",
    4: "Quinta-feira",
    5: "Sexta-feira",
    6: "Sábado",
    7: "Domingo",
}


def _resolve_owned_wallet_ids(
    db: DatabaseConnection,
    user_id: int,
    wallet_ids: list[int] | set[int] | tuple[int, ...] | None,
) -> list[int]:
    owned_rows = WalletsRepository(db).list_for_user(int(user_id))
    owned_ids = [int(w["id"]) for w in owned_rows]
    if wallet_ids is None:
        return owned_ids
    requested = {int(wid) for wid in wallet_ids}
    return [wid for wid in owned_ids if wid in requested]


def _extract_quote_price(live_quotes: dict[str, Any] | None, ticker: str) -> float | None:
    if not live_quotes:
        return None
    norm = normalize_b3_ticker(ticker)
    q = live_quotes.get(ticker) or live_quotes.get(norm)
    if q is None:
        return None
    if isinstance(q, dict):
        px = float(q.get("price") or 0.0)
    else:
        px = float(getattr(q, "price", 0.0) or 0.0)
    return px if px > 0.0 else None


def _compute_readonly_wallet_snapshot(
    db: DatabaseConnection,
    user_id: int,
    wallet_row: dict[str, Any],
    live_quotes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    uid = int(user_id)
    wid = int(wallet_row["id"])
    trading_repo = TradingRepository(db)
    raw_positions = trading_repo.list_positions(uid, wid)
    today = get_brt_now().date()

    pos_dicts: list[dict[str, Any]] = []
    invested_value = 0.0
    unrealized_pnl = 0.0

    for pos in raw_positions:
        tk = normalize_b3_ticker(pos.ticker)
        live_px = _extract_quote_price(live_quotes, tk)
        cur_px = round(live_px if live_px is not None else float(pos.current_price), 4)
        entry_px = round(float(pos.entry_price), 4)
        high_px = round(max(float(pos.highest_price), cur_px), 4)
        sl_px = round(float(pos.stop_loss), 4)
        qty = int(pos.quantity)
        mkt_val = round(qty * cur_px, 2)
        cost_val = round(qty * entry_px, 2)
        unr = round((cur_px - entry_px) * qty, 2)
        pnl_pct = round(((cur_px - entry_px) / entry_px) * 100.0, 2) if entry_px > 0.0 else 0.0
        stop_dist_pct = (
            round(((cur_px - sl_px) / cur_px) * 100.0, 2)
            if cur_px > 0.0 and sl_px > 0.0
            else 0.0
        )
        holding_days = 0
        opened_str = str(pos.opened_at or "")[:10]
        if len(opened_str) == 10:
            try:
                holding_days = max(0, (today - date.fromisoformat(opened_str)).days)
            except ValueError:
                holding_days = 0

        invested_value += mkt_val
        unrealized_pnl += unr
        pos_dicts.append(
            {
                "wallet_id": wid,
                "ticker": tk,
                "sector": SECTOR_MAP.get(tk, "Outros"),
                "quantity": qty,
                "entry_price": round(entry_px, 2),
                "fiscal_avg_price": round(float(pos.fiscal_avg_price), 4),
                "current_price": round(cur_px, 2),
                "stop_loss": round(sl_px, 2),
                "highest_price": round(high_px, 2),
                "market_value": mkt_val,
                "cost_basis": cost_val,
                "weight_pct": 0.0,
                "unrealized_pnl": unr,
                "pnl_percent": pnl_pct,
                "stop_distance_pct": stop_dist_pct,
                "do_not_touch": bool(pos.do_not_touch),
                "partial_tp_taken": bool(pos.partial_tp_taken),
                "holding_days": holding_days,
                "opened_at": str(pos.opened_at or ""),
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )

    invested_value = round(invested_value, 2)
    unrealized_pnl = round(unrealized_pnl, 2)
    free_cash = round(float(wallet_row.get("free_cash") or 0.0), 2)
    vault_reserve_cash = round(float(wallet_row.get("vault_reserve_cash") or 0.0), 2)
    initial_capital = round(float(wallet_row.get("initial_capital") or 0.0), 2)
    ai_allocated_capital = round(float(wallet_row.get("ai_allocated_capital") or 0.0), 2)

    if initial_capital <= 0.0 and (free_cash + invested_value) > 0.0:
        initial_capital = round(free_cash + invested_value, 2)

    if ai_allocated_capital > 0.0:
        buying_power = round(max(0.0, min(free_cash, ai_allocated_capital - invested_value)), 2)
    elif settings.IS_SIMULATION:
        buying_power = round(max(0.0, free_cash), 2)
    else:
        buying_power = 0.0

    operational_equity = round(free_cash + invested_value, 2)
    total_equity = round(free_cash + vault_reserve_cash + invested_value, 2)

    for p in pos_dicts:
        p["weight_pct"] = (
            round((float(p["market_value"]) / total_equity) * 100.0, 2)
            if total_equity > 0.0
            else 0.0
        )

    pnl_percent = (
        round(((total_equity - initial_capital) / initial_capital) * 100.0, 2)
        if initial_capital > 0.0
        else 0.0
    )
    day_base = trading_repo.get_day_baseline_operational_equity(uid, wid)
    if day_base is not None and day_base > 0.0:
        daily_pnl_percent = round(((operational_equity - day_base) / day_base) * 100.0, 2)
    elif initial_capital > 0.0:
        daily_pnl_percent = round(((operational_equity - initial_capital) / initial_capital) * 100.0, 2)
    else:
        daily_pnl_percent = 0.0

    return {
        "wallet_id": wid,
        "initial_capital": initial_capital,
        "free_cash": free_cash,
        "vault_reserve_cash": vault_reserve_cash,
        "ai_allocated_capital": ai_allocated_capital,
        "allocated_capital": invested_value,
        "buying_power": buying_power,
        "operational_equity": operational_equity,
        "total_equity": total_equity,
        "unrealized_pnl": unrealized_pnl,
        "pnl_percent": pnl_percent,
        "daily_pnl_percent": daily_pnl_percent,
        "positions": pos_dicts,
    }


def _synthesize_single_equity_point(snap: dict[str, Any]) -> dict[str, Any]:
    now = get_brt_now()
    tot_eq = round(float(snap["total_equity"]), 2)
    init_cap = round(float(snap["initial_capital"]), 2)
    hwm = round(max(tot_eq, init_cap), 2)
    dd = round(min(0.0, ((tot_eq - hwm) / hwm) * 100.0), 4) if hwm > 0.0 else 0.0
    if abs(dd) < 1e-9:
        dd = 0.0
    return {
        "id": 0,
        "wallet_id": int(snap["wallet_id"]),
        "date": now.date().isoformat(),
        "snapshot_date": now.date().isoformat(),
        "timestamp": now.isoformat(),
        "initial_capital": init_cap,
        "total_equity": tot_eq,
        "operational_equity": round(float(snap["operational_equity"]), 2),
        "free_cash": round(float(snap["free_cash"]), 2),
        "allocated_capital": round(float(snap["allocated_capital"]), 2),
        "vault_reserve_cash": round(float(snap["vault_reserve_cash"]), 2),
        "pnl_percent": round(float(snap["pnl_percent"]), 4),
        "daily_pnl_percent": round(float(snap["daily_pnl_percent"]), 4),
        "high_water_mark": hwm,
        "drawdown_pct": dd,
        "buys_count": 0,
        "sells_count": 0,
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
    }


def build_dim_wallets(
    db: DatabaseConnection,
    user_id: int,
    allowed_wallet_ids: list[int] | set[int] | tuple[int, ...] | None = None,
) -> list[dict[str, Any]]:
    wallets = WalletsRepository(db).list_for_user(int(user_id))
    allowed_set = {int(x) for x in allowed_wallet_ids} if allowed_wallet_ids is not None else None
    rows: list[dict[str, Any]] = []
    for w in wallets:
        wid = int(w["id"])
        if allowed_set is not None and wid not in allowed_set:
            continue
        rows.append(
            {
                "wallet_id": wid,
                "user_id": int(w["user_id"]),
                "name": str(w.get("name") or ""),
                "connector_type": str(w.get("connector_type") or ""),
                "is_default": bool(w.get("is_default", 0)),
                "automation_enabled": bool(w.get("automation_enabled", 0)),
                "operational_mode": str(w.get("operational_mode") or "ACTIVE"),
                "initial_capital": round(float(w.get("initial_capital") or 0.0), 2),
                "free_cash": round(float(w.get("free_cash") or 0.0), 2),
                "ai_allocated_capital": round(float(w.get("ai_allocated_capital") or 0.0), 2),
                "vault_reserve_cash": round(float(w.get("vault_reserve_cash") or 0.0), 2),
                "stop_loss_pct": round(float(w.get("stop_loss_pct") or 0.0), 4),
                "trailing_trigger_pct": round(float(w.get("trailing_trigger_pct") or 0.0), 4),
                "trailing_distance_pct": round(float(w.get("trailing_distance_pct") or 0.0), 4),
                "protect_20k_exemption": bool(w.get("protect_20k_exemption", 1)),
                "avoid_day_trade_tax": bool(w.get("avoid_day_trade_tax", 1)),
                "created_at": str(w.get("created_at") or ""),
                "updated_at": str(w.get("updated_at") or ""),
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )
    return rows


def build_dim_assets_b3() -> list[dict[str, Any]]:
    ibov_set = set(B3_PORTFOLIOS.get("IBOV", []))
    idiv_set = set(B3_PORTFOLIOS.get("IDIV", []))
    smll_set = set(B3_PORTFOLIOS.get("SMLL", []))
    bdrs_set = set(B3_PORTFOLIOS.get("BDRS", []))
    etfs_set = set(B3_PORTFOLIOS.get("ETFS", []))

    rows: list[dict[str, Any]] = []
    for tk in ALL_B3_TICKERS:
        aliases = TICKER_ALIASES.get(tk)
        company_name = str(aliases[-1]) if aliases else tk

        if tk in ibov_set:
            primary_index = "IBOV"
        elif tk in idiv_set:
            primary_index = "IDIV"
        elif tk in smll_set:
            primary_index = "SMLL"
        elif tk in bdrs_set:
            primary_index = "BDRS"
        elif tk in etfs_set:
            primary_index = "ETFS"
        else:
            primary_index = "B3"

        if tk in B3_ETF_FII_TICKERS or tk in etfs_set:
            asset_class = "ETF_FII"
        elif tk in B3_EQUITY_UNITS:
            asset_class = "UNIT"
        elif tk in bdrs_set or any(tk.endswith(suf) for suf in ("31", "32", "33", "34", "35", "39")):
            asset_class = "BDR"
        else:
            asset_class = "ACAO"

        lot_size = 1 if is_b3_unit_lot_asset(tk) else 100
        exempt_20k = bool(is_b3_pf_20k_exempt_eligible_stock(tk))

        rows.append(
            {
                "ticker": tk,
                "company_name": company_name,
                "sector": SECTOR_MAP.get(tk, "Outros"),
                "primary_index": primary_index,
                "asset_class": asset_class,
                "standard_lot_size": lot_size,
                "swing_20k_exempt_eligible": exempt_20k,
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )
    return rows


def build_dim_calendar_b3(days: int = 730) -> list[dict[str, Any]]:
    total_days = max(30, min(int(days), 3650))
    today = get_brt_now().date()
    future_horizon = min(14, max(4, total_days // 20))
    start_date = today - timedelta(days=total_days - future_horizon - 1)

    rows: list[dict[str, Any]] = []
    ash_cache: dict[int, date] = {}

    for offset in range(total_days):
        curr = start_date + timedelta(days=offset)
        yr = curr.year
        if yr not in ash_cache:
            ash_cache[yr] = get_ash_wednesday(yr)
        dow = curr.isoweekday()
        bday = is_b3_business_day(curr)
        settle_d2 = add_b3_business_days(curr, 2)

        rows.append(
            {
                "date": curr.isoformat(),
                "year": yr,
                "quarter": ((curr.month - 1) // 3) + 1,
                "month": curr.month,
                "year_month": f"{yr:04d}-{curr.month:02d}",
                "month_name_pt": _MONTHS_PT[curr.month],
                "day_of_week_num": dow,
                "day_of_week_pt": _WEEKDAYS_PT[dow],
                "is_b3_business_day": bday,
                "is_ash_wednesday": curr == ash_cache[yr],
                "d2_settlement_date": settle_d2.isoformat(),
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )
    return rows


def build_fact_equity(
    db: DatabaseConnection,
    user_id: int,
    wallet_ids: list[int] | set[int] | tuple[int, ...] | None = None,
    days: int = 730,
) -> list[dict[str, Any]]:
    uid = int(user_id)
    owned_wids = _resolve_owned_wallet_ids(db, uid, wallet_ids)
    if not owned_wids:
        return []

    cutoff_date = (get_brt_now() - timedelta(days=max(1, int(days)))).date().isoformat()
    placeholders = ",".join("?" for _ in owned_wids)
    wallets_map = {
        int(w["id"]): w
        for w in WalletsRepository(db).list_for_user(uid)
        if int(w["id"]) in set(owned_wids)
    }

    with db.readonly() as conn:
        eq_rows = conn.execute(
            f"""
            SELECT * FROM equity_snapshots
            WHERE user_id = ? AND wallet_id IN ({placeholders}) AND snapshot_date >= ?
            ORDER BY wallet_id ASC, id ASC
            """,
            [uid, *owned_wids, cutoff_date],
        ).fetchall()
        tr_rows = conn.execute(
            f"""
            SELECT wallet_id, action, timestamp FROM trades
            WHERE user_id = ? AND wallet_id IN ({placeholders}) AND substr(timestamp, 1, 10) >= ?
            """,
            [uid, *owned_wids, cutoff_date],
        ).fetchall()

    eq_by_wallet: dict[int, list[dict[str, Any]]] = {}
    for r in eq_rows:
        d = dict(r)
        eq_by_wallet.setdefault(int(d["wallet_id"]), []).append(d)

    tr_by_wallet: dict[int, list[dict[str, Any]]] = {}
    for t in tr_rows:
        td = dict(t)
        tr_by_wallet.setdefault(int(td["wallet_id"]), []).append(td)

    out: list[dict[str, Any]] = []
    for wid in owned_wids:
        w_row = wallets_map.get(wid)
        init_cap = float(w_row.get("initial_capital") or 0.0) if w_row else 0.0
        raw_curve = eq_by_wallet.get(wid, [])
        if raw_curve:
            enriched = enrich_equity_series(
                raw_curve,
                trade_rows=tr_by_wallet.get(wid, []),
                initial_capital=init_cap,
            )
            for item in enriched:
                out.append(
                    {
                        "id": int(item.get("id") or 0),
                        "wallet_id": wid,
                        "date": str(item["date"]),
                        "snapshot_date": str(item["snapshot_date"]),
                        "timestamp": str(item["timestamp"]),
                        "initial_capital": float(item["initial_capital"]),
                        "total_equity": float(item["total_equity"]),
                        "operational_equity": float(item["operational_equity"]),
                        "free_cash": float(item["free_cash"]),
                        "allocated_capital": float(item["allocated_capital"]),
                        "vault_reserve_cash": float(item["vault_reserve_cash"]),
                        "pnl_percent": float(item["pnl_percent"]),
                        "daily_pnl_percent": float(item["daily_pnl_percent"]),
                        "high_water_mark": float(item["high_water_mark"]),
                        "drawdown_pct": float(item["drawdown_pct"]),
                        "buys_count": int(item["buys_count"]),
                        "sells_count": int(item["sells_count"]),
                        "environment": settings.ENVIRONMENT_ID,
                        "is_simulation": settings.IS_SIMULATION,
                    }
                )
        elif w_row is not None:
            snap = _compute_readonly_wallet_snapshot(db, uid, w_row)
            out.append(_synthesize_single_equity_point(snap))

    return out


def build_fact_positions(
    db: DatabaseConnection,
    user_id: int,
    wallet_ids: list[int] | set[int] | tuple[int, ...] | None = None,
    live_quotes: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    uid = int(user_id)
    owned_wids = _resolve_owned_wallet_ids(db, uid, wallet_ids)
    if not owned_wids:
        return []

    wallets_repo = WalletsRepository(db)
    rows: list[dict[str, Any]] = []
    for wid in owned_wids:
        w_row = wallets_repo.get_owned_wallet(uid, wid)
        if w_row is None:
            continue
        snap = _compute_readonly_wallet_snapshot(db, uid, w_row, live_quotes=live_quotes)
        rows.extend(snap["positions"])
    return rows


def build_fact_trades(
    db: DatabaseConnection,
    user_id: int,
    wallet_ids: list[int] | set[int] | tuple[int, ...] | None = None,
    days: int = 730,
) -> list[dict[str, Any]]:
    uid = int(user_id)
    owned_wids = _resolve_owned_wallet_ids(db, uid, wallet_ids)
    if not owned_wids:
        return []

    cutoff_date = (get_brt_now() - timedelta(days=max(1, int(days)))).date().isoformat()
    placeholders = ",".join("?" for _ in owned_wids)

    with db.readonly() as conn:
        raw_rows = conn.execute(
            f"""
            SELECT * FROM trades
            WHERE user_id = ? AND wallet_id IN ({placeholders}) AND substr(timestamp, 1, 10) >= ?
            ORDER BY timestamp DESC, id DESC
            """,
            [uid, *owned_wids, cutoff_date],
        ).fetchall()

    out: list[dict[str, Any]] = []
    for r in raw_rows:
        d = dict(r)
        tk = normalize_b3_ticker(str(d.get("ticker") or ""))
        act = str(d.get("action") or "").strip().upper()
        tot_val = round(float(d.get("total_value") or 0.0), 2)
        fee = round(float(d.get("fee") or 0.0), 4)
        net_val = round(tot_val - fee, 2) if act == "SELL" else round(tot_val + fee, 2)
        ts = str(d.get("timestamp") or "")
        out.append(
            {
                "id": int(d["id"]),
                "wallet_id": int(d["wallet_id"]),
                "date": ts[:10],
                "timestamp": ts,
                "ticker": tk,
                "sector": SECTOR_MAP.get(tk, "Outros"),
                "action": act,
                "quantity": int(d.get("quantity") or 0),
                "price": round(float(d.get("price") or 0.0), 4),
                "total_value": tot_val,
                "fee": fee,
                "net_value": net_val,
                "realized_pnl": round(float(d.get("realized_pnl") or 0.0), 2),
                "stop_loss": round(float(d.get("stop_loss") or 0.0), 4),
                "is_day_trade": bool(d.get("is_day_trade", 0)),
                "client_order_id": str(d.get("client_order_id") or ""),
                "reasoning": str(d.get("reasoning") or ""),
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )
    return out


def build_fact_decisions(
    db: DatabaseConnection,
    user_id: int,
    wallet_ids: list[int] | set[int] | tuple[int, ...] | None = None,
    days: int = 180,
) -> list[dict[str, Any]]:
    uid = int(user_id)
    owned_wids = _resolve_owned_wallet_ids(db, uid, wallet_ids)
    if not owned_wids:
        return []

    cutoff_date = (get_brt_now() - timedelta(days=max(1, int(days)))).date().isoformat()
    placeholders = ",".join("?" for _ in owned_wids)

    with db.readonly() as conn:
        raw_rows = conn.execute(
            f"""
            SELECT * FROM ai_decisions
            WHERE user_id = ? AND wallet_id IN ({placeholders}) AND substr(timestamp, 1, 10) >= ?
            ORDER BY timestamp DESC, id DESC
            """,
            [uid, *owned_wids, cutoff_date],
        ).fetchall()

    out: list[dict[str, Any]] = []
    for r in raw_rows:
        d = dict(r)
        raw_tk = str(d.get("ticker") or "MARKET").strip().upper()
        tk = "MARKET" if raw_tk == "MARKET" else (normalize_b3_ticker(raw_tk) or raw_tk)
        raw_conf = float(d.get("confidence") or 0.0)
        conf_pct = round(raw_conf * 100.0 if raw_conf <= 1.0 else raw_conf, 2)
        ts = str(d.get("timestamp") or "")
        out.append(
            {
                "id": int(d["id"]),
                "wallet_id": int(d["wallet_id"]),
                "date": ts[:10],
                "timestamp": ts,
                "ticker": tk,
                "sector": "Mercado" if tk == "MARKET" else SECTOR_MAP.get(tk, "Outros"),
                "action": str(d.get("action") or "HOLD").strip().upper(),
                "confidence": round(raw_conf, 4),
                "confidence_pct": conf_pct,
                "trend_signal": str(d.get("trend_signal") or "NEUTRAL"),
                "sentiment_score": round(float(d.get("sentiment_score") or 0.0), 4),
                "reasoning": str(d.get("reasoning") or ""),
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )
    return out


def build_fact_cashflow_d2(
    db: DatabaseConnection,
    user_id: int,
    wallet_ids: list[int] | set[int] | tuple[int, ...] | None = None,
    days: int = 365,
) -> dict[str, Any]:
    uid = int(user_id)
    owned_wids = _resolve_owned_wallet_ids(db, uid, wallet_ids)
    if not owned_wids:
        return {
            "rows": [],
            "d2_settlements": [],
            "capital_transactions": [],
            "withdrawal_goals": [],
            "tax_20k_summary": [],
        }

    today_str = get_brt_now().date().isoformat()
    cutoff_date = (get_brt_now() - timedelta(days=max(1, int(days)))).date().isoformat()
    placeholders = ",".join("?" for _ in owned_wids)

    with db.readonly() as conn:
        d2_raw = conn.execute(
            f"""
            SELECT * FROM d2_settlements
            WHERE user_id = ? AND wallet_id IN ({placeholders}) AND trade_date >= ?
            ORDER BY settlement_date ASC, id ASC
            """,
            [uid, *owned_wids, cutoff_date],
        ).fetchall()
        tx_raw = conn.execute(
            f"""
            SELECT * FROM capital_transactions
            WHERE user_id = ? AND wallet_id IN ({placeholders}) AND substr(timestamp, 1, 10) >= ?
            ORDER BY timestamp DESC, id DESC
            """,
            [uid, *owned_wids, cutoff_date],
        ).fetchall()
        goals_raw = conn.execute(
            f"""
            SELECT * FROM withdrawal_goals
            WHERE user_id = ? AND wallet_id IN ({placeholders}) AND substr(created_at, 1, 10) >= ?
            ORDER BY id DESC
            """,
            [uid, *owned_wids, cutoff_date],
        ).fetchall()

    d2_settlements: list[dict[str, Any]] = []
    unified_rows: list[dict[str, Any]] = []
    for r in d2_raw:
        d = dict(r)
        settle_date = str(d.get("settlement_date") or "")
        is_settled = bool(d.get("settled", 0)) or (bool(settle_date) and settle_date <= today_str)
        amt = round(float(d.get("net_sell_amount") or 0.0), 2)
        item = {
            "id": int(d["id"]),
            "wallet_id": int(d["wallet_id"]),
            "trade_date": str(d.get("trade_date") or ""),
            "settlement_date": settle_date,
            "net_sell_amount": amt,
            "settled": is_settled,
            "updated_at": str(d.get("updated_at") or ""),
            "environment": settings.ENVIRONMENT_ID,
            "is_simulation": settings.IS_SIMULATION,
        }
        d2_settlements.append(item)
        unified_rows.append(
            {
                "wallet_id": item["wallet_id"],
                "date": item["trade_date"],
                "settlement_date": item["settlement_date"],
                "entry_kind": "D2_SETTLEMENT",
                "subtype": "SETTLED" if is_settled else "PENDING_D2",
                "amount": amt,
                "settled": is_settled,
                "details": f"Liquidação B3 D+2 ({item['trade_date']} -> {item['settlement_date']})",
                "timestamp": item["updated_at"],
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )

    capital_transactions: list[dict[str, Any]] = []
    for r in tx_raw:
        d = dict(r)
        ts = str(d.get("timestamp") or "")
        amt = round(float(d.get("amount") or 0.0), 2)
        tx_item = {
            "id": int(d["id"]),
            "wallet_id": int(d["wallet_id"]),
            "date": ts[:10],
            "tx_type": str(d.get("tx_type") or ""),
            "amount": amt,
            "resulting_ai_capital": round(float(d.get("resulting_ai_capital") or 0.0), 2),
            "resulting_vault": round(float(d.get("resulting_vault") or 0.0), 2),
            "details": str(d.get("details") or ""),
            "timestamp": ts,
            "environment": settings.ENVIRONMENT_ID,
            "is_simulation": settings.IS_SIMULATION,
        }
        capital_transactions.append(tx_item)
        unified_rows.append(
            {
                "wallet_id": tx_item["wallet_id"],
                "date": tx_item["date"],
                "settlement_date": tx_item["date"],
                "entry_kind": "CAPITAL_TX",
                "subtype": tx_item["tx_type"],
                "amount": amt,
                "settled": True,
                "details": tx_item["details"],
                "timestamp": ts,
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )

    withdrawal_goals: list[dict[str, Any]] = []
    for r in goals_raw:
        d = dict(r)
        withdrawal_goals.append(
            {
                "id": int(d["id"]),
                "wallet_id": int(d["wallet_id"]),
                "amount": round(float(d.get("amount") or 0.0), 2),
                "mode": str(d.get("mode") or "instant"),
                "target_equity": (
                    round(float(d["target_equity"]), 2)
                    if d.get("target_equity") is not None
                    else None
                ),
                "status": str(d.get("status") or "ACTIVE"),
                "created_at": str(d.get("created_at") or ""),
                "completed_at": d.get("completed_at"),
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )

    trading_repo = TradingRepository(db)
    tax_guard = B3TaxGuard(
        exemption_limit=settings.B3_SWING_EXEMPTION_LIMIT,
        guard_buffer=settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
    )
    tax_summaries: list[dict[str, Any]] = []
    for wid in owned_wids:
        m_sells = trading_repo.get_month_sell_trades(uid, wid)
        summary_dict = tax_guard.summarize_month(m_sells).to_dict()
        tax_summaries.append(
            {
                "wallet_id": wid,
                **summary_dict,
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )

    return {
        "rows": unified_rows,
        "d2_settlements": d2_settlements,
        "capital_transactions": capital_transactions,
        "withdrawal_goals": withdrawal_goals,
        "tax_20k_summary": tax_summaries,
    }


def build_fact_market_radar(db: DatabaseConnection) -> list[dict[str, Any]]:
    snapshots = MarketHubRepository(db).get_market_snapshots()
    out: list[dict[str, Any]] = []
    for r in snapshots:
        tk = normalize_b3_ticker(str(r.get("ticker") or ""))
        if not tk:
            continue
        out.append(
            {
                "ticker": tk,
                "sector": SECTOR_MAP.get(tk, "Outros"),
                "price": round(float(r.get("price") or 0.0), 4),
                "bid": round(float(r.get("bid") or 0.0), 4),
                "ask": round(float(r.get("ask") or 0.0), 4),
                "spread_pct": round(float(r.get("spread_pct") or 0.0), 4),
                "volume": round(float(r.get("volume") or 0.0), 2),
                "change_pct": round(float(r.get("change_pct") or 0.0), 2),
                "rsi": round(float(r.get("rsi") or 50.0), 2),
                "macd_hist": round(float(r.get("macd_hist") or 0.0), 4),
                "atr": round(float(r.get("atr") or 0.0), 4),
                "adx": round(float(r.get("adx") or 0.0), 2),
                "trend_signal": str(r.get("trend_signal") or "NEUTRAL"),
                "mtf_bullish": bool(r.get("mtf_bullish", 1)),
                "sentiment_score": round(float(r.get("sentiment_score") or 0.0), 4),
                "is_stale": bool(r.get("is_stale", 0)),
                "updated_at": str(r.get("updated_at") or ""),
                "environment": settings.ENVIRONMENT_ID,
                "is_simulation": settings.IS_SIMULATION,
            }
        )
    return out


def _empty_kpi_summary(bova11_change_pct: float = 0.0) -> dict[str, Any]:
    return {
        "wallet_id": None,
        "initial_capital": 0.0,
        "total_equity": 0.0,
        "operational_equity": 0.0,
        "free_cash": 0.0,
        "allocated_capital": 0.0,
        "vault_reserve_cash": 0.0,
        "buying_power": 0.0,
        "unrealized_pnl": 0.0,
        "realized_pnl_total": 0.0,
        "total_fees": 0.0,
        "period_return_pct": 0.0,
        "cumulative_return_pct": 0.0,
        "daily_return_pct": 0.0,
        "current_drawdown_pct": 0.0,
        "max_drawdown_pct": 0.0,
        "high_water_mark": 0.0,
        "volatility_annualized_pct": 0.0,
        "sharpe_annualized": 0.0,
        "sortino_annualized": 0.0,
        "calmar_ratio": 0.0,
        "total_trades": 0,
        "buy_trades": 0,
        "sell_trades": 0,
        "winning_sells": 0,
        "losing_sells": 0,
        "win_rate_pct": 0.0,
        "avg_win_brl": 0.0,
        "avg_loss_brl": 0.0,
        "payoff_ratio": 0.0,
        "profit_factor": 0.0,
        "expectancy_brl": 0.0,
        "positive_days_pct": 0.0,
        "bova11_change_pct": round(float(bova11_change_pct), 2),
        "tax_sold_month_brl": 0.0,
        "tax_limit_brl": round(float(settings.B3_SWING_EXEMPTION_LIMIT), 2),
        "tax_guard_buffer_brl": round(float(settings.B3_SWING_EXEMPTION_GUARD_BUFFER), 2),
        "tax_remaining_exempt_brl": round(float(settings.B3_SWING_EXEMPTION_LIMIT), 2),
        "tax_utilization_pct": 0.0,
        "settled_cash_brl": 0.0,
        "pending_d2_net_brl": 0.0,
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
    }


def build_kpi_summary(
    db: DatabaseConnection,
    user_id: int,
    wallet_id: int | None,
    live_quotes: dict[str, Any] | None = None,
    bova11_change_pct: float = 0.0,
    days: int = 365,
) -> dict[str, Any]:
    if wallet_id is None:
        return _empty_kpi_summary(bova11_change_pct=bova11_change_pct)

    uid = int(user_id)
    wid = int(wallet_id)
    wallet_row = WalletsRepository(db).get_owned_wallet(uid, wid)
    if wallet_row is None:
        return _empty_kpi_summary(bova11_change_pct=bova11_change_pct)

    snap = _compute_readonly_wallet_snapshot(db, uid, wallet_row, live_quotes=live_quotes)
    cutoff_date = (get_brt_now() - timedelta(days=max(1, int(days)))).date().isoformat()
    today_str = get_brt_now().date().isoformat()

    with db.readonly() as conn:
        eq_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM equity_snapshots
                WHERE user_id = ? AND wallet_id = ? AND snapshot_date >= ?
                ORDER BY id ASC
                """,
                (uid, wid, cutoff_date),
            ).fetchall()
        ]
        trade_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM trades
                WHERE user_id = ? AND wallet_id = ? AND substr(timestamp, 1, 10) >= ?
                ORDER BY timestamp ASC, id ASC
                """,
                (uid, wid, cutoff_date),
            ).fetchall()
        ]
        d2_rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT net_sell_amount, settlement_date, settled
                FROM d2_settlements
                WHERE user_id = ? AND wallet_id = ?
                """,
                (uid, wid),
            ).fetchall()
        ]

    if not eq_rows:
        eq_rows = [_synthesize_single_equity_point(snap)]

    curve_metrics = compute_curve_risk_metrics(eq_rows, initial_capital=snap["initial_capital"])
    trade_metrics = compute_trade_metrics(trade_rows)

    month_sells = TradingRepository(db).get_month_sell_trades(uid, wid)
    tax_dict = B3TaxGuard(
        exemption_limit=settings.B3_SWING_EXEMPTION_LIMIT,
        guard_buffer=settings.B3_SWING_EXEMPTION_GUARD_BUFFER,
    ).summarize_month(month_sells).to_dict()

    pending_d2_receivable = 0.0
    pending_d2_net = 0.0
    for dr in d2_rows:
        settle_dt = str(dr.get("settlement_date") or "")
        is_settled = bool(dr.get("settled", 0)) or (bool(settle_dt) and settle_dt <= today_str)
        if not is_settled:
            amt = float(dr.get("net_sell_amount") or 0.0)
            pending_d2_net += amt
            if amt > 0.0:
                pending_d2_receivable += amt

    total_liquid = float(snap["free_cash"]) + float(snap["vault_reserve_cash"])
    settled_cash = round(max(0.0, total_liquid - pending_d2_receivable), 2)

    return {
        "wallet_id": wid,
        "initial_capital": snap["initial_capital"],
        "total_equity": snap["total_equity"],
        "operational_equity": snap["operational_equity"],
        "free_cash": snap["free_cash"],
        "allocated_capital": snap["allocated_capital"],
        "vault_reserve_cash": snap["vault_reserve_cash"],
        "buying_power": snap["buying_power"],
        "unrealized_pnl": snap["unrealized_pnl"],
        "realized_pnl_total": float(trade_metrics["realized_pnl_total"]),
        "total_fees": float(trade_metrics["total_fees"]),
        "period_return_pct": curve_metrics["period_return_pct"],
        "cumulative_return_pct": snap["pnl_percent"]
        if snap["initial_capital"] > 0.0
        else curve_metrics["cumulative_return_pct"],
        "daily_return_pct": snap["daily_pnl_percent"],
        "current_drawdown_pct": curve_metrics["current_drawdown_pct"],
        "max_drawdown_pct": curve_metrics["max_drawdown_pct"],
        "high_water_mark": max(curve_metrics["high_water_mark"], snap["total_equity"]),
        "volatility_annualized_pct": curve_metrics["volatility_annualized_pct"],
        "sharpe_annualized": curve_metrics["sharpe_annualized"],
        "sortino_annualized": curve_metrics["sortino_annualized"],
        "calmar_ratio": curve_metrics["calmar_ratio"],
        "total_trades": int(trade_metrics["total_trades"]),
        "buy_trades": int(trade_metrics["buy_trades"]),
        "sell_trades": int(trade_metrics["sell_trades"]),
        "winning_sells": int(trade_metrics["winning_sells"]),
        "losing_sells": int(trade_metrics["losing_sells"]),
        "win_rate_pct": float(trade_metrics["win_rate_pct"]),
        "avg_win_brl": float(trade_metrics["avg_win_brl"]),
        "avg_loss_brl": float(trade_metrics["avg_loss_brl"]),
        "payoff_ratio": float(trade_metrics["payoff_ratio"]),
        "profit_factor": float(trade_metrics["profit_factor"]),
        "expectancy_brl": float(trade_metrics["expectancy_brl"]),
        "positive_days_pct": curve_metrics["positive_days_pct"],
        "bova11_change_pct": round(float(bova11_change_pct), 2),
        "tax_sold_month_brl": float(tax_dict["gross_exempt_eligible_sales"]),
        "tax_limit_brl": float(tax_dict["exemption_limit"]),
        "tax_guard_buffer_brl": float(tax_dict["guard_buffer"]),
        "tax_remaining_exempt_brl": float(tax_dict["remaining_exemption_headroom"]),
        "tax_utilization_pct": float(tax_dict["utilization_pct"]),
        "settled_cash_brl": settled_cash,
        "pending_d2_net_brl": round(pending_d2_net, 2),
        "environment": settings.ENVIRONMENT_ID,
        "is_simulation": settings.IS_SIMULATION,
    }


def _extract_market_runtime_state(
    request: Any,
    radar_rows: list[dict[str, Any]],
) -> tuple[bool, float, bool, dict[str, Any] | None]:
    orch = getattr(getattr(request, "app", None), "state", None)
    orch_inst = getattr(orch, "orchestrator", None) if orch is not None else None
    hub_snap = None
    if orch_inst is not None:
        hub = getattr(orch_inst, "market_hub", None) or getattr(orch_inst, "hub", None)
        if hub is not None and hasattr(hub, "get_latest_snapshot"):
            try:
                hub_snap = hub.get_latest_snapshot()
            except Exception:
                hub_snap = None

    if hub_snap is not None:
        market_open = bool(hub_snap.market_open)
        bova11_pct = round(float(hub_snap.bova11_change_pct), 2)
        bearish_veto = bool(hub_snap.regime_bearish_veto)
        live_quotes = dict(hub_snap.quotes)
    else:
        now = get_brt_now()
        if is_b3_business_day(now.date()):
            open_t = dt_time(13, 0) if now.date() == get_ash_wednesday(now.year) else dt_time(10, 0)
            market_open = open_t <= now.time() <= dt_time(17, 55)
        else:
            market_open = False
        bova11_pct = 0.0
        for item in radar_rows:
            if item.get("ticker") == "BOVA11":
                bova11_pct = round(float(item.get("change_pct") or 0.0), 2)
                break
        bearish_veto = bova11_pct <= float(settings.MARKET_REGIME_MAX_DROP_PCT)
        live_quotes = None

    return market_open, bova11_pct, bearish_veto, live_quotes


def build_mobile_bundle(
    request: Any,
    db: DatabaseConnection,
    user_id: int,
    selected_wallet: dict[str, Any] | None,
    all_user_wallets: list[dict[str, Any]],
    days: int = 180,
) -> dict[str, Any]:
    uid = int(user_id)
    safe_days = max(7, min(int(days), 3650))
    radar_full = build_fact_market_radar(db)
    market_open, bova11_pct, bearish_veto, live_quotes = _extract_market_runtime_state(request, radar_full)

    wallets_payload = [
        {
            "id": int(w["id"]),
            "name": str(w.get("name") or ""),
            "connector_type": str(w.get("connector_type") or ""),
            "is_default": bool(w.get("is_default", 0)),
            "operational_mode": str(w.get("operational_mode") or "ACTIVE"),
            "automation_enabled": bool(w.get("automation_enabled", 0)),
            "initial_capital": round(float(w.get("initial_capital") or 0.0), 2),
            "free_cash": round(float(w.get("free_cash") or 0.0), 2),
            "ai_allocated_capital": round(float(w.get("ai_allocated_capital") or 0.0), 2),
            "vault_reserve_cash": round(float(w.get("vault_reserve_cash") or 0.0), 2),
        }
        for w in all_user_wallets
    ]

    market_radar = [
        {
            "ticker": str(r["ticker"]),
            "sector": str(r["sector"]),
            "price": float(r["price"]),
            "change_pct": float(r["change_pct"]),
            "volume": float(r["volume"]),
            "rsi": float(r["rsi"]),
            "macd_hist": float(r["macd_hist"]),
            "adx": float(r["adx"]),
            "sentiment_score": float(r["sentiment_score"]),
            "trend_signal": str(r["trend_signal"]),
            "is_stale": bool(r["is_stale"]),
        }
        for r in radar_full
    ]

    if selected_wallet is None:
        empty_kpi = _empty_kpi_summary(bova11_change_pct=bova11_pct)
        return {
            "success": True,
            "schema_version": "1.0",
            "environment": settings.ENVIRONMENT_ID,
            "environment_label": settings.ENVIRONMENT_LABEL,
            "is_production": settings.IS_PRODUCTION,
            "is_simulation": settings.IS_SIMULATION,
            "generated_at": get_brt_now().isoformat(),
            "market_open": market_open,
            "market_status": "ABERTO" if market_open else "FECHADO",
            "bova11_change_pct": bova11_pct,
            "regime_bearish_veto": bearish_veto,
            "active_wallet": None,
            "wallets": wallets_payload,
            "kpi_summary": empty_kpi,
            "equity_series": [],
            "monthly_returns": [],
            "positions": [],
            "sector_exposure": [],
            "pnl_by_ticker": [],
            "pnl_by_sector": [],
            "cashflow_d2": [],
            "decisions_summary": compute_decisions_summary([]),
            "market_radar": market_radar,
        }

    wid = int(selected_wallet["id"])
    active_wallet_info = {
        "id": wid,
        "name": str(selected_wallet.get("name") or ""),
        "connector_type": str(selected_wallet.get("connector_type") or ""),
        "operational_mode": str(selected_wallet.get("operational_mode") or "ACTIVE"),
        "automation_enabled": bool(selected_wallet.get("automation_enabled", 0)),
    }

    kpi_summary = build_kpi_summary(
        db=db,
        user_id=uid,
        wallet_id=wid,
        live_quotes=live_quotes,
        bova11_change_pct=bova11_pct,
        days=safe_days,
    )

    fact_eq = build_fact_equity(db, uid, [wid], days=safe_days)
    equity_series = [
        {
            "date": str(pt["date"]),
            "timestamp": str(pt["timestamp"]),
            "total_equity": float(pt["total_equity"]),
            "operational_equity": float(pt["operational_equity"]),
            "initial_capital": float(pt["initial_capital"]),
            "free_cash": float(pt["free_cash"]),
            "allocated_capital": float(pt["allocated_capital"]),
            "vault_reserve_cash": float(pt["vault_reserve_cash"]),
            "pnl_percent": float(pt["pnl_percent"]),
            "daily_pnl_percent": float(pt["daily_pnl_percent"]),
            "high_water_mark": float(pt["high_water_mark"]),
            "drawdown_pct": float(pt["drawdown_pct"]),
            "buys_count": int(pt["buys_count"]),
            "sells_count": int(pt["sells_count"]),
        }
        for pt in fact_eq
    ]

    fact_tr = build_fact_trades(db, uid, [wid], days=safe_days)
    monthly_returns = compute_monthly_returns(equity_series, fact_tr)

    raw_positions = build_fact_positions(db, uid, [wid], live_quotes=live_quotes)
    positions = [
        {
            "ticker": str(p["ticker"]),
            "sector": str(p["sector"]),
            "quantity": int(p["quantity"]),
            "entry_price": float(p["entry_price"]),
            "current_price": float(p["current_price"]),
            "stop_loss": float(p["stop_loss"]),
            "highest_price": float(p["highest_price"]),
            "market_value": float(p["market_value"]),
            "weight_pct": float(p["weight_pct"]),
            "unrealized_pnl": float(p["unrealized_pnl"]),
            "pnl_percent": float(p["pnl_percent"]),
            "stop_distance_pct": float(p["stop_distance_pct"]),
            "do_not_touch": bool(p["do_not_touch"]),
            "partial_tp_taken": bool(p["partial_tp_taken"]),
            "holding_days": int(p["holding_days"]),
        }
        for p in raw_positions
    ]

    tot_eq = float(kpi_summary["total_equity"])
    sec_map_exp: dict[str, dict[str, Any]] = {}
    for p in positions:
        sec = p["sector"]
        entry = sec_map_exp.setdefault(
            sec,
            {
                "sector": sec,
                "market_value": 0.0,
                "unrealized_pnl": 0.0,
                "positions_count": 0,
            },
        )
        entry["market_value"] += p["market_value"]
        entry["unrealized_pnl"] += p["unrealized_pnl"]
        entry["positions_count"] += 1

    sector_exposure = [
        {
            "sector": sec,
            "market_value": round(val["market_value"], 2),
            "weight_pct": round((val["market_value"] / tot_eq) * 100.0, 2) if tot_eq > 0.0 else 0.0,
            "unrealized_pnl": round(val["unrealized_pnl"], 2),
            "positions_count": int(val["positions_count"]),
        }
        for sec, val in sec_map_exp.items()
    ]
    sector_exposure.sort(key=lambda item: (-item["market_value"], item["sector"]))

    pnl_by_ticker, pnl_by_sector = compute_pnl_by_ticker_and_sector(
        fact_tr,
        positions,
        SECTOR_MAP,
    )

    cashflow_payload = build_fact_cashflow_d2(db, uid, [wid], days=safe_days)
    cashflow_d2 = [
        {
            "trade_date": str(item["trade_date"]),
            "settlement_date": str(item["settlement_date"]),
            "net_sell_amount": float(item["net_sell_amount"]),
            "settled": bool(item["settled"]),
        }
        for item in cashflow_payload["d2_settlements"]
    ]

    fact_dec = build_fact_decisions(db, uid, [wid], days=safe_days)
    decisions_summary = compute_decisions_summary(fact_dec)

    return {
        "success": True,
        "schema_version": "1.0",
        "environment": settings.ENVIRONMENT_ID,
        "environment_label": settings.ENVIRONMENT_LABEL,
        "is_production": settings.IS_PRODUCTION,
        "is_simulation": settings.IS_SIMULATION,
        "generated_at": get_brt_now().isoformat(),
        "market_open": market_open,
        "market_status": "ABERTO" if market_open else "FECHADO",
        "bova11_change_pct": bova11_pct,
        "regime_bearish_veto": bearish_veto,
        "active_wallet": active_wallet_info,
        "wallets": wallets_payload,
        "kpi_summary": kpi_summary,
        "equity_series": equity_series,
        "monthly_returns": monthly_returns,
        "positions": positions,
        "sector_exposure": sector_exposure,
        "pnl_by_ticker": pnl_by_ticker,
        "pnl_by_sector": pnl_by_sector,
        "cashflow_d2": cashflow_d2,
        "decisions_summary": decisions_summary,
        "market_radar": market_radar,
    }
