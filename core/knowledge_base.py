"""
Base de conhecimento interativa e glossário do Assistente Córtex IA.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

GLOSSARY_DATABASE: List[Dict[str, Any]] = [
    {
        "id": "stop-loss",
        "category": "Risco & Proteção",
        "term": "Stop-Loss Inicial & Adaptativo (ATR)",
        "short_def": "Limite máximo de perda tolerada em uma operação antes de encerrá-la automaticamente.",
        "details": "Ao abrir uma posição de compra, o Córtex IA calcula o Stop-Loss combinando volatilidade adaptativa (1,5 × ATR com piso mínimo de distância) e o limite percentual máximo configurado (padrão: 10,0%, ajustável pelo Dashboard). Se a cotação atingir esse valor, a venda emergencial protege o patrimônio."
    },
    {
        "id": "trailing-stop",
        "category": "Risco & Proteção",
        "term": "Trailing Stop (Stop Móvel)",
        "short_def": "Mecanismo dinâmico que sobe o Stop-Loss acompanhando as altas da ação para travar o lucro.",
        "details": "À medida que a ação valoriza e atinge o Gatilho de Ativação (padrão: +2,0%, configurável no Dashboard), o Trailing Stop entra em ação e passa a acompanhar as máximas mantendo uma Distância de segurança (padrão: 1,5%). Se a ação recuar até o stop móvel, a posição é encerrada com lucro protegido."
    },
    {
        "id": "gatilho-trailing",
        "category": "Risco & Proteção",
        "term": "Gatilho de Ativação do Trailing Stop",
        "short_def": "Porcentagem mínima de lucro necessária para acionar o modo de proteção móvel.",
        "details": "Define o momento em que o Stop-Loss deixa de ser estático na entrada e passa a subir conforme novas máximas. O valor padrão do sistema é 2,0% e pode ser ajustado diretamente pelo Dashboard."
    },
    {
        "id": "distancia-trailing",
        "category": "Risco & Proteção",
        "term": "Distância do Trailing Stop",
        "short_def": "Espaçamento mantido entre a cotação atual no topo e o novo nível de Stop-Loss móvel.",
        "details": "Permite que o ativo respire sem acionar saídas prematuras por ruído intradiário, mantendo-se sempre menor que o gatilho de ativação. O valor padrão é 1,5% (configurável via Dashboard)."
    },
    {
        "id": "take-profit-parcial",
        "category": "Risco & Proteção",
        "term": "Take-Profit Parcial (+15%)",
        "short_def": "Realização automática de metade da posição ao atingir +15% de lucro.",
        "details": "Para posições com 2 ou mais ações que atingem +15,0% de valorização, o Córtex realiza 50% do volume no bolso e eleva o Stop-Loss do lote remanescente para pelo menos o preço de entrada (breakeven), sem jamais reduzir um Trailing Stop já superior."
    },
    {
        "id": "teto-capital",
        "category": "Gestão de Capital",
        "term": "Teto de Capital Operacional da IA",
        "short_def": "Valor máximo da sua produção que a inteligência artificial está autorizada a utilizar em compras.",
        "details": "Funciona como um limite de segurança rígido descontando o custo das posições já abertas. Mesmo que você possua saldo maior na corretora, a IA nunca comprometerá acima do teto definido."
    },
    {
        "id": "cofre-reserva",
        "category": "Gestão de Capital",
        "term": "Cofre Reserva Protegido",
        "short_def": "Saldo em dinheiro na produção que fica 100% blindado contra compras da IA.",
        "details": "É o montante destinado a resgates ou reserva de segurança. A IA exclui integralmente o saldo do Cofre Reserva dos cálculos de poder de compra e de rentabilidade operacional."
    },
    {
        "id": "auto-compounding",
        "category": "Gestão de Capital",
        "term": "Auto-Compounding (Juros Compostos)",
        "short_def": "Reinvestimento automático dos lucros obtidos nas vendas de ações vencedoras.",
        "details": "Sempre que uma operação realiza lucro, o ganho líquido é somado automaticamente ao teto operacional da IA e persistido no banco de dados, acelerando o crescimento composto da carteira."
    },
    {
        "id": "liquidacao-inteligente",
        "category": "Gestão de Capital",
        "term": "Liquidação Inteligente Fracionária",
        "short_def": "Algoritmo que seleciona as melhores posições para vender apenas o necessário para um saque ou ajuste de teto.",
        "details": "Quando o caixa livre é insuficiente para atender um resgate ou redução de teto, o Córtex IA prioriza as ações com maior ganho percentual e vende apenas a quantidade necessária de ações."
    },
    {
        "id": "foco-saque",
        "category": "Gestão de Capital",
        "term": "Foco de Saque (Instantâneo vs Meta de Lucro)",
        "short_def": "Ferramenta exclusiva da Produção para transferir lucros ao Cofre Reserva.",
        "details": "1. **Retirar AGORA (Instantâneo):** Usa caixa livre desbloqueado ou liquida posições lucrativas e transfere o valor ao Cofre Reserva.\n2. **Retirar por Meta de Lucro:** Monitora o patrimônio operacional em Produção e, ao atingir o alvo, executa o saque inteligente automaticamente."
    },
    {
        "id": "homologacao-vs-producao",
        "category": "Ambientes",
        "term": "Homologação vs Produção",
        "short_def": "Diferença entre o ambiente de testes simulado e a operação com dinheiro real.",
        "details": "• **Homologação:** Opera com saldo virtual e cotações reais da B3 (Paper Trading). Ideal para validar estratégias e calibrar parâmetros sem risco.\n• **Produção:** Opera exclusivamente conectada ao MetaTrader 5 / Bridge real na corretora, com proteção fail-safe automática para Homologação em caso de indisponibilidade."
    },
    {
        "id": "finbert-nlp",
        "category": "Inteligência Artificial",
        "term": "Análise de Sentimento NLP (FinBERT)",
        "short_def": "Processamento de linguagem natural das notícias do mercado financeiro brasileiro.",
        "details": "O Córtex monitora portais financeiros como InfoMoney, Valor Econômico e Google News, classificando o sentimento (-1.0 a +1.0). Compras só são autorizadas quando há convergência técnica e sentimento não hostil."
    },
    {
        "id": "analise-tecnica",
        "category": "Inteligência Artificial",
        "term": "Convergência Técnica Quantitativa",
        "short_def": "Conjunto de indicadores matemáticos usados para gerar sinais de compra e venda.",
        "details": "Combina médias móveis exponenciais (EMA 9, 21, 50), RSI 14, MACD, Bandas de Bollinger, ATR, Suportes/Resistências e Volume relativo para validar entradas de alta probabilidade."
    },
    {
        "id": "circuit-breaker",
        "category": "Risco & Proteção",
        "term": "Circuit Breaker Diário",
        "short_def": "Trava de segurança por ambiente que suspende novas compras em dias de queda expressiva.",
        "details": "Se o patrimônio operacional do dia recuar além do limite diário (padrão: -3,0%), o motor bloqueia novas compras naquele ambiente pelo restante do pregão, mantendo ativa a proteção de Stop-Loss."
    },
    {
        "id": "comandos-telegram",
        "category": "Notificações & Controle",
        "term": "Comandos do Telegram Bot",
        "short_def": "Comandos interativos para consultar e controlar o Córtex diretamente pelo Telegram.",
        "details": (
            "• `/status` — Visão geral da conta, patrimônio, teto da IA e custódia.\n"
            "• `/carteira` — Lista todas as ações abertas, cotação, stop-loss e P&L.\n"
            "• `/saldo` — Detalhamento de caixa livre, alocado, teto da IA, cofre e poder de compra.\n"
            "• `/trades` — Histórico das últimas operações do ambiente ativo.\n"
            "• `/pensamentos` — Últimas deliberações quantitativas do Cérebro IA.\n"
            "• `/desempenho` — Taxa de acerto (Win Rate), operações e drawdown máximo.\n"
            "• `/mercado` — Horário BRT e status do pregão da B3.\n"
            "• `/pause` — Pausa ordens reais alternando para Homologação.\n"
            "• `/resume` — Reativa o modo Produção (requer MT5 conectado).\n"
            "• `/fechartudo` — Liquidação emergencial de todas as posições abertas.\n"
            "• `/help` — Exibe a lista completa de comandos."
        )
    }
]


def _matches_any(query: str, keywords: List[str]) -> bool:
    """Verifica correspondência usando fronteira de palavra (`\\b`) para termos curtos (<= 3 letras)."""
    for kw in keywords:
        if len(kw) <= 3:
            if re.search(rf"\b{re.escape(kw)}\b", query):
                return True
        elif kw in query:
            return True
    return False


class AssistantKnowledgeBase:
    @staticmethod
    def get_all_glossary() -> List[Dict[str, Any]]:
        return GLOSSARY_DATABASE

    @staticmethod
    def answer_question(user_query: str) -> Dict[str, Any]:
        q = user_query.lower().strip()

        if _matches_any(q, ["trailing", "stop móvel", "stop movel", "perseguir", "travar lucro"]):
            return {
                "answer": (
                    "🎯 **Trailing Stop (Stop Móvel) & Realização Parcial:**\n\n"
                    "O Trailing Stop protege seus ganhos automaticamente conforme a ação sobe:\n\n"
                    "1. **Gatilho de Ativação:** Quando a posição atinge o lucro configurado (padrão: **`+2,0%`**, ajustável no Dashboard), o Stop-Loss passa a acompanhar a alta.\n"
                    "2. **Distância de Segurança:** Mantém um recuo permitido abaixo do topo recente (padrão: **`1,5%`**, sempre menor que o gatilho).\n"
                    "3. **Take-Profit Parcial (`+15%`):** Em posições com 2 ou mais ações, ao atingir **`+15,0%`** o Córtex vende 50% para garantir lucro no bolso e protege o restante no mínimo no breakeven!"
                ),
                "suggestions": ["O que é Stop Loss Inicial?", "Como funciona o Take-Profit Parcial?", "O que é Auto-compounding?"]
            }

        if _matches_any(q, ["take profit", "take-profit", "realização parcial", "realizacao parcial", "breakeven"]):
            return {
                "answer": (
                    "🎯 **Take-Profit Parcial (+15%):**\n\n"
                    "• Quando uma posição com **2 ou mais ações** alcança **`+15,0%`** de lucro, o Córtex vende automaticamente metade das ações.\n"
                    "• O Stop-Loss da metade restante é elevado para `max(stop_atual, preço_de_entrada)` (nunca reduzindo um Trailing Stop já mais alto).\n"
                    "• Posições de apenas **1 ação** não sofrem saída parcial para não encerrar 100% da posição prematuramente, permanecendo protegidas pelo Trailing Stop."
                ),
                "suggestions": ["Como funciona o Trailing Stop?", "O que é Stop Loss Inicial?", "Comandos do Telegram"]
            }

        if _matches_any(q, ["stop loss", "stop-loss", "stop inicial", "limite de perda", "atr"]):
            return {
                "answer": (
                    "🛡️ **Stop-Loss Inicial & Adaptativo (ATR):**\n\n"
                    "É a linha prioritária de defesa do patrimônio na entrada de cada compra:\n\n"
                    "• **Limite Percentual:** Padrão de **`10,0%`** abaixo da entrada (configurável em tempo real no Dashboard).\n"
                    "• **Ajuste por Volatilidade (ATR):** Quando o ATR está disponível, posiciona o stop em `entrada - 1,5 × ATR` respeitando distância mínima de segurança e o teto máximo de perda, arredondado ao centavo da B3 (`R$ 0,01`).\n"
                    "• **Execução Imediata:** Verificado no início de cada ciclo antes de qualquer nova compra."
                ),
                "suggestions": ["Como funciona o Trailing Stop?", "O que é Circuit Breaker?", "O que é o Cofre Reserva?"]
            }

        if _matches_any(q, ["saque", "retirar", "resgate", "meta de lucro", "foco"]):
            return {
                "answer": (
                    "💰 **Foco de Saque Automático (Exclusivo da Produção):**\n\n"
                    "Você pode transferir recursos para o **Cofre Reserva** de duas maneiras:\n\n"
                    "1. **⚡ Retirar AGORA (Instantâneo):** Utiliza o caixa livre desbloqueado; se necessário, vende parcialmente as posições mais lucrativas e transfere o valor ao Cofre.\n"
                    "2. **📈 Retirar por Meta de Lucro:** Monitora o patrimônio operacional na Produção e, ao atingir a meta definida, executa a liquidação inteligente e protege o lucro no Cofre.\n\n"
                    "🔒 *Em modo Homologação, saques reais ficam bloqueados para total isolamento de ambiente.*"
                ),
                "suggestions": ["O que é o Cofre Reserva?", "O que é Liquidação Inteligente?", "Qual a diferença de Homologação e Produção?"]
            }

        if _matches_any(q, ["cofre", "reserva", "blindado", "bloqueado"]):
            return {
                "answer": (
                    "🔒 **Cofre Reserva Protegido:**\n\n"
                    "O Cofre Reserva é a parcela do caixa na corretora que a IA **não pode utilizar**:\n\n"
                    "• **Blindagem Total:** Deduzido integralmente do cálculo de Poder de Compra.\n"
                    "• **Contabilidade Fiel:** O saldo do Cofre não infla artificialmente o P&L percentual da carteira.\n"
                    "• **Alimentação:** Pelo ajuste do Teto de Capital da IA ou pelo Foco de Saque."
                ),
                "suggestions": ["Como funciona o Foco de Saque?", "O que é Teto de Capital?", "O que é Auto-compounding?"]
            }

        if _matches_any(q, ["juros compostos", "auto-compounding", "compounding", "reinvestimento", "aumenta o capital"]):
            return {
                "answer": (
                    "📈 **Auto-Compounding (Juros Compostos):**\n\n"
                    "• Sempre que uma venda realiza lucro líquido, esse ganho é somado automaticamente ao **Teto Operacional da IA** e salvo no banco de dados.\n"
                    "• Assim, nos próximos ciclos o robô dispõe do capital inicial somado aos lucros acumulados, potencializando os retornos de forma autônoma."
                ),
                "suggestions": ["O que é Teto de Capital?", "Como funciona o Trailing Stop?", "Como programar um Saque?"]
            }

        if _matches_any(q, ["homologação", "homologacao", "simulador", "produção", "producao", "diferença", "ambiente"]):
            return {
                "answer": (
                    "🔄 **Diferença entre Homologação e Produção:**\n\n"
                    "• **🟡 HOMOLOGAÇÃO (Simulador / Paper Trading):**\n"
                    "  - Opera com saldo virtual isolado e cotações reais da B3.\n"
                    "  - Ideal para calibrar risco e validar setups sem expor capital.\n"
                    "  - Possui opção de **Reset de Simulação**.\n\n"
                    "• **🟢 PRODUÇÃO:**\n"
                    "  - Envia ordens reais exclusivamente via **MetaTrader 5 / Bridge** (nunca usa simulador oculto).\n"
                    "  - Caso o MT5 desconecte ou o servidor reinicie após falha inesperada, o fail-safe retorna automaticamente para **Homologação** e alerta via Telegram."
                ),
                "suggestions": ["Comandos do Telegram", "O que é Foco de Saque?", "O que é o Cofre Reserva?"]
            }

        if _matches_any(q, ["telegram", "comandos", "notificação", "notificacao", "alerta", "bot telegram"]):
            return {
                "answer": (
                    "📱 **Comandos do Bot no Telegram:**\n\n"
                    "• `/status` — Resumo do patrimônio, caixa, teto da IA e posições.\n"
                    "• `/carteira` — Ações em custódia, preço médio, cotação, SL e P&L.\n"
                    "• `/saldo` — Detalhamento completo de caixa, teto da IA, cofre e poder de compra.\n"
                    "• `/trades` — Últimas operações executadas no ambiente ativo.\n"
                    "• `/pensamentos` — Últimas análises e justificativas do Cérebro IA.\n"
                    "• `/desempenho` — Taxa de acerto (Win Rate), total de trades e drawdown.\n"
                    "• `/mercado` — Horário BRT e estado atual do pregão da B3.\n"
                    "• `/pause` — Alterna imediatamente para Homologação (pausa ordens reais).\n"
                    "• `/resume` — Reativa o ambiente de Produção (se MT5 estiver conectado).\n"
                    "• `/fechartudo` — Liquidação emergencial de toda a custódia.\n"
                    "• `/help` — Exibe o menu de comandos."
                ),
                "suggestions": ["O que são Indicadores Técnicos?", "O que é Sentimento NLP?", "Como funciona o Trailing Stop?"]
            }

        if _matches_any(q, ["sentimento", "notícia", "noticia", "nlp", "finbert", "ia", "cérebro", "cerebro", "inteligência", "inteligencia"]):
            return {
                "answer": (
                    "🧠 **Cérebro IA & Análise de Notícias (FinBERT):**\n\n"
                    "O Córtex combina **Análise Técnica Quantitativa** com **Processamento de Linguagem Natural**:\n\n"
                    "1. **Coleta de Notícias:** Monitora manchetes de InfoMoney, Valor Econômico e Google News.\n"
                    "2. **Score de Sentimento (`-1.0` a `+1.0`):** Classifica o noticiário corporativo de cada ticker.\n"
                    "3. **Convergência:** Novas compras exigem alinhamento técnico altista e ausência de sentimento hostil."
                ),
                "suggestions": ["Como funciona o Stop-Loss?", "O que é Trailing Stop?", "Comandos do Telegram"]
            }

        return {
            "answer": (
                "🤖 **Assistente Córtex IA:**\n\n"
                "O **Córtex IA** é um sistema quantitativo autônomo para operações na B3.\n\n"
                "Você pode perguntar sobre:\n"
                "• **Gestão de Risco:** *Stop-Loss (10% + ATR), Trailing Stop (Gatilho 2% / Distância 1,5%), Take-Profit Parcial (+15%) e Circuit Breaker.*\n"
                "• **Gestão de Capital:** *Teto Operacional da IA, Cofre Reserva, Foco de Saque e Auto-Compounding.*\n"
                "• **Ambientes:** *Homologação vs Produção e Fail-Safe automático.*\n"
                "• **Controle:** *Convergência Técnica, Sentimento NLP e Comandos do Telegram (`/status`, `/saldo`, `/carteira`, `/pause`, `/fechartudo`, etc.).*"
            ),
            "suggestions": [
                "Como funciona o Trailing Stop?",
                "Qual a diferença de Homologação e Produção?",
                "O que é o Cofre Reserva?",
                "Comandos do Telegram",
            ]
        }
