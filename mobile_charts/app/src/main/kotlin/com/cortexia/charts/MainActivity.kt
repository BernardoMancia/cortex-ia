package com.cortexia.charts

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.cortexia.charts.data.EnvTarget
import com.cortexia.charts.ui.ChartsUiState
import com.cortexia.charts.ui.ChartsViewModel
import com.cortexia.charts.ui.Fmt
import com.cortexia.charts.ui.components.EnvBadgePill
import com.cortexia.charts.ui.components.FullscreenChartDialog
import com.cortexia.charts.ui.components.FullscreenChartSpec
import com.cortexia.charts.ui.screens.AlocacaoTab
import com.cortexia.charts.ui.screens.ExecucoesTab
import com.cortexia.charts.ui.screens.IndicesTab
import com.cortexia.charts.ui.screens.LoginScreen
import com.cortexia.charts.ui.screens.PatrimonioTab
import com.cortexia.charts.ui.screens.RadarIaTab
import com.cortexia.charts.ui.theme.ChartsColors
import com.cortexia.charts.ui.theme.CortexChartsTheme
import kotlinx.coroutines.launch

enum class ChartsTab(val label: String, val iconRes: Int) {
    PATRIMONIO("Patrimônio", R.drawable.ic_tab_equity),
    ALOCACAO("Alocação", R.drawable.ic_tab_allocation),
    EXECUCOES("Execuções", R.drawable.ic_tab_executions),
    RADAR_IA("Radar & IA", R.drawable.ic_tab_radar),
    INDICES("Índices", R.drawable.ic_tab_ratios),
}

class MainActivity : ComponentActivity() {
    private val vm: ChartsViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                vm.foregroundLoop()
            }
        }
        setContent {
            CortexChartsTheme {
                val state by vm.state.collectAsStateWithLifecycle()
                CortexChartsRoot(state = state, vm = vm)
            }
        }
    }
}

@Composable
fun CortexChartsRoot(
    state: ChartsUiState,
    vm: ChartsViewModel,
) {
    if (!state.loggedIn) {
        LoginScreen(
            state = state,
            onSwitchEnv = { vm.switchEnvironment(it) },
            onLogin = { u, p, r, code, rec -> vm.login(u, p, r, code, rec) },
        )
        return
    }

    var currentTab by rememberSaveable { mutableStateOf(ChartsTab.PATRIMONIO) }
    var expandedChart by remember { mutableStateOf<FullscreenChartSpec?>(null) }

    expandedChart?.let { spec ->
        FullscreenChartDialog(
            env = state.env,
            spec = spec,
            onDismiss = { expandedChart = null },
        )
    }

    Scaffold(
        containerColor = ChartsColors.Background,
        topBar = {
            ChartsTopBar(
                state = state,
                onSwitchEnv = {
                    expandedChart = null
                    vm.switchEnvironment(it)
                },
                onSelectWallet = { vm.selectWallet(it) },
                onRefresh = { vm.refreshNow() },
            )
        },
        bottomBar = {
            NavigationBar(
                containerColor = ChartsColors.Surface,
                tonalElevation = 0.dp,
            ) {
                ChartsTab.entries.forEach { tab ->
                    val selected = currentTab == tab
                    NavigationBarItem(
                        selected = selected,
                        onClick = { currentTab = tab },
                        icon = {
                            Icon(
                                painter = painterResource(tab.iconRes),
                                contentDescription = tab.label,
                                modifier = Modifier.size(20.dp),
                            )
                        },
                        label = {
                            Text(
                                text = tab.label,
                                fontSize = 10.sp,
                                fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                                maxLines = 1,
                            )
                        },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = ChartsColors.Cyan,
                            selectedTextColor = ChartsColors.Cyan,
                            unselectedIconColor = ChartsColors.TextSecondary,
                            unselectedTextColor = ChartsColors.TextSecondary,
                            indicatorColor = ChartsColors.SurfaceElevated,
                        ),
                    )
                }
            }
        },
    ) { innerPadding ->
        when (currentTab) {
            ChartsTab.PATRIMONIO -> PatrimonioTab(
                state = state,
                padding = innerPadding,
                onSelectDays = { vm.setChartDays(it) },
                onExpandChart = { expandedChart = it },
            )
            ChartsTab.ALOCACAO -> AlocacaoTab(
                state = state,
                padding = innerPadding,
                onExpandChart = { expandedChart = it },
            )
            ChartsTab.EXECUCOES -> ExecucoesTab(
                state = state,
                padding = innerPadding,
                onExpandChart = { expandedChart = it },
            )
            ChartsTab.RADAR_IA -> RadarIaTab(
                state = state,
                padding = innerPadding,
                onExpandChart = { expandedChart = it },
            )
            ChartsTab.INDICES -> IndicesTab(
                state = state,
                padding = innerPadding,
                onSelectRefreshSec = { vm.setRefreshIntervalSec(it) },
                onSwitchEnv = {
                    expandedChart = null
                    vm.switchEnvironment(it)
                },
                onSelectWallet = { vm.selectWallet(it) },
                onLogout = {
                    expandedChart = null
                    vm.logout()
                },
                onExpandChart = { expandedChart = it },
            )
        }
    }
}

@Composable
private fun ChartsTopBar(
    state: ChartsUiState,
    onSwitchEnv: (EnvTarget) -> Unit,
    onSelectWallet: (Int) -> Unit,
    onRefresh: () -> Unit,
) {
    var envMenuOpen by remember { mutableStateOf(false) }
    var walletMenuOpen by remember { mutableStateOf(false) }
    val bundle = state.bundle

    Column(
        modifier = Modifier
            .fillMaxWidth()
            .background(ChartsColors.Surface)
            .statusBarsPadding(),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 14.dp, end = 8.dp, top = 8.dp, bottom = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        text = "Córtex Charts",
                        fontSize = 18.sp,
                        fontWeight = FontWeight.Bold,
                        color = ChartsColors.TextPrimary,
                    )
                    Spacer(Modifier.width(8.dp))
                    Box(
                        Modifier
                            .size(8.dp)
                            .clip(CircleShape)
                            .background(if (state.offline) ChartsColors.Crimson else ChartsColors.Emerald)
                    )
                    Spacer(Modifier.width(5.dp))
                    Text(
                        text = when {
                            state.offline -> "Offline"
                            bundle == null -> "Sincronizando…"
                            else -> "B3 ${bundle.market_status.lowercase()} • IBOV ${Fmt.pct(bundle.bova11_change_pct)}"
                        },
                        fontSize = 11.sp,
                        color = ChartsColors.TextSecondary,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }

                Row(
                    modifier = Modifier.padding(top = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Box {
                        EnvBadgePill(
                            env = state.env,
                            onClick = { envMenuOpen = true },
                        )
                        DropdownMenu(
                            expanded = envMenuOpen,
                            onDismissRequest = { envMenuOpen = false },
                        ) {
                            EnvTarget.entries.forEach { target ->
                                DropdownMenuItem(
                                    text = {
                                        Text(
                                            text = target.badgeLabel,
                                            color = ChartsColors.envAccent(target),
                                            fontWeight = FontWeight.Bold,
                                            fontSize = 12.sp,
                                        )
                                    },
                                    onClick = {
                                        envMenuOpen = false
                                        onSwitchEnv(target)
                                    },
                                )
                            }
                        }
                    }

                    val activeWallet = state.activeWallet
                    if (activeWallet != null) {
                        Box {
                            Row(
                                modifier = Modifier
                                    .clip(RoundedCornerShape(50))
                                    .background(ChartsColors.SurfaceElevated)
                                    .border(1.dp, ChartsColors.Outline, RoundedCornerShape(50))
                                    .clickable { walletMenuOpen = true }
                                    .padding(horizontal = 10.dp, vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Text(
                                    text = activeWallet.name,
                                    fontSize = 11.sp,
                                    fontWeight = FontWeight.SemiBold,
                                    color = ChartsColors.Cyan,
                                    maxLines = 1,
                                    overflow = TextOverflow.Ellipsis,
                                )
                                Spacer(Modifier.width(4.dp))
                                Text("▾", fontSize = 11.sp, color = ChartsColors.Cyan)
                            }
                            DropdownMenu(
                                expanded = walletMenuOpen,
                                onDismissRequest = { walletMenuOpen = false },
                            ) {
                                state.wallets.forEach { w ->
                                    DropdownMenuItem(
                                        text = {
                                            Text(
                                                text = "${w.name} (${w.operational_mode})",
                                                fontSize = 12.sp,
                                                fontWeight = if (w.id == activeWallet.id) FontWeight.Bold else FontWeight.Normal,
                                            )
                                        },
                                        onClick = {
                                            walletMenuOpen = false
                                            onSelectWallet(w.id)
                                        },
                                    )
                                }
                            }
                        }
                    }
                }
            }

            if (state.loading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .padding(horizontal = 12.dp)
                        .size(20.dp),
                    strokeWidth = 2.dp,
                    color = ChartsColors.Cyan,
                )
            } else {
                IconButton(onClick = onRefresh) {
                    Icon(
                        imageVector = Icons.Filled.Refresh,
                        contentDescription = "Atualizar gráficos",
                        tint = ChartsColors.TextPrimary,
                    )
                }
            }
        }

        if (!state.error.isNullOrBlank()) {
            Text(
                text = state.error,
                fontSize = 11.sp,
                color = ChartsColors.Crimson,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(ChartsColors.Crimson.copy(alpha = 0.12f))
                    .padding(horizontal = 14.dp, vertical = 4.dp),
            )
        }
        HorizontalDivider(color = ChartsColors.Outline, thickness = 1.dp)
    }
}
