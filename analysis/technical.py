"""
Motor de análise técnica do Projeto Córtex.

Calcula indicadores quantitativos (EMAs, RSI, MACD, Bandas de Bollinger,
ATR, Volume Relativo ajustado para pregão parcial e Suporte/Resistência)
e gera sinais de tendência para cada ativo da watchlist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
import math
from typing import Any, Optional

import numpy as np
import pandas as pd
import pandas_ta as ta

from utils.helpers import BRT, format_brl, format_number
from utils.logger import get_logger

logger = get_logger("analysis.technical")

_B3_SESSION_OPEN_MIN: float = 10.0 * 60.0   # 10:00 BRT
_B3_SESSION_CLOSE_MIN: float = 17.0 * 60.0  # 17:00 BRT
_B3_SESSION_MINUTES: float = _B3_SESSION_CLOSE_MIN - _B3_SESSION_OPEN_MIN  # 420 min


def _is_nan_or_inf(val: Any) -> bool:
    """Retorna True se o valor for None, NaN ou Infinito."""
    if val is None:
        return True
    try:
        f = float(val)
        return math.isnan(f) or math.isinf(f)
    except (TypeError, ValueError):
        return True


def _safe_float(val: Any, default: float) -> float:
    """Converte valor para float substituindo None/NaN/Inf pelo default."""
    if _is_nan_or_inf(val):
        return default
    return float(val)


class TrendSignal(Enum):
    """Sinal de tendência gerado pela análise técnica."""

    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


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
    reasoning: str = ""


class TechnicalAnalyzer:
    """
    Motor de análise técnica quantitativa do Córtex.

    Calcula EMAs (9, 21, 50), RSI (14), MACD (12, 26, 9), ATR (14),
    Bandas de Bollinger (20, 2), Volume Relativo normalizado e S/R
    em barras completas anteriores.
    """

    DEFAULT_EMA_PERIODS: tuple[int, ...] = (9, 21, 50)
    DEFAULT_RSI_PERIOD: int = 14
    DEFAULT_SR_WINDOW: int = 20

    REQUIRED_COLUMNS: set[str] = {"Open", "High", "Low", "Close", "Volume"}
    OHLC_COLUMNS: list[str] = ["Open", "High", "Low", "Close"]

    def __init__(
        self,
        ema_periods: Optional[list[int]] = None,
        rsi_period: int = DEFAULT_RSI_PERIOD,
        sr_window: int = DEFAULT_SR_WINDOW,
    ) -> None:
        self.ema_periods = ema_periods or list(self.DEFAULT_EMA_PERIODS)
        self.rsi_period = rsi_period
        self.sr_window = sr_window
        logger.info(
            "TechnicalAnalyzer inicializado — EMAs: %s, RSI: %d, S/R janela: %d",
            self.ema_periods, self.rsi_period, self.sr_window,
        )

    def _min_required_bars(self) -> int:
        """Quantidade mínima de candles válidos para convergência dos indicadores."""
        return max(*self.ema_periods, self.rsi_period + 1, 35, self.sr_window) + 1

    @classmethod
    def _prepare_dataframe(cls, raw_df: Any) -> pd.DataFrame:
        """
        Converte entrada (lista de OHLCV ou DataFrame), achata MultiIndex,
        padroniza nomes de colunas e remove linhas com NaN em OHLC.
        """
        if isinstance(raw_df, (list, tuple)):
            if not raw_df:
                return pd.DataFrame()
            rows = []
            timestamps = []
            has_all_ts = True
            for item in raw_df:
                ts = getattr(item, "timestamp", None)
                if ts is None:
                    has_all_ts = False
                else:
                    timestamps.append(ts)
                rows.append({
                    "Open": getattr(item, "open", getattr(item, "Open", np.nan)),
                    "High": getattr(item, "high", getattr(item, "High", np.nan)),
                    "Low": getattr(item, "low", getattr(item, "Low", np.nan)),
                    "Close": getattr(item, "close", getattr(item, "Close", np.nan)),
                    "Volume": getattr(item, "volume", getattr(item, "Volume", 0)),
                })
            df_out = pd.DataFrame(rows)
            if has_all_ts and len(timestamps) == len(rows):
                try:
                    df_out.index = pd.DatetimeIndex(timestamps)
                except Exception:
                    pass
            return df_out

        if not isinstance(raw_df, pd.DataFrame):
            return pd.DataFrame()

        df_out = raw_df.copy()
        if isinstance(df_out.columns, pd.MultiIndex):
            df_out.columns = df_out.columns.get_level_values(0)

        col_map = {
            c: str(c).strip().capitalize()
            for c in df_out.columns
            if str(c).strip().lower() in ("open", "high", "low", "close", "volume")
        }
        if col_map:
            df_out = df_out.rename(columns=col_map)

        # Se colunas duplicadas surgirem após flatten de MultiIndex, mantém a primeira
        if df_out.columns.duplicated().any():
            df_out = df_out.loc[:, ~df_out.columns.duplicated()].copy()

        if cls.REQUIRED_COLUMNS.issubset(set(df_out.columns)):
            for col in cls.OHLC_COLUMNS:
                df_out[col] = pd.to_numeric(df_out[col], errors="coerce")
            df_out["Volume"] = pd.to_numeric(df_out["Volume"], errors="coerce").fillna(0.0)
            df_out = df_out.dropna(subset=cls.OHLC_COLUMNS).copy()

        return df_out

    def analyze(self, ticker: str, df: Any) -> TechnicalResult:
        """
        Executa análise técnica completa para um ativo.
        """
        df_work = self._prepare_dataframe(df)
        logger.debug("Iniciando análise técnica para %s (%d candles válidos)", ticker, len(df_work))

        self._validate_dataframe(df_work, ticker)

        self.calculate_ema(df_work, self.ema_periods)
        self.calculate_rsi(df_work, self.rsi_period)
        self.calculate_macd(df_work)
        self.calculate_atr(df_work)
        self.calculate_relative_volume(df_work)
        self.calculate_bollinger_bands(df_work)
        support, resistance = self.find_support_resistance(df_work, self.sr_window)

        last_row = df_work.iloc[-1]
        current_price = _safe_float(last_row.get("Close"), 0.0)

        # Verifica NaN nos indicadores principais ANTES de mascarar com defaults
        core_ema_cols = [f"EMA_{p}" for p in self.ema_periods]
        for std_col in ("EMA_9", "EMA_21", "EMA_50"):
            if std_col in df_work.columns and std_col not in core_ema_cols:
                core_ema_cols.append(std_col)

        raw_core_missing = (
            any(_is_nan_or_inf(last_row.get(col)) for col in core_ema_cols)
            or _is_nan_or_inf(last_row.get("RSI"))
        )

        if raw_core_missing:
            logger.warning(
                "Indicadores centrais (EMAs/RSI) com NaN para %s — retornando NEUTRAL (confiança=0.0)",
                ticker,
            )
            neutral_res = TechnicalResult(
                signal=TrendSignal.NEUTRAL,
                ema_9=round(_safe_float(last_row.get("EMA_9"), 0.0), 2),
                ema_21=round(_safe_float(last_row.get("EMA_21"), 0.0), 2),
                ema_50=round(_safe_float(last_row.get("EMA_50"), 0.0), 2),
                rsi=round(_safe_float(last_row.get("RSI"), 50.0), 2),
                support=round(support, 2),
                resistance=round(resistance, 2),
                macd_hist=round(_safe_float(last_row.get("MACD_HIST"), 0.0), 4),
                atr=round(_safe_float(last_row.get("ATR"), 0.0), 2),
                rel_vol=round(_safe_float(last_row.get("REL_VOL"), 1.0), 2),
                bb_lower=round(_safe_float(last_row.get("BB_LOWER"), 0.0), 2),
                bb_upper=round(_safe_float(last_row.get("BB_UPPER"), 0.0), 2),
                confidence=0.0,
                reasoning=f"Indicadores técnicos principais ainda em formação (NaN) para {ticker}.",
            )
            return neutral_res

        ema_9 = _safe_float(last_row.get("EMA_9"), current_price)
        ema_21 = _safe_float(last_row.get("EMA_21"), current_price)
        ema_50 = _safe_float(last_row.get("EMA_50"), current_price)
        rsi = _safe_float(last_row.get("RSI"), 50.0)
        macd_hist = _safe_float(last_row.get("MACD_HIST"), 0.0)
        atr = _safe_float(last_row.get("ATR"), 0.0)
        rel_vol = _safe_float(last_row.get("REL_VOL"), 1.0)
        bb_lower = _safe_float(last_row.get("BB_LOWER"), 0.0)
        bb_upper = _safe_float(last_row.get("BB_UPPER"), 0.0)

        # Detecta cruzamento real na última barra vs tendência já em andamento
        crossed_up = False
        crossed_down = False
        if len(df_work) >= 2:
            prev_row = df_work.iloc[-2]
            prev_ema9 = prev_row.get("EMA_9")
            prev_ema21 = prev_row.get("EMA_21")
            if not _is_nan_or_inf(prev_ema9) and not _is_nan_or_inf(prev_ema21):
                p9, p21 = float(prev_ema9), float(prev_ema21)
                crossed_up = (p9 <= p21) and (ema_9 > ema_21)
                crossed_down = (p9 >= p21) and (ema_9 < ema_21)

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
            reasoning="",
        )
        result.reasoning = self._generate_reasoning(
            ticker,
            result,
            crossed_up=crossed_up,
            crossed_down=crossed_down,
        )

        logger.info(
            "Análise técnica %s: sinal=%s, confiança=%.2f, RSI=%.1f, RelVol=%.2fx",
            ticker, signal.value, confidence, rsi, rel_vol,
        )
        return result

    def calculate_ema(
        self, df: pd.DataFrame, periods: Optional[list[int]] = None
    ) -> pd.DataFrame:
        """Calcula Médias Móveis Exponenciais (EMAs) usando pandas_ta."""
        periods = periods or self.ema_periods
        for period in periods:
            col_name = f"EMA_{period}"
            ema_series = ta.ema(df["Close"], length=period)
            if ema_series is not None:
                df[col_name] = ema_series
            else:
                logger.warning("EMA(%d) retornou None — dados insuficientes?", period)
                df[col_name] = np.nan
        return df

    def calculate_rsi(
        self, df: pd.DataFrame, period: Optional[int] = None
    ) -> pd.DataFrame:
        """Calcula o Índice de Força Relativa (RSI) usando pandas_ta."""
        period = period or self.rsi_period
        rsi_series = ta.rsi(df["Close"], length=period)
        if rsi_series is not None:
            df["RSI"] = rsi_series
        else:
            logger.warning("RSI(%d) retornou None — dados insuficientes?", period)
            df["RSI"] = np.nan
        return df

    def calculate_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calcula o MACD(12, 26, 9) usando pandas_ta."""
        macd_df = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        if macd_df is not None and not macd_df.empty:
            col = [c for c in macd_df.columns if c.startswith("MACDh_")]
            df["MACD_HIST"] = macd_df[col[0]] if col else np.nan
        else:
            logger.warning("MACD retornou None — dados insuficientes?")
            df["MACD_HIST"] = np.nan
        return df

    def calculate_bollinger_bands(
        self, df: pd.DataFrame, length: int = 20, std: float = 2.0
    ) -> pd.DataFrame:
        """Calcula as Bandas de Bollinger usando pandas_ta."""
        bb_df = ta.bbands(df["Close"], length=length, std=std)
        if bb_df is not None and not bb_df.empty:
            lower_col = [c for c in bb_df.columns if c.startswith("BBL_")]
            upper_col = [c for c in bb_df.columns if c.startswith("BBU_")]
            df["BB_LOWER"] = bb_df[lower_col[0]] if lower_col else np.nan
            df["BB_UPPER"] = bb_df[upper_col[0]] if upper_col else np.nan
        else:
            logger.warning("Bollinger Bands retornou None — dados insuficientes?")
            df["BB_LOWER"] = np.nan
            df["BB_UPPER"] = np.nan
        return df

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """Calcula o ATR(14) para medir a volatilidade."""
        atr_series = ta.atr(df["High"], df["Low"], df["Close"], length=period)
        if atr_series is not None:
            df["ATR"] = atr_series
        else:
            logger.warning("ATR(%d) retornou None", period)
            df["ATR"] = np.nan
        return df

    @staticmethod
    def _is_daily_partial_session_bar(df: pd.DataFrame, now_brt: datetime) -> bool:
        """
        Verifica se a última barra pertence a um DataFrame diário (`1d`) da sessão
        atual da B3 durante o horário regular (`10:00–17:00 BRT`).
        """
        if len(df) < 2 or not isinstance(df.index, pd.DatetimeIndex):
            return False
        if now_brt.weekday() >= 5:
            return False

        now_min = now_brt.hour * 60.0 + now_brt.minute + now_brt.second / 60.0
        if not (_B3_SESSION_OPEN_MIN <= now_min < _B3_SESSION_CLOSE_MIN):
            return False

        try:
            t_last = df.index[-1]
            t_prev = df.index[-2]
            delta = t_last - t_prev
            if delta < timedelta(hours=18):
                return False

            dt_last = t_last.to_pydatetime() if hasattr(t_last, "to_pydatetime") else t_last
            if isinstance(dt_last, datetime):
                if dt_last.tzinfo is not None and not (dt_last.hour == 0 and dt_last.minute == 0):
                    bar_date = dt_last.astimezone(BRT).date()
                else:
                    bar_date = dt_last.date()
            elif isinstance(dt_last, date):
                bar_date = dt_last
            else:
                return False

            return bar_date == now_brt.date()
        except Exception:
            return False

    def calculate_relative_volume(
        self,
        df: pd.DataFrame,
        period: int = 20,
        now_brt: Optional[datetime] = None,
    ) -> pd.DataFrame:
        """
        Calcula SMA(20) do Volume e `REL_VOL` sem avisos de divisão por zero,
        projetando proporcionalmente o volume parcial da manhã na B3 (`10:00–17:00 BRT`).
        """
        vol_series = pd.to_numeric(df["Volume"], errors="coerce").fillna(0.0).astype(float)
        now = now_brt.astimezone(BRT) if (now_brt and now_brt.tzinfo) else (now_brt or datetime.now(tz=BRT))

        effective_vol = vol_series.copy()
        if self._is_daily_partial_session_bar(df, now):
            now_min = now.hour * 60.0 + now.minute + now.second / 60.0
            elapsed_minutes = max(15.0, now_min - _B3_SESSION_OPEN_MIN)
            scale_factor = _B3_SESSION_MINUTES / elapsed_minutes
            effective_vol.iloc[-1] = effective_vol.iloc[-1] * scale_factor
            sma_vol = vol_series.shift(1).rolling(period).mean().combine_first(vol_series.rolling(period).mean())
        else:
            sma_vol = vol_series.rolling(period).mean()

        df["SMA_VOL"] = sma_vol
        df["REL_VOL"] = effective_vol.div(sma_vol.where(sma_vol > 0)).fillna(1.0)
        return df

    def find_support_resistance(
        self, df: pd.DataFrame, window: Optional[int] = None
    ) -> tuple[float, float]:
        """
        Identifica suporte e resistência sobre as barras completas anteriores
        (`df.iloc[-effective_window - 1 : -1]` quando `len(df) > 2`), evitando
        auto-inclusão do próprio candle em formação.
        """
        window = window or self.sr_window
        last_close = _safe_float(df["Close"].iloc[-1], 0.0) if len(df) > 0 else 0.0

        if len(df) < 2:
            logger.warning(
                "Dados insuficientes para S/R (janela=%d, disponível=%d). Usando fallback.",
                window, len(df),
            )
            return last_close * 0.95, last_close * 1.05

        if len(df) > 2:
            effective_window = min(window, len(df) - 1)
            recent = df.iloc[-effective_window - 1 : -1]
        else:
            effective_window = len(df)
            recent = df.tail(effective_window)

        support = _safe_float(recent["Low"].min(), last_close * 0.95)
        resistance = _safe_float(recent["High"].max(), last_close * 1.05)

        if support <= 0.0 and last_close > 0.0:
            support = last_close * 0.95
        if resistance <= 0.0 and last_close > 0.0:
            resistance = last_close * 1.05

        logger.debug(
            "Suporte: %s, Resistência: %s (janela=%d)",
            format_brl(support), format_brl(resistance), effective_window,
        )
        return support, resistance

    def _validate_dataframe(self, df: pd.DataFrame, ticker: str) -> None:
        """
        Valida colunas obrigatórias e tamanho mínimo após remoção de linhas com NaN em OHLC.
        """
        if df is None or df.empty:
            raise ValueError(
                f"DataFrame vazio ou nulo para {ticker}. "
                "Não é possível executar análise técnica."
            )

        if isinstance(df.columns, pd.MultiIndex):
            cols_present = set(df.columns.get_level_values(0))
        else:
            cols_present = set(df.columns)

        missing_cols = self.REQUIRED_COLUMNS - cols_present
        if missing_cols:
            raise ValueError(
                f"DataFrame de {ticker} não possui colunas obrigatórias: "
                f"{missing_cols}. Colunas presentes: {list(cols_present)}"
            )

        clean_df = df.dropna(subset=self.OHLC_COLUMNS)
        min_required = self._min_required_bars()
        if len(clean_df) < min_required:
            raise ValueError(
                f"Dados insuficientes para {ticker}: {len(clean_df)} candles válidos "
                f"(mínimo necessário: {min_required})"
            )

    def _determine_signal(
        self,
        current_price: float,
        ema_9: float,
        ema_21: float,
        ema_50: float,
        rsi: float,
        macd_hist: float,
        rel_vol: float,
        bb_lower: float,
        bb_upper: float,
    ) -> TrendSignal:
        """
        Determina o sinal de tendência eliminando zonas mortas de RSI e permitindo
        `SELL` / `STRONG_SELL` durante quedas agudas (crash/panic-sell) sem bloqueio
        por sobrevenda ou Banda de Bollinger inferior.
        """
        if any(
            _is_nan_or_inf(v)
            for v in (ema_9, ema_21, ema_50, rsi, macd_hist, rel_vol)
        ):
            logger.warning("Valores NaN detectados nos indicadores — sinal NEUTRAL")
            return TrendSignal.NEUTRAL

        signal = TrendSignal.NEUTRAL

        if (
            ema_9 > ema_21 > ema_50
            and current_price >= ema_9
            and 35.0 <= rsi <= 68.0
            and macd_hist > 0.0
            and rel_vol >= 1.0
        ):
            signal = TrendSignal.STRONG_BUY
        elif (
            ema_9 < ema_21 < ema_50
            and current_price <= ema_9
            and rsi <= 65.0
            and macd_hist < 0.0
            and rel_vol >= 1.0
        ):
            signal = TrendSignal.STRONG_SELL
        elif (
            ema_9 > ema_21
            and current_price > ema_21
            and rsi <= 68.0
            and macd_hist > 0.0
        ):
            signal = TrendSignal.BUY
        elif (
            ema_9 < ema_21
            and current_price < ema_21
            and macd_hist < 0.0
        ):
            signal = TrendSignal.SELL

        # Filtro de exaustão altista apenas para novas compras (>2% acima da BB superior)
        if signal in (TrendSignal.BUY, TrendSignal.STRONG_BUY):
            if not _is_nan_or_inf(bb_upper) and bb_upper > 0.0 and current_price > (bb_upper * 1.02):
                logger.debug(
                    "Sinal de compra pausado: preço esticado > 2%% acima da Banda de Bollinger superior."
                )
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
        """Calcula nível de confiança do sinal (0.0 a 1.0)."""
        if signal == TrendSignal.STRONG_BUY:
            base = 0.75
            trend_bonus = 0.10 if (ema_9 > ema_21 > ema_50) else 0.0
            rsi_bonus = 0.05 if (40.0 <= rsi <= 62.0) else 0.0
            vol_bonus = min(0.10, max(0.0, (rel_vol - 1.0) * 0.1))
            return min(1.0, base + trend_bonus + rsi_bonus + vol_bonus)

        if signal == TrendSignal.BUY:
            base = 0.60
            rsi_bonus = 0.05 if (40.0 <= rsi <= 62.0) else 0.0
            vol_bonus = min(0.10, max(0.0, (rel_vol - 1.0) * 0.05))
            macd_bonus = 0.05 if macd_hist > 0.0 else 0.0
            return min(0.85, base + rsi_bonus + vol_bonus + macd_bonus)

        if signal == TrendSignal.NEUTRAL:
            return 0.30

        if signal == TrendSignal.SELL:
            base = 0.60
            rsi_bonus = 0.05 if rsi <= 60.0 else 0.0
            vol_bonus = min(0.10, max(0.0, (rel_vol - 1.0) * 0.05))
            macd_bonus = 0.05 if macd_hist < 0.0 else 0.0
            return min(0.85, base + rsi_bonus + vol_bonus + macd_bonus)

        if signal == TrendSignal.STRONG_SELL:
            base = 0.75
            trend_bonus = 0.10 if (ema_9 < ema_21 < ema_50) else 0.0
            rsi_bonus = 0.05 if rsi <= 60.0 else 0.0
            vol_bonus = min(0.10, max(0.0, (rel_vol - 1.0) * 0.1))
            return min(1.0, base + trend_bonus + rsi_bonus + vol_bonus)

        return 0.30

    def _generate_reasoning(
        self,
        ticker: str,
        result: TechnicalResult,
        crossed_up: bool = False,
        crossed_down: bool = False,
    ) -> str:
        """
        Gera texto explicativo em português diferenciando cruzamento recente
        de tendência já em andamento.
        """
        parts: list[str] = []

        ema9_str = format_brl(result.ema_9)
        ema21_str = format_brl(result.ema_21)
        ema50_str = format_brl(result.ema_50)

        if result.ema_9 > result.ema_21:
            verb = "cruzou acima da" if crossed_up else "opera acima da"
            parts.append(
                f"EMA(9) em {ema9_str} {verb} EMA(21) em {ema21_str}, "
                f"indicando momentum de alta."
            )
        elif result.ema_9 < result.ema_21:
            verb = "cruzou abaixo da" if crossed_down else "opera abaixo da"
            parts.append(
                f"EMA(9) em {ema9_str} {verb} EMA(21) em {ema21_str}, "
                f"indicando momentum de baixa."
            )
        else:
            parts.append("EMAs sem direção definida.")

        if _is_nan_or_inf(result.macd_hist):
            parts.append("MACD indisponível (dados insuficientes).")
        else:
            macd_sign = "positivo" if result.macd_hist > 0 else ("negativo" if result.macd_hist < 0 else "neutro")
            parts.append(f"MACD indica momentum {macd_sign}.")

        if result.rel_vol > 1.2:
            parts.append(f"Volume elevado ({result.rel_vol:.1f}x a média), confirmando o movimento.")

        if result.ema_9 > result.ema_21 > result.ema_50:
            parts.append(
                f"EMA(50) em {ema50_str} confirma tendência altista com alinhamento completo."
            )
        elif result.ema_9 < result.ema_21 < result.ema_50:
            parts.append(
                f"EMA(50) em {ema50_str} confirma tendência baixista com alinhamento completo."
            )
        else:
            parts.append(f"EMA(50) em {ema50_str} — tendência mista.")

        rsi_str = format_number(result.rsi, 1)
        is_bearish_signal = result.signal in (TrendSignal.SELL, TrendSignal.STRONG_SELL)
        if _is_nan_or_inf(result.rsi):
            parts.append("RSI indisponível (dados insuficientes).")
        elif result.rsi < 35:
            if is_bearish_signal:
                parts.append(
                    f"RSI em {rsi_str} — forte pressão vendedora em zona de sobrevenda."
                )
            elif result.rsi < 30:
                parts.append(
                    f"RSI em {rsi_str} — zona de sobrevendido extremo, possível reversão."
                )
            else:
                parts.append(
                    f"RSI em {rsi_str} — zona de sobrevendido."
                )
        elif result.rsi < 45:
            parts.append(f"RSI em {rsi_str} — levemente abaixo do neutro.")
        elif result.rsi <= 55:
            parts.append(f"RSI em {rsi_str} — zona neutra, sem pressão extrema.")
        elif result.rsi <= 68:
            parts.append(f"RSI em {rsi_str} — força compradora saudável.")
        else:
            parts.append(f"RSI em {rsi_str} — zona de sobrecompra, atenção ao risco de correção.")

        sup_str = format_brl(result.support)
        res_str = format_brl(result.resistance)
        parts.append(f"Suporte em {sup_str}, resistência em {res_str}.")

        return " ".join(parts)
