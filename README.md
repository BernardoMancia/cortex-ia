# Córtex IA v0.1.0 — Plataforma Quantitativa Institucional B3

Plataforma multiusuário e multicarteira de automação quantitativa, inteligência de mercado e proteção patrimonial para operações reais e simuladas na **B3 (Brasil, Bolsa, Balcão)**.

---

## 1. Arquitetura Modular

O sistema foi reconstruído do zero em camadas desacopladas, auditáveis e multi-tenant:

| Pacote | Responsabilidade |
|---|---|
| `config/` | Perfil de ambiente (`production` vs `homolog`), universo de ativos B3 e configurações tipadas |
| `domain/` | Regras oficiais de calendário/liquidação `D+2` B3, escudo tributário PF `R$ 20k/mês`, trava operacional e Firewall Financeiro |
| `security/` | Senhas `PBKDF2-HMAC-SHA256` (600k iterações + Pepper), sessões vinculadas por fingerprint, CSRF double-check, `TOTP 2FA` com QR SVG 100% offline, `Step-Up` (TTL 180s), rate-limit por IP/usuário, criptografia `AES-256-GCM + HKDF` por carteira e blindagem de permissões (`chmod 0700/0600`) |
| `storage/` | SQLite `WAL` transacional isolado por `user_id` e `wallet_id`, com livro-razão de auditoria encadeado via `HMAC-SHA256` |
| `brokers/` | Conectores de corretoras (`Clear Investimentos` via MT5 Bridge pronta para Produção, simulador realista B3 para Homologação e stubs seguros `WIP` para XP, Rico e BTG) |
| `wallets/` | Gestão isolada de portfólio, PM fiscal, teto operacional da IA, Cofre de Reserva blindado e liquidação inteligente `D+2` |
| `quant/` & `market/` | `MarketIntelligenceHub` compartilhado (1 coleta para `N` carteiras), indicadores multi-timeframe, regime `BOVA11`, notícias RSS sanitizadas e base educacional B3 |
| `engine/` | Orquestrador resiliente com timeout por carteira, reconciliação de custódia real, `PanicService` e comandos administrativos Telegram |
| `observability/` | Logs sanitizados contra vazamento de segredos, centralizados em `/LOGS-PROJETOS/cortex-ia/logs.db`, health-check de host e backups diários cifrados |
| `web/` | Servidor `FastAPI` endurecido + Cockpit Web escuro institucional (zero CDNs externos) + compatibilidade nativa com o app Android `Córtex Monitor` |

---

## 2. Ambientes: Produção (`8003`) vs. Homologação (`9003`)

| Característica | Produção (`Cortex-IA`) | Homologação (`Cortex-IA-Homolog`) |
|---|---|---|
| **Porta Padrão** | `8003` | `9003` |
| **Execução de Ordens** | Dinheiro real via conector homologado (`Clear MT5`) | Simulador realista B3 (slippage, spread e taxas B3) |
| **Comportamento Pós-Boot** | Entra obrigatoriamente em **`MAINTENANCE`** (ordens reais travadas até Pre-Flight + 2FA Step-Up de `Luke`) | Inicia em `ACTIVE` no ambiente simulado |
| **Logs Centralizados** | `/LOGS-PROJETOS/cortex-ia/logs.db` | `/LOGS-PROJETOS/cortex-ia-homolog/logs.db` |
| **Endpoints Simulados** | **Inexistentes no backend de Produção** | Isolados exclusivamente na instância de Homologação |

---

## 3. As 8 Camadas de Segurança Institucional

1. **Autenticação `PBKDF2-HMAC-SHA256` (600k + Pepper) & Anti-Enumeração:** Verificação em tempo constante mesmo para usuários inexistentes, bloqueio progressivo por falhas e troca obrigatória de senha no primeiro acesso.
2. **2FA `TOTP` 100% Offline:** Geração local de QR Code SVG sem chamadas a APIs externas, segredo cifrado em repouso e 8 códigos de recuperação de uso único.
3. **Confirmação `Step-Up` (TTL 180s):** Operações críticas (liberação pós-boot, atualização de credenciais de corretora, saque/liberação de cofre, criação/reset de usuários e liquidação de pânico) exigem token efêmero validado por senha + 2FA.
4. **Sessões Blindadas + Anti-CSRF:** Cookie `cortex_session` (`HttpOnly`, `SameSite=Strict`), suporte a `Authorization: Bearer` para o app Android, vínculo criptográfico de `User-Agent` e validação estrita de `X-CSRF-Token` em todas as mutações.
5. **Isolamento Multicarteira Anti-IDOR:** Toda leitura ou escrita de carteira resolve obrigatoriamente `(user_id, wallet_id)`. Tentativas cruzadas são bloqueadas com `HTTP 403` e gravadas como `IDOR_BLOCKED`.
6. **Cofre Criptográfico por Carteira (`AES-256-GCM + HKDF`):** Chaves derivadas individualmente por `(user_id, wallet_id)` a partir da chave-mestra local protegida em disco.
7. **Endurecimento HTTP & Limite de Payload:** Documentação automática desativada em produção, limite rígido de `64 KB` no corpo HTTP (inclusive stream chunked -> `HTTP 413`) e cabeçalhos `CSP`, `X-Frame-Options: DENY`, `nosniff`, `COOP` e `CORP`.
8. **Auditoria Imutável `HMAC-SHA256`:** Cada evento sensível é encadeado ao hash anterior (`prev_hash -> entry_hmac`), permitindo verificação instantânea de integridade contra adulteração no banco.

---

## 4. Painel Administrativo Exclusivo (`Luke`)

- O elemento **`🛡️ Painel de Admin`** permanece estritamente oculto no Cockpit para qualquer operador padrão e todos os endpoints `/api/admin/*`, `/api/logs` e `/ws/logs` validam no backend `id == 1`, `username == "Luke"` e `is_owner_admin == 1`.
- Qualquer tentativa de acesso por outro usuário autenticado retorna `HTTP 403` e registra `UNAUTHORIZED_ADMIN_ATTEMPT` na auditoria.
- Funcionalidades exclusivas de `Luke`:
  - Cadastro de novos operadores com senha temporária forte autoexpirável em **24 horas** (sem auto-registro público).
  - Bloqueio/desbloqueio instantâneo de contas, redefinição de senha temporária e reset de 2FA.
  - Verificador criptográfico da cadeia `HMAC-SHA256` e inspetor de eventos de segurança.
  - Terminal ao vivo via WebSocket (`/ws/logs`) com sanitização automática de segredos.

---

## 5. Modos de Manutenção Pós-Boot & Botão de Pânico

- **Manutenção Pós-Boot (`MAINTENANCE`):** Sempre que o servidor de Produção reinicia, o envio de ordens reais nasce bloqueado enquanto o radar de mercado segue operando normalmente. Somente `Luke`, após aprovação do **Pre-Flight Check** (saúde da bridge, autenticação na corretora, conciliação de custódia, relógio BRT e integridade da auditoria) + **Step-Up 2FA**, libera o motor para `ACTIVE`.
- **Botão de Pânico (`PANIC`):**
  - **Parada Instantânea (1 clique, sem fricção):** Suspende imediatamente novas ordens na carteira do usuário (ou globalmente no servidor, para `Luke`) por `15m`, `30m`, `1h`, `2h`, `4h`, até o próximo pregão, tempo personalizado ou indefinido.
  - **Liquidação de Emergência (Step-Up):** Opcionalmente zera a mercado as posições abertas, preservando ativos marcados manualmente com **`🔒 Proteger`** (`do_not_touch`).

---

## 6. Execução Local e Testes

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

Para executar a suíte de testes automatizados:

```bash
.venv\Scripts\pytest -q
```
