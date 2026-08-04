"""
Motor de análise técnica do Projeto Córtex.

Calcula indicadores quantitativos (EMAs, RSI, suporte/resistência)
e gera sinais de tendência para cada ativo da watchlist.
Utiliza pandas_ta para cálculo de indicadores técnicos.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional ,Any

import numpy as np
import pandas as pd
import pandas_ta as ta

from utils .logger import get_logger
from utils .helpers import format_brl ,format_number

logger =get_logger ('analysis.technical')

class TrendSignal (Enum ):
    """Sinal de tendência gerado pela análise técnica."""

    STRONG_BUY ='STRONG_BUY'
    BUY ='BUY'
    NEUTRAL ='NEUTRAL'
    SELL ='SELL'
    STRONG_SELL ='STRONG_SELL'

@dataclass
class TechnicalResult :
    """Resultado completo da análise técnica de um ativo."""

    signal :TrendSignal
    ema_9 :float
    ema_21 :float
    ema_50 :float
    rsi :float
    support :float
    resistance :float
    confidence :float
    reasoning :str

class TechnicalAnalyzer :
    """
    Motor de análise técnica quantitativa do Córtex.

    Calcula EMAs (9, 21, 50), RSI (14), suporte e resistência
    baseados em pivôs, e gera sinais de tendência com confiança
    e raciocínio explicativo em português.
    """

    DEFAULT_EMA_PERIODS :tuple [int ,...]=(9 ,21 ,50 )

    DEFAULT_RSI_PERIOD :int =14

    DEFAULT_SR_WINDOW :int =20

    REQUIRED_COLUMNS :set [str ]={'Open','High','Low','Close','Volume'}

    def __init__ (
    self ,
    ema_periods :Optional [list [int ]]=None ,
    rsi_period :int =DEFAULT_RSI_PERIOD ,
    sr_window :int =DEFAULT_SR_WINDOW ,
    )->None :
        """
        Inicializa o analisador técnico.

        Args:
            ema_periods: Períodos das EMAs a calcular (padrão: [9, 21, 50]).
            rsi_period: Período do RSI (padrão: 14).
            sr_window: Janela de períodos para suporte/resistência (padrão: 20).
        """
        self .ema_periods =ema_periods or list (self .DEFAULT_EMA_PERIODS )
        self .rsi_period =rsi_period
        self .sr_window =sr_window
        logger .info (
        'TechnicalAnalyzer inicializado — EMAs: %s, RSI: %d, S/R janela: %d',
        self .ema_periods ,self .rsi_period ,self .sr_window ,
        )

    def analyze (self ,ticker :str ,df :Any )->TechnicalResult :
        """
        Executa análise técnica completa para um ativo.

        Pipeline:
            1. Validação do DataFrame de entrada
            2. Cálculo das EMAs (9, 21, 50)
            3. Cálculo do RSI (14)
            4. Identificação de suporte e resistência
            5. Determinação do sinal de tendência
            6. Geração do raciocínio explicativo

        Args:
            ticker: Código do ativo (ex: 'PETR4').
            df: DataFrame com colunas OHLCV (Open, High, Low, Close, Volume).

        Returns:
            TechnicalResult com sinal, indicadores e raciocínio.

        Raises:
            ValueError: Se o DataFrame não tiver colunas obrigatórias
                        ou dados insuficientes.
        """
        if isinstance (df ,list ):
            data =[]
            for item in df :
                data .append ({
                'Open':getattr (item ,'open',0.0 ),
                'High':getattr (item ,'high',0.0 ),
                'Low':getattr (item ,'low',0.0 ),
                'Close':getattr (item ,'close',0.0 ),
                'Volume':getattr (item ,'volume',0 ),
                'Timestamp':getattr (item ,'timestamp',None )
                })
            df =pd .DataFrame (data )

        logger .debug ('Iniciando análise técnica para %s (%d candles)',ticker ,len (df ))

        self ._validate_dataframe (df ,ticker )

        df_work =df .copy ()

        self .calculate_ema (df_work ,self .ema_periods )
        self .calculate_rsi (df_work ,self .rsi_period )
        support ,resistance =self .find_support_resistance (df_work ,self .sr_window )

        last_row =df_work .iloc [-1 ]
        ema_9 =float (last_row .get ('EMA_9',0.0 ))
        ema_21 =float (last_row .get ('EMA_21',0.0 ))
        ema_50 =float (last_row .get ('EMA_50',0.0 ))
        rsi =float (last_row .get ('RSI',50.0 ))

        signal =self ._determine_signal (ema_9 ,ema_21 ,ema_50 ,rsi )
        confidence =self ._calculate_signal_confidence (signal ,ema_9 ,ema_21 ,ema_50 ,rsi )

        result =TechnicalResult (
        signal =signal ,
        ema_9 =round (ema_9 ,2 ),
        ema_21 =round (ema_21 ,2 ),
        ema_50 =round (ema_50 ,2 ),
        rsi =round (rsi ,2 ),
        support =round (support ,2 ),
        resistance =round (resistance ,2 ),
        confidence =round (confidence ,4 ),
        reasoning ='',
        )

        result .reasoning =self ._generate_reasoning (ticker ,result )

        logger .info (
        'Análise técnica %s: sinal=%s, confiança=%.2f, RSI=%.1f',
        ticker ,signal .value ,confidence ,rsi ,
        )

        return result

    def calculate_ema (
    self ,df :pd .DataFrame ,periods :Optional [list [int ]]=None
    )->pd .DataFrame :
        """
        Calcula Médias Móveis Exponenciais (EMAs) usando pandas_ta.

        Args:
            df: DataFrame com coluna 'Close'.
            periods: Lista de períodos para as EMAs (padrão: [9, 21, 50]).

        Returns:
            DataFrame com colunas EMA_<período> adicionadas.
        """
        periods =periods or self .ema_periods
        for period in periods :
            col_name =f'EMA_{period }'
            ema_series =ta .ema (df ['Close'],length =period )
            if ema_series is not None :
                df [col_name ]=ema_series
            else :
                logger .warning (
                'EMA(%d) retornou None — dados insuficientes?',period
                )
                df [col_name ]=np .nan
        return df

    def calculate_rsi (
    self ,df :pd .DataFrame ,period :Optional [int ]=None
    )->pd .DataFrame :
        """
        Calcula o Índice de Força Relativa (RSI) usando pandas_ta.

        Args:
            df: DataFrame com coluna 'Close'.
            period: Período do RSI (padrão: 14).

        Returns:
            DataFrame com coluna 'RSI' adicionada.
        """
        period =period or self .rsi_period
        rsi_series =ta .rsi (df ['Close'],length =period )
        if rsi_series is not None :
            df ['RSI']=rsi_series
        else :
            logger .warning ('RSI(%d) retornou None — dados insuficientes?',period )
            df ['RSI']=np .nan
        return df

    def find_support_resistance (
    self ,df :pd .DataFrame ,window :Optional [int ]=None
    )->tuple [float ,float ]:
        """
        Identifica níveis de suporte e resistência baseados em pivôs.

        Utiliza rolling min/max sobre as colunas Low e High
        dentro da janela especificada.

        Args:
            df: DataFrame com colunas 'High' e 'Low'.
            window: Janela de períodos para cálculo (padrão: 20).

        Returns:
            Tupla (suporte, resistência) em valores absolutos.
        """
        window =window or self .sr_window

        effective_window =min (window ,len (df ))
        if effective_window <2 :
            last_close =float (df ['Close'].iloc [-1 ])
            logger .warning (
            'Dados insuficientes para S/R (janela=%d, disponível=%d). '
            'Usando preço atual como fallback.',
            window ,len (df ),
            )
            return last_close *0.95 ,last_close *1.05

        recent =df .tail (effective_window )
        support =float (recent ['Low'].min ())
        resistance =float (recent ['High'].max ())

        logger .debug (
        'Suporte: %s, Resistência: %s (janela=%d)',
        format_brl (support ),format_brl (resistance ),effective_window ,
        )

        return support ,resistance

    def _validate_dataframe (self ,df :pd .DataFrame ,ticker :str )->None :
        """
        Valida se o DataFrame possui colunas e dados suficientes.

        Args:
            df: DataFrame a validar.
            ticker: Código do ativo (para mensagens de erro).

        Raises:
            ValueError: Se faltar colunas ou dados insuficientes.
        """
        if df is None or df .empty :
            raise ValueError (
            f'DataFrame vazio ou nulo para {ticker }. '
            'Não é possível executar análise técnica.'
            )

        missing_cols =self .REQUIRED_COLUMNS -set (df .columns )
        if missing_cols :
            raise ValueError (
            f'DataFrame de {ticker } não possui colunas obrigatórias: '
            f'{missing_cols }. Colunas presentes: {list (df .columns )}'
            )

        min_required =max (self .ema_periods )+1
        if len (df )<min_required :
            raise ValueError (
            f'Dados insuficientes para {ticker }: {len (df )} candles '
            f'(mínimo necessário: {min_required } para EMA({max (self .ema_periods )}))'
            )

    def _determine_signal (
    self ,ema_9 :float ,ema_21 :float ,ema_50 :float ,rsi :float
    )->TrendSignal :
        """
        Determina o sinal de tendência baseado nas EMAs e RSI.

        Lógica de sinais:
            - STRONG_BUY:  EMA9 > EMA21 > EMA50 E RSI < 45 (tendência + espaço p/ alta)
            - BUY:         EMA9 > EMA21 E RSI < 60
            - NEUTRAL:     Sem alinhamento claro ou RSI entre 40-60
            - SELL:        EMA9 < EMA21 E RSI > 60
            - STRONG_SELL: EMA9 < EMA21 < EMA50 E RSI > 55 (tendência + pressão de baixa)

        Args:
            ema_9: Valor atual da EMA(9).
            ema_21: Valor atual da EMA(21).
            ema_50: Valor atual da EMA(50).
            rsi: Valor atual do RSI.

        Returns:
            TrendSignal correspondente à análise.
        """

        if any (np .isnan (v )for v in [ema_9 ,ema_21 ,ema_50 ,rsi ]):
            logger .warning ('Valores NaN detectados nos indicadores — sinal NEUTRAL')
            return TrendSignal .NEUTRAL

        if ema_9 >ema_21 >ema_50 and rsi <45 :
            return TrendSignal .STRONG_BUY

        if ema_9 <ema_21 <ema_50 and rsi >55 :
            return TrendSignal .STRONG_SELL

        if ema_9 >ema_21 and rsi <60 :
            return TrendSignal .BUY

        if ema_9 <ema_21 and rsi >60 :
            return TrendSignal .SELL

        return TrendSignal .NEUTRAL

    def _calculate_signal_confidence (
    self ,
    signal :TrendSignal ,
    ema_9 :float ,
    ema_21 :float ,
    ema_50 :float ,
    rsi :float ,
    )->float :
        """
        Calcula nível de confiança do sinal (0.0 a 1.0).

        A confiança é maior quando:
            - Todas as EMAs estão alinhadas na direção do sinal
            - O RSI está em zona extrema coerente com o sinal
            - A distância entre EMAs é significativa

        Args:
            signal: Sinal determinado.
            ema_9: Valor da EMA(9).
            ema_21: Valor da EMA(21).
            ema_50: Valor da EMA(50).
            rsi: Valor do RSI.

        Returns:
            Confiança entre 0.0 e 1.0.
        """
        if signal ==TrendSignal .STRONG_BUY :

            base =0.80
            rsi_bonus =max (0.0 ,(35.0 -rsi )/35.0 )*0.15

            spread =(ema_9 -ema_50 )/ema_50 if ema_50 >0 else 0.0
            spread_bonus =min (abs (spread )*2.0 ,0.05 )
            return min (1.0 ,base +rsi_bonus +spread_bonus )

        if signal ==TrendSignal .BUY :
            base =0.55

            rsi_bonus =max (0.0 ,(60.0 -rsi )/60.0 )*0.20

            alignment_bonus =0.10 if ema_9 >ema_21 >ema_50 else 0.0
            return min (1.0 ,base +rsi_bonus +alignment_bonus )

        if signal ==TrendSignal .NEUTRAL :
            return 0.30

        if signal ==TrendSignal .SELL :
            base =0.55
            rsi_bonus =max (0.0 ,(rsi -60.0 )/40.0 )*0.20
            alignment_bonus =0.10 if ema_9 <ema_21 <ema_50 else 0.0
            return min (1.0 ,base +rsi_bonus +alignment_bonus )

        if signal ==TrendSignal .STRONG_SELL :
            base =0.80
            rsi_bonus =max (0.0 ,(rsi -70.0 )/30.0 )*0.15
            spread =(ema_50 -ema_9 )/ema_50 if ema_50 >0 else 0.0
            spread_bonus =min (abs (spread )*2.0 ,0.05 )
            return min (1.0 ,base +rsi_bonus +spread_bonus )

        return 0.30

    def _generate_reasoning (self ,ticker :str ,result :TechnicalResult )->str :
        """
        Gera texto explicativo em português sobre a análise técnica.

        Descreve a posição das EMAs, zona do RSI e níveis de
        suporte/resistência de forma compreensível.

        Args:
            ticker: Código do ativo.
            result: Resultado da análise técnica.

        Returns:
            Texto explicativo em português.
        """
        parts :list [str ]=[]

        ema9_str =format_brl (result .ema_9 )
        ema21_str =format_brl (result .ema_21 )
        ema50_str =format_brl (result .ema_50 )

        if result .ema_9 >result .ema_21 :
            parts .append (
            f'EMA(9) em {ema9_str } cruzou acima da EMA(21) em {ema21_str }, '
            f'indicando momentum de alta.'
            )
        elif result .ema_9 <result .ema_21 :
            parts .append (
            f'EMA(9) em {ema9_str } está abaixo da EMA(21) em {ema21_str }, '
            f'indicando momentum de baixa.'
            )
        else :
            parts .append (
            f'EMA(9) em {ema9_str } e EMA(21) em {ema21_str } estão convergindo.'
            )

        if result .ema_9 >result .ema_21 >result .ema_50 :
            parts .append (
            f'EMA(50) em {ema50_str } confirma tendência altista com alinhamento completo.'
            )
        elif result .ema_9 <result .ema_21 <result .ema_50 :
            parts .append (
            f'EMA(50) em {ema50_str } confirma tendência baixista com alinhamento completo.'
            )
        else :
            parts .append (f'EMA(50) em {ema50_str } — tendência mista.')

        rsi_str =format_number (result .rsi ,1 )
        if result .rsi <30 :
            parts .append (
            f'RSI em {rsi_str } — zona de sobrevendido extremo, '
            f'possível reversão de alta.'
            )
        elif result .rsi <35 :
            parts .append (
            f'RSI em {rsi_str } — zona de sobrevendido, '
            f'sinaliza oportunidade de compra.'
            )
        elif result .rsi <45 :
            parts .append (
            f'RSI em {rsi_str } — levemente abaixo do neutro, '
            f'espaço para valorização.'
            )
        elif result .rsi <=55 :
            parts .append (
            f'RSI em {rsi_str } — zona neutra, sem pressão direcional.'
            )
        elif result .rsi <=60 :
            parts .append (
            f'RSI em {rsi_str } — levemente acima do neutro.'
            )
        elif result .rsi <=70 :
            parts .append (
            f'RSI em {rsi_str } — pressão de alta, atenção à sobrecompra.'
            )
        else :
            parts .append (
            f'RSI em {rsi_str } — zona de sobrecomprado, '
            f'risco de correção.'
            )

        sup_str =format_brl (result .support )
        res_str =format_brl (result .resistance )
        parts .append (f'Suporte em {sup_str }, resistência em {res_str }.')

        return ' '.join (parts )
