from __future__ import annotations

from typing import Any, Final

from config.settings import settings

GUIDE_SECTIONS: Final[list[dict[str, Any]]] = [
    {
        "id": "arquitetura-multi-carteira",
        "title": "Arquitetura Multi-Carteira",
        "category": "Governança e Isolamento",
        "summary": (
            "Cada usuário pode operar múltiplas carteiras segregadas com conectores, "
            "limites de risco, saldos e trilhas de auditoria completamente independentes."
        ),
        "content": (
            "O Cortex-IA separa a inteligência de mercado (executada uma única vez por ciclo no "
            "Market Intelligence Hub) da execução individual por carteira. Cada carteira possui "
            "modo operacional próprio (ACTIVE, OBSERVE_ONLY, PAUSED_UNCONFIGURED, PAUSED_BROKER_ERROR "
            "ou PANIC), credenciais criptografadas de forma isolada e controle individual de posições "
            "manuais marcadas como protegidas (do_not_touch)."
        ),
        "bullets": [
            "Isolamento estrito de custódia, caixa livre, capital alocado à IA e histórico fiscal por carteira.",
            "Hub compartilhado coleta cotações, indicadores técnicos e sentimento sem duplicar requisições externas.",
            "Posições marcadas com do_not_touch jamais são vendidas ou alteradas automaticamente pelo motor.",
        ],
    },
    {
        "id": "lote-padrao-vs-fracionario",
        "title": "Lote Padrão vs. Mercado Fracionário na B3",
        "category": "Microestrutura B3",
        "summary": (
            "Ações à vista na B3 negociam em lotes múltiplos de 100 unidades no mercado padrão "
            "e de 1 a 99 unidades no mercado fracionário (sufixo F)."
        ),
        "content": (
            "Ao calcular quantidades ótimas pelo gerenciamento de risco, ordens acima de 100 ações "
            "com sobra fracionária são automaticamente decompostas em duas pernas: múltiplo inteiro "
            "de 100 no ticker padrão (ex.: PETR4) e o restante de 1 a 99 no ticker fracionário "
            "(ex.: PETR4F). Ativos negociados em lote unitário na B3 — como ETFs (BOVA11, IVVB11), "
            "FIIs e BDRs (NVDC34, AAPL34) — operam diretamente em múltiplos de 1 sem sufixo F, "
            "enquanto Units de ações (como TAEE11, BPAC11 e SANB11) seguem a regra de lote de 100."
        ),
        "bullets": [
            "Lote padrão: múltiplos exatos de 100 ações (ex.: 100, 200, 300 em WEGE3).",
            "Mercado fracionário: quantidades de 1 a 99 ações utilizando sufixo F (ex.: WEGE3F).",
            "ETFs, FIIs e BDRs negociam em lote unitário de 1 unidade sem sufixo fracionário.",
        ],
    },
    {
        "id": "liquidacao-d2",
        "title": "Ciclo de Liquidação Financeira D+2",
        "category": "Operacional B3",
        "summary": (
            "Vendas no mercado à vista da B3 liquidam financeiramente em 2 dias úteis da bolsa (D+2), "
            "excluindo finais de semana e feriados oficiais da B3."
        ),
        "content": (
            "Quando uma posição é encerrada na B3, o valor líquido da alienação entra em trânsito "
            "de liquidação D+2 conforme o calendário oficial de dias úteis da bolsa brasileira. "
            "O controle de liquidação registra separadamente os valores em trânsito para saques "
            "externos e a recomposição de caixa operacional conforme o regime da corretora ou carteira."
        ),
        "bullets": [
            "Cálculo respeita feriados nacionais fixos, móveis (Carnaval, Sexta-Feira Santa, Corpus Christi) e vésperas sem pregão.",
            "Quarta-Feira de Cinzas possui abertura especial às 13:00 BRT até 17:55 BRT.",
            "Agenda D+2 permite distinguir recursos já liquidados de recebíveis em trânsito.",
        ],
    },
    {
        "id": "isencao-mensal-20k",
        "title": "Isenção Mensal PF de R$ 20.000 em Swing Trade",
        "category": "Tributação Brasileira",
        "summary": (
            "Pessoas Físicas contam com isenção de IR sobre ganho líquido em vendas mensais de ações "
            "à vista até R$ 20.000,00 em operações Swing Trade."
        ),
        "content": (
            "Pela legislação tributária brasileira aplicável à Pessoa Física, alienações de ações "
            "e Units de ações no mercado à vista em operações comuns (Swing Trade) cujo somatório "
            "bruto de vendas no mês-calendário não ultrapasse R$ 20.000,00 são isentas de Imposto "
            "de Renda sobre o ganho de capital. Essa isenção NÃO se aplica a operações Day-Trade "
            "(alíquota de 20%), nem a ETFs, FIIs ou BDRs. Quando a proteção fiscal está ativa na "
            "carteira, o sistema monitora uma margem de segurança preventiva (R$ 19.500,00) e evita "
            "realizações parciais no mesmo dia da compra para não descaracterizar Swing Trade."
        ),
        "bullets": [
            "Limite considera o valor bruto total de vendas de ações elegíveis dentro do mês-calendário.",
            "Stop-Loss de proteção patrimonial tem prioridade absoluta mesmo próximo ao teto mensal.",
            "Day-Trade, ETFs, BDRs e FIIs possuem regras tributárias próprias sem isenção de R$ 20 mil.",
        ],
    },
    {
        "id": "cofre-reserva",
        "title": "Cofre Reserva e Segregação de Capital",
        "category": "Gestão de Capital",
        "summary": (
            "O Cofre Reserva blinda parte do caixa ou dos lucros realizados para que o motor "
            "quantitativo não utilize esse montante em novas compras."
        ),
        "content": (
            "Cada carteira divide seus recursos entre o Capital Alocado à IA (disponível para "
            "dimensionamento de posições e exposição de risco) e o Cofre Reserva (vault_reserve_cash). "
            "Valores transferidos ao Cofre Reserva — manualmente ou por metas programadas de "
            "preservação de lucro — permanecem segregados e fora da base de cálculo de compras."
        ),
        "bullets": [
            "Dimensionamento de ordens utiliza apenas o saldo efetivamente alocado à IA.",
            "Metas de retirada podem operar em modo imediato ou condicionadas a atingir determinado patrimônio.",
            "Toda movimentação entre capital operacional e Cofre Reserva fica registrada no razão contábil.",
        ],
    },
    {
        "id": "botao-de-panico",
        "title": "Botão de Pânico e Desligamento de Emergência",
        "category": "Controles Críticos",
        "summary": (
            "Mecanismo de resposta imediata que congela novas entradas e permite encerrar "
            "posições automatizadas em situações extremas de mercado."
        ),
        "content": (
            "O modo PANIC pode ser acionado individualmente em uma carteira ou globalmente pelo "
            "administrador. Ao entrar em pânico, todas as ordens de compra são bloqueadas no ato "
            "e o sistema pode comandar o zeramento das posições geridas pela IA durante o pregão, "
            "preservando intactos os ativos marcados manualmente como do_not_touch."
        ),
        "bullets": [
            "Exige confirmação reforçada (Step-Up) e registra autoria, horário e motivo na auditoria.",
            "Bloqueio permanece ativo até expiração configurada ou reativação deliberada pelo titular.",
            "Respeita rigorosamente posições manuais protegidas pelo usuário.",
        ],
    },
    {
        "id": "modo-manutencao-pos-boot",
        "title": "Modo Manutenção Pós-Boot e Pré-Voo",
        "category": "Confiabilidade Operacional",
        "summary": (
            "Garante que reinicializações inesperadas não enviem ordens antes da validação "
            "de conectividade, relógio, custódia e integridade de auditoria."
        ),
        "content": (
            "Quando configurado para iniciar em Modo Manutenção Pós-Boot, o sistema sobe com a "
            "execução automática bloqueada em estado MAINTENANCE, mantendo apenas coleta passiva "
            "de dados e monitoramento. A entrada em operação requer inspeção do relatório de "
            "Pré-Voo (Pre-Flight Check) — verificando ponte da corretora, sincronização de custódia, "
            "cadeia criptográfica de auditoria e sincronismo de horário — seguida de liberação explícita."
        ),
        "bullets": [
            "Impede ordens órfãs ou duplicadas após quedas de energia, atualizações ou reinícios de serviço.",
            "Relatório de Pré-Voo diagnostica conectividade, saldo da corretora e integridade do banco.",
            "Transição de MAINTENANCE para ACTIVE exige autenticação administrativa verificada.",
        ],
    },
    {
        "id": "seguranca-2fa-step-up",
        "title": "Segurança 2FA (TOTP), Step-Up e Auditoria Imutável",
        "category": "Segurança da Informação",
        "summary": (
            "Proteção em camadas com segundo fator TOTP, elevação temporária de privilégio "
            "para ações sensíveis e log de auditoria encadeado por hash SHA-256."
        ),
        "content": (
            "Além da sessão autenticada com proteção anti-CSRF e limite de inatividade, operações "
            "críticas — como edição de credenciais de corretora, ativação de automação, transferência "
            "de capital, acionamento de pânico e gestão de usuários — exigem verificação Step-Up "
            "de curta duração via senha e código TOTP (ou código de recuperação de uso único). "
            "Cada evento sensível é gravado em tabela imutável com encadeamento criptográfico SHA-256."
        ),
        "bullets": [
            "Segredos de conectores e chaves TOTP são cifrados com derivação vinculada ao usuário e carteira.",
            "Tokens Step-Up expiram rapidamente para mitigar riscos de sessões abertas.",
            "Gatilhos de banco impedem alteração ou exclusão de registros na trilha de auditoria.",
        ],
    },
]

GLOSSARY_TERMS: Final[list[dict[str, str]]] = [
    {
        "term": "ATR (Average True Range)",
        "category": "Análise Técnica",
        "definition": (
            "Medida de volatilidade média diária em reais usada para calibrar a distância "
            "técnica do Stop-Loss e evitar saídas por ruído normal do preço."
        ),
    },
    {
        "term": "ADX (Average Directional Index)",
        "category": "Análise Técnica",
        "definition": (
            "Indicador de 0 a 100 que quantifica a força de uma tendência (leituras acima de 25 "
            "indicam movimento direcional consistente, seja de alta ou de baixa)."
        ),
    },
    {
        "term": "Circuit Breaker Diário",
        "category": "Gestão de Risco",
        "definition": (
            "Trava automática que suspende novas compras na carteira caso a variação diária "
            "atinja o limite máximo de perda diária parametrizado."
        ),
    },
    {
        "term": "Cooldown de Abertura",
        "category": "Microestrutura B3",
        "definition": (
            "Janela inicial após a abertura do pregão da B3 na qual novas compras aguardam "
            "a estabilização dos leilões de abertura e dos spreads."
        ),
    },
    {
        "term": "D+2",
        "category": "Operacional B3",
        "definition": (
            "Prazo padrão da B3 de dois dias úteis após a execução da venda para liquidação "
            "financeira efetiva dos recursos."
        ),
    },
    {
        "term": "do_not_touch (Posição Protegida)",
        "category": "Governança",
        "definition": (
            "Marcação individual por ativo em custódia que proíbe a IA de aumentar, reduzir "
            "ou encerrar aquela posição automaticamente."
        ),
    },
    {
        "term": "IFR / RSI (Índice de Força Relativa)",
        "category": "Análise Técnica",
        "definition": (
            "Oscilador de momento de 14 períodos (suavização de Wilder) que identifica "
            "força compradora, enfraquecimento ou regiões de sobrecompra e sobrevenda."
        ),
    },
    {
        "term": "MTF (Multi-Timeframe Confirmation)",
        "category": "Análise Técnica",
        "definition": (
            "Validação cruzada entre o gráfico diário (D1) e o gráfico intradiário de 60 minutos "
            "para filtrar entradas contra o fluxo de curto prazo."
        ),
    },
    {
        "term": "Stale Quote Guard",
        "category": "Segurança Operacional",
        "definition": (
            "Proteção que bloqueia ordens de compra sempre que a idade da última cotação recebida "
            "ultrapassa o limite máximo permitido durante o pregão aberto."
        ),
    },
    {
        "term": "Step-Up Authentication",
        "category": "Segurança",
        "definition": (
            "Revalidação pontual de identidade (senha + 2FA quando habilitado) exigida antes "
            "de executar comandos críticos de capital, conectores ou modo operacional."
        ),
    },
    {
        "term": "Trailing Stop (Stop Móvel)",
        "category": "Gestão de Risco",
        "definition": (
            "Ajuste dinâmico ascendente do preço de Stop-Loss conforme a cotação atinge novas "
            "máximas acima do gatilho percentual configurado, protegendo ganhos acumulados."
        ),
    },
]


def get_guide_payload() -> dict[str, Any]:
    return {
        "title": "Guia Operacional e Base de Conhecimento — Cortex-IA B3",
        "subtitle": (
            "Referência técnica sobre arquitetura multi-carteira, regras de negociação e "
            "tributação na B3, controles de risco e segurança operacional."
        ),
        "version": settings.VERSION,
        "sections": [dict(sec, bullets=list(sec["bullets"])) for sec in GUIDE_SECTIONS],
        "glossary": [dict(item) for item in GLOSSARY_TERMS],
    }
