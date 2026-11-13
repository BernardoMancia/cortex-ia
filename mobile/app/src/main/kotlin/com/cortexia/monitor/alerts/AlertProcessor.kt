package com.cortexia.monitor.alerts

import android.content.Context
import com.cortexia.monitor.data.AppPrefs
import com.cortexia.monitor.data.Snapshot
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.LocalDate
import java.time.ZoneId

object AlertProcessor {
    private val mutex = Mutex()
    private val B3_ZONE: ZoneId = ZoneId.of("America/Sao_Paulo")
    private const val MAX_NOTIFICATIONS_PER_RUN = 6

    private val _historyVersion = MutableStateFlow(0L)
    val historyVersion: StateFlow<Long> = _historyVersion

    fun todayB3(): String = LocalDate.now(B3_ZONE).toString()

    suspend fun processSuccess(ctx: Context, prefs: AppPrefs, snap: Snapshot) = mutex.withLock {
        val input = AlertInput(
            marketStatus = snap.status.market_status,
            environment = snap.status.environment,
            equity = snap.status.equity,
            positions = snap.status.positions,
            trades = snap.trades,
            dailyReports = snap.equityCurve,
            logs = snap.logs,
        )
        val (next, alerts) = AlertRules.onSuccess(prefs.alertState, prefs.alertConfig, input, todayB3())
        prefs.alertState = next
        dispatch(ctx, prefs, alerts)
    }

    suspend fun processFailure(ctx: Context, prefs: AppPrefs, error: Throwable, threshold: Int) = mutex.withLock {
        val (next, alerts) = AlertRules.onFailure(
            prefs.alertState, prefs.alertConfig, error.message ?: error.javaClass.simpleName, threshold
        )
        prefs.alertState = next
        dispatch(ctx, prefs, alerts)
    }

    suspend fun processAuthExpired(ctx: Context, prefs: AppPrefs) = mutex.withLock {
        val (next, alerts) = AlertRules.onAuthExpired(prefs.alertState)
        prefs.alertState = next
        dispatch(ctx, prefs, alerts)
    }

    fun dispatch(ctx: Context, prefs: AppPrefs, alerts: List<Alert>) {
        if (alerts.isEmpty()) return
        val now = System.currentTimeMillis()
        prefs.addAlertHistory(alerts.map {
            AlertRecord(now, it.kind.name, it.kind.severity.name, it.title, it.message)
        })
        if (alerts.size <= MAX_NOTIFICATIONS_PER_RUN) {
            alerts.forEach { Notifier.show(ctx, it) }
        } else {
            alerts.take(MAX_NOTIFICATIONS_PER_RUN - 1).forEach { Notifier.show(ctx, it) }
            Notifier.show(
                ctx,
                Alert(AlertKind.ENGINE, "+${alerts.size - MAX_NOTIFICATIONS_PER_RUN + 1} alertas", "Abra o app para ver o histórico completo."),
            )
        }
        _historyVersion.value = now
    }
}
