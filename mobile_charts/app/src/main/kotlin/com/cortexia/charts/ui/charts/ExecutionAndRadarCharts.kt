package com.cortexia.charts.ui.charts

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.charts.data.CashflowD2Item
import com.cortexia.charts.data.ConfidenceBucket
import com.cortexia.charts.data.MarketRadarItem
import com.cortexia.charts.ui.Fmt
import com.cortexia.charts.ui.components.EmptyChartPlaceholder
import com.cortexia.charts.ui.components.LegendDotItem
import com.cortexia.charts.ui.theme.ChartsColors
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.sin

data class RankingBarItem(
    val label: String,
    val value: Double,
    val detail: String,
)

@Composable
fun HorizontalRankingChart(
    items: List<RankingBarItem>,
    valueFormatter: (Double) -> String = Fmt::brlSigned,
    isFullscreen: Boolean = false,
) {
    if (items.isEmpty()) {
        EmptyChartPlaceholder(message = "Sem resultados realizados/abertos no período", height = 140.dp)
        return
    }
    val tm = rememberTextMeasurer()
    var selectedIdx by remember(items) { mutableStateOf<Int?>(0) }
    val rowHeightDp = 30.dp
    val canvasHeight = (rowHeightDp * items.size).coerceAtLeast(90.dp)

    val wrapperMod = if (isFullscreen) {
        Modifier.fillMaxSize().verticalScroll(rememberScrollState())
    } else {
        Modifier.fillMaxWidth()
    }

    Column(modifier = wrapperMod) {
        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(canvasHeight)
                .pointerInput(items) {
                    detectTapGestures { tap ->
                        val rowPx = size.height.toFloat() / items.size
                        val idx = (tap.y / rowPx).toInt().coerceIn(0, items.lastIndex)
                        selectedIdx = idx
                    }
                }
        ) {
            val maxAbs = items.maxOf { abs(it.value) }.takeIf { it > 1e-6 } ?: 1.0
            val labelColW = 78.dp.toPx()
            val valueColW = 92.dp.toPx()
            val barZoneLeft = labelColW + 6.dp.toPx()
            val barZoneRight = (size.width - valueColW - 6.dp.toPx()).coerceAtLeast(barZoneLeft + 20f)
            val zeroX = (barZoneLeft + barZoneRight) / 2f
            val halfSpan = (barZoneRight - barZoneLeft) / 2f
            val rowH = size.height / items.size

            drawLine(
                color = ChartsColors.TextSecondary.copy(alpha = 0.6f),
                start = Offset(zeroX, 0f),
                end = Offset(zeroX, size.height),
                strokeWidth = 1.3.dp.toPx(),
            )

            items.forEachIndexed { idx, item ->
                val centerY = rowH * idx + rowH / 2f
                val isSel = selectedIdx == idx
                if (isSel) {
                    drawRoundRect(
                        color = ChartsColors.SurfaceElevated.copy(alpha = 0.7f),
                        topLeft = Offset(0f, rowH * idx + 1.dp.toPx()),
                        size = Size(size.width, rowH - 2.dp.toPx()),
                        cornerRadius = CornerRadius(6.dp.toPx()),
                    )
                }

                val lbl = tm.measure(
                    item.label.take(11),
                    TextStyle(
                        color = if (isSel) ChartsColors.Cyan else ChartsColors.TextPrimary,
                        fontSize = 11.sp,
                        fontWeight = if (isSel) FontWeight.Bold else FontWeight.Medium,
                    ),
                )
                drawText(lbl, topLeft = Offset(4.dp.toPx(), centerY - lbl.size.height / 2f))

                val frac = (abs(item.value) / maxAbs).toFloat().coerceIn(0.03f, 1f)
                val barLen = halfSpan * frac
                val barH = (rowH * 0.52f).coerceAtMost(16.dp.toPx())
                val barColor = if (item.value >= 0.0) ChartsColors.Emerald else ChartsColors.Crimson
                val left = if (item.value >= 0.0) zeroX else zeroX - barLen
                drawRoundRect(
                    color = barColor,
                    topLeft = Offset(left, centerY - barH / 2f),
                    size = Size(barLen, barH),
                    cornerRadius = CornerRadius(4.dp.toPx()),
                )

                val valLayout = tm.measure(
                    valueFormatter(item.value),
                    TextStyle(
                        color = ChartsColors.signed(item.value),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                    ),
                )
                drawText(
                    valLayout,
                    topLeft = Offset(
                        (size.width - valLayout.size.width - 4.dp.toPx()).coerceAtLeast(barZoneRight),
                        centerY - valLayout.size.height / 2f,
                    ),
                )
            }
        }

        selectedIdx?.let { idx ->
            val sel = items.getOrNull(idx) ?: return@let
            Spacer(Modifier.height(6.dp))
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .background(ChartsColors.SurfaceElevated.copy(alpha = 0.65f))
                    .padding(horizontal = 10.dp, vertical = 6.dp),
            ) {
                Text(
                    text = "${sel.label} • ${sel.detail}",
                    fontSize = 11.sp,
                    color = ChartsColors.TextSecondary,
                )
            }
        }
    }
}

@Composable
fun SemicircularGaugeChart(
    value: Double,
    minValue: Double = 0.0,
    maxValue: Double = 100.0,
    primaryText: String,
    secondaryText: String,
    zone1EndFraction: Float = 0.75f,
    zone2EndFraction: Float = 0.975f,
    invertZones: Boolean = false,
    isFullscreen: Boolean = false,
    height: Dp = 155.dp,
) {
    val safeRange = (maxValue - minValue).takeIf { abs(it) > 1e-9 } ?: 1.0
    val normalized = ((value - minValue) / safeRange).toFloat().coerceIn(0f, 1f)

    val zoneColors = if (!invertZones) {
        Triple(ChartsColors.Emerald, ChartsColors.Amber, ChartsColors.Crimson)
    } else {
        Triple(ChartsColors.Crimson, ChartsColors.Amber, ChartsColors.Emerald)
    }

    val activeColor = when {
        normalized < zone1EndFraction -> zoneColors.first
        normalized < zone2EndFraction -> zoneColors.second
        else -> zoneColors.third
    }

    val boxMod = if (isFullscreen) Modifier.fillMaxSize() else Modifier.fillMaxWidth().height(height)

    Box(modifier = boxMod, contentAlignment = Alignment.BottomCenter) {
        Canvas(modifier = Modifier.fillMaxSize()) {
            val stroke = if (isFullscreen) 24.dp.toPx() else 18.dp.toPx()
            val diameter = minOf(size.width - stroke * 1.4f, (size.height - 26.dp.toPx()) * 1.92f).coerceAtLeast(40f)
            val arcLeft = (size.width - diameter) / 2f
            val arcTop = (size.height - 22.dp.toPx()) - diameter / 2f
            val arcSize = Size(diameter, diameter)

            val s1 = 180f * zone1EndFraction
            val s2 = 180f * (zone2EndFraction - zone1EndFraction)
            val s3 = 180f * (1f - zone2EndFraction)

            drawArc(
                color = zoneColors.first.copy(alpha = 0.28f),
                startAngle = 180f,
                sweepAngle = s1,
                useCenter = false,
                topLeft = Offset(arcLeft, arcTop),
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Butt),
            )
            drawArc(
                color = zoneColors.second.copy(alpha = 0.30f),
                startAngle = 180f + s1,
                sweepAngle = s2,
                useCenter = false,
                topLeft = Offset(arcLeft, arcTop),
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Butt),
            )
            drawArc(
                color = zoneColors.third.copy(alpha = 0.34f),
                startAngle = 180f + s1 + s2,
                sweepAngle = s3,
                useCenter = false,
                topLeft = Offset(arcLeft, arcTop),
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Butt),
            )

            drawArc(
                color = activeColor,
                startAngle = 180f,
                sweepAngle = (180f * normalized).coerceAtLeast(1.5f),
                useCenter = false,
                topLeft = Offset(arcLeft, arcTop),
                size = arcSize,
                style = Stroke(width = stroke, cap = StrokeCap.Round),
            )

            val cx = size.width / 2f
            val cy = arcTop + diameter / 2f
            val angleRad = Math.toRadians((180.0 + 180.0 * normalized))
            val needleLen = diameter / 2f - stroke * 0.65f
            val nx = cx + (needleLen * cos(angleRad)).toFloat()
            val ny = cy + (needleLen * sin(angleRad)).toFloat()

            drawLine(
                color = Color.White,
                start = Offset(cx, cy),
                end = Offset(nx, ny),
                strokeWidth = 3.dp.toPx(),
                cap = StrokeCap.Round,
            )
            drawCircle(color = activeColor, radius = 6.5.dp.toPx(), center = Offset(cx, cy))
            drawCircle(color = ChartsColors.Background, radius = 3.dp.toPx(), center = Offset(cx, cy))
        }

        Column(
            horizontalAlignment = Alignment.CenterHorizontally,
            modifier = Modifier.padding(bottom = 2.dp),
        ) {
            Text(
                text = primaryText,
                fontSize = if (isFullscreen) 22.sp else 17.sp,
                fontWeight = FontWeight.Bold,
                color = activeColor,
            )
            Text(
                text = secondaryText,
                fontSize = 11.sp,
                color = ChartsColors.TextSecondary,
            )
        }
    }
}

@Composable
fun RsiSentimentScatterChart(
    items: List<MarketRadarItem>,
    isFullscreen: Boolean = false,
    height: Dp = 235.dp,
) {
    val plotItems = remember(items) { items.filter { it.ticker.isNotBlank() } }
    if (plotItems.isEmpty()) {
        EmptyChartPlaceholder(message = "Sem ativos no Radar B3 no momento", height = height)
        return
    }
    val tm = rememberTextMeasurer()
    var selectedTicker by remember(plotItems) { mutableStateOf<String?>(plotItems.first().ticker) }
    val pointCoords = remember { mutableListOf<Pair<MarketRadarItem, Offset>>() }

    Column(modifier = if (isFullscreen) Modifier.fillMaxSize() else Modifier.fillMaxWidth()) {
        val canvasMod = if (isFullscreen) Modifier.weight(1f).fillMaxWidth() else Modifier.fillMaxWidth().height(height)

        Canvas(
            modifier = canvasMod.pointerInput(plotItems) {
                detectTapGestures { tap ->
                    val nearest = pointCoords.minByOrNull { (_, p) -> hypot(tap.x - p.x, tap.y - p.y) }
                    if (nearest != null && hypot(tap.x - nearest.second.x, tap.y - nearest.second.y) <= 42.dp.toPx()) {
                        selectedTicker = nearest.first.ticker
                    }
                }
            }
        ) {
            val leftPad = 36.dp.toPx()
            val rightPad = 12.dp.toPx()
            val topPad = 12.dp.toPx()
            val bottomPad = 24.dp.toPx()
            val plotW = (size.width - leftPad - rightPad).coerceAtLeast(1f)
            val plotH = (size.height - topPad - bottomPad).coerceAtLeast(1f)

            val xOfRsi = { rsi: Double -> (leftPad + plotW * (rsi.coerceIn(0.0, 100.0) / 100.0)).toFloat() }
            val yOfSent = { sent: Double ->
                val norm = (sent.coerceIn(-1.0, 1.0) + 1.0) / 2.0
                (topPad + plotH * (1.0 - norm)).toFloat()
            }

            val x30 = xOfRsi(30.0)
            val x70 = xOfRsi(70.0)
            drawRect(
                color = ChartsColors.Emerald.copy(alpha = 0.09f),
                topLeft = Offset(leftPad, topPad),
                size = Size((x30 - leftPad).coerceAtLeast(0f), plotH),
            )
            drawRect(
                color = ChartsColors.Crimson.copy(alpha = 0.09f),
                topLeft = Offset(x70, topPad),
                size = Size((leftPad + plotW - x70).coerceAtLeast(0f), plotH),
            )

            val dash = PathEffect.dashPathEffect(floatArrayOf(6f, 6f))
            listOf(30.0, 50.0, 70.0).forEach { r ->
                val rx = xOfRsi(r)
                val col = when (r) {
                    30.0 -> ChartsColors.Emerald.copy(alpha = 0.55f)
                    70.0 -> ChartsColors.Crimson.copy(alpha = 0.55f)
                    else -> ChartsColors.GridLine
                }
                drawLine(col, Offset(rx, topPad), Offset(rx, topPad + plotH), 1.2f, pathEffect = dash)
                val lbl = tm.measure("RSI ${r.toInt()}", TextStyle(color = ChartsColors.TextSecondary, fontSize = 9.sp))
                drawText(lbl, topLeft = Offset(rx - lbl.size.width / 2f, topPad + plotH + 4.dp.toPx()))
            }

            listOf(-1.0, -0.5, 0.0, 0.5, 1.0).forEach { s ->
                val sy = yOfSent(s)
                drawLine(
                    color = if (s == 0.0) ChartsColors.TextSecondary.copy(alpha = 0.6f) else ChartsColors.GridLine,
                    start = Offset(leftPad, sy),
                    end = Offset(leftPad + plotW, sy),
                    strokeWidth = if (s == 0.0) 1.5f else 1f,
                    pathEffect = if (s == 0.0) null else dash,
                )
                val lbl = tm.measure(String.format("%+.1f", s), TextStyle(color = ChartsColors.TextSecondary, fontSize = 9.sp))
                drawText(lbl, topLeft = Offset((leftPad - lbl.size.width - 4.dp.toPx()).coerceAtLeast(0f), sy - lbl.size.height / 2f))
            }

            pointCoords.clear()
            plotItems.forEach { item ->
                val rsiVal = item.rsi ?: 50.0
                val sentVal = item.sentiment_score ?: 0.0
                val pt = Offset(xOfRsi(rsiVal), yOfSent(sentVal))
                pointCoords.add(item to pt)

                val isSel = selectedTicker == item.ticker
                val dotColor = when {
                    sentVal > 0.15 && rsiVal < 70.0 -> ChartsColors.Emerald
                    sentVal < -0.15 || rsiVal > 72.0 -> ChartsColors.Crimson
                    else -> ChartsColors.Cyan
                }
                if (isSel) {
                    drawCircle(color = dotColor.copy(alpha = 0.30f), radius = 12.dp.toPx(), center = pt)
                    drawCircle(color = Color.White, radius = 6.5.dp.toPx(), center = pt)
                }
                drawCircle(color = dotColor, radius = if (isSel) 4.8.dp.toPx() else 4.dp.toPx(), center = pt)

                if (isSel || plotItems.size <= 14 || isFullscreen) {
                    val tkLayout = tm.measure(
                        item.ticker,
                        TextStyle(
                            color = if (isSel) ChartsColors.Cyan else ChartsColors.TextPrimary.copy(alpha = 0.85f),
                            fontSize = 9.sp,
                            fontWeight = if (isSel) FontWeight.Bold else FontWeight.Normal,
                        ),
                    )
                    val tx = (pt.x + 5.dp.toPx()).coerceAtMost(size.width - tkLayout.size.width - 2.dp.toPx())
                    val ty = (pt.y - tkLayout.size.height - 2.dp.toPx()).coerceAtLeast(topPad)
                    drawText(tkLayout, topLeft = Offset(tx, ty))
                }
            }
        }

        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            LegendDotItem(ChartsColors.Emerald, "Sobrevenda (RSI < 30)")
            LegendDotItem(ChartsColors.Cyan, "Neutro (30–70)")
            LegendDotItem(ChartsColors.Crimson, "Sobrecompra (RSI > 70)")
        }

        val active = plotItems.firstOrNull { it.ticker == selectedTicker } ?: plotItems.first()
        Spacer(Modifier.height(6.dp))
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .background(ChartsColors.SurfaceElevated.copy(alpha = 0.7f))
                .border(1.dp, ChartsColors.Outline, RoundedCornerShape(8.dp))
                .padding(horizontal = 10.dp, vertical = 7.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = "${active.ticker} • ${active.sector} • ${active.trend_signal}",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = ChartsColors.Cyan,
                )
                Text(
                    text = "RSI: ${Fmt.ratio(active.rsi, 1)} • Sentimento: ${Fmt.ratio(active.sentiment_score, 2)} • ADX: ${Fmt.ratio(active.adx, 1)}",
                    fontSize = 10.sp,
                    color = ChartsColors.TextSecondary,
                )
            }
            Column(horizontalAlignment = Alignment.End) {
                Text(Fmt.brl(active.price), fontSize = 12.sp, fontWeight = FontWeight.Bold, color = ChartsColors.TextPrimary)
                Text(Fmt.pct(active.change_pct), fontSize = 11.sp, color = ChartsColors.signed(active.change_pct))
            }
        }
    }
}

@Composable
fun CashflowD2Chart(
    settledBrl: Double,
    pendingD2Brl: Double,
    schedule: List<CashflowD2Item>,
    isFullscreen: Boolean = false,
) {
    val totalCash = max(0.0, settledBrl) + max(0.0, pendingD2Brl)
    val settledFrac = if (totalCash > 0.0) (max(0.0, settledBrl) / totalCash).toFloat().coerceIn(0f, 1f) else 1f

    val mod = if (isFullscreen) {
        Modifier.fillMaxSize().verticalScroll(rememberScrollState())
    } else {
        Modifier.fillMaxWidth()
    }

    Column(modifier = mod, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Column {
                Text("Liquidado Disponível", fontSize = 11.sp, color = ChartsColors.TextSecondary)
                Text(Fmt.brl(settledBrl), fontSize = 14.sp, fontWeight = FontWeight.Bold, color = ChartsColors.Emerald)
            }
            Column(horizontalAlignment = Alignment.End) {
                Text("Em Liquidação (D+2)", fontSize = 11.sp, color = ChartsColors.TextSecondary)
                Text(Fmt.brl(pendingD2Brl), fontSize = 14.sp, fontWeight = FontWeight.Bold, color = ChartsColors.Amber)
            }
        }

        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(18.dp)
                .clip(RoundedCornerShape(9.dp))
        ) {
            val w1 = size.width * settledFrac
            drawRect(ChartsColors.Emerald, Offset(0f, 0f), Size(w1, size.height))
            drawRect(ChartsColors.Amber, Offset(w1, 0f), Size(size.width - w1, size.height))
        }

        if (schedule.isNotEmpty()) {
            val maxAmt = schedule.maxOf { it.net_sell_amount }.takeIf { it > 0.0 } ?: 1.0
            schedule.take(if (isFullscreen) 15 else 6).forEach { item ->
                val barRatio = (item.net_sell_amount / maxAmt).toFloat().coerceIn(0.05f, 1f)
                val tone = if (item.settled) ChartsColors.Emerald else ChartsColors.Amber
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = "${Fmt.dayShort(item.trade_date)} → ${Fmt.dayShort(item.settlement_date)}",
                        fontSize = 10.sp,
                        color = ChartsColors.TextSecondary,
                        modifier = Modifier.width(96.dp),
                    )
                    Box(Modifier.weight(1f).height(12.dp)) {
                        Box(
                            Modifier
                                .fillMaxWidth(barRatio)
                                .fillMaxHeight()
                                .clip(RoundedCornerShape(4.dp))
                                .background(tone)
                        )
                    }
                    Spacer(Modifier.width(8.dp))
                    Text(
                        text = Fmt.brl(item.net_sell_amount),
                        fontSize = 11.sp,
                        fontWeight = FontWeight.SemiBold,
                        color = tone,
                    )
                }
            }
        }
    }
}

@Composable
fun ConfidenceHistogramChart(
    buckets: List<ConfidenceBucket>,
    isFullscreen: Boolean = false,
    height: Dp = 175.dp,
) {
    if (buckets.isEmpty() || buckets.all { it.count == 0 }) {
        EmptyChartPlaceholder(message = "Sem decisões IA registradas na janela", height = height)
        return
    }
    val tm = rememberTextMeasurer()
    var selected by remember(buckets) { mutableStateOf<Int?>(null) }
    val chartMod = if (isFullscreen) Modifier.fillMaxSize() else Modifier.fillMaxWidth().height(height)

    Canvas(
        modifier = chartMod.pointerInput(buckets) {
            detectTapGestures { tap ->
                val slot = size.width.toFloat() / buckets.size
                selected = (tap.x / slot).toInt().coerceIn(0, buckets.lastIndex)
            }
        }
    ) {
        val maxCount = buckets.maxOf { it.count }.coerceAtLeast(1)
        val topPad = 24.dp.toPx()
        val bottomPad = 24.dp.toPx()
        val plotH = (size.height - topPad - bottomPad).coerceAtLeast(1f)
        val slot = size.width / buckets.size
        val barW = (slot * 0.62f).coerceAtLeast(8f)

        buckets.forEachIndexed { idx, bucket ->
            val frac = bucket.count.toFloat() / maxCount.toFloat()
            val barH = (plotH * frac).coerceAtLeast(3f)
            val left = slot * idx + (slot - barW) / 2f
            val top = topPad + plotH - barH
            val isSel = selected == idx
            val color = if (idx >= 3) ChartsColors.Emerald else if (idx == 2) ChartsColors.Cyan else ChartsColors.Indigo

            drawRoundRect(
                color = color.copy(alpha = if (selected == null || isSel) 0.90f else 0.45f),
                topLeft = Offset(left, top),
                size = Size(barW, barH),
                cornerRadius = CornerRadius(6.dp.toPx()),
            )

            val countText = tm.measure(
                "${bucket.count}",
                TextStyle(color = ChartsColors.TextPrimary, fontSize = 10.sp, fontWeight = FontWeight.Bold),
            )
            drawText(
                countText,
                topLeft = Offset(left + (barW - countText.size.width) / 2f, (top - countText.size.height - 3.dp.toPx()).coerceAtLeast(0f)),
            )

            val lbl = tm.measure(bucket.label, TextStyle(color = ChartsColors.TextSecondary, fontSize = 10.sp))
            drawText(
                lbl,
                topLeft = Offset(slot * idx + (slot - lbl.size.width) / 2f, size.height - bottomPad + 5.dp.toPx()),
            )
        }
    }
}
