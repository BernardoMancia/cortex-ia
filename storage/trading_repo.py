from __future__ import annotations

from typing import Any

from domain.b3_rules import get_brt_now, normalize_b3_ticker
from domain.models import Position
from storage.connection import DatabaseConnection


class TradingRepository:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()

    @staticmethod
    def _row_to_position(row: dict[str, Any]) -> Position:
        return Position(
            ticker=str(row["ticker"]),
            quantity=int(row["quantity"]),
            entry_price=float(row["entry_price"]),
            current_price=float(row["current_price"]),
            stop_loss=float(row.get("stop_loss", 0.0) or 0.0),
            opened_at=str(row.get("opened_at") or ""),
            highest_price=float(row.get("highest_price", 0.0) or 0.0),
            partial_tp_taken=bool(row.get("partial_tp_taken", 0)),
            do_not_touch=bool(row.get("do_not_touch", 0)),
            ticket=int(row.get("ticket", 0) or 0),
            fiscal_avg_price=float(row.get("fiscal_avg_price", 0.0) or float(row["entry_price"])),
        )

    def list_positions(self, user_id: int, wallet_id: int) -> list[Position]:
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT * FROM positions
                WHERE user_id = ? AND wallet_id = ?
                ORDER BY ticker ASC
                """,
                (int(user_id), int(wallet_id)),
            ).fetchall()
        return [self._row_to_position(dict(r)) for r in rows]

    def get_position(self, user_id: int, wallet_id: int, ticker: str) -> Position | None:
        clean_tk = normalize_b3_ticker(ticker)
        if not clean_tk:
            return None
        with self.db.readonly() as conn:
            row = conn.execute(
                """
                SELECT * FROM positions
                WHERE user_id = ? AND wallet_id = ? AND ticker = ?
                """,
                (int(user_id), int(wallet_id), clean_tk),
            ).fetchone()
        return self._row_to_position(dict(row)) if row is not None else None

    def upsert_position(self, user_id: int, wallet_id: int, pos: Position) -> None:
        clean_tk = normalize_b3_ticker(pos.ticker)
        if not clean_tk or int(pos.quantity) <= 0:
            return
        now_iso = get_brt_now().isoformat()
        opened_iso = pos.opened_at or now_iso
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO positions (
                    user_id, wallet_id, ticker, quantity, entry_price, current_price,
                    stop_loss, highest_price, fiscal_avg_price, partial_tp_taken,
                    do_not_touch, ticket, opened_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet_id, ticker) DO UPDATE SET
                    quantity = excluded.quantity,
                    entry_price = excluded.entry_price,
                    current_price = excluded.current_price,
                    stop_loss = excluded.stop_loss,
                    highest_price = excluded.highest_price,
                    fiscal_avg_price = excluded.fiscal_avg_price,
                    partial_tp_taken = excluded.partial_tp_taken,
                    do_not_touch = excluded.do_not_touch,
                    ticket = excluded.ticket,
                    updated_at = excluded.updated_at
                WHERE positions.user_id = excluded.user_id
                """,
                (
                    int(user_id),
                    int(wallet_id),
                    clean_tk,
                    int(pos.quantity),
                    round(float(pos.entry_price), 4),
                    round(float(pos.current_price), 4),
                    round(float(pos.stop_loss), 4),
                    round(float(pos.highest_price), 4),
                    round(float(pos.fiscal_avg_price), 4),
                    int(bool(pos.partial_tp_taken)),
                    int(bool(pos.do_not_touch)),
                    int(pos.ticket),
                    opened_iso,
                    now_iso,
                ),
            )

    def delete_position(self, user_id: int, wallet_id: int, ticker: str) -> None:
        clean_tk = normalize_b3_ticker(ticker)
        with self.db.transaction() as conn:
            conn.execute(
                "DELETE FROM positions WHERE user_id = ? AND wallet_id = ? AND ticker = ?",
                (int(user_id), int(wallet_id), clean_tk),
            )

    def set_do_not_touch(self, user_id: int, wallet_id: int, ticker: str, flag: bool) -> bool:
        clean_tk = normalize_b3_ticker(ticker)
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                UPDATE positions
                SET do_not_touch = ?, updated_at = ?
                WHERE user_id = ? AND wallet_id = ? AND ticker = ?
                """,
                (int(bool(flag)), now_iso, int(user_id), int(wallet_id), clean_tk),
            )
            return int(cur.rowcount or 0) > 0

    def record_trade(
        self,
        user_id: int,
        wallet_id: int,
        ticker: str,
        action: str,
        quantity: int,
        price: float,
        fee: float = 0.0,
        realized_pnl: float = 0.0,
        stop_loss: float = 0.0,
        is_day_trade: bool = False,
        client_order_id: str = "",
        reasoning: str = "",
        timestamp: str | None = None,
    ) -> int:
        clean_tk = normalize_b3_ticker(ticker)
        clean_act = action.strip().upper()
        ts = timestamp or get_brt_now().isoformat()
        total_val = round(float(quantity) * float(price), 2)
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO trades (
                    user_id, wallet_id, ticker, action, quantity, price, total_value,
                    fee, realized_pnl, stop_loss, is_day_trade, client_order_id, reasoning, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    int(wallet_id),
                    clean_tk,
                    clean_act,
                    int(quantity),
                    round(float(price), 4),
                    total_val,
                    round(float(fee), 4),
                    round(float(realized_pnl), 2),
                    round(float(stop_loss), 4),
                    int(bool(is_day_trade)),
                    (client_order_id or "").strip()[:64],
                    (reasoning or "").strip()[:500],
                    ts,
                ),
            )
            return int(cur.lastrowid or 0)

    def list_trades(self, user_id: int, wallet_id: int, limit: int = 100) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 500))
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT * FROM trades
                WHERE user_id = ? AND wallet_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (int(user_id), int(wallet_id), capped),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_month_sell_trades(
        self,
        user_id: int,
        wallet_id: int,
        year_month: str | None = None,
    ) -> list[dict[str, Any]]:
        ym = (year_month or get_brt_now().strftime("%Y-%m")).strip()[:7]
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT * FROM trades
                WHERE user_id = ? AND wallet_id = ? AND action = 'SELL'
                  AND substr(timestamp, 1, 7) = ?
                ORDER BY timestamp ASC
                """,
                (int(user_id), int(wallet_id), ym),
            ).fetchall()
        return [dict(r) for r in rows]

    def has_same_day_action(
        self,
        user_id: int,
        wallet_id: int,
        ticker: str,
        action: str,
        day_iso_prefix: str | None = None,
    ) -> bool:
        clean_tk = normalize_b3_ticker(ticker)
        day_str = (day_iso_prefix or get_brt_now().date().isoformat())[:10]
        with self.db.readonly() as conn:
            row = conn.execute(
                """
                SELECT id FROM trades
                WHERE user_id = ? AND wallet_id = ? AND ticker = ?
                  AND action = ? AND substr(timestamp, 1, 10) = ?
                LIMIT 1
                """,
                (int(user_id), int(wallet_id), clean_tk, action.strip().upper(), day_str),
            ).fetchone()
        return row is not None

    def find_trade_by_client_order_id(
        self,
        user_id: int,
        wallet_id: int,
        client_order_id: str,
    ) -> dict[str, Any] | None:
        clean_id = (client_order_id or "").strip()
        if not clean_id:
            return None
        with self.db.readonly() as conn:
            row = conn.execute(
                """
                SELECT * FROM trades
                WHERE user_id = ? AND wallet_id = ? AND client_order_id = ?
                LIMIT 1
                """,
                (int(user_id), int(wallet_id), clean_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def get_trade_stats(self, user_id: int, wallet_id: int) -> dict[str, Any]:
        with self.db.readonly() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_trades,
                    SUM(CASE WHEN action = 'SELL' THEN 1 ELSE 0 END) AS sell_trades,
                    SUM(CASE WHEN action = 'SELL' AND realized_pnl > 0 THEN 1 ELSE 0 END) AS winning_sells,
                    COALESCE(SUM(CASE WHEN action = 'SELL' THEN realized_pnl ELSE 0.0 END), 0.0) AS realized_pnl_total,
                    COALESCE(SUM(fee), 0.0) AS total_fees
                FROM trades
                WHERE user_id = ? AND wallet_id = ?
                """,
                (int(user_id), int(wallet_id)),
            ).fetchone()
        total = int(row["total_trades"] or 0) if row else 0
        sells = int(row["sell_trades"] or 0) if row else 0
        wins = int(row["winning_sells"] or 0) if row else 0
        win_rate = round((wins / sells) * 100.0, 2) if sells > 0 else 0.0
        return {
            "total_trades": total,
            "sell_trades": sells,
            "winning_sells": wins,
            "win_rate": win_rate,
            "realized_pnl_total": round(float(row["realized_pnl_total"] or 0.0), 2) if row else 0.0,
            "total_fees": round(float(row["total_fees"] or 0.0), 2) if row else 0.0,
        }

    def record_decision(
        self,
        user_id: int,
        wallet_id: int,
        ticker: str,
        action: str,
        confidence: float,
        trend_signal: str,
        sentiment_score: float,
        reasoning: str,
    ) -> None:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO ai_decisions (
                    user_id, wallet_id, ticker, action, confidence,
                    trend_signal, sentiment_score, reasoning, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    int(wallet_id),
                    normalize_b3_ticker(ticker) or "MARKET",
                    str(action).strip().upper()[:24],
                    round(float(confidence), 4),
                    str(trend_signal)[:24],
                    round(float(sentiment_score), 4),
                    str(reasoning)[:500],
                    now_iso,
                ),
            )

    def list_decisions(self, user_id: int, wallet_id: int, limit: int = 50) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 250))
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT * FROM ai_decisions
                WHERE user_id = ? AND wallet_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (int(user_id), int(wallet_id), capped),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_equity_snapshot(
        self,
        user_id: int,
        wallet_id: int,
        total_equity: float,
        operational_equity: float,
        free_cash: float,
        allocated_capital: float,
        vault_reserve_cash: float,
        pnl_percent: float,
        daily_pnl_percent: float,
    ) -> None:
        now = get_brt_now()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO equity_snapshots (
                    user_id, wallet_id, total_equity, operational_equity, free_cash,
                    allocated_capital, vault_reserve_cash, pnl_percent, daily_pnl_percent,
                    snapshot_date, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    int(wallet_id),
                    round(float(total_equity), 2),
                    round(float(operational_equity), 2),
                    round(float(free_cash), 2),
                    round(float(allocated_capital), 2),
                    round(float(vault_reserve_cash), 2),
                    round(float(pnl_percent), 4),
                    round(float(daily_pnl_percent), 4),
                    now.date().isoformat(),
                    now.isoformat(),
                ),
            )

    def get_day_baseline_operational_equity(
        self,
        user_id: int,
        wallet_id: int,
        snapshot_date: str | None = None,
    ) -> float | None:
        day_str = (snapshot_date or get_brt_now().date().isoformat())[:10]
        with self.db.readonly() as conn:
            row = conn.execute(
                """
                SELECT operational_equity FROM equity_snapshots
                WHERE user_id = ? AND wallet_id = ? AND snapshot_date = ?
                ORDER BY id ASC
                LIMIT 1
                """,
                (int(user_id), int(wallet_id), day_str),
            ).fetchone()
        return float(row["operational_equity"]) if row is not None else None

    def list_equity_curve(self, user_id: int, wallet_id: int, limit: int = 300) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 1000))
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM equity_snapshots
                    WHERE user_id = ? AND wallet_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                ) ORDER BY id ASC
                """,
                (int(user_id), int(wallet_id), capped),
            ).fetchall()
        return [dict(r) for r in rows]
