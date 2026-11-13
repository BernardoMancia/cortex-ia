package com.cortexia.charts.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.charts.ui.ChartsUiState
import com.cortexia.charts.ui.Fmt
import com.cortexia.charts.ui.charts.DonutSlice
import com.cortexia.charts.ui.charts.InteractiveDonutChart
import com.cortexia.charts.ui.charts.PositionBulletChart
import com.cortexia.charts.ui.charts.SectorTreemapChart
import com.cortexia.charts.ui.charts.TreemapNode
import com.cortexia.charts.ui.components.ChartCard
import com.cortexia.charts.ui.components.FullscreenChartSpec
import com.cortexia.charts.ui.components.KpiTile
import com.cortexia.charts.ui.theme.ChartsColors

@Composable
fun AlocacaoTab(
    state: ChartsUiState,
    padding: PaddingValues,
    onExpandChart: (FullscreenChartSpec) -> Unit,
) {
    val bundle = state.bundle
    val kpi = bundle?.kpi_summary
    val positions = bundle?.positions.orEmpty()
    val sectors = bundle?.sector_exposure.orEmpty()

    var byTickerMode by rememberSaveable { mutableStateOf(false) }

    val donutSlices = if (!byTickerMode) {
        listOf(
            DonutSlice("Ações B3", kpi?.allocated_capital ?: 0.0, ChartsColors.Cyan, "Exposição em renda variável"),
            DonutSlice("Caixa Livre", kpi?.free_cash ?: 0.0, ChartsColors.Emerald, "Disponível operacional"),
            DonutSlice("Cofre Protegido", kpi?.vault_reserve_cash ?: 0.0, ChartsColors.Amber, "Lucro blindado"),
        )
    } else {
        buildList {
            positions.forEachIndexed { i, pos ->
                add(
                    DonutSlice(
                        label = pos.ticker,
                        value = pos.market_value,
                        color = ChartsColors.SeriesPalette[i % ChartsColors.SeriesPalette.size],
                        subtitle = "${pos.sector} • ${Fmt.pct(pos.pnl_percent)}",
                    )
                )
            }
            if ((kpi?.free_cash ?: 0.0) > 0.0) {
                add(DonutSlice("Caixa Livre", kpi?.free_cash ?: 0.0, ChartsColors.Emerald, "Liquidez imediata"))
            }
            if ((kpi?.vault_reserve_cash ?: 0.0) > 0.0) {
                add(DonutSlice("Cofre", kpi?.vault_reserve_cash ?: 0.0, ChartsColors.Amber, "Reserva blindada"))
            }
        }
    }

    val treemapNodes = sectors.map { sec ->
        val sectorCost = (sec.market_value - sec.unrealized_pnl).takeIf { it > 0.0 } ?: sec.market_value.coerceAtLeast(1.0)
        val pct = (sec.unrealized_pnl / sectorCost) * 100.0
        TreemapNode(
            title = sec.sector,
            sizeValue = sec.market_value,
            metricPct = pct,
            valueFormatted = Fmt.brl(sec.market_value),
            detailLine = "Peso: ${Fmt.pct(sec.weight_pct, signed = false)} • ${sec.positions_count} pos. • P&L: ${Fmt.brlSigned(sec.unrealized_pnl)}",
        )
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            KpiTile(
                label = "Capital Alocado",
                value = Fmt.brl(kpi?.allocated_capital),
                valueColor = ChartsColors.Cyan,
                sub = "${positions.size} posições abertas",
                modifier = Modifier.weight(1f),
            )
            KpiTile(
                label = "Caixa Livre",
                value = Fmt.brl(kpi?.free_cash),
                valueColor = ChartsColors.Emerald,
                sub = "Poder compra: ${Fmt.brl(kpi?.buying_power)}",
                modifier = Modifier.weight(1f),
            )
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            KpiTile(
                label = "Cofre Protegido",
                value = Fmt.brl(kpi?.vault_reserve_cash),
                valueColor = ChartsColors.Amber,
                sub = "Reserva fora de risco",
                modifier = Modifier.weight(1f),
            )
            KpiTile(
                label = "P&L Não Realizado",
                value = Fmt.brlSigned(kpi?.unrealized_pnl),
                valueColor = ChartsColors.signed(kpi?.unrealized_pnl ?: 0.0),
                sub = "Marcação a mercado",
                modifier = Modifier.weight(1f),
            )
        }

        ChartCard(
            title = "Distribuição de Alocação",
            subtitle = "Toque em uma fatia para destacar valor em R$ e participação %",
            headerExtra = {
                FilterChip(
                    selected = byTickerMode,
                    onClick = { byTickerMode = !byTickerMode },
                    label = { Text(if (byTickerMode) "Por Ativo" else "Macro", fontSize = 10.sp) },
                )
            },
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Distribuição de Alocação",
                        subtitle = if (byTickerMode) "Detalhamento por Ativo, Caixa e Cofre" else "Ações • Caixa Livre • Cofre Protegido",
                    ) { full ->
                        InteractiveDonutChart(
                            slices = donutSlices,
                            centerTitleWhenIdle = "Patrimônio",
                            isFullscreen = full,
                        )
                    }
                )
            },
        ) {
            InteractiveDonutChart(
                slices = donutSlices,
                centerTitleWhenIdle = "Patrimônio",
                isFullscreen = false,
            )
        }

        ChartCard(
            title = "Mapa Setorial da Carteira",
            subtitle = "Área proporcional ao capital alocado por setor B3 e cor pelo retorno",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Treemap Setorial da Carteira",
                        subtitle = "Exposição proporcional e resultado aberto por setor B3",
                    ) { full ->
                        SectorTreemapChart(nodes = treemapNodes, isFullscreen = full)
                    }
                )
            },
        ) {
            SectorTreemapChart(nodes = treemapNodes, isFullscreen = false)
        }

        ChartCard(
            title = "Régua Quantitativa de Posições",
            subtitle = "Stop-Loss → PM Entrada → Cotação Atual → Topo Trailing",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Régua de Risco das Posições Abertas",
                        subtitle = "Stop-Loss (vermelho) • Entrada (branco) • Atual (ciano/verde) • Topo (ouro)",
                    ) { full ->
                        PositionBulletChart(positions = positions, isFullscreen = full)
                    }
                )
            },
        ) {
            PositionBulletChart(positions = positions, isFullscreen = false)
        }

        Spacer(Modifier.height(8.dp))
    }
}
