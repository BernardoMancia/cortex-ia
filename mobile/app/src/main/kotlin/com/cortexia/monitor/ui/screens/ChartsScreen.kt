package com.cortexia.monitor.ui.screens

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.monitor.stats.Stats
import com.cortexia.monitor.ui.Fmt
import com.cortexia.monitor.ui.UiState
import com.cortexia.monitor.ui.components.BarChart
import com.cortexia.monitor.ui.components.DonutChart
import com.cortexia.monitor.ui.components.GroupedBarChart
import com.cortexia.monitor.ui.components.HorizontalBars
import com.cortexia.monitor.ui.components.KpiCard
import com.cortexia.monitor.ui.components.KpiGrid
import com.cortexia.monitor.ui.components.LineChart
import com.cortexia.monitor.ui.components.SectionCard
import com.cortexia.monitor.ui.components.StatRow
import com.cortexia.monitor.ui.theme.CortexColors

private val PERIODS = listOf(7, 30, 90, 180, 365)

@Composable
fun ChartsScreen(state: UiState, padding: PaddingValues, onDays: (Int) -> Unit) {
    val snap = state.snapshot
    val curve = remember(snap) { snap?.equityCurve?.filter { it.total_equity > 0 }?.sortedBy { it.date }.orEmpty() }
    val perf = remember(curve) { Stats.performance(curve) }
    val daily = remember(curve) { Stats.dailyReturns(curve) }
    val tradeStats = remember(snap) { snap?.let { Stats.trades(it.trades) } }
    val decStats = remember(snap) { snap?.let { Stats.decisions(it.decisions) } }

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = 12.dp, end = 12.dp,
            top = padding.calculateTopPadding() + 8.dp, bottom = padding.calculateBottomPadding() + 16.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                PERIODS.forEach { d ->
                    FilterChip(
                        selected = state.chartDays == d,
                        onClick = { onDays(d) },
                        label = { Text(if (d == 365) "1 ano" else "$d dias") },
                    )
                }
            }
        }
        if (snap == null) {
            item { Text("Sem dados.", color = CortexColors.TextMuted) }
            return@LazyColumn
        }

        if (perf != null) {
            item {
                KpiGrid(
                    listOf<@Composable (Modifier) -> Unit>(
                        { m -> KpiCard("Retorno no período", Fmt.pct(perf.periodReturnPct), m, valueColor = CortexColors.signed(perf.periodReturnPct), sub = "${perf.days} pregões") },
                        { m -> KpiCard("Drawdown máximo", Fmt.pct(perf.maxDrawdownPct), m, valueColor = CortexColors.signed(perf.maxDrawdownPct)) },
                        { m -> KpiCard("Dias positivos", Fmt.pct(perf.positiveDaysPct, signed = false), m) },
                        { m -> KpiCard("Sharpe (anualiz.)", Fmt.num(perf.sharpe), m, valueColor = CortexColors.signed(perf.sharpe)) },
                    )
                )
            }
        }

        item {
            SectionCard("Curva de patrimônio", subtitle = "Toque ou arraste no gráfico para ver valores") {
                LineChart(
                    values = curve.map { it.total_equity },
                    labels = curve.map { Fmt.dayShort(it.date) },
                    yFormatter = { Fmt.brlCompact(it) },
                    tooltip = { i -> "${Fmt.dayShort(curve[i].date)} · ${Fmt.brl(curve[i].total_equity)} · ${Fmt.pct(curve[i].pnl_percent)}" },
                )
            }
        }

        item {
            SectionCard("Retorno diário (%)") {
                BarChart(
                    values = daily.map { it.second },
                    labels = daily.map { Fmt.dayShort(it.first) },
                    yFormatter = { String.format(java.util.Locale.US, "%.1f%%", it) },
                    tooltip = { i -> "${Fmt.dayShort(daily[i].first)}: ${Fmt.pct(daily[i].second)}" },
                )
            }
        }

        if (curve.size >= 2) {
            item {
                val dd = remember(curve) {
                    var peak = curve.first().total_equity
                    curve.map { r -> peak = maxOf(peak, r.total_equity); (r.total_equity - peak) / peak * 100.0 }
                }
                SectionCard("Drawdown (%)", subtitle = "Queda em relação ao pico anterior") {
                    LineChart(
                        values = dd,
                        labels = curve.map { Fmt.dayShort(it.date) },
                        color = CortexColors.Negative,
                        height = 160.dp,
                        yFormatter = { String.format(java.util.Locale.US, "%.1f%%", it) },
                        tooltip = { i -> "${Fmt.dayShort(curve[i].date)}: ${Fmt.pct(dd[i])}" },
                        zeroLine = true,
                    )
                }
            }
        }

        item {
            val slices = buildList {
                snap.status.positions
                    .sortedByDescending { it.marketValue }
                    .forEach { add(it.ticker to it.marketValue) }
                if (snap.status.balance > 0) add("Caixa" to snap.status.balance)
            }.let { all -> if (all.size > 10) all.take(9) + ("Outros" to all.drop(9).sumOf { it.second }) else all }
            SectionCard("Alocação da carteira") {
                DonutChart(slices, valueFormatter = { Fmt.brl(it) })
            }
        }

        if (curve.isNotEmpty()) {
            item {
                SectionCard("Operações por dia", subtitle = "Compras x vendas (relatório diário)") {
                    GroupedBarChart(
                        a = curve.map { it.buys_count.toDouble() },
                        b = curve.map { it.sells_count.toDouble() },
                        labels = curve.map { Fmt.dayShort(it.date) },
                        nameA = "Compras", nameB = "Vendas",
                        height = 170.dp,
                    )
                }
            }
        }

        if (perf != null) {
            item {
                SectionCard("Estatísticas de desempenho") {
                    StatRow("Retorno acumulado (desde o início)", Fmt.pct(perf.cumulativeReturnPct), CortexColors.signed(perf.cumulativeReturnPct))
                    StatRow("Retorno médio diário", Fmt.pct(perf.avgDailyPct), CortexColors.signed(perf.avgDailyPct))
                    StatRow("Volatilidade diária", Fmt.pct(perf.volatilityPct, signed = false))
                    StatRow("Melhor dia (${Fmt.dayShort(perf.bestDayDate)})", Fmt.pct(perf.bestDayPct), CortexColors.signed(perf.bestDayPct))
                    StatRow("Pior dia (${Fmt.dayShort(perf.worstDayDate)})", Fmt.pct(perf.worstDayPct), CortexColors.signed(perf.worstDayPct))
                    StatRow("Compras no período", perf.totalBuys.toString())
                    StatRow("Vendas no período", perf.totalSells.toString())
                }
            }
        }

        if (tradeStats != null) {
            item {
                SectionCard("Estatísticas de operações", subtitle = "P&L realizado estimado por casamento FIFO (últimos 365 dias)") {
                    StatRow("Total de ordens", "${tradeStats.totalTrades} (${tradeStats.buys} C / ${tradeStats.sells} V)")
                    StatRow("Volume negociado", Fmt.brl(tradeStats.volume))
                    StatRow("Vendas encerradas", tradeStats.closedTrades.toString())
                    StatRow("Taxa de acerto", Fmt.pct(tradeStats.winRatePct, signed = false), if (tradeStats.winRatePct >= 50) CortexColors.Positive else CortexColors.Warning)
                    StatRow("Ganhos / Perdas", "${tradeStats.wins} / ${tradeStats.losses}")
                    StatRow("P&L realizado", Fmt.brl(tradeStats.realizedPnl), CortexColors.signed(tradeStats.realizedPnl))
                    StatRow("Ganho médio", Fmt.brl(tradeStats.avgWin), CortexColors.Positive)
                    StatRow("Perda média", Fmt.brl(tradeStats.avgLoss), CortexColors.Negative)
                    StatRow("Fator de lucro", Fmt.num(tradeStats.profitFactor))
                    StatRow("Melhor operação", Fmt.brl(tradeStats.bestTrade), CortexColors.signed(tradeStats.bestTrade))
                    StatRow("Pior operação", Fmt.brl(tradeStats.worstTrade), CortexColors.signed(tradeStats.worstTrade))
                }
            }
            if (tradeStats.byTicker.isNotEmpty()) {
                item {
                    val items = (tradeStats.byTicker.take(6) + tradeStats.byTicker.takeLast(4))
                        .distinctBy { it.ticker }
                        .sortedByDescending { it.pnl }
                        .map { it.ticker to it.pnl }
                    SectionCard("P&L realizado por ativo", subtitle = "Melhores e piores") {
                        HorizontalBars(items, valueFormatter = { Fmt.brl(it) })
                    }
                }
            }
        }

        if (decStats != null && decStats.total > 0) {
            item {
                SectionCard("Decisões da IA", subtitle = "Últimas ${decStats.total} decisões") {
                    KpiGrid(
                        listOf<@Composable (Modifier) -> Unit>(
                            { m -> KpiCard("Compra", decStats.buy.toString(), m, valueColor = CortexColors.Positive) },
                            { m -> KpiCard("Venda", decStats.sell.toString(), m, valueColor = CortexColors.Negative) },
                            { m -> KpiCard("Manter", decStats.hold.toString(), m) },
                            { m -> KpiCard("Confiança média", Fmt.pct(decStats.avgConfidencePct, signed = false), m, valueColor = CortexColors.Primary) },
                        )
                    )
                    Spacer(Modifier.height(10.dp))
                    DonutChart(
                        listOf("Compra" to decStats.buy.toDouble(), "Venda" to decStats.sell.toDouble(), "Manter" to decStats.hold.toDouble())
                            .filter { it.second > 0 },
                        valueFormatter = { "${it.toInt()} decisões" },
                    )
                }
            }
        }
        item {
            Text(
                "Os dados de patrimônio vêm dos relatórios diários do Córtex IA (${state.chartDays} dias solicitados).",
                fontSize = 11.sp, color = CortexColors.TextMuted, modifier = Modifier.fillMaxWidth().padding(4.dp),
            )
        }
    }
}
