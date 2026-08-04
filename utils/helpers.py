"""
Funções utilitárias do Projeto Córtex.

Fornece helpers para formatação monetária, manipulação de datas,
cálculos financeiros e conversão de tickers.
"""

from datetime import datetime ,timezone ,timedelta
from typing import Final

BRT :Final [timezone ]=timezone (timedelta (hours =-3 ),name ="BRT")

def format_brl (value :float )->str :
    """
    Formata um valor numérico para o padrão monetário brasileiro.

    Args:
        value: Valor em reais.

    Returns:
        String formatada no padrão 'R$ 1.234,56'.

    Exemplos::

        >>> format_brl(1234.56)
        'R$ 1.234,56'
        >>> format_brl(-50.1)
        'R$ -50,10'
        >>> format_brl(0)
        'R$ 0,00'
    """
    formatted =f"{abs (value ):,.2f}".replace (',','X').replace ('.',',').replace ('X','.')
    sign ='-'if value <0 else ''
    return f"{sign }R$ {formatted }"

def get_brt_now ()->datetime :
    """
    Retorna o datetime atual com timezone BRT (UTC-3).

    Returns:
        Datetime timezone-aware no horário de Brasília.
    """
    return datetime .now (tz =BRT )

def percentage_change (old :float ,new :float )->float :
    """
    Calcula a variação percentual entre dois valores.

    Args:
        old: Valor original (denominador).
        new: Valor atual (numerador).

    Returns:
        Variação percentual como float (ex: 10.5 para +10.5%).
        Retorna 0.0 se o valor original for zero.

    Exemplos::

        >>> percentage_change(100.0, 110.0)
        10.0
        >>> percentage_change(200.0, 180.0)
        -10.0
    """
    if old ==0.0 :
        return 0.0
    return ((new -old )/old )*100.0

def safe_division (a :float ,b :float ,default :float =0.0 )->float :
    """
    Divisão segura que retorna um valor padrão ao dividir por zero.

    Args:
        a: Numerador.
        b: Denominador.
        default: Valor retornado quando b é zero.

    Returns:
        Resultado da divisão ou o valor padrão.

    Exemplos::

        >>> safe_division(10.0, 3.0)
        3.3333333333333335
        >>> safe_division(10.0, 0.0)
        0.0
        >>> safe_division(10.0, 0.0, default=-1.0)
        -1.0
    """
    if b ==0.0 :
        return default
    return a /b

def ensure_fractional_ticker (ticker :str )->str :
    """
    Garante que o ticker tenha o sufixo 'F' para mercado fracionário.

    No mercado fracionário da B3, os tickers recebem sufixo 'F'
    (ex: PETR4F) para negociação de 1 a 99 ações.

    Args:
        ticker: Código do ativo (ex: 'PETR4').

    Returns:
        Ticker com sufixo 'F' se ainda não presente.

    Exemplos::

        >>> ensure_fractional_ticker('PETR4')
        'PETR4F'
        >>> ensure_fractional_ticker('PETR4F')
        'PETR4F'
        >>> ensure_fractional_ticker('BPAC11')
        'BPAC11F'
    """
    ticker =ticker .strip ().upper ()
    if not ticker .endswith ("F"):
        return f"{ticker }F"
    return ticker

def to_yfinance_ticker (ticker :str )->str :
    """
    Converte ticker da B3 para o formato do Yahoo Finance.

    Adiciona o sufixo '.SA' usado pelo yfinance para ações brasileiras.
    Remove sufixo 'F' (fracionário) antes da conversão, se presente.

    Args:
        ticker: Código do ativo na B3 (ex: 'PETR4' ou 'PETR4F').

    Returns:
        Ticker no formato Yahoo Finance (ex: 'PETR4.SA').

    Exemplos::

        >>> to_yfinance_ticker('PETR4')
        'PETR4.SA'
        >>> to_yfinance_ticker('PETR4F')
        'PETR4.SA'
        >>> to_yfinance_ticker('BPAC11')
        'BPAC11.SA'
    """
    ticker =ticker .strip ().upper ()

    if ticker .endswith ("F")and not ticker .endswith ("SA"):

        base =ticker [:-1 ]
        if base and base [-1 ].isdigit ():
            ticker =base

    if not ticker .endswith (".SA"):
        return f"{ticker }.SA"
    return ticker

def format_timestamp (dt :datetime )->str :
    """
    Formata datetime para string legível no padrão brasileiro.

    Args:
        dt: Datetime a ser formatado (preferencialmente timezone-aware).

    Returns:
        String no formato 'DD/MM/YYYY HH:MM:SS BRT'.

    Exemplos::

        >>> from datetime import datetime, timezone, timedelta
        >>> brt = timezone(timedelta(hours=-3))
        >>> dt = datetime(2026, 7, 9, 14, 30, 0, tzinfo=brt)
        >>> format_timestamp(dt)
        '09/07/2026 14:30:00 BRT'
    """

    if dt .tzinfo is not None :
        dt =dt .astimezone (BRT )

    return dt .strftime ("%d/%m/%Y %H:%M:%S")+" BRT"

def truncate_text (text :str ,max_len :int =200 )->str :
    """
    Trunca texto para o comprimento máximo especificado.

    Se o texto exceder max_len, ele é cortado e recebe reticências.

    Args:
        text: Texto a ser truncado.
        max_len: Comprimento máximo permitido (padrão: 200).

    Returns:
        Texto truncado com '...' se necessário, ou o texto original.

    Exemplos::

        >>> truncate_text("Texto curto", 200)
        'Texto curto'
        >>> truncate_text("Texto longo" * 50, 20)
        'Texto longoTexto l...'
    """
    if not text :
        return ""
    text =text .strip ()
    if len (text )<=max_len :
        return text
    return text [:max_len -3 ]+"..."

def format_percent (value :float ,decimals :int =2 )->str :
    """
    Formata valor como percentual no padrão brasileiro.

    Args:
        value: Valor numérico (0.10 = 10%).
        decimals: Casas decimais.

    Returns:
        String formatada, ex: '10,50%'.

    Exemplos::

        >>> format_percent(0.10)
        '10,00%'
        >>> format_percent(0.0542, 1)
        '5,4%'
    """
    pct =value *100
    formatted =f"{pct :.{decimals }f}".replace (".",",")
    return f"{formatted }%"

def format_number (value :float ,decimals :int =2 )->str :
    """
    Formata número decimal no padrão brasileiro.

    Args:
        value: Valor numérico.
        decimals: Casas decimais.

    Returns:
        String formatada, ex: '1.234,56'.

    Exemplos::

        >>> format_number(1234.56)
        '1.234,56'
        >>> format_number(42.5, 1)
        '42,5'
    """
    formatted =f"{value :,.{decimals }f}".replace (",","X").replace (".",",").replace ("X",".")
    return formatted

def clamp (value :float ,min_val :float ,max_val :float )->float :
    """
    Limita valor entre mínimo e máximo.

    Args:
        value: Valor a limitar.
        min_val: Limite inferior.
        max_val: Limite superior.

    Returns:
        Valor limitado ao intervalo [min_val, max_val].

    Exemplos::

        >>> clamp(1.5, 0.0, 1.0)
        1.0
        >>> clamp(-0.5, 0.0, 1.0)
        0.0
        >>> clamp(0.5, 0.0, 1.0)
        0.5
    """
    return max (min_val ,min (max_val ,value ))
