"""
Broker MetaTrader 5 do Projeto Córtex (Via Bridge REST).

Implementa a interface BrokerBase comunicando-se com a API REST
do container Docker rodando MT5. Permite rodar o Córtex nativamente
no Linux.
"""

import logging
import requests
import threading
from datetime import datetime
from typing import Any ,Optional
from zoneinfo import ZoneInfo

from broker .base import BrokerBase ,Order ,OrderStatus ,OrderType ,Position
from config .settings import settings

logger =logging .getLogger ("cortex.broker.rest_mt5")
BRT =ZoneInfo ("America/Sao_Paulo")

class RestMT5Broker (BrokerBase ):
    """
    Broker MT5 que se comunica via HTTP com o container Bridge.
    """

    MAGIC_NUMBER :int =settings .mt5_magic

    def __init__ (self ,base_url :str ="http://127.0.0.1:5000"):
        self ._base_url =base_url
        self ._connected =False
        self ._lock =threading .Lock ()

        try :
            self ._login =int (settings .mt5_login )if settings .mt5_login else 0
        except (ValueError ,TypeError ):
            logger .warning ("MT5_LOGIN inválido ('%s'), usando 0",settings .mt5_login )
            self ._login =0
        self ._password =settings .mt5_password
        self ._server =settings .mt5_server

    def _api_call (self ,endpoint :str ,data :dict =None ,method :str ="POST")->dict :
        url =f"{self ._base_url }/{endpoint }"
        try :
            if method =="GET":
                resp =requests .get (url ,timeout =10 )
            else :
                resp =requests .post (url ,json =data ,timeout =10 )
            resp .raise_for_status ()
            return resp .json ()
        except Exception as e :
            logger .error ("API Bridge falhou: %s",e )
            return None

    @staticmethod
    def _fractional_ticker (ticker :str )->str :
        """Garante sufixo 'F' para mercado fracionário (ex: PETR4 -> PETR4F)."""
        t =ticker .upper ().strip ()
        if not t .endswith ("F"):
            t =t +"F"
        return t

    def connect (self )->bool :
        with self ._lock :
            data ={
            "login":self ._login ,
            "password":self ._password ,
            "server":self ._server
            }
            res =self ._api_call ("connect",data )
            if res and res .get ("status")=="connected":
                self ._connected =True
                logger .info ("Conectado ao MT5 Bridge. Saldo: R$%.2f",res .get ("balance",0 ))
                return True
            return False

    def disconnect (self )->None :
        self ._connected =False
        logger .info ("RestMT5Broker desconectado")

    def get_balance (self )->float :
        res =self ._api_call ("account",method ="GET")
        if res :
            return res .get ("balance",0.0 )
        return 0.0

    def get_equity (self )->float :
        res =self ._api_call ("account",method ="GET")
        if res :
            return res .get ("equity",0.0 )
        return 0.0

    def buy (self ,ticker :str ,quantity :int ,price :float ,stop_loss :float )->Order :
        now =datetime .now (tz =BRT )
        symbol =self ._fractional_ticker (ticker )
        req ={
        "action":1 ,
        "symbol":symbol ,
        "volume":float (quantity ),
        "type":0 ,
        "price":price ,
        "sl":stop_loss ,
        "tp":0.0 ,
        "deviation":20 ,
        "magic":self .MAGIC_NUMBER ,
        "comment":"Cortex BUY",
        "type_time":0 ,
        "type_filling":1
        }

        check =self ._api_call ("order_check",req )
        if not check or check .get ("retcode")!=0 :
            return Order (ticker ,OrderType .BUY ,quantity ,price ,stop_loss ,OrderStatus .REJECTED ,timestamp =now )

        res =self ._api_call ("order_send",req )
        if res and res .get ("retcode")==10009 :
            return Order (ticker ,OrderType .BUY ,quantity ,res .get ("price",price ),stop_loss ,OrderStatus .FILLED ,res .get ("order"),now )

        return Order (ticker ,OrderType .BUY ,quantity ,price ,stop_loss ,OrderStatus .REJECTED ,timestamp =now )

    def sell (self ,ticker :str ,quantity :int ,price :float )->Order :
        now =datetime .now (tz =BRT )
        symbol =self ._fractional_ticker (ticker )
        req ={
        "action":1 ,
        "symbol":symbol ,
        "volume":float (quantity ),
        "type":1 ,
        "price":price ,
        "deviation":20 ,
        "magic":self .MAGIC_NUMBER ,
        "comment":"Cortex SELL",
        "type_time":0 ,
        "type_filling":1
        }

        res =self ._api_call ("order_send",req )
        if res and res .get ("retcode")==10009 :
            return Order (ticker ,OrderType .SELL ,quantity ,res .get ("price",price ),status =OrderStatus .FILLED ,ticket =res .get ("order"),timestamp =now )

        return Order (ticker ,OrderType .SELL ,quantity ,price ,status =OrderStatus .REJECTED ,timestamp =now )

    def emergency_sell (self ,ticker :str )->Order :
        """Venda de emergência — liquida toda a posição a preço de mercado."""
        position =self .get_position (ticker )
        if position is None :
            logger .warning ("emergency_sell: sem posição aberta para %s",ticker )
            return Order (
            ticker ,OrderType .SELL ,0 ,0.0 ,
            status =OrderStatus .REJECTED ,
            timestamp =datetime .now (tz =BRT ),
            comment ="Sem posição aberta",
            )
        return self .sell (ticker ,position .quantity ,position .current_price )

    def get_positions (self )->list [Position ]:
        res =self ._api_call ("positions",method ="GET")
        if not res :
            return []

        positions =[]
        for p in res :
            if p .get ("magic")==self .MAGIC_NUMBER :
                positions .append (Position (
                ticker =p .get ("symbol"),
                quantity =int (p .get ("volume",0 )),
                entry_price =p .get ("price_open",0 ),
                current_price =p .get ("price_current",0 ),
                stop_loss =p .get ("sl",0 ),
                ticket =p .get ("ticket",0 ),
                timestamp =datetime .now (tz =BRT )
                ))
        return positions

    def get_position (self ,ticker :str )->Optional [Position ]:
        for p in self .get_positions ():
            if p .ticker ==ticker :
                return p
        return None

    def modify_stop_loss (self ,ticker :str ,new_sl :float )->bool :
        return False

    @property
    def is_connected (self )->bool :
        return self ._connected
