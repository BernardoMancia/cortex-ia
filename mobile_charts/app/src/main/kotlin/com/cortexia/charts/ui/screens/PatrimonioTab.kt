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
import com.cortexia.charts.ui.charts.DailyReturnBarChart
import com.cortexia.charts.ui.charts.MonthlyHeatmapGrid
import com.cortexia.charts.ui.charts.MultiSeriesAreaChart
import com.cortexia.charts.ui.charts.UnderwaterDrawdownChart
import com.cortexia.charts.ui.components.ChartCard
import com.cortexia.charts.ui.components.FullscreenChartSpec
import com.cortexia.charts.ui.components.KpiTile
import com.cortexia.charts.ui.components.PeriodChipsRow
import com.cortexia.charts.ui.theme.ChartsColors

@Composable
fun PatrimonioTab(
    state: ChartsUiState,
    padding: PaddingValues,
    onSelectDays: (Int) -> Unit,
    onExpandChart: (FullscreenChartSpec) -> Unit,
) {
    val bundle = state.bundle
    val kpi = bundle?.kpi_summary
    val series = bundle?.equity_series.orEmpty()
    val monthly = bundle?.monthly_returns.orEmpty()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(padding)
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 14.dp, vertical = 10.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        PeriodChipsRow(
            selectedDays = state.chartDays,
            onSelectDays = onSelectDays,
        )

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            KpiTile(
                label = "Patrimônio Total",
                value = Fmt.brl(kpi?.total_equity),
                sub = "Inicial: ${Fmt.brl(kpi?.initial_capital)}",
                modifier = Modifier.weight(1f),
            )
            KpiTile(
                label = "Operacional",
                value = Fmt.brl(kpi?.operational_equity),
                valueColor = ChartsColors.Cyan,
                sub = "Cofre: ${Fmt.brl(kpi?.vault_reserve_cash)}",
                subColor = ChartsColors.Amber,
                modifier = Modifier.weight(1f),
            )
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            val perRet = kpi?.period_return_pct ?: 0.0
            val cumRet = kpi?.cumulative_return_pct ?: 0.0
            KpiTile(
                label = "Retorno (${state.chartDays}d)",
                value = Fmt.pct(kpi?.period_return_pct),
                valueColor = ChartsColors.signed(perRet),
                sub = "Dia: ${Fmt.pct(kpi?.daily_return_pct)}",
                subColor = ChartsColors.signed(kpi?.daily_return_pct ?: 0.0),
                modifier = Modifier.weight(1f),
            )
            KpiTile(
                label = "Acumulado / Máx DD",
                value = Fmt.pct(kpi?.cumulative_return_pct),
                valueColor = ChartsColors.signed(cumRet),
                sub = "Máx DD: ${Fmt.pct(kpi?.max_drawdown_pct, signed = false)}",
                subColor = ChartsColors.Crimson,
                modifier = Modifier.weight(1f),
            )
        }

        ChartCard(
            title = "Evolução Patrimonial",
            subtitle = "Total vs Operacional vs Capital Inicial • Pinça para zoom e arraste para crosshair",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Evolução Patrimonial (${state.chartDays}D)",
                        subtitle = "Patrimônio Total • Operacional • Capital Inicial",
                    ) { full ->
                        MultiSeriesAreaChart(series = series, isFullscreen = full)
                    }
                )
            },
        ) {
            MultiSeriesAreaChart(series = series, isFullscreen = false)
        }

        ChartCard(
            title = "Curva Subaquática de Drawdown",
            subtitle = "Queda percentual desde o topo histórico (High-Water Mark)",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Drawdown Subaquático (%)",
                        subtitle = "HWM: ${Fmt.brl(kpi?.high_water_mark)} • Máx DD: ${Fmt.pct(kpi?.max_drawdown_pct, signed = false)}",
                    ) { full ->
                        UnderwaterDrawdownChart(
                            series = series,
                            maxDrawdownOverride = kpi?.max_drawdown_pct,
                            isFullscreen = full,
                        )
                    }
                )
            },
        ) {
            UnderwaterDrawdownChart(
                series = series,
                maxDrawdownOverride = kpi?.max_drawdown_pct,
                isFullscreen = false,
            )
        }

        ChartCard(
            title = "Variação Diária (%)",
            subtitle = "Retornos diários com linha zero e contagem de execuções",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Retornos Diários (%)",
                        subtitle = "Dias positivos: ${Fmt.pct(kpi?.positive_days_pct, signed = false)}",
                    ) { full ->
                        DailyReturnBarChart(series = series, isFullscreen = full)
                    }
                )
            },
        ) {
            DailyReturnBarChart(series = series, isFullscreen = false)
        }

        ChartCard(
            title = "Mapa de Calor Mensal",
            subtitle = "Grade Ano x Mês (Jan..Dez) colorida por intensidade de retorno",
            onExpand = {
                onExpandChart(
                    FullscreenChartSpec(
                        title = "Mapa de Calor Mensal (Ano x Mês)",
                        subtitle = "Toque em qualquer mês para inspecionar P&L em R$ e nº de trades",
                    ) { full ->
                        MonthlyHeatmapGrid(items = monthly, isFullscreen = full)
                    }
                )
            },
        ) {
            MonthlyHeatmapGrid(items = monthly, isFullscreen = false)
        }

        Spacer(Modifier.height(8.dp))
    }
}
