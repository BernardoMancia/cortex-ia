from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time as dt_time
import math
import threading
import time
import warnings
import pandas as pd
import yfinance as yf

from config.settings import settings
from domain.b3_rules import (
    BRT,
    get_ash_wednesday,
    get_brt_now,
    is_b3_business_day,
    normalize_b3_ticker,
    to_yfinance_ticker,
)
from domain.models import MarketQuote
from observability.logger import get_logger

logger = get_logger("market.yfinance")

OHLCV_COLUMNS: list[str] = ["Open", "High", "Low", "Close", "Volume"]


def _empty_ohlcv_df() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def _is_b3_open_now() -> bool:
    now = get_brt_now()
    if not is_b3_business_day(now.date()):
        return False
    open_t = dt_time(13, 0) if now.date() == get_ash_wednesday(now.year) else dt_time(10, 0)
    return open_t <= now.time() <= dt_time(17, 55)


def _safe_float(val: object, default: float = 0.0) -> float:
    try:
        num = float(val)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError):
        return default


def _clean_single_ohlcv(raw: pd.DataFrame | None) -> pd.DataFrame:
    if raw is None or raw.empty:
        return _empty_ohlcv_df()
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [str(col[-1]) for col in df.columns]
    rename_map: dict[str, str] = {}
    for col in df.columns:
        low = str(col).strip().lower()
        if low == "open":
            rename_map[col] = "Open"
        elif low == "high":
            rename_map[col] = "High"
        elif low == "low":
            rename_map[col] = "Low"
        elif low == "close":
            rename_map[col] = "Close"
        elif low == "volume":
            rename_map[col] = "Volume"
    df = df.rename(columns=rename_map)
    if any(req not in df.columns for req in ("Open", "High", "Low", "Close")):
        return _empty_ohlcv_df()
    if "Volume" not in df.columns:
        df["Volume"] = 0.0
    out = df[OHLCV_COLUMNS].copy()
    for col in OHLCV_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["Volume"] = out["Volume"].fillna(0.0).clip(lower=0.0)
    out = out.dropna(subset=["Open", "High", "Low", "Close"])
    out = out[out["Close"] > 0.0]
    if out.empty:
        return _empty_ohlcv_df()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out


def _extract_symbol_frame(
    batch_df: pd.DataFrame | None,
    yf_symbol: str,
    single_symbol: bool,
) -> pd.DataFrame:
    if batch_df is None or batch_df.empty:
        return _empty_ohlcv_df()
    if isinstance(batch_df.columns, pd.MultiIndex):
        lvl0 = set(map(str, batch_df.columns.get_level_values(0)))
        lvl_last = set(map(str, batch_df.columns.get_level_values(-1)))
        if yf_symbol in lvl0:
            return _clean_single_ohlcv(batch_df[yf_symbol])
        if yf_symbol in lvl_last:
            return _clean_single_ohlcv(batch_df.xs(yf_symbol, axis=1, level=-1))
        return _empty_ohlcv_df()
    if single_symbol:
        return _clean_single_ohlcv(batch_df)
    return _empty_ohlcv_df()


class YFinanceMarketProvider:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._quote_cache: dict[str, tuple[float, MarketQuote]] = {}
        self._ohlcv_cache: dict[tuple[str, str, str], tuple[float, pd.DataFrame]] = {}

    def _compute_bid_ask_spread(self, price: float) -> tuple[float, float, float]:
        if price <= 0.0:
            return (0.0, 0.0, 0.0)
        half_spread = round(price * 0.0005, 2)
        if half_spread >= 0.01:
            bid = round(max(0.01, price - half_spread), 2)
            ask = round(max(bid, price + half_spread), 2)
        elif price >= 2.0:
            bid = round(price, 2)
            ask = round(price + 0.01, 2)
        else:
            bid = round(price, 2)
            ask = round(price, 2)
        spread_pct = round(max(0.0, ask - bid) / price, 6)
        return (bid, ask, spread_pct)

    def _refresh_staleness(self, quote: MarketQuote, market_open: bool) -> MarketQuote:
        now_ts = time.time()
        stale = bool(market_open and (now_ts - quote.quote_ts) > settings.STALE_QUOTE_MAX_SECONDS)
        if stale == quote.is_stale:
            return quote
        return replace(quote, is_stale=stale)

    def fetch_quotes(
        self,
        tickers: list[str],
        market_open: bool | None = None,
    ) -> dict[str, MarketQuote]:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tk in tickers:
            clean_tk = normalize_b3_ticker(raw_tk)
            if clean_tk and clean_tk not in seen:
                seen.add(clean_tk)
                normalized.append(clean_tk)
        if not normalized:
            return {}

        is_open = _is_b3_open_now() if market_open is None else bool(market_open)
        mono_now = time.monotonic()
        results: dict[str, MarketQuote] = {}
        missing: list[str] = []

        with self._lock:
            for tk in normalized:
                cached = self._quote_cache.get(tk)
                if cached is not None and (mono_now - cached[0]) <= settings.PRICE_CACHE_TTL:
                    results[tk] = self._refresh_staleness(cached[1], is_open)
                else:
                    missing.append(tk)

        if not missing:
            return results

        chunk_size = 60
        today_brt = get_brt_now().date()

        for idx in range(0, len(missing), chunk_size):
            chunk = missing[idx : idx + chunk_size]
            yf_map = {tk: to_yfinance_ticker(tk) for tk in chunk}
            yf_symbols = [sym for sym in yf_map.values() if sym]
            if not yf_symbols:
                continue
            fetch_ts = time.time()
            batch_df: pd.DataFrame | None = None
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    batch_df = yf.download(
                        tickers=yf_symbols if len(yf_symbols) > 1 else yf_symbols[0],
                        period="5d",
                        interval="1d",
                        group_by="ticker",
                        auto_adjust=False,
                        progress=False,
                        threads=True,
                    )
            except Exception as exc:
                logger.warning("Falha no download em lote YFinance (%d ativos): %s", len(yf_symbols), exc)
                batch_df = None

            single_flag = len(yf_symbols) == 1
            for tk, yf_sym in yf_map.items():
                try:
                    sub = _extract_symbol_frame(batch_df, yf_sym, single_symbol=single_flag)
                    if sub.empty:
                        with self._lock:
                            stale_cached = self._quote_cache.get(tk)
                        if stale_cached is not None:
                            results[tk] = self._refresh_staleness(stale_cached[1], is_open)
                        continue
                    last_row = sub.iloc[-1]
                    price = round(_safe_float(last_row["Close"]), 2)
                    if price <= 0.0:
                        continue
                    prev_close = (
                        _safe_float(sub.iloc[-2]["Close"], price)
                        if len(sub) >= 2
                        else _safe_float(last_row["Open"], price)
                    )
                    change_pct = (
                        round(((price - prev_close) / prev_close) * 100.0, 4)
                        if prev_close > 0.0
                        else 0.0
                    )
                    volume = round(_safe_float(last_row["Volume"], 0.0), 2)
                    bid, ask, spread_pct = self._compute_bid_ask_spread(price)

                    bar_ts_raw = pd.Timestamp(sub.index[-1])
                    bar_date = (
                        bar_ts_raw.tz_convert(BRT).date()
                        if bar_ts_raw.tzinfo is not None
                        else bar_ts_raw.date()
                    )
                    if is_open and bar_date < today_brt:
                        closed_dt = datetime.combine(bar_date, dt_time(17, 55), tzinfo=BRT)
                        quote_ts = closed_dt.timestamp()
                    else:
                        quote_ts = fetch_ts

                    is_stale = bool(
                        is_open and (time.time() - quote_ts) > settings.STALE_QUOTE_MAX_SECONDS
                    )
                    quote = MarketQuote(
                        ticker=tk,
                        price=price,
                        bid=bid,
                        ask=ask,
                        spread_pct=spread_pct,
                        volume=volume,
                        change_pct=change_pct,
                        quote_ts=quote_ts,
                        source="yfinance",
                        is_stale=is_stale,
                    )
                    with self._lock:
                        self._quote_cache[tk] = (time.monotonic(), quote)
                    results[tk] = quote
                except Exception as exc:
                    logger.debug("Erro ao processar cotação YFinance para %s: %s", tk, exc)
                    with self._lock:
                        fallback = self._quote_cache.get(tk)
                    if fallback is not None:
                        results[tk] = self._refresh_staleness(fallback[1], is_open)

        return results

    def _fetch_ohlcv_single(self, ticker: str, interval: str, period: str) -> pd.DataFrame:
        clean_tk = normalize_b3_ticker(ticker)
        if not clean_tk:
            return _empty_ohlcv_df()
        cache_key = (clean_tk, interval, period)
        mono_now = time.monotonic()
        with self._lock:
            cached = self._ohlcv_cache.get(cache_key)
            if cached is not None and (mono_now - cached[0]) <= settings.OHLCV_CACHE_TTL:
                return cached[1].copy()

        yf_sym = to_yfinance_ticker(clean_tk)
        if not yf_sym:
            return _empty_ohlcv_df()

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = yf.download(
                    tickers=yf_sym,
                    period=period,
                    interval=interval,
                    group_by="ticker",
                    auto_adjust=False,
                    progress=False,
                    threads=False,
                )
            df = _extract_symbol_frame(raw, yf_sym, single_symbol=True)
            if not df.empty:
                with self._lock:
                    self._ohlcv_cache[cache_key] = (time.monotonic(), df)
                return df.copy()
        except Exception as exc:
            logger.debug("Falha ao buscar OHLCV (%s, %s) para %s: %s", interval, period, clean_tk, exc)

        with self._lock:
            stale = self._ohlcv_cache.get(cache_key)
            if stale is not None:
                return stale[1].copy()
        return _empty_ohlcv_df()

    def fetch_ohlcv_daily(self, ticker: str, period: str = "6mo") -> pd.DataFrame:
        return self._fetch_ohlcv_single(ticker=ticker, interval="1d", period=period)

    def fetch_ohlcv_60m(self, ticker: str, period: str = "1mo") -> pd.DataFrame:
        return self._fetch_ohlcv_single(ticker=ticker, interval="60m", period=period)

    def prefetch_ohlcv_batch(
        self,
        tickers: list[str],
        period_daily: str = "6mo",
        period_60m: str = "1mo",
        include_60m: bool = True,
    ) -> None:
        normalized: list[str] = []
        seen: set[str] = set()
        for raw_tk in tickers:
            clean_tk = normalize_b3_ticker(raw_tk)
            if clean_tk and clean_tk not in seen:
                seen.add(clean_tk)
                normalized.append(clean_tk)
        if not normalized:
            return

        self._batch_warm_interval(normalized, interval="1d", period=period_daily)
        if include_60m:
            self._batch_warm_interval(normalized, interval="60m", period=period_60m)

    def _batch_warm_interval(self, tickers: list[str], interval: str, period: str) -> None:
        mono_now = time.monotonic()
        missing: list[str] = []
        with self._lock:
            for tk in tickers:
                cached = self._ohlcv_cache.get((tk, interval, period))
                if cached is None or (mono_now - cached[0]) > settings.OHLCV_CACHE_TTL:
                    missing.append(tk)
        if not missing:
            return

        chunk_size = 50
        for idx in range(0, len(missing), chunk_size):
            chunk = missing[idx : idx + chunk_size]
            yf_map = {tk: to_yfinance_ticker(tk) for tk in chunk}
            yf_symbols = [sym for sym in yf_map.values() if sym]
            if not yf_symbols:
                continue
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    raw = yf.download(
                        tickers=yf_symbols if len(yf_symbols) > 1 else yf_symbols[0],
                        period=period,
                        interval=interval,
                        group_by="ticker",
                        auto_adjust=False,
                        progress=False,
                        threads=True,
                    )
                single_flag = len(yf_symbols) == 1
                stored_ts = time.monotonic()
                for tk, yf_sym in yf_map.items():
                    df = _extract_symbol_frame(raw, yf_sym, single_symbol=single_flag)
                    if not df.empty:
                        with self._lock:
                            self._ohlcv_cache[(tk, interval, period)] = (stored_ts, df)
            except Exception as exc:
                logger.warning("Falha no pré-carregamento OHLCV (%s) em lote: %s", interval, exc)
