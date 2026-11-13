package com.cortexia.monitor

import android.app.Application
import com.cortexia.monitor.alerts.MonitorWorker
import com.cortexia.monitor.alerts.Notifier
import com.cortexia.monitor.data.AppPrefs

class CortexApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        Notifier.createChannels(this)
        val prefs = AppPrefs(this)
        if (prefs.isLoggedIn || prefs.canAutoRelogin) MonitorWorker.schedule(this)
    }
}
