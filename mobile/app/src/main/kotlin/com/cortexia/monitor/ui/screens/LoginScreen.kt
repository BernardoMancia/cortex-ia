package com.cortexia.monitor.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.cortexia.monitor.BuildConfig
import com.cortexia.monitor.ui.UiState
import com.cortexia.monitor.ui.theme.CortexColors

@Composable
fun LoginScreen(state: UiState, onLogin: (String, String, Boolean) -> Unit) {
    var user by rememberSaveable { mutableStateOf(state.username) }
    var pass by rememberSaveable { mutableStateOf("") }
    var remember by rememberSaveable { mutableStateOf(state.remember) }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .imePadding()
            .padding(24.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Spacer(Modifier.height(48.dp))
        Icon(Icons.Filled.Lock, null, tint = CortexColors.Primary, modifier = Modifier.size(48.dp))
        Spacer(Modifier.height(12.dp))
        Text("Córtex Monitor", fontSize = 26.sp, fontWeight = FontWeight.Bold)
        Text("Monitoramento do Córtex IA", color = CortexColors.TextMuted, fontSize = 14.sp)
        Spacer(Modifier.height(28.dp))

        OutlinedTextField(
            value = user, onValueChange = { user = it }, label = { Text("Usuário") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(10.dp))
        OutlinedTextField(
            value = pass, onValueChange = { pass = it }, label = { Text("Senha") },
            singleLine = true, modifier = Modifier.fillMaxWidth(),
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
        )
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
            Checkbox(checked = remember, onCheckedChange = { remember = it })
            Column {
                Text("Manter conectado", fontSize = 14.sp)
                Text(
                    "Guarda a senha cifrada (Android Keystore) para reconectar e manter os alertas em segundo plano.",
                    fontSize = 11.sp, color = CortexColors.TextMuted,
                )
            }
        }
        if (state.loginError != null) {
            Spacer(Modifier.height(8.dp))
            Text(state.loginError, color = CortexColors.Negative, fontSize = 13.sp)
        }
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = { onLogin(user, pass, remember) },
            enabled = !state.loginInProgress && user.isNotBlank() && pass.isNotBlank(),
            modifier = Modifier.fillMaxWidth().height(48.dp),
        ) {
            if (state.loginInProgress) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            else Text("Entrar")
        }
        Spacer(Modifier.height(20.dp))
        Text(
            "Acesso somente leitura. Primeiro acesso (Admin/Admin) deve ser concluído no Dashboard Web.",
            fontSize = 11.sp, color = CortexColors.TextMuted,
        )
        Text("v${BuildConfig.VERSION_NAME}", fontSize = 11.sp, color = CortexColors.TextMuted)
    }
}
