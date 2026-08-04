"""
Broker MetaTrader 5 do Projeto Córtex.

Implementa a interface BrokerBase utilizando a API Python do MT5.
Funciona exclusivamente em Windows. Inclui auto-reconexão,
detecção automática de modo de preenchimento, e tratamento completo
de retcodes.
"""

from __future__ import annotations

import logging
import platform
import threading
import time
from datetime import datetime
from typing import Any ,Optional

from zoneinfo import ZoneInfo

from broker .base import BrokerBase ,Order ,OrderStatus ,OrderType ,Position
from config .settings import settings

logger =logging .getLogger ("cortex.broker.mt5")

BRT :ZoneInfo =ZoneInfo ("America/Sao_Paulo")

_mt5_available :bool =False
try :
    if platform .system ()=="Windows":
        import MetaTrader5 as mt5
        _mt5_available =True
except ImportError :
    mt5 =None

_MT5_RETCODE_MESSAGES :dict [int ,str ]={
10004 :"Requote — preço mudou",
10006 :"Requisição rejeitada",
10007 :"Requisição cancelada pelo trader",
10008 :"Ordem colocada com sucesso",
10009 :"Ordem executada com sucesso",
10010 :"Ordem executada parcialmente",
10011 :"Erro no processamento da requisição",
10012 :"Requisição cancelada por timeout",
10013 :"Requisição inválida",
10014 :"Volume inválido na requisição",
10015 :"Preço inválido na requisição",
10016 :"Stops inválidos na requisição",
10017 :"Trading desabilitado",
10018 :"Mercado fechado",
10019 :"Capital insuficiente",
10020 :"Preços mudaram",
10021 :"Sem cotação para processar",
10022 :"Período de expiração inválido",
10023 :"Estado da ordem mudou",
10024 :"Muitas requisições",
10025 :"Sem mudanças na requisição",
10026 :"Autotrading desabilitado no servidor",
10027 :"Autotrading desabilitado no terminal",
10028 :"Requisição bloqueada para processamento",
10029 :"Ordem ou posição congelada",
10030 :"Tipo de preenchimento inválido",
10031 :"Sem conexão com o servidor",
10032 :"Operação permitida apenas para contas reais",
10033 :"Limite de ordens pendentes atingido",
10034 :"Limite de volume atingido",
10035 :"Tipo de ordem inválido ou proibido",
10036 :"Posição já fechada",
10038 :"Volume de fechamento excede posição atual",
10039 :"Ordem de fechamento já existe para esta posição",
10040 :"Limite de posições atingido",
10041 :"Requisição rejeitada — verificação de hedging pendente",
10042 :"Requisição rejeitada — limite de regra FIFO",
10043 :"Requisição rejeitada — regra de hedge-only",
}

def _retcode_to_message (retcode :int )->str :
    """Converte retcode MT5 em mensagem legível."""
    return _MT5_RETCODE_MESSAGES .get (retcode ,f"Código desconhecido: {retcode }")

class MT5Broker (BrokerBase ):
    """
    Implementação de corretora via MetaTrader 5.

    Requer Windows e o pacote MetaTrader5 instalado.
    Suporta auto-reconexão, detecção de filling mode,
    e magic number configurável.
    """

    MAGIC_NUMBER :int =settings .mt5_magic
    MAX_RECONNECT_ATTEMPTS :int =3
    RECONNECT_DELAY_SECONDS :float =2.0

    def __init__ (self )->None :
        """Inicializa o broker MT5."""
        self ._connected :bool =False
        self ._lock :threading .Lock =threading .Lock ()
        try :
            self ._login :int =int (settings .mt5_login )
        except (ValueError ,TypeError ):
            raise ValueError (
            "MT5_LOGIN inválido ou ausente. "
            "Defina a variável de ambiente MT5_LOGIN com o número da conta MT5."
            )
        self ._password :str =settings .mt5_password
        self ._server :str =settings .mt5_server
        self ._mt5_path :str =settings .mt5_path
        logger .info (
        "MT5Broker criado — login: %d, server: %s",
        self ._login ,
        self ._server ,
        )

    def _check_platform (self )->None :
        """
        Verifica se o sistema operacional é Windows.

        Raises:
            RuntimeError: Se não for Windows.
        """
        if platform .system ()!="Windows":
            raise RuntimeError (
            "MT5Broker requer Windows. "
            f"Sistema detectado: {platform .system ()}. "
            "Use SimulatorBroker em ambientes não-Windows."
            )

    def _check_mt5_available (self )->None :
        """
        Verifica se o módulo MetaTrader5 está instalado.

        Raises:
            RuntimeError: Se o módulo não estiver disponível.
        """
        if not _mt5_available or mt5 is None :
            raise RuntimeError (
            "Módulo MetaTrader5 não instalado. "
            "Execute: pip install MetaTrader5"
            )

    def _ensure_connected (self )->None :
        """
        Verifica conexão e tenta reconectar se necessário.

        Raises:
            ConnectionError: Se não conseguir restabelecer conexão.
        """
        if self ._connected :

            try :
                info =mt5 .account_info ()
                if info is not None :
                    return
            except Exception :
                pass

        logger .warning ("Conexão MT5 perdida — tentando reconectar...")
        for attempt in range (1 ,self .MAX_RECONNECT_ATTEMPTS +1 ):
            if self .connect ():
                logger .info ("Reconexão bem-sucedida na tentativa %d",attempt )
                return
            time .sleep (self .RECONNECT_DELAY_SECONDS )

        raise ConnectionError (
        f"Falha ao reconectar ao MT5 após {self .MAX_RECONNECT_ATTEMPTS } tentativas"
        )

    def _ensure_symbol_visible (self ,symbol :str )->bool :
        """
        Garante que o símbolo está selecionado e visível no Market Watch.

        Args:
            symbol: Código do ativo MT5.

        Returns:
            True se o símbolo está disponível, False caso contrário.
        """
        info =mt5 .symbol_info (symbol )
        if info is None :
            logger .error ("Símbolo %s não encontrado no MT5",symbol )
            return False

        if not info .visible :
            if not mt5 .symbol_select (symbol ,True ):
                logger .error ("Falha ao selecionar símbolo %s no Market Watch",symbol )
                return False

            time .sleep (0.5 )

        return True

    def _detect_filling_mode (self ,symbol :str )->int :
        """
        Detecta o modo de preenchimento suportado pelo símbolo.

        Args:
            symbol: Código do ativo MT5.

        Returns:
            Constante MT5 de filling mode adequada.
        """
        info =mt5 .symbol_info (symbol )
        if info is None :
            logger .warning ("Símbolo %s não encontrado — usando ORDER_FILLING_RETURN",symbol )
            return mt5 .ORDER_FILLING_RETURN

        filling =info .filling_mode

        if filling &mt5 .SYMBOL_FILLING_FOK :
            return mt5 .ORDER_FILLING_FOK
        elif filling &mt5 .SYMBOL_FILLING_IOC :
            return mt5 .ORDER_FILLING_IOC

        return mt5 .ORDER_FILLING_RETURN

    @staticmethod
    def _ensure_fractional_suffix (ticker :str )->str :
        """Garante sufixo 'F' para mercado fracionário."""
        ticker =ticker .upper ().strip ()
        if not ticker .endswith ("F"):
            ticker +="F"
        return ticker

    def connect (self )->bool :
        """
        Conecta ao terminal MetaTrader 5.

        Returns:
            True se a conexão e login foram bem-sucedidos.

        Raises:
            RuntimeError: Se não for Windows ou MT5 não estiver instalado.
        """
        self ._check_platform ()
        self ._check_mt5_available ()

        with self ._lock :

            init_kwargs :dict [str ,Any ]={}
            if self ._mt5_path :
                init_kwargs ["path"]=self ._mt5_path

            if not mt5 .initialize (**init_kwargs ):
                error =mt5 .last_error ()
                logger .error ("Falha ao inicializar MT5: %s",error )
                self ._connected =False
                return False

            if self ._login and self ._password and self ._server :
                authorized =mt5 .login (
                login =self ._login ,
                password =self ._password ,
                server =self ._server ,
                )
                if not authorized :
                    error =mt5 .last_error ()
                    logger .error ("Falha no login MT5: %s",error )
                    mt5 .shutdown ()
                    self ._connected =False
                    return False

            account_info =mt5 .account_info ()
            if account_info is None :
                logger .error ("Não foi possível obter informações da conta MT5")
                mt5 .shutdown ()
                self ._connected =False
                return False

            self ._connected =True
            logger .info (
            "MT5 conectado — conta: %d, servidor: %s, saldo: R$%.2f",
            account_info .login ,
            account_info .server ,
            account_info .balance ,
            )
            return True

    def disconnect (self )->None :
        """Encerra a conexão com o MetaTrader 5."""
        with self ._lock :
            if _mt5_available and mt5 is not None :
                try :
                    mt5 .shutdown ()
                except Exception as exc :
                    logger .warning ("Erro ao desconectar MT5: %s",exc )
            self ._connected =False
            logger .info ("MT5Broker desconectado")

    def get_balance (self )->float :
        """
        Retorna o saldo em caixa da conta MT5.

        Returns:
            Saldo em R$.
        """
        self ._ensure_connected ()
        info =mt5 .account_info ()
        if info is None :
            logger .error ("Não foi possível obter saldo da conta")
            return 0.0
        return round (float (info .balance ),2 )

    def get_equity (self )->float :
        """
        Retorna o patrimônio total da conta MT5.

        Returns:
            Patrimônio em R$.
        """
        self ._ensure_connected ()
        info =mt5 .account_info ()
        if info is None :
            logger .error ("Não foi possível obter equity da conta")
            return 0.0
        return round (float (info .equity ),2 )

    def buy (self ,ticker :str ,quantity :int ,price :float ,stop_loss :float )->Order :
        """
        Envia ordem de compra via MT5.

        Args:
            ticker: Código do ativo.
            quantity: Quantidade de ações (1–99 para fracionário).
            price: Preço unitário desejado.
            stop_loss: Preço de stop-loss.

        Returns:
            Order com status da execução.
        """
        symbol =self ._ensure_fractional_suffix (ticker )
        now =datetime .now (tz =BRT )

        try :
            self ._ensure_connected ()
        except ConnectionError as exc :
            return Order (
            ticker =symbol ,order_type =OrderType .BUY ,quantity =quantity ,
            price =price ,stop_loss =stop_loss ,status =OrderStatus .REJECTED ,
            timestamp =now ,comment =f"Sem conexão MT5: {exc }",
            )

        if not self ._ensure_symbol_visible (symbol ):
            return Order (
            ticker =symbol ,order_type =OrderType .BUY ,quantity =quantity ,
            price =price ,stop_loss =stop_loss ,status =OrderStatus .REJECTED ,
            timestamp =now ,comment =f"Símbolo {symbol } indisponível",
            )

        filling_mode =self ._detect_filling_mode (symbol )

        with self ._lock :
            request :dict [str ,Any ]={
            "action":mt5 .TRADE_ACTION_DEAL ,
            "symbol":symbol ,
            "volume":float (quantity ),
            "type":mt5 .ORDER_TYPE_BUY ,
            "price":price ,
            "sl":stop_loss ,
            "deviation":20 ,
            "magic":self .MAGIC_NUMBER ,
            "comment":"Cortex BUY",
            "type_time":mt5 .ORDER_TIME_GTC ,
            "type_filling":filling_mode ,
            }

            check_result =mt5 .order_check (request )
            if check_result is None or check_result .retcode !=0 :
                retcode =check_result .retcode if check_result else -1
                msg =f"Pré-validação falhou: {_retcode_to_message (retcode )}"
                logger .warning ("Compra rejeitada MT5 — %s",msg )
                return Order (
                ticker =symbol ,order_type =OrderType .BUY ,quantity =quantity ,
                price =price ,stop_loss =stop_loss ,status =OrderStatus .REJECTED ,
                timestamp =now ,comment =msg ,
                )

            result =mt5 .order_send (request )
            if result is None :
                error =mt5 .last_error ()
                msg =f"Erro ao enviar ordem: {error }"
                logger .error ("Compra falhou MT5 — %s",msg )
                return Order (
                ticker =symbol ,order_type =OrderType .BUY ,quantity =quantity ,
                price =price ,stop_loss =stop_loss ,status =OrderStatus .REJECTED ,
                timestamp =now ,comment =msg ,
                )

            if result .retcode ==10009 :
                logger .info (
                "COMPRA MT5 executada: %s | %d × R$%.2f | Ticket: %d",
                symbol ,quantity ,result .price ,result .order ,
                )
                return Order (
                ticker =symbol ,order_type =OrderType .BUY ,quantity =quantity ,
                price =result .price ,stop_loss =stop_loss ,
                status =OrderStatus .FILLED ,ticket =result .order ,
                timestamp =now ,comment =f"MT5 executada — {result .comment }",
                )
            elif result .retcode ==10008 :
                logger .info (
                "Compra MT5 colocada: %s | Ticket: %d",symbol ,result .order ,
                )
                return Order (
                ticker =symbol ,order_type =OrderType .BUY ,quantity =quantity ,
                price =price ,stop_loss =stop_loss ,
                status =OrderStatus .PENDING ,ticket =result .order ,
                timestamp =now ,comment =f"MT5 pendente — {result .comment }",
                )
            else :
                msg =_retcode_to_message (result .retcode )
                logger .warning ("Compra MT5 rejeitada — retcode %d: %s",result .retcode ,msg )
                return Order (
                ticker =symbol ,order_type =OrderType .BUY ,quantity =quantity ,
                price =price ,stop_loss =stop_loss ,status =OrderStatus .REJECTED ,
                timestamp =now ,comment =f"Retcode {result .retcode }: {msg }",
                )

    def sell (self ,ticker :str ,quantity :int ,price :float )->Order :
        """
        Envia ordem de venda via MT5.

        Args:
            ticker: Código do ativo.
            quantity: Quantidade de ações a vender.
            price: Preço unitário desejado.

        Returns:
            Order com status da execução.
        """
        symbol =self ._ensure_fractional_suffix (ticker )
        now =datetime .now (tz =BRT )

        try :
            self ._ensure_connected ()
        except ConnectionError as exc :
            return Order (
            ticker =symbol ,order_type =OrderType .SELL ,quantity =quantity ,
            price =price ,status =OrderStatus .REJECTED ,
            timestamp =now ,comment =f"Sem conexão MT5: {exc }",
            )

        if not self ._ensure_symbol_visible (symbol ):
            return Order (
            ticker =symbol ,order_type =OrderType .SELL ,quantity =quantity ,
            price =price ,status =OrderStatus .REJECTED ,
            timestamp =now ,comment =f"Símbolo {symbol } indisponível",
            )

        filling_mode =self ._detect_filling_mode (symbol )

        with self ._lock :
            request :dict [str ,Any ]={
            "action":mt5 .TRADE_ACTION_DEAL ,
            "symbol":symbol ,
            "volume":float (quantity ),
            "type":mt5 .ORDER_TYPE_SELL ,
            "price":price ,
            "deviation":20 ,
            "magic":self .MAGIC_NUMBER ,
            "comment":"Cortex SELL",
            "type_time":mt5 .ORDER_TIME_GTC ,
            "type_filling":filling_mode ,
            }

            check_result =mt5 .order_check (request )
            if check_result is None or check_result .retcode !=0 :
                retcode =check_result .retcode if check_result else -1
                msg =f"Pré-validação falhou: {_retcode_to_message (retcode )}"
                logger .warning ("Venda rejeitada MT5 — %s",msg )
                return Order (
                ticker =symbol ,order_type =OrderType .SELL ,quantity =quantity ,
                price =price ,status =OrderStatus .REJECTED ,
                timestamp =now ,comment =msg ,
                )

            result =mt5 .order_send (request )
            if result is None :
                error =mt5 .last_error ()
                msg =f"Erro ao enviar ordem: {error }"
                logger .error ("Venda falhou MT5 — %s",msg )
                return Order (
                ticker =symbol ,order_type =OrderType .SELL ,quantity =quantity ,
                price =price ,status =OrderStatus .REJECTED ,
                timestamp =now ,comment =msg ,
                )

            if result .retcode ==10009 :
                logger .info (
                "VENDA MT5 executada: %s | %d × R$%.2f | Ticket: %d",
                symbol ,quantity ,result .price ,result .order ,
                )
                return Order (
                ticker =symbol ,order_type =OrderType .SELL ,quantity =quantity ,
                price =result .price ,status =OrderStatus .FILLED ,
                ticket =result .order ,timestamp =now ,
                comment =f"MT5 executada — {result .comment }",
                )
            elif result .retcode ==10008 :
                return Order (
                ticker =symbol ,order_type =OrderType .SELL ,quantity =quantity ,
                price =price ,status =OrderStatus .PENDING ,
                ticket =result .order ,timestamp =now ,
                comment =f"MT5 pendente — {result .comment }",
                )
            else :
                msg =_retcode_to_message (result .retcode )
                logger .warning ("Venda MT5 rejeitada — retcode %d: %s",result .retcode ,msg )
                return Order (
                ticker =symbol ,order_type =OrderType .SELL ,quantity =quantity ,
                price =price ,status =OrderStatus .REJECTED ,
                timestamp =now ,comment =f"Retcode {result .retcode }: {msg }",
                )

    def emergency_sell (self ,ticker :str )->Order :
        """
        Venda de emergência — liquida toda a posição a preço de mercado.

        Args:
            ticker: Código do ativo a liquidar.

        Returns:
            Order com status da execução.
        """
        symbol =self ._ensure_fractional_suffix (ticker )
        now =datetime .now (tz =BRT )

        try :
            self ._ensure_connected ()
        except ConnectionError as exc :
            return Order (
            ticker =symbol ,order_type =OrderType .SELL ,quantity =0 ,
            price =0.0 ,status =OrderStatus .REJECTED ,
            timestamp =now ,comment =f"Sem conexão MT5: {exc }",
            )

        positions =mt5 .positions_get (symbol =symbol )
        if positions is None or len (positions )==0 :
            return Order (
            ticker =symbol ,order_type =OrderType .SELL ,quantity =0 ,
            price =0.0 ,status =OrderStatus .REJECTED ,
            timestamp =now ,comment =f"Nenhuma posição aberta em {symbol }",
            )

        pos =positions [0 ]
        quantity =int (pos .volume )

        tick =mt5 .symbol_info_tick (symbol )
        if tick is None :
            return Order (
            ticker =symbol ,order_type =OrderType .SELL ,quantity =quantity ,
            price =0.0 ,status =OrderStatus .REJECTED ,
            timestamp =now ,comment =f"Cotação indisponível para {symbol }",
            )

        market_price =tick .bid

        logger .warning (
        "VENDA EMERGENCIAL MT5: %s | %d ações @ R$%.2f",
        symbol ,quantity ,market_price ,
        )
        return self .sell (symbol ,quantity ,market_price )

    def get_positions (self )->list [Position ]:
        """
        Retorna todas as posições abertas via MT5.

        Returns:
            Lista de objetos Position.
        """
        try :
            self ._ensure_connected ()
        except ConnectionError :
            logger .error ("Sem conexão para obter posições")
            return []

        mt5_positions =mt5 .positions_get ()
        if mt5_positions is None :
            return []

        result :list [Position ]=[]
        for pos in mt5_positions :

            if pos .magic !=self .MAGIC_NUMBER :
                continue

            tick =mt5 .symbol_info_tick (pos .symbol )
            current_price =tick .bid if tick else pos .price_current

            try :
                ts =datetime .fromtimestamp (pos .time ,tz =BRT )
            except (OSError ,ValueError ):
                ts =datetime .now (tz =BRT )

            result .append (
            Position (
            ticker =pos .symbol ,
            quantity =int (pos .volume ),
            entry_price =pos .price_open ,
            current_price =current_price ,
            stop_loss =pos .sl ,
            ticket =pos .ticket ,
            timestamp =ts ,
            )
            )
        return result

    def get_position (self ,ticker :str )->Optional [Position ]:
        """
        Retorna a posição de um ativo específico.

        Args:
            ticker: Código do ativo.

        Returns:
            Position se existir, None caso contrário.
        """
        symbol =self ._ensure_fractional_suffix (ticker )
        positions =self .get_positions ()
        for pos in positions :
            if pos .ticker ==symbol :
                return pos
        return None

    def modify_stop_loss (self ,ticker :str ,new_sl :float )->bool :
        """
        Modifica o stop-loss de uma posição aberta via TRADE_ACTION_SLTP.

        Args:
            ticker: Código do ativo.
            new_sl: Novo preço de stop-loss.

        Returns:
            True se modificado com sucesso, False caso contrário.
        """
        symbol =self ._ensure_fractional_suffix (ticker )

        try :
            self ._ensure_connected ()
        except ConnectionError :
            logger .error ("Sem conexão para modificar SL de %s",symbol )
            return False

        positions =mt5 .positions_get (symbol =symbol )
        if positions is None or len (positions )==0 :
            logger .warning ("Posição %s não encontrada para modificar SL",symbol )
            return False

        pos =positions [0 ]

        with self ._lock :
            request :dict [str ,Any ]={
            "action":mt5 .TRADE_ACTION_SLTP ,
            "symbol":symbol ,
            "position":pos .ticket ,
            "sl":new_sl ,
            "tp":pos .tp ,
            "magic":self .MAGIC_NUMBER ,
            "comment":"Cortex SL_MODIFY",
            }

            result =mt5 .order_send (request )
            if result is None :
                error =mt5 .last_error ()
                logger .error ("Falha ao modificar SL de %s: %s",symbol ,error )
                return False

            if result .retcode ==10009 :
                logger .info (
                "Stop-loss modificado MT5: %s | R$%.2f → R$%.2f",
                symbol ,pos .sl ,new_sl ,
                )
                return True
            else :
                msg =_retcode_to_message (result .retcode )
                logger .warning (
                "Falha ao modificar SL de %s — retcode %d: %s",
                symbol ,result .retcode ,msg ,
                )
                return False

    @property
    def is_connected (self )->bool :
        """Indica se o broker MT5 está conectado."""
        return self ._connected

    def __repr__ (self )->str :
        """Representação textual do broker MT5."""
        return (
        f"MT5Broker(login={self ._login }, "
        f"server='{self ._server }', "
        f"connected={self ._connected })"
        )
