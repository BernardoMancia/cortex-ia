"""
Fixtures compartilhadas para os testes do Projeto Córtex.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT =Path (__file__ ).resolve ().parent .parent
if str (PROJECT_ROOT )not in sys .path :
    sys .path .insert (0 ,str (PROJECT_ROOT ))

from models .data_models import Action ,BRT ,Decision ,OHLCV ,Position

@pytest .fixture
def sample_position ()->Position :
    """Posição de exemplo para testes."""
    return Position (
    ticker ='PETR4',
    quantity =10 ,
    entry_price =30.00 ,
    stop_loss =27.00 ,
    current_price =30.00 ,
    )

@pytest .fixture
def sample_position_below_sl ()->Position :
    """Posição com preço abaixo do stop-loss."""
    return Position (
    ticker ='VALE3',
    quantity =5 ,
    entry_price =60.00 ,
    stop_loss =54.00 ,
    current_price =53.00 ,
    )

@pytest .fixture
def sample_decision_buy ()->Decision :
    """Decisão de compra de exemplo."""
    return Decision (
    ticker ='WEGE3',
    action =Action .BUY ,
    confidence =0.75 ,
    reasoning ='Convergência técnica + sentimento positivo',
    technical_score =0.8 ,
    sentiment_score =0.5 ,
    quantity =5 ,
    price =40.00 ,
    stop_loss =36.00 ,
    )

@pytest .fixture
def sample_ohlcv_data ()->list [OHLCV ]:
    """Dados OHLCV de exemplo com 30 candles para testes técnicos."""
    from datetime import datetime ,timedelta

    base_prices =[
    100.0 ,101.5 ,102.0 ,101.0 ,103.0 ,
    104.5 ,103.5 ,105.0 ,106.0 ,104.0 ,
    105.5 ,107.0 ,106.5 ,108.0 ,109.0 ,
    108.5 ,110.0 ,111.0 ,109.5 ,112.0 ,
    113.0 ,112.5 ,114.0 ,115.0 ,113.5 ,
    116.0 ,117.0 ,116.5 ,118.0 ,119.0 ,
    ]*2

    candles :list [OHLCV ]=[]
    base_time =datetime (2026 ,6 ,1 ,10 ,0 ,tzinfo =BRT )

    for i ,close in enumerate (base_prices ):
        candle =OHLCV (
        ticker ='PETR4',
        timestamp =base_time +timedelta (days =i ),
        open =close -0.5 ,
        high =close +1.0 ,
        low =close -1.0 ,
        close =close ,
        volume =1_000_000 +i *10_000 ,
        )
        candles .append (candle )

    return candles

@pytest .fixture
def bearish_ohlcv_data ()->list [OHLCV ]:
    """Dados OHLCV com tendência de baixa para testes de venda."""
    from datetime import datetime ,timedelta

    base_prices =[
    120.0 ,119.0 ,118.5 ,117.0 ,116.0 ,
    115.5 ,114.0 ,113.0 ,112.5 ,111.0 ,
    110.0 ,109.5 ,108.0 ,107.0 ,106.5 ,
    105.0 ,104.0 ,103.5 ,102.0 ,101.0 ,
    100.5 ,99.0 ,98.0 ,97.5 ,96.0 ,
    94.0 ,93.0 ,92.5 ,91.0 ,90.0 ,
    ]*2

    candles :list [OHLCV ]=[]
    base_time =datetime (2026 ,6 ,1 ,10 ,0 ,tzinfo =BRT )

    for i ,close in enumerate (base_prices ):
        candle =OHLCV (
        ticker ='MGLU3',
        timestamp =base_time +timedelta (days =i ),
        open =close +0.5 ,
        high =close +1.0 ,
        low =close -1.0 ,
        close =close ,
        volume =500_000 +i *5_000 ,
        )
        candles .append (candle )

    return candles
