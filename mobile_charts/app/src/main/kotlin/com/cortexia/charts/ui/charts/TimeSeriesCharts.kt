package com.cortexia.charts.ui.charts

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
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
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.charts.data.EquityPoint
import com.cortexia.charts.data.MonthlyReturnItem
import com.cortexia.charts.ui.Fmt
import com.cortexia.charts.ui.components.EmptyChartPlaceholder
import com.cortexia.charts.ui.components.LegendDotItem
import com.cortexia.charts.ui.theme.ChartsColors
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

private val AxisTextStyle = TextStyle(color = ChartsColors.TextSecondary, fontSize = 10.sp)
private val TooltipTextStyle = TextStyle(color = ChartsColors.TextPrimary, fontSize = 11.sp, fontWeight = FontWeight.Medium)

private class ChartInsets {
    var left = 120f
    var right = 16f
    var top = 28f
    var bottom = 26f
}

private fun DrawScope.configureInsets(
    insets: ChartInsets,
    tm: TextMeasurer,
    minV: Double,
    maxV: Double,
    yFormat: (Double) -> String,
    topDp: Dp = 14.dp,
) {
    insets.top = topDp.toPx()
    insets.bottom = 24.dp.toPx()
    insets.right = 10.dp.toPx()
    val maxLabelW = (0..4).maxOf { step ->
        val v = minV + (maxV - minV) * step / 4.0
        tm.measure(yFormat(v), AxisTextStyle).size.width
    }
    insets.left = maxLabelW.toFloat() + 10.dp.toPx()
}

private fun DrawScope.drawHorizontalGrid(
    insets: ChartInsets,
    tm: TextMeasurer,
    minV: Double,
    maxV: Double,
    yFormat: (Double) -> String,
) {
    val plotH = (size.height - insets.top - insets.bottom).coerceAtLeast(1f)
    val dash = PathEffect.dashPathEffect(floatArrayOf(6f, 6f))
    for (step in 0..4) {
        val frac = step / 4f
        val y = insets.top + plotH * (1f - frac)
        drawLine(
            color = ChartsColors.GridLine,
            start = Offset(insets.left, y),
            end = Offset(size.width - insets.right, y),
            strokeWidth = 1f,
            pathEffect = dash,
        )
        val v = minV + (maxV - minV) * frac
        val measured = tm.measure(yFormat(v), AxisTextStyle)
        drawText(
            textLayoutResult = measured,
            topLeft = Offset(
                (insets.left - measured.size.width - 6.dp.toPx()).coerceAtLeast(0f),
                y - measured.size.height / 2f,
            ),
        )
    }
}

private fun DrawScope.drawXAxisDates(
    insets: ChartInsets,
    tm: TextMeasurer,
    labels: List<String>,
    xOf: (Int) -> Float,
) {
    if (labels.isEmpty()) return
    val tickCount = minOf(5, labels.size)
    val stride = max(1, (labels.size - 1) / max(1, tickCount - 1))
    var idx = 0
    val maxRight = (size.width - insets.right).coerceAtLeast(insets.left)
    while (idx < labels.size) {
        val measured = tm.measure(labels[idx], AxisTextStyle)
        val rawX = xOf(idx) - measured.size.width / 2f
        val clampedX = rawX.coerceIn(insets.left, (maxRight - measured.size.width).coerceAtLeast(insets.left))
        drawText(
            textLayoutResult = measured,
            topLeft = Offset(clampedX, size.height - insets.bottom + 5.dp.toPx()),
        )
        idx += stride
    }
}

private fun DrawScope.drawFloatingBadge(
    insets: ChartInsets,
    tm: TextMeasurer,
    text: String,
    anchorX: Float,
) {
    val measured = tm.measure(text, TooltipTextStyle)
    val padX = 8.dp.toPx()
    val padY = 4.dp.toPx()
    val boxW = measured.size.width + padX * 2
    val boxH = measured.size.height + padY * 2
    val maxLeft = (size.width - insets.right - boxW).coerceAtLeast(0f)
    val left = (anchorX - boxW / 2f).coerceIn(insets.left.coerceAtMost(maxLeft), maxLeft)
    drawRoundRect(
        color = ChartsColors.SurfaceBright,
        topLeft = Offset(left, 2.dp.toPx()),
        size = Size(boxW, boxH),
        cornerRadius = CornerRadius(8.dp.toPx()),
    )
    drawRoundRect(
        color = ChartsColors.Cyan.copy(alpha = 0.6f),
        topLeft = Offset(left, 2.dp.toPx()),
        size = Size(boxW, boxH),
        cornerRadius = CornerRadius(8.dp.toPx()),
        style = Stroke(width = 1.dp.toPx()),
    )
    drawText(
        textLayoutResult = measured,
        topLeft = Offset(left + padX, 2.dp.toPx() + padY),
    )
}

@Composable
fun MultiSeriesAreaChart(
    series: List<EquityPoint>,
    isFullscreen: Boolean = false,
    height: Dp = 235.dp,
) {
    if (series.isEmpty()) {
        EmptyChartPlaceholder(height = height)
        return
    }
    val tm = rememberTextMeasurer()
    val insets = remember { ChartInsets() }
    val canvasWidthHolder = remember { floatArrayOf(1f) }

    var zoomSpan by remember(series.size) { mutableFloatStateOf(1f) }
    var zoomStart by remember(series.size) { mutableFloatStateOf(0f) }
    var selectedIdx by remember(series) { mutableStateOf<Int?>(series.lastIndex) }

    val totalSize = series.size
    val startIndex = ((zoomStart * (totalSize - 1)).roundToInt()).coerceIn(0, max(0, totalSize - 1))
    val visibleCount = max(2, (zoomSpan * totalSize).roundToInt()).coerceAtMost(totalSize)
    val endIndex = (startIndex + visibleCount - 1).coerceIn(startIndex, totalSize - 1)
    val visible = series.subList(startIndex, endIndex + 1)

    val activePoint = selectedIdx?.coerceIn(0, series.lastIndex)?.let { series[it] } ?: visible.last()

    Column(modifier = if (isFullscreen) Modifier.fillMaxSize() else Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp))
                .background(ChartsColors.SurfaceElevated.copy(alpha = 0.7f))
                .padding(horizontal = 10.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    text = "${Fmt.dayShort(activePoint.date)} • Total ${Fmt.brl(activePoint.total_equity)}",
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    color = ChartsColors.TextPrimary,
                )
                Text(
                    text = "Oper.: ${Fmt.brl(activePoint.operational_equity)} • Cofre: ${Fmt.brl(activePoint.vault_reserve_cash)} • Ret.: ${Fmt.pct(activePoint.pnl_percent)}",
                    fontSize = 10.sp,
                    color = ChartsColors.signed(activePoint.pnl_percent),
                )
            }
            if (totalSize >= 6) {
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    listOf(1f to "1x", 0.5f to "2x", 0.25f to "4x").forEach { (span, lbl) ->
                        val active = abs(zoomSpan - span) < 0.08f
                        Box(
                            modifier = Modifier
                                .clip(RoundedCornerShape(6.dp))
                                .background(if (active) ChartsColors.Cyan.copy(alpha = 0.25f) else ChartsColors.Surface)
                                .border(
                                    1.dp,
                                    if (active) ChartsColors.Cyan else ChartsColors.Outline,
                                    RoundedCornerShape(6.dp),
                                )
                                .clickable {
                                    zoomSpan = span
                                    zoomStart = (1f - span).coerceAtLeast(0f)
                                }
                                .padding(horizontal = 7.dp, vertical = 3.dp),
                        ) {
                            Text(lbl, fontSize = 10.sp, color = if (active) ChartsColors.Cyan else ChartsColors.TextSecondary)
                        }
                    }
                }
            }
        }

        Spacer(Modifier.height(6.dp))

        val chartMod = if (isFullscreen) {
            Modifier.weight(1f).fillMaxWidth()
        } else {
            Modifier.fillMaxWidth().height(height)
        }

        Canvas(
            modifier = chartMod.pointerInput(series, startIndex, endIndex) {
                awaitEachGesture {
                    var prevSpread: Float? = null
                    do {
                        val event = awaitPointerEvent()
                        val pressed = event.changes.filter { it.pressed }
                        if (pressed.size >= 2) {
                            val x0 = pressed[0].position.x
                            val x1 = pressed[1].position.x
                            val spread = abs(x1 - x0).coerceAtLeast(12f)
                            val prev = prevSpread
                            if (prev != null && totalSize > 3) {
                                val scale = prev / spread
                                val newSpan = (zoomSpan * scale).coerceIn(0.15f, 1f)
                                val centerRatio = (((x0 + x1) / 2f - insets.left) / (canvasWidthHolder[0] - insets.left).coerceAtLeast(1f))
                                    .coerceIn(0f, 1f)
                                val anchor = zoomStart + zoomSpan * centerRatio
                                zoomSpan = newSpan
                                zoomStart = (anchor - newSpan * centerRatio).coerceIn(0f, (1f - newSpan).coerceAtLeast(0f))
                            }
                            prevSpread = spread
                            pressed.forEach { it.consume() }
                        } else if (pressed.size == 1 && prevSpread == null) {
                            val px = pressed[0].position.x
                            val usableW = (canvasWidthHolder[0] - insets.left - insets.right).coerceAtLeast(1f)
                            val rel = ((px - insets.left) / usableW).coerceIn(0f, 1f)
                            val localIdx = if (visible.size <= 1) 0 else (rel * (visible.size - 1)).roundToInt()
                            selectedIdx = (startIndex + localIdx).coerceIn(0, series.lastIndex)
                            pressed[0].consume()
                        }
                    } while (event.changes.any { it.pressed })
                }
            }
        ) {
            canvasWidthHolder[0] = size.width
            val allValues = buildList {
                visible.forEach {
                    add(it.total_equity)
                    if (it.operational_equity > 0.0) add(it.operational_equity)
                    if (it.initial_capital > 0.0) add(it.initial_capital)
                }
            }
            var minV = allValues.minOrNull() ?: 0.0
            var maxV = allValues.maxOrNull() ?: 1.0
            if (maxV - minV < 1e-6) {
                minV -= 50.0
                maxV += 50.0
            }
            val pad = (maxV - minV) * 0.10
            minV -= pad
            maxV += pad

            configureInsets(insets, tm, minV, maxV, Fmt::brlCompact)
            val plotW = (size.width - insets.left - insets.right).coerceAtLeast(1f)
            val plotH = (size.height - insets.top - insets.bottom).coerceAtLeast(1f)
            val xOf = { i: Int ->
                if (visible.size <= 1) insets.left + plotW / 2f
                else insets.left + plotW * i / (visible.size - 1).toFloat()
            }
            val yOf = { v: Double ->
                (insets.top + plotH * (1.0 - (v - minV) / (maxV - minV))).toFloat()
            }

            drawHorizontalGrid(insets, tm, minV, maxV, Fmt::brlCompact)

            val initRef = visible.lastOrNull { it.initial_capital > 0.0 }?.initial_capital
            if (initRef != null && initRef in minV..maxV) {
                val initY = yOf(initRef)
                drawLine(
                    color = ChartsColors.Amber.copy(alpha = 0.75f),
                    start = Offset(insets.left, initY),
                    end = Offset(size.width - insets.right, initY),
                    strokeWidth = 1.5.dp.toPx(),
                    pathEffect = PathEffect.dashPathEffect(floatArrayOf(10f, 8f)),
                )
            }

            val opPath = Path()
            visible.forEachIndexed { idx, pt ->
                val v = if (pt.operational_equity > 0.0) pt.operational_equity else pt.total_equity
                if (idx == 0) opPath.moveTo(xOf(idx), yOf(v)) else opPath.lineTo(xOf(idx), yOf(v))
            }
            drawPath(
                path = opPath,
                color = ChartsColors.Indigo,
                style = Stroke(width = 1.8.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round),
            )

            val totalPath = Path()
            visible.forEachIndexed { idx, pt ->
                if (idx == 0) totalPath.moveTo(xOf(idx), yOf(pt.total_equity))
                else totalPath.lineTo(xOf(idx), yOf(pt.total_equity))
            }
            val areaPath = Path().apply {
                addPath(totalPath)
                lineTo(xOf(visible.lastIndex), insets.top + plotH)
                lineTo(xOf(0), insets.top + plotH)
                close()
            }
            drawPath(
                path = areaPath,
                brush = Brush.verticalGradient(
                    colors = listOf(ChartsColors.Cyan.copy(alpha = 0.36f), ChartsColors.Emerald.copy(alpha = 0.03f)),
                    startY = insets.top,
                    endY = insets.top + plotH,
                ),
            )
            drawPath(
                path = totalPath,
                color = ChartsColors.Cyan,
                style = Stroke(width = 2.4.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round),
            )

            drawXAxisDates(insets, tm, visible.map { Fmt.dayShort(it.date) }, xOf)

            val localSel = ((selectedIdx ?: series.lastIndex) - startIndex).coerceIn(0, visible.lastIndex)
            val cx = xOf(localSel)
            val cy = yOf(visible[localSel].total_equity)
            drawLine(
                color = ChartsColors.Cyan.copy(alpha = 0.7f),
                start = Offset(cx, insets.top),
                end = Offset(cx, insets.top + plotH),
                strokeWidth = 1.2.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(5f, 5f)),
            )
            drawCircle(color = Color.White, radius = 5.dp.toPx(), center = Offset(cx, cy))
            drawCircle(color = ChartsColors.Cyan, radius = 3.2.dp.toPx(), center = Offset(cx, cy))
        }

        Spacer(Modifier.height(6.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            LegendDotItem(ChartsColors.Cyan, "Patrimônio Total")
            LegendDotItem(ChartsColors.Indigo, "Operacional")
            LegendDotItem(ChartsColors.Amber, "Capital Inicial")
        }
    }
}

@Composable
fun UnderwaterDrawdownChart(
    series: List<EquityPoint>,
    maxDrawdownOverride: Double? = null,
    isFullscreen: Boolean = false,
    height: Dp = 195.dp,
) {
    if (series.isEmpty()) {
        EmptyChartPlaceholder(height = height)
        return
    }
    val tm = rememberTextMeasurer()
    val insets = remember { ChartInsets() }
    val widthHolder = remember { floatArrayOf(1f) }
    var selected by remember(series) { mutableStateOf<Int?>(null) }

    val ddValues = series.map { min(0.0, it.drawdown_pct) }
    val worstDd = min(ddValues.minOrNull() ?: 0.0, maxDrawdownOverride ?: 0.0)

    val mapXToIndex = { px: Float ->
        val usable = (widthHolder[0] - insets.left - insets.right).coerceAtLeast(1f)
        if (series.size <= 1) 0
        else (((px - insets.left) / usable) * (series.size - 1)).roundToInt().coerceIn(0, series.lastIndex)
    }

    val chartMod = if (isFullscreen) Modifier.fillMaxSize() else Modifier.fillMaxWidth().height(height)

    Canvas(
        modifier = chartMod
            .pointerInput(series) { detectTapGestures { o -> selected = mapXToIndex(o.x) } }
            .pointerInput(series) {
                detectHorizontalDragGestures { change, _ -> selected = mapXToIndex(change.position.x) }
            }
    ) {
        widthHolder[0] = size.width
        val minV = min(-1.0, worstDd * 1.15)
        val maxV = 0.25
        val yFmt = { v: Double -> Fmt.pct(v, signed = false, decimals = 1) }
        configureInsets(insets, tm, minV, maxV, yFmt, topDp = 28.dp)
        val plotW = (size.width - insets.left - insets.right).coerceAtLeast(1f)
        val plotH = (size.height - insets.top - insets.bottom).coerceAtLeast(1f)
        val xOf = { i: Int ->
            if (series.size <= 1) insets.left + plotW / 2f
            else insets.left + plotW * i / (series.size - 1).toFloat()
        }
        val yOf = { v: Double ->
            (insets.top + plotH * (1.0 - (v - minV) / (maxV - minV))).toFloat()
        }

        drawHorizontalGrid(insets, tm, minV, maxV, yFmt)
        val zeroY = yOf(0.0)
        drawLine(
            color = ChartsColors.TextSecondary,
            start = Offset(insets.left, zeroY),
            end = Offset(size.width - insets.right, zeroY),
            strokeWidth = 1.4f,
        )

        val line = Path()
        ddValues.forEachIndexed { i, v ->
            if (i == 0) line.moveTo(xOf(i), yOf(v)) else line.lineTo(xOf(i), yOf(v))
        }
        val underwater = Path().apply {
            moveTo(xOf(0), zeroY)
            ddValues.forEachIndexed { i, v -> lineTo(xOf(i), yOf(v)) }
            lineTo(xOf(ddValues.lastIndex), zeroY)
            close()
        }
        drawPath(
            path = underwater,
            brush = Brush.verticalGradient(
                colors = listOf(ChartsColors.Crimson.copy(alpha = 0.12f), ChartsColors.Crimson.copy(alpha = 0.50f)),
                startY = zeroY,
                endY = insets.top + plotH,
            ),
        )
        drawPath(
            path = line,
            color = ChartsColors.Crimson,
            style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round),
        )

        if (worstDd < -0.01) {
            val maxDdY = yOf(worstDd)
            drawLine(
                color = ChartsColors.Gold,
                start = Offset(insets.left, maxDdY),
                end = Offset(size.width - insets.right, maxDdY),
                strokeWidth = 1.3.dp.toPx(),
                pathEffect = PathEffect.dashPathEffect(floatArrayOf(8f, 6f)),
            )
            val lbl = tm.measure("Máx DD ${Fmt.pct(worstDd, signed = false)}", AxisTextStyle.copy(color = ChartsColors.Gold))
            drawText(
                textLayoutResult = lbl,
                topLeft = Offset((size.width - insets.right - lbl.size.width - 4.dp.toPx()).coerceAtLeast(insets.left), (maxDdY - lbl.size.height - 2.dp.toPx()).coerceAtLeast(insets.top)),
            )
        }

        drawXAxisDates(insets, tm, series.map { Fmt.dayShort(it.date) }, xOf)

        selected?.let { idx ->
            val pt = series[idx]
            val cx = xOf(idx)
            val cy = yOf(ddValues[idx])
            drawLine(ChartsColors.TextPrimary.copy(alpha = 0.6f), Offset(cx, insets.top), Offset(cx, insets.top + plotH), 1.2f)
            drawCircle(Color.White, 4.5.dp.toPx(), Offset(cx, cy))
            drawCircle(ChartsColors.Crimson, 3.dp.toPx(), Offset(cx, cy))
            drawFloatingBadge(
                insets,
                tm,
                "${Fmt.dayShort(pt.date)} • DD ${Fmt.pct(ddValues[idx], signed = false)} • HWM ${Fmt.brlCompact(pt.high_water_mark)}",
                cx,
            )
        }
    }
}

@Composable
fun DailyReturnBarChart(
    series: List<EquityPoint>,
    isFullscreen: Boolean = false,
    height: Dp = 195.dp,
) {
    if (series.isEmpty()) {
        EmptyChartPlaceholder(height = height)
        return
    }
    val tm = rememberTextMeasurer()
    val insets = remember { ChartInsets() }
    val widthHolder = remember { floatArrayOf(1f) }
    var selected by remember(series) { mutableStateOf<Int?>(null) }

    val values = series.map { it.daily_pnl_percent }
    val mapXToIndex = { px: Float ->
        val slot = (widthHolder[0] - insets.left - insets.right).coerceAtLeast(1f) / series.size
        ((px - insets.left) / slot).toInt().coerceIn(0, series.lastIndex)
    }

    val chartMod = if (isFullscreen) Modifier.fillMaxSize() else Modifier.fillMaxWidth().height(height)

    Canvas(
        modifier = chartMod
            .pointerInput(series) { detectTapGestures { o -> selected = mapXToIndex(o.x) } }
            .pointerInput(series) {
                detectHorizontalDragGestures { change, _ -> selected = mapXToIndex(change.position.x) }
            }
    ) {
        widthHolder[0] = size.width
        var minV = min(0.0, values.minOrNull() ?: 0.0)
        var maxV = max(0.0, values.maxOrNull() ?: 0.0)
        if (maxV - minV < 1e-6) {
            maxV += 1.0
            minV -= 1.0
        }
        val pad = (maxV - minV) * 0.12
        if (minV < 0.0) minV -= pad
        maxV += pad

        val yFmt = { v: Double -> Fmt.pct(v, signed = true, decimals = 1) }
        configureInsets(insets, tm, minV, maxV, yFmt, topDp = 28.dp)
        val plotW = (size.width - insets.left - insets.right).coerceAtLeast(1f)
        val plotH = (size.height - insets.top - insets.bottom).coerceAtLeast(1f)
        val slot = plotW / series.size
        val barW = (slot * 0.72f).coerceAtLeast(2f)
        val yOf = { v: Double ->
            (insets.top + plotH * (1.0 - (v - minV) / (maxV - minV))).toFloat()
        }

        drawHorizontalGrid(insets, tm, minV, maxV, yFmt)
        val zeroY = yOf(0.0)
        drawLine(
            color = ChartsColors.TextSecondary,
            start = Offset(insets.left, zeroY),
            end = Offset(size.width - insets.right, zeroY),
            strokeWidth = 1.5f,
        )

        values.forEachIndexed { i, v ->
            val left = insets.left + slot * i + (slot - barW) / 2f
            val yVal = yOf(v)
            val top = min(yVal, zeroY)
            val barH = abs(yVal - zeroY).coerceAtLeast(1.5f)
            val baseColor = if (v >= 0.0) ChartsColors.Emerald else ChartsColors.Crimson
            val alpha = if (selected == null || selected == i) 1f else 0.42f
            drawRoundRect(
                color = baseColor.copy(alpha = alpha),
                topLeft = Offset(left, top),
                size = Size(barW, barH),
                cornerRadius = CornerRadius(3.dp.toPx()),
            )
        }

        drawXAxisDates(insets, tm, series.map { Fmt.dayShort(it.date) }) { i ->
            insets.left + slot * i + slot / 2f
        }

        selected?.let { idx ->
            val pt = series[idx]
            val centerBarX = insets.left + slot * idx + slot / 2f
            drawFloatingBadge(
                insets,
                tm,
                "${Fmt.dayShort(pt.date)} • ${Fmt.pct(pt.daily_pnl_percent)} • C:${pt.buys_count} V:${pt.sells_count}",
                centerBarX,
            )
        }
    }
}

@Composable
fun MonthlyHeatmapGrid(
    items: List<MonthlyReturnItem>,
    isFullscreen: Boolean = false,
) {
    if (items.isEmpty()) {
        EmptyChartPlaceholder(message = "Sem histórico mensal suficiente", height = 130.dp)
        return
    }
    val byYear = remember(items) {
        items.filter { it.year > 0 && it.month in 1..12 }
            .groupBy { it.year }
            .toSortedMap(compareByDescending { it })
    }
    var selectedCell by remember(items) { mutableStateOf<MonthlyReturnItem?>(items.lastOrNull()) }

    val scrollMod = if (isFullscreen) {
        Modifier.fillMaxSize().verticalScroll(rememberScrollState())
    } else {
        Modifier.fillMaxWidth()
    }

    Column(modifier = scrollMod) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.width(42.dp)) {
                    Text("Ano", fontSize = 10.sp, fontWeight = FontWeight.Bold, color = ChartsColors.TextSecondary)
                }
                for (m in 1..12) {
                    Box(Modifier.width(44.dp), contentAlignment = Alignment.Center) {
                        Text(Fmt.monthLabel(m), fontSize = 10.sp, color = ChartsColors.TextSecondary)
                    }
                }
                Box(Modifier.width(52.dp), contentAlignment = Alignment.Center) {
                    Text("Acum.", fontSize = 10.sp, fontWeight = FontWeight.Bold, color = ChartsColors.Cyan)
                }
            }

            byYear.forEach { (year, monthList) ->
                val mapByMonth = monthList.associateBy { it.month }
                val yearFactor = monthList.fold(1.0) { acc, it -> acc * (1.0 + it.return_pct / 100.0) }
                val yearCompoundPct = (yearFactor - 1.0) * 100.0

                Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.width(42.dp)) {
                        Text("$year", fontSize = 11.sp, fontWeight = FontWeight.Bold, color = ChartsColors.TextPrimary)
                    }
                    for (m in 1..12) {
                        val cell = mapByMonth[m]
                        val isSel = cell != null && selectedCell?.year == year && selectedCell?.month == m
                        Box(
                            modifier = Modifier
                                .width(44.dp)
                                .height(30.dp)
                                .clip(RoundedCornerShape(6.dp))
                                .background(
                                    if (cell != null) ChartsColors.heatColor(cell.return_pct)
                                    else ChartsColors.SurfaceElevated.copy(alpha = 0.35f)
                                )
                                .border(
                                    width = if (isSel) 1.5.dp else 0.5.dp,
                                    color = if (isSel) ChartsColors.Cyan else ChartsColors.Outline.copy(alpha = 0.5f),
                                    shape = RoundedCornerShape(6.dp),
                                )
                                .let { mod -> if (cell != null) mod.clickable { selectedCell = cell } else mod },
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(
                                text = if (cell != null) Fmt.pct(cell.return_pct, signed = true, decimals = 1) else "—",
                                fontSize = 9.sp,
                                fontWeight = if (cell != null) FontWeight.SemiBold else FontWeight.Normal,
                                color = if (cell != null) ChartsColors.TextPrimary else ChartsColors.TextDim,
                                textAlign = TextAlign.Center,
                            )
                        }
                    }
                    Box(
                        modifier = Modifier
                            .width(52.dp)
                            .height(30.dp)
                            .clip(RoundedCornerShape(6.dp))
                            .background(ChartsColors.SurfaceElevated),
                        contentAlignment = Alignment.Center,
                    ) {
                        Text(
                            text = Fmt.pct(yearCompoundPct, signed = true, decimals = 1),
                            fontSize = 10.sp,
                            fontWeight = FontWeight.Bold,
                            color = ChartsColors.signed(yearCompoundPct),
                        )
                    }
                }
            }
        }

        selectedCell?.let { sel ->
            Spacer(Modifier.height(8.dp))
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clip(RoundedCornerShape(8.dp))
                    .background(ChartsColors.SurfaceElevated.copy(alpha = 0.65f))
                    .padding(horizontal = 10.dp, vertical = 6.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                Text(
                    text = "${Fmt.monthLabel(sel.month)}/${sel.year}",
                    fontSize = 11.sp,
                    fontWeight = FontWeight.Bold,
                    color = ChartsColors.Cyan,
                )
                Text(
                    text = "Retorno: ${Fmt.pct(sel.return_pct)} • P&L: ${Fmt.brlSigned(sel.pnl_brl)} • Trades: ${sel.trades_count}",
                    fontSize = 11.sp,
                    color = ChartsColors.signed(sel.return_pct),
                )
            }
        }
    }
}
