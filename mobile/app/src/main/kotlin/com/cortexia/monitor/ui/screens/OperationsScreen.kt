package com.cortexia.monitor.ui.screens

import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.monitor.data.Decision
import com.cortexia.monitor.data.Trade
import com.cortexia.monitor.ui.Fmt
import com.cortexia.monitor.ui.UiState
import com.cortexia.monitor.ui.components.Badge
import com.cortexia.monitor.ui.components.SectionCard
import com.cortexia.monitor.ui.theme.CortexColors

private enum class OpsTab(val label: String) { TRADES("Ordens"), DECISIONS("Decisões"), LOGS("Logs") }

@Composable
fun OperationsScreen(state: UiState, padding: PaddingValues) {
    var tab by rememberSaveable { mutableStateOf(OpsTab.TRADES) }
    var onlyErrors by rememberSaveable { mutableStateOf(false) }
    var tickerFilter by rememberSaveable { mutableStateOf<String?>(null) }
    val snap = state.snapshot

    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(
            start = 12.dp, end = 12.dp,
            top = padding.calculateTopPadding() + 8.dp, bottom = padding.calculateBottomPadding() + 16.dp,
        ),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OpsTab.entries.forEach { t ->
                    FilterChip(selected = tab == t, onClick = { tab = t }, label = { Text(t.label) })
                }
            }
        }
        if (snap == null) {
            item { Text("Sem dados.", color = CortexColors.TextMuted) }
            return@LazyColumn
        }
        when (tab) {
            OpsTab.TRADES -> {
                val tickers = snap.trades.map { it.ticker }.distinct().sorted()
                if (tickers.size > 1) {
                    item {
                        androidx.compose.foundation.lazy.LazyRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            item { FilterChip(selected = tickerFilter == null, onClick = { tickerFilter = null }, label = { Text("Todos") }) }
                            items(tickers) { t ->
                                FilterChip(selected = tickerFilter == t, onClick = { tickerFilter = t }, label = { Text(t) })
                            }
                        }
                    }
                }
                val list = snap.trades.filter { tickerFilter == null || it.ticker == tickerFilter }
                if (list.isEmpty()) item { Text("Nenhuma ordem registrada.", color = CortexColors.TextMuted) }
                items(list, key = { it.id ?: it.hashCode().toLong() }) { TradeCard(it) }
            }
            OpsTab.DECISIONS -> {
                if (snap.decisions.isEmpty()) item { Text("Nenhuma decisão registrada.", color = CortexColors.TextMuted) }
                items(snap.decisions) { DecisionCard(it) }
            }
            OpsTab.LOGS -> {
                item {
                    FilterChip(selected = onlyErrors, onClick = { onlyErrors = !onlyErrors }, label = { Text("Somente WARNING/ERROR") })
                }
                val lines = snap.logs.reversed().filter {
                    !onlyErrors || it.contains("[ERROR]") || it.contains("[CRITICAL]") || it.contains("[WARNING]")
                }
                if (lines.isEmpty()) item { Text("Sem linhas de log.", color = CortexColors.TextMuted) }
                items(lines) { line ->
                    val color = when {
                        line.contains("[ERROR]") || line.contains("[CRITICAL]") -> CortexColors.Negative
                        line.contains("[WARNING]") -> CortexColors.Warning
                        line.contains("[DEBUG]") -> CortexColors.TextMuted
                        else -> CortexColors.TextPrimary
                    }
                    Text(line, color = color, fontSize = 11.sp, fontFamily = FontFamily.Monospace, lineHeight = 14.sp)
                }
            }
        }
    }
}

@Composable
private fun TradeCard(t: Trade) {
    var expanded by remember { mutableStateOf(false) }
    SectionCard(
        title = "${if (t.action.uppercase() == "BUY") "Compra" else "Venda"} · ${t.ticker}",
        subtitle = Fmt.dateTime(t.timestamp),
        modifier = Modifier.animateContentSize().clickable { expanded = !expanded },
        trailing = { Badge(t.action.uppercase(), actionColor(t.action)) },
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("${t.quantity.toLong()} × ${Fmt.brl(t.price)}", fontSize = 13.sp, modifier = Modifier.weight(1f))
            Text(Fmt.brl(t.total_value), fontWeight = FontWeight.Bold, fontSize = 14.sp)
        }
        if (t.stop_loss != null && t.stop_loss > 0) {
            Text("Stop: ${Fmt.brl(t.stop_loss)}", fontSize = 11.sp, color = CortexColors.TextMuted)
        }
        val reason = t.reasoning
        if (!reason.isNullOrBlank()) {
            Spacer(Modifier.height(4.dp))
            Text(
                if (expanded) reason else reason.take(110) + if (reason.length > 110) "…" else "",
                fontSize = 12.sp, color = CortexColors.TextMuted,
            )
        }
    }
}

@Composable
private fun DecisionCard(d: Decision) {
    var expanded by remember { mutableStateOf(false) }
    SectionCard(
        title = d.ticker,
        subtitle = Fmt.dateTime(d.timestamp),
        modifier = Modifier.animateContentSize().clickable { expanded = !expanded },
        trailing = { Badge(d.action.uppercase(), actionColor(d.action)) },
    ) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column {
                Text("Confiança", fontSize = 10.sp, color = CortexColors.TextMuted)
                Text("${(d.confidence * 100).toInt()}%", fontWeight = FontWeight.Bold, color = CortexColors.Primary)
            }
            d.trend_signal?.let {
                Column {
                    Text("Tendência", fontSize = 10.sp, color = CortexColors.TextMuted)
                    Text(it, fontWeight = FontWeight.SemiBold, fontSize = 13.sp)
                }
            }
            d.sentiment_score?.let {
                Column {
                    Text("Sentimento", fontSize = 10.sp, color = CortexColors.TextMuted)
                    Text(String.format(java.util.Locale.US, "%+.2f", it), fontWeight = FontWeight.SemiBold, color = CortexColors.signed(it))
                }
            }
        }
        val reason = d.reasoning
        if (!reason.isNullOrBlank()) {
            Spacer(Modifier.height(6.dp))
            Text(
                if (expanded) reason else reason.take(140) + if (reason.length > 140) "…" else "",
                fontSize = 12.sp, color = CortexColors.TextMuted,
            )
        }
        Spacer(Modifier.width(0.dp).fillMaxWidth())
    }
}
