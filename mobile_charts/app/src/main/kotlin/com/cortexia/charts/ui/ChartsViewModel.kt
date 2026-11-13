package com.cortexia.charts.ui

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.cortexia.charts.data.AuthException
import com.cortexia.charts.data.ChartsPrefs
import com.cortexia.charts.data.ChartsRepository
import com.cortexia.charts.data.EnvTarget
import com.cortexia.charts.data.EnvironmentIsolationException
import com.cortexia.charts.data.LoginOutcome
import com.cortexia.charts.data.MobileBundle
import com.cortexia.charts.data.WalletInfo
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.net.ConnectException
import java.net.SocketTimeoutException
import java.net.UnknownHostException

data class ChartsUiState(
    val env: EnvTarget = EnvTarget.PRODUCTION,
    val loggedIn: Boolean = false,
    val username: String = "",
    val remember: Boolean = true,
    val totpRequired: Boolean = false,
    val totpPromptMessage: String? = null,
    val loginInProgress: Boolean = false,
    val loginError: String? = null,
    val loading: Boolean = false,
    val offline: Boolean = false,
    val error: String? = null,
    val bundle: MobileBundle? = null,
    val selectedWalletId: Int? = null,
    val chartDays: Int = 90,
    val refreshIntervalSec: Int = 15,
    val lastUpdatedMillis: Long? = null,
) {
    val wallets: List<WalletInfo>
        get() = bundle?.wallets.orEmpty()

    val activeWallet: WalletInfo?
        get() = bundle?.active_wallet
            ?: wallets.firstOrNull { it.id == selectedWalletId }
            ?: wallets.firstOrNull { it.is_selected }
            ?: wallets.firstOrNull()
}

class ChartsViewModel(app: Application) : AndroidViewModel(app) {
    private val prefs = ChartsPrefs(app)
    private val repo = ChartsRepository(prefs)
    private var fetchJob: Job? = null
    private var lastHeartbeatMillis = 0L

    private val _state = MutableStateFlow(buildStateForEnv(prefs.activeEnv))
    val state: StateFlow<ChartsUiState> = _state

    private fun buildStateForEnv(target: EnvTarget): ChartsUiState = ChartsUiState(
        env = target,
        loggedIn = prefs.isLoggedIn(target) || prefs.canAutoRelogin(target),
        username = prefs.username(target),
        remember = prefs.rememberCredentials(target),
        totpRequired = false,
        totpPromptMessage = null,
        loginInProgress = false,
        loginError = null,
        loading = false,
        offline = false,
        error = null,
        bundle = null,
        selectedWalletId = prefs.selectedWalletId(target),
        chartDays = prefs.chartDays,
        refreshIntervalSec = prefs.refreshIntervalSec,
        lastUpdatedMillis = null,
    )

    suspend fun foregroundLoop() {
        while (true) {
            val current = _state.value
            if (current.loggedIn) {
                refreshInternal(current.env)
                val now = System.currentTimeMillis()
                if (now - lastHeartbeatMillis > 240_000L && !_state.value.offline) {
                    lastHeartbeatMillis = now
                    repo.api.heartbeat(current.env)
                }
            }
            val waitSec = _state.value.refreshIntervalSec.coerceIn(10, 60)
            delay(waitSec * 1000L)
        }
    }

    fun refreshNow() {
        val target = _state.value.env
        fetchJob?.cancel()
        fetchJob = viewModelScope.launch {
            refreshInternal(target)
        }
    }

    private suspend fun refreshInternal(targetEnv: EnvTarget) {
        if (!_state.value.loggedIn || _state.value.env != targetEnv) return
        _state.update { it.copy(loading = true, error = null) }
        try {
            val walletId = _state.value.selectedWalletId ?: prefs.selectedWalletId(targetEnv)
            val days = _state.value.chartDays
            val fetched = repo.fetchBundle(env = targetEnv, walletId = walletId, days = days)
            if (_state.value.env != targetEnv) return
            if (!targetEnv.matchesServerEnvironment(fetched.environment)) {
                throw EnvironmentIsolationException(
                    "Isolamento violado: esperado '${targetEnv.wireName}', recebido '${fetched.environment}'."
                )
            }
            val resolvedWalletId = fetched.active_wallet?.id ?: walletId
            if (resolvedWalletId != null && resolvedWalletId > 0) {
                prefs.setSelectedWalletId(targetEnv, resolvedWalletId)
            }
            _state.update { current ->
                if (current.env != targetEnv) current
                else current.copy(
                    loading = false,
                    offline = false,
                    error = null,
                    bundle = fetched,
                    selectedWalletId = resolvedWalletId,
                    lastUpdatedMillis = System.currentTimeMillis(),
                )
            }
        } catch (ce: CancellationException) {
            throw ce
        } catch (ie: EnvironmentIsolationException) {
            _state.update { current ->
                if (current.env != targetEnv) current
                else current.copy(
                    loading = false,
                    bundle = null,
                    error = ie.message,
                )
            }
        } catch (ae: AuthException) {
            _state.update { current ->
                if (current.env != targetEnv) current
                else current.copy(
                    loading = false,
                    loggedIn = false,
                    bundle = null,
                    loginError = ae.message,
                )
            }
        } catch (e: Exception) {
            val friendly = friendlyNetworkMessage(e, targetEnv)
            _state.update { current ->
                if (current.env != targetEnv) current
                else current.copy(
                    loading = false,
                    offline = true,
                    error = friendly,
                )
            }
        }
    }

    fun switchEnvironment(target: EnvTarget) {
        if (_state.value.env == target && _state.value.bundle == null && !_state.value.loggedIn) {
            return
        }
        fetchJob?.cancel()
        prefs.activeEnv = target
        val cleanState = buildStateForEnv(target)
        _state.value = cleanState
        if (cleanState.loggedIn) {
            fetchJob = viewModelScope.launch {
                refreshInternal(target)
            }
        }
    }

    fun selectWallet(walletId: Int) {
        val target = _state.value.env
        prefs.setSelectedWalletId(target, walletId)
        _state.update { it.copy(selectedWalletId = walletId) }
        refreshNow()
    }

    fun setChartDays(days: Int) {
        val clamped = days.coerceIn(7, 730)
        prefs.chartDays = clamped
        _state.update { it.copy(chartDays = clamped) }
        refreshNow()
    }

    fun setRefreshIntervalSec(seconds: Int) {
        val clamped = seconds.coerceIn(10, 60)
        prefs.refreshIntervalSec = clamped
        _state.update { it.copy(refreshIntervalSec = clamped) }
    }

    fun login(
        username: String,
        password: String,
        remember: Boolean,
        secondFactorInput: String = "",
        forceRecoveryMode: Boolean = false,
    ) {
        val targetEnv = _state.value.env
        val trimmedUser = username.trim()
        val cleanCode = secondFactorInput.trim()
        val isSixDigits = cleanCode.length == 6 && cleanCode.all { it.isDigit() }
        val totpCode = if (cleanCode.isNotEmpty() && !forceRecoveryMode && isSixDigits) cleanCode else null
        val recoveryCode = if (cleanCode.isNotEmpty() && (forceRecoveryMode || !isSixDigits)) cleanCode else null

        fetchJob?.cancel()
        fetchJob = viewModelScope.launch {
            _state.update {
                it.copy(
                    loginInProgress = true,
                    loginError = null,
                    bundle = null,
                )
            }
            try {
                when (
                    val outcome = repo.api.login(
                        env = targetEnv,
                        username = trimmedUser,
                        password = password,
                        totpCode = totpCode,
                        recoveryCode = recoveryCode,
                    )
                ) {
                    is LoginOutcome.TotpRequired -> {
                        _state.update {
                            it.copy(
                                loginInProgress = false,
                                totpRequired = true,
                                totpPromptMessage = outcome.message,
                                loginError = null,
                            )
                        }
                    }
                    is LoginOutcome.Authenticated -> {
                        prefs.setRememberCredentials(targetEnv, remember)
                        val storePass = remember && !(outcome.user?.totp_enabled ?: false) && cleanCode.isEmpty()
                        prefs.setPassword(targetEnv, if (storePass) password else null)
                        lastHeartbeatMillis = System.currentTimeMillis()
                        _state.update {
                            it.copy(
                                loggedIn = true,
                                loginInProgress = false,
                                username = trimmedUser,
                                remember = remember,
                                totpRequired = false,
                                totpPromptMessage = null,
                                loginError = null,
                                error = null,
                                bundle = null,
                            )
                        }
                        refreshInternal(targetEnv)
                    }
                }
            } catch (ce: CancellationException) {
                throw ce
            } catch (e: Exception) {
                val msg = friendlyNetworkMessage(e, targetEnv)
                _state.update {
                    it.copy(
                        loginInProgress = false,
                        loginError = msg,
                    )
                }
            }
        }
    }

    fun logout() {
        val targetEnv = _state.value.env
        fetchJob?.cancel()
        viewModelScope.launch {
            repo.api.logout(targetEnv)
            _state.value = buildStateForEnv(targetEnv).copy(loggedIn = false, bundle = null)
        }
    }

    private fun friendlyNetworkMessage(e: Exception, env: EnvTarget): String = when (e) {
        is UnknownHostException, is ConnectException, is SocketTimeoutException ->
            "Sem conexão com ${env.shortLabel}. Verifique sua rede ou disponibilidade do servidor."
        else -> e.message ?: "Falha inesperada na comunicação."
    }
}
