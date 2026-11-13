package com.cortexia.monitor

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.cortexia.monitor.alerts.AlertProcessor
import com.cortexia.monitor.ui.MainViewModel
import com.cortexia.monitor.ui.UiState
import com.cortexia.monitor.ui.components.Badge
import com.cortexia.monitor.ui.components.StatusDot
import com.cortexia.monitor.ui.screens.AlertsScreen
import com.cortexia.monitor.ui.screens.ChartsScreen
import com.cortexia.monitor.ui.screens.DashboardScreen
import com.cortexia.monitor.ui.screens.LoginScreen
import com.cortexia.monitor.ui.screens.OperationsScreen
import com.cortexia.monitor.ui.screens.SettingsScreen
import com.cortexia.monitor.ui.theme.CortexColors
import com.cortexia.monitor.ui.theme.CortexTheme
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

enum class Tab(val label: String) { HOME("Painel"), CHARTS("Gráficos"), OPS("Operações"), ALERTS("Alertas"), SETTINGS("Config") }

class MainActivity : ComponentActivity() {
    private val vm: MainViewModel by viewModels()
    private val openTab = MutableStateFlow<Tab?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        handleIntent(intent)
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) { vm.foregroundLoop() }
        }
        setContent {
            CortexTheme {
                val state by vm.state.collectAsStateWithLifecycle()
                val requested by openTab.collectAsStateWithLifecycle()
                CortexApp(state, vm, requested) { openTab.value = null }
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleIntent(intent)
    }

    private fun handleIntent(intent: Intent?) {
        if (intent?.getStringExtra("open_tab") == "alerts") openTab.value = Tab.ALERTS
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CortexApp(state: UiState, vm: MainViewModel, requestedTab: Tab?, onTabConsumed: () -> Unit) {
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }
    val requestNotifications = {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            permissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }

    if (!state.loggedIn) {
        Scaffold(containerColor = CortexColors.Background) { p ->
            Column(Modifier.then(Modifier)) {
                Spacer(Modifier.size(p.calculateTopPadding()))
                LoginScreen(state) { user, pass, remember -> vm.login(user, pass, remember) }
            }
        }
        return
    }

    LaunchedEffect(Unit) { requestNotifications() }

    var tab by rememberSaveable { mutableStateOf(Tab.HOME) }
    LaunchedEffect(requestedTab) {
        if (requestedTab != null) {
            tab = requestedTab
            onTabConsumed()
        }
    }

    Scaffold(
        containerColor = CortexColors.Background,
        topBar = {
            TopAppBar(
                colors = TopAppBarDefaults.topAppBarColors(containerColor = CortexColors.Background),
                title = {
                    Column {
                        Text("Córtex Monitor", fontWeight = FontWeight.Bold, fontSize = 18.sp)
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            StatusDot(if (state.offline) CortexColors.Negative else CortexColors.Positive)
                            Spacer(Modifier.width(6.dp))
                            val snap = state.snapshot
                            Text(
                                when {
                                    state.offline -> "Offline"
                                    snap == null -> "Conectando…"
                                    else -> "B3 ${snap.status.market_status.lowercase()}"
                                },
                                fontSize = 11.sp, color = CortexColors.TextMuted,
                            )
                            if (snap != null) {
                                Spacer(Modifier.width(8.dp))
                                if (snap.isProduction) Badge("PROD", CortexColors.Negative) else Badge("HOMOLOG", CortexColors.Warning)
                            }
                        }
                    }
                },
                actions = {
                    if (state.loading) {
                        CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(14.dp))
                    } else {
                        IconButton(onClick = { vm.refreshNow() }) { Icon(Icons.Filled.Refresh, "Atualizar") }
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar(containerColor = CortexColors.Surface) {
                Tab.entries.forEach { t ->
                    val unread = t == Tab.ALERTS && state.history.any { it.timestamp > System.currentTimeMillis() - 3_600_000 }
                    NavigationBarItem(
                        selected = tab == t,
                        onClick = { tab = t },
                        icon = { TabIcon(t, unread) },
                        label = { Text(t.label, fontSize = 11.sp) },
                        colors = NavigationBarItemDefaults.colors(indicatorColor = CortexColors.SurfaceHigh),
                    )
                }
            }
        },
    ) { padding ->
        val today = AlertProcessor.todayB3()
        when (tab) {
            Tab.HOME -> DashboardScreen(state, padding, today)
            Tab.CHARTS -> ChartsScreen(state, padding) { vm.setChartDays(it) }
            Tab.OPS -> OperationsScreen(state, padding)
            Tab.ALERTS -> AlertsScreen(state, padding, onClear = { vm.clearHistory() }, onTest = { vm.testNotification() })
            Tab.SETTINGS -> SettingsScreen(
                state, padding,
                onConfig = { vm.updateConfig(it) },
                onFgRefresh = { vm.setForegroundRefresh(it) },
                onBackground = { enabled, minutes -> vm.setBackground(enabled, minutes) },
                onRequestNotifications = requestNotifications,
                onLogout = { vm.logout() },
            )
        }
    }
}

@Composable
private fun TabIcon(t: Tab, highlight: Boolean) {
    val tint = if (highlight) CortexColors.Warning else androidx.compose.material3.LocalContentColor.current
    when (t) {
        Tab.CHARTS -> Icon(painterResource(R.drawable.ic_chart), t.label)
        else -> {
            val v: ImageVector = when (t) {
                Tab.HOME -> Icons.Filled.Home
                Tab.OPS -> Icons.AutoMirrored.Filled.List
                Tab.ALERTS -> Icons.Filled.Notifications
                else -> Icons.Filled.Settings
            }
            Icon(v, t.label, tint = tint)
        }
    }
}
