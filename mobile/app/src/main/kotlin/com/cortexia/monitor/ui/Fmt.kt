package com.cortexia.monitor.ui

import java.text.NumberFormat
import java.util.Locale
import kotlin.math.abs

object Fmt {
    private val ptBR: Locale = Locale.forLanguageTag("pt-BR")
    private val currency: NumberFormat = NumberFormat.getCurrencyInstance(ptBR)

    fun brl(v: Double?): String = if (v == null) "—" else currency.format(v)

    fun brlCompact(v: Double): String {
        val a = abs(v)
        return when {
            a >= 1_000_000 -> String.format(ptBR, "R$ %.2fM", v / 1_000_000)
            a >= 10_000 -> String.format(ptBR, "R$ %.1fk", v / 1_000)
            else -> String.format(ptBR, "R$ %.0f", v)
        }
    }

    fun pct(v: Double?, signed: Boolean = true): String =
        if (v == null || v.isNaN()) "—" else String.format(ptBR, if (signed) "%+.2f%%" else "%.2f%%", v)

    fun num(v: Double?, decimals: Int = 2): String =
        if (v == null || v.isNaN()) "—" else if (v.isInfinite()) "∞" else String.format(ptBR, "%,.${decimals}f", v)

    fun dayShort(date: String): String {
        val d = date.take(10)
        return if (d.length == 10) "${d.substring(8, 10)}/${d.substring(5, 7)}" else date
    }

    fun dateTime(ts: String?): String {
        if (ts.isNullOrBlank()) return "—"
        val t = ts.replace(' ', 'T')
        val date = dayShort(t.take(10))
        val time = t.substringAfter('T', "").take(5)
        return if (time.isNotEmpty()) "$date $time" else date
    }

    fun ago(millis: Long?): String {
        if (millis == null) return "nunca"
        val s = (System.currentTimeMillis() - millis) / 1000
        return when {
            s < 5 -> "agora"
            s < 60 -> "há ${s}s"
            s < 3600 -> "há ${s / 60} min"
            s < 86_400 -> "há ${s / 3600} h"
            else -> "há ${s / 86_400} d"
        }
    }

    fun clock(millis: Long): String =
        java.text.SimpleDateFormat("dd/MM HH:mm:ss", ptBR).format(java.util.Date(millis))
}
