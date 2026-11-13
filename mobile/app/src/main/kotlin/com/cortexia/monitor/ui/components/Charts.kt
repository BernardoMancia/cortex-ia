package com.cortexia.monitor.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextMeasurer
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.monitor.ui.theme.CortexColors
import java.util.Locale
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.roundToInt

private val AXIS_STYLE = TextStyle(color = CortexColors.TextMuted, fontSize = 10.sp)
private val TIP_STYLE = TextStyle(color = CortexColors.TextPrimary, fontSize = 11.sp)

private class Pads {
    var left = 120f
    var top = 90f
    var bottom = 60f
}

private fun DrawScope.computePads(p: Pads, tm: TextMeasurer, minV: Double, maxV: Double, yFmt: (Double) -> String) {
    p.top = 30.dp.toPx()
    p.bottom = 22.dp.toPx()
    p.left = (0..4).maxOf { tm.measure(yFmt(minV + (maxV - minV) * it / 4.0), AXIS_STYLE).size.width }.toFloat() + 10.dp.toPx()
}

private fun DrawScope.drawGrid(p: Pads, tm: TextMeasurer, minV: Double, maxV: Double, yFmt: (Double) -> String) {
    val chartH = size.height - p.bottom - p.top
    for (i in 0..4) {
        val frac = i / 4f
        val y = p.top + chartH * (1 - frac)
        drawLine(
            CortexColors.Outline, Offset(p.left, y), Offset(size.width, y), 1f,
            pathEffect = PathEffect.dashPathEffect(floatArrayOf(6f, 6f)),
        )
        val layout = tm.measure(yFmt(minV + (maxV - minV) * frac), AXIS_STYLE)
        drawText(layout, topLeft = Offset(p.left - layout.size.width - 6.dp.toPx(), y - layout.size.height / 2f))
    }
}

private fun DrawScope.drawXLabels(p: Pads, tm: TextMeasurer, labels: List<String>, xOf: (Int) -> Float) {
    if (labels.isEmpty()) return
    val count = minOf(5, labels.size)
    val step = max(1, (labels.size - 1) / max(1, count - 1))
    var i = 0
    while (i < labels.size) {
        val layout = tm.measure(labels[i], AXIS_STYLE)
        val x = (xOf(i) - layout.size.width / 2f).coerceIn(p.left, (size.width - layout.size.width).coerceAtLeast(p.left))
        drawText(layout, topLeft = Offset(x, size.height - p.bottom + 4.dp.toPx()))
        i += step
    }
}

private fun DrawScope.drawTooltip(p: Pads, tm: TextMeasurer, text: String, x: Float) {
    val layout = tm.measure(text, TIP_STYLE)
    val padH = 8.dp.toPx()
    val w = layout.size.width + padH * 2
    val h = layout.size.height + 6.dp.toPx()
    val maxLeft = (size.width - w).coerceAtLeast(0f)
    val left = (x - w / 2f).coerceIn(p.left.coerceAtMost(maxLeft), maxLeft)
    drawRoundRect(CortexColors.SurfaceHigh, Offset(left, 0f), Size(w, h), CornerRadius(10f))
    drawRoundRect(CortexColors.Outline, Offset(left, 0f), Size(w, h), CornerRadius(10f), style = Stroke(1f))
    drawText(layout, topLeft = Offset(left + padH, 3.dp.toPx()))
}

private fun Modifier.selectable(key: Any, xToIndex: (Float) -> Int, onSelect: (Int?) -> Unit): Modifier =
    this
        .pointerInput(key) { detectTapGestures { o -> onSelect(xToIndex(o.x)) } }
        .pointerInput(key) {
            detectHorizontalDragGestures { change, _ -> onSelect(xToIndex(change.position.x)) }
        }

@Composable
fun LineChart(
    values: List<Double>,
    labels: List<String>,
    modifier: Modifier = Modifier,
    color: Color = CortexColors.Primary,
    height: Dp = 220.dp,
    yFormatter: (Double) -> String = { it.roundToInt().toString() },
    tooltip: (Int) -> String = { "${labels.getOrElse(it) { "" }}: ${yFormatter(values[it])}" },
    zeroLine: Boolean = false,
) {
    if (values.isEmpty()) {
        EmptyChart(modifier, height); return
    }
    val tm = rememberTextMeasurer()
    val pads = remember { Pads() }
    val widthHolder = remember { floatArrayOf(1f) }
    var selected by remember(values) { mutableStateOf<Int?>(null) }
    val xToIndex = { x: Float ->
        val usable = widthHolder[0] - pads.left
        if (values.size == 1) 0
        else (((x - pads.left) / usable) * (values.size - 1)).roundToInt().coerceIn(0, values.lastIndex)
    }
    Canvas(
        modifier
            .fillMaxWidth()
            .height(height)
            .selectable(values, xToIndex) { selected = it }
    ) {
        widthHolder[0] = size.width
        var minV = values.min()
        var maxV = values.max()
        if (zeroLine) { minV = minOf(minV, 0.0); maxV = maxOf(maxV, 0.0) }
        if (maxV - minV < 1e-9) { maxV += 1.0; minV -= 1.0 }
        val pad = (maxV - minV) * 0.08
        minV -= pad; maxV += pad
        computePads(pads, tm, minV, maxV, yFormatter)
        val chartH = size.height - pads.bottom - pads.top
        val usable = size.width - pads.left
        val xOf = { i: Int -> if (values.size == 1) pads.left + usable / 2 else pads.left + usable * i / (values.size - 1) }
        val yOf = { v: Double -> (pads.top + chartH * (1 - (v - minV) / (maxV - minV))).toFloat() }

        drawGrid(pads, tm, minV, maxV, yFormatter)
        if (zeroLine && minV < 0 && maxV > 0) {
            drawLine(CortexColors.TextMuted, Offset(pads.left, yOf(0.0)), Offset(size.width, yOf(0.0)), 1.5f)
        }
        val line = Path()
        values.forEachIndexed { i, v -> if (i == 0) line.moveTo(xOf(i), yOf(v)) else line.lineTo(xOf(i), yOf(v)) }
        val area = Path().apply {
            addPath(line)
            lineTo(xOf(values.lastIndex), pads.top + chartH)
            lineTo(xOf(0), pads.top + chartH)
            close()
        }
        drawPath(area, Brush.verticalGradient(listOf(color.copy(alpha = 0.35f), Color.Transparent), pads.top, pads.top + chartH))
        drawPath(line, color, style = Stroke(width = 2.dp.toPx(), cap = StrokeCap.Round))
        if (values.size <= 40) values.forEachIndexed { i, v -> drawCircle(color, 2.dp.toPx(), Offset(xOf(i), yOf(v))) }
        drawXLabels(pads, tm, labels, xOf)

        selected?.let { i ->
            val x = xOf(i)
            drawLine(CortexColors.TextMuted, Offset(x, pads.top), Offset(x, pads.top + chartH), 1.5f)
            drawCircle(Color.White, 5.dp.toPx(), Offset(x, yOf(values[i])))
            drawCircle(color, 3.dp.toPx(), Offset(x, yOf(values[i])))
            drawTooltip(pads, tm, tooltip(i), x)
        }
    }
}

@Composable
fun BarChart(
    values: List<Double>,
    labels: List<String>,
    modifier: Modifier = Modifier,
    height: Dp = 200.dp,
    positive: Color = CortexColors.Positive,
    negative: Color = CortexColors.Negative,
    yFormatter: (Double) -> String = { String.format(Locale.US, "%.1f", it) },
    tooltip: (Int) -> String = { "${labels.getOrElse(it) { "" }}: ${yFormatter(values[it])}" },
) {
    if (values.isEmpty()) {
        EmptyChart(modifier, height); return
    }
    val tm = rememberTextMeasurer()
    val pads = remember { Pads() }
    val widthHolder = remember { floatArrayOf(1f) }
    var selected by remember(values) { mutableStateOf<Int?>(null) }
    val xToIndex = { x: Float ->
        val slot = (widthHolder[0] - pads.left) / values.size
        ((x - pads.left) / slot).toInt().coerceIn(0, values.lastIndex)
    }
    Canvas(
        modifier
            .fillMaxWidth()
            .height(height)
            .selectable(values, xToIndex) { selected = it }
    ) {
        widthHolder[0] = size.width
        var minV = minOf(values.min(), 0.0)
        var maxV = maxOf(values.max(), 0.0)
        if (maxV - minV < 1e-9) maxV += 1.0
        val pad = (maxV - minV) * 0.08
        if (minV < 0) minV -= pad
        maxV += pad
        computePads(pads, tm, minV, maxV, yFormatter)
        val chartH = size.height - pads.bottom - pads.top
        val slot = (size.width - pads.left) / values.size
        val barW = (slot * 0.7f).coerceAtLeast(2f)
        val yOf = { v: Double -> (pads.top + chartH * (1 - (v - minV) / (maxV - minV))).toFloat() }
        drawGrid(pads, tm, minV, maxV, yFormatter)
        val zeroY = yOf(0.0)
        drawLine(CortexColors.TextMuted, Offset(pads.left, zeroY), Offset(size.width, zeroY), 1.5f)
        values.forEachIndexed { i, v ->
            val left = pads.left + slot * i + (slot - barW) / 2
            val top = minOf(yOf(v), zeroY)
            val h = abs(yOf(v) - zeroY).coerceAtLeast(1f)
            val c = if (v >= 0) positive else negative
            drawRoundRect(
                if (selected == null || selected == i) c else c.copy(alpha = 0.45f),
                Offset(left, top), Size(barW, h), CornerRadius(3f),
            )
        }
        drawXLabels(pads, tm, labels) { i -> pads.left + slot * i + slot / 2 }
        selected?.let { i -> drawTooltip(pads, tm, tooltip(i), pads.left + slot * i + slot / 2) }
    }
}

@Composable
fun GroupedBarChart(
    a: List<Double>,
    b: List<Double>,
    labels: List<String>,
    modifier: Modifier = Modifier,
    colorA: Color = CortexColors.Positive,
    colorB: Color = CortexColors.Negative,
    nameA: String = "A",
    nameB: String = "B",
    height: Dp = 200.dp,
) {
    if (a.isEmpty()) {
        EmptyChart(modifier, height); return
    }
    val tm = rememberTextMeasurer()
    val pads = remember { Pads() }
    val widthHolder = remember { floatArrayOf(1f) }
    var selected by remember(a, b) { mutableStateOf<Int?>(null) }
    val xToIndex = { x: Float ->
        val slot = (widthHolder[0] - pads.left) / a.size
        ((x - pads.left) / slot).toInt().coerceIn(0, a.lastIndex)
    }
    val fmt = { v: Double -> v.roundToInt().toString() }
    Column(modifier) {
        Canvas(
            Modifier
                .fillMaxWidth()
                .height(height)
                .selectable(a to b, xToIndex) { selected = it }
        ) {
            widthHolder[0] = size.width
            val maxV = maxOf(1.0, (a + b).max()) * 1.1
            computePads(pads, tm, 0.0, maxV, fmt)
            val chartH = size.height - pads.bottom - pads.top
            val slot = (size.width - pads.left) / a.size
            val barW = (slot * 0.38f).coerceAtLeast(1.5f)
            val yOf = { v: Double -> (pads.top + chartH * (1 - v / maxV)).toFloat() }
            drawGrid(pads, tm, 0.0, maxV, fmt)
            a.indices.forEach { i ->
                val base = pads.left + slot * i + slot * 0.1f
                val alpha = if (selected == null || selected == i) 1f else 0.45f
                drawRect(colorA.copy(alpha = alpha), Offset(base, yOf(a[i])), Size(barW, pads.top + chartH - yOf(a[i])))
                drawRect(colorB.copy(alpha = alpha), Offset(base + barW, yOf(b[i])), Size(barW, pads.top + chartH - yOf(b[i])))
            }
            drawXLabels(pads, tm, labels) { i -> pads.left + slot * i + slot / 2 }
            selected?.let { i ->
                drawTooltip(
                    pads, tm,
                    "${labels.getOrElse(i) { "" }} · $nameA ${a[i].roundToInt()} · $nameB ${b[i].roundToInt()}",
                    pads.left + slot * i + slot / 2,
                )
            }
        }
        Row(Modifier.padding(start = 8.dp, top = 4.dp), horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            LegendDot(colorA, nameA)
            LegendDot(colorB, nameB)
        }
    }
}

@Composable
fun DonutChart(slices: List<Pair<String, Double>>, modifier: Modifier = Modifier, valueFormatter: (Double) -> String) {
    val total = slices.sumOf { it.second }
    if (slices.isEmpty() || total <= 0) {
        EmptyChart(modifier, 160.dp); return
    }
    Row(modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(140.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.size(140.dp)) {
                var start = -90f
                val stroke = 22.dp.toPx()
                slices.forEachIndexed { i, (_, v) ->
                    val sweep = (v / total * 360f).toFloat()
                    drawArc(
                        CortexColors.Palette[i % CortexColors.Palette.size], start, (sweep - 1.2f).coerceAtLeast(0.5f), false,
                        topLeft = Offset(stroke / 2, stroke / 2),
                        size = Size(size.width - stroke, size.height - stroke),
                        style = Stroke(stroke),
                    )
                    start += sweep
                }
            }
            Text(
                "${slices.size}\nitens",
                style = TextStyle(color = CortexColors.TextMuted, fontSize = 12.sp, textAlign = TextAlign.Center),
            )
        }
        Spacer(Modifier.width(12.dp))
        Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
            slices.take(10).forEachIndexed { i, (name, v) ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(10.dp).clip(CircleShape).background(CortexColors.Palette[i % CortexColors.Palette.size]))
                    Spacer(Modifier.width(6.dp))
                    Text(
                        "$name  ${String.format(Locale.forLanguageTag("pt-BR"), "%.1f", v / total * 100)}%",
                        style = TextStyle(color = CortexColors.TextPrimary, fontSize = 12.sp),
                    )
                }
                Text(
                    valueFormatter(v),
                    style = TextStyle(color = CortexColors.TextMuted, fontSize = 10.sp),
                    modifier = Modifier.padding(start = 16.dp),
                )
            }
        }
    }
}

@Composable
fun HorizontalBars(items: List<Pair<String, Double>>, valueFormatter: (Double) -> String, modifier: Modifier = Modifier) {
    if (items.isEmpty()) {
        EmptyChart(modifier, 80.dp); return
    }
    val maxAbs = items.maxOf { abs(it.second) }.takeIf { it > 0 } ?: 1.0
    Column(modifier, verticalArrangement = Arrangement.spacedBy(6.dp)) {
        items.forEach { (name, v) ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(name, style = TextStyle(color = CortexColors.TextPrimary, fontSize = 12.sp), modifier = Modifier.width(64.dp))
                Box(Modifier.weight(1f).height(14.dp)) {
                    Box(
                        Modifier
                            .fillMaxWidth((abs(v) / maxAbs).toFloat().coerceIn(0.02f, 1f))
                            .height(14.dp)
                            .clip(RoundedCornerShape(4.dp))
                            .background(CortexColors.signed(v))
                    )
                }
                Text(
                    valueFormatter(v),
                    style = TextStyle(color = CortexColors.signed(v), fontSize = 12.sp, textAlign = TextAlign.End),
                    modifier = Modifier.width(100.dp).padding(start = 6.dp),
                )
            }
        }
    }
}

@Composable
fun LegendDot(color: Color, label: String) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(10.dp).clip(CircleShape).background(color))
        Spacer(Modifier.width(4.dp))
        Text(label, style = TextStyle(color = CortexColors.TextMuted, fontSize = 11.sp))
    }
}

@Composable
fun EmptyChart(modifier: Modifier, height: Dp) {
    Box(modifier.fillMaxWidth().height(height), contentAlignment = Alignment.Center) {
        Text("Sem dados suficientes ainda", style = TextStyle(color = CortexColors.TextMuted, fontSize = 13.sp))
    }
}
