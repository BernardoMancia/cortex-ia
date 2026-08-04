"""
Gerenciador de risco do Projeto Córtex.

Responsável por cálculo de stop-loss, validação de ordens,
dimensionamento de posições e verificação de gatilhos de risco.
Este é o componente de MAIOR PRIORIDADE — executa antes de qualquer análise.

Funcionalidades:
- Stop-loss fixo na entrada
- Trailing stop-loss (move SL para cima quando posição lucra > 5%)
- Limite de concentração (máx 30% do portfólio por ativo)
- Circuit breaker de perda diária (suspende operações se P&L < -5%)
- Dimensionamento por confiança (alta confiança = mais ações)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from config .settings import settings
from models .data_models import BRT ,Position

logger =logging .getLogger ('cortex.risk_manager')

class RiskManager :
    """Gerenciador de risco para operações no mercado fracionário da B3."""

    MIN_SHARES :int =1
    MAX_SHARES :int =99

    MAX_CONCENTRATION :float =0.30
    DAILY_LOSS_LIMIT :float =-0.05
    TRAILING_STOP_ACTIVATION :float =0.05

    def __init__ (self ,stop_loss_percent :float =settings .STOP_LOSS_PERCENT )->None :
        """
        Inicializa o gerenciador de risco.

        Args:
            stop_loss_percent: Percentual de perda máxima tolerada (0.10 = 10%).
        """
        self .stop_loss_percent =stop_loss_percent
        self ._daily_pnl :float =0.0
        self ._circuit_breaker_active :bool =False
        self ._last_reset_date :Optional [str ]=None
        logger .info (
        'RiskManager inicializado — stop-loss: %.1f%%, concentração max: %.0f%%, '
        'circuit breaker: %.1f%%',
        self .stop_loss_percent *100 ,
        self .MAX_CONCENTRATION *100 ,
        self .DAILY_LOSS_LIMIT *100 ,
        )

    def calculate_stop_loss (self ,entry_price :float )->float :
        """
        Calcula o preço de stop-loss para uma entrada.

        P_stop = entry_price * (1 - settings.STOP_LOSS_PERCENT)

        Args:
            entry_price: Preço de entrada da posição.

        Returns:
            Preço de stop-loss calculado.
        """
        stop_price =entry_price *(1.0 -self .stop_loss_percent )
        logger .debug (
        'Stop-loss calculado: entrada R$ %.2f → stop R$ %.2f (%.1f%%)',
        entry_price ,stop_price ,self .stop_loss_percent *100 ,
        )
        return stop_price

    def update_trailing_stop (self ,position :Position )->bool :
        """
        Atualiza stop-loss com trailing stop quando posição tem lucro.

        Quando o preço atual sobe mais de TRAILING_STOP_ACTIVATION (5%) acima
        da entrada, o stop-loss é movido para cima para proteger lucros.

        O novo stop = max(stop_atual, preço_atual * (1 - stop_loss_percent))

        Args:
            position: Posição a avaliar.

        Returns:
            True se o stop-loss foi atualizado, False caso contrário.
        """
        if position .current_price is None or position .current_price <=0 :
            return False

        gain_pct =(position .current_price -position .entry_price )/position .entry_price

        if gain_pct <self .TRAILING_STOP_ACTIVATION :
            return False

        new_stop =position .current_price *(1.0 -self .stop_loss_percent )

        if new_stop >position .stop_loss :
            old_stop =position .stop_loss
            position .stop_loss =round (new_stop ,2 )
            logger .info (
            '📈 TRAILING STOP atualizado: %s — R$ %.2f → R$ %.2f '
            '(preço atual: R$ %.2f, ganho: +%.1f%%)',
            position .ticker ,old_stop ,position .stop_loss ,
            position .current_price ,gain_pct *100 ,
            )
            return True

        return False

    def check_stop_loss_triggers (
    self ,
    positions :list [Position ],
    market_data :object ,
    )->list [Position ]:
        """
        Verifica gatilhos de stop-loss para todas as posições.

        Esta é a verificação de MAIOR PRIORIDADE — executa antes de
        qualquer análise técnica ou de sentimento.

        Para cada posição, obtém o preço atual. Se o preço atual
        for menor ou igual ao stop-loss, a posição é adicionada
        à lista de gatilhos ativados. Também atualiza trailing stops.

        Args:
            positions: Lista de posições abertas.
            market_data: Instância do provedor de dados de mercado.

        Returns:
            Lista de posições cujo stop-loss foi ativado.
        """
        triggered :list [Position ]=[]

        for position in positions :
            try :
                price_data =getattr (market_data ,'get_current_price',lambda t :None )(
                position .ticker
                )

                if isinstance (price_data ,dict ):
                    current_price =price_data .get ('last')
                else :
                    current_price =price_data

                if current_price is None :

                    current_price =position .current_price

                if current_price is None or current_price <=0 :
                    logger .warning (
                    'Preço indisponível para %s — não é possível verificar stop-loss',
                    position .ticker ,
                    )
                    continue

                position .current_price =current_price

                self .update_trailing_stop (position )

                if current_price <=position .stop_loss :
                    triggered .append (position )
                    logger .warning (
                    '🚨 STOP-LOSS ATIVADO: %s — preço R$ %.2f <= SL R$ %.2f '
                    '(entrada R$ %.2f, perda %.1f%%)',
                    position .ticker ,
                    current_price ,
                    position .stop_loss ,
                    position .entry_price ,
                    ((current_price -position .entry_price )/position .entry_price )*100 ,
                    )
                else :
                    distance_pct =(
                    (current_price -position .stop_loss )/position .stop_loss
                    )*100
                    logger .debug (
                    'Stop-loss OK: %s — preço R$ %.2f, SL R$ %.2f (distância: +%.1f%%)',
                    position .ticker ,
                    current_price ,
                    position .stop_loss ,
                    distance_pct ,
                    )

            except Exception as e :
                logger .error (
                'Erro ao verificar stop-loss de %s: %s',
                position .ticker ,e ,
                )

        if triggered :
            logger .warning (
            '⚠️ %d posição(ões) com stop-loss ativado: %s',
            len (triggered ),
            ', '.join (p .ticker for p in triggered ),
            )
        else :
            logger .debug (
            'Verificação de stop-loss concluída — %d posições OK',
            len (positions ),
            )

        return triggered

    def validate_order (
    self ,
    ticker :str ,
    quantity :int ,
    price :float ,
    available_capital :float ,
    total_portfolio_value :float =0.0 ,
    )->tuple [bool ,str ]:
        """
        Valida uma ordem antes da execução.

        Verificações realizadas:
        1. Circuit breaker não está ativo.
        2. Quantidade dentro do intervalo [1, 99] (mercado fracionário).
        3. Custo total (price * quantity) dentro do capital disponível.
        4. Concentração máxima por ativo não excede MAX_CONCENTRATION.

        Args:
            ticker: Código do ativo.
            quantity: Quantidade de ações.
            price: Preço unitário.
            available_capital: Capital disponível para a operação.
            total_portfolio_value: Valor total do portfólio (para concentração).

        Returns:
            Tupla (is_valid, reason_if_invalid).
            Se válida: (True, 'Ordem válida').
            Se inválida: (False, 'motivo da rejeição').
        """

        if self ._circuit_breaker_active :
            reason =(
            f'Circuit breaker ativo — operações suspensas '
            f'(perda diária: {self ._daily_pnl *100 :.1f}%)'
            )
            logger .warning ('Ordem rejeitada para %s: %s',ticker ,reason )
            return False ,reason

        if quantity <self .MIN_SHARES :
            reason =(
            f'Quantidade inválida: {quantity } < {self .MIN_SHARES } '
            f'(mínimo para mercado fracionário)'
            )
            logger .warning ('Ordem rejeitada para %s: %s',ticker ,reason )
            return False ,reason

        if quantity >self .MAX_SHARES :
            reason =(
            f'Quantidade inválida: {quantity } > {self .MAX_SHARES } '
            f'(máximo para mercado fracionário)'
            )
            logger .warning ('Ordem rejeitada para %s: %s',ticker ,reason )
            return False ,reason

        total_cost =price *quantity
        if total_cost >available_capital :
            reason =(
            f'Capital insuficiente: R$ {total_cost :.2f} necessário, '
            f'R$ {available_capital :.2f} disponível'
            )
            logger .warning ('Ordem rejeitada para %s: %s',ticker ,reason )
            return False ,reason

        if total_portfolio_value >0 :
            concentration =total_cost /total_portfolio_value
            if concentration >self .MAX_CONCENTRATION :
                reason =(
                f'Concentração excessiva: {concentration *100 :.1f}% > '
                f'{self .MAX_CONCENTRATION *100 :.0f}% máximo permitido'
                )
                logger .warning ('Ordem rejeitada para %s: %s',ticker ,reason )
                return False ,reason

        logger .info (
        'Ordem validada: %s — %d ações @ R$ %.2f = R$ %.2f '
        '(capital disponível: R$ %.2f)',
        ticker ,quantity ,price ,total_cost ,available_capital ,
        )
        return True ,'Ordem válida'

    def get_max_shares (
    self ,
    price :float ,
    available_capital :float ,
    confidence :float =1.0 ,
    total_portfolio_value :float =0.0 ,
    )->int :
        """
        Calcula a quantidade máxima de ações que podem ser compradas.

        Leva em conta:
        - Capital disponível
        - Concentração máxima por ativo
        - Confiança da decisão (modula tamanho: alta confiança = mais ações)
        - Limite do mercado fracionário (1-99)

        Args:
            price: Preço unitário da ação.
            available_capital: Capital disponível.
            confidence: Nível de confiança da decisão (0.0 a 1.0).
            total_portfolio_value: Valor total do portfólio.

        Returns:
            Quantidade máxima de ações (0 se insuficiente, máx 99).
        """
        if price <=0 or available_capital <=0 :
            logger .debug (
            'Cálculo de max_shares: preço=%.2f, capital=%.2f → 0',
            price ,available_capital ,
            )
            return 0

        max_by_capital =int (available_capital /price )

        if total_portfolio_value >0 :
            max_by_concentration =int (
            (total_portfolio_value *self .MAX_CONCENTRATION )/price
            )
            max_shares =min (max_by_capital ,max_by_concentration )
        else :
            max_shares =max_by_capital

        if confidence <0.5 :
            confidence_factor =0.5
        elif confidence <0.7 :
            confidence_factor =0.7
        else :
            confidence_factor =1.0

        max_shares =int (max_shares *confidence_factor )

        result =max (0 ,min (max_shares ,self .MAX_SHARES ))

        logger .debug (
        'Max shares: preço R$ %.2f, capital R$ %.2f, confiança %.0f%% → %d ações '
        '(custo total: R$ %.2f)',
        price ,available_capital ,confidence *100 ,result ,price *result ,
        )
        return result

    def update_daily_pnl (self ,pnl_percent :float )->None :
        """
        Atualiza o P&L diário e verifica circuit breaker.

        Args:
            pnl_percent: Variação percentual do portfólio no dia (ex: -0.03 = -3%).
        """
        today =datetime .now (BRT ).strftime ('%Y-%m-%d')
        if self ._last_reset_date !=today :
            self ._daily_pnl =0.0
            self ._circuit_breaker_active =False
            self ._last_reset_date =today

        self ._daily_pnl =pnl_percent

        if pnl_percent <=self .DAILY_LOSS_LIMIT and not self ._circuit_breaker_active :
            self ._circuit_breaker_active =True
            logger .warning (
            '🛑 CIRCUIT BREAKER ATIVADO — Perda diária de %.1f%% '
            'excede limite de %.1f%%. Operações suspensas até amanhã.',
            pnl_percent *100 ,
            self .DAILY_LOSS_LIMIT *100 ,
            )

    def reset_daily_limits (self )->None :
        """Reseta limites diários (chamado no início de cada dia)."""
        self ._daily_pnl =0.0
        self ._circuit_breaker_active =False
        self ._last_reset_date =datetime .now (BRT ).strftime ('%Y-%m-%d')
        logger .debug ('Limites diários de risco resetados')

    @property
    def is_circuit_breaker_active (self )->bool :
        """Retorna se o circuit breaker está ativo."""
        return self ._circuit_breaker_active
