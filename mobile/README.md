# Córtex Monitor — App Android (v1.0.2)

App Android **somente leitura** para monitorar o Córtex IA: painel em tempo real, gráficos,
estatísticas de desempenho e **alertas push** no celular. Não altera nada no servidor e
conecta diretamente à API do Dashboard (`http://<SEU_SERVIDOR>:8003`).

## Instalação

1. Copie `releases/CortexMonitor_v1.0.2.apk` para o celular (Android 8.0+).
2. Abra o arquivo e permita "instalar apps de fontes desconhecidas" quando o Android pedir.
3. No app, informe apenas o **usuário e a senha** do Dashboard Web (o servidor já vem configurado internamente).
   - Se o usuário ainda estiver no **Primeiro Acesso (Admin/Admin)**, conclua a troca de senha
     no Dashboard Web antes — o app é somente leitura e não faz essa troca.
4. Permita as notificações e, de preferência, libere o app da **otimização de bateria**
   (Config → Otimização de bateria) para os alertas em segundo plano não atrasarem.

## Telas

| Aba | Conteúdo |
|---|---|
| **Painel** | Status (online/offline, HOMOLOGAÇÃO/PRODUÇÃO, B3 aberta/fechada), patrimônio, caixa, resultado do dia, P&L realizado/não realizado, retorno acumulado, cofre e D+2 (produção), posições com distância do stop, últimas decisões da IA. |
| **Gráficos** | Curva de patrimônio, retorno diário, drawdown, alocação (rosca), compras x vendas por dia, P&L por ativo, distribuição das decisões. Toque/arraste nos gráficos para ver valores. Períodos: 7/30/90/180/365 dias. |
| **Estatísticas** | Retorno do período, drawdown máximo, Sharpe anualizado, volatilidade, % de dias positivos, melhor/pior dia, taxa de acerto, fator de lucro, ganho/perda médios (casamento FIFO das ordens, consolidando `PETR4` + `PETR4F`). |
| **Operações** | Ordens (filtro por ativo), decisões da IA com raciocínio, logs do motor (filtro WARNING/ERROR). |
| **Alertas** | Histórico local (últimos 300), resumo das últimas 24h, teste de notificação. |
| **Config** | Liga/desliga cada alerta, limites, intervalo de atualização, segundo plano, logout. |

## Alertas

| Alerta | Regra padrão (configurável) |
|---|---|
| Servidor/IA offline | 3 falhas seguidas com o app aberto, ou 2 tentativas falhas (com 20 s de intervalo) em segundo plano. Avisa também quando volta. Status `DESCONHECIDO` indica motor parado. |
| Nova compra/venda | Cada nova ordem em `/api/trades` (máx. 5 notificações por ciclo). |
| Perda diária | Patrimônio atual vs. fechamento do pregão anterior ≤ **-3%**; repete a cada novo múltiplo (-6%, -9%...). |
| Prejuízo em posição | `pnl_percent` ≤ **-5%** (repete a cada múltiplo). |
| Perto do stop | Preço a ≤ **1%** do stop-loss. |
| Variação de patrimônio | ±**2%** desde o último alerta. |
| Erros nos logs | Novas linhas `[ERROR]` / `[CRITICAL]` em `/api/logs`. |
| Mercado B3 | Abertura e fechamento do pregão. |
| Ambiente | Troca entre Homologação e Produção. |

- **App aberto:** consulta a cada 15 s (configurável) e gera alertas na hora.
- **Segundo plano:** WorkManager a cada 15/30/60 min (o mínimo do Android é 15 min), só com internet.
- Na primeira execução, ordens e logs antigos viram apenas referência (sem enxurrada de alertas).

## Segurança

- Login em `POST /api/auth/login`; o token da sessão (cookie `cortex_session`) é guardado
  **cifrado com AES-256-GCM no Android Keystore** e enviado como `Authorization: Bearer`.
- "Manter conectado" guarda a senha também cifrada para refazer o login quando a sessão expira
  (`AUTH_MAX_IDLE_SECONDS` / `AUTH_MAX_SESSION_SECONDS`). Sem isso, os alertas em segundo plano param
  quando a sessão expira (o app avisa).
- O heartbeat (`POST /api/auth/heartbeat`, com `X-CSRF-Token`) é enviado só com o app aberto, como no Dashboard Web.
- ⚠️ A API atual opera em **HTTP sem criptografia** (`:8003`).

## Build

```powershell
cd mobile
.\gradlew.bat :app:testDebugUnitTest   # 10 testes (regras de alerta, estatísticas, parsing)
.\gradlew.bat :app:assembleRelease     # app\build\outputs\apk\release\app-release.apk
```

- Versão em `app/build.gradle.kts` (`appVersionName` / `appVersionCode`) — incremente a cada build.
- Assinatura: `keystore/cortex-monitor-release.jks` + `keystore/keystore.properties` (**fora do Git e do deploy**).
  **Guarde uma cópia segura do keystore**: sem ele não é possível atualizar o app instalado mantendo a assinatura.
- Stack: Kotlin, Jetpack Compose (Material 3), OkHttp, kotlinx.serialization, WorkManager; gráficos desenhados em Canvas (sem bibliotecas externas).
