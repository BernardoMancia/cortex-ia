from __future__ import annotations

import math
import numpy as np
import pandas as pd

from config.settings import settings
from domain.b3_rules import clamp, normalize_b3_ticker
from domain.models import TechnicalVector, TrendSignal


def _finite(val: object, default: float = 0.0) -> float:
    try:
        num = float(val)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError):
        return default


def _wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    alpha = 1.0 / float(period)
    avg_gain = gain.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    g_arr = avg_gain.to_numpy(dtype=float)
    l_arr = avg_loss.to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(l_arr > 1e-12, g_arr / l_arr, np.inf)
        rsi_arr = np.where(
            np.isnan(g_arr) | np.isnan(l_arr),
            50.0,
            np.where(
                (l_arr <= 1e-12) & (g_arr <= 1e-12),
                50.0,
                np.where(l_arr <= 1e-12, 100.0, 100.0 - (100.0 / (1.0 + rs))),
            ),
        )
    return pd.Series(rsi_arr, index=close.index, dtype=float)


def _normalize_ohlcv_columns(df: pd.DataFrame) -> pd.DataFrame:
    col_map: dict[object, str] = {}
    for col in df.columns:
        low = str(col).strip().lower()
        if low == "open":
            col_map[col] = "Open"
        elif low == "high":
            col_map[col] = "High"
        elif low == "low":
            col_map[col] = "Low"
        elif low == "close":
            col_map[col] = "Close"
        elif low == "volume":
            col_map[col] = "Volume"
    renamed = df.rename(columns=col_map)
    if any(req not in renamed.columns for req in ("Open", "High", "Low", "Close")):
        return pd.DataFrame()
    out = pd.DataFrame(index=renamed.index)
    for req in ("Open", "High", "Low", "Close"):
        out[req] = pd.to_numeric(renamed[req], errors="coerce")
    if "Volume" in renamed.columns:
        out["Volume"] = pd.to_numeric(renamed["Volume"], errors="coerce").fillna(0.0).clip(lower=0.0)
    else:
        out["Volume"] = 0.0
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out = out[out["Close"] > 0.0]
    return out


class TechnicalAnalyzer:
    def _evaluate_mtf_bullish(self, df_60m: pd.DataFrame | None) -> bool:
        if not settings.ENABLE_MTF_CONFIRMATION:
            return True
        if df_60m is None or df_60m.empty:
            return True
        clean_60m = _normalize_ohlcv_columns(df_60m)
        if clean_60m.empty or len(clean_60m) < 15:
            return True
        close_60m = clean_60m["Close"].astype(float)
        ema_9_60m = _finite(close_60m.ewm(span=9, adjust=False).mean().iloc[-1], 0.0)
        ema_21_60m = _finite(close_60m.ewm(span=21, adjust=False).mean().iloc[-1], 0.0)
        rsi_60m = _finite(_wilder_rsi(close_60m, 14).iloc[-1], 50.0)
        return bool(ema_9_60m >= ema_21_60m and rsi_60m > 45.0)

    def analyze_ticker(
        self,
        ticker: str,
        df_d1: pd.DataFrame,
        df_60m: pd.DataFrame | None = None,
    ) -> TechnicalVector:
        norm_tk = normalize_b3_ticker(ticker)
        if df_d1 is None or df_d1.empty:
            return TechnicalVector(
                ticker=norm_tk,
                signal=TrendSignal.NEUTRAL,
                mtf_bullish=self._evaluate_mtf_bullish(df_60m),
                confidence=0.0,
                reasoning="Sem histórico diário disponível para análise técnica.",
            )

        df = _normalize_ohlcv_columns(df_d1)
        if df.empty or len(df) < 20:
            return TechnicalVector(
                ticker=norm_tk,
                signal=TrendSignal.NEUTRAL,
                mtf_bullish=self._evaluate_mtf_bullish(df_60m),
                confidence=0.0,
                reasoning="Amostra histórica insuficiente (mínimo de 20 pregões).",
            )

        close = df["Close"].astype(float)
        high = df["High"].astype(float)
        low = df["Low"].astype(float)
        volume = df["Volume"].astype(float)

        ema_9_s = close.ewm(span=9, adjust=False).mean()
        ema_21_s = close.ewm(span=21, adjust=False).mean()
        ema_50_s = close.ewm(span=50, adjust=False).mean()

        rsi_s = _wilder_rsi(close, period=14)

        ema_12_s = close.ewm(span=12, adjust=False).mean()
        ema_26_s = close.ewm(span=26, adjust=False).mean()
        macd_line = ema_12_s - ema_26_s
        macd_signal = macd_line.ewm(span=9, adjust=False).mean()
        macd_hist_s = macd_line - macd_signal

        sma_20 = close.rolling(window=20, min_periods=20).mean()
        std_20 = close.rolling(window=20, min_periods=20).std(ddof=0)
        bb_upper_s = sma_20 + (2.0 * std_20)
        bb_lower_s = sma_20 - (2.0 * std_20)

        prev_close_s = close.shift(1)
        tr_s = pd.concat(
            [
                (high - low).abs(),
                (high - prev_close_s).abs(),
                (low - prev_close_s).abs(),
            ],
            axis=1,
        ).max(axis=1)
        alpha_14 = 1.0 / 14.0
        atr_s = tr_s.ewm(alpha=alpha_14, min_periods=14, adjust=False).mean()

        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = pd.Series(
            np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0),
            index=df.index,
            dtype=float,
        )
        minus_dm = pd.Series(
            np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0),
            index=df.index,
            dtype=float,
        )
        atr_safe = atr_s.replace(0.0, np.nan)
        plus_di_s = 100.0 * (plus_dm.ewm(alpha=alpha_14, min_periods=14, adjust=False).mean() / atr_safe)
        minus_di_s = 100.0 * (minus_dm.ewm(alpha=alpha_14, min_periods=14, adjust=False).mean() / atr_safe)
        di_sum = (plus_di_s + minus_di_s).replace(0.0, np.nan)
        dx_s = (100.0 * (plus_di_s - minus_di_s).abs() / di_sum).fillna(0.0)
        adx_s = dx_s.ewm(alpha=alpha_14, min_periods=14, adjust=False).mean()

        last_close = _finite(close.iloc[-1], 0.0)
        prev_close = _finite(close.iloc[-2], last_close) if len(close) >= 2 else last_close
        ema_9 = round(_finite(ema_9_s.iloc[-1], last_close), 4)
        ema_21 = round(_finite(ema_21_s.iloc[-1], last_close), 4)
        ema_50 = round(_finite(ema_50_s.iloc[-1], last_close), 4)
        rsi = round(clamp(_finite(rsi_s.iloc[-1], 50.0), 0.0, 100.0), 2)
        macd_hist = round(_finite(macd_hist_s.iloc[-1], 0.0), 4)
        prev_macd_hist = _finite(macd_hist_s.iloc[-2], macd_hist) if len(macd_hist_s) >= 2 else macd_hist
        atr = round(max(0.0, _finite(atr_s.iloc[-1], last_close * 0.02)), 4)
        adx = round(clamp(_finite(adx_s.iloc[-1], 15.0), 0.0, 100.0), 2)

        vol_20_mean = _finite(volume.tail(20).mean(), 0.0)
        last_vol = _finite(volume.iloc[-1], 0.0)
        rel_vol = round(clamp(last_vol / vol_20_mean, 0.0, 15.0), 2) if vol_20_mean > 0.0 else 1.0

        bb_lower = round(_finite(bb_lower_s.iloc[-1], last_close * 0.96), 4)
        bb_upper = round(_finite(bb_upper_s.iloc[-1], last_close * 1.04), 4)
        support = round(_finite(low.tail(20).min(), bb_lower), 4)
        resistance = round(_finite(high.tail(20).max(), bb_upper), 4)

        mtf_bullish = self._evaluate_mtf_bullish(df_60m)

        score = 0.0
        reasons: list[str] = []

        if last_close > ema_9 > ema_21 > ema_50:
            score += 0.38
            reasons.append("EMAs 9/21/50 alinhadas em alta")
        elif ema_9 > ema_21 and last_close >= ema_21:
            score += 0.22
            reasons.append("EMA9 acima da EMA21")
        elif last_close < ema_9 < ema_21 < ema_50:
            score -= 0.38
            reasons.append("EMAs 9/21/50 alinhadas em baixa")
        elif ema_9 < ema_21 and last_close <= ema_21:
            score -= 0.22
            reasons.append("EMA9 abaixo da EMA21")
        else:
            reasons.append("EMAs em consolidação lateral")

        if macd_hist > 0.0 and macd_hist >= prev_macd_hist:
            score += 0.22
            reasons.append("MACD acelerando positivo")
        elif macd_hist > 0.0:
            score += 0.12
            reasons.append("MACD positivo")
        elif macd_hist < 0.0 and macd_hist <= prev_macd_hist:
            score -= 0.22
            reasons.append("MACD acelerando negativo")
        elif macd_hist < 0.0:
            score -= 0.12
            reasons.append("MACD negativo")

        if 52.0 <= rsi <= 68.0:
            score += 0.18
            reasons.append(f"IFR construtivo ({rsi:.1f})")
        elif rsi < 32.0 and last_close > prev_close:
            score += 0.14
            reasons.append(f"IFR sobrevendido em repique ({rsi:.1f})")
        elif rsi > 76.0:
            score -= 0.16
            reasons.append(f"IFR sobrecomprado ({rsi:.1f})")
        elif 30.0 <= rsi <= 45.0:
            score -= 0.16
            reasons.append(f"IFR enfraquecido ({rsi:.1f})")
        elif rsi < 30.0:
            score -= 0.22
            reasons.append(f"IFR em pressão vendedora ({rsi:.1f})")

        if bb_lower > 0.0 and last_close <= bb_lower * 1.01 and last_close > prev_close:
            score += 0.08
        elif bb_upper > 0.0 and last_close >= bb_upper * 0.995 and rsi > 72.0:
            score -= 0.08

        if rel_vol >= 1.25 and abs(score) >= 0.15:
            score += 0.10 if score > 0.0 else -0.10
            reasons.append(f"volume relativo forte ({rel_vol:.2f}x)")

        if adx >= 25.0:
            score *= 1.12
            reasons.append(f"ADX direcional ({adx:.1f})")

        if score > 0.0 and mtf_bullish:
            score += 0.06
        elif score > 0.0 and not mtf_bullish:
            score -= 0.14
            reasons.append("MTF 60m sem confirmação")
        elif score < 0.0 and not mtf_bullish:
            score -= 0.06

        score = clamp(score, -1.0, 1.0)

        if score >= 0.55:
            signal = TrendSignal.STRONG_BUY
        elif score >= 0.22:
            signal = TrendSignal.BUY
        elif score <= -0.55:
            signal = TrendSignal.STRONG_SELL
        elif score <= -0.22:
            signal = TrendSignal.SELL
        else:
            signal = TrendSignal.NEUTRAL

        if signal == TrendSignal.NEUTRAL:
            confidence = round(clamp(0.22 + abs(score) * 0.85, 0.15, 0.48), 4)
        else:
            adx_bonus = 0.06 if adx >= 25.0 else 0.0
            confidence = round(clamp(0.45 + abs(score) * 0.46 + adx_bonus, 0.48, 0.97), 4)

        mtf_tag = "60m altista" if mtf_bullish else "60m cauteloso"
        reasoning = f"{', '.join(reasons[:4])} ({mtf_tag})."

        return TechnicalVector(
            ticker=norm_tk,
            signal=signal,
            ema_9=ema_9,
            ema_21=ema_21,
            ema_50=ema_50,
            rsi=rsi,
            macd_hist=macd_hist,
            atr=atr,
            adx=adx,
            rel_vol=rel_vol,
            bb_lower=bb_lower,
            bb_upper=bb_upper,
            support=support,
            resistance=resistance,
            mtf_bullish=mtf_bullish,
            confidence=confidence,
            reasoning=reasoning[:380],
        )
