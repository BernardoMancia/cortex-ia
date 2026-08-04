"""
Módulo de configurações centrais do Projeto Córtex.

Carrega variáveis de ambiente, define constantes de negociação,
horários de mercado, feriados da B3 e lista de ativos monitorados.
Implementa padrão singleton para garantir instância única.
"""

import os
import logging
from datetime import date ,datetime ,time ,timezone ,timedelta
from pathlib import Path
from typing import Any ,Final

from dotenv import load_dotenv

_PROJECT_ROOT :Final [Path ]=Path (__file__ ).resolve ().parent .parent

_env_path =_PROJECT_ROOT /".env"
load_dotenv (dotenv_path =_env_path if _env_path .exists ()else None )

BRT :Final [timezone ]=timezone (timedelta (hours =-3 ),name ="BRT")

def _env_bool (key :str ,default :bool =False )->bool :
    """Converte variável de ambiente para booleano."""
    val =os .getenv (key ,str (default )).strip ().lower ()
    return val in ("true","1","yes","sim")

def _env_float (key :str ,default :float =0.0 )->float :
    """Converte variável de ambiente para float."""
    try :
        return float (os .getenv (key ,str (default )))
    except (ValueError ,TypeError ):
        return default

def _env_int (key :str ,default :int =0 )->int :
    """Converte variável de ambiente para inteiro."""
    try :
        return int (os .getenv (key ,str (default )))
    except (ValueError ,TypeError ):
        return default

class Settings :
    """
    Configurações centrais do sistema Córtex.

    Carrega parâmetros de ambiente e define constantes de negociação,
    horários de mercado, feriados e lista de ativos.
    Utiliza padrão singleton — a instância global é `settings`.
    """

    _instance :"Settings | None"=None

    def __new__ (cls ,**kwargs :object )->"Settings":
        """Garante instância única (singleton)."""
        if cls ._instance is None :
            cls ._instance =super ().__new__ (cls )
            cls ._instance ._initialized =False
        return cls ._instance

    def __init__ (
    self ,
    simulation_mode :bool |None =None ,
    verbose :bool =False ,
    **kwargs :object ,
    )->None :
        if self ._initialized :

            if simulation_mode is not None :
                self .SIMULATION_MODE =simulation_mode
            if verbose :
                self .VERBOSE =verbose
            return
        self ._initialized =True
        self .VERBOSE :bool =verbose

        self .PROJECT_ROOT :Final [Path ]=_PROJECT_ROOT

        self .SIMULATION_MODE :bool =(
        simulation_mode if simulation_mode is not None
        else _env_bool ("SIMULATION_MODE",default =True )
        )
        self .BROKER_MODE :str =os .getenv ("BROKER_MODE","simulator").strip ().lower ()

        self .CAPITAL_INICIAL :Final [float ]=_env_float ("CAPITAL_INICIAL",200.00 )
        self .STOP_LOSS_PERCENT :Final [float ]=_env_float ("STOP_LOSS_PERCENT",0.10 )

        self .SENTIMENT_MODE :str =os .getenv ("SENTIMENT_MODE","lightweight").strip ().lower ()

        self .MT5_LOGIN :str =os .getenv ("MT5_LOGIN","")
        self .MT5_PASSWORD :str =os .getenv ("MT5_PASSWORD","")
        self .MT5_SERVER :str =os .getenv ("MT5_SERVER","ClearInvestimentos-Server")
        self .MT5_PATH :str =os .getenv (
        "MT5_PATH",
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
        )
        self .MT5_MAGIC :int =_env_int ("MT5_MAGIC",234000 )

        self .TELEGRAM_TOKEN :str =os .getenv ("TELEGRAM_TOKEN","")
        self .TELEGRAM_CHAT_ID :str =os .getenv ("TELEGRAM_CHAT_ID","")

        self .DASHBOARD_HOST :str =os .getenv ("DASHBOARD_HOST","0.0.0.0")
        self .DASHBOARD_PORT :int =_env_int ("DASHBOARD_PORT",8003 )

        self .LOG_LEVEL :str =os .getenv ("LOG_LEVEL","INFO").strip ().upper ()
        self .LOG_DIR :Path =_PROJECT_ROOT /os .getenv ("LOG_DIR","logs")

        self .LOG_DIR .mkdir (parents =True ,exist_ok =True )

        self .DB_PATH :Path =_PROJECT_ROOT /"data"/"cortex.db"
        self .DB_PATH .parent .mkdir (parents =True ,exist_ok =True )

        self .TRADING_CYCLE_INTERVAL :int =_env_int ("TRADING_CYCLE_INTERVAL",60 )
        self .CLOSED_CHECK_INTERVAL :int =_env_int ("CLOSED_CHECK_INTERVAL",300 )
        self .HEALTH_CHECK_INTERVAL :int =_env_int ("HEALTH_CHECK_INTERVAL",120 )
        self .ALERT_COOLDOWN :int =_env_int ("ALERT_COOLDOWN",1800 )
        self .VOLATILITY_ALERT_THRESHOLD :float =_env_float (
        "VOLATILITY_ALERT_THRESHOLD",5.0 ,
        )
        self .NEWS_REQUEST_TIMEOUT :int =_env_int ("NEWS_REQUEST_TIMEOUT",15 )

        self .min_quantity :int =1
        self .max_quantity :int =99

        self .price_cache_ttl_seconds :int =_env_int ("PRICE_CACHE_TTL",30 )

        self .simulator_state_path :Path =_PROJECT_ROOT /"data"/"simulator_state.json"

        self .WATCHLIST :Final [list [str ]]=[
        "PETR4","VALE3","ITUB4","BBDC4","BBAS3",
        "WEGE3","RENT3","ABEV3","MGLU3","SUZB3",
        "EMBR3","PRIO3","B3SA3","RDOR3","VIVT3",
        "CSAN3","GGBR4","CSNA3","TOTS3","BPAC11",
        ]

        self .YFINANCE_SUFFIX_MAP :Final [dict [str ,str ]]={
        ticker :f"{ticker }.SA"for ticker in self .WATCHLIST
        }

    def __getattr__ (self ,name :str )->Any :
        """Permite acesso snake_case para propriedades UPPERCASE (ex: settings.capital_inicial)."""
        upper_name =name .upper ()
        if upper_name in self .__dict__ :
            return self .__dict__ [upper_name ]
        raise AttributeError (f"'{self .__class__ .__name__ }' object has no attribute '{name }'")

    def __repr__ (self )->str :
        mode ="SIMULAÇÃO"if self .SIMULATION_MODE else "PRODUÇÃO"
        return (
        f"Settings(mode={mode }, capital=R${self .CAPITAL_INICIAL :.2f}, "
        f"stop_loss={self .STOP_LOSS_PERCENT :.0%}, "
        f"watchlist={len (self .WATCHLIST )} ativos, "
        f"sentiment={self .SENTIMENT_MODE })"
        )

settings :Final [Settings ]=Settings ()
