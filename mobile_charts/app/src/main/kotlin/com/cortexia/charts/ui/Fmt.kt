package com.cortexia.charts.ui

import java.text.NumberFormat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.abs

object Fmt {
    private val ptBR: Locale = Locale.forLanguageTag("pt-BR")

    fun brl(value: Double?): String {
        if (value == null || value.isNaN() || value.isInfinite()) return "—"
        return NumberFormat.getCurrencyInstance(ptBR).format(value)
    }

    fun brlSigned(value: Double?): String {
        if (value == null || value.isNaN() || value.isInfinite()) return "—"
        val formatted = NumberFormat.getCurrencyInstance(ptBR).format(abs(value))
        return when {
            value > 0.004 -> "+$formatted"
            value < -0.004 -> "-$formatted"
            else -> formatted
        }
    }

    fun brlCompact(value: Double): String {
        if (value.isNaN() || value.isInfinite()) return "—"
        val a = abs(value)
        val sign = if (value < 0) "-" else ""
        return when {
            a >= 1_000_000.0 -> String.format(ptBR, "%sR$ %.2fM", sign, a / 1_000_000.0)
            a >= 1_000.0 -> String.format(ptBR, "%sR$ %.1fk", sign, a / 1_000.0)
            else -> String.format(ptBR, "%sR$ %.0f", sign, a)
        }
    }

    fun pct(value: Double?, signed: Boolean = true, decimals: Int = 2): String {
        if (value == null || value.isNaN() || value.isInfinite()) return "—"
        val pattern = if (signed) "%+.${decimals}f%%" else "%.${decimals}f%%"
        return String.format(ptBR, pattern, value)
    }

    fun ratio(value: Double?, decimals: Int = 2): String {
        if (value == null || value.isNaN()) return "—"
        if (value.isInfinite() || value >= 99.0) return "99,9+"
        return String.format(ptBR, "%,.${decimals}f", value)
    }

    fun dayShort(date: String): String {
        val clean = date.take(10)
        return if (clean.length == 10 && clean[4] == '-' && clean[7] == '-') {
            "${clean.substring(8, 10)}/${clean.substring(5, 7)}"
        } else {
            date
        }
    }

    fun dateTimeShort(iso: String?): String {
        if (iso.isNullOrBlank()) return "—"
        val norm = iso.replace(' ', 'T')
        val day = dayShort(norm.take(10))
        val hm = norm.substringAfter('T', "").take(5)
        return if (hm.isNotEmpty()) "$day $hm" else day
    }

    fun clock(millis: Long?): String {
        if (millis == null || millis <= 0L) return "—"
        return SimpleDateFormat("HH:mm:ss", ptBR).format(Date(millis))
    }

    fun monthLabel(month: Int): String = when (month) {
        1 -> "Jan"
        2 -> "Fev"
        3 -> "Mar"
        4 -> "Abr"
        5 -> "Mai"
        6 -> "Jun"
        7 -> "Jul"
        8 -> "Ago"
        9 -> "Set"
        10 -> "Out"
        11 -> "Nov"
        12 -> "Dez"
        else -> "$month"
    }
}
