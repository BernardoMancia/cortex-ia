package com.cortexia.monitor.alerts

import com.cortexia.monitor.data.DailyReport
import com.cortexia.monitor.data.Position
import com.cortexia.monitor.data.Trade
import kotlinx.serialization.Serializable
import java.util.Locale
import kotlin.math.abs
import kotlin.math.floor

enum class Severity { CRITICAL, WARNING, INFO }

enum class AlertKind(val severity: Severity, val label: String) {
    OFFLINE(Severity.CRITICAL, "Servidor offline"),
    RECOVERED(Severity.INFO, "Servidor online"),
    ENGINE(Severity.WARNING, "Motor da IA"),
    SESSION(Severity.WARNING, "Sessão"),
    TRADE(Severity.INFO, "Operação"),
    DAILY_LOSS(Severity.CRITICAL, "Perda diária"),
    POSITION_LOSS(Severity.WARNING, "Prejuízo em posição"),
    STOP_NEAR(Severity.WARNING, "Perto do stop"),
    EQUITY_UP(Severity.INFO, "Patrimônio subiu"),
    EQUITY_DOWN(Severity.WARNING, "Patrimônio caiu"),
    LOG_ERROR(Severity.CRITICAL, "Erro no motor"),
    MARKET(Severity.INFO, "Mercado B3"),
    ENVIRONMENT(Severity.WARNING, "Ambiente"),
}

data class Alert(val kind: AlertKind, val title: String, val message: String)

@Serializable
data class AlertRecord(
    val timestamp: Long,
    val kind: String,
    val severity: String,
    val title: String,
    val message: String,
)

@Serializable
data class AlertConfig(
    val offline: Boolean = true,
    val trades: Boolean = true,
    val dailyLoss: Boolean = true,
    val position: Boolean = true,
    val equity: Boolean = true,
    val logs: Boolean = true,
    val market: Boolean = true,
    val environment: Boolean = true,
    val dailyLossPct: Double = 3.0,
    val positionLossPct: Double = 5.0,
    val stopProximityPct: Double = 1.0,
    val equityChangePct: Double = 2.0,
)

@Serializable
data class AlertState(
    val initialized: Boolean = false,
    val consecutiveFailures: Int = 0,
    val offlineNotified: Boolean = false,
    val sessionNotified: Boolean = false,
    val engineUnknownNotified: Boolean = false,
    val lastMarketStatus: String? = null,
    val lastEnvironment: String? = null,
    val lastTradeId: Long = 0,
    val equityRef: Double? = null,
    val dayDate: String? = null,
    val dayBaseline: Double? = null,
    val dailyLossLevel: Int = 0,
    val positionLossLevels: Map<String, Int> = emptyMap(),
    val stopNotified: Set<String> = emptySet(),
    val lastLogTs: String? = null,
    val lastLogHashes: Set<Int> = emptySet(),
)

data class AlertInput(
    val marketStatus: String,
    val environment: String,
    val equity: Double,
    val positions: List<Position>,
    val trades: List<Trade>,
    val dailyReports: List<DailyReport>,
    val logs: List<String>,
)

object AlertRules {
    private val LOG_RE = Regex("""^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})]\s*\[([^\]]*)]\s*\[(\w+)]\s*(.*)$""")
    private const val MAX_TRADE_ALERTS = 5

    fun brl(v: Double): String = "R$ " + String.format(Locale.forLanguageTag("pt-BR"), "%,.2f", v)
    fun pct(v: Double): String = String.format(Locale.forLanguageTag("pt-BR"), "%+.2f%%", v)

    fun envLabel(env: String?) = if (env == "production") "PRODUÇÃO" else "HOMOLOGAÇÃO"

    fun onAuthExpired(state: AlertState): Pair<AlertState, List<Alert>> {
        if (state.sessionNotified) return state to emptyList()
        return state.copy(sessionNotified = true) to listOf(
            Alert(
                AlertKind.SESSION,
                "Sessão expirada",
                "Abra o Córtex Monitor e faça login novamente para continuar recebendo alertas.",
            )
        )
    }

    fun onFailure(
        state: AlertState,
        cfg: AlertConfig,
        error: String,
        threshold: Int,
    ): Pair<AlertState, List<Alert>> {
        val failures = state.consecutiveFailures + 1
        var next = state.copy(consecutiveFailures = failures)
        val alerts = mutableListOf<Alert>()
        if (failures >= threshold && !state.offlineNotified && cfg.offline) {
            alerts += Alert(
                AlertKind.OFFLINE,
                "Córtex IA sem resposta",
                "Não foi possível conectar ao servidor ($failures tentativa(s)). Detalhe: $error",
            )
            next = next.copy(offlineNotified = true)
        }
        return next to alerts
    }

    fun onSuccess(
        state: AlertState,
        cfg: AlertConfig,
        input: AlertInput,
        today: String,
    ): Pair<AlertState, List<Alert>> {
        val alerts = mutableListOf<Alert>()
        var s = state.copy(consecutiveFailures = 0, sessionNotified = false)

        if (state.offlineNotified) {
            s = s.copy(offlineNotified = false)
            if (cfg.offline) alerts += Alert(AlertKind.RECOVERED, "Córtex IA online novamente", "A conexão com o servidor foi restabelecida.")
        }

        val unknown = input.marketStatus.uppercase() !in setOf("ABERTO", "FECHADO")
        if (unknown && !s.engineUnknownNotified) {
            if (cfg.offline) alerts += Alert(
                AlertKind.ENGINE,
                "Motor da IA sem estado",
                "A API respondeu, mas o status do mercado está '${input.marketStatus}'. O motor de trading pode estar parado.",
            )
            s = s.copy(engineUnknownNotified = true)
        } else if (!unknown && s.engineUnknownNotified) {
            s = s.copy(engineUnknownNotified = false)
        }

        val envChanged = s.lastEnvironment != null && s.lastEnvironment != input.environment
        if (envChanged) {
            if (cfg.environment) alerts += Alert(
                AlertKind.ENVIRONMENT,
                "Ambiente alterado para ${envLabel(input.environment)}",
                "O Córtex IA passou de ${envLabel(s.lastEnvironment)} para ${envLabel(input.environment)}.",
            )
            s = s.copy(
                equityRef = null, dayBaseline = null, dayDate = null, dailyLossLevel = 0,
                positionLossLevels = emptyMap(), stopNotified = emptySet(),
            )
        }
        s = s.copy(lastEnvironment = input.environment)

        if (!unknown) {
            val prev = s.lastMarketStatus
            if (prev != null && prev != input.marketStatus.uppercase() && cfg.market) {
                alerts += if (input.marketStatus.uppercase() == "ABERTO") {
                    Alert(AlertKind.MARKET, "B3 abriu", "O pregão está ABERTO. O Córtex IA está operando.")
                } else {
                    Alert(AlertKind.MARKET, "B3 fechou", "O pregão está FECHADO.")
                }
            }
            s = s.copy(lastMarketStatus = input.marketStatus.uppercase())
        }

        val maxId = input.trades.mapNotNull { it.id }.maxOrNull() ?: 0L
        if (s.initialized) {
            val fresh = input.trades.filter { (it.id ?: 0L) > s.lastTradeId }.sortedBy { it.id ?: 0L }
            if (cfg.trades && fresh.isNotEmpty()) {
                fresh.take(MAX_TRADE_ALERTS).forEach { t ->
                    val verb = if (t.action.uppercase() == "BUY") "COMPRA" else "VENDA"
                    alerts += Alert(
                        AlertKind.TRADE,
                        "$verb ${t.ticker}",
                        "${t.quantity.toLong()} x ${brl(t.price)} = ${brl(t.total_value)}" +
                            (t.reasoning?.takeIf { it.isNotBlank() }?.let { "\n${it.take(160)}" } ?: ""),
                    )
                }
                if (fresh.size > MAX_TRADE_ALERTS) {
                    alerts += Alert(AlertKind.TRADE, "+${fresh.size - MAX_TRADE_ALERTS} operações", "Abra o app para ver todas as operações recentes.")
                }
            }
        }
        s = s.copy(lastTradeId = maxOf(s.lastTradeId, maxId))

        if (s.dayDate != today) {
            s = s.copy(dayDate = today, dayBaseline = null, dailyLossLevel = 0, positionLossLevels = emptyMap(), stopNotified = emptySet())
        }
        val prevClose = input.dailyReports
            .filter { it.date.take(10) < today && it.total_equity > 0 }
            .maxByOrNull { it.date }?.total_equity
        val baseline = prevClose ?: s.dayBaseline ?: input.equity.takeIf { it > 0 }
        s = s.copy(dayBaseline = baseline)
        if (baseline != null && baseline > 0 && input.equity > 0 && cfg.dailyLossPct > 0) {
            val dayPct = (input.equity - baseline) / baseline * 100.0
            val level = if (dayPct < 0) floor(-dayPct / cfg.dailyLossPct).toInt() else 0
            if (level > s.dailyLossLevel) {
                if (cfg.dailyLoss) alerts += Alert(
                    AlertKind.DAILY_LOSS,
                    "Perda diária de ${pct(dayPct)}",
                    "Patrimônio ${brl(input.equity)} vs. referência ${brl(baseline)} (limite: -${cfg.dailyLossPct}%).",
                )
                s = s.copy(dailyLossLevel = level)
            }
        }

        val levels = s.positionLossLevels.toMutableMap()
        val stops = s.stopNotified.toMutableSet()
        val held = input.positions.map { it.ticker }.toSet()
        levels.keys.retainAll(held)
        stops.retainAll(held)
        for (p in input.positions) {
            if (cfg.positionLossPct > 0 && p.pnl_percent < 0) {
                val lvl = floor(-p.pnl_percent / cfg.positionLossPct).toInt()
                if (lvl > (levels[p.ticker] ?: 0)) {
                    if (cfg.position) alerts += Alert(
                        AlertKind.POSITION_LOSS,
                        "${p.ticker} em ${pct(p.pnl_percent)}",
                        "Prejuízo de ${brl(abs(p.pnl))} (entrada ${brl(p.entry_price)}, atual ${brl(p.current_price)}).",
                    )
                    levels[p.ticker] = lvl
                }
            }
            val dist = p.stopDistancePct
            if (dist != null && cfg.stopProximityPct > 0) {
                if (dist <= cfg.stopProximityPct && p.ticker !in stops) {
                    if (cfg.position) alerts += Alert(
                        AlertKind.STOP_NEAR,
                        "${p.ticker} perto do stop-loss",
                        "Preço ${brl(p.current_price)} a ${String.format(Locale.forLanguageTag("pt-BR"), "%.2f", dist)}% do stop ${brl(p.stop_loss ?: 0.0)}.",
                    )
                    stops += p.ticker
                } else if (dist > cfg.stopProximityPct * 2) {
                    stops -= p.ticker
                }
            }
        }
        s = s.copy(positionLossLevels = levels, stopNotified = stops)

        if (input.equity > 0) {
            val ref = s.equityRef
            if (ref == null || ref <= 0) {
                s = s.copy(equityRef = input.equity)
            } else if (cfg.equityChangePct > 0) {
                val change = (input.equity - ref) / ref * 100.0
                if (abs(change) >= cfg.equityChangePct) {
                    if (cfg.equity) alerts += Alert(
                        if (change > 0) AlertKind.EQUITY_UP else AlertKind.EQUITY_DOWN,
                        "Patrimônio ${if (change > 0) "subiu" else "caiu"} ${pct(change)}",
                        "De ${brl(ref)} para ${brl(input.equity)}.",
                    )
                    s = s.copy(equityRef = input.equity)
                }
            }
        }

        val errorLines = input.logs.mapNotNull { line ->
            val m = LOG_RE.find(line.trim()) ?: return@mapNotNull null
            val level = m.groupValues[3].uppercase()
            if (level == "ERROR" || level == "CRITICAL") Triple(m.groupValues[1], line.trim(), m.groupValues[4]) else null
        }
        if (errorLines.isNotEmpty()) {
            val lastTs = s.lastLogTs
            val fresh = errorLines.filter { (ts, line, _) ->
                lastTs == null || ts > lastTs || (ts == lastTs && line.hashCode() !in s.lastLogHashes)
            }
            if (s.initialized && lastTs != null && fresh.isNotEmpty() && cfg.logs) {
                alerts += if (fresh.size == 1) {
                    Alert(AlertKind.LOG_ERROR, "Erro no motor do Córtex IA", fresh.first().third.take(300))
                } else {
                    Alert(AlertKind.LOG_ERROR, "${fresh.size} novos erros no motor", fresh.last().third.take(300))
                }
            }
            val newestTs = errorLines.maxOf { it.first }
            val effectiveTs = if (lastTs != null && lastTs > newestTs) lastTs else newestTs
            val hashes = errorLines.filter { it.first == effectiveTs }.map { it.second.hashCode() }.toSet() +
                (if (effectiveTs == lastTs) s.lastLogHashes else emptySet())
            s = s.copy(lastLogTs = effectiveTs, lastLogHashes = hashes.toList().takeLast(200).toSet())
        } else if (s.lastLogTs == null) {
            s = s.copy(lastLogTs = "0000-00-00 00:00:00")
        }

        return s.copy(initialized = true) to alerts
    }
}
