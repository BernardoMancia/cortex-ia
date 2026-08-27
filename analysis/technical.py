"""
Motor de análise técnica do Projeto Córtex.

Calcula indicadores quantitativos (EMAs, RSI, suporte/resistência)
e gera sinais de tendência para cada ativo da watchlist.
Utiliza pandas_ta para cálculo de indicadores técnicos.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Any

import numpy as np
import pandas as pd
import pandas_ta as ta

from utils.logger import get_logger
from utils.helpers import format_brl, format_number

logger = get_logger('analysis.technical')

class TrendSignal(Enum):
    """Sinal de tendência gerado pela análise técnica."""

    STRONG_BUY = 'STRONG_BUY'
    BUY = 'BUY'
    NEUTRAL = 'NEUTRAL'
    SELL = 'SELL'
    STRONG_SELL = 'STRONG_SELL'

@dataclass
class TechnicalResult:
    """Resultado completo da análise técnica de um ativo."""

    signal: TrendSignal = TrendSignal.NEUTRAL
    ema_9: float = 0.0
    ema_21: float = 0.0
    ema_50: float = 0.0
    rsi: float = 50.0
    support: float = 0.0
    resistance: float = 0.0
    macd_hist: float = 0.0
    atr: float = 0.0
    rel_vol: float = 1.0
    bb_lower: float = 0.0
    bb_upper: float = 0.0
    confidence: float = 0.0
    reasoning: str = ''

class TechnicalAnalyzer:
    """
    Motor de análise técnica quantitativa do Córtex.

    Calcula EMAs (9, 21, 50), RSI (14), suporte e resistência
    baseados em pivôs, e gera sinais de tendência com confiança
    e raciocínio explicativo em português.
    """

    DEFAULT_EMA_PERIODS: tuple[int, ...] = (9, 21, 50)
    DEFAULT_RSI_PERIOD: int = 14
    DEFAULT_SR_WINDOW: int = 20

    REQUIRED_COLUMNS: set[str] = {'Open', 'High', 'Low', 'Close', 'Volume'}

    def __init__(
        self,
        ema_periods: Optional[list[int]] = None,
        rsi_period: int = DEFAULT_RSI_PERIOD,
        sr_window: int = DEFAULT_SR_WINDOW,
    ) -> None:
        """
        Inicializa o analisador técnico.

        Args:
            ema_periods: Períodos das EMAs a calcular (padrão: [9, 21, 50]).
            rsi_period: Período do RSI (padrão: 14).
            sr_window: Janela de períodos para suporte/resistência (padrão: 20).
        """
        self.ema_periods = ema_periods or list(self.DEFAULT_EMA_PERIODS)
        self.rsi_period = rsi_period
        self.sr_window = sr_window
        logger.info(
            'TechnicalAnalyzer inicializado — EMAs: %s, RSI: %d, S/R janela: %d',
            self.ema_periods, self.rsi_period, self.sr_window,
        )

    def analyze(self, ticker: str, df: Any) -> TechnicalResult:
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
        if isinstance(df, list):
            data = []
            for item in df:
                data.append({
                    'Open': getattr(item, 'open', 0.0),
                    'High': getattr(item, 'high', 0.0),
                    'Low': getattr(item, 'low', 0.0),
                    'Close': getattr(item, 'close', 0.0),
                    'Volume': getattr(item, 'volume', 0),
                    'Timestamp': getattr(item, 'timestamp', None)
                })
            df = pd.DataFrame(data)

        logger.debug('Iniciando análise técnica para %s (%d candles)', ticker, len(df))

        self._validate_dataframe(df, ticker)

        df_work = df.copy()

        self.calculate_ema(df_work, self.ema_periods)
        self.calculate_rsi(df_work, self.rsi_period)
        self.calculate_macd(df_work)
        self.calculate_atr(df_work)
        self.calculate_relative_volume(df_work)
        self.calculate_bollinger_bands(df_work)
        support, resistance = self.find_support_resistance(df_work, self.sr_window)

        last_row = df_work.iloc[-1]
        current_price = float(last_row.get('Close', 0.0))
        ema_9 = float(last_row.get('EMA_9', 0.0))
        ema_21 = float(last_row.get('EMA_21', 0.0))
        ema_50 = float(last_row.get('EMA_50', 0.0))
        rsi = float(last_row.get('RSI', 50.0))
        macd_hist = float(last_row.get('MACD_HIST', 0.0))
        atr = float(last_row.get('ATR', 0.0))
        rel_vol = float(last_row.get('REL_VOL', 1.0))
        bb_lower = float(last_row.get('BB_LOWER', 0.0))
        bb_upper = float(last_row.get('BB_UPPER', 0.0))

        signal = self._determine_signal(
            current_price, ema_9, ema_21, ema_50, rsi, macd_hist, rel_vol, bb_lower, bb_upper
        )
        confidence = self._calculate_signal_confidence(
            signal, ema_9, ema_21, ema_50, rsi, macd_hist, rel_vol
        )

        result = TechnicalResult(
            signal=signal,
            ema_9=round(ema_9, 2),
            ema_21=round(ema_21, 2),
            ema_50=round(ema_50, 2),
            rsi=round(rsi, 2),
            support=round(support, 2),
            resistance=round(resistance, 2),
            macd_hist=round(macd_hist, 4),
            atr=round(atr, 2),
            rel_vol=round(rel_vol, 2),
            bb_lower=round(bb_lower, 2),
            bb_upper=round(bb_upper, 2),
            confidence=round(confidence, 4),
            reasoning='',
        )

        result.reasoning = self._generate_reasoning(ticker, result)

        logger.info(
            'Análise técnica %s: sinal=%s, confiança=%.2f, RSI=%.1f',
            ticker, signal.value, confidence, rsi,
        )

        return result

    def calculate_ema(
        self, df: pd.DataFrame, periods: Optional[list[int]] = None
    ) -> pd.DataFrame:
        """
        Calcula Médias Móveis Exponenciais (EMAs) usando pandas_ta.

        Args:
            df: DataFrame com coluna 'Close'.
            periods: Lista de períodos para as EMAs (padrão: [9, 21, 50]).

        Returns:
            DataFrame com colunas EMA_<período> adicionadas.
        """
        periods = periods or self.ema_periods
        for period in periods:
            col_name = f'EMA_{period}'
            ema_series = ta.ema(df['Close'], length=period)
            if ema_series is not None:
                df[col_name] = ema_series
            else:
                logger.warning(
                    'EMA(%d) retornou None — dados insuficientes?', period
                )
                df[col_name] = np.nan
        return df

    def calculate_rsi(
        self, df: pd.DataFrame, period: Optional[int] = None
    ) -> pd.DataFrame:
        """
        Calcula o Índice de Força Relativa (RSI) usando pandas_ta.

        Args:
            df: DataFrame com coluna 'Close'.
            period: Período do RSI (padrão: 14).

        Returns:
            DataFrame com coluna 'RSI' adicionada.
        """
        period = period or self.rsi_period
        rsi_series = ta.rsi(df['Close'], length=period)
        if rsi_series is not None:
            df['RSI'] = rsi_series
        else:
            logger.warning('RSI(%d) retornou None — dados insuficientes?', period)
            df['RSI'] = np.nan
        return df

    def calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcula o MACD(12, 26, 9) usando pandas_ta."""
        macd_df = ta.macd(df['Close'], fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            col = [c for c in macd_df.columns if c.startswith('MACDh_')]
            if col:
                df['MACD_HIST'] = macd_df[col[0]]
            else:
                df['MACD_HIST'] = np.nan
        else:
            logger.warning('MACD retornou None — dados insuficientes?')
            df['MACD_HIST'] = np.nan
        return df

    def calculate_bollinger_bands(self, df: pd.DataFrame, length: int = 20, std: float = 2.0) -> pd.DataFrame:
        """Calcula as Bandas de Bollinger usando pandas_ta."""
        bb_df = ta.bbands(df['Close'], length=length, std=std)
        if bb_df is not None and not bb_df.empty:
            lower_col = [c for c in bb_df.columns if c.startswith('BBL_')]
            upper_col = [c for c in bb_df.columns if c.startswith('BBU_')]
            df['BB_LOWER'] = bb_df[lower_col[0]] if lower_col else np.nan
            df['BB_UPPER'] = bb_df[upper_col[0]] if upper_col else np.nan
        else:
            logger.warning('Bollinger Bands retornou None — dados insuficientes?')
            df['BB_LOWER'] = np.nan
            df['BB_UPPER'] = np.nan
        return df

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Calcula o ATR(14) para medir a volatilidade."""
        atr_series = ta.atr(df['High'], df['Low'], df['Close'], length=period)
        if atr_series is not None:
            df['ATR'] = atr_series
        else:
            logger.warning('ATR(%d) retornou None', period)
            df['ATR'] = np.nan
        return df

    def calculate_relative_volume(self, df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
        """Calcula a SMA do Volume e o Volume Relativo."""
        df['SMA_VOL'] = df['Volume'].rolling(period).mean()
        df['REL_VOL'] = np.where(df['SMA_VOL'] > 0, df['Volume'] / df['SMA_VOL'], 1.0)
        return df

    def find_support_resistance(
        self, df: pd.DataFrame, window: Optional[int] = None
    ) -> tuple[float, float]:
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
        window = window or self.sr_window

        effective_window = min(window, len(df))
        if effective_window < 2:
            last_close = float(df['Close'].iloc[-1])
            logger.warning(
                'Dados insuficientes para S/R (janela=%d, disponível=%d). '
                'Usando preço atual como fallback.',
                window, len(df),
            )
            return last_close * 0.95, last_close * 1.05

        recent = df.tail(effective_window)
        support = float(recent['Low'].min())
        resistance = float(recent['High'].max())

        logger.debug(
            'Suporte: %s, Resistência: %s (janela=%d)',
            format_brl(support), format_brl(resistance), effective_window,
        )

        return support, resistance

    def _validate_dataframe(self, df: pd.DataFrame, ticker: str) -> None:
        """
        Valida se o DataFrame possui colunas e dados suficientes.

        Args:
            df: DataFrame a validar.
            ticker: Código do ativo (para mensagens de erro).

        Raises:
            ValueError: Se faltar colunas ou dados insuficientes.
        """
        if df is None or df.empty:
            raise ValueError(
                f'DataFrame vazio ou nulo para {ticker}. '
                'Não é possível executar análise técnica.'
            )

        missing_cols = self.REQUIRED_COLUMNS - set(df.columns)
        if missing_cols:
            raise ValueError(
                f'DataFrame de {ticker} não possui colunas obrigatórias: '
                f'{missing_cols}. Colunas presentes: {list(df.columns)}'
            )

        min_required = max(self.ema_periods) + 1
        if len(df) < min_required:
            raise ValueError(
                f'Dados insuficientes para {ticker}: {len(df)} candles '
                f'(mínimo necessário: {min_required} para EMA({max(self.ema_periods)}))'
            )

    def _determine_signal(
        self, current_price: float, ema_9: float, ema_21: float, ema_50: float, 
        rsi: float, macd_hist: float, rel_vol: float, bb_lower: float, bb_upper: float
    ) -> TrendSignal:
        """
        Determina o sinal de tendência baseado nas EMAs, RSI, MACD e Volume, com filtro de BB.

        Lógica de sinais:
            - STRONG_BUY:  EMA9 > EMA21 > EMA50 E RSI < 45 E MACD > 0 E Vol > 1.2x
            - BUY:         EMA9 > EMA21 E RSI < 60 E MACD > 0
            - NEUTRAL:     Sem alinhamento claro ou sem volume
            - SELL:        EMA9 < EMA21 E RSI > 60 E MACD < 0
            - STRONG_SELL: EMA9 < EMA21 < EMA50 E RSI > 55 E MACD < 0 E Vol > 1.2x
        """
        if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in [ema_9, ema_21, ema_50, rsi, macd_hist, rel_vol, bb_lower, bb_upper]):
            logger.warning('Valores NaN detectados nos indicadores — sinal NEUTRAL')
            return TrendSignal.NEUTRAL

        signal = TrendSignal.NEUTRAL

        if ema_9 > ema_21 > ema_50 and current_price >= ema_9 and 35 <= rsi <= 68 and macd_hist > 0 and rel_vol >= 1.0:
            signal = TrendSignal.STRONG_BUY
        elif ema_9 < ema_21 < ema_50 and current_price <= ema_9 and 32 <= rsi <= 65 and macd_hist < 0 and rel_vol >= 1.0:
            signal = TrendSignal.STRONG_SELL
        elif ema_9 > ema_21 and current_price > ema_21 and rsi <= 65 and macd_hist > 0:
            signal = TrendSignal.BUY
        elif ema_9 < ema_21 and current_price < ema_21 and rsi >= 35 and macd_hist < 0:
            signal = TrendSignal.SELL

        if signal in (TrendSignal.BUY, TrendSignal.STRONG_BUY):
            if bb_upper > 0 and current_price > (bb_upper * 1.02):
                logger.debug("Sinal de compra pausado: preço esticado > 2% acima da Banda de Bollinger superior.")
                signal = TrendSignal.NEUTRAL
        elif signal in (TrendSignal.SELL, TrendSignal.STRONG_SELL):
            if bb_lower > 0 and current_price < (bb_lower * 0.98):
                logger.debug("Sinal de venda pausado: preço esticado > 2% abaixo da Banda de Bollinger inferior.")
                signal = TrendSignal.NEUTRAL

        return signal

    def _calculate_signal_confidence(
        self,
        signal: TrendSignal,
        ema_9: float,
        ema_21: float,
        ema_50: float,
        rsi: float,
        macd_hist: float,
        rel_vol: float,
    ) -> float:
        """
        Calcula nível de confiança do sinal (0.0 a 1.0).
        """
        if signal == TrendSignal.STRONG_BUY:
            base = 0.75
            trend_bonus = 0.10 if (ema_9 > ema_21 > ema_50) else 0.0
            rsi_bonus = 0.05 if (40.0 <= rsi <= 60.0) else 0.0
            vol_bonus = min(0.10, max(0.0, (rel_vol - 1.0) * 0.1))
            return min(1.0, base + trend_bonus + rsi_bonus + vol_bonus)

        if signal == TrendSignal.BUY:
            base = 0.60
            rsi_bonus = 0.05 if (40.0 <= rsi <= 60.0) else 0.0
            vol_bonus = min(0.10, max(0.0, (rel_vol - 1.0) * 0.05))
            macd_bonus = 0.05 if macd_hist > 0 else 0.0
            return min(0.85, base + rsi_bonus + vol_bonus + macd_bonus)

        if signal == TrendSignal.NEUTRAL:
            return 0.30

        if signal == TrendSignal.SELL:
            base = 0.60
            rsi_bonus = 0.05 if (40.0 <= rsi <= 60.0) else 0.0
            vol_bonus = min(0.10, max(0.0, (rel_vol - 1.0) * 0.05))
            macd_bonus = 0.05 if macd_hist < 0 else 0.0
            return min(0.85, base + rsi_bonus + vol_bonus + macd_bonus)

        if signal == TrendSignal.STRONG_SELL:
            base = 0.75
            trend_bonus = 0.10 if (ema_9 < ema_21 < ema_50) else 0.0
            rsi_bonus = 0.05 if (40.0 <= rsi <= 60.0) else 0.0
            vol_bonus = min(0.10, max(0.0, (rel_vol - 1.0) * 0.1))
            return min(1.0, base + trend_bonus + rsi_bonus + vol_bonus)

        return 0.30

    def _generate_reasoning(self, ticker: str, result: TechnicalResult) -> str:
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
        parts: list[str] = []

        ema9_str = format_brl(result.ema_9)
        ema21_str = format_brl(result.ema_21)
        ema50_str = format_brl(result.ema_50)

        if result.ema_9 > result.ema_21:
            parts.append(
                f'EMA(9) em {ema9_str} cruzou acima da EMA(21) em {ema21_str}, '
                f'indicando momentum de alta.'
            )
        elif result.ema_9 < result.ema_21:
            parts.append(
                f'EMA(9) em {ema9_str} está abaixo da EMA(21) em {ema21_str}, '
                f'indicando momentum de baixa.'
            )
        else:
            parts.append('EMAs sem direção definida.')

        if np.isnan(result.macd_hist):
            parts.append('MACD indisponível (dados insuficientes).')
        else:
            macd_sign = 'positivo' if result.macd_hist > 0 else 'negativo'
            parts.append(f'MACD indica momentum {macd_sign}.')

        if result.rel_vol > 1.2:
            parts.append(f'Volume alto ({result.rel_vol:.1f}x a média), confirmando o movimento.')

        if result.ema_9 > result.ema_21 > result.ema_50:
            parts.append(
                f'EMA(50) em {ema50_str} confirma tendência altista com alinhamento completo.'
            )
        elif result.ema_9 < result.ema_21 < result.ema_50:
            parts.append(
                f'EMA(50) em {ema50_str} confirma tendência baixista com alinhamento completo.'
            )
        else:
            parts.append(f'EMA(50) em {ema50_str} — tendência mista.')

        rsi_str = format_number(result.rsi, 1)
        if np.isnan(result.rsi):
            parts.append('RSI indisponível (dados insuficientes).')
        elif result.rsi < 30:
            parts.append(
                f'RSI em {rsi_str} — zona de sobrevendido extremo, '
                f'possível reversão de alta.'
            )
        elif result.rsi < 35:
            parts.append(
                f'RSI em {rsi_str} — zona de sobrevendido, '
                f'sinaliza oportunidade de compra.'
            )
        elif result.rsi < 45:
            parts.append(
                f'RSI em {rsi_str} — levemente abaixo do neutro, '
                f'espaço para valorização.'
            )
        elif result.rsi <= 55:
            parts.append(
                f'RSI em {rsi_str} — zona neutra, sem pressão direcional.'
            )
        elif result.rsi <= 60:
            parts.append(
                f'RSI em {rsi_str} — levemente acima do neutro.'
            )
        elif result.rsi <= 70:
            parts.append(
                f'RSI em {rsi_str} — pressão de alta, atenção à sobrecompra.'
            )
        else:
            parts.append(
                f'RSI em {rsi_str} — zona de sobrecomprado, '
                f'risco de correção.'
            )

        sup_str = format_brl(result.support)
        res_str = format_brl(result.resistance)
        parts.append(f'Suporte em {sup_str}, resistência em {res_str}.')

        return ' '.join(parts)
