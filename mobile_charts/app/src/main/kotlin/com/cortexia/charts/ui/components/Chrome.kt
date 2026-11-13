package com.cortexia.charts.ui.components

import android.app.Activity
import android.content.Context
import android.content.ContextWrapper
import android.content.pm.ActivityInfo
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import com.cortexia.charts.R
import com.cortexia.charts.data.EnvTarget
import com.cortexia.charts.ui.theme.ChartsColors

data class FullscreenChartSpec(
    val title: String,
    val subtitle: String = "",
    val render: @Composable (Boolean) -> Unit,
)

fun Context.findActivity(): Activity? {
    var cur: Context? = this
    while (cur is ContextWrapper) {
        if (cur is Activity) return cur
        cur = cur.baseContext
    }
    return null
}

@Composable
fun FullscreenChartDialog(
    env: EnvTarget,
    spec: FullscreenChartSpec,
    onDismiss: () -> Unit,
) {
    val context = LocalContext.current
    DisposableEffect(Unit) {
        val activity = context.findActivity()
        activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_SENSOR_LANDSCAPE
        onDispose {
            activity?.requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
        }
    }

    Dialog(
        onDismissRequest = onDismiss,
        properties = DialogProperties(
            usePlatformDefaultWidth = false,
            dismissOnBackPress = true,
            dismissOnClickOutside = false,
        ),
    ) {
        Surface(
            modifier = Modifier.fillMaxSize(),
            color = ChartsColors.Background,
        ) {
            Column(
                modifier = Modifier
                    .fillMaxSize()
                    .systemBarsPadding()
                    .padding(horizontal = 16.dp, vertical = 10.dp),
            ) {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    EnvBadgePill(env = env)
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(
                            text = spec.title,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Bold,
                            color = ChartsColors.TextPrimary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        if (spec.subtitle.isNotBlank()) {
                            Text(
                                text = spec.subtitle,
                                fontSize = 11.sp,
                                color = ChartsColors.TextSecondary,
                                maxLines = 1,
                                overflow = TextOverflow.Ellipsis,
                            )
                        }
                    }
                    IconButton(onClick = onDismiss) {
                        Icon(
                            imageVector = Icons.Filled.Close,
                            contentDescription = "Fechar tela cheia",
                            tint = ChartsColors.TextPrimary,
                        )
                    }
                }
                Spacer(Modifier.height(6.dp))
                Box(
                    modifier = Modifier
                        .fillMaxSize()
                        .clip(RoundedCornerShape(14.dp))
                        .background(ChartsColors.Surface)
                        .border(1.dp, ChartsColors.Outline, RoundedCornerShape(14.dp))
                        .padding(12.dp),
                ) {
                    spec.render(true)
                }
            }
        }
    }
}

@Composable
fun ChartCard(
    title: String,
    modifier: Modifier = Modifier,
    subtitle: String? = null,
    onExpand: (() -> Unit)? = null,
    headerExtra: (@Composable () -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Card(
        modifier = modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = ChartsColors.Surface),
        border = BorderStroke(1.dp, ChartsColors.Outline),
    ) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(
                        text = title,
                        style = MaterialTheme.typography.titleSmall,
                        fontWeight = FontWeight.SemiBold,
                        color = ChartsColors.TextPrimary,
                    )
                    if (!subtitle.isNullOrBlank()) {
                        Text(
                            text = subtitle,
                            fontSize = 11.sp,
                            color = ChartsColors.TextSecondary,
                        )
                    }
                }
                headerExtra?.invoke()
                if (onExpand != null) {
                    TextButton(
                        onClick = onExpand,
                        contentPadding = PaddingValues(horizontal = 8.dp, vertical = 2.dp),
                    ) {
                        Icon(
                            painter = painterResource(R.drawable.ic_fullscreen),
                            contentDescription = "Expandir gráfico",
                            tint = ChartsColors.Cyan,
                            modifier = Modifier.size(15.dp),
                        )
                        Spacer(Modifier.width(4.dp))
                        Text("⛶ Expandir", fontSize = 11.sp, color = ChartsColors.Cyan, fontWeight = FontWeight.Medium)
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
            content()
        }
    }
}

@Composable
fun EnvBadgePill(
    env: EnvTarget,
    modifier: Modifier = Modifier,
    onClick: (() -> Unit)? = null,
) {
    val accent = ChartsColors.envAccent(env)
    val baseMod = modifier
        .clip(RoundedCornerShape(50))
        .background(accent.copy(alpha = 0.16f))
        .border(1.dp, accent.copy(alpha = 0.55f), RoundedCornerShape(50))
        .let { if (onClick != null) it.clickable(onClick = onClick) else it }
        .padding(horizontal = 10.dp, vertical = 4.dp)

    Row(modifier = baseMod, verticalAlignment = Alignment.CenterVertically) {
        Text(
            text = env.badgeLabel,
            color = accent,
            fontSize = 11.sp,
            fontWeight = FontWeight.Bold,
            maxLines = 1,
        )
        if (onClick != null) {
            Spacer(Modifier.width(4.dp))
            Text("▾", color = accent, fontSize = 11.sp, fontWeight = FontWeight.Bold)
        }
    }
}

@Composable
fun KpiTile(
    label: String,
    value: String,
    modifier: Modifier = Modifier,
    valueColor: Color = ChartsColors.TextPrimary,
    sub: String? = null,
    subColor: Color = ChartsColors.TextSecondary,
) {
    Card(
        modifier = modifier,
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = ChartsColors.Surface),
        border = BorderStroke(1.dp, ChartsColors.Outline),
    ) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 10.dp)) {
            Text(
                text = label,
                fontSize = 11.sp,
                color = ChartsColors.TextSecondary,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Spacer(Modifier.height(3.dp))
            Text(
                text = value,
                fontSize = 16.sp,
                fontWeight = FontWeight.Bold,
                color = valueColor,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            if (!sub.isNullOrBlank()) {
                Spacer(Modifier.height(2.dp))
                Text(
                    text = sub,
                    fontSize = 10.sp,
                    color = subColor,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
            }
        }
    }
}

@Composable
fun PeriodChipsRow(
    selectedDays: Int,
    onSelectDays: (Int) -> Unit,
) {
    val options = listOf(
        7 to "7D",
        30 to "30D",
        90 to "90D",
        180 to "180D",
        365 to "1A",
        730 to "2A",
    )
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        options.forEach { (days, label) ->
            val selected = selectedDays == days
            FilterChip(
                selected = selected,
                onClick = { onSelectDays(days) },
                label = { Text(label, fontSize = 12.sp, fontWeight = FontWeight.SemiBold) },
                colors = FilterChipDefaults.filterChipColors(
                    selectedContainerColor = ChartsColors.Cyan.copy(alpha = 0.22f),
                    selectedLabelColor = ChartsColors.Cyan,
                    containerColor = ChartsColors.Surface,
                    labelColor = ChartsColors.TextSecondary,
                ),
            )
        }
    }
}

@Composable
fun LegendDotItem(color: Color, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier
                .size(9.dp)
                .clip(CircleShape)
                .background(color)
        )
        Spacer(Modifier.width(5.dp))
        Text(label, fontSize = 11.sp, color = ChartsColors.TextSecondary)
    }
}

@Composable
fun StatKeyValueRow(
    label: String,
    value: String,
    valueColor: Color = ChartsColors.TextPrimary,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = label,
            fontSize = 12.sp,
            color = ChartsColors.TextSecondary,
            modifier = Modifier.weight(1f),
        )
        Spacer(Modifier.width(8.dp))
        Text(
            text = value,
            fontSize = 12.sp,
            fontWeight = FontWeight.SemiBold,
            color = valueColor,
        )
    }
}

@Composable
fun EmptyChartPlaceholder(
    message: String = "Sem dados suficientes neste período",
    height: Dp = 180.dp,
) {
    Box(
        modifier = Modifier
            .fillMaxWidth()
            .height(height)
            .clip(RoundedCornerShape(12.dp))
            .background(ChartsColors.SurfaceElevated.copy(alpha = 0.4f)),
        contentAlignment = Alignment.Center,
    ) {
        Text(message, fontSize = 12.sp, color = ChartsColors.TextSecondary)
    }
}
