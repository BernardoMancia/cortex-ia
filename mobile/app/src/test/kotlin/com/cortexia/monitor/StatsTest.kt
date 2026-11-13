package com.cortexia.monitor

import com.cortexia.monitor.data.CortexJson
import com.cortexia.monitor.data.DailyReport
import com.cortexia.monitor.data.StatusResponse
import com.cortexia.monitor.data.Trade
import com.cortexia.monitor.data.TradesResponse
import com.cortexia.monitor.stats.Stats
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Test

class StatsTest {
    @Test
    fun fifoMatchingConsolidatesFractionalTicker() {
        val trades = listOf(
            Trade(id = 1, ticker = "PETR4", action = "BUY", quantity = 100.0, price = 30.0, timestamp = "2026-11-10T10:00:00-03:00"),
            Trade(id = 2, ticker = "PETR4F", action = "BUY", quantity = 10.0, price = 32.0, timestamp = "2026-11-10T11:00:00-03:00"),
            Trade(id = 3, ticker = "PETR4", action = "SELL", quantity = 110.0, price = 33.0, timestamp = "2026-11-11T10:00:00-03:00"),
            Trade(id = 4, ticker = "VALE3", action = "BUY", quantity = 10.0, price = 60.0, timestamp = "2026-11-11T10:00:00-03:00"),
            Trade(id = 5, ticker = "VALE3", action = "SELL", quantity = 10.0, price = 55.0, timestamp = "2026-11-12T10:00:00-03:00"),
        )
        val s = Stats.trades(trades)
        assertEquals(2, s.closedTrades)
        assertEquals(1, s.wins)
        assertEquals(1, s.losses)
        assertEquals(310.0 - 50.0, s.realizedPnl, 0.001)
        assertEquals("PETR4", s.byTicker.first().ticker)
    }

    @Test
    fun performanceDrawdownAndReturns() {
        val reports = listOf(
            DailyReport(date = "2026-11-09", total_equity = 100.0),
            DailyReport(date = "2026-11-10", total_equity = 110.0),
            DailyReport(date = "2026-11-11", total_equity = 99.0),
            DailyReport(date = "2026-11-12", total_equity = 105.0, pnl_percent = 5.0),
        )
        val p = Stats.performance(reports)
        assertNotNull(p)
        assertEquals(5.0, p!!.periodReturnPct, 0.001)
        assertEquals(-10.0, p.maxDrawdownPct, 0.001)
        assertEquals(10.0, p.bestDayPct, 0.001)
        assertEquals("2026-11-11", p.worstDayDate)
    }

    @Test
    fun parsesBackendPayloadsTolerantly() {
        val status = """{"balance": 1000, "equity": 1500.5, "positions": [{"ticker":"PETR4","quantity":100,
            "entry_price":30,"current_price":31,"stop_loss":null,"pnl":100,"pnl_percent":3.33}],
            "recent_decisions": [{"ticker":"VALE3","action":"HOLD","confidence":0.42,"timestamp":"2026-11-12T10:00:00-03:00",
            "environment":"homologation","is_simulated":true}],
            "market_status":"ABERTO","watchlist":["PETR4"],"environment":"homologation","is_simulation":true}"""
        val s = CortexJson.decodeFromString(StatusResponse.serializer(), status)
        assertEquals(1, s.positions.size)
        assertEquals(null, s.positions[0].stop_loss)
        val trades = """{"success":true,"trades":[{"id":3,"ticker":"PETR4","action":"BUY","quantity":100,"price":30.5,
            "total_value":3050,"stop_loss":27.4,"timestamp":"2026-11-12T10:00:00-03:00","reasoning":null,
            "environment":"homologation","is_simulated":1}],"count":1,"environment":"homologation"}"""
        val t = CortexJson.decodeFromString(TradesResponse.serializer(), trades)
        assertEquals(3L, t.trades[0].id)
    }
}
