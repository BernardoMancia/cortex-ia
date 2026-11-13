from __future__ import annotations

import math
from typing import Any

from domain.b3_rules import normalize_b3_ticker

_SQRT_252: float = math.sqrt(252.0)


def _finite(val: Any, default: float = 0.0) -> float:
    try:
        res = float(val)
        return res if math.isfinite(res) else default
    except (TypeError, ValueError):
        return default


def _clean_zero(val: float, digits: int = 4) -> float:
    rounded = round(_finite(val, 0.0), digits)
    return 0.0 if abs(rounded) < (10.0 ** (-digits)) else rounded


def enrich_equity_series(
    equity_rows: list[dict[str, Any]],
    trade_rows: list[dict[str, Any]] | None = None,
    initial_capital: float = 0.0,
) -> list[dict[str, Any]]:
    day_counts: dict[str, tuple[int, int]] = {}
    for tr in trade_rows or []:
        d_key = str(tr.get("timestamp") or tr.get("date") or "")[:10]
        if not d_key:
            continue
        b_cnt, s_cnt = day_counts.get(d_key, (0, 0))
        act = str(tr.get("action") or "").strip().upper()
        if act == "BUY":
            day_counts[d_key] = (b_cnt + 1, s_cnt)
        elif act == "SELL":
            day_counts[d_key] = (b_cnt, s_cnt + 1)

    ordered = sorted(
        equity_rows,
        key=lambda r: (
            str(r.get("snapshot_date") or r.get("date") or str(r.get("timestamp") or "")[:10]),
            str(r.get("timestamp") or ""),
            int(r.get("id") or 0),
        ),
    )

    init_cap = max(0.0, _finite(initial_capital, 0.0))
    hwm = 0.0
    out: list[dict[str, Any]] = []

    for idx, row in enumerate(ordered):
        snap_date = str(row.get("snapshot_date") or row.get("date") or str(row.get("timestamp") or "")[:10])
        ts = str(row.get("timestamp") or snap_date)
        tot_eq = _clean_zero(_finite(row.get("total_equity"), 0.0), 2)
        op_eq = _clean_zero(_finite(row.get("operational_equity"), tot_eq), 2)
        row_init = _clean_zero(_finite(row.get("initial_capital"), init_cap), 2)
        if idx == 0 and len(ordered) == 1 and row_init > tot_eq:
            hwm = row_init
        hwm = max(hwm, tot_eq)
        raw_dd = ((tot_eq - hwm) / hwm) * 100.0 if hwm > 0.0 else 0.0
        dd_pct = _clean_zero(min(0.0, raw_dd), 4)
        b_cnt, s_cnt = day_counts.get(snap_date, (0, 0))
        if "buys_count" in row and b_cnt == 0:
            b_cnt = int(row.get("buys_count") or 0)
        if "sells_count" in row and s_cnt == 0:
            s_cnt = int(row.get("sells_count") or 0)

        enriched = {
            **row,
            "date": snap_date,
            "snapshot_date": snap_date,
            "timestamp": ts,
            "initial_capital": row_init,
            "total_equity": tot_eq,
            "operational_equity": op_eq,
            "free_cash": _clean_zero(_finite(row.get("free_cash"), 0.0), 2),
            "allocated_capital": _clean_zero(_finite(row.get("allocated_capital"), 0.0), 2),
            "vault_reserve_cash": _clean_zero(_finite(row.get("vault_reserve_cash"), 0.0), 2),
            "pnl_percent": _clean_zero(_finite(row.get("pnl_percent"), 0.0), 4),
            "daily_pnl_percent": _clean_zero(_finite(row.get("daily_pnl_percent"), 0.0), 4),
            "high_water_mark": _clean_zero(hwm, 2),
            "drawdown_pct": dd_pct,
            "buys_count": b_cnt,
            "sells_count": s_cnt,
        }
        out.append(enriched)

    return out


def compute_curve_risk_metrics(
    equity_rows: list[dict[str, Any]],
    initial_capital: float = 0.0,
) -> dict[str, float]:
    enriched = enrich_equity_series(equity_rows, initial_capital=initial_capital)
    init_cap = max(0.0, _finite(initial_capital, 0.0))

    if not enriched:
        return {
            "period_return_pct": 0.0,
            "cumulative_return_pct": 0.0,
            "current_drawdown_pct": 0.0,
            "max_drawdown_pct": 0.0,
            "high_water_mark": _clean_zero(init_cap, 2),
            "volatility_annualized_pct": 0.0,
            "sharpe_annualized": 0.0,
            "sortino_annualized": 0.0,
            "calmar_ratio": 0.0,
            "positive_days_pct": 0.0,
        }

    first_eq = _finite(enriched[0].get("total_equity"), 0.0)
    last_eq = _finite(enriched[-1].get("total_equity"), 0.0)
    if init_cap <= 0.0:
        init_cap = _finite(enriched[0].get("initial_capital"), first_eq)

    if len(enriched) >= 2 and first_eq > 0.0:
        period_ret_pct = _clean_zero(((last_eq - first_eq) / first_eq) * 100.0, 2)
    elif init_cap > 0.0:
        period_ret_pct = _clean_zero(((last_eq - init_cap) / init_cap) * 100.0, 2)
    else:
        period_ret_pct = 0.0

    cum_ret_pct = (
        _clean_zero(((last_eq - init_cap) / init_cap) * 100.0, 2)
        if init_cap > 0.0
        else period_ret_pct
    )

    current_dd = _clean_zero(min(0.0, _finite(enriched[-1].get("drawdown_pct"), 0.0)), 4)
    max_dd = _clean_zero(
        min(0.0, min(_finite(r.get("drawdown_pct"), 0.0) for r in enriched)),
        4,
    )
    hwm = _clean_zero(max(_finite(r.get("high_water_mark"), 0.0) for r in enriched), 2)

    by_date: dict[str, float] = {}
    for idx, r in enumerate(enriched):
        d_key = str(r.get("date") or r.get("snapshot_date") or f"pt_{idx:05d}")
        by_date[d_key] = _finite(r.get("total_equity"), 0.0)

    series_vals = list(by_date.values())
    if len(series_vals) < 2 and len(enriched) >= 2:
        series_vals = [_finite(r.get("total_equity"), 0.0) for r in enriched]

    daily_ret: list[float] = []
    for idx in range(1, len(series_vals)):
        prev_v = series_vals[idx - 1]
        curr_v = series_vals[idx]
        if prev_v > 0.0:
            daily_ret.append((curr_v - prev_v) / prev_v)

    n = len(daily_ret)
    if n >= 2:
        mean_r = sum(daily_ret) / n
        variance = sum((r - mean_r) ** 2 for r in daily_ret) / (n - 1)
        std_r = math.sqrt(max(0.0, variance))
        vol_ann_pct = _clean_zero(std_r * 100.0 * _SQRT_252, 4)
        sharpe = _clean_zero((mean_r / std_r) * _SQRT_252, 4) if std_r > 1e-9 else 0.0

        downside_sq = [r * r for r in daily_ret if r < 0.0]
        downside_dev = math.sqrt(sum(downside_sq) / n) if downside_sq else 0.0
        if downside_dev > 1e-9:
            sortino = _clean_zero((mean_r / downside_dev) * _SQRT_252, 4)
        elif mean_r > 0.0 and sharpe > 0.0:
            sortino = sharpe
        else:
            sortino = 0.0

        ann_ret_pct = mean_r * 252.0 * 100.0
        calmar = (
            _clean_zero(ann_ret_pct / abs(max_dd), 4)
            if abs(max_dd) > 1e-9
            else 0.0
        )
        pos_days = sum(1 for r in daily_ret if r > 0.0)
        pos_days_pct = _clean_zero((pos_days / n) * 100.0, 2)
    elif n == 1:
        vol_ann_pct = 0.0
        sharpe = 0.0
        sortino = 0.0
        calmar = 0.0
        pos_days_pct = 100.0 if daily_ret[0] > 0.0 else 0.0
    else:
        vol_ann_pct = 0.0
        sharpe = 0.0
        sortino = 0.0
        calmar = 0.0
        pos_days_pct = 0.0

    return {
        "period_return_pct": period_ret_pct,
        "cumulative_return_pct": cum_ret_pct,
        "current_drawdown_pct": current_dd,
        "max_drawdown_pct": max_dd,
        "high_water_mark": hwm,
        "volatility_annualized_pct": vol_ann_pct,
        "sharpe_annualized": sharpe,
        "sortino_annualized": sortino,
        "calmar_ratio": calmar,
        "positive_days_pct": pos_days_pct,
    }


def compute_trade_metrics(trades: list[dict[str, Any]]) -> dict[str, int | float]:
    total_trades = len(trades)
    buy_trades = 0
    sell_trades = 0
    wins: list[float] = []
    losses: list[float] = []
    realized_total = 0.0
    total_fees = 0.0

    for tr in trades:
        act = str(tr.get("action") or "").strip().upper()
        fee = max(0.0, _finite(tr.get("fee"), 0.0))
        total_fees += fee
        if act == "BUY":
            buy_trades += 1
        elif act == "SELL":
            sell_trades += 1
            pnl = _finite(tr.get("realized_pnl"), 0.0)
            realized_total += pnl
            if pnl > 0.0:
                wins.append(pnl)
            elif pnl < 0.0:
                losses.append(pnl)

    winning_sells = len(wins)
    losing_sells = len(losses)
    win_rate_pct = _clean_zero((winning_sells / sell_trades) * 100.0, 2) if sell_trades > 0 else 0.0

    gross_profit = sum(wins)
    gross_loss_abs = abs(sum(losses))

    avg_win_brl = _clean_zero(gross_profit / winning_sells, 2) if winning_sells > 0 else 0.0
    avg_loss_brl = _clean_zero(sum(losses) / losing_sells, 2) if losing_sells > 0 else 0.0

    payoff_ratio = (
        _clean_zero(avg_win_brl / abs(avg_loss_brl), 4)
        if abs(avg_loss_brl) > 1e-9 and winning_sells > 0
        else 0.0
    )
    if gross_loss_abs > 1e-9:
        profit_factor = _clean_zero(gross_profit / gross_loss_abs, 4)
    elif gross_profit > 0.0:
        profit_factor = 99.99
    else:
        profit_factor = 0.0

    expectancy_brl = _clean_zero(realized_total / sell_trades, 2) if sell_trades > 0 else 0.0

    return {
        "total_trades": total_trades,
        "buy_trades": buy_trades,
        "sell_trades": sell_trades,
        "winning_sells": winning_sells,
        "losing_sells": losing_sells,
        "win_rate_pct": win_rate_pct,
        "avg_win_brl": avg_win_brl,
        "avg_loss_brl": avg_loss_brl,
        "payoff_ratio": payoff_ratio,
        "profit_factor": profit_factor,
        "expectancy_brl": expectancy_brl,
        "realized_pnl_total": _clean_zero(realized_total, 2),
        "total_fees": _clean_zero(total_fees, 2),
    }


def compute_monthly_returns(
    equity_rows: list[dict[str, Any]],
    trade_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    trades_by_month: dict[str, int] = {}
    sell_pnl_by_month: dict[str, float] = {}
    for tr in trade_rows or []:
        ts = str(tr.get("timestamp") or tr.get("date") or "")
        ym = ts[:7]
        if len(ym) == 7 and ym[4] == "-" and ym[:4].isdigit() and ym[5:7].isdigit():
            trades_by_month[ym] = trades_by_month.get(ym, 0) + 1
            if str(tr.get("action") or "").strip().upper() == "SELL":
                sell_pnl_by_month[ym] = sell_pnl_by_month.get(ym, 0.0) + _finite(tr.get("realized_pnl"), 0.0)

    ordered = enrich_equity_series(equity_rows, trade_rows=trade_rows)
    eq_by_month: dict[str, list[dict[str, Any]]] = {}
    for row in ordered:
        d_str = str(row.get("date") or row.get("snapshot_date") or "")
        ym = d_str[:7]
        if len(ym) == 7 and ym[4] == "-" and ym[:4].isdigit() and ym[5:7].isdigit():
            eq_by_month.setdefault(ym, []).append(row)

    all_months = sorted(set(eq_by_month.keys()) | set(trades_by_month.keys()))
    result: list[dict[str, Any]] = []
    prev_end_equity: float | None = None

    for ym in all_months:
        year_num = int(ym[:4])
        month_num = int(ym[5:7])
        m_rows = eq_by_month.get(ym)
        t_count = trades_by_month.get(ym, 0)

        if m_rows:
            first_eq = _finite(m_rows[0].get("total_equity"), 0.0)
            end_eq = _finite(m_rows[-1].get("total_equity"), 0.0)
            row_init = _finite(m_rows[0].get("initial_capital"), 0.0)
            if prev_end_equity is not None and prev_end_equity > 0.0:
                base_eq = prev_end_equity
            elif len(m_rows) == 1 and row_init > 0.0:
                base_eq = row_init
            else:
                base_eq = first_eq if first_eq > 0.0 else row_init

            pnl_brl = _clean_zero(end_eq - base_eq, 2) if base_eq > 0.0 else 0.0
            ret_pct = _clean_zero(((end_eq - base_eq) / base_eq) * 100.0, 2) if base_eq > 0.0 else 0.0
            prev_end_equity = end_eq
        else:
            pnl_brl = _clean_zero(sell_pnl_by_month.get(ym, 0.0), 2)
            ret_pct = 0.0

        result.append(
            {
                "year": year_num,
                "month": month_num,
                "year_month": ym,
                "return_pct": ret_pct,
                "pnl_brl": pnl_brl,
                "trades_count": t_count,
            }
        )

    return result


def compute_pnl_by_ticker_and_sector(
    trades: list[dict[str, Any]],
    positions: list[Any],
    sector_map: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_ticker: dict[str, dict[str, Any]] = {}

    def _ensure_entry(raw_tk: str) -> dict[str, Any] | None:
        tk = normalize_b3_ticker(raw_tk)
        if not tk:
            return None
        if tk not in by_ticker:
            by_ticker[tk] = {
                "ticker": tk,
                "sector": sector_map.get(tk, "Outros"),
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "trades_count": 0,
                "sell_count": 0,
                "win_sells": 0,
            }
        return by_ticker[tk]

    for tr in trades:
        entry = _ensure_entry(str(tr.get("ticker") or ""))
        if entry is None:
            continue
        entry["trades_count"] += 1
        if str(tr.get("action") or "").strip().upper() == "SELL":
            pnl = _finite(tr.get("realized_pnl"), 0.0)
            entry["sell_count"] += 1
            entry["realized_pnl"] += pnl
            if pnl > 0.0:
                entry["win_sells"] += 1

    for pos in positions:
        if isinstance(pos, dict):
            raw_tk = str(pos.get("ticker") or "")
            unr = _finite(
                pos.get("unrealized_pnl") if "unrealized_pnl" in pos else pos.get("pnl"),
                0.0,
            )
        else:
            raw_tk = str(getattr(pos, "ticker", "") or "")
            unr = _finite(getattr(pos, "pnl", 0.0), 0.0)
        entry = _ensure_entry(raw_tk)
        if entry is None:
            continue
        entry["unrealized_pnl"] += unr

    ticker_list: list[dict[str, Any]] = []
    sector_acc: dict[str, dict[str, Any]] = {}

    for tk, data in by_ticker.items():
        real_pnl = _clean_zero(data["realized_pnl"], 2)
        unr_pnl = _clean_zero(data["unrealized_pnl"], 2)
        tot_pnl = _clean_zero(real_pnl + unr_pnl, 2)
        s_cnt = int(data["sell_count"])
        w_cnt = int(data["win_sells"])
        win_rate = _clean_zero((w_cnt / s_cnt) * 100.0, 2) if s_cnt > 0 else 0.0
        sector = str(data["sector"])

        ticker_list.append(
            {
                "ticker": tk,
                "sector": sector,
                "realized_pnl": real_pnl,
                "unrealized_pnl": unr_pnl,
                "total_pnl": tot_pnl,
                "trades_count": int(data["trades_count"]),
                "win_rate_pct": win_rate,
            }
        )

        sec_entry = sector_acc.setdefault(
            sector,
            {
                "sector": sector,
                "realized_pnl": 0.0,
                "unrealized_pnl": 0.0,
                "trades_count": 0,
            },
        )
        sec_entry["realized_pnl"] += real_pnl
        sec_entry["unrealized_pnl"] += unr_pnl
        sec_entry["trades_count"] += int(data["trades_count"])

    ticker_list.sort(key=lambda item: (-item["total_pnl"], item["ticker"]))

    sector_list: list[dict[str, Any]] = []
    for sec, sdata in sector_acc.items():
        sr = _clean_zero(sdata["realized_pnl"], 2)
        su = _clean_zero(sdata["unrealized_pnl"], 2)
        sector_list.append(
            {
                "sector": sec,
                "realized_pnl": sr,
                "unrealized_pnl": su,
                "total_pnl": _clean_zero(sr + su, 2),
                "trades_count": int(sdata["trades_count"]),
            }
        )
    sector_list.sort(key=lambda item: (-item["total_pnl"], item["sector"]))

    return ticker_list, sector_list


def compute_decisions_summary(decisions: list[dict[str, Any]]) -> dict[str, Any]:
    buy_count = 0
    sell_count = 0
    hold_count = 0
    conf_values: list[float] = []
    b_0_50 = 0
    b_50_70 = 0
    b_70_85 = 0
    b_85_100 = 0

    for dec in decisions:
        act = str(dec.get("action") or "").strip().upper()
        if act == "BUY":
            buy_count += 1
        elif act == "SELL":
            sell_count += 1
        else:
            hold_count += 1

        raw_conf = _finite(dec.get("confidence"), 0.0)
        conf_pct = raw_conf * 100.0 if raw_conf <= 1.0 else raw_conf
        conf_pct = max(0.0, min(100.0, conf_pct))
        conf_values.append(conf_pct)

        if conf_pct < 50.0:
            b_0_50 += 1
        elif conf_pct < 70.0:
            b_50_70 += 1
        elif conf_pct < 85.0:
            b_70_85 += 1
        else:
            b_85_100 += 1

    total = len(decisions)
    avg_conf = _clean_zero(sum(conf_values) / total, 2) if total > 0 else 0.0

    return {
        "total": total,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "hold_count": hold_count,
        "avg_confidence_pct": avg_conf,
        "confidence_buckets": [
            {"label": "0-50%", "count": b_0_50},
            {"label": "50-70%", "count": b_50_70},
            {"label": "70-85%", "count": b_70_85},
            {"label": "85-100%", "count": b_85_100},
        ],
    }
