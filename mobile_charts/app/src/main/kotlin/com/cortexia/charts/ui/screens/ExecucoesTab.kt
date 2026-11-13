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
import com.cortexia.charts.ui.charts.CashflowD2Chart
import com.cortexia.charts.ui.charts.HorizontalRankingChart
import com.cortexia.charts.ui.charts.RankingBarItem
import com.cortexia.charts.ui.charts.SemicircularGaugeChart
import com.cortexia.charts.ui.components.ChartCard
import com.cortexia.charts.ui.components.FullscreenChartSpec
import com.cortexia.charts.ui.components.KpiTile
import com.cortexia.charts.ui.components.StatKeyValueRow
import com.cortexia.charts.ui.theme.ChartsColors

@Composable
fun ExecucoesTab(
    state: ChartsUiState,
    padding: PaddingValues,
    onExpandChart: (FullscreenChartSpec) -> Unit,
) {
    val bundle = state.bundle
    val kpi = bundle?.kpi_summary
    val tickerRanking = bundle?.pnl_by_ticker.orEmpty().map { item ->
        RankingBarItem(
            label = item.ticker,
            value = item.total_pnl,
            detail = "${item.sector} • Real.: ${Fmt.brlSigned(item.realized_pnl)} • Aberto: ${Fmt.brlSigned(item.unrealized_pnl)} • ${item.trades_count} trades (WR ${Fmt.pct(item.win_rate_pct, signed = false, decimals = 0)})",
        )
    }
    val sectorRanking = bundle?.pnl_by_sector.orEmpty().map { item ->
        RankingBarItem(
            label = item.sector,
            value = item.total_pnl,
            detail = "Realizado: ${Fmt.brlSigned(item.realized_pnl)} • Aberto: ${Fmt.brlSigned(item.unrealized_pnl)} • ${item.trades_count} trades",
        )
    }
    val cashflow = bundle?.cashflow_d2.orEmpty()

    val taxUtil = kpi?.tax_utilization_pct ?: 0.0
    val taxSold = kpi?.tax_sold_month_brl ?: 0.0
    val taxLimit = kpi?.tax_limit_brl ?: 20000.0
    val taxBuffer = kpi?.tax_guard_buffer_brl ?: 19500.0
    val taxRem = kpi?.tax_remaining_exempt_brl ?: 20000.0

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
                label = "Win Rate",
                value = Fmt.pct(kpi?.win_rate_pct, signed = false),
                valueColor = if ((kpi?.win_rate_pct ?: 0.0) >= 50.0) ChartsColors.Emerald else ChartsColors.Amber,
                sub = "${kpi?.winning_sells ?: 0}W / ${kpi?.losing_sells ?: 0}L (${kpi?.total_trades ?: 0} ordens)",
                modifier = Modifier.weight(1f),
            )
            KpiTile(
                label = "Payoff / Profit Factor",
                value = Fmt.ratio(kpi?.payoff_ratio),
                valueColor = ChartsColors.Cyan,
                sub = "PF: ${Fmt.ratio(kpi?.profit_factor)}",
                modifier = Modifier.weight(1f),
            )
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            KpiTile(
                label = "P&L Realizado",
                value = Fmt.brlSigned(kpi?.realized_pnl_total),
                valueColor = ChartsColors.signed(kpi?.realized_pnl_total ?: 0.0),
                sub = "Taxas: ${Fmt.brl(kpi?.total_fees)}",
                modifier = Modifier.weight(1f),
            )
            KpiTile(
                label = "Ganho / Perda Média",
                value = Fmt.brl(kpi?.avg_win_brl),
                valueColor = ChartsColors.Emerald,
                sub = "Perda méd.: ${Fmt.brl(kpi?.avg_loss_brl)}",
                subColor = ChartsColors.Crimson,
                modifier = Modifier.weight(1f),
            )
        }

        ChartCard(
            title = "Termômetro Fiscal PF (Isenção R$ 20k/mês)",
            subtitle = "Zonas: Verde (<75%) • Âmbar (75%–97,5%) • Vermelha (≥97,5%)",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Termômetro Fiscal PF (R$ 20.000/mês)",
                        subtitle = "Vendido: ${Fmt.brl(taxSold)} • Restante Isento: ${Fmt.brl(taxRem)}",
                    ) { full ->
                        SemicircularGaugeChart(
                            value = taxUtil,
                            minValue = 0.0,
                            maxValue = 100.0,
                            primaryText = Fmt.pct(taxUtil, signed = false, decimals = 1),
                            secondaryText = "${Fmt.brl(taxSold)} de ${Fmt.brl(taxLimit)}",
                            zone1EndFraction = 0.75f,
                            zone2EndFraction = 0.975f,
                            isFullscreen = full,
                        )
                    }
                )
            },
        ) {
            SemicircularGaugeChart(
                value = taxUtil,
                minValue = 0.0,
                maxValue = 100.0,
                primaryText = Fmt.pct(taxUtil, signed = false, decimals = 1),
                secondaryText = "Vendido no mês: ${Fmt.brl(taxSold)}",
                zone1EndFraction = 0.75f,
                zone2EndFraction = 0.975f,
                isFullscreen = false,
            )
            Spacer(Modifier.height(6.dp))
            StatKeyValueRow("Limite Isenção PF Mensal", Fmt.brl(taxLimit))
            StatKeyValueRow("Trava Guard Buffer", Fmt.brl(taxBuffer), ChartsColors.Amber)
            StatKeyValueRow("Margem Isenta Restante", Fmt.brl(taxRem), ChartsColors.Emerald)
        }

        ChartCard(
            title = "Liquidação Financeira D+2",
            subtitle = "Caixa liquidado disponível vs vendas aguardando compensação B3",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Cronograma de Liquidação B3 (D+2)",
                        subtitle = "Liquidado vs Pendente D+2",
                    ) { full ->
                        CashflowD2Chart(
                            settledBrl = kpi?.settled_cash_brl ?: 0.0,
                            pendingD2Brl = kpi?.pending_d2_net_brl ?: 0.0,
                            schedule = cashflow,
                            isFullscreen = full,
                        )
                    }
                )
            },
        ) {
            CashflowD2Chart(
                settledBrl = kpi?.settled_cash_brl ?: 0.0,
                pendingD2Brl = kpi?.pending_d2_net_brl ?: 0.0,
                schedule = cashflow,
                isFullscreen = false,
            )
        }

        ChartCard(
            title = "Ranking P&L por Ativo",
            subtitle = "Barras divergentes (Realizado + Não Realizado)",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Ranking P&L por Ativo",
                        subtitle = "Toque em uma barra para ver P&L realizado, aberto e win rate",
                    ) { full ->
                        HorizontalRankingChart(items = tickerRanking, isFullscreen = full)
                    }
                )
            },
        ) {
            HorizontalRankingChart(items = tickerRanking.take(12), isFullscreen = false)
        }

        ChartCard(
            title = "Ranking P&L por Setor B3",
            subtitle = "Contribuição setorial consolidada no resultado",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Ranking P&L por Setor B3",
                        subtitle = "Resultado consolidado por segmento econômico",
                    ) { full ->
                        HorizontalRankingChart(items = sectorRanking, isFullscreen = full)
                    }
                )
            },
        ) {
            HorizontalRankingChart(items = sectorRanking, isFullscreen = false)
        }

        Spacer(Modifier.height(8.dp))
    }
}
