"""
Módulo base do broker — classes de domínio e contrato abstrato.

Define os tipos fundamentais (Order, Position, OrderType, OrderStatus)
e a interface abstrata BrokerBase que todas as implementações de corretora
devem seguir.
"""

from __future__ import annotations

import logging
from abc import ABC ,abstractmethod
from dataclasses import dataclass ,field
from datetime import datetime
from enum import Enum
from typing import Optional

from zoneinfo import ZoneInfo

logger =logging .getLogger ("cortex.broker")

BRT :ZoneInfo =ZoneInfo ("America/Sao_Paulo")

class OrderType (Enum ):
    """Tipo de ordem: compra ou venda."""

    BUY ="BUY"
    SELL ="SELL"

class OrderStatus (Enum ):
    """Status de uma ordem enviada ao mercado."""

    PENDING ="PENDING"
    FILLED ="FILLED"
    REJECTED ="REJECTED"
    CANCELLED ="CANCELLED"

@dataclass
class Order :
    """
    Representa uma ordem de compra ou venda.

    Attributes:
        ticker: Código do ativo (ex.: PETR4F).
        order_type: Tipo da ordem (BUY / SELL).
        quantity: Quantidade de ações.
        price: Preço unitário da ordem.
        stop_loss: Preço de stop-loss (opcional).
        status: Status atual da ordem.
        ticket: Número de ticket atribuído pela corretora.
        timestamp: Data/hora de criação (timezone-aware, BRT).
        comment: Comentário descritivo sobre a ordem.
    """

    ticker :str
    order_type :OrderType
    quantity :int
    price :float
    stop_loss :Optional [float ]=None
    status :OrderStatus =OrderStatus .PENDING
    ticket :Optional [int ]=None
    timestamp :datetime =field (default_factory =lambda :datetime .now (tz =BRT ))
    comment :str =""

    def __post_init__ (self )->None :
        """Valida campos básicos e normaliza ticker."""
        if self .quantity <0 :
            raise ValueError (f"Quantidade inválida: {self .quantity }. Deve ser >= 0.")
        if self .price <0 :
            raise ValueError (f"Preço inválido: {self .price }. Deve ser >= 0.")
        self .ticker =self .ticker .upper ().strip ()

    @property
    def total_value (self )->float :
        """Valor total da ordem (quantidade × preço)."""
        return round (self .quantity *self .price ,2 )

    def to_dict (self )->dict :
        """Serializa a ordem para dicionário."""
        return {
        "ticker":self .ticker ,
        "order_type":self .order_type .value ,
        "quantity":self .quantity ,
        "price":self .price ,
        "stop_loss":self .stop_loss ,
        "status":self .status .value ,
        "ticket":self .ticket ,
        "timestamp":self .timestamp .isoformat (),
        "comment":self .comment ,
        }

@dataclass
class Position :
    """
    Representa uma posição aberta no portfólio.

    Attributes:
        ticker: Código do ativo.
        quantity: Quantidade de ações na posição.
        entry_price: Preço médio de entrada.
        current_price: Preço de mercado atual.
        stop_loss: Preço de stop-loss configurado.
        ticket: Ticket da ordem que abriu a posição.
        timestamp: Data/hora de abertura (timezone-aware, BRT).
    """

    ticker :str
    quantity :int
    entry_price :float
    current_price :float
    stop_loss :float
    ticket :int
    timestamp :datetime

    def __post_init__ (self )->None :
        """Normaliza ticker e valida campos."""
        self .ticker =self .ticker .upper ().strip ()
        if self .quantity <=0 :
            raise ValueError (f"Quantidade da posição inválida: {self .quantity }")
        if self .entry_price <=0 :
            raise ValueError (f"Preço de entrada inválido: {self .entry_price }")

    @property
    def pnl (self )->float :
        """Lucro ou prejuízo absoluto (em R$)."""
        return round ((self .current_price -self .entry_price )*self .quantity ,2 )

    @property
    def pnl_percent (self )->float :
        """Lucro ou prejuízo percentual em relação ao preço de entrada."""
        if self .entry_price ==0 :
            return 0.0
        return round (((self .current_price -self .entry_price )/self .entry_price )*100 ,2 )

    @property
    def total_value (self )->float :
        """Valor de mercado atual da posição (quantidade × preço atual)."""
        return round (self .current_price *self .quantity ,2 )

    @property
    def invested_value (self )->float :
        """Valor investido na entrada da posição."""
        return round (self .entry_price *self .quantity ,2 )

    def to_dict (self )->dict :
        """Serializa a posição para dicionário."""
        return {
        "ticker":self .ticker ,
        "quantity":self .quantity ,
        "entry_price":self .entry_price ,
        "current_price":self .current_price ,
        "stop_loss":self .stop_loss ,
        "ticket":self .ticket ,
        "timestamp":self .timestamp .isoformat (),
        "pnl":self .pnl ,
        "pnl_percent":self .pnl_percent ,
        "total_value":self .total_value ,
        }

class BrokerBase (ABC ):
    """
    Contrato abstrato que toda implementação de corretora deve seguir.

    Define as operações fundamentais: conectar, desconectar, consultar
    saldo, enviar ordens de compra/venda, gerenciar posições e modificar
    stop-loss.
    """

    @abstractmethod
    def connect (self )->bool :
        """
        Estabelece conexão com a corretora.

        Returns:
            True se a conexão foi bem-sucedida, False caso contrário.
        """
        ...

    @abstractmethod
    def disconnect (self )->None :
        """Encerra a conexão com a corretora."""
        ...

    @abstractmethod
    def get_balance (self )->float :
        """
        Retorna o saldo em caixa disponível para operações.

        Returns:
            Saldo em R$.
        """
        ...

    @abstractmethod
    def get_equity (self )->float :
        """
        Retorna o patrimônio total (saldo + valor das posições abertas).

        Returns:
            Patrimônio total em R$.
        """
        ...

    @abstractmethod
    def buy (self ,ticker :str ,quantity :int ,price :float ,stop_loss :float )->Order :
        """
        Envia ordem de compra.

        Args:
            ticker: Código do ativo.
            quantity: Quantidade de ações (1–99 para fracionário).
            price: Preço unitário desejado.
            stop_loss: Preço de stop-loss.

        Returns:
            Objeto Order com status da execução.
        """
        ...

    @abstractmethod
    def sell (self ,ticker :str ,quantity :int ,price :float )->Order :
        """
        Envia ordem de venda.

        Args:
            ticker: Código do ativo.
            quantity: Quantidade de ações a vender.
            price: Preço unitário desejado.

        Returns:
            Objeto Order com status da execução.
        """
        ...

    @abstractmethod
    def emergency_sell (self ,ticker :str )->Order :
        """
        Venda de emergência — liquida toda a posição de um ativo a preço de mercado.

        Args:
            ticker: Código do ativo a liquidar.

        Returns:
            Objeto Order com status da execução.
        """
        ...

    @abstractmethod
    def get_positions (self )->list [Position ]:
        """
        Retorna todas as posições abertas.

        Returns:
            Lista de objetos Position.
        """
        ...

    @abstractmethod
    def get_position (self ,ticker :str )->Optional [Position ]:
        """
        Retorna a posição de um ativo específico.

        Args:
            ticker: Código do ativo.

        Returns:
            Position se existir, None caso contrário.
        """
        ...

    @abstractmethod
    def modify_stop_loss (self ,ticker :str ,new_sl :float )->bool :
        """
        Modifica o stop-loss de uma posição aberta.

        Args:
            ticker: Código do ativo.
            new_sl: Novo preço de stop-loss.

        Returns:
            True se modificado com sucesso, False caso contrário.
        """
        ...

BaseBroker =BrokerBase
