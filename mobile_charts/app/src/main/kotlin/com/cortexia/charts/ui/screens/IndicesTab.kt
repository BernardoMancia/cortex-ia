package com.cortexia.charts.ui.screens

import androidx.compose.foundation.BorderStroke
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.charts.BuildConfig
import com.cortexia.charts.data.EnvTarget
import com.cortexia.charts.ui.ChartsUiState
import com.cortexia.charts.ui.Fmt
import com.cortexia.charts.ui.charts.SemicircularGaugeChart
import com.cortexia.charts.ui.components.ChartCard
import com.cortexia.charts.ui.components.FullscreenChartSpec
import com.cortexia.charts.ui.components.KpiTile
import com.cortexia.charts.ui.components.StatKeyValueRow
import com.cortexia.charts.ui.theme.ChartsColors

@Composable
fun IndicesTab(
    state: ChartsUiState,
    padding: PaddingValues,
    onSelectRefreshSec: (Int) -> Unit,
    onSwitchEnv: (EnvTarget) -> Unit,
    onSelectWallet: (Int) -> Unit,
    onLogout: () -> Unit,
    onExpandChart: (FullscreenChartSpec) -> Unit,
) {
    val bundle = state.bundle
    val kpi = bundle?.kpi_summary

    val sharpe = kpi?.sharpe_annualized ?: 0.0
    val sortino = kpi?.sortino_annualized ?: 0.0
    val calmar = kpi?.calmar_ratio ?: 0.0
    val pf = (kpi?.profit_factor ?: 0.0).coerceAtMost(5.0)
    val payoff = (kpi?.payoff_ratio ?: 0.0).coerceAtMost(5.0)
    val vol = kpi?.volatility_annualized_pct ?: 0.0

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
                label = "Expectativa / Trade",
                value = Fmt.brlSigned(kpi?.expectancy_brl),
                valueColor = ChartsColors.signed(kpi?.expectancy_brl ?: 0.0),
                sub = "Esperança matemática líquida",
                modifier = Modifier.weight(1f),
            )
            KpiTile(
                label = "Dias Positivos",
                value = Fmt.pct(kpi?.positive_days_pct, signed = false),
                valueColor = ChartsColors.Cyan,
                sub = "Consistência diária",
                modifier = Modifier.weight(1f),
            )
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            ChartCard(
                title = "Sharpe Anual.",
                subtitle = "Retorno / Risco Total",
                modifier = Modifier.weight(1f),
                onExpand = {
                    onExpandChart(
                        FullscreenChartSpec("Índice de Sharpe Anualizado", "Acima de 1,0 indica eficiência institucional") { full ->
                            SemicircularGaugeChart(
                                value = sharpe,
                                minValue = -1.0,
                                maxValue = 3.5,
                                primaryText = Fmt.ratio(sharpe),
                                secondaryText = "Sharpe (252 pregões)",
                                zone1EndFraction = 0.35f,
                                zone2EndFraction = 0.58f,
                                invertZones = true,
                                isFullscreen = full,
                            )
                        }
                    )
                },
            ) {
                SemicircularGaugeChart(
                    value = sharpe,
                    minValue = -1.0,
                    maxValue = 3.5,
                    primaryText = Fmt.ratio(sharpe),
                    secondaryText = "Meta ≥ 1,00",
                    zone1EndFraction = 0.35f,
                    zone2EndFraction = 0.58f,
                    invertZones = true,
                    height = 122.dp,
                )
            }

            ChartCard(
                title = "Sortino Anual.",
                subtitle = "Retorno / Risco Queda",
                modifier = Modifier.weight(1f),
                onExpand = {
                    onExpandChart(
                        FullscreenChartSpec("Índice de Sortino Anualizado", "Penaliza apenas volatilidade negativa") { full ->
                            SemicircularGaugeChart(
                                value = sortino,
                                minValue = -1.0,
                                maxValue = 4.5,
                                primaryText = Fmt.ratio(sortino),
                                secondaryText = "Sortino Anualizado",
                                zone1EndFraction = 0.32f,
                                zone2EndFraction = 0.55f,
                                invertZones = true,
                                isFullscreen = full,
                            )
                        }
                    )
                },
            ) {
                SemicircularGaugeChart(
                    value = sortino,
                    minValue = -1.0,
                    maxValue = 4.5,
                    primaryText = Fmt.ratio(sortino),
                    secondaryText = "Meta ≥ 1,50",
                    zone1EndFraction = 0.32f,
                    zone2EndFraction = 0.55f,
                    invertZones = true,
                    height = 122.dp,
                )
            }
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            ChartCard(
                title = "Calmar Ratio",
                subtitle = "Retorno / Máx DD",
                modifier = Modifier.weight(1f),
                onExpand = {
                    onExpandChart(
                        FullscreenChartSpec("Calmar Ratio", "Retorno anualizado dividido pelo Drawdown Máximo") { full ->
                            SemicircularGaugeChart(
                                value = calmar,
                                minValue = -0.5,
                                maxValue = 4.0,
                                primaryText = Fmt.ratio(calmar),
                                secondaryText = "Calmar Ratio",
                                zone1EndFraction = 0.30f,
                                zone2EndFraction = 0.55f,
                                invertZones = true,
                                isFullscreen = full,
                            )
                        }
                    )
                },
            ) {
                SemicircularGaugeChart(
                    value = calmar,
                    minValue = -0.5,
                    maxValue = 4.0,
                    primaryText = Fmt.ratio(calmar),
                    secondaryText = "Meta ≥ 1,00",
                    zone1EndFraction = 0.30f,
                    zone2EndFraction = 0.55f,
                    invertZones = true,
                    height = 122.dp,
                )
            }

            ChartCard(
                title = "Fator de Lucro",
                subtitle = "Lucro Bruto / Perda",
                modifier = Modifier.weight(1f),
                onExpand = {
                    onExpandChart(
                        FullscreenChartSpec("Fator de Lucro (Profit Factor)", "Soma dos ganhos dividida pelas perdas") { full ->
                            SemicircularGaugeChart(
                                value = pf,
                                minValue = 0.0,
                                maxValue = 3.5,
                                primaryText = Fmt.ratio(kpi?.profit_factor),
                                secondaryText = "Profit Factor",
                                zone1EndFraction = 0.30f,
                                zone2EndFraction = 0.48f,
                                invertZones = true,
                                isFullscreen = full,
                            )
                        }
                    )
                },
            ) {
                SemicircularGaugeChart(
                    value = pf,
                    minValue = 0.0,
                    maxValue = 3.5,
                    primaryText = Fmt.ratio(kpi?.profit_factor),
                    secondaryText = "Meta ≥ 1,40",
                    zone1EndFraction = 0.30f,
                    zone2EndFraction = 0.48f,
                    invertZones = true,
                    height = 122.dp,
                )
            }
        }

        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            ChartCard(
                title = "Payoff Ratio",
                subtitle = "Ganho Méd. / Perda Méd.",
                modifier = Modifier.weight(1f),
                onExpand = {
                    onExpandChart(
                        FullscreenChartSpec("Assimetria de Payoff", "Relação entre ganho médio e perda média por trade") { full ->
                            SemicircularGaugeChart(
                                value = payoff,
                                minValue = 0.0,
                                maxValue = 3.5,
                                primaryText = Fmt.ratio(kpi?.payoff_ratio),
                                secondaryText = "Payoff Ratio",
                                zone1EndFraction = 0.28f,
                                zone2EndFraction = 0.48f,
                                invertZones = true,
                                isFullscreen = full,
                            )
                        }
                    )
                },
            ) {
                SemicircularGaugeChart(
                    value = payoff,
                    minValue = 0.0,
                    maxValue = 3.5,
                    primaryText = Fmt.ratio(kpi?.payoff_ratio),
                    secondaryText = "Meta ≥ 1,20",
                    zone1EndFraction = 0.28f,
                    zone2EndFraction = 0.48f,
                    invertZones = true,
                    height = 122.dp,
                )
            }

            ChartCard(
                title = "Volatilidade",
                subtitle = "Desvio Anualizado",
                modifier = Modifier.weight(1f),
                onExpand = {
                    onExpandChart(
                        FullscreenChartSpec("Volatilidade Anualizada (%)", "Oscilação anualizada da curva de capital") { full ->
                            SemicircularGaugeChart(
                                value = vol,
                                minValue = 0.0,
                                maxValue = 50.0,
                                primaryText = Fmt.pct(vol, signed = false, decimals = 1),
                                secondaryText = "Volatilidade Anual",
                                zone1EndFraction = 0.45f,
                                zone2EndFraction = 0.75f,
                                invertZones = false,
                                isFullscreen = full,
                            )
                        }
                    )
                },
            ) {
                SemicircularGaugeChart(
                    value = vol,
                    minValue = 0.0,
                    maxValue = 50.0,
                    primaryText = Fmt.pct(vol, signed = false, decimals = 1),
                    secondaryText = "Risco anual",
                    zone1EndFraction = 0.45f,
                    zone2EndFraction = 0.75f,
                    invertZones = false,
                    height = 122.dp,
                )
            }
        }

        ChartCard(
            title = "Controle de Sessão, Ambiente e Carteira",
            subtitle = "Atualização automática e isolamento estrito Produção vs Homologação",
        ) {
            Text("Intervalo de Atualização Automática", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = ChartsColors.TextPrimary)
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                listOf(10, 15, 30, 60).forEach { sec ->
                    FilterChip(
                        selected = state.refreshIntervalSec == sec,
                        onClick = { onSelectRefreshSec(sec) },
                        label = { Text("${sec}s", fontSize = 12.sp) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = ChartsColors.Cyan.copy(alpha = 0.22f),
                            selectedLabelColor = ChartsColors.Cyan,
                        ),
                    )
                }
            }

            Spacer(Modifier.height(12.dp))
            Text("Alternância Rápida de Ambiente", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = ChartsColors.TextPrimary)
            Spacer(Modifier.height(6.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                EnvTarget.entries.forEach { target ->
                    val accent = ChartsColors.envAccent(target)
                    FilterChip(
                        selected = state.env == target,
                        onClick = { onSwitchEnv(target) },
                        label = { Text(target.shortLabel, fontSize = 12.sp, fontWeight = FontWeight.SemiBold) },
                        colors = FilterChipDefaults.filterChipColors(
                            selectedContainerColor = accent.copy(alpha = 0.22f),
                            selectedLabelColor = accent,
                        ),
                    )
                }
            }

            if (state.wallets.isNotEmpty()) {
                Spacer(Modifier.height(12.dp))
                Text("Carteira Ativa", fontSize = 12.sp, fontWeight = FontWeight.SemiBold, color = ChartsColors.TextPrimary)
                Spacer(Modifier.height(6.dp))
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    state.wallets.forEach { w ->
                        val active = state.activeWallet?.id == w.id
                        FilterChip(
                            selected = active,
                            onClick = { onSelectWallet(w.id) },
                            label = {
                                Text(
                                    "${w.name} • ${w.operational_mode} (${w.connector_type})",
                                    fontSize = 11.sp,
                                )
                            },
                        )
                    }
                }
            }

            Spacer(Modifier.height(12.dp))
            StatKeyValueRow("Usuário Autenticado", state.username.ifBlank { "—" })
            StatKeyValueRow("Ambiente Validado", bundle?.environment ?: state.env.wireName, ChartsColors.envAccent(state.env))
            StatKeyValueRow("Gerado no Servidor", Fmt.dateTimeShort(bundle?.generated_at))
            StatKeyValueRow("Última Sincronização", Fmt.clock(state.lastUpdatedMillis))
            StatKeyValueRow("Versão do Aplicativo", "Córtex Charts v${BuildConfig.VERSION_NAME}")

            Spacer(Modifier.height(12.dp))
            OutlinedButton(
                onClick = onLogout,
                modifier = Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(10.dp),
                border = BorderStroke(1.dp, ChartsColors.Crimson.copy(alpha = 0.7f)),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = ChartsColors.Crimson),
            ) {
                Text("Encerrar Sessão (${state.env.shortLabel})", fontWeight = FontWeight.SemiBold)
            }
        }

        Spacer(Modifier.height(8.dp))
    }
}
