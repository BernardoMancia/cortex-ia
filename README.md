# Projeto Córtex

**Sistema de Inteligência de Mercado e Algorithmic Trading para a B3 (Mercado Fracionário)**

O Projeto Córtex é um ecossistema automatizado de negociação quantitativa focado no mercado de ações fracionário brasileiro (B3). Ele atua como um agente autônomo projetado para operar com um limite inicial de capital (ex: R$ 200,00), realizando decisões inteligentes baseadas na combinação de **Análise Técnica** e **Análise de Sentimento (NLP)**.

O Córtex monitora em tempo real uma carteira de ativos pré-selecionados, identifica oportunidades de mercado, gerencia o risco de cada operação e reporta os resultados via Telegram, além de possuir um Dashboard Web para acompanhamento em tempo real.

---

## 🌟 Funcionalidades Principais

- **Análise Técnica**: Cálculo automático de Indicadores (EMA 9, 21, 50, RSI) e detecção de Suporte/Resistência.
- **Análise de Sentimento (NLP)**: Escaneamento de notícias via RSS feeds (Google News, InfoMoney, Investing.com) com processamento de linguagem natural focado no mercado financeiro (FinBERT-PT-BR ou léxico leve).
- **Gerenciamento de Risco Rigoroso**: Proteção do capital (ex: R$ 200,00) via stop-loss dinâmico (calculado automaticamente em 10% por padrão) e limitação de exposição (1-99 ações no fracionário).
- **Execução Híbrida**: 
  - `SimulatorBroker`: Ambiente seguro para paper trading e validação de estratégias.
  - `MT5Broker`: Integração com MetaTrader 5 (Windows) para envio real de ordens (via Clear Corretora, taxa zero).
- **Notificações Integradas**: Alertas por Telegram em tempo real (abertura de posições, stop-loss acionado, fechamento de mercado).
- **Dashboard Web (WebSocket)**: Interface local limpa e moderna para visualizar o status do robô, portfólio, notícias avaliadas e saúde do servidor.
- **Monitoramento de Saúde (Health Check)**: Observação constante do uso de CPU, RAM e Disco, parando o robô caso necessário.

---

## 🏗️ Arquitetura do Sistema

```text
+--------------------------------------------------------------------------+
|                              PROJETO CÓRTEX                              |
+--------------------------------------------------------------------------+
|  +----------------+    +------------------+    +---------------------+   |
|  |   MarketData   |    |    NewsScraper   |    |      Portfolio      |   |
|  | (yfinance/MT5) |    |   (RSS Feeds)    |    |  (Controle de PNL)  |   |
|  +-------+--------+    +--------+---------+    +----------+----------+   |
|          |                      |                         |              |
|          v                      v                         v              |
|  +----------------+    +------------------+    +---------------------+   |
|  |   Technical    |    |     Sentiment    |    |    RiskManager      |   |
|  |   Analyzer     |    |     Analyzer     |    | (Stop-loss, Capital)|   |
|  +-------+--------+    +--------+---------+    +----------+----------+   |
|          |                      |                         |              |
|          +------------------+   |   +---------------------+              |
|                             v   v   v                                    |
|                       +-------------------+                              |
|                       |  DecisionEngine   |                              |
|                       |   (O Cérebro)     |                              |
|                       +---------+---------+                              |
|                                 |                                        |
|                                 v                                        |
|                       +-------------------+                              |
|                       |   CortexEngine    | <=== (Scheduler, Health)     |
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

## 📋 Pré-requisitos e Dependências

- **Python 3.10+** (Recomendado 3.12)
- Sistema Operacional: 
  - **Linux (VPS)**: Ideal para modo `SimulatorBroker`.
  - **Windows**: Obrigatório apenas para o modo `MT5Broker` (MetaTrader 5 SDK é exclusivo do Windows).

---

## 🚀 Instalação

1. **Clone o repositório:**
   ```bash
   git clone https://github.com/seu-usuario/cortex-ia.git
   cd cortex-ia
   ```

2. **Crie um ambiente virtual (recomendado):**
   ```bash
   python -m venv venv
   
   # No Windows:
   venv\Scripts\activate
   # No Linux/Mac:
   source venv/bin/activate
   ```

3. **Instale as dependências:**
   ```bash
   pip install -r requirements.txt
   ```

---

## ⚙️ Configuração (.env)

Crie um arquivo `.env` na raiz do projeto contendo as seguintes configurações principais. (Se não criado, o sistema usará os padrões seguros).

```ini
# Configurações de Operação
SIMULATION_MODE=True
BROKER_MODE=simulator
CAPITAL_INICIAL=200.00
STOP_LOSS_PERCENT=0.10
SENTIMENT_MODE=lightweight # Opções: lightweight ou full (usa FinBERT)

# Credenciais MT5 (Se BROKER_MODE=mt5)
MT5_LOGIN=seu_login
MT5_PASSWORD=sua_senha
MT5_SERVER=ClearInvestimentos-Server

# Notificações via Telegram
TELEGRAM_TOKEN=seu_bot_token_aqui
TELEGRAM_CHAT_ID=seu_chat_id_aqui

# Dashboard
DASHBOARD_HOST=0.0.0.0
DASHBOARD_PORT=8000

# Logs e Intervalos (segundos)
LOG_LEVEL=INFO
TRADING_CYCLE_INTERVAL=60
CLOSED_CHECK_INTERVAL=300
HEALTH_CHECK_INTERVAL=120
```

---

## 💻 Uso e Execução

O sistema possui vários modos de execução baseados em flags na linha de comando:

**1. Modo Padrão (usa a configuração do `.env`):**
```bash
python main.py
```

**2. Modo Simulação (Ignora o `.env` e força o SimulatorBroker):**
```bash
python main.py --simulation
```

**3. Modo Detalhado (Log em nível DEBUG para inspeção):**
```bash
python main.py --verbose
# ou
python main.py --simulation -v
```

**4. Execução Única (Roda 1 ciclo de análise e encerra — ótimo para testes):**
```bash
python main.py --once
```

**5. Apenas Dashboard (Sem motor de trading rodando):**
```bash
python main.py --dashboard-only
```

---

## 🖥️ Acesso ao Dashboard

Quando o sistema está rodando, o Dashboard fica acessível via web.
Acesse no seu navegador:

**Localmente:** `http://localhost:8000`  
**Na VPS:** `http://IP_DA_VPS:8000`

O dashboard exibirá:
- Status de Conexão com o motor.
- Preços em tempo real e evolução da carteira.
- P&L das posições abertas.
- Últimas decisões tomadas ("Pensamento do Córtex").
- Uso de CPU/RAM.

---

## 🌐 Deploy na VPS (Linux)

O repositório inclui um script shell pronto para realizar o setup automático e colocar o bot para rodar como um serviço *Systemd* (garantindo que o processo inicie sozinho se a máquina reiniciar).

1. Suba os arquivos do projeto para sua VPS (ex: `/home/servico/cortex-ia`).
2. Acesse a pasta via terminal.
3. Torne o script de setup executável:
   ```bash
   chmod +x deploy/setup.sh
   ```
4. Execute o setup como root ou com permissão sudo:
   ```bash
   sudo ./deploy/setup.sh
   ```
   *(O script atualizará pacotes, instalará Python 3.12, instalará as dependências, configurará o Córtex como serviço e abrirá a porta 8000 no firewall).*

**Comandos do Serviço:**
- **Status:** `sudo systemctl status cortex`
- **Parar:** `sudo systemctl stop cortex`
- **Reiniciar:** `sudo systemctl restart cortex`
- **Logs ao vivo:** `journalctl -u cortex -f`

---

## 📄 Licença

Este software é distribuído sob a Licença **MIT**. Veja o arquivo `LICENSE` (ou crie um) para mais detalhes.

---
**Desenvolvido por Luke Arwolf** | Inteligência de Mercado e Algorithmic Trading
