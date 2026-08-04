<div align="center">

# 🧠 CÓRTEX-IA
### Autonomous Quantitative Trading & Financial Intelligence System for B3
*Sistema Autônomo de Negociação Quantitativa e Inteligência Financeira para a B3*

[![Python Version](https://img.shields.io/badge/Python-3.10%20%7C%203.12-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Market](https://img.shields.io/badge/Market-B3%20Fractional%20(Brasil)-00529B?style=for-the-badge)](https://www.b3.com.br/)
[![Framework](https://img.shields.io/badge/Backend-FastAPI%20%7C%20MetaTrader5-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![NLP Sentiment](https://img.shields.io/badge/NLP-Financial%20News-FF6F00?style=for-the-badge)](https://github.com/BernardoMancia/cortex-ia)
[![License](https://img.shields.io/badge/License-MIT-green.svg?style=for-the-badge)](LICENSE)

[ 🇧🇷 Português ](#-português) &nbsp;•&nbsp; [ 🇺🇸 English ](#-english)

</div>

---

<a name="-português"></a>
## 🇧🇷 Português

### 📌 Sobre o Projeto
O **Córtex-IA** é um ecossistema completo e autônomo de negociação quantitativa focado no **mercado fracionário da bolsa brasileira (B3)**. Projetado para operar com gestão de risco estrita e limites de capital adaptáveis, o sistema combina indicadores de **Análise Técnica** clássica e avançada com **Análise de Sentimento (NLP)** em tempo real extraída de notícias do mercado financeiro.

O motor atua de forma ininterrupta: monitora uma carteira de ativos (ex: PETR4, VALE3, ITUB4), calcula o risco de cada operação, envia sinal de compra ou venda para a corretora (via MetaTrader 5 ou Simulador interno), dispara notificações no Telegram e expõe uma Dashboard Web em tempo real.

---

### ⚡ Principais Funcionalidades
- 📊 **Análise Técnica Multifator**: Cálculo de médias móveis exponenciais (EMA 9, 21, 50), Índice de Força Relativa (RSI), MACD e níveis dinâmicos de Suporte e Resistência.
- 📰 **Processamento de Linguagem Natural (NLP)**: Coleta e análise contínua de notícias via RSS Feeds (Google News, InfoMoney, Investing.com) para cálculo do escore de sentimento dos ativos.
- 🛡️ **Gerenciamento Rígido de Risco**: Stop-loss automático dinâmico (padrão de 10%), dimensionamento de lote (1 a 99 ações no fracionário) e proteção de saldo.
- 🔄 **Execução Híbrida Versátil**:
  - `SimulatorBroker`: Ambiente 100% seguro para paper trading, simulação e backtesting.
  - `MT5Broker`: Integração nativa via biblioteca MetaTrader 5 para roteamento real de ordens.
- 📱 **Notificações via Telegram**: Alertas instantâneos sobre ordens executadas, acionamento de stop-loss e resumos de mercado.
- 🖥️ **Dashboard Web Interativo (FastAPI + WebSockets)**: Interface moderna em tempo real para visualizar posições abertas, evolução patrimonial, log de operações e saúde do servidor.
- 🏥 **Health Check & Logs Centrais**: Monitoramento constante de uso de CPU, memória RAM e disco com fallback de proteção.

---

### 🏗️ Arquitetura do Sistema

```text
+--------------------------------------------------------------------------+
|                              CÓRTEX ENGINE                               |
+--------------------------------------------------------------------------+
|  +----------------+    +------------------+    +---------------------+   |
|  |   MarketData   |    |    NewsScraper   |    |      Portfolio      |   |
|  | (yfinance/MT5) |    |   (RSS Feeds)    |    |  (PNL & Balanço)    |   |
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
|                       |  (Decisão Final)  |                              |
|                       +---------+---------+                              |
|                                 |                                        |
|                                 v                                        |
|                       +-------------------+                              |
|                       |   CortexEngine    |                              |
|                       +---------+---------+                              |
|                                 |                                        |
|                +----------------+----------------+                       |
|                v                                 v                       |
|       +-----------------+               +-----------------+              |
|       |     Broker      |               | Dashboard Web   |              |
|       | (Simulator/MT5) |               |  (FastAPI / WS) |              |
|       +-----------------+               +-----------------+              |
+--------------------------------------------------------------------------+
```

---

### 🚀 Guia de Início Rápido

#### 1. Clonar o Repositório
```bash
git clone https://github.com/BernardoMancia/cortex-ia.git
cd cortex-ia
```

#### 2. Configurar o Ambiente Virtual
```bash
# Criar venv
python -m venv .venv

# Ativar no Windows:
.venv\Scriptsctivate

# Ativar no Linux/macOS:
source .venv/bin/activate
```

#### 3. Instalar Dependências
```bash
pip install -r requirements.txt
```

#### 4. Configurar Variáveis de Ambiente
Copie o arquivo de modelo `.env.example` para `.env` e preencha suas configurações:
```bash
cp .env.example .env
```

---

### ⚙️ Variáveis de Ambiente (`.env`)

| Variável | Padrão | Descrição |
| :--- | :--- | :--- |
| `SIMULATION_MODE` | `true` | `true` para modo simulação sem risco; `false` para operação real |
| `CAPITAL_INICIAL` | `200.00` | Capital inicial reservado para negociação (em R$) |
| `STOP_LOSS_PERCENT`| `0.10` | Percentual padrão de stop-loss (10%) |
| `SENTIMENT_MODE` | `lightweight` | Modo de análise de sentimento (`lightweight` ou `full`) |
| `MT5_LOGIN` | `-` | Número da conta no MetaTrader 5 |
| `MT5_PASSWORD` | `-` | Senha da conta no MetaTrader 5 |
| `MT5_SERVER` | `ClearInvestimentos-Server` | Nome do servidor de negociação da corretora |
| `TELEGRAM_TOKEN` | `-` | Token de API do Bot do Telegram |
| `TELEGRAM_CHAT_ID` | `-` | ID do chat no Telegram para receber os alertas |
| `DASHBOARD_PORT` | `8003` | Porta HTTP para o Dashboard Web local/servidor |

---

### 💻 Execução do Sistema

| Comando | Descrição |
| :--- | :--- |
| `python main.py` | Execução padrão (utiliza configurações salvas no `.env`) |
| `python main.py --simulation` | Força a execução em modo simulação |
| `python main.py --once` | Roda 1 ciclo completo de análise e encerra (ideal para testes) |
| `python main.py --dashboard-only` | Inicia apenas o servidor do Dashboard Web na porta 8003 |

---

<br/>

---

<a name="-english"></a>
## 🇺🇸 English

### 📌 About The Project
**Córtex-IA** is a complete, autonomous quantitative trading ecosystem focused on the **Brazilian Stock Exchange (B3) fractional equity market**. Engineered for strict risk mitigation and dynamic capital allocation, the system synergizes classic/advanced **Technical Analysis** with real-time **Financial News NLP Sentiment Analysis**.

The engine operates continuously: monitoring a portfolio of selected stocks (e.g., PETR4, VALE3, ITUB4), evaluating per-trade risk metrics, executing orders (via MetaTrader 5 or built-in Simulator), pushing live alerts to Telegram, and serving a real-time Web Dashboard.

---

### ⚡ Key Features
- 📊 **Multifactor Technical Analysis**: Computes Exponential Moving Averages (EMA 9, 21, 50), Relative Strength Index (RSI), MACD, and dynamic Support/Resistance levels.
- 📰 **Natural Language Processing (NLP)**: Real-time scraping and processing of financial news via RSS feeds (Google News, InfoMoney, Investing.com) to derive asset sentiment scores.
- 🛡️ **Strict Risk Management**: Automatic dynamic stop-loss (default 10%), fractional lot sizing (1-99 shares), and equity drawdown safeguards.
- 🔄 **Versatile Hybrid Execution**:
  - `SimulatorBroker`: 100% risk-free paper trading & strategy validation environment.
  - `MT5Broker`: Native MetaTrader 5 Python SDK integration for live order routing.
- 📱 **Telegram Bot Integration**: Instant alerts for executed trades, stop-loss triggers, and market summaries.
- 🖥️ **Interactive Web Dashboard (FastAPI + WebSockets)**: Modern real-time monitoring interface for open positions, equity growth, trade logs, and server health.
- 🏥 **Health Check & Centralized Logging**: Continuous monitoring of CPU, RAM, and disk utilization with automated safeguards.

---

### 🚀 Quick Start Guide

#### 1. Clone the Repository
```bash
git clone https://github.com/BernardoMancia/cortex-ia.git
cd cortex-ia
```

#### 2. Configure Virtual Environment
```bash
# Create venv
python -m venv .venv

# Activate on Windows:
.venv\Scriptsctivate

# Activate on Linux/macOS:
source .venv/bin/activate
```

#### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

#### 4. Configure Environment Variables
Copy the template `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```

---

### ⚙️ Environment Variables (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `SIMULATION_MODE` | `true` | `true` for paper trading; `false` for live execution |
| `CAPITAL_INICIAL` | `200.00` | Initial capital allocated for trading (in BRL) |
| `STOP_LOSS_PERCENT`| `0.10` | Default stop-loss percentage (10%) |
| `SENTIMENT_MODE` | `lightweight` | Sentiment engine mode (`lightweight` or `full`) |
| `MT5_LOGIN` | `-` | MetaTrader 5 account login number |
| `MT5_PASSWORD` | `-` | MetaTrader 5 account password |
| `MT5_SERVER` | `ClearInvestimentos-Server` | Broker server identifier |
| `TELEGRAM_TOKEN` | `-` | Telegram Bot API Token |
| `TELEGRAM_CHAT_ID` | `-` | Telegram Chat ID for receiving notifications |
| `DASHBOARD_PORT` | `8003` | HTTP port for Web Dashboard |

---

### 💻 Execution Commands

| Command | Description |
| :--- | :--- |
| `python main.py` | Standard execution (uses `.env` settings) |
| `python main.py --simulation` | Forces paper trading simulation mode |
| `python main.py --once` | Runs 1 single analysis cycle and exits (great for testing) |
| `python main.py --dashboard-only` | Launches Web Dashboard server only on port 8003 |

---

### 📄 License
Distributed under the **MIT License**. See `LICENSE` for more details.

<div align="center">
  <sub>Developed by <b>Luke Arwolf</b> • Market Intelligence & Quantitative Trading</sub>
</div>
