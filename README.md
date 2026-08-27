# Córtex IA — v2.5.0

**Sistema Autônomo de Inteligência Quantitativa e Algorithmic Trading para a B3**

O **Córtex IA** é um ecossistema autônomo de negociação quantitativa e gerenciamento de portfólio para o mercado de ações brasileiro (**B3**). O sistema opera de forma contínua combinando **Análise Técnica Multitemporal**, **Processamento de Linguagem Natural (NLP) para Sentimento de Notícias** e **Gerenciamento de Risco Rigoroso** com Trailing Stop dinâmico.

Monitora em tempo real um universo de **101 ações líquidas da B3** (ações padrão e mercado fracionário), executa ordens de forma inteligente, gerencia o ciclo de vida das posições e disponibiliza um **Dashboard Web em Tempo Real** 100% responsivo para desktop, tablets e smartphones.

---

## 🌟 Funcionalidades Principais

- **Análise Técnica Avançada**: Médias Móveis Exponenciais (EMA 9, 21, 50), MACD, RSI e detecção de rompimentos e tendências.
- **Análise de Sentimento (NLP Financeiro)**: Escaneamento e pontuação contínua de notícias de portais financeiros (InfoMoney, Money Times, Investing, Google News).
- **Gerenciamento de Risco e Capital**:
  - **Trailing Stop Dinâmico**: Protege lucros subindo o stop automaticamente após atingir o gatilho (+2,0%).
  - **Stop-Loss Inicial**: Proteção estrita contra volatilidade adversa.
  - **Normalização de Tickers**: Suporte transparente para lotes padrão e fracionário (ex: `PETR4` e `PETR4F`).
  - **Controle de Concentração**: Limite máximo de exposição por ativo (25%) e travas diárias.
- **Sincronização Bidirecional Portfolio ⇄ Broker**:
  - Sincronização automática na inicialização e antes de cada ciclo de trading, garantindo consistência total entre saldo, posições abertas e custódia.
- **Execução Híbrida**:
  - `SimulatorBroker`: Ambiente completo de paper trading de alta fidelidade com persistência em banco SQLite e JSON.
  - `MT5Broker` / `RESTBridge`: Integração com MetaTrader 5 (Windows) para envio real de ordens com corretagem zero.
- **Dashboard Web Multi-Dispositivo (Mobile-First)**:
  - Interface moderna Fintech / Glassmorphism de alto contraste.
  - Navegação fluida por abas em celulares e tablets (*Visão Geral, Carteira, Cérebro IA, Terminal, Notícias*).
  - Visualização alternável: **Tabela Completa** e **Cards Tácteis** para smartphones.
  - Gráfico de Alocação interativo High-DPI com chips percentuais em tempo real.
  - Terminal de logs ao vivo via **WebSockets** com realce de sintaxe por nível (`INFO`, `WARNING`, `ERROR`).
- **Sistema de Notificações**: Alertas automáticos via Telegram para compras, vendas, stop-loss e relatórios diários.
- **Suíte de Testes Automatizados**: Mais de 70 testes unitários cobrindo tomada de decisão, gestão de risco, calendário B3 e análise técnica.

---

## 🏗️ Arquitetura do Sistema

```text
+--------------------------------------------------------------------------+
|                             CÓRTEX IA v2.5.0                             |
+--------------------------------------------------------------------------+
|  +----------------+    +------------------+    +---------------------+   |
|  |   MarketData   |    |    NewsScraper   |    |      Portfolio      |   |
|  | (yfinance/MT5) |    |  (RSS / Portais) |    |  (Controle de P&L)  |   |
|  +-------+--------+    +--------+---------+    +----------+----------+   |
|          |                      |                         |              |
|          v                      v                         v              |
|  +----------------+    +------------------+    +---------------------+   |
|  |   Technical    |    |     Sentiment    |    |    RiskManager      |   |
|  |   Analyzer     |    |     Analyzer     |    | (Trailing, Stop-Loss|   |
|  +-------+--------+    +--------+---------+    +----------+----------+   |
|          |                      |                         |              |
|          +------------------+   |   +---------------------+              |
|                             v   v   v                                    |
|                       +-------------------+                              |
|                       |  DecisionEngine   |                              |
|                       |   (O Cérebro IA)  |                              |
|                       +---------+---------+                              |
|                                 |                                        |
|                                 v                                        |
|                       +-------------------+                              |
|                       |   CortexEngine    | <=== (Scheduler B3, Health)  |
|                       | (Orquestrador)    |                              |
|                       +---------+---------+                              |
|                                 |                                        |
|                +----------------+----------------+                       |
|                |                                 |                       |
|                v                                 v                       |
|       +-----------------+               +-----------------+              |
|       |     Broker      |               | Dashboard Server|              |
|       | (Simulator/MT5) |               |  (FastAPI / WS) |              |
|       +-----------------+               +-----------------+              |
+--------------------------------------------------------------------------+
```

---

## 📋 Pré-requisitos

- **Python 3.10+** (Recomendado **Python 3.12**)
- Ambientes suportados:
  - **Linux (Ubuntu 22.04 / Debian 12 / VPS)**: Ideal para operação 24/7 com `SimulatorBroker`.
  - **Windows 10/11**: Suporte completo tanto para simulação quanto para execução real com `MT5Broker`.

---

## 🚀 Instalação e Configuração Rápida (1-Click)

O Córtex IA conta com um **instalador universal automatizado** que detecta seu sistema operacional (Windows, Linux, macOS ou Servidor VPS), cria o ambiente virtual `.venv`, atualiza ferramentas, instala todas as dependências compatíveis, cria as pastas estruturais e valida a integridade do sistema.

### 1. Clonar o repositório:
```bash
git clone https://github.com/BernardoMancia/cortex-ia.git
cd cortex-ia
```

### 2. Executar o Instalador Automático:

- **No Windows:**
  - Execute via terminal: `python bootstrap.py` (ou dê 2 cliques no arquivo `setup.bat`)
- **No Linux / VPS / macOS:**
  - Execute no terminal: `bash setup.sh` (ou `python3 bootstrap.py`)

---

### 4. Configurar variáveis de ambiente (`.env`):
Crie ou edite o arquivo `.env` na raiz do projeto:

```ini
# Modo de Operação
SIMULATION_MODE=True
BROKER_MODE=simulator
CAPITAL_INICIAL=100000.00
STOP_LOSS_PERCENT=0.10
TRAILING_STOP_TRIGGER_PERCENT=0.02
TRAILING_STOP_DISTANCE_PERCENT=0.015

# Credenciais MetaTrader 5 (Necessário apenas se BROKER_MODE=mt5)
MT5_LOGIN=seu_login
MT5_PASSWORD=sua_senha
MT5_SERVER=ClearInvestimentos-Server

# Alertas via Telegram
TELEGRAM_TOKEN=seu_token_aqui
TELEGRAM_CHAT_ID=seu_chat_id_aqui

# Configurações do Dashboard Web
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8003

# Intervalos de Execução (em segundos)
TRADING_CYCLE_INTERVAL=60
CLOSED_CHECK_INTERVAL=300
HEALTH_CHECK_INTERVAL=120
LOG_LEVEL=INFO
```

---

## 💻 Uso e Execução

O Córtex possui flags de linha de comando para diferentes cenários operacionais:

**1. Execução Padrão (utiliza as configurações do `.env`):**
```bash
python main.py
```

**2. Forçar Modo Simulação (Paper Trading):**
```bash
python main.py --simulation
```

**3. Modo Verboso (Logs em nível DEBUG detalhados):**
```bash
python main.py --verbose
```

**4. Ciclo Único de Validação (Executa 1 ciclo e encerra):**
```bash
python main.py --once
```

**5. Apenas Dashboard Web (Sem motor de ordens ativo):**
```bash
python main.py --dashboard-only
```

---

## 🖥️ Acesso ao Dashboard Web

Quando o Córtex está ativo, o painel web fica acessível em:

- **Localmente:** `http://localhost:8003` (ou porta configurada)
- **No Servidor / VPS:** `http://ip_servidor:8003`

### Recursos do Dashboard:
- **Resumo Financeiro:** Patrimônio total, saldo em caixa livre, alocação e P&L consolidado.
- **Custódia em Tempo Real:** Tabela e cards com cotação atual, preço médio, stop-loss e trailing stop.
- **Radar do Cérebro IA:** Feed filtrável com os pensamentos, análises técnicas e decisões de cada ativo.
- **Gráfico de Alocação:** Visualização doughnut responsiva com legenda dinâmica por ativo.
- **Terminal ao Vivo:** Stream de logs com syntax highlighting, pausa e busca textual.
- **Notícias da B3:** Últimas notícias financeiras capturadas pelo scraper com links originais.

---

## 🧪 Testes Automatizados

Para rodar a suíte completa de testes unitários e de integração:

```bash
pytest tests/ -v
```

---

## 🌐 Deploy na VPS (Systemd)

Para manter o Córtex rodando 24/7 na VPS:

```bash
# Copiar o serviço systemd
sudo cp scripts/cortex-ia.service /etc/systemd/system/
sudo systemctl daemon-reload

# Habilitar e iniciar
sudo systemctl enable cortex-ia.service
sudo systemctl restart cortex-ia.service

# Verificar status e logs
sudo systemctl status cortex-ia.service
journalctl -u cortex-ia.service -f
```

---

## 📜 Licença e Termos de Uso

**Copyright © 2026 Bernardo Mancia. Todos os direitos reservados.**

Este repositório é disponibilizado publicamente exclusivamente para fins de **demonstração técnica, portfólio profissional e auditoria educacional**. É expressamente proibida a reprodução, cópia, redistribuição ou exploração comercial sem autorização prévia por escrito.

Consulte o arquivo [LICENSE](LICENSE) para os termos completos.

---

## ⚠️ Aviso Legal e Isenção de Responsabilidade (Disclaimer)

O **Córtex IA** é um sistema experimental de inteligência computacional e algoritmos quantitativos. Este projeto **não constitui recomendação de investimento**, consultoria financeira ou solicitação de compra/venda de ativos mobiliários. Operações em renda variável na B3 envolvem riscos substanciais de perda patrimonial. O autor não se responsabiliza por quaisquer decisões financeiras ou prejuízos decorrentes do uso direto ou indireto deste software.

