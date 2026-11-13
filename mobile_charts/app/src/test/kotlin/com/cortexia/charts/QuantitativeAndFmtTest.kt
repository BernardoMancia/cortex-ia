package com.cortexia.charts

import com.cortexia.charts.data.BundleSynthesizer
import com.cortexia.charts.data.ChartsJson
import com.cortexia.charts.data.EnvTarget
import com.cortexia.charts.data.LegacyCapitalResponse
import com.cortexia.charts.data.LegacyDailyReport
import com.cortexia.charts.data.LegacyDecision
import com.cortexia.charts.data.LegacyPosition
import com.cortexia.charts.data.LegacyStatusResponse
import com.cortexia.charts.data.LegacyTrade
import com.cortexia.charts.data.WalletInfo
import com.cortexia.charts.ui.Fmt
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

class QuantitativeAndFmtTest {

    @Test
    fun formattingBrlPercentagesAndDatesProducesPtBrOutput() {
        val brl = Fmt.brl(12345.67)
        assertTrue(brl.contains("R$"))
        assertTrue(brl.contains("12.345,67"))

        assertEquals("+R$ 150,00", Fmt.brlSigned(150.0).replace('\u00A0', ' '))
        assertEquals("-R$ 42,50", Fmt.brlSigned(-42.5).replace('\u00A0', ' '))
        assertEquals("R$ 25,4k", Fmt.brlCompact(25400.0))
        assertEquals("+3,45%", Fmt.pct(3.45, signed = true))
        assertEquals("14,20%", Fmt.pct(14.2, signed = false))
        assertEquals("1,85", Fmt.ratio(1.85))
        assertEquals("15/03", Fmt.dayShort("2026-03-15T14:20:00"))
        assertEquals("Nov", Fmt.monthLabel(11))
    }

    @Test
    fun businessDaysSkipWeekendsForD2Settlement() {
        val friday = LocalDate.of(2026, 11, 13)
        val tuesday = BundleSynthesizer.addBusinessDays(friday, 2)
        assertEquals(LocalDate.of(2026, 11, 17), tuesday)

        val thursday = LocalDate.of(2026, 11, 12)
        val monday = BundleSynthesizer.addBusinessDays(thursday, 2)
        assertEquals(LocalDate.of(2026, 11, 16), monday)
    }

    @Test
    fun legacySynthesizerComputesMetricsFifoTaxAndD2Accurately() {
        val today = LocalDate.of(2026, 11, 13)
        val status = LegacyStatusResponse(
            balance = 4200.0,
            equity = 11200.0,
            operational_equity = 10200.0,
            vault_reserve_cash = 1000.0,
            initial_capital = 10000.0,
            positions = listOf(
                LegacyPosition(
                    ticker = "PETR4",
                    quantity = 100.0,
                    entry_price = 36.0,
                    current_price = 40.0,
                    stop_loss = 38.0,
                    highest_price = 41.0,
                    pnl = 400.0,
                    pnl_percent = 11.11,
                ),
                LegacyPosition(
                    ticker = "ITUB4",
                    quantity = 100.0,
                    entry_price = 30.0,
                    current_price = 30.0,
                    stop_loss = 28.5,
                    pnl = 0.0,
                    pnl_percent = 0.0,
                ),
            ),
            market_status = "ABERTO",
            bova11_change_pct = 0.85,
            regime_bearish_veto = false,
            environment = "production",
            is_simulation = false,
        )

        val capitalObj = ChartsJson.parseToJsonElement(
            """{"free_cash": 4200.0, "vault_reserve_cash": 1000.0, "total_equity": 11200.0, "initial_capital": 10000.0}"""
        ) as JsonObject
        val capital = LegacyCapitalResponse(success = true, capital = capitalObj, environment = "production", is_simulation = false)

        val reports = listOf(
            LegacyDailyReport(date = "2026-10-30", total_equity = 10000.0, initial_capital = 10000.0, daily_pnl_percent = 0.0),
            LegacyDailyReport(date = "2026-11-05", total_equity = 10800.0, initial_capital = 10000.0, buys_count = 2),
            LegacyDailyReport(date = "2026-11-10", total_equity = 10500.0, initial_capital = 10000.0, sells_count = 1),
            LegacyDailyReport(date = "2026-11-13", total_equity = 11200.0, initial_capital = 10000.0, sells_count = 1),
        )

        val trades = listOf(
            LegacyTrade(id = 1, ticker = "VALE3F", action = "BUY", quantity = 50.0, price = 60.0, total_value = 3000.0, timestamp = "2026-11-03T11:00:00"),
            LegacyTrade(id = 2, ticker = "VALE3", action = "SELL", quantity = 50.0, price = 66.0, total_value = 3300.0, fees = 5.0, timestamp = "2026-11-06T15:00:00"),
            LegacyTrade(id = 3, ticker = "BBDC4", action = "BUY", quantity = 100.0, price = 15.0, total_value = 1500.0, timestamp = "2026-11-10T11:00:00"),
            LegacyTrade(id = 4, ticker = "BBDC4", action = "SELL", quantity = 100.0, price = 14.0, total_value = 1400.0, fees = 5.0, timestamp = "2026-11-13T14:00:00"),
        )

        val decisions = listOf(
            LegacyDecision(id = 10, ticker = "PETR4", action = "BUY", confidence = 0.86, rsi = 44.0, sentiment_score = 0.62),
            LegacyDecision(id = 11, ticker = "BBDC4", action = "SELL", confidence = 0.74, rsi = 73.0, sentiment_score = -0.45),
            LegacyDecision(id = 12, ticker = "WEGE3", action = "HOLD", confidence = 0.52, rsi = 51.0, sentiment_score = 0.05),
        )

        val bundle = BundleSynthesizer.fromLegacy(
            env = EnvTarget.PRODUCTION,
            walletId = 1,
            wallets = listOf(WalletInfo(id = 1, name = "B3 Real", connector_type = "b3", operational_mode = "AUTONOMOUS", is_selected = true)),
            status = status,
            capital = capital,
            reports = reports,
            trades = trades,
            decisions = decisions,
            todayOverride = today,
        )

        assertEquals("production", bundle.environment)
        assertTrue(bundle.is_production)
        assertFalse(bundle.is_simulation)
        assertEquals(295.0 - 105.0, bundle.kpi_summary.realized_pnl_total, 0.01)
        assertEquals(50.0, bundle.kpi_summary.win_rate_pct, 0.01)
        assertEquals(4700.0, bundle.kpi_summary.tax_sold_month_brl, 0.01)
        assertEquals(23.5, bundle.kpi_summary.tax_utilization_pct, 0.01)
        assertEquals(15300.0, bundle.kpi_summary.tax_remaining_exempt_brl, 0.01)
        assertEquals(2, bundle.monthly_returns.size)
        assertEquals(2, bundle.cashflow_d2.size)
        val pendingItem = bundle.cashflow_d2.first { it.trade_date == "2026-11-13" }
        assertFalse(pendingItem.settled)
        assertEquals("2026-11-17", pendingItem.settlement_date)
        val settledItem = bundle.cashflow_d2.first { it.trade_date == "2026-11-06" }
        assertTrue(settledItem.settled)
        assertEquals(3, bundle.decisions_summary.total)
        assertEquals(1, bundle.decisions_summary.buy_count)
        assertEquals(1, bundle.decisions_summary.sell_count)
        assertEquals(1, bundle.decisions_summary.hold_count)
        assertEquals(5.0, bundle.positions.first { it.ticker == "PETR4" }.computedStopDistancePct ?: 0.0, 0.01)
    }
}
