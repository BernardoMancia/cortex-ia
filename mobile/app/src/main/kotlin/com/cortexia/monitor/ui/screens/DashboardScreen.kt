package com.cortexia.monitor.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.monitor.data.Position
import com.cortexia.monitor.data.Snapshot
import com.cortexia.monitor.data.num
import com.cortexia.monitor.data.str
import com.cortexia.monitor.ui.Fmt
import com.cortexia.monitor.ui.UiState
import com.cortexia.monitor.ui.components.Badge
import com.cortexia.monitor.ui.components.KpiCard
import com.cortexia.monitor.ui.components.KpiGrid
import com.cortexia.monitor.ui.components.LineChart
import com.cortexia.monitor.ui.components.SectionCard
import com.cortexia.monitor.ui.components.StatusDot
import com.cortexia.monitor.ui.theme.CortexColors

@Composable
fun StatusHeader(state: UiState) {
    val snap = state.snapshot
    SectionCard(title = "Status do sistema") {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            StatusDot(if (state.offline) CortexColors.Negative else CortexColors.Positive)
            Text(
                if (state.offline) "Servidor sem resposta" else "Conectado",
                fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
            )
            Spacer(Modifier.weight(1f))
            Text("Atualizado ${Fmt.ago(state.lastUpdate)}", fontSize = 11.sp, color = CortexColors.TextMuted)
        }
        if (snap != null) {
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (snap.isProduction) Badge("PRODUÇÃO", CortexColors.Negative) else Badge("HOMOLOGAÇÃO", CortexColors.Warning)
                val market = snap.status.market_status.uppercase()
                Badge(
                    "B3 $market",
                    when (market) {
                        "ABERTO" -> CortexColors.Positive
                        "FECHADO" -> CortexColors.TextMuted
                        else -> CortexColors.Warning
                    },
                )
            }
        }
        if (state.offline && state.error != null) {
            Spacer(Modifier.height(6.dp))
            Text(state.error, fontSize = 12.sp, color = CortexColors.Negative)
        }
    }
}

fun todayResult(snap: Snapshot, today: String): Pair<Double, Double>? {
    val prev = snap.equityCurve.filter { it.date.take(10) < today && it.total_equity > 0 }.maxByOrNull { it.date }
        ?: return null
    val eq = snap.status.equity
    if (eq <= 0) return null
    return (eq - prev.total_equity) to (eq - prev.total_equity) / prev.total_equity * 100.0
}

@Composable
fun DashboardScreen(state: UiState, padding: PaddingValues, today: String) {
    val snap = state.snapshot
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = 12.dp, end = 12.dp,
            top = padding.calculateTopPadding() + 8.dp, bottom = padding.calculateBottomPadding() + 16.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item { StatusHeader(state) }
        if (snap == null) {
            item {
                Text(
                    if (state.loading) "Carregando dados…" else "Sem dados. Toque em atualizar.",
                    color = CortexColors.TextMuted, modifier = Modifier.padding(16.dp),
                )
            }
            return@LazyColumn
        }
        val cap = snap.capital?.capital
        val st = snap.status
        val dayRes = todayResult(snap, today)
        val lastReport = snap.equityCurve.maxByOrNull { it.date }
        item {
            KpiGrid(
                buildList<@Composable (Modifier) -> Unit> {
                    add { m -> KpiCard("Patrimônio total", Fmt.brl(st.equity), m) }
                    add { m -> KpiCard(if (snap.isProduction) "Capital da IA" else "Caixa livre", Fmt.brl(st.balance), m) }
                    add { m ->
                        KpiCard(
                            "Resultado hoje",
                            dayRes?.let { Fmt.brl(it.first) } ?: "—", m,
                            valueColor = CortexColors.signed(dayRes?.first ?: 0.0),
                            sub = dayRes?.let { Fmt.pct(it.second) },
                            subColor = CortexColors.signed(dayRes?.second ?: 0.0),
                        )
                    }
                    add { m ->
                        KpiCard(
                            "P&L não realizado", Fmt.brl(snap.unrealizedPnl), m,
                            valueColor = CortexColors.signed(snap.unrealizedPnl),
                            sub = "${st.positions.size} posição(ões)",
                        )
                    }
                    add { m ->
                        val v = cap.num("cumulative_realized_pnl")
                        KpiCard("P&L realizado", Fmt.brl(v), m, valueColor = CortexColors.signed(v ?: 0.0))
                    }
                    add { m ->
                        val v = lastReport?.pnl_percent
                        KpiCard(
                            "Retorno acumulado", Fmt.pct(v), m, valueColor = CortexColors.signed(v ?: 0.0),
                            sub = lastReport?.let { "ref. ${Fmt.dayShort(it.date)}" },
                        )
                    }
                    add { m -> KpiCard("Capital alocado", Fmt.brl(cap.num("allocated_capital")), m) }
                    add { m -> KpiCard("Poder de compra", Fmt.brl(cap.num("buying_power")), m) }
                    if (snap.isProduction) {
                        add { m -> KpiCard("Cofre de reserva", Fmt.brl(cap.num("vault_reserve_cash")), m) }
                        add { m ->
                            KpiCard(
                                "D+2 a liquidar", Fmt.brl(cap.num("unsettled_d2_cash")), m,
                                sub = cap.str("next_d2_settlement_date")?.let { "próx. ${Fmt.dayShort(it)}" },
                            )
                        }
                        add { m -> KpiCard("Sacável liquidado", Fmt.brl(cap.num("settled_withdrawable_cash")), m) }
                        add { m ->
                            val connected = cap.str("broker_connected")?.toBooleanStrictOrNull()
                            KpiCard(
                                "Corretora", when (connected) { true -> "Conectada"; false -> "Desconectada"; null -> "—" }, m,
                                valueColor = when (connected) { true -> CortexColors.Positive; false -> CortexColors.Negative; null -> CortexColors.TextMuted },
                            )
                        }
                    }
                }
            )
        }
        if (snap.equityCurve.size >= 2) {
            item {
                val curve = snap.equityCurve.sortedBy { it.date }
                SectionCard("Evolução do patrimônio", subtitle = "Últimos ${curve.size} pregões registrados") {
                    LineChart(
                        values = curve.map { it.total_equity },
                        labels = curve.map { Fmt.dayShort(it.date) },
                        height = 150.dp,
                        yFormatter = { Fmt.brlCompact(it) },
                        tooltip = { i -> "${Fmt.dayShort(curve[i].date)}: ${Fmt.brl(curve[i].total_equity)}" },
                    )
                }
            }
        }
        item {
            SectionCard("Posições abertas", subtitle = "${st.positions.size} ativo(s)") {
                if (st.positions.isEmpty()) Text("Nenhuma posição aberta.", color = CortexColors.TextMuted, fontSize = 13.sp)
                st.positions.sortedBy { it.pnl_percent }.forEachIndexed { i, p ->
                    if (i > 0) HorizontalDivider(color = CortexColors.Outline, modifier = Modifier.padding(vertical = 8.dp))
                    PositionRow(p, state.config.stopProximityPct)
                }
            }
        }
        if (st.recent_decisions.isNotEmpty()) {
            item {
                SectionCard("Últimas decisões da IA") {
                    st.recent_decisions.take(6).forEach { d ->
                        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                            Badge(d.action.uppercase(), actionColor(d.action))
                            Spacer(Modifier.width(8.dp))
                            Text(d.ticker, fontWeight = FontWeight.SemiBold, fontSize = 13.sp, modifier = Modifier.weight(1f))
                            Text("${(d.confidence * 100).toInt()}%", fontSize = 12.sp, color = CortexColors.TextMuted)
                            Spacer(Modifier.width(8.dp))
                            Text(Fmt.dateTime(d.timestamp), fontSize = 11.sp, color = CortexColors.TextMuted)
                        }
                    }
                }
            }
        }
    }
}

fun actionColor(action: String) = when (action.uppercase()) {
    "BUY" -> CortexColors.Positive
    "SELL" -> CortexColors.Negative
    else -> CortexColors.TextMuted
}

@Composable
fun PositionRow(p: Position, stopAlertPct: Double) {
    Column {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(p.ticker, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                Text(
                    "${p.quantity.toLong()} un · PM ${Fmt.brl(p.entry_price)} · Atual ${Fmt.brl(p.current_price)}",
                    fontSize = 11.sp, color = CortexColors.TextMuted,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(Fmt.brl(p.pnl), color = CortexColors.signed(p.pnl), fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                Text(Fmt.pct(p.pnl_percent), color = CortexColors.signed(p.pnl_percent), fontSize = 12.sp)
            }
        }
        val dist = p.stopDistancePct
        if (dist != null) {
            Spacer(Modifier.height(6.dp))
            val danger = dist <= stopAlertPct
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "Stop ${Fmt.brl(p.stop_loss)} · ${Fmt.pct(dist, signed = false)} de margem",
                    fontSize = 11.sp, color = if (danger) CortexColors.Negative else CortexColors.TextMuted,
                    modifier = Modifier.weight(1f),
                )
            }
            LinearProgressIndicator(
                progress = { (dist / 15.0).toFloat().coerceIn(0f, 1f) },
                modifier = Modifier.fillMaxWidth().padding(top = 4.dp).height(4.dp),
                color = if (danger) CortexColors.Negative else if (dist < stopAlertPct * 3) CortexColors.Warning else CortexColors.Positive,
                trackColor = CortexColors.SurfaceHigh,
            )
        }
    }
}
