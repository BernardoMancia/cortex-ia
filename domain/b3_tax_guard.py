from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from domain.b3_rules import get_brt_now, is_b3_pf_20k_exempt_eligible_stock


@dataclass(frozen=True)
class TaxMonthlySummary:
    year_month: str
    gross_exempt_eligible_sales: float
    gross_non_exempt_sales: float
    exemption_limit: float
    guard_buffer: float
    remaining_exemption_headroom: float
    utilization_pct: float
    exemption_exceeded: bool
    realized_exempt_profit: float
    estimated_darf_brl: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "year_month": self.year_month,
            "gross_exempt_eligible_sales": round(self.gross_exempt_eligible_sales, 2),
            "gross_non_exempt_sales": round(self.gross_non_exempt_sales, 2),
            "exemption_limit": round(self.exemption_limit, 2),
            "guard_buffer": round(self.guard_buffer, 2),
            "remaining_exemption_headroom": round(self.remaining_exemption_headroom, 2),
            "utilization_pct": round(self.utilization_pct, 2),
            "exemption_exceeded": self.exemption_exceeded,
            "realized_exempt_profit": round(self.realized_exempt_profit, 2),
            "estimated_darf_brl": round(self.estimated_darf_brl, 2),
        }


class B3TaxGuard:
    def __init__(
        self,
        exemption_limit: float = 20000.0,
        guard_buffer: float = 19500.0,
        swing_tax_rate: float = 0.15,
    ) -> None:
        self.exemption_limit = max(1000.0, float(exemption_limit))
        self.guard_buffer = min(self.exemption_limit, max(500.0, float(guard_buffer)))
        self.swing_tax_rate = max(0.0, float(swing_tax_rate))

    @staticmethod
    def compute_fiscal_avg_price(
        prev_qty: int,
        prev_fiscal_avg: float,
        buy_qty: int,
        buy_price: float,
        b3_fee_rate: float = 0.0003,
    ) -> float:
        total_qty = max(0, int(prev_qty)) + max(0, int(buy_qty))
        if total_qty <= 0:
            return 0.0
        prev_cost = max(0, int(prev_qty)) * max(0.0, float(prev_fiscal_avg))
        new_gross = max(0, int(buy_qty)) * max(0.0, float(buy_price))
        new_cost_with_fee = new_gross * (1.0 + max(0.0, float(b3_fee_rate)))
        return round((prev_cost + new_cost_with_fee) / total_qty, 4)

    def summarize_month(
        self,
        month_sell_trades: list[dict[str, Any]],
        year_month: str | None = None,
    ) -> TaxMonthlySummary:
        ym = year_month or get_brt_now().strftime("%Y-%m")
        eligible_sales = 0.0
        non_exempt_sales = 0.0
        eligible_profit = 0.0
        non_exempt_profit = 0.0

        for tr in month_sell_trades:
            ticker = str(tr.get("ticker", ""))
            gross = float(tr.get("total_value", 0.0) or 0.0)
            pnl = float(tr.get("realized_pnl", 0.0) or 0.0)
            is_day_trade = bool(tr.get("is_day_trade", False))
            if not is_day_trade and is_b3_pf_20k_exempt_eligible_stock(ticker):
                eligible_sales += gross
                eligible_profit += pnl
            else:
                non_exempt_sales += gross
                non_exempt_profit += pnl

        exceeded = eligible_sales > self.exemption_limit
        taxable_stock_profit = max(0.0, eligible_profit) if exceeded else 0.0
        taxable_other_profit = max(0.0, non_exempt_profit)
        darf = (taxable_stock_profit + taxable_other_profit) * self.swing_tax_rate
        headroom = max(0.0, self.exemption_limit - eligible_sales)
        util_pct = (eligible_sales / self.exemption_limit) * 100.0 if self.exemption_limit > 0 else 0.0

        return TaxMonthlySummary(
            year_month=ym,
            gross_exempt_eligible_sales=eligible_sales,
            gross_non_exempt_sales=non_exempt_sales,
            exemption_limit=self.exemption_limit,
            guard_buffer=self.guard_buffer,
            remaining_exemption_headroom=headroom,
            utilization_pct=util_pct,
            exemption_exceeded=exceeded,
            realized_exempt_profit=eligible_profit,
            estimated_darf_brl=darf,
        )

    def should_defer_discretionary_profit_sell(
        self,
        ticker: str,
        projected_sale_gross: float,
        current_month_eligible_sales: float,
        protect_enabled: bool = True,
        is_stop_loss_or_emergency: bool = False,
    ) -> tuple[bool, str]:
        if is_stop_loss_or_emergency or not protect_enabled:
            return False, ""
        if not is_b3_pf_20k_exempt_eligible_stock(ticker):
            return False, ""
        if current_month_eligible_sales > self.exemption_limit:
            return False, ""
        projected_total = float(current_month_eligible_sales) + max(0.0, float(projected_sale_gross))
        if projected_total >= self.guard_buffer:
            return (
                True,
                f"Venda discricionária adiada pelo Guardião Tributário PF: projetaria R$ {projected_total:,.2f} em vendas no mês (preservando isenção de R$ {self.exemption_limit:,.2f}).",
            )
        return False, ""
