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
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.cortexia.charts.ui.ChartsUiState
import com.cortexia.charts.ui.Fmt
import com.cortexia.charts.ui.charts.ConfidenceHistogramChart
import com.cortexia.charts.ui.charts.DonutSlice
import com.cortexia.charts.ui.charts.InteractiveDonutChart
import com.cortexia.charts.ui.charts.RsiSentimentScatterChart
import com.cortexia.charts.ui.charts.SectorTreemapChart
import com.cortexia.charts.ui.charts.TreemapNode
import com.cortexia.charts.ui.components.ChartCard
import com.cortexia.charts.ui.components.FullscreenChartSpec
import com.cortexia.charts.ui.components.KpiTile
import com.cortexia.charts.ui.theme.ChartsColors

@Composable
fun RadarIaTab(
    state: ChartsUiState,
    padding: PaddingValues,
    onExpandChart: (FullscreenChartSpec) -> Unit,
) {
    val bundle = state.bundle
    val radar = bundle?.market_radar.orEmpty()
    val dec = bundle?.decisions_summary
    val bova = bundle?.bova11_change_pct ?: 0.0
    val veto = bundle?.regime_bearish_veto ?: false

    val marketTreemapNodes = radar
        .groupBy { it.sector.ifBlank { "Outros" } }
        .map { (sec, list) ->
            val avgChange = if (list.isNotEmpty()) list.map { it.change_pct }.average() else 0.0
            val sampleTickers = list.take(4).joinToString(", ") { it.ticker }
            TreemapNode(
                title = sec,
                sizeValue = list.size.toDouble().coerceAtLeast(1.0),
                metricPct = avgChange,
                valueFormatted = "${list.size} ativos",
                detailLine = "Média dia: ${Fmt.pct(avgChange)} • $sampleTickers",
            )
        }
        .let { nodes ->
            if (nodes.any { it.title.contains("Índice") || it.title.contains("BOVA11") }) nodes
            else listOf(
                TreemapNode(
                    title = "BOVA11",
                    sizeValue = 2.0,
                    metricPct = bova,
                    valueFormatted = "Benchmark",
                    detailLine = "Referência Ibovespa no pregão atual",
                )
            ) + nodes
        }

    val decisionSlices = listOf(
        DonutSlice("Compra (BUY)", (dec?.buy_count ?: 0).toDouble(), ChartsColors.Emerald, "${dec?.buy_count ?: 0} sinais de entrada"),
        DonutSlice("Venda (SELL)", (dec?.sell_count ?: 0).toDouble(), ChartsColors.Crimson, "${dec?.sell_count ?: 0} sinais de saída"),
        DonutSlice("Manter (HOLD)", (dec?.hold_count ?: 0).toDouble(), ChartsColors.Indigo, "${dec?.hold_count ?: 0} neutros/aguardando"),
    )

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
                label = "Benchmark BOVA11",
                value = Fmt.pct(bova),
                valueColor = ChartsColors.signed(bova),
                sub = "Pregão: ${bundle?.market_status ?: "—"}",
                modifier = Modifier.weight(1f),
            )
            KpiTile(
                label = "Regime / Veto Bearish",
                value = if (veto) "VETO ATIVO" else "LIBERADO",
                valueColor = if (veto) ChartsColors.Crimson else ChartsColors.Emerald,
                sub = "Conf. Média IA: ${Fmt.pct(dec?.avg_confidence_pct, signed = false, decimals = 1)}",
                modifier = Modifier.weight(1f),
            )
        }

        ChartCard(
            title = "Mapa Setorial do Mercado B3",
            subtitle = "BOVA11 + variação média intradiária dos setores monitorados",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Mapa Setorial do Mercado B3",
                        subtitle = "BOVA11 (${Fmt.pct(bova)}) e temperatura setorial",
                    ) { full ->
                        SectorTreemapChart(nodes = marketTreemapNodes, isFullscreen = full)
                    }
                )
            },
        ) {
            SectorTreemapChart(nodes = marketTreemapNodes, isFullscreen = false)
        }

        ChartCard(
            title = "Matriz RSI x Sentimento IA",
            subtitle = "RSI (0..100) vs Sentimento (-1..+1) • Toque em um ponto para detalhes",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Plano Cartesiano RSI x Sentimento IA",
                        subtitle = "Sobrevenda (<30) • Neutro (30–70) • Sobrecompra (>70)",
                    ) { full ->
                        RsiSentimentScatterChart(items = radar, isFullscreen = full)
                    }
                )
            },
        ) {
            RsiSentimentScatterChart(items = radar, isFullscreen = false)
        }

        ChartCard(
            title = "Distribuição de Decisões IA",
            subtitle = "Proporção de sinais Compra / Venda / Manter (${dec?.total ?: 0} avaliações)",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Funil de Decisões do Motor IA",
                        subtitle = "Total avaliado: ${dec?.total ?: 0} decisões",
                    ) { full ->
                        InteractiveDonutChart(
                            slices = decisionSlices,
                            valueFormatter = { "${it.toInt()} sinais" },
                            centerTitleWhenIdle = "Decisões",
                            isFullscreen = full,
                        )
                    }
                )
            },
        ) {
            InteractiveDonutChart(
                slices = decisionSlices,
                valueFormatter = { "${it.toInt()} sinais" },
                centerTitleWhenIdle = "Decisões",
                isFullscreen = false,
            )
        }

        ChartCard(
            title = "Histograma de Confiança IA",
            subtitle = "Distribuição de convicção estatística por faixa (confidence_buckets)",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Histograma de Confiança das Decisões IA",
                        subtitle = "Confiança média: ${Fmt.pct(dec?.avg_confidence_pct, signed = false, decimals = 1)}",
                    ) { full ->
                        ConfidenceHistogramChart(
                            buckets = dec?.confidence_buckets.orEmpty(),
                            isFullscreen = full,
                        )
                    }
                )
            },
        ) {
            ConfidenceHistogramChart(
                buckets = dec?.confidence_buckets.orEmpty(),
                isFullscreen = false,
            )
        }

        Spacer(Modifier.height(8.dp))
    }
}
