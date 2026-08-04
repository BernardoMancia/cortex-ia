"""
Gerenciador de portfólio do Projeto Córtex.

Mantém o estado do portfólio, posições abertas e
calcula métricas de desempenho.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from config .settings import settings
from models .data_models import Position ,PortfolioSummary

logger =logging .getLogger ('cortex.portfolio')

class Portfolio :
    """Gerenciador de portfólio do sistema de trading."""

    def __init__ (self ,initial_capital :float =settings .CAPITAL_INICIAL )->None :
        """
        Inicializa o portfólio.

        Args:
            initial_capital: Capital inicial disponível.
        """
        self ._lock =threading .RLock ()
        self .initial_capital =initial_capital
        self .free_cash =initial_capital
        self ._positions :list [Position ]=[]
        logger .info ('Portfolio inicializado — capital: R$ %.2f',initial_capital )

    @property
    def positions (self )->list [Position ]:
        """Retorna posições abertas."""
        with self ._lock :
            return list (self ._positions )

    @property
    def allocated_capital (self )->float :
        """Capital alocado em posições."""
        with self ._lock :
            return sum (p .total_cost for p in self ._positions )

    @property
    def total_value (self )->float :
        """Valor total do portfólio (caixa + posições a mercado)."""
        with self ._lock :
            positions_value =sum (p .current_value for p in self ._positions )
            return self .free_cash +positions_value

    @property
    def total_pnl (self )->float :
        """Lucro/prejuízo total."""
        with self ._lock :
            return self .total_value -self .initial_capital

    @property
    def total_pnl_percent (self )->float :
        """Lucro/prejuízo percentual."""
        with self ._lock :
            if self .initial_capital ==0 :
                return 0.0
            return (self .total_pnl /self .initial_capital )*100.0

    def add_position (self ,position :Position )->None :
        """Adiciona posição ao portfólio."""
        with self ._lock :
            self ._positions .append (position )
            self .free_cash -=position .total_cost
        logger .info (
        'Posição adicionada: %s — %d ações @ R$ %.2f',
        position .ticker ,position .quantity ,position .entry_price ,
        )

    def remove_position (self ,ticker :str ,sell_price :float )->Optional [Position ]:
        """
        Remove posição do portfólio (venda).

        Args:
            ticker: Código do ativo.
            sell_price: Preço de venda.

        Returns:
            Posição removida ou None.
        """
        with self ._lock :
            for i ,pos in enumerate (self ._positions ):
                if pos .ticker ==ticker :
                    removed =self ._positions .pop (i )
                    self .free_cash +=sell_price *removed .quantity
                    logger .info (
                    'Posição removida: %s — R$ %.2f → R$ %.2f',
                    ticker ,removed .entry_price ,sell_price ,
                    )
                    return removed
        return None

    def update_prices (self ,prices :dict [str ,Optional [float ]])->None :
        """Atualiza preços atuais das posições."""
        with self ._lock :
            for pos in self ._positions :
                price =prices .get (pos .ticker )
                if price is not None :
                    pos .current_price =price

    def get_summary (self )->PortfolioSummary :
        """Retorna resumo consolidado do portfólio."""
        with self ._lock :
            return PortfolioSummary (
            total_value =self .total_value ,
            free_cash =self .free_cash ,
            allocated_capital =self .allocated_capital ,
            positions =list (self ._positions ),
            total_pnl =self .total_pnl ,
            total_pnl_percent =self .total_pnl_percent ,
            num_positions =len (self ._positions ),
            simulation_mode =settings .SIMULATION_MODE ,
            )

    def find_position (self ,ticker :str )->Optional [Position ]:
        """Encontra posição aberta de um ativo."""
        with self ._lock :
            for pos in self ._positions :
                if pos .ticker ==ticker :
                    return pos
        return None

    def get_position (self ,ticker :str )->Optional [Position ]:
        """Alias para find_position — compatibilidade com PortfolioProtocol."""
        return self .find_position (ticker )

    def get_all_positions (self )->list [Position ]:
        """Retorna todas as posições abertas — compatibilidade com PortfolioProtocol."""
        with self ._lock :
            return list (self ._positions )
