package com.cortexia.monitor.stats

import com.cortexia.monitor.data.DailyReport
import com.cortexia.monitor.data.Decision
import com.cortexia.monitor.data.Trade
import kotlin.math.sqrt

data class PerformanceStats(
    val days: Int,
    val periodReturnPct: Double,
    val cumulativeReturnPct: Double,
    val maxDrawdownPct: Double,
    val bestDayPct: Double,
    val bestDayDate: String,
    val worstDayPct: Double,
    val worstDayDate: String,
    val avgDailyPct: Double,
    val volatilityPct: Double,
    val sharpe: Double,
    val positiveDaysPct: Double,
    val totalBuys: Int,
    val totalSells: Int,
)

data class TickerPnl(val ticker: String, val pnl: Double, val trades: Int)

data class TradeStats(
    val totalTrades: Int,
    val buys: Int,
    val sells: Int,
    val volume: Double,
    val closedTrades: Int,
    val wins: Int,
    val losses: Int,
    val winRatePct: Double,
    val realizedPnl: Double,
    val avgWin: Double,
    val avgLoss: Double,
    val profitFactor: Double,
    val bestTrade: Double,
    val worstTrade: Double,
    val byTicker: List<TickerPnl>,
)

data class DecisionStats(
    val total: Int,
    val buy: Int,
    val sell: Int,
    val hold: Int,
    val avgConfidencePct: Double,
)

object Stats {

    fun dailyReturns(reports: List<DailyReport>): List<Pair<String, Double>> {
        val sorted = reports.filter { it.total_equity > 0 }.sortedBy { it.date }
        if (sorted.isEmpty()) return emptyList()
        val out = mutableListOf<Pair<String, Double>>()
        out += sorted.first().date to sorted.first().daily_pnl_percent
        for (i in 1 until sorted.size) {
            val prev = sorted[i - 1].total_equity
            out += sorted[i].date to (sorted[i].total_equity - prev) / prev * 100.0
        }
        return out
    }

    fun performance(reports: List<DailyReport>): PerformanceStats? {
        val sorted = reports.filter { it.total_equity > 0 }.sortedBy { it.date }
        if (sorted.isEmpty()) return null
        val rets = dailyReturns(sorted)
        val values = rets.map { it.second }
        val first = sorted.first().total_equity
        val last = sorted.last().total_equity

        var peak = sorted.first().total_equity
        var maxDd = 0.0
        for (r in sorted) {
            if (r.total_equity > peak) peak = r.total_equity
            val dd = (r.total_equity - peak) / peak * 100.0
            if (dd < maxDd) maxDd = dd
        }
        val avg = values.average()
        val vol = if (values.size > 1) sqrt(values.sumOf { (it - avg) * (it - avg) } / (values.size - 1)) else 0.0
        val best = rets.maxByOrNull { it.second }!!
        val worst = rets.minByOrNull { it.second }!!
        return PerformanceStats(
            days = sorted.size,
            periodReturnPct = (last - first) / first * 100.0,
            cumulativeReturnPct = sorted.last().pnl_percent,
            maxDrawdownPct = maxDd,
            bestDayPct = best.second,
            bestDayDate = best.first,
            worstDayPct = worst.second,
            worstDayDate = worst.first,
            avgDailyPct = avg,
            volatilityPct = vol,
            sharpe = if (vol > 0) avg / vol * sqrt(252.0) else 0.0,
            positiveDaysPct = values.count { it > 0 } * 100.0 / values.size,
            totalBuys = sorted.sumOf { it.buys_count },
            totalSells = sorted.sumOf { it.sells_count },
        )
    }

    fun baseTicker(t: String): String {
        val up = t.trim().uppercase()
        return if (up.length >= 6 && up.endsWith("F") && up[up.length - 2].isDigit()) up.dropLast(1) else up
    }

    fun trades(trades: List<Trade>): TradeStats {
        val sorted = trades.sortedWith(compareBy<Trade>({ it.timestamp }, { it.id ?: 0L }))
        val lots = HashMap<String, ArrayDeque<DoubleArray>>()
        val results = mutableListOf<Pair<String, Double>>()
        for (t in sorted) {
            val key = baseTicker(t.ticker)
            val queue = lots.getOrPut(key) { ArrayDeque() }
            if (t.action.uppercase() == "BUY") {
                queue.addLast(doubleArrayOf(t.quantity, t.price))
            } else if (t.action.uppercase() == "SELL") {
                var remaining = t.quantity
                var pnl = 0.0
                var matched = 0.0
                while (remaining > 0 && queue.isNotEmpty()) {
                    val lot = queue.first()
                    val q = minOf(lot[0], remaining)
                    pnl += (t.price - lot[1]) * q
                    matched += q
                    lot[0] -= q
                    remaining -= q
                    if (lot[0] <= 1e-9) queue.removeFirst()
                }
                if (matched > 0) results += key to pnl
            }
        }
        val wins = results.filter { it.second > 0 }
        val losses = results.filter { it.second < 0 }
        val grossWin = wins.sumOf { it.second }
        val grossLoss = -losses.sumOf { it.second }
        val byTicker = results.groupBy { it.first }
            .map { (k, v) -> TickerPnl(k, v.sumOf { it.second }, v.size) }
            .sortedByDescending { it.pnl }
        return TradeStats(
            totalTrades = trades.size,
            buys = trades.count { it.action.uppercase() == "BUY" },
            sells = trades.count { it.action.uppercase() == "SELL" },
            volume = trades.sumOf { it.total_value },
            closedTrades = results.size,
            wins = wins.size,
            losses = losses.size,
            winRatePct = if (results.isNotEmpty()) wins.size * 100.0 / results.size else 0.0,
            realizedPnl = results.sumOf { it.second },
            avgWin = if (wins.isNotEmpty()) grossWin / wins.size else 0.0,
            avgLoss = if (losses.isNotEmpty()) -grossLoss / losses.size else 0.0,
            profitFactor = if (grossLoss > 0) grossWin / grossLoss else if (grossWin > 0) Double.POSITIVE_INFINITY else 0.0,
            bestTrade = results.maxOfOrNull { it.second } ?: 0.0,
            worstTrade = results.minOfOrNull { it.second } ?: 0.0,
            byTicker = byTicker,
        )
    }

    fun decisions(decisions: List<Decision>): DecisionStats = DecisionStats(
        total = decisions.size,
        buy = decisions.count { it.action.uppercase() == "BUY" },
        sell = decisions.count { it.action.uppercase() == "SELL" },
        hold = decisions.count { it.action.uppercase() !in setOf("BUY", "SELL") },
        avgConfidencePct = if (decisions.isNotEmpty()) decisions.map { it.confidence }.average() * 100.0 else 0.0,
    )
}
