<div align="center">

# 🧠 CÓRTEX-IA
### Autonomous Quantitative Trading & Financial Intelligence System for B3
*Sistema Autônomo de Negociação Quantitativa e Inteligência Financeira para a B3*

[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.12-blue.svg?style=for-the-badge&logo=python)](https://www.python.org/)
[![Market](https://img.shields.io/badge/Market-B3%20Fractional%20(Brasil)-green.svg?style=for-the-badge)](https://www.b3.com.br/)
[![Framework](https://img.shields.io/badge/Backend-FastAPI%20%7C%20MetaTrader5-009688.svg?style=for-the-badge)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](LICENSE)

[Português](#-português) • [English](#-english)

</div>

---

<a name="-português"></a>
## 🇧🇷 Português

### 📌 Sobre o Projeto
O **Córtex-IA** é um ecossistema completo e autônomo de negociação quantitativa focado no **mercado fracionário da bolsa brasileira (B3)**. Projetado para operar com gestão de risco estrita e limites de capital adaptáveis, o sistema combina indicadores de **Análise Técnica** com **Análise de Sentimento (NLP)** em tempo real extraída de notícias do mercado financeiro.

### ⚡ Principais Funcionalidades
- 📊 **Análise Técnica Multifator**: Avaliação de médias móveis (EMA 9, 21, 50), RSI, MACD e suporte/resistência.
- 📰 **Processamento de Linguagem Natural (NLP)**: Leitura de notícias via RSS Feeds (Google News, InfoMoney, Investing.com) para calcular a pontuação de sentimento dos ativos.
- 🛡️ **Gerenciamento Rígido de Risco**: Stop-loss automático dinâmico, limitação por posição e monitoramento contínuo de exposição.
- 🔄 **Execução Híbrida Versátil**:
  - `SimulatorBroker`: Ambiente seguro para paper trading e backtesting sem risco.
  - `MT5Broker`: Integração nativa com MetaTrader 5 para envio de ordens reais.
- 📱 **Notificações via Telegram**: Alertas instantâneos de compra/venda, acionamento de stop e fechamento de mercado.
- 🖥️ **Dashboard Web Interativo**: Painel FastAPI + WebSockets para monitoramento ao vivo de posições, saldo e logs.
- 🏥 **Health Check & Logs Centrais**: Monitoramento continuo do servidor (CPU/RAM/Disco) e logging centralizado SQLite.

---

### 🏗️ Arquitetura do Sistema
