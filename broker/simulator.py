"""
Simulador de corretora (paper trading) do Projeto Córtex.

Implementa a interface BrokerBase mantendo estado interno em memória
com persistência em JSON para sobreviver a reinicializações. Totalmente
thread-safe e adequado ao mercado fracionário B3 (1–99 ações).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any ,Optional

from zoneinfo import ZoneInfo

from broker .base import BrokerBase ,Order ,OrderStatus ,OrderType ,Position
from config .settings import settings

logger =logging .getLogger ("cortex.broker.simulator")

BRT :ZoneInfo =ZoneInfo ("America/Sao_Paulo")

class SimulatorBroker (BrokerBase ):
    """
    Corretora simulada para paper trading.

    Mantém saldo e posições em memória com persistência em JSON.
    Valida regras do mercado fracionário B3 (1–99 ações, sufixo 'F').
    Thread-safe via threading.Lock.
    """

    def __init__ (
    self ,
    initial_balance :float =settings .capital_inicial ,
    state_path :Path |None =None ,
    )->None :
        """
        Inicializa o simulador.

        Args:
            initial_balance: Capital inicial em R$.
            state_path: Caminho do arquivo JSON de persistência.
        """
        self ._initial_balance :float =initial_balance
        self ._balance :float =initial_balance
        self ._positions :dict [str ,dict [str ,Any ]]={}
        self ._next_ticket :int =1
        self ._connected :bool =False
        self ._lock :threading .Lock =threading .Lock ()
        self ._state_path :Path =state_path or settings .simulator_state_path
        self ._market_data :Any =None

        logger .info (
        "SimulatorBroker criado — capital inicial: R$%.2f, state: %s",
        self ._initial_balance ,
        self ._state_path ,
        )

    def _get_market_data (self )->Any :
        """Retorna instância de MarketData (lazy import para evitar circular)."""
        if self ._market_data is None :
            from data .market_data import MarketData
            self ._market_data =MarketData ()
        return self ._market_data

    @staticmethod
    def _ensure_fractional_suffix (ticker :str )->str :
        """
        Garante que o ticker possua sufixo 'F' para mercado fracionário.

        Args:
            ticker: Código do ativo (ex.: 'PETR4' ou 'PETR4F').

        Returns:
            Ticker com sufixo 'F' (ex.: 'PETR4F').
        """
        ticker =ticker .upper ().strip ()
        if not ticker .endswith ("F"):
            ticker +="F"
        return ticker

    @staticmethod
    def _validate_quantity (quantity :int )->None :
        """
        Valida que a quantidade esteja dentro do limite fracionário (1–99).

        Raises:
            ValueError: Se a quantidade for inválida.
        """
        if not isinstance (quantity ,int )or quantity <1 or quantity >99 :
            raise ValueError (
            f"Quantidade inválida: {quantity }. "
            f"Mercado fracionário aceita de {settings .min_quantity } a {settings .max_quantity } ações."
            )

    def _generate_ticket (self )->int :
        """Gera próximo número de ticket sequencial (já sob lock)."""
        ticket =self ._next_ticket
        self ._next_ticket +=1
        return ticket

    def _save_state (self )->None :
        """Persiste o estado atual em arquivo JSON (chamado sob lock)."""
        state :dict [str ,Any ]={
        "balance":self ._balance ,
        "initial_balance":self ._initial_balance ,
        "next_ticket":self ._next_ticket ,
        "positions":{},
        }
        for ticker ,pos_data in self ._positions .items ():
            state ["positions"][ticker ]={
            "ticker":pos_data ["ticker"],
            "quantity":pos_data ["quantity"],
            "entry_price":pos_data ["entry_price"],
            "stop_loss":pos_data ["stop_loss"],
            "ticket":pos_data ["ticket"],
            "timestamp":pos_data ["timestamp"],
            }
        try :
            self ._state_path .parent .mkdir (parents =True ,exist_ok =True )
            with open (self ._state_path ,"w",encoding ="utf-8")as f :
                json .dump (state ,f ,indent =2 ,ensure_ascii =False )
            logger .debug ("Estado salvo em %s",self ._state_path )
        except OSError as exc :
            logger .error ("Falha ao salvar estado do simulador: %s",exc )

    def _load_state (self )->bool :
        """
        Carrega estado persistido do arquivo JSON.

        Returns:
            True se o estado foi carregado, False se não havia arquivo.
        """
        if not self ._state_path .exists ():
            logger .info ("Nenhum estado anterior encontrado — iniciando limpo")
            return False

        try :
            with open (self ._state_path ,"r",encoding ="utf-8")as f :
                state :dict [str ,Any ]=json .load (f )

            self ._balance =float (state .get ("balance",self ._initial_balance ))
            self ._initial_balance =float (state .get ("initial_balance",self ._initial_balance ))
            self ._next_ticket =int (state .get ("next_ticket",1 ))

            self ._positions ={}
            for ticker ,pos_data in state .get ("positions",{}).items ():
                self ._positions [ticker ]={
                "ticker":str (pos_data ["ticker"]),
                "quantity":int (pos_data ["quantity"]),
                "entry_price":float (pos_data ["entry_price"]),
                "stop_loss":float (pos_data ["stop_loss"]),
                "ticket":int (pos_data ["ticket"]),
                "timestamp":str (pos_data ["timestamp"]),
                }

            logger .info (
            "Estado restaurado — saldo: R$%.2f, posições: %d, próximo ticket: %d",
            self ._balance ,
            len (self ._positions ),
            self ._next_ticket ,
            )
            return True
        except (json .JSONDecodeError ,KeyError ,TypeError ,ValueError )as exc :
            logger .warning ("Estado corrompido, iniciando limpo: %s",exc )
            self ._balance =self ._initial_balance
            self ._positions ={}
            self ._next_ticket =1
            return False

    def _get_current_price (self ,ticker :str )->float :
        """
        Obtém preço de mercado atual via MarketData.

        Args:
            ticker: Código do ativo (com ou sem sufixo 'F').

        Returns:
            Preço atual. Retorna 0.0 se indisponível.
        """
        base_ticker =ticker .rstrip ("Ff")
        try :
            md =self ._get_market_data ()
            price_info =md .get_current_price (base_ticker )
            price =price_info .get ("last",0.0 )
            if price and price >0 :
                return float (price )
        except Exception as exc :
            logger .warning ("Falha ao obter preço de %s: %s",base_ticker ,exc )
        return 0.0

    def connect (self )->bool :
        """
        Conecta o simulador — carrega estado persistido.

        Returns:
            Sempre True (simulador não depende de conexão externa).
        """
        with self ._lock :
            self ._load_state ()
            self ._connected =True
            logger .info ("SimulatorBroker conectado — saldo: R$%.2f",self ._balance )
            return True

    def disconnect (self )->None :
        """Desconecta o simulador — salva estado final."""
        with self ._lock :
            self ._save_state ()
            self ._connected =False
            logger .info ("SimulatorBroker desconectado")

    def get_balance (self )->float :
        """
        Retorna o saldo em caixa disponível.

        Returns:
            Saldo em R$.
        """
        with self ._lock :
            return round (self ._balance ,2 )

    def get_equity (self )->float :
        """
        Retorna patrimônio total (caixa + valor de mercado das posições).

        Returns:
            Patrimônio em R$.
        """
        with self ._lock :
            positions_value =0.0
            for ticker ,pos_data in self ._positions .items ():
                current_price =self ._get_current_price (ticker )
                if current_price >0 :
                    positions_value +=current_price *pos_data ["quantity"]
                else :

                    positions_value +=pos_data ["entry_price"]*pos_data ["quantity"]
            return round (self ._balance +positions_value ,2 )

    def buy (self ,ticker :str ,quantity :int ,price :float ,stop_loss :float )->Order :
        """
        Executa ordem de compra simulada.

        Args:
            ticker: Código do ativo.
            quantity: Quantidade (1–99).
            price: Preço unitário.
            stop_loss: Preço de stop-loss.

        Returns:
            Order com status FILLED ou REJECTED.
        """
        ticker =self ._ensure_fractional_suffix (ticker )
        now =datetime .now (tz =BRT )

        try :
            self ._validate_quantity (quantity )
        except ValueError as exc :
            logger .warning ("Compra rejeitada — %s",exc )
            return Order (
            ticker =ticker ,
            order_type =OrderType .BUY ,
            quantity =quantity ,
            price =price ,
            stop_loss =stop_loss ,
            status =OrderStatus .REJECTED ,
            timestamp =now ,
            comment =str (exc ),
            )

        total_cost =round (quantity *price ,2 )

        with self ._lock :

            if total_cost >self ._balance :
                msg =(
                f"Capital insuficiente: R${total_cost :.2f} necessário, "
                f"R${self ._balance :.2f} disponível"
                )
                logger .warning ("Compra rejeitada — %s",msg )
                return Order (
                ticker =ticker ,
                order_type =OrderType .BUY ,
                quantity =quantity ,
                price =price ,
                stop_loss =stop_loss ,
                status =OrderStatus .REJECTED ,
                timestamp =now ,
                comment =msg ,
                )

            if ticker in self ._positions :
                msg =f"Já existe posição aberta em {ticker }"
                logger .warning ("Compra rejeitada — %s",msg )
                return Order (
                ticker =ticker ,
                order_type =OrderType .BUY ,
                quantity =quantity ,
                price =price ,
                stop_loss =stop_loss ,
                status =OrderStatus .REJECTED ,
                timestamp =now ,
                comment =msg ,
                )

            ticket =self ._generate_ticket ()
            self ._balance -=total_cost
            self ._positions [ticker ]={
            "ticker":ticker ,
            "quantity":quantity ,
            "entry_price":price ,
            "stop_loss":stop_loss ,
            "ticket":ticket ,
            "timestamp":now .isoformat (),
            }
            self ._save_state ()

            logger .info (
            "COMPRA executada: %s | %d × R$%.2f = R$%.2f | SL: R$%.2f | Ticket: %d",
            ticker ,
            quantity ,
            price ,
            total_cost ,
            stop_loss ,
            ticket ,
            )

            return Order (
            ticker =ticker ,
            order_type =OrderType .BUY ,
            quantity =quantity ,
            price =price ,
            stop_loss =stop_loss ,
            status =OrderStatus .FILLED ,
            ticket =ticket ,
            timestamp =now ,
            comment =f"Compra simulada executada — total: R${total_cost :.2f}",
            )

    def sell (self ,ticker :str ,quantity :int ,price :float )->Order :
        """
        Executa ordem de venda simulada.

        Args:
            ticker: Código do ativo.
            quantity: Quantidade a vender.
            price: Preço unitário de venda.

        Returns:
            Order com status FILLED ou REJECTED.
        """
        ticker =self ._ensure_fractional_suffix (ticker )
        now =datetime .now (tz =BRT )

        with self ._lock :

            if ticker not in self ._positions :
                msg =f"Nenhuma posição aberta em {ticker }"
                logger .warning ("Venda rejeitada — %s",msg )
                return Order (
                ticker =ticker ,
                order_type =OrderType .SELL ,
                quantity =quantity ,
                price =price ,
                status =OrderStatus .REJECTED ,
                timestamp =now ,
                comment =msg ,
                )

            pos_data =self ._positions [ticker ]

            if quantity >pos_data ["quantity"]:
                msg =(
                f"Quantidade solicitada ({quantity }) excede posição "
                f"({pos_data ['quantity']}) em {ticker }"
                )
                logger .warning ("Venda rejeitada — %s",msg )
                return Order (
                ticker =ticker ,
                order_type =OrderType .SELL ,
                quantity =quantity ,
                price =price ,
                status =OrderStatus .REJECTED ,
                timestamp =now ,
                comment =msg ,
                )

            ticket =self ._generate_ticket ()
            total_revenue =round (quantity *price ,2 )
            self ._balance +=total_revenue

            pnl =round ((price -pos_data ["entry_price"])*quantity ,2 )

            if quantity >=pos_data ["quantity"]:
                del self ._positions [ticker ]
            else :
                self ._positions [ticker ]["quantity"]-=quantity

            self ._save_state ()

            logger .info (
            "VENDA executada: %s | %d × R$%.2f = R$%.2f | P&L: R$%+.2f | Ticket: %d",
            ticker ,
            quantity ,
            price ,
            total_revenue ,
            pnl ,
            ticket ,
            )

            return Order (
            ticker =ticker ,
            order_type =OrderType .SELL ,
            quantity =quantity ,
            price =price ,
            status =OrderStatus .FILLED ,
            ticket =ticket ,
            timestamp =now ,
            comment =f"Venda simulada executada — receita: R${total_revenue :.2f}, P&L: R${pnl :+.2f}",
            )

    def emergency_sell (self ,ticker :str )->Order :
        """
        Venda de emergência — liquida toda a posição a preço de mercado.

        Args:
            ticker: Código do ativo a liquidar.

        Returns:
            Order com status FILLED ou REJECTED.
        """
        ticker =self ._ensure_fractional_suffix (ticker )

        with self ._lock :
            if ticker not in self ._positions :
                return Order (
                ticker =ticker ,
                order_type =OrderType .SELL ,
                quantity =0 ,
                price =0.0 ,
                status =OrderStatus .REJECTED ,
                timestamp =datetime .now (tz =BRT ),
                comment =f"Nenhuma posição aberta em {ticker } para venda emergencial",
                )
            pos_data =self ._positions [ticker ]
            quantity =pos_data ["quantity"]

        market_price =self ._get_current_price (ticker )
        if market_price <=0 :
            market_price =pos_data ["entry_price"]
            logger .warning (
            "Preço de mercado indisponível para %s — usando preço de entrada R$%.2f",
            ticker ,
            market_price ,
            )

        logger .warning (
        "VENDA EMERGENCIAL: %s | %d ações @ R$%.2f",
        ticker ,
        quantity ,
        market_price ,
        )
        return self .sell (ticker ,quantity ,market_price )

    def get_positions (self )->list [Position ]:
        """
        Retorna todas as posições abertas como objetos Position.

        Returns:
            Lista de Position com preços atualizados.
        """
        with self ._lock :
            positions_copy =dict (self ._positions )

        result :list [Position ]=[]
        for ticker ,pos_data in positions_copy .items ():
            current_price =self ._get_current_price (ticker )
            if current_price <=0 :
                current_price =pos_data ["entry_price"]

            try :
                ts =datetime .fromisoformat (pos_data ["timestamp"])
                if ts .tzinfo is None :
                    ts =ts .replace (tzinfo =BRT )
            except (ValueError ,TypeError ):
                ts =datetime .now (tz =BRT )

            result .append (
            Position (
            ticker =pos_data ["ticker"],
            quantity =pos_data ["quantity"],
            entry_price =pos_data ["entry_price"],
            current_price =current_price ,
            stop_loss =pos_data ["stop_loss"],
            ticket =pos_data ["ticket"],
            timestamp =ts ,
            )
            )
        return result

    def get_position (self ,ticker :str )->Optional [Position ]:
        """
        Retorna a posição de um ativo específico.

        Args:
            ticker: Código do ativo (com ou sem sufixo 'F').

        Returns:
            Position se existir, None caso contrário.
        """
        ticker =self ._ensure_fractional_suffix (ticker )
        positions =self .get_positions ()
        for pos in positions :
            if pos .ticker ==ticker :
                return pos
        return None

    def modify_stop_loss (self ,ticker :str ,new_sl :float )->bool :
        """
        Modifica o stop-loss de uma posição aberta.

        Args:
            ticker: Código do ativo.
            new_sl: Novo preço de stop-loss.

        Returns:
            True se modificado com sucesso, False caso contrário.
        """
        ticker =self ._ensure_fractional_suffix (ticker )

        if new_sl <=0 :
            logger .warning ("Stop-loss inválido: R$%.2f — deve ser > 0",new_sl )
            return False

        with self ._lock :
            if ticker not in self ._positions :
                logger .warning ("Posição %s não encontrada para modificar SL",ticker )
                return False

            old_sl =self ._positions [ticker ]["stop_loss"]
            self ._positions [ticker ]["stop_loss"]=new_sl
            self ._save_state ()

            logger .info (
            "Stop-loss modificado: %s | R$%.2f → R$%.2f",
            ticker ,
            old_sl ,
            new_sl ,
            )
            return True

    def reset (self )->None :
        """Reseta o simulador ao estado inicial (útil para testes)."""
        with self ._lock :
            self ._balance =self ._initial_balance
            self ._positions .clear ()
            self ._next_ticket =1
            self ._save_state ()
            logger .info ("Simulador resetado — capital: R$%.2f",self ._initial_balance )

    @property
    def is_connected (self )->bool :
        """Indica se o simulador está conectado."""
        return self ._connected

    def __repr__ (self )->str :
        """Representação textual do simulador."""
        return (
        f"SimulatorBroker(balance=R${self ._balance :.2f}, "
        f"positions={len (self ._positions )}, "
        f"connected={self ._connected })"
        )
