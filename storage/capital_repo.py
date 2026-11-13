from __future__ import annotations

from datetime import date
from typing import Any

from domain.b3_rules import add_b3_business_days, get_brt_now
from storage.connection import DatabaseConnection


class CapitalRepository:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()

    def record_transaction(
        self,
        user_id: int,
        wallet_id: int,
        tx_type: str,
        amount: float,
        resulting_ai_capital: float,
        resulting_vault: float,
        details: str = "",
    ) -> int:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO capital_transactions (
                    user_id, wallet_id, tx_type, amount,
                    resulting_ai_capital, resulting_vault, details, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    int(wallet_id),
                    tx_type.strip().upper()[:40],
                    round(float(amount), 2),
                    round(float(resulting_ai_capital), 2),
                    round(float(resulting_vault), 2),
                    (details or "").strip()[:320],
                    now_iso,
                ),
            )
            return int(cur.lastrowid or 0)

    def list_transactions(
        self,
        user_id: int,
        wallet_id: int,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 250))
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT * FROM capital_transactions
                WHERE user_id = ? AND wallet_id = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (int(user_id), int(wallet_id), capped),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_d2_net_activity(
        self,
        user_id: int,
        wallet_id: int,
        action: str,
        gross_amount: float,
        fee: float = 0.0,
        trade_date_str: str | None = None,
    ) -> None:
        now = get_brt_now()
        t_date_str = (trade_date_str or now.date().isoformat())[:10]
        t_date = date.fromisoformat(t_date_str)
        settle_str = add_b3_business_days(t_date, 2).isoformat()
        clean_act = action.strip().upper()
        delta = (float(gross_amount) - float(fee)) if clean_act == "SELL" else -(float(gross_amount) + float(fee))

        with self.db.transaction() as conn:
            existing = conn.execute(
                """
                SELECT id, net_sell_amount, settled
                FROM d2_settlements
                WHERE wallet_id = ? AND user_id = ? AND trade_date = ?
                """,
                (int(wallet_id), int(user_id), t_date_str),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO d2_settlements (
                        user_id, wallet_id, trade_date, settlement_date,
                        net_sell_amount, settled, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        int(user_id),
                        int(wallet_id),
                        t_date_str,
                        settle_str,
                        round(delta, 2),
                        now.isoformat(),
                    ),
                )
            else:
                new_net = round(float(existing["net_sell_amount"] or 0.0) + delta, 2)
                conn.execute(
                    """
                    UPDATE d2_settlements
                    SET net_sell_amount = ?, settled = 0, updated_at = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (new_net, now.isoformat(), int(existing["id"]), int(user_id)),
                )

    def settle_matured_d2(
        self,
        user_id: int,
        wallet_id: int,
        ref_date_str: str | None = None,
    ) -> float:
        today_str = (ref_date_str or get_brt_now().date().isoformat())[:10]
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            rows = conn.execute(
                """
                SELECT id, net_sell_amount FROM d2_settlements
                WHERE user_id = ? AND wallet_id = ? AND settled = 0 AND settlement_date <= ?
                """,
                (int(user_id), int(wallet_id), today_str),
            ).fetchall()
            matured_positive = sum(max(0.0, float(r["net_sell_amount"] or 0.0)) for r in rows)
            if rows:
                conn.execute(
                    """
                    UPDATE d2_settlements
                    SET settled = 1, updated_at = ?
                    WHERE user_id = ? AND wallet_id = ? AND settled = 0 AND settlement_date <= ?
                    """,
                    (now_iso, int(user_id), int(wallet_id), today_str),
                )
            return round(matured_positive, 2)

    def get_d2_summary(
        self,
        user_id: int,
        wallet_id: int,
        ref_date_str: str | None = None,
    ) -> dict[str, Any]:
        today_str = (ref_date_str or get_brt_now().date().isoformat())[:10]
        self.settle_matured_d2(user_id, wallet_id, ref_date_str=today_str)
        with self.db.readonly() as conn:
            rows = conn.execute(
                """
                SELECT trade_date, settlement_date, net_sell_amount
                FROM d2_settlements
                WHERE user_id = ? AND wallet_id = ? AND settled = 0
                ORDER BY settlement_date ASC
                """,
                (int(user_id), int(wallet_id)),
            ).fetchall()
        schedule = []
        pending_receivable = 0.0
        for r in rows:
            amt = round(float(r["net_sell_amount"] or 0.0), 2)
            if amt > 0:
                pending_receivable += amt
            schedule.append(
                {
                    "trade_date": str(r["trade_date"]),
                    "settlement_date": str(r["settlement_date"]),
                    "net_sell_amount": amt,
                }
            )
        return {
            "pending_d2_receivable": round(pending_receivable, 2),
            "schedule": schedule,
        }

    def create_withdrawal_goal(
        self,
        user_id: int,
        wallet_id: int,
        amount: float,
        mode: str,
        target_equity: float | None = None,
    ) -> dict[str, Any]:
        clean_mode = mode if mode in ("instant", "on_profit") else "instant"
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE withdrawal_goals
                SET status = 'CANCELLED', completed_at = ?
                WHERE user_id = ? AND wallet_id = ? AND status = 'ACTIVE'
                """,
                (now_iso, int(user_id), int(wallet_id)),
            )
            cur = conn.execute(
                """
                INSERT INTO withdrawal_goals (
                    user_id, wallet_id, amount, mode, target_equity, status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    int(user_id),
                    int(wallet_id),
                    round(float(amount), 2),
                    clean_mode,
                    round(float(target_equity), 2) if target_equity is not None else None,
                    now_iso,
                ),
            )
            gid = int(cur.lastrowid or 0)
            row = conn.execute("SELECT * FROM withdrawal_goals WHERE id = ?", (gid,)).fetchone()
        return dict(row) if row is not None else {}

    def get_active_withdrawal_goal(self, user_id: int, wallet_id: int) -> dict[str, Any] | None:
        with self.db.readonly() as conn:
            row = conn.execute(
                """
                SELECT * FROM withdrawal_goals
                WHERE user_id = ? AND wallet_id = ? AND status = 'ACTIVE'
                ORDER BY id DESC LIMIT 1
                """,
                (int(user_id), int(wallet_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def complete_withdrawal_goal(
        self,
        user_id: int,
        wallet_id: int,
        goal_id: int,
        status: str = "COMPLETED",
    ) -> None:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                UPDATE withdrawal_goals
                SET status = ?, completed_at = ?
                WHERE id = ? AND user_id = ? AND wallet_id = ?
                """,
                (status.strip().upper(), now_iso, int(goal_id), int(user_id), int(wallet_id)),
            )
