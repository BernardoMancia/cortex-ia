package com.cortexia.monitor.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.cortexia.monitor.alerts.Alert
import com.cortexia.monitor.alerts.AlertConfig
import com.cortexia.monitor.alerts.AlertKind
import com.cortexia.monitor.alerts.AlertProcessor
import com.cortexia.monitor.alerts.AlertRecord
import com.cortexia.monitor.alerts.MonitorWorker
import com.cortexia.monitor.data.AppPrefs
import com.cortexia.monitor.data.AuthException
import com.cortexia.monitor.data.Repository
import com.cortexia.monitor.data.Snapshot
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

data class UiState(
    val loggedIn: Boolean,
    val username: String = "",
    val loading: Boolean = false,
    val snapshot: Snapshot? = null,
    val error: String? = null,
    val offline: Boolean = false,
    val lastUpdate: Long? = null,
    val loginInProgress: Boolean = false,
    val loginError: String? = null,
    val history: List<AlertRecord> = emptyList(),
    val config: AlertConfig = AlertConfig(),
    val chartDays: Int = 30,
    val fgRefreshSec: Int = 15,
    val bgEnabled: Boolean = true,
    val bgIntervalMin: Int = 15,
    val remember: Boolean = true,
)

class MainViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = AppPrefs(app)
    private val repo = Repository(prefs)
    private val refreshMutex = Mutex()
    private var lastHeartbeat = 0L

    private val _state = MutableStateFlow(initialState())
    val state: StateFlow<UiState> = _state

    init {
        viewModelScope.launch {
            AlertProcessor.historyVersion.collect { _state.update { it.copy(history = prefs.alertHistory()) } }
        }
    }

    private fun initialState() = UiState(
        loggedIn = prefs.isLoggedIn || prefs.canAutoRelogin,
        username = prefs.username,
        history = prefs.alertHistory(),
        config = prefs.alertConfig,
        chartDays = prefs.chartDays,
        fgRefreshSec = prefs.foregroundRefreshSec,
        bgEnabled = prefs.backgroundEnabled,
        bgIntervalMin = prefs.backgroundIntervalMin,
        remember = prefs.rememberCredentials,
    )

    suspend fun foregroundLoop() {
        while (true) {
            if (_state.value.loggedIn) {
                refresh()
                val now = System.currentTimeMillis()
                if (now - lastHeartbeat > 4 * 60_000L && !_state.value.offline) {
                    lastHeartbeat = now
                    repo.api.heartbeat()
                }
            }
            delay(prefs.foregroundRefreshSec * 1000L)
        }
    }

    fun refreshNow() {
        viewModelScope.launch { refresh() }
    }

    private suspend fun refresh() = refreshMutex.withLock {
        if (!_state.value.loggedIn) return@withLock
        _state.update { it.copy(loading = true) }
        try {
            val snap = repo.fetchSnapshot(chartDays = maxOf(prefs.chartDays, 10))
            AlertProcessor.processSuccess(getApplication(), prefs, snap)
            _state.update {
                it.copy(
                    loading = false, snapshot = snap, error = null, offline = false,
                    lastUpdate = snap.fetchedAt, history = prefs.alertHistory(),
                )
            }
        } catch (e: AuthException) {
            _state.update {
                it.copy(loading = false, loggedIn = false, loginError = e.message, snapshot = null)
            }
        } catch (e: Exception) {
            AlertProcessor.processFailure(getApplication(), prefs, e, threshold = 3)
            _state.update {
                it.copy(
                    loading = false, offline = true,
                    error = e.message ?: e.javaClass.simpleName, history = prefs.alertHistory(),
                )
            }
        }
    }

    fun login(username: String, password: String, remember: Boolean) {
        viewModelScope.launch {
            _state.update { it.copy(loginInProgress = true, loginError = null) }
            try {
                repo.api.login(username.trim(), password)
                prefs.rememberCredentials = remember
                prefs.password = if (remember) password else null
                MonitorWorker.schedule(getApplication())
                lastHeartbeat = System.currentTimeMillis()
                _state.update {
                    it.copy(
                        loggedIn = true, loginInProgress = false, username = prefs.username,
                        remember = remember, error = null,
                    )
                }
                refresh()
            } catch (e: Exception) {
                val msg = when (e) {
                    is java.net.UnknownHostException, is java.net.ConnectException, is java.net.SocketTimeoutException ->
                        "Não foi possível conectar ao servidor do Córtex IA. Verifique sua conexão com a internet."
                    else -> e.message ?: "Falha no login."
                }
                _state.update { it.copy(loginInProgress = false, loginError = msg) }
            }
        }
    }

    fun logout() {
        viewModelScope.launch {
            repo.api.logout()
            MonitorWorker.cancel(getApplication())
            _state.update { initialState().copy(loggedIn = false) }
        }
    }

    fun updateConfig(cfg: AlertConfig) {
        prefs.alertConfig = cfg
        _state.update { it.copy(config = cfg) }
    }

    fun setChartDays(days: Int) {
        prefs.chartDays = days
        _state.update { it.copy(chartDays = days) }
        refreshNow()
    }

    fun setForegroundRefresh(sec: Int) {
        prefs.foregroundRefreshSec = sec
        _state.update { it.copy(fgRefreshSec = prefs.foregroundRefreshSec) }
    }

    fun setBackground(enabled: Boolean, intervalMin: Int) {
        prefs.backgroundEnabled = enabled
        prefs.backgroundIntervalMin = intervalMin
        MonitorWorker.schedule(getApplication())
        _state.update { it.copy(bgEnabled = enabled, bgIntervalMin = prefs.backgroundIntervalMin) }
    }

    fun clearHistory() {
        prefs.clearAlertHistory()
        _state.update { it.copy(history = emptyList()) }
    }

    fun testNotification() {
        AlertProcessor.dispatch(
            getApplication(), prefs,
            listOf(Alert(AlertKind.RECOVERED, "Teste de notificação", "Se você está vendo isto, os alertas do Córtex Monitor estão funcionando.")),
        )
    }
}
