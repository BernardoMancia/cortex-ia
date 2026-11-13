package com.cortexia.monitor.alerts

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.cortexia.monitor.MainActivity
import com.cortexia.monitor.R
import java.util.concurrent.atomic.AtomicInteger

object Notifier {
    const val CH_CRITICAL = "cortex_critical"
    const val CH_TRADES = "cortex_trades"
    const val CH_INFO = "cortex_info"
    private val ids = AtomicInteger((System.currentTimeMillis() % 100_000).toInt())

    fun createChannels(ctx: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CH_CRITICAL, "Alertas críticos", NotificationManager.IMPORTANCE_HIGH).apply {
                description = "Servidor offline, perda diária, stop-loss, erros do motor"
                enableVibration(true)
            }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_TRADES, "Operações (compra/venda)", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Novas ordens executadas pelo Córtex IA"
            }
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_INFO, "Informativos", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Abertura/fechamento da B3, variação de patrimônio, ambiente"
            }
        )
    }

    fun canNotify(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun channelFor(kind: AlertKind) = when {
        kind == AlertKind.TRADE -> CH_TRADES
        kind.severity == Severity.INFO -> CH_INFO
        else -> CH_CRITICAL
    }

    fun show(ctx: Context, alert: Alert) {
        if (!canNotify(ctx)) return
        val intent = Intent(ctx, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra("open_tab", "alerts")
        }
        val pi = PendingIntent.getActivity(
            ctx, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val color = when (alert.kind.severity) {
            Severity.CRITICAL -> 0xFFEF4444.toInt()
            Severity.WARNING -> 0xFFF59E0B.toInt()
            Severity.INFO -> 0xFF22D3EE.toInt()
        }
        val n = NotificationCompat.Builder(ctx, channelFor(alert.kind))
            .setSmallIcon(R.drawable.ic_stat_cortex)
            .setColor(color)
            .setContentTitle(alert.title)
            .setContentText(alert.message.lineSequence().first())
            .setStyle(NotificationCompat.BigTextStyle().bigText(alert.message))
            .setSubText(alert.kind.label)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .setPriority(
                if (alert.kind.severity == Severity.CRITICAL) NotificationCompat.PRIORITY_HIGH
                else NotificationCompat.PRIORITY_DEFAULT
            )
            .build()
        try {
            NotificationManagerCompat.from(ctx).notify(ids.incrementAndGet(), n)
        } catch (_: SecurityException) {
        }
    }
}
