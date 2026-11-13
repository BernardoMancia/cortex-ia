package com.cortexia.charts.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.charts.BuildConfig
import com.cortexia.charts.data.EnvTarget
import com.cortexia.charts.ui.ChartsUiState
import com.cortexia.charts.ui.components.EnvBadgePill
import com.cortexia.charts.ui.theme.ChartsColors

@Composable
fun LoginScreen(
    state: ChartsUiState,
    onSwitchEnv: (EnvTarget) -> Unit,
    onLogin: (username: String, password: String, remember: Boolean, secondFactor: String, forceRecovery: Boolean) -> Unit,
) {
    var user by rememberSaveable(state.env) { mutableStateOf(state.username) }
    var pass by rememberSaveable(state.env) { mutableStateOf("") }
    var remember by rememberSaveable(state.env) { mutableStateOf(state.remember) }
    var secondFactor by rememberSaveable(state.env) { mutableStateOf("") }
    var manualShow2fa by rememberSaveable(state.env) { mutableStateOf(false) }
    var recoveryMode by rememberSaveable(state.env) { mutableStateOf(false) }

    LaunchedEffect(state.totpRequired) {
        if (state.totpRequired) manualShow2fa = true
    }

    val show2faSection = state.totpRequired || manualShow2fa
    val accent = ChartsColors.envAccent(state.env)

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(ChartsColors.Background)
            .systemBarsPadding()
            .verticalScroll(rememberScrollState())
            .imePadding()
            .padding(horizontal = 22.dp, vertical = 20.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Box(
            modifier = Modifier
                .size(64.dp)
                .clip(RoundedCornerShape(18.dp))
                .background(ChartsColors.SurfaceElevated)
                .border(1.dp, ChartsColors.Cyan.copy(alpha = 0.5f), RoundedCornerShape(18.dp)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                imageVector = Icons.Filled.Lock,
                contentDescription = null,
                tint = ChartsColors.Cyan,
                modifier = Modifier.size(32.dp),
            )
        }
        Spacer(Modifier.height(12.dp))
        Text(
            text = "Córtex Charts",
            fontSize = 26.sp,
            fontWeight = FontWeight.Bold,
            color = ChartsColors.TextPrimary,
        )
        Text(
            text = "Terminal Gráfico Quantitativo • B3 & IA",
            fontSize = 13.sp,
            color = ChartsColors.TextSecondary,
        )

        Spacer(Modifier.height(18.dp))
        EnvBadgePill(env = state.env)
        Spacer(Modifier.height(12.dp))

        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            EnvTarget.entries.forEach { target ->
                val isSelected = state.env == target
                val tColor = ChartsColors.envAccent(target)
                Box(
                    modifier = Modifier
                        .weight(1f)
                        .clip(RoundedCornerShape(12.dp))
                        .background(if (isSelected) tColor.copy(alpha = 0.18f) else ChartsColors.Surface)
                        .border(
                            width = if (isSelected) 1.5.dp else 1.dp,
                            color = if (isSelected) tColor else ChartsColors.Outline,
                            shape = RoundedCornerShape(12.dp),
                        )
                        .clickable { onSwitchEnv(target) }
                        .padding(vertical = 10.dp, horizontal = 8.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(
                        text = target.shortLabel,
                        fontSize = 12.sp,
                        fontWeight = if (isSelected) FontWeight.Bold else FontWeight.Medium,
                        color = if (isSelected) tColor else ChartsColors.TextSecondary,
                    )
                }
            }
        }

        Spacer(Modifier.height(18.dp))

        Card(
            modifier = Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(18.dp),
            colors = CardDefaults.cardColors(containerColor = ChartsColors.Surface),
            border = BorderStroke(1.dp, ChartsColors.Outline),
        ) {
            Column(Modifier.padding(16.dp)) {
                OutlinedTextField(
                    value = user,
                    onValueChange = { user = it },
                    label = { Text("Usuário") },
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.height(10.dp))
                OutlinedTextField(
                    value = pass,
                    onValueChange = { pass = it },
                    label = { Text("Senha") },
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
                    modifier = Modifier.fillMaxWidth(),
                )

                if (show2faSection) {
                    Spacer(Modifier.height(12.dp))
                    if (!state.totpPromptMessage.isNullOrBlank()) {
                        Text(
                            text = state.totpPromptMessage,
                            fontSize = 12.sp,
                            color = ChartsColors.Amber,
                            fontWeight = FontWeight.Medium,
                        )
                        Spacer(Modifier.height(6.dp))
                    }
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(
                            selected = !recoveryMode,
                            onClick = { recoveryMode = false },
                            label = { Text("App 2FA (6 dígitos)", fontSize = 11.sp) },
                        )
                        FilterChip(
                            selected = recoveryMode,
                            onClick = { recoveryMode = true },
                            label = { Text("Código Recuperação", fontSize = 11.sp) },
                        )
                    }
                    Spacer(Modifier.height(6.dp))
                    OutlinedTextField(
                        value = secondFactor,
                        onValueChange = { secondFactor = it },
                        label = {
                            Text(
                                if (recoveryMode) "Código de Recuperação"
                                else "Código 2FA (6 dígitos) ou Recuperação"
                            )
                        },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(
                            keyboardType = if (recoveryMode) KeyboardType.Ascii else KeyboardType.Text
                        ),
                        modifier = Modifier.fillMaxWidth(),
                    )
                } else {
                    TextButton(
                        onClick = { manualShow2fa = true },
                        modifier = Modifier.align(Alignment.End),
                    ) {
                        Text("Informar código 2FA / Recuperação", fontSize = 11.sp, color = ChartsColors.Cyan)
                    }
                }

                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Checkbox(checked = remember, onCheckedChange = { remember = it })
                    Spacer(Modifier.width(4.dp))
                    Column {
                        Text("Manter conectado neste ambiente", fontSize = 13.sp, color = ChartsColors.TextPrimary)
                        Text(
                            text = "Credenciais isoladas por ambiente e cifradas com AES-256-GCM no Android Keystore.",
                            fontSize = 10.sp,
                            color = ChartsColors.TextSecondary,
                        )
                    }
                }

                if (!state.loginError.isNullOrBlank()) {
                    Spacer(Modifier.height(8.dp))
                    Text(
                        text = state.loginError,
                        fontSize = 12.sp,
                        color = ChartsColors.Crimson,
                        fontWeight = FontWeight.Medium,
                    )
                }

                Spacer(Modifier.height(14.dp))
                val canSubmit = !state.loginInProgress &&
                    user.isNotBlank() &&
                    pass.isNotBlank() &&
                    (!state.totpRequired || secondFactor.isNotBlank())

                Button(
                    onClick = { onLogin(user, pass, remember, secondFactor, recoveryMode) },
                    enabled = canSubmit,
                    colors = ButtonDefaults.buttonColors(
                        containerColor = accent,
                        contentColor = ChartsColors.Background,
                    ),
                    modifier = Modifier
                        .fillMaxWidth()
                        .height(48.dp),
                    shape = RoundedCornerShape(12.dp),
                ) {
                    if (state.loginInProgress) {
                        CircularProgressIndicator(
                            modifier = Modifier.size(20.dp),
                            strokeWidth = 2.dp,
                            color = ChartsColors.Background,
                        )
                    } else {
                        Text(
                            text = if (state.totpRequired) "Validar 2FA e Entrar" else "Entrar em ${state.env.shortLabel}",
                            fontWeight = FontWeight.Bold,
                        )
                    }
                }
            }
        }

        Spacer(Modifier.height(16.dp))
        Text(
            text = "Isolamento estrito: sessões e carteiras de Produção e Homologação nunca se misturam.\nCórtex Charts v${BuildConfig.VERSION_NAME}",
            fontSize = 11.sp,
            color = ChartsColors.TextDim,
            textAlign = TextAlign.Center,
        )
    }
}
