package com.cortexia.monitor.alerts

import android.content.Context
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.cortexia.monitor.data.AppPrefs
import com.cortexia.monitor.data.AuthException
import com.cortexia.monitor.data.Repository
import kotlinx.coroutines.delay
import java.util.concurrent.TimeUnit

class MonitorWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    override suspend fun doWork(): Result {
        val prefs = AppPrefs(applicationContext)
        if (!prefs.isLoggedIn && !prefs.canAutoRelogin) return Result.success()
        val repo = Repository(prefs)

        suspend fun fetch() = repo.fetchSnapshot(chartDays = 10, tradesLimit = 50, decisionsLimit = 1, logsLimit = 200)

        val snapshot = try {
            fetch()
        } catch (e: AuthException) {
            AlertProcessor.processAuthExpired(applicationContext, prefs)
            return Result.success()
        } catch (first: Exception) {
            delay(20_000)
            try {
                fetch()
            } catch (e: AuthException) {
                AlertProcessor.processAuthExpired(applicationContext, prefs)
                return Result.success()
            } catch (e: Exception) {
                AlertProcessor.processFailure(applicationContext, prefs, e, threshold = 1)
                return Result.success()
            }
        }
        AlertProcessor.processSuccess(applicationContext, prefs, snapshot)
        return Result.success()
    }

    companion object {
        private const val WORK_NAME = "cortex_monitor_periodic"

        fun schedule(ctx: Context) {
            val prefs = AppPrefs(ctx)
            val wm = WorkManager.getInstance(ctx)
            if (!prefs.backgroundEnabled) {
                wm.cancelUniqueWork(WORK_NAME)
                return
            }
            val request = PeriodicWorkRequestBuilder<MonitorWorker>(
                prefs.backgroundIntervalMin.toLong(), TimeUnit.MINUTES
            ).setConstraints(
                Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
            ).build()
            wm.enqueueUniquePeriodicWork(WORK_NAME, ExistingPeriodicWorkPolicy.UPDATE, request)
        }

        fun cancel(ctx: Context) = WorkManager.getInstance(ctx).cancelUniqueWork(WORK_NAME)
    }
}
