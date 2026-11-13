package com.cortexia.monitor.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.monitor.alerts.AlertRecord
import com.cortexia.monitor.ui.Fmt
import com.cortexia.monitor.ui.UiState
import com.cortexia.monitor.ui.components.Badge
import com.cortexia.monitor.ui.components.SectionCard
import com.cortexia.monitor.ui.theme.CortexColors

@Composable
fun AlertsScreen(state: UiState, padding: PaddingValues, onClear: () -> Unit, onTest: () -> Unit) {
    val h = state.history
    val dayAgo = System.currentTimeMillis() - 86_400_000L
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = 12.dp, end = 12.dp,
            top = padding.calculateTopPadding() + 8.dp, bottom = padding.calculateBottomPadding() + 16.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            SectionCard("Resumo das últimas 24h") {
                val recent = h.filter { it.timestamp >= dayAgo }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    Badge("${recent.count { it.severity == "CRITICAL" }} críticos", CortexColors.Negative)
                    Badge("${recent.count { it.severity == "WARNING" }} atenção", CortexColors.Warning)
                    Badge("${recent.count { it.severity == "INFO" }} info", CortexColors.Primary)
                }
                Spacer(Modifier.padding(top = 8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = onTest) { Text("Testar notificação") }
                    OutlinedButton(onClick = onClear, enabled = h.isNotEmpty()) { Text("Limpar histórico") }
                }
            }
        }
        if (h.isEmpty()) {
            item {
                Text(
                    "Nenhum alerta ainda. Os alertas aparecem aqui e como notificação no celular.",
                    color = CortexColors.TextMuted, fontSize = 13.sp, modifier = Modifier.padding(8.dp),
                )
            }
        }
        items(h) { AlertRow(it) }
    }
}

@Composable
private fun AlertRow(r: AlertRecord) {
    val (icon, color) = when (r.severity) {
        "CRITICAL" -> Icons.Filled.Warning to CortexColors.Negative
        "WARNING" -> Icons.Filled.Info to CortexColors.Warning
        else -> Icons.Filled.CheckCircle to CortexColors.Primary
    }
    SectionCard(title = r.title, subtitle = Fmt.clock(r.timestamp), trailing = {
        Icon(icon, null, tint = color, modifier = Modifier.size(20.dp))
    }) {
        Row(verticalAlignment = Alignment.Top) {
            Column(Modifier.weight(1f)) {
                Text(r.message, fontSize = 12.sp, color = CortexColors.TextMuted)
            }
            Spacer(Modifier.width(6.dp))
            Text(Fmt.ago(r.timestamp), fontSize = 10.sp, color = CortexColors.TextMuted, fontWeight = FontWeight.Medium)
        }
    }
}
