# Córtex IA — v2.9.1

**[🇧🇷 Português (PT-BR)](#-português-pt-br) | [🇺🇸 English](#-english)**

---

## 🇧🇷 Português (PT-BR)

**Sistema Autônomo de Inteligência Quantitativa, Algorithmic Trading, Sentimento com Gemini Interactions API e Dashboard Seguro para a B3**

O **Córtex IA (v2.9.1)** é um ecossistema autônomo de negociação quantitativa e gestão de portfólio para o mercado de ações brasileiro (**B3**). O motor opera continuamente combinando **Análise Técnica Multitemporal**, **Análise de Sentimento de Notícias em Tempo Real** (com suporte nativo à **Gemini 3.5 / 3.8 Interactions API** via `google-genai>=2.30.1` e fallback léxico local), **Gerenciamento de Risco Dinâmico** (Trailing Stop, Stop-Loss e limites de concentração), **Segregação Estrita Homologação vs. Produção** (com Cofre de Reserva e Liquidação B3 `D+2`) e um **Dashboard Web Mobile-First com Autenticação de Nível Bancário**.

Monitora continuamente o universo de **ações de alta liquidez da B3** (mais de 100 ativos configuráveis via `WATCHLIST_PORTFOLIO`, com roteamento inteligente entre lote padrão e mercado fracionário como `PETR4` e `PETR4F`), gerencia ordens e posições em tempo real e oferece operação híbrida via `SimulatorBroker`, `MT5Broker` (Windows) ou `MT5 REST Bridge` autenticada por chave (`BRIDGE_API_KEY`).

---

### 🌟 Funcionalidades Principais (v2.9.1)

- **Segurança Web e Autenticação de Nível Bancário**:
  - **Hashing Criptográfico Forte**: Senhas protegidas com **PBKDF2-HMAC-SHA256** e *salt* exclusivo por usuário.
  - **Proteção contra SQL Injection**: Consultas 100% parametrizadas em SQLite com modo WAL.
  - **Rate Limiting & Lockout Inteligente**: Bloqueio automático após tentativas inválidas (`AUTH_MAX_LOGIN_ATTEMPTS`, `AUTH_LOCKOUT_SECONDS`) e utilitário CLI (`scripts/reset_password.py`) que limpa bloqueios e revoga sessões ativas.
  - **Fluxo Obrigatório de Primeiro Acesso**: Credenciais iniciais (`Admin` / `Admin`) exigem redefinição imediata no primeiro login.
  - **Cookies, CORS e WebSockets Endurecidos**: Controle explícito de `COOKIE_SECURE`, `ALLOWED_ORIGINS`, expiração por inatividade (`AUTH_MAX_IDLE_SECONDS`), sessão máxima (`AUTH_MAX_SESSION_SECONDS`) e headers HTTP de defesa (`X-Content-Type-Options`, `X-Frame-Options`, `Content-Security-Policy`).
- **Sentimento Financeiro Híbrido (Gemini 3.5 / 3.8 Interactions API + Léxico)**:
  - Modo `gemini` (`SENTIMENT_MODE=gemini`): utiliza o SDK oficial `google-genai>=2.30.1` e a **Interactions API** (`client.interactions.create`) para avaliação contextual de manchetes financeiras da B3 com cache inteligente (`SENTIMENT_CACHE_TTL`).
  - Modo `lightweight` (`SENTIMENT_MODE=lightweight`): motor léxico financeiro local de baixíssima latência, também acionado automaticamente como *fallback* resiliente.
- **Arquitetura Dual-Environment & Gestão de Capital**:
  - Alternância segura entre **Homologação (Paper Trading)** e **Produção**, com estados isolados (`data/simulator_state.json` e `data/prod_simulator_state.json`).
  - Gestão dedicada de **Saldo na Corretora**, **Teto Alocado para a IA** e **Cofre de Reserva Protegido**.
- **Gerenciamento de Risco Rigoroso**:
  - **Trailing Stop Dinâmico** (`TRAILING_STOP_TRIGGER_PERCENT=0.02`, `TRAILING_STOP_DISTANCE_PERCENT=0.015`) e **Stop-Loss Inicial** (`STOP_LOSS_PERCENT=0.10`).
  - **Controle de Exposição**: Limite máximo de concentração por ativo (`MAX_CONCENTRATION=0.25`), perda diária máxima (`MAX_DAILY_LOSS_PERCENT=0.03`) e dimensionamento dinâmico (`MAX_POSITIONS=0` ilimitado por capital/concentração ou teto fixo).
- **Execução Multi-Broker & MT5 Bridge Protegida**:
  - `simulator`: Paper trading de alta fidelidade com persistência transacional.
  - `mt5`: Execução nativa direta via terminal MetaTrader 5 no Windows (`ClearInvestimentos-CLEAR`).
  - `bridge`: Comunicação HTTP REST autenticada via header `X-Bridge-Api-Key` (`BRIDGE_API_KEY`) com o servidor `bridge/app.py` (Windows ou container Docker/Wine isolado em `127.0.0.1:5000` e noVNC autenticado em `127.0.0.1:6080`).
- **Backtesting & Relatórios**:
  - Motor de backtesting histórico com métricas quantitativas e geração de gráficos via `matplotlib`.
  - Alertas operacionais e resumos diários via Telegram (`TELEGRAM_CHAT_ID` / `TELEGRAM_CHANNEL_ID`).

---

### 🏗️ Arquitetura do Sistema

```text
+----------------------------------------------------------------------------+
|                             CÓRTEX IA v2.9.1                               |
+----------------------------------------------------------------------------+
|  +----------------+    +--------------------+    +---------------------+   |
|  |   MarketData   |    |    NewsScraper     |    |      Portfolio      |   |
|  | (yfinance/MT5) |    |  (RSS / Portais)   |    |  (Homolog / Prod)   |   |
|  +-------+--------+    +---------+----------+    +----------+----------+   |
|          |                       |                          |              |
|          v                       v                          v              |
|  +----------------+    +--------------------+    +---------------------+   |
|  |   Technical    |    | SentimentAnalyzer  |    |    RiskManager      |   |
|  |   Analyzer     |    | (Gemini 3.5/3.8 /  |    | (Trailing, Stop,    |   |
|  | (EMA/MACD/RSI) |    |  Lexical Fallback) |    |  Max Concentration) |   |
|  +-------+--------+    +---------+----------+    +----------+----------+   |
|          |                       |                          |              |
|          +-------------------+   |   +----------------------+              |
|                              v   v   v                                     |
|                        +-------------------+                               |
|                        |  DecisionEngine   |                               |
|                        |   (O Cérebro IA)  |                               |
|                        +---------+---------+                               |
|                                  |                                         |
|                                  v                                         |
|                        +-------------------+                               |
|                        |   CortexEngine    | <=== (Scheduler B3, Health)   |
|                        |  (Orquestrador)   |                               |
|                        +---------+---------+                               |
|                                  |                                         |
|                 +----------------+----------------+                        |
|                 |                                 |                        |
|                 v                                 v                        |
|      +---------------------+           +---------------------+             |
|      |       Broker        |           |  Dashboard Server   |             |
|      | (Sim / MT5 / Bridge |           | (FastAPI / Auth /   |             |
|      |  c/ BRIDGE_API_KEY) |           |  WebSockets :8003)  |             |
|      +---------------------+           +---------------------+             |
+----------------------------------------------------------------------------+
```

---

### 📋 Pré-requisitos

- **Python 3.10 a 3.12** (Recomendado **Python 3.12**)
- **Linux (Ubuntu 22.04+ / Debian 12+ / VPS)**: Operação 24/7 via `systemd` (`SimulatorBroker` ou `MT5 Bridge`).
- **Windows 10/11**: Operação completa com `SimulatorBroker`, `MT5Broker` nativo ou servidor `iniciar_bridge.bat`.

---

### 🚀 Instalação Rápida (Instalador Universal)

O instalador universal em `scripts/setup.py` cria o ambiente `.venv` na raiz do projeto, instala as dependências compatíveis com o seu SO, prepara `data/`, `logs/` e `backups/`, gera `.env` a partir de `.env.example` e valida os módulos principais.

#### 1. Clonar o repositório:
```bash
git clone https://github.com/BernardoMancia/cortex-ia.git
cd cortex-ia
```

#### 2. Executar o instalador:
- **Multiplataforma (Python direto):**
  ```bash
  python scripts/setup.py
  ```
- **Windows (CMD ou PowerShell):**
  ```bat
  scripts\setup.bat
  ```
  ou:
  ```powershell
  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
  ```
- **Linux / macOS / VPS:**
  ```bash
  bash scripts/setup.sh
  ```

#### 3. Configurar o arquivo `.env`:
Edite o arquivo `.env` gerado na raiz (consulte todos os parâmetros comentados em `.env.example`):

```ini
SIMULATION_MODE=true
BROKER_MODE=simulator
CAPITAL_INICIAL=1000000.00
MAX_POSITIONS=0
MAX_CONCENTRATION=0.25
STOP_LOSS_PERCENT=0.10
TRAILING_STOP_TRIGGER_PERCENT=0.02
TRAILING_STOP_DISTANCE_PERCENT=0.015

# Sentimento via Gemini Interactions API ('lightweight' ou 'gemini')
SENTIMENT_MODE=lightweight
GEMINI_API_KEY=

# Segurança da Bridge MT5 (descomente MT5_BRIDGE_URL quando BROKER_MODE=bridge)
# MT5_BRIDGE_URL=http://127.0.0.1:5000
MT5_STARTUP_SYMBOL=EURUSD
BRIDGE_API_KEY=gere_um_token_aleatorio_forte_aqui

# Dashboard & Sessão
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8003
ALLOWED_ORIGINS=http://localhost:8003,http://127.0.0.1:8003
COOKIE_SECURE=false
```

---

### 💻 Execução e Operação

```bash
# Execução padrão (conforme configurações do .env)
python main.py

# Forçar modo simulação (Paper Trading)
python main.py --simulation

# Executar apenas 1 ciclo completo de análise e encerrar
python main.py --once

# Subir somente o Dashboard Web seguro (sem motor de ordens)
python main.py --dashboard-only

# Ativar logs detalhados (DEBUG)
python main.py --verbose
```

#### Scripts Operacionais Linux / VPS (`systemd`):
- `./iniciar_ia.sh` — Inicia `cortex-ia.service` e valida resposta HTTP em `/login` (até 15s).
- `./parar_ia.sh` — Solicita parada graciosa (`TimeoutStopSec=90`) e confirma encerramento limpo.
- `./status_ia.sh` — Exibe estado detalhado (`active` / `failed` / `inactive`), porta e últimos logs.
- `bash scripts/backup.sh` — Gera backup atômico de `data/cortex.db`, `simulator_state.json` e `prod_simulator_state.json`.
- `bash deploy/setup.sh` — Provisionamento completo de VPS Linux (`.venv`, serviço unificado `cortex-ia.service` e `logrotate`).

#### Container Docker `mt5-bridge` & Túnel SSH `6080` (Linux / VPS):
Para operar com conta real na B3 em servidor Linux via container Wine/MT5 headless (`docker/docker-compose.yml`), mantendo a API (`127.0.0.1:5000`) e o painel diagnóstico noVNC (`127.0.0.1:6080`) estritamente restritos ao loopback local:

1. **Subir / atualizar o container `mt5-bridge` na VPS:**
   ```bash
   cd docker
   docker compose --env-file ../.env up -d --build
   curl -H "X-Bridge-Api-Key: $BRIDGE_API_KEY" http://127.0.0.1:5000/health
   ```
2. **Inspecionar o terminal MetaTrader 5 via túnel SSH seguro (porta `6080`):**
   No seu computador local, abra um túnel SSH encaminhando apenas `127.0.0.1:6080`:
   ```bash
   ssh -L 6080:127.0.0.1:6080 seu_usuario@ip_servidor
   ```
   Em seguida, acesse `http://localhost:6080/vnc.html` no navegador local e informe `VNC_PASSWORD` (caso tenha 2FA/token inicial da corretora, autentique uma única vez; a sessão fica persistida no volume Docker `mt5-data`).

#### Acesso ao Dashboard & Recuperação de Senha:
- **Local:** `http://localhost:8003`
- **Servidor / VPS:** `http://ip_servidor:8003`
- **Redefinir senha e limpar bloqueios de login via CLI:**
  ```bash
  python scripts/reset_password.py --username meu_usuario --password "MinhaNovaSenhaForte!2026"
  python scripts/reset_password.py --restore-factory-default
  ```

---

### 🧪 Suíte de Testes Automatizados

A suíte completa utiliza `pytest`, `pytest-asyncio` e `httpx` para validar autenticação, APIs do Dashboard, motor de risco, persistência dual-state, broker/bridge, agendamento B3 e sentimento:

```bash
.venv/bin/pytest tests/ -v        # Linux / macOS
.\.venv\Scripts\pytest tests\ -v  # Windows
```

---

## 🇺🇸 English

**Autonomous Quantitative Intelligence, Algorithmic Trading, Gemini Interactions API Sentiment & Bank-Grade Web Dashboard for B3 (Brazilian Stock Exchange)**

**Córtex IA (v2.9.1)** is an autonomous quantitative trading and portfolio management platform built for the Brazilian equity market (**B3**). It combines **Multi-Timeframe Technical Analysis**, **Financial News Sentiment Analysis** (powered by the **Gemini 3.5 / 3.8 Interactions API** via `google-genai>=2.30.1` with automatic local lexical fallback), **Dynamic Risk Management** (Trailing Stop, Stop-Loss, concentration caps, and daily circuit breakers), **Strict Homologation vs. Production Capital Isolation** (with Reserve Vault and `D+2` B3 settlement tracking), and a **Mobile-First Real-Time Web Dashboard**.

### Key Highlights (v2.9.1)

- **Bank-Grade Authentication & Hardened Web Layer**:
  - **PBKDF2-HMAC-SHA256** password hashing with per-user cryptographic salt, parameterized SQLite queries (WAL mode), brute-force IP/account lockout, mandatory first-login credential rotation (`Admin` / `Admin`), strict `ALLOWED_ORIGINS` / `COOKIE_SECURE` controls, and authenticated WebSockets.
- **Gemini 3.5 / 3.8 Interactions API Sentiment Engine**:
  - Supports `SENTIMENT_MODE=gemini` using `google-genai>=2.30.1` (`client.interactions.create`) with TTL caching (`SENTIMENT_CACHE_TTL`) and seamless fallback to the zero-dependency `lightweight` financial lexicon analyzer.
- **Hybrid Execution & Authenticated MT5 Bridge**:
  - Supports `SimulatorBroker` (dual JSON + SQLite persistence), native Windows `MT5Broker` (`ClearInvestimentos-CLEAR`), and `MT5 REST Bridge` secured via `X-Bridge-Api-Key` (`BRIDGE_API_KEY`).
  - Dockerized Wine/MT5 bridge (`docker/docker-compose.yml`) binds strictly to loopback (`127.0.0.1:5000` API and `127.0.0.1:6080` noVNC protected by `VNC_PASSWORD`; inspectable remotely via `ssh -L 6080:127.0.0.1:6080 user@your_server_ip` -> `http://localhost:6080/vnc.html`).
- **Safe DevOps & Zero-Leak Git Sync**:
  - `scripts/deploy.py` performs pre-deploy backups on the remote server and strictly protects live `data/*.db*` and `data/*.json`.
  - `scripts/sync_git.py` synchronizes production code (including `data/*.py`), prunes stale files, and runs an automated secret/IP scanner before any commit; remote pushes require explicit `--push`.

### Quick Start (English)

1. **Clone & Install**:
   ```bash
   git clone https://github.com/BernardoMancia/cortex-ia.git
   cd cortex-ia
   python scripts/setup.py
   ```
   *(Or run `scripts\setup.bat` on Windows / `bash scripts/setup.sh` on Linux & macOS).*

2. **Configure `.env`**:
   Copy or edit `.env` from `.env.example`, setting `SIMULATION_MODE`, `BROKER_MODE`, `BRIDGE_API_KEY` (if using the MT5 REST Bridge), `GEMINI_API_KEY` (if `SENTIMENT_MODE=gemini`), and Telegram credentials.

3. **Run & Test**:
   ```bash
   python main.py --simulation
   pytest tests/ -v
   ```
   Access the web dashboard at `http://localhost:8003` (or `http://your_server_ip:8003`).

---

## 📜 Licença / License

**Copyright © 2026 Bernardo Mancia. Todos os direitos reservados / All rights reserved.**

Disponibilizado exclusivamente para fins de demonstração técnica, portfólio profissional e auditoria educacional. Consulte [LICENSE](LICENSE) para os termos completos.

---

## ⚠️ Aviso Legal / Disclaimer

O **Córtex IA** é um software experimental de engenharia quantitativa e **não constitui recomendação de investimento**, análise de valores mobiliários ou oferta de compra/venda de ativos. Operações em renda variável envolvem riscos substanciais de perda patrimonial.
