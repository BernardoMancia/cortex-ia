package com.cortexia.monitor.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

object CortexColors {
    val Background = Color(0xFF0B1220)
    val Surface = Color(0xFF121A2B)
    val SurfaceHigh = Color(0xFF1A2438)
    val Outline = Color(0xFF2A3650)
    val Primary = Color(0xFF22D3EE)
    val Secondary = Color(0xFF818CF8)
    val Positive = Color(0xFF22C55E)
    val Negative = Color(0xFFEF4444)
    val Warning = Color(0xFFF59E0B)
    val TextPrimary = Color(0xFFE5E7EB)
    val TextMuted = Color(0xFF94A3B8)
    val Palette = listOf(
        Color(0xFF22D3EE), Color(0xFF818CF8), Color(0xFF22C55E), Color(0xFFF59E0B),
        Color(0xFFEC4899), Color(0xFF14B8A6), Color(0xFFA855F7), Color(0xFFF97316),
        Color(0xFF84CC16), Color(0xFF64748B),
    )

    fun signed(v: Double): Color = when {
        v > 0 -> Positive
        v < 0 -> Negative
        else -> TextMuted
    }
}

private val scheme = darkColorScheme(
    primary = CortexColors.Primary,
    onPrimary = Color(0xFF00222A),
    secondary = CortexColors.Secondary,
    background = CortexColors.Background,
    onBackground = CortexColors.TextPrimary,
    surface = CortexColors.Surface,
    onSurface = CortexColors.TextPrimary,
    surfaceVariant = CortexColors.SurfaceHigh,
    onSurfaceVariant = CortexColors.TextMuted,
    surfaceContainer = CortexColors.Surface,
    surfaceContainerHigh = CortexColors.SurfaceHigh,
    outline = CortexColors.Outline,
    error = CortexColors.Negative,
)

@Composable
fun CortexTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = scheme, typography = Typography(), content = content)
}
