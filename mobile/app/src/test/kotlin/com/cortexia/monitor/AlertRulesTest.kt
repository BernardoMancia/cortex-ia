package com.cortexia.monitor

import com.cortexia.monitor.alerts.AlertConfig
import com.cortexia.monitor.alerts.AlertInput
import com.cortexia.monitor.alerts.AlertKind
import com.cortexia.monitor.alerts.AlertRules
import com.cortexia.monitor.alerts.AlertState
import com.cortexia.monitor.data.DailyReport
import com.cortexia.monitor.data.Position
import com.cortexia.monitor.data.Trade
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class AlertRulesTest {
    private val cfg = AlertConfig()
    private val today = "2026-11-12"

    private fun input(
        equity: Double = 100_000.0,
        market: String = "ABERTO",
        env: String = "homologation",
        positions: List<Position> = emptyList(),
        trades: List<Trade> = emptyList(),
        reports: List<DailyReport> = listOf(DailyReport(date = "2026-11-11", total_equity = 100_000.0)),
        logs: List<String> = emptyList(),
    ) = AlertInput(market, env, equity, positions, trades, reports, logs)

    private fun kinds(r: Pair<AlertState, List<com.cortexia.monitor.alerts.Alert>>) = r.second.map { it.kind }

    @Test
    fun firstRunSetsBaselinesWithoutNoise() {
        val r = AlertRules.onSuccess(
            AlertState(), cfg,
            input(trades = listOf(Trade(id = 7, ticker = "PETR4", action = "BUY", quantity = 100.0, price = 30.0, total_value = 3000.0))),
            today,
        )
        assertTrue(r.second.isEmpty())
        assertEquals(7L, r.first.lastTradeId)
        assertEquals("ABERTO", r.first.lastMarketStatus)
    }

    @Test
    fun newTradeMarketChangeAndEnvironmentChange() {
        val s0 = AlertRules.onSuccess(AlertState(), cfg, input(), today).first
        val r = AlertRules.onSuccess(
            s0, cfg,
            input(
                market = "FECHADO", env = "production",
                trades = listOf(Trade(id = 1, ticker = "VALE3", action = "SELL", quantity = 10.0, price = 60.0, total_value = 600.0)),
            ),
            today,
        )
        val k = kinds(r)
        assertTrue(AlertKind.TRADE in k)
        assertTrue(AlertKind.MARKET in k)
        assertTrue(AlertKind.ENVIRONMENT in k)
    }

    @Test
    fun dailyLossEscalatesByMultiples() {
        var s = AlertRules.onSuccess(AlertState(), cfg, input(), today).first
        val r1 = AlertRules.onSuccess(s, cfg, input(equity = 96_500.0), today)
        assertTrue(AlertKind.DAILY_LOSS in kinds(r1))
        s = r1.first
        val r2 = AlertRules.onSuccess(s, cfg, input(equity = 96_000.0), today)
        assertTrue(AlertKind.DAILY_LOSS !in kinds(r2))
        val r3 = AlertRules.onSuccess(r2.first, cfg, input(equity = 93_500.0), today)
        assertTrue(AlertKind.DAILY_LOSS in kinds(r3))
    }

    @Test
    fun positionLossAndStopProximityAreDeduplicated() {
        val p = Position("ITUB4", 100.0, 30.0, 28.2, stop_loss = 28.0, pnl = -180.0, pnl_percent = -6.0)
        val s0 = AlertRules.onSuccess(AlertState(), cfg.copy(equityChangePct = 50.0), input(), today).first
        val r1 = AlertRules.onSuccess(s0, cfg.copy(equityChangePct = 50.0), input(positions = listOf(p)), today)
        assertTrue(AlertKind.POSITION_LOSS in kinds(r1))
        assertTrue(AlertKind.STOP_NEAR in kinds(r1))
        val r2 = AlertRules.onSuccess(r1.first, cfg.copy(equityChangePct = 50.0), input(positions = listOf(p)), today)
        assertTrue(r2.second.none { it.kind == AlertKind.POSITION_LOSS || it.kind == AlertKind.STOP_NEAR })
    }

    @Test
    fun offlineAfterThresholdAndRecovery() {
        val s0 = AlertRules.onSuccess(AlertState(), cfg, input(), today).first
        val f1 = AlertRules.onFailure(s0, cfg, "timeout", threshold = 2)
        assertTrue(f1.second.isEmpty())
        val f2 = AlertRules.onFailure(f1.first, cfg, "timeout", threshold = 2)
        assertEquals(listOf(AlertKind.OFFLINE), kinds(f2))
        val f3 = AlertRules.onFailure(f2.first, cfg, "timeout", threshold = 2)
        assertTrue(f3.second.isEmpty())
        val ok = AlertRules.onSuccess(f3.first, cfg, input(), today)
        assertTrue(AlertKind.RECOVERED in kinds(ok))
    }

    @Test
    fun newErrorLogsOnly() {
        val old = "[2026-11-12 10:00:00] [cortex.engine] [ERROR] falha antiga"
        val s0 = AlertRules.onSuccess(AlertState(), cfg, input(logs = listOf(old)), today)
        assertTrue(AlertKind.LOG_ERROR !in kinds(s0))
        val fresh = "[2026-11-12 10:05:00] [cortex.broker] [CRITICAL] bridge caiu"
        val info = "[2026-11-12 10:06:00] [cortex.engine] [INFO] ciclo ok"
        val r = AlertRules.onSuccess(s0.first, cfg, input(logs = listOf(old, fresh, info)), today)
        val err = r.second.single { it.kind == AlertKind.LOG_ERROR }
        assertTrue(err.message.contains("bridge caiu"))
        val again = AlertRules.onSuccess(r.first, cfg, input(logs = listOf(old, fresh, info)), today)
        assertTrue(AlertKind.LOG_ERROR !in kinds(again))
    }

    @Test
    fun equityChangeMovesReference() {
        val s0 = AlertRules.onSuccess(AlertState(), cfg, input(equity = 100_000.0), today).first
        val up = AlertRules.onSuccess(s0, cfg, input(equity = 102_500.0), today)
        assertTrue(AlertKind.EQUITY_UP in kinds(up))
        assertEquals(102_500.0, up.first.equityRef!!, 0.001)
    }
}
