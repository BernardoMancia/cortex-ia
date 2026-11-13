package com.cortexia.charts.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import com.cortexia.charts.data.EnvTarget

object ChartsColors {
    val Background = Color(0xFF07131F)
    val Surface = Color(0xFF0D1D2E)
    val SurfaceElevated = Color(0xFF13283F)
    val SurfaceBright = Color(0xFF1A3552)
    val Outline = Color(0xFF213E5E)
    val GridLine = Color(0xFF19314B)

    val Cyan = Color(0xFF38BDF8)
    val Emerald = Color(0xFF10B981)
    val Amber = Color(0xFFF59E0B)
    val Crimson = Color(0xFFF43F5E)
    val Indigo = Color(0xFF818CF8)
    val Gold = Color(0xFFFBBF24)
    val Violet = Color(0xFFA855F7)
    val Teal = Color(0xFF14B8A6)

    val TextPrimary = Color(0xFFF1F5F9)
    val TextSecondary = Color(0xFF94A3B8)
    val TextDim = Color(0xFF64748B)

    val SeriesPalette = listOf(
        Color(0xFF38BDF8),
        Color(0xFF10B981),
        Color(0xFFF59E0B),
        Color(0xFF818CF8),
        Color(0xFFEC4899),
        Color(0xFF14B8A6),
        Color(0xFFA855F7),
        Color(0xFFF97316),
        Color(0xFF84CC16),
        Color(0xFFEAB308),
    )

    fun envAccent(env: EnvTarget): Color = when (env) {
        EnvTarget.PRODUCTION -> Emerald
        EnvTarget.HOMOLOGATION -> Amber
    }

    fun signed(value: Double): Color = when {
        value > 0.0001 -> Emerald
        value < -0.0001 -> Crimson
        else -> TextSecondary
    }

    fun heatColor(pct: Double): Color = when {
        pct >= 6.0 -> Color(0xFF059669)
        pct >= 3.0 -> Color(0xFF10B981).copy(alpha = 0.80f)
        pct >= 0.5 -> Color(0xFF10B981).copy(alpha = 0.48f)
        pct > 0.0 -> Color(0xFF10B981).copy(alpha = 0.25f)
        pct <= -6.0 -> Color(0xFFE11D48)
        pct <= -3.0 -> Color(0xFFF43F5E).copy(alpha = 0.80f)
        pct <= -0.5 -> Color(0xFFF43F5E).copy(alpha = 0.48f)
        pct < 0.0 -> Color(0xFFF43F5E).copy(alpha = 0.25f)
        else -> SurfaceElevated
    }
}

private val darkScheme = darkColorScheme(
    primary = ChartsColors.Cyan,
    onPrimary = Color(0xFF031926),
    secondary = ChartsColors.Emerald,
    onSecondary = Color(0xFF022117),
    tertiary = ChartsColors.Amber,
    background = ChartsColors.Background,
    onBackground = ChartsColors.TextPrimary,
    surface = ChartsColors.Surface,
    onSurface = ChartsColors.TextPrimary,
    surfaceVariant = ChartsColors.SurfaceElevated,
    onSurfaceVariant = ChartsColors.TextSecondary,
    surfaceContainer = ChartsColors.Surface,
    surfaceContainerHigh = ChartsColors.SurfaceElevated,
    outline = ChartsColors.Outline,
    error = ChartsColors.Crimson,
)

@Composable
fun CortexChartsTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = darkScheme,
        typography = Typography(),
        content = content,
    )
}
