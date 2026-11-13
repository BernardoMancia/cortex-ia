package com.cortexia.charts.ui.charts

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
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
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
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
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.charts.data.PositionItem
import com.cortexia.charts.ui.Fmt
import com.cortexia.charts.ui.components.EmptyChartPlaceholder
import com.cortexia.charts.ui.components.LegendDotItem
import com.cortexia.charts.ui.theme.ChartsColors
import kotlin.math.atan2
import kotlin.math.hypot
import kotlin.math.max
import kotlin.math.min

data class DonutSlice(
    val label: String,
    val value: Double,
    val color: Color,
    val subtitle: String? = null,
)

@Composable
fun InteractiveDonutChart(
    slices: List<DonutSlice>,
    valueFormatter: (Double) -> String = Fmt::brl,
    centerTitleWhenIdle: String = "Total",
    isFullscreen: Boolean = false,
) {
    val positiveSlices = remember(slices) { slices.filter { it.value > 0.0 } }
    val total = remember(positiveSlices) { positiveSlices.sumOf { it.value } }
    if (positiveSlices.isEmpty() || total <= 0.0) {
        EmptyChartPlaceholder(height = 170.dp)
        return
    }

    var selectedIdx by remember(positiveSlices) { mutableStateOf<Int?>(0) }
    val ringSize = if (isFullscreen) 220.dp else 164.dp

    val angles = remember(positiveSlices, total) {
        var cursor = 0f
        positiveSlices.map { s ->
            val sweep = ((s.value / total) * 360.0).toFloat()
            val pair = cursor to (cursor + sweep)
            cursor += sweep
            pair
        }
    }

    val containerMod = if (isFullscreen) Modifier.fillMaxSize() else Modifier.fillMaxWidth()

    Row(
        modifier = containerMod,
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Box(
            modifier = Modifier.size(ringSize),
            contentAlignment = Alignment.Center,
        ) {
            Canvas(
                modifier = Modifier
                    .fillMaxSize()
                    .pointerInput(positiveSlices) {
                        detectTapGestures { tap ->
                            val cx = size.width / 2f
                            val cy = size.height / 2f
                            val dx = tap.x - cx
                            val dy = tap.y - cy
                            val dist = hypot(dx, dy)
                            val outerR = min(size.width, size.height) / 2f
                            if (dist in (outerR * 0.32f)..(outerR * 1.05f)) {
                                var deg = Math.toDegrees(atan2(dy.toDouble(), dx.toDouble())).toFloat() + 90f
                                if (deg < 0f) deg += 360f
                                val hit = angles.indexOfFirst { (start, end) -> deg >= start && deg <= end }
                                if (hit >= 0) {
                                    selectedIdx = if (selectedIdx == hit) null else hit
                                }
                            } else {
                                selectedIdx = null
                            }
                        }
                    }
            ) {
                val baseStroke = if (isFullscreen) 28.dp.toPx() else 22.dp.toPx()
                val selStroke = baseStroke + 5.dp.toPx()
                val pad = selStroke / 2f + 2.dp.toPx()
                var startAngle = -90f
                positiveSlices.forEachIndexed { idx, slice ->
                    val sweep = ((slice.value / total) * 360.0).toFloat()
                    val isSel = selectedIdx == idx
                    val alpha = if (selectedIdx == null || isSel) 1f else 0.42f
                    val st = if (isSel) selStroke else baseStroke
                    drawArc(
                        color = slice.color.copy(alpha = alpha),
                        startAngle = startAngle,
                        sweepAngle = (sweep - 1.5f).coerceAtLeast(0.8f),
                        useCenter = false,
                        topLeft = Offset(pad, pad),
                        size = Size(size.width - pad * 2f, size.height - pad * 2f),
                        style = Stroke(width = st, cap = StrokeCap.Butt),
                    )
                    startAngle += sweep
                }
            }

            val active = selectedIdx?.let { positiveSlices.getOrNull(it) }
            Column(
                horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier.padding(horizontal = 24.dp),
            ) {
                Text(
                    text = active?.label ?: centerTitleWhenIdle,
                    fontSize = 11.sp,
                    color = active?.color ?: ChartsColors.TextSecondary,
                    fontWeight = FontWeight.SemiBold,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Text(
                    text = valueFormatter(active?.value ?: total),
                    fontSize = if (isFullscreen) 15.sp else 13.sp,
                    color = ChartsColors.TextPrimary,
                    fontWeight = FontWeight.Bold,
                    textAlign = TextAlign.Center,
                    maxLines = 1,
                )
                if (active != null) {
                    Text(
                        text = Fmt.pct((active.value / total) * 100.0, signed = false, decimals = 1),
                        fontSize = 11.sp,
                        color = ChartsColors.TextSecondary,
                    )
                }
            }
        }

        Column(
            modifier = Modifier
                .weight(1f)
                .let { if (isFullscreen) it.fillMaxHeight().verticalScroll(rememberScrollState()) else it },
            verticalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            positiveSlices.forEachIndexed { idx, slice ->
                val isSel = selectedIdx == idx
                val sharePct = (slice.value / total) * 100.0
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(8.dp))
                        .background(if (isSel) slice.color.copy(alpha = 0.14f) else Color.Transparent)
                        .clickable { selectedIdx = if (isSel) null else idx }
                        .padding(horizontal = 8.dp, vertical = 5.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        Modifier
                            .size(10.dp)
                            .clip(CircleShape)
                            .background(slice.color)
                    )
                    Spacer(Modifier.width(8.dp))
                    Column(Modifier.weight(1f)) {
                        Text(
                            text = slice.label,
                            fontSize = 12.sp,
                            fontWeight = if (isSel) FontWeight.Bold else FontWeight.Medium,
                            color = ChartsColors.TextPrimary,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            text = slice.subtitle ?: valueFormatter(slice.value),
                            fontSize = 10.sp,
                            color = ChartsColors.TextSecondary,
                            maxLines = 1,
                        )
                    }
                    Text(
                        text = Fmt.pct(sharePct, signed = false, decimals = 1),
                        fontSize = 12.sp,
                        fontWeight = FontWeight.Bold,
                        color = slice.color,
                    )
                }
            }
        }
    }
}

data class TreemapNode(
    val title: String,
    val sizeValue: Double,
    val metricPct: Double,
    val valueFormatted: String,
    val detailLine: String,
)

private fun layoutTreemap(nodes: List<TreemapNode>, bounds: Rect): List<Pair<TreemapNode, Rect>> {
    if (nodes.isEmpty() || bounds.width <= 1f || bounds.height <= 1f) return emptyList()
    if (nodes.size == 1) return listOf(nodes.first() to bounds)
    val total = nodes.sumOf { it.sizeValue }.takeIf { it > 0.0 } ?: return emptyList()
    var acc = 0.0
    var splitIdx = 1
    for (i in 0 until nodes.lastIndex) {
        acc += nodes[i].sizeValue
        splitIdx = i + 1
        if (acc >= total * 0.5) break
    }
    val leftNodes = nodes.subList(0, splitIdx)
    val rightNodes = nodes.subList(splitIdx, nodes.size)
    val ratio = (leftNodes.sumOf { it.sizeValue } / total).toFloat().coerceIn(0.12f, 0.88f)
    return if (bounds.width >= bounds.height) {
        val midX = bounds.left + bounds.width * ratio
        layoutTreemap(leftNodes, Rect(bounds.left, bounds.top, midX, bounds.bottom)) +
            layoutTreemap(rightNodes, Rect(midX, bounds.top, bounds.right, bounds.bottom))
    } else {
        val midY = bounds.top + bounds.height * ratio
        layoutTreemap(leftNodes, Rect(bounds.left, bounds.top, bounds.right, midY)) +
            layoutTreemap(rightNodes, Rect(bounds.left, midY, bounds.right, bounds.bottom))
    }
}

@Composable
fun SectorTreemapChart(
    nodes: List<TreemapNode>,
    isFullscreen: Boolean = false,
    height: Dp = 210.dp,
) {
    val cleanNodes = remember(nodes) {
        nodes.filter { it.sizeValue > 0.0 }.sortedByDescending { it.sizeValue }
    }
    if (cleanNodes.isEmpty()) {
        EmptyChartPlaceholder(message = "Sem exposição setorial para exibir", height = height)
        return
    }
    val tm = rememberTextMeasurer()
    var selectedTitle by remember(cleanNodes) { mutableStateOf<String?>(cleanNodes.first().title) }
    val rectsHolder = remember { mutableListOf<Pair<TreemapNode, Rect>>() }

    Column(modifier = if (isFullscreen) Modifier.fillMaxSize() else Modifier.fillMaxWidth()) {
        val canvasMod = if (isFullscreen) {
            Modifier.weight(1f).fillMaxWidth()
        } else {
            Modifier.fillMaxWidth().height(height)
        }

        Canvas(
            modifier = canvasMod.pointerInput(cleanNodes) {
                detectTapGestures { tap ->
                    val hit = rectsHolder.firstOrNull { (_, r) -> r.contains(tap) }
                    if (hit != null) {
                        selectedTitle = hit.first.title
                    }
                }
            }
        ) {
            val laidOut = layoutTreemap(cleanNodes, Rect(0f, 0f, size.width, size.height))
            rectsHolder.clear()
            rectsHolder.addAll(laidOut)
            val gap = 2.5.dp.toPx()

            laidOut.forEach { (node, rawRect) ->
                val r = Rect(
                    left = rawRect.left + gap,
                    top = rawRect.top + gap,
                    right = (rawRect.right - gap).coerceAtLeast(rawRect.left + gap + 2f),
                    bottom = (rawRect.bottom - gap).coerceAtLeast(rawRect.top + gap + 2f),
                )
                val fill = ChartsColors.heatColor(node.metricPct)
                val isSel = selectedTitle == node.title
                drawRoundRect(
                    color = fill,
                    topLeft = r.topLeft,
                    size = r.size,
                    cornerRadius = CornerRadius(8.dp.toPx()),
                )
                drawRoundRect(
                    color = if (isSel) ChartsColors.Cyan else ChartsColors.Background.copy(alpha = 0.6f),
                    topLeft = r.topLeft,
                    size = r.size,
                    cornerRadius = CornerRadius(8.dp.toPx()),
                    style = Stroke(width = if (isSel) 2.dp.toPx() else 1.dp.toPx()),
                )

                if (r.width > 52.dp.toPx() && r.height > 34.dp.toPx()) {
                    val titleLayout = tm.measure(
                        node.title,
                        TextStyle(color = ChartsColors.TextPrimary, fontSize = 11.sp, fontWeight = FontWeight.Bold),
                    )
                    val pctLayout = tm.measure(
                        Fmt.pct(node.metricPct, signed = true, decimals = 1),
                        TextStyle(color = ChartsColors.TextPrimary.copy(alpha = 0.92f), fontSize = 10.sp),
                    )
                    if (titleLayout.size.width < r.width - 8.dp.toPx()) {
                        drawText(titleLayout, topLeft = Offset(r.left + 6.dp.toPx(), r.top + 5.dp.toPx()))
                        if (r.height > 44.dp.toPx() && pctLayout.size.width < r.width - 8.dp.toPx()) {
                            drawText(
                                pctLayout,
                                topLeft = Offset(r.left + 6.dp.toPx(), r.top + 6.dp.toPx() + titleLayout.size.height),
                            )
                        }
                    }
                }
            }
        }

        val active = cleanNodes.firstOrNull { it.title == selectedTitle } ?: cleanNodes.first()
        Spacer(Modifier.height(8.dp))
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp))
                .background(ChartsColors.SurfaceElevated.copy(alpha = 0.7f))
                .padding(horizontal = 10.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(active.title, fontSize = 12.sp, fontWeight = FontWeight.Bold, color = ChartsColors.Cyan)
                Text(active.detailLine, fontSize = 10.sp, color = ChartsColors.TextSecondary)
            }
            Text(
                text = "${active.valueFormatted} (${Fmt.pct(active.metricPct)})",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = ChartsColors.signed(active.metricPct),
            )
        }
    }
}

@Composable
fun PositionBulletChart(
    positions: List<PositionItem>,
    isFullscreen: Boolean = false,
) {
    if (positions.isEmpty()) {
        EmptyChartPlaceholder(message = "Nenhuma posição aberta na carteira ativa", height = 140.dp)
        return
    }
    val mod = if (isFullscreen) {
        Modifier.fillMaxSize().verticalScroll(rememberScrollState())
    } else {
        Modifier.fillMaxWidth()
    }

    Column(modifier = mod, verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            LegendDotItem(ChartsColors.Crimson, "Stop-Loss")
            LegendDotItem(ChartsColors.TextPrimary, "PM Entrada")
            LegendDotItem(ChartsColors.Cyan, "Atual")
            LegendDotItem(ChartsColors.Gold, "Topo Trailing")
        }

        positions.forEach { pos ->
            PositionSingleBulletRow(pos)
        }
    }
}

@Composable
private fun PositionSingleBulletRow(pos: PositionItem) {
    val cur = if (pos.current_price > 0.0) pos.current_price else pos.entry_price
    val stop = pos.stop_loss?.takeIf { it > 0.0 }
    val high = pos.highest_price?.takeIf { it > 0.0 } ?: max(cur, pos.entry_price)
    val stopDist = pos.computedStopDistancePct

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(12.dp))
            .background(ChartsColors.SurfaceElevated.copy(alpha = 0.55f))
            .border(1.dp, ChartsColors.Outline, RoundedCornerShape(12.dp))
            .padding(10.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(pos.ticker, fontSize = 14.sp, fontWeight = FontWeight.Bold, color = ChartsColors.TextPrimary)
            Spacer(Modifier.width(6.dp))
            Text(pos.sector, fontSize = 10.sp, color = ChartsColors.TextSecondary)
            if (pos.do_not_touch) {
                Spacer(Modifier.width(6.dp))
                Text("TRAVA", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = ChartsColors.Amber)
            }
            if (pos.partial_tp_taken) {
                Spacer(Modifier.width(6.dp))
                Text("TP PARCIAL", fontSize = 9.sp, fontWeight = FontWeight.Bold, color = ChartsColors.Emerald)
            }
            Spacer(Modifier.weight(1f))
            Text(
                text = "${Fmt.brlSigned(pos.unrealized_pnl)} (${Fmt.pct(pos.pnl_percent)})",
                fontSize = 12.sp,
                fontWeight = FontWeight.Bold,
                color = ChartsColors.signed(pos.unrealized_pnl),
            )
        }

        Spacer(Modifier.height(8.dp))

        Canvas(
            modifier = Modifier
                .fillMaxWidth()
                .height(26.dp),
        ) {
            val candidates = buildList {
                add(pos.entry_price)
                add(cur)
                if (stop != null) add(stop)
                if (high > 0.0) add(high)
            }.filter { it > 0.0 }
            var minP = candidates.minOrNull() ?: 1.0
            var maxP = candidates.maxOrNull() ?: 2.0
            if (maxP - minP < 1e-6) {
                minP *= 0.95
                maxP *= 1.05
            } else {
                val pad = (maxP - minP) * 0.14
                minP = (minP - pad).coerceAtLeast(0.01)
                maxP += pad
            }
            val padX = 10.dp.toPx()
            val trackW = (size.width - padX * 2f).coerceAtLeast(1f)
            val cy = size.height / 2f
            val xOf = { p: Double -> (padX + trackW * ((p - minP) / (maxP - minP))).toFloat() }

            drawRoundRect(
                color = ChartsColors.Background,
                topLeft = Offset(padX, cy - 4.dp.toPx()),
                size = Size(trackW, 8.dp.toPx()),
                cornerRadius = CornerRadius(4.dp.toPx()),
            )

            if (stop != null) {
                val sx = xOf(stop)
                drawRoundRect(
                    color = ChartsColors.Crimson.copy(alpha = 0.28f),
                    topLeft = Offset(padX, cy - 4.dp.toPx()),
                    size = Size((sx - padX).coerceAtLeast(2f), 8.dp.toPx()),
                    cornerRadius = CornerRadius(4.dp.toPx()),
                )
            }

            val entryX = xOf(pos.entry_price)
            val curX = xOf(cur)
            val spanLeft = min(entryX, curX)
            val spanW = kotlin.math.abs(curX - entryX).coerceAtLeast(2f)
            drawRoundRect(
                color = (if (cur >= pos.entry_price) ChartsColors.Emerald else ChartsColors.Crimson).copy(alpha = 0.55f),
                topLeft = Offset(spanLeft, cy - 4.dp.toPx()),
                size = Size(spanW, 8.dp.toPx()),
                cornerRadius = CornerRadius(4.dp.toPx()),
            )

            if (stop != null) {
                val sx = xOf(stop)
                drawLine(
                    color = ChartsColors.Crimson,
                    start = Offset(sx, cy - 9.dp.toPx()),
                    end = Offset(sx, cy + 9.dp.toPx()),
                    strokeWidth = 2.5.dp.toPx(),
                    cap = StrokeCap.Round,
                )
            }

            drawLine(
                color = ChartsColors.TextPrimary,
                start = Offset(entryX, cy - 8.dp.toPx()),
                end = Offset(entryX, cy + 8.dp.toPx()),
                strokeWidth = 2.dp.toPx(),
                cap = StrokeCap.Round,
            )

            if (high > 0.0) {
                val hx = xOf(high)
                val d = 5.dp.toPx()
                val diamond = Path().apply {
                    moveTo(hx, cy - d)
                    lineTo(hx + d, cy)
                    lineTo(hx, cy + d)
                    lineTo(hx - d, cy)
                    close()
                }
                drawPath(diamond, ChartsColors.Gold)
            }

            drawCircle(Color.White, radius = 6.dp.toPx(), center = Offset(curX, cy))
            drawCircle(
                color = if (cur >= pos.entry_price) ChartsColors.Emerald else ChartsColors.Cyan,
                radius = 4.dp.toPx(),
                center = Offset(curX, cy),
            )
        }

        Spacer(Modifier.height(4.dp))
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                text = "Stop: ${if (stop != null) Fmt.brl(stop) else "—"}",
                fontSize = 10.sp,
                color = ChartsColors.Crimson,
            )
            Text(
                text = "PM: ${Fmt.brl(pos.entry_price)}",
                fontSize = 10.sp,
                color = ChartsColors.TextSecondary,
            )
            Text(
                text = "Atual: ${Fmt.brl(cur)}",
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = ChartsColors.Cyan,
            )
            Text(
                text = if (stopDist != null) "Dist. Stop: ${Fmt.pct(stopDist, signed = false)}" else "Peso: ${Fmt.pct(pos.weight_pct, signed = false)}",
                fontSize = 10.sp,
                fontWeight = FontWeight.SemiBold,
                color = if ((stopDist ?: 10.0) < 2.5) ChartsColors.Amber else ChartsColors.Emerald,
            )
        }
    }
}
