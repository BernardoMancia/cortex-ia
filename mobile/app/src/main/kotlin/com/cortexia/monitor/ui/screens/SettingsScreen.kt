package com.cortexia.monitor.ui.screens

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.monitor.BuildConfig
import com.cortexia.monitor.alerts.AlertConfig
import com.cortexia.monitor.alerts.Notifier
import com.cortexia.monitor.ui.UiState
import com.cortexia.monitor.ui.components.SectionCard
import com.cortexia.monitor.ui.components.StatRow
import com.cortexia.monitor.ui.theme.CortexColors
import java.util.Locale

@Composable
fun SettingsScreen(
    state: UiState,
    padding: PaddingValues,
    onConfig: (AlertConfig) -> Unit,
    onFgRefresh: (Int) -> Unit,
    onBackground: (Boolean, Int) -> Unit,
    onRequestNotifications: () -> Unit,
    onLogout: () -> Unit,
) {
    val ctx = LocalContext.current
    val cfg = state.config
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = 12.dp, end = 12.dp,
            top = padding.calculateTopPadding() + 8.dp, bottom = padding.calculateBottomPadding() + 16.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        item {
            SectionCard("Sessão") {
                StatRow("Usuário", state.username.ifBlank { "—" })
                StatRow("Reconexão automática", if (state.remember) "Ativada" else "Desativada")
            }
        }
        item {
            SectionCard("Tipos de alerta") {
                ToggleRow("Servidor/IA offline", cfg.offline) { onConfig(cfg.copy(offline = it)) }
                ToggleRow("Novas compras/vendas", cfg.trades) { onConfig(cfg.copy(trades = it)) }
                ToggleRow("Perda diária", cfg.dailyLoss) { onConfig(cfg.copy(dailyLoss = it)) }
                ToggleRow("Prejuízo / perto do stop", cfg.position) { onConfig(cfg.copy(position = it)) }
                ToggleRow("Variação de patrimônio", cfg.equity) { onConfig(cfg.copy(equity = it)) }
                ToggleRow("Erros/CRITICAL nos logs", cfg.logs) { onConfig(cfg.copy(logs = it)) }
                ToggleRow("Abertura/fechamento da B3", cfg.market) { onConfig(cfg.copy(market = it)) }
                ToggleRow("Mudança de ambiente", cfg.environment) { onConfig(cfg.copy(environment = it)) }
            }
        }
        item {
            SectionCard("Limites dos alertas") {
                ThresholdSlider("Perda diária a partir de", cfg.dailyLossPct, 0.5f, 10f) { onConfig(cfg.copy(dailyLossPct = it)) }
                ThresholdSlider("Prejuízo por posição a partir de", cfg.positionLossPct, 1f, 20f) { onConfig(cfg.copy(positionLossPct = it)) }
                ThresholdSlider("Distância do stop-loss menor que", cfg.stopProximityPct, 0.2f, 5f) { onConfig(cfg.copy(stopProximityPct = it)) }
                ThresholdSlider("Variação de patrimônio de", cfg.equityChangePct, 0.5f, 10f) { onConfig(cfg.copy(equityChangePct = it)) }
                Text(
                    "Alertas de perda repetem a cada novo múltiplo do limite (ex.: -3%, -6%, -9%).",
                    fontSize = 11.sp, color = CortexColors.TextMuted,
                )
            }
        }
        item {
            SectionCard("Atualização") {
                var fg by remember(state.fgRefreshSec) { mutableFloatStateOf(state.fgRefreshSec.toFloat()) }
                Text("App aberto: a cada ${fg.toInt()} s", fontSize = 13.sp)
                Slider(value = fg, onValueChange = { fg = it }, valueRange = 5f..120f, onValueChangeFinished = { onFgRefresh(fg.toInt()) })
                ToggleRow("Monitorar em segundo plano", state.bgEnabled) { onBackground(it, state.bgIntervalMin) }
                if (state.bgEnabled) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf(15, 30, 60).forEach { m ->
                            FilterChip(
                                selected = state.bgIntervalMin == m,
                                onClick = { onBackground(true, m) },
                                label = { Text("$m min") },
                            )
                        }
                    }
                    Text(
                        "O Android limita verificações em segundo plano a no mínimo 15 min. Para não atrasar, libere o app da otimização de bateria.",
                        fontSize = 11.sp, color = CortexColors.TextMuted,
                    )
                }
            }
        }
        item {
            SectionCard("Permissões") {
                val notif = Notifier.canNotify(ctx)
                StatRow("Notificações", if (notif) "Permitidas" else "Bloqueadas", if (notif) CortexColors.Positive else CortexColors.Negative)
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(top = 6.dp)) {
                    if (!notif) OutlinedButton(onClick = onRequestNotifications) { Text("Permitir") }
                    OutlinedButton(onClick = {
                        val i = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
                        runCatching { ctx.startActivity(i) }
                    }) { Text("Otimização de bateria") }
                }
                OutlinedButton(onClick = {
                    val i = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName)
                    } else {
                        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${ctx.packageName}"))
                    }
                    runCatching { ctx.startActivity(i) }
                }) { Text("Canais de notificação") }
            }
        }
        item {
            Column(Modifier.fillMaxWidth()) {
                Button(
                    onClick = onLogout,
                    colors = ButtonDefaults.buttonColors(containerColor = CortexColors.Negative),
                    modifier = Modifier.fillMaxWidth(),
                ) { Text("Sair (encerra sessão e apaga a senha salva)") }
                Text(
                    "Córtex Monitor v${BuildConfig.VERSION_NAME} (build ${BuildConfig.VERSION_CODE}) · somente leitura",
                    fontSize = 11.sp, color = CortexColors.TextMuted, modifier = Modifier.padding(top = 8.dp),
                )
            }
        }
    }
}

@Composable
private fun ToggleRow(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, fontSize = 14.sp, modifier = Modifier.weight(1f))
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable
private fun ThresholdSlider(label: String, value: Double, min: Float, max: Float, onDone: (Double) -> Unit) {
    var v by remember(value) { mutableFloatStateOf(value.toFloat()) }
    Text("$label ${String.format(Locale.forLanguageTag("pt-BR"), "%.1f", v)}%", fontSize = 13.sp)
    Slider(
        value = v, onValueChange = { v = (it * 10).toInt() / 10f }, valueRange = min..max,
        onValueChangeFinished = { onDone(v.toDouble()) },
    )
}
