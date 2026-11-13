"""
Dados de mercado do Projeto Córtex.

Fornece cotações em tempo real e dados históricos OHLCV usando
yfinance como fonte primária (cross-platform) com fallback para
MetaTrader 5 quando disponível. Inclui cache thread-safe com TTL
para cotações e candles OHLCV, além de detecção de cotação defasada
na abertura da B3.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
import logging
import math
import threading
import time
from typing import Any, Optional

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

from config.settings import settings
from utils.helpers import (
    BRT,
    format_b3_ticker_for_quantity,
    normalize_b3_ticker,
    to_yfinance_ticker,
)

logger = logging.getLogger("cortex.data.market_data")

_MAX_YF_WORKERS: int = 6
_YF_INIT_LOCK: threading.Lock = threading.Lock()
_YF_SESSION_WARMED: bool = False


def _b3_to_yfinance(ticker: str) -> str:
    """Converte ticker B3 para formato Yahoo Finance ('.SA')."""
    return to_yfinance_ticker(ticker)


def _yfinance_to_b3(ticker: str) -> str:
    """Converte ticker Yahoo Finance/fracionário para o ticker-base da B3."""
    return normalize_b3_ticker(ticker)


def _is_b3_market_hours(now_brt: Optional[datetime] = None) -> bool:
    """
    Verifica se o horário atual está dentro da janela de pregão regular da B3
    (dias úteis, 10:00–17:55 BRT).
    """
    now = now_brt.astimezone(BRT) if (now_brt and now_brt.tzinfo) else (now_brt or datetime.now(tz=BRT))
    if now.weekday() >= 5:
        return False
    total_minutes = now.hour * 60 + now.minute
    return (10 * 60) <= total_minutes <= (17 * 60 + 55)


def _index_last_date_brt(index: pd.Index) -> Optional[date]:
    """Extrai a data em BRT do último candle de um DatetimeIndex."""
    if index is None or len(index) == 0:
        return None
    last_ts = index[-1]
    try:
        dt = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts
        if isinstance(dt, datetime):
            if dt.tzinfo is not None:
                if dt.hour == 0 and dt.minute == 0 and dt.second == 0:
                    return dt.date()
                return dt.astimezone(BRT).date()
            return dt.date()
        if isinstance(dt, date):
            return dt
    except Exception:
        return None
    return None


def _index_last_iso_brt(index: pd.Index, fallback_now: datetime) -> str:
    """Extrai timestamp ISO em BRT do último candle de um DatetimeIndex."""
    if index is None or len(index) == 0:
        return fallback_now.isoformat()
    last_ts = index[-1]
    try:
        dt = last_ts.to_pydatetime() if hasattr(last_ts, "to_pydatetime") else last_ts
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRT)
            else:
                dt = dt.astimezone(BRT)
            return dt.isoformat()
    except Exception:
        pass
    return fallback_now.isoformat()


class _PriceCache:
    """Cache thread-safe de preços com TTL configurável."""

    def __init__(self, ttl_seconds: Optional[int] = None) -> None:
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._lock: threading.Lock = threading.Lock()
        self._explicit_ttl: Optional[int] = ttl_seconds

    @property
    def ttl(self) -> float:
        if self._explicit_ttl is not None:
            return float(self._explicit_ttl)
        return float(getattr(settings, "price_cache_ttl_seconds", 30))

    def get(self, key: str) -> Optional[dict[str, Any]]:
        ttl_val = self.ttl
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                data, timestamp = entry
                if time.monotonic() - timestamp < ttl_val:
                    return dict(data)
                del self._cache[key]
        return None

    def set(self, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._cache[key] = (dict(data), time.monotonic())

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class _OHLCVCache:
    """Cache thread-safe de DataFrames OHLCV com TTL e cópia defensiva."""

    def __init__(self, ttl_seconds: Optional[int] = None) -> None:
        self._cache: dict[tuple[str, str, str], tuple[pd.DataFrame, float]] = {}
        self._lock: threading.Lock = threading.Lock()
        self._explicit_ttl: Optional[int] = ttl_seconds

    @property
    def ttl(self) -> float:
        if self._explicit_ttl is not None:
            return float(self._explicit_ttl)
        return float(getattr(settings, "ohlcv_cache_ttl_seconds", 300))

    def get(self, key: tuple[str, str, str]) -> Optional[pd.DataFrame]:
        ttl_val = self.ttl
        with self._lock:
            entry = self._cache.get(key)
            if entry is not None:
                df, timestamp = entry
                if time.monotonic() - timestamp < ttl_val:
                    return df.copy()
                del self._cache[key]
        return None

    def set(self, key: tuple[str, str, str], df: pd.DataFrame) -> None:
        if df is None or df.empty:
            return
        with self._lock:
            self._cache[key] = (df.copy(), time.monotonic())

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


class MarketData:
    """
    Provedor de dados de mercado para ativos da B3.

    Usa yfinance como fonte primária com fallback para MetaTrader 5.
    Mantém caches thread-safe para cotações e OHLCV, normalização unificada
    de tickers e proteção contra cotações defasadas na abertura do pregão.
    """

    MAX_RETRIES: int = 3
    BASE_BACKOFF_SECONDS: float = 1.0

    def __init__(self, broker: Any = None) -> None:
        self._broker: Any = broker
        self._cache: _PriceCache = _PriceCache()
        self._ohlcv_cache: _OHLCVCache = _OHLCVCache()
        self._yf_available: bool = False
        self._price_cache_legacy: dict[str, float] = {}
        self._previous_prices: dict[str, float] = {}
        self._quote_info: dict[str, dict[str, Any]] = {}
        self._legacy_lock: threading.Lock = threading.Lock()
        self._bridge_local = threading.local()
        self._bridge_sessions_lock: threading.Lock = threading.Lock()
        self._bridge_sessions: list[requests.Session] = []
        self._primary_bridge_session: Optional[requests.Session] = None
        self._bridge_backoff_until: float = 0.0
        self._regime_cache: tuple[dict[str, Any], float] | None = None
        self._executor_lock: threading.Lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=_MAX_YF_WORKERS,
            thread_name_prefix="cortex-yf",
        )
        # Inicializa a sessão Bridge da thread principal
        self._primary_bridge_session = self._get_bridge_session()

        try:
            import yfinance  # noqa: F401
            self._yf_available = True
            logger.info("yfinance disponível como fonte primária de dados")
        except ImportError:
            logger.warning(
                "yfinance não instalado — funcionalidade de market data limitada. "
                "Execute: pip install yfinance"
            )

        self._mt5_available: bool = False
        try:
            import MetaTrader5  # noqa: F401
            self._mt5_available = True
            logger.info("MetaTrader5 disponível como fonte secundária")
        except ImportError:
            pass

        logger.info(
            "MarketData inicializado — yfinance: %s, mt5: %s",
            self._yf_available,
            self._mt5_available,
        )

    def _get_executor(self) -> ThreadPoolExecutor:
        """Retorna o ThreadPoolExecutor persistente para buscas concorrentes, recriando após close()."""
        with self._executor_lock:
            if self._executor is None or getattr(self._executor, "_shutdown", False):
                self._executor = ThreadPoolExecutor(
                    max_workers=_MAX_YF_WORKERS,
                    thread_name_prefix="cortex-yf",
                )
            return self._executor

    def _create_bridge_session(self) -> requests.Session:
        sess = requests.Session()
        adapter = HTTPAdapter(pool_connections=4, pool_maxsize=8)
        sess.mount("http://", adapter)
        sess.mount("https://", adapter)
        with self._bridge_sessions_lock:
            self._bridge_sessions.append(sess)
        return sess

    def _get_bridge_session(self) -> requests.Session:
        """Retorna `requests.Session` isolado por thread para o MT5 Bridge."""
        primary = self._primary_bridge_session
        if primary is not None and "get" in getattr(primary, "__dict__", {}):
            return primary
        sess = getattr(self._bridge_local, "session", None)
        if sess is None:
            sess = self._create_bridge_session()
            self._bridge_local.session = sess
        return sess

    @property
    def _bridge_session(self) -> requests.Session:
        return self._get_bridge_session()

    @_bridge_session.setter
    def _bridge_session(self, session: requests.Session) -> None:
        self._bridge_local.session = session
        self._primary_bridge_session = session
        with self._bridge_sessions_lock:
            if session not in self._bridge_sessions:
                self._bridge_sessions.append(session)

    def close(self) -> None:
        """Encerra o pool persistente de threads e fecha todas as sessões HTTP do MT5 Bridge."""
        with self._executor_lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:
                logger.debug("Erro ao encerrar executor do MarketData: %s", exc)

        with self._bridge_sessions_lock:
            sessions = list(self._bridge_sessions)
            self._bridge_sessions.clear()
        self._primary_bridge_session = None
        for sess in sessions:
            try:
                sess.close()
            except Exception as exc:
                logger.debug("Erro ao fechar sessão HTTP do Bridge: %s", exc)
        self._bridge_local = threading.local()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _record_quote_metadata(
        self,
        base_ticker: str,
        price: float,
        timestamp: str,
        is_stale: bool,
    ) -> None:
        """Registra metadados da última cotação válida de forma thread-safe."""
        if not base_ticker or price <= 0.0 or math.isnan(price):
            return
        with self._legacy_lock:
            if base_ticker in self._price_cache_legacy:
                self._previous_prices[base_ticker] = self._price_cache_legacy[base_ticker]
            self._price_cache_legacy[base_ticker] = float(price)
            self._quote_info[base_ticker] = {
                "price": float(price),
                "timestamp": str(timestamp),
                "is_stale": bool(is_stale),
            }

    def get_quote_info(self, ticker: str) -> Optional[dict[str, Any]]:
        """
        Retorna metadados da cotação mais recente de um ativo:
        `{"price": float, "timestamp": str, "is_stale": bool}` ou None.
        """
        base = normalize_b3_ticker(ticker)
        if not base:
            return None

        cached = self._cache.get(base)
        if cached is not None:
            price = float(cached.get("last", 0.0) or 0.0)
            if price > 0.0:
                return {
                    "price": price,
                    "timestamp": str(cached.get("timestamp", "")),
                    "is_stale": bool(cached.get("is_stale", False)),
                }

        with self._legacy_lock:
            info = self._quote_info.get(base)
            if info is not None:
                return dict(info)
        return None

    def is_quote_stale(self, ticker: str) -> bool:
        """
        Retorna True se a cotação em uso durante o pregão da B3 pertence ao
        fechamento anterior (sem negócio intraday hoje). Retorna False se a
        cotação for fresca, manual ou oriunda de mock/teste.
        """
        info = self.get_quote_info(ticker)
        if info is None:
            return False
        return bool(info.get("is_stale", False))

    def get_cached_price(self, ticker: str) -> Optional[float]:
        """
        Retorna o último preço válido em cache (TTL ou legado) sem realizar chamadas externas.
        """
        base = normalize_b3_ticker(ticker)
        if not base:
            return None

        cached = self._cache.get(base)
        if cached is not None:
            last = float(cached.get("last", 0.0) or 0.0)
            if last > 0.0 and not math.isnan(last):
                return last

        with self._legacy_lock:
            legacy = self._price_cache_legacy.get(base)
        if legacy is not None and legacy > 0.0 and not math.isnan(legacy):
            return float(legacy)
        return None

    def _get_bridge_base_url(self) -> str:
        """Retorna a URL configurada do MT5 Bridge se disponível e fora de backoff."""
        url = str(getattr(settings, "MT5_BRIDGE_URL", "") or "").strip().rstrip("/")
        if not url:
            return ""
        if time.monotonic() < self._bridge_backoff_until:
            return ""
        return url

    def _bridge_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        api_key = str(getattr(settings, "BRIDGE_API_KEY", "") or "").strip()
        if api_key:
            headers["X-Bridge-Api-Key"] = api_key
        return headers

    def _get_price_bridge(self, ticker: str) -> Optional[dict[str, Any]]:
        """
        Obtém cotação em tempo real (0ms de atraso B3) diretamente do MT5 Bridge
        quando conectado à corretora. Em falha de rede/503 aplica backoff curto
        para fallback transparente ao yfinance.
        """
        base_url = self._get_bridge_base_url()
        if not base_url:
            return None

        base = normalize_b3_ticker(ticker)
        if not base:
            return None

        unit_sym = format_b3_ticker_for_quantity(base, 1)
        candidates = [base] if unit_sym == base else [base, unit_sym]

        for symbol in candidates:
            try:
                resp = self._bridge_session.get(
                    f"{base_url}/tick/{symbol}",
                    headers=self._bridge_headers(),
                    timeout=2.5,
                )
                if resp.status_code in (502, 503, 504, 403):
                    self._bridge_backoff_until = time.monotonic() + 30.0
                    return None
                if resp.status_code != 200:
                    continue

                payload = resp.json()
                if not isinstance(payload, dict):
                    continue

                bid = float(payload.get("bid", 0.0) or 0.0)
                ask = float(payload.get("ask", 0.0) or 0.0)
                last = float(payload.get("last", 0.0) or 0.0)
                if last <= 0.0 and bid <= 0.0:
                    continue

                effective_last = last if last > 0.0 else bid
                effective_bid = bid if bid > 0.0 else effective_last
                effective_ask = ask if ask > 0.0 else effective_last
                raw_time = int(payload.get("time", 0) or 0)
                now_brt = datetime.now(tz=BRT)
                if raw_time > 0:
                    try:
                        tick_dt = datetime.fromtimestamp(raw_time, tz=BRT)
                        ts_iso = tick_dt.isoformat()
                        is_stale = bool(
                            _is_b3_market_hours(now_brt)
                            and tick_dt.date() < now_brt.date()
                        )
                    except (OSError, ValueError):
                        ts_iso = now_brt.isoformat()
                        is_stale = False
                else:
                    ts_iso = now_brt.isoformat()
                    is_stale = False

                return {
                    "bid": effective_bid,
                    "ask": effective_ask,
                    "last": effective_last,
                    "spread_pct": float(payload.get("spread_pct", 0.0) or 0.0),
                    "volume": int(payload.get("volume_real", 0) or payload.get("volume", 0) or 0),
                    "timestamp": ts_iso,
                    "source": "mt5_bridge",
                    "ticker": base,
                    "is_stale": is_stale,
                }
            except requests.RequestException:
                self._bridge_backoff_until = time.monotonic() + 30.0
                return None
            except Exception as exc:
                logger.debug("Aviso ao consultar tick Bridge para %s: %s", symbol, exc)
                return None
        return None

    def _get_ohlcv_bridge(
        self,
        base: str,
        period: str = "3mo",
        interval: str = "1d",
    ) -> Optional[pd.DataFrame]:
        """
        Obtém candles OHLCV em tempo real direto da B3 via MT5 Bridge (`/rates/{symbol}`),
        garantindo profundidade mínima para cálculo dos indicadores técnicos.
        """
        base_url = self._get_bridge_base_url()
        if not base_url:
            return None

        norm_interval = (interval or "1d").strip().lower()
        norm_period = (period or "3mo").strip().lower()
        tf_map = {
            "1m": ("M1", 120, 20),
            "5m": ("M5", 120, 25),
            "15m": ("M15", 120, 25),
            "30m": ("M30", 120, 25),
            "60m": ("H1", 140, 52),
            "1h": ("H1", 140, 52),
            "1d": (
                "D1",
                520 if norm_period == "2y"
                else 260 if norm_period == "1y"
                else 140 if norm_period == "6mo"
                else 95 if norm_period == "3mo"
                else 75 if norm_period == "2mo"
                else 65 if norm_period == "1mo"
                else 10 if norm_period == "5d"
                else 120,
                3 if norm_period == "5d" else 25 if norm_period == "1mo" else 55,
            ),
            "1wk": ("W1", 104, 20),
            "1mo": ("MN1", 60, 12),
        }
        if norm_interval not in tf_map:
            return None

        mt5_tf, count, min_bars = tf_map[norm_interval]
        unit_sym = format_b3_ticker_for_quantity(base, 1)
        candidates = [base] if unit_sym == base else [base, unit_sym]

        for symbol in candidates:
            try:
                resp = self._bridge_session.get(
                    f"{base_url}/rates/{symbol}",
                    params={"timeframe": mt5_tf, "count": count},
                    headers=self._bridge_headers(),
                    timeout=4.0,
                )
                if resp.status_code in (502, 503, 504, 403):
                    self._bridge_backoff_until = time.monotonic() + 30.0
                    return None
                if resp.status_code != 200:
                    continue

                rows = resp.json()
                if not isinstance(rows, list) or len(rows) < min_bars:
                    continue

                df = pd.DataFrame(rows)
                if not {"time", "open", "high", "low", "close"}.issubset(df.columns):
                    continue

                vol_series = (
                    df["volume"]
                    if "volume" in df.columns
                    else df.get("real_volume", df.get("tick_volume", 0))
                )
                if mt5_tf in ("D1", "W1", "MN1"):
                    idx = (
                        pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True)
                        .dt.tz_localize(None)
                        .dt.tz_localize(BRT)
                    )
                else:
                    idx = pd.to_datetime(df["time"].astype("int64"), unit="s", utc=True).dt.tz_convert(BRT)
                out_df = pd.DataFrame(
                    {
                        "Open": df["open"].astype(float).to_numpy(),
                        "High": df["high"].astype(float).to_numpy(),
                        "Low": df["low"].astype(float).to_numpy(),
                        "Close": df["close"].astype(float).to_numpy(),
                        "Volume": pd.Series(vol_series).fillna(0).astype("int64").to_numpy(),
                    },
                    index=pd.DatetimeIndex(idx, name="Date"),
                )
                out_df = out_df[out_df["Close"] > 0]
                if len(out_df) >= min_bars:
                    return out_df
            except requests.RequestException:
                self._bridge_backoff_until = time.monotonic() + 30.0
                return None
            except Exception as exc:
                logger.debug("Aviso ao obter OHLCV via Bridge para %s: %s", symbol, exc)
                return None
        return None

    def get_current_price(self, ticker: str) -> dict[str, Any]:
        """
        Obtém preço atual de um ativo (prioriza MT5 Bridge tempo real 0ms quando ativa,
        seguido de yfinance e MT5 nativo como fallback).
        """
        base = normalize_b3_ticker(ticker)
        if not base:
            raise ValueError(f"Ticker inválido: '{ticker}'")

        cached = self._cache.get(base)
        if cached is not None:
            logger.debug("Cache hit para %s", base)
            return cached

        bridge_result = self._get_price_bridge(base)
        if bridge_result is not None and float(bridge_result.get("last", 0.0) or 0.0) > 0.0:
            if not bridge_result.get("is_stale", False):
                self._cache.set(base, bridge_result)
                self._record_quote_metadata(
                    base,
                    float(bridge_result["last"]),
                    str(bridge_result["timestamp"]),
                    False,
                )
                return bridge_result

        yf_result: Optional[dict[str, Any]] = None
        if self._yf_available:
            yf_result = self._get_price_yfinance(base)
            if yf_result is not None and float(yf_result.get("last", 0.0) or 0.0) > 0.0:
                if not yf_result.get("is_stale", False):
                    self._cache.set(base, yf_result)
                    self._record_quote_metadata(
                        base,
                        float(yf_result["last"]),
                        str(yf_result["timestamp"]),
                        bool(yf_result.get("is_stale", False)),
                    )
                    return yf_result

        if self._mt5_available:
            mt5_result = self._get_price_mt5(base)
            if mt5_result is not None and float(mt5_result.get("last", 0.0) or 0.0) > 0.0:
                self._cache.set(base, mt5_result)
                self._record_quote_metadata(
                    base,
                    float(mt5_result["last"]),
                    str(mt5_result["timestamp"]),
                    False,
                )
                return mt5_result

        for fallback_res in (bridge_result, yf_result):
            if fallback_res is not None and float(fallback_res.get("last", 0.0) or 0.0) > 0.0:
                self._cache.set(base, fallback_res)
                self._record_quote_metadata(
                    base,
                    float(fallback_res["last"]),
                    str(fallback_res["timestamp"]),
                    bool(fallback_res.get("is_stale", False)),
                )
                return fallback_res

        raise ValueError(
            f"Não foi possível obter preço de {base} — "
            "nenhuma fonte de dados disponível"
        )

    def get_prices_batch(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        """
        Obtém cotações atuais de uma lista de ativos com concorrência limitada
        e cache thread-safe.
        """
        results: dict[str, dict[str, Any]] = {}
        missing_tickers: list[str] = []
        seen_missing: set[str] = set()

        for raw_ticker in tickers:
            clean = normalize_b3_ticker(raw_ticker)
            if not clean:
                continue
            cached = self._cache.get(clean)
            if cached is not None:
                results[clean] = cached
            elif clean not in seen_missing:
                seen_missing.add(clean)
                missing_tickers.append(clean)

        if not missing_tickers:
            return results

        def _fetch_single(t: str) -> tuple[str, Optional[dict[str, Any]]]:
            try:
                return t, self.get_current_price(t)
            except Exception as exc:
                logger.debug("Falha ao obter preço para %s no lote: %s", t, exc)
                return t, None

        executor = self._get_executor()
        future_to_ticker = {executor.submit(_fetch_single, t): t for t in missing_tickers}
        for future in as_completed(future_to_ticker):
            t, price_data = future.result()
            if price_data is not None:
                results[t] = price_data

        return results

    def get_ohlcv_batch(
        self,
        tickers: list[str],
        period: str = "1y",
        interval: str = "1d",
    ) -> dict[str, pd.DataFrame]:
        """
        Obtém dados históricos OHLCV para múltiplos ativos aproveitando cache TTL
        e limitando concorrência para proteger o cache interno do yfinance.
        """
        results: dict[str, pd.DataFrame] = {}
        missing_bases: list[str] = []
        base_to_raw: dict[str, list[str]] = {}

        for raw_ticker in tickers:
            base = normalize_b3_ticker(raw_ticker)
            if not base:
                results[raw_ticker] = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
                continue
            base_to_raw.setdefault(base, []).append(raw_ticker)
            cached_df = self._ohlcv_cache.get((base, period, interval))
            if cached_df is not None:
                results[raw_ticker] = cached_df
            elif base not in missing_bases:
                missing_bases.append(base)

        if not missing_bases:
            return results

        def _fetch_df(base_t: str) -> tuple[str, pd.DataFrame]:
            try:
                df = self.get_ohlcv(base_t, period=period, interval=interval)
                return base_t, df
            except Exception as exc:
                logger.warning("Falha ao obter OHLCV para %s no lote: %s", base_t, exc)
                return base_t, pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        executor = self._get_executor()
        future_to_base = {executor.submit(_fetch_df, b): b for b in missing_bases}
        for future in as_completed(future_to_base):
            base_t, df = future.result()
            for raw_t in base_to_raw.get(base_t, [base_t]):
                results[raw_t] = df.copy()

        return results

    @staticmethod
    def _ensure_yf_warmed(yf_module: Any, yf_symbol: str) -> Any:
        """
        Garante que a primeira inicialização de sessão/crumb SQLite do yfinance
        ocorra de forma serializada entre threads.
        """
        global _YF_SESSION_WARMED
        if _YF_SESSION_WARMED:
            return yf_module.Ticker(yf_symbol)
        with _YF_INIT_LOCK:
            ticker_obj = yf_module.Ticker(yf_symbol)
            _YF_SESSION_WARMED = True
            return ticker_obj

    @staticmethod
    def _extract_last_close_and_vol(hist: pd.DataFrame) -> tuple[float, int]:
        """Extrai último Close e Volume de um DataFrame OHLCV (achatando MultiIndex se presente)."""
        df = hist
        if isinstance(df.columns, pd.MultiIndex):
            df = df.copy()
            df.columns = df.columns.get_level_values(0)
        last_row = df.iloc[-1]
        close_val = float(last_row["Close"])
        raw_vol = last_row.get("Volume", 0)
        vol_val = 0 if (raw_vol is None or (isinstance(raw_vol, float) and math.isnan(raw_vol))) else int(raw_vol)
        return close_val, vol_val

    def _get_price_yfinance(self, ticker: str) -> Optional[dict[str, Any]]:
        """
        Obtém preço atual via yfinance com retry, backoff e verificação de
        defasagem intraday durante o pregão da B3.
        """
        import yfinance as yf

        base = normalize_b3_ticker(ticker)
        yf_symbol = _b3_to_yfinance(base)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                now_brt = datetime.now(tz=BRT)
                today_brt = now_brt.date()
                in_market_hours = _is_b3_market_hours(now_brt)
                yf_ticker = self._ensure_yf_warmed(yf, yf_symbol)

                # Fora do pregão regular, fast_info é suficiente e rápido
                if not in_market_hours:
                    try:
                        fast = yf_ticker.fast_info
                        last_price = float(
                            fast.get("lastPrice", 0)
                            or fast.get("last_price", 0)
                            or 0
                        )
                        if last_price > 0.0 and not math.isnan(last_price):
                            raw_vol = fast.get("lastVolume", 0) or fast.get("last_volume", 0) or 0
                            vol = 0 if (isinstance(raw_vol, float) and math.isnan(raw_vol)) else int(raw_vol)
                            return {
                                "bid": last_price,
                                "ask": last_price,
                                "last": last_price,
                                "volume": vol,
                                "timestamp": now_brt.isoformat(),
                                "source": "yfinance_fast_info",
                                "ticker": base,
                                "is_stale": False,
                            }
                    except Exception:
                        pass

                # Consulta intraday 1d/1m para validar negócio no pregão de hoje
                hist = yf_ticker.history(period="1d", interval="1m")
                if isinstance(hist, pd.DataFrame):
                    if not hist.empty:
                        last_price, volume = self._extract_last_close_and_vol(hist)
                        if last_price > 0.0 and not math.isnan(last_price):
                            bar_date = _index_last_date_brt(hist.index)
                            is_stale = bool(
                                in_market_hours
                                and bar_date is not None
                                and bar_date < today_brt
                            )
                            ts_iso = _index_last_iso_brt(hist.index, now_brt)
                            return {
                                "bid": last_price,
                                "ask": last_price,
                                "last": last_price,
                                "volume": volume,
                                "timestamp": ts_iso,
                                "source": "yfinance_history",
                                "ticker": base,
                                "is_stale": is_stale,
                            }
                else:
                    # Caso history seja um mock em testes unitários que apenas mockaram fast_info
                    try:
                        fast = yf_ticker.fast_info
                        last_price = float(
                            fast.get("lastPrice", 0)
                            or fast.get("last_price", 0)
                            or 0
                        )
                        if last_price > 0.0 and not math.isnan(last_price):
                            return {
                                "bid": last_price,
                                "ask": last_price,
                                "last": last_price,
                                "volume": int(fast.get("lastVolume", 0) or fast.get("last_volume", 0) or 0),
                                "timestamp": now_brt.isoformat(),
                                "source": "yfinance_fast_info",
                                "ticker": base,
                                "is_stale": False,
                            }
                    except Exception:
                        pass

                # Fallback para histórico diário (5d / 1mo): se estivermos em horário de pregão
                # e 1d/1m não teve barra de hoje, marca is_stale=True!
                for period_fallback, src_name in (("5d", "yfinance_daily"), ("1mo", "yfinance_monthly")):
                    hist_daily = yf_ticker.history(period=period_fallback)
                    if isinstance(hist_daily, pd.DataFrame) and not hist_daily.empty:
                        last_price, volume = self._extract_last_close_and_vol(hist_daily)
                        if last_price > 0.0 and not math.isnan(last_price):
                            bar_date = _index_last_date_brt(hist_daily.index)
                            is_stale = bool(
                                in_market_hours
                                and (bar_date is None or bar_date < today_brt)
                            )
                            ts_iso = _index_last_iso_brt(hist_daily.index, now_brt)
                            if is_stale:
                                logger.warning(
                                    "Cotação defasada detectada para %s (%s): fechamento anterior R$%.2f sem barra intraday hoje.",
                                    base, src_name, last_price,
                                )
                            return {
                                "bid": last_price,
                                "ask": last_price,
                                "last": last_price,
                                "volume": volume,
                                "timestamp": ts_iso,
                                "source": src_name,
                                "ticker": base,
                                "is_stale": is_stale,
                            }

                return None

            except Exception as exc:
                wait_time = self.BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.debug(
                    "Erro yfinance para %s (tentativa %d/%d): %s — aguardando %.1fs",
                    yf_symbol, attempt, self.MAX_RETRIES, exc, wait_time,
                )
                if attempt < self.MAX_RETRIES:
                    time.sleep(wait_time)

        logger.debug(
            "Falha ao obter preço de %s via yfinance após %d tentativas",
            base, self.MAX_RETRIES,
        )
        return None

    def _get_price_mt5(self, ticker: str) -> Optional[dict[str, Any]]:
        """
        Obtém preço atual via MetaTrader 5 selecionando símbolo no Market Watch
        e validando preços estritamente positivos.
        """
        base = normalize_b3_ticker(ticker)
        if not base:
            return None

        try:
            import MetaTrader5 as mt5

            unit_sym = format_b3_ticker_for_quantity(base, 1)
            candidates = [unit_sym] if unit_sym == base else [unit_sym, base]

            for symbol in candidates:
                try:
                    mt5.symbol_select(symbol, True)
                except Exception:
                    pass
                tick = mt5.symbol_info_tick(symbol)
                if tick is None:
                    continue

                bid = float(getattr(tick, "bid", 0.0) or 0.0)
                ask = float(getattr(tick, "ask", 0.0) or 0.0)
                last = float(getattr(tick, "last", 0.0) or 0.0)

                if last <= 0.0 and bid <= 0.0:
                    continue

                effective_last = last if last > 0.0 else bid
                effective_bid = bid if bid > 0.0 else effective_last
                effective_ask = ask if ask > 0.0 else effective_last

                result: dict[str, Any] = {
                    "bid": effective_bid,
                    "ask": effective_ask,
                    "last": effective_last,
                    "volume": int(getattr(tick, "volume", 0) or 0),
                    "timestamp": datetime.now(tz=BRT).isoformat(),
                    "source": "mt5",
                    "ticker": base,
                    "is_stale": False,
                }
                logger.debug(
                    "Preço %s via MT5 (%s): bid=R$%.2f, ask=R$%.2f, last=R$%.2f",
                    base, symbol, effective_bid, effective_ask, effective_last,
                )
                return result

        except Exception as exc:
            logger.warning("Erro ao obter preço via MT5 para %s: %s", base, exc)
        return None

    def get_ohlcv(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        Obtém dados históricos OHLCV com cache TTL thread-safe e cópia defensiva.
        """
        base = normalize_b3_ticker(ticker)
        empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        if not base:
            return empty_df

        cache_key = (base, period, interval)
        cached_df = self._ohlcv_cache.get(cache_key)
        if cached_df is not None:
            logger.debug("OHLCV cache hit para %s (%s/%s)", base, period, interval)
            return cached_df

        bridge_df = self._get_ohlcv_bridge(base, period=period, interval=interval)
        if bridge_df is not None and not bridge_df.empty:
            self._ohlcv_cache.set(cache_key, bridge_df)
            logger.info(
                "OHLCV obtido via MT5 Bridge (0ms): %s | %d candles | period=%s, interval=%s",
                base, len(bridge_df), period, interval,
            )
            return bridge_df.copy()

        if not self._yf_available:
            logger.error("yfinance não disponível para dados OHLCV")
            return empty_df

        yf_symbol = _b3_to_yfinance(base)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                import yfinance as yf

                yf_ticker = self._ensure_yf_warmed(yf, yf_symbol)
                hist = yf_ticker.history(period=period, interval=interval)

                if hist is None or hist.empty:
                    logger.debug(
                        "Dados OHLCV vazios para %s (period=%s, interval=%s)",
                        base, period, interval,
                    )
                    return empty_df

                if isinstance(hist.columns, pd.MultiIndex):
                    hist = hist.copy()
                    hist.columns = hist.columns.get_level_values(0)

                columns = ["Open", "High", "Low", "Close", "Volume"]
                available_cols = [c for c in columns if c in hist.columns]
                result = hist[available_cols].copy()

                if not result.empty:
                    self._ohlcv_cache.set(cache_key, result)

                logger.info(
                    "OHLCV obtido: %s | %d candles | period=%s, interval=%s",
                    base, len(result), period, interval,
                )
                return result.copy()

            except Exception as exc:
                wait_time = self.BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.debug(
                    "Erro ao obter OHLCV de %s (tentativa %d/%d): %s — aguardando %.1fs",
                    yf_symbol, attempt, self.MAX_RETRIES, exc, wait_time,
                )
                if attempt < self.MAX_RETRIES:
                    time.sleep(wait_time)

        logger.debug(
            "Falha ao obter OHLCV de %s após %d tentativas",
            base, self.MAX_RETRIES,
        )
        return empty_df

    def get_intraday(
        self,
        ticker: str,
        interval: str = "1m",
        period: str = "1d",
    ) -> pd.DataFrame:
        """Obtém dados intraday OHLCV."""
        return self.get_ohlcv(ticker, period=period, interval=interval)

    def get_multiple_prices(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        """Obtém preços de múltiplos ativos com fallback seguro de erro."""
        if not tickers:
            return {}

        results: dict[str, dict[str, Any]] = {}

        def _fetch_one(raw_ticker: str) -> tuple[str, dict[str, Any]]:
            try:
                return raw_ticker, self.get_current_price(raw_ticker)
            except Exception as exc:
                logger.warning("Falha ao obter preço de %s: %s", raw_ticker, exc)
                return raw_ticker, {
                    "bid": 0.0,
                    "ask": 0.0,
                    "last": 0.0,
                    "volume": 0,
                    "timestamp": datetime.now(tz=BRT).isoformat(),
                    "source": "error",
                    "ticker": normalize_b3_ticker(raw_ticker) or raw_ticker,
                    "is_stale": False,
                    "error": str(exc),
                }

        executor = self._get_executor()
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for future in as_completed(futures):
            raw_ticker, data = future.result()
            results[raw_ticker] = data

        return results

    def clear_cache(self) -> None:
        """Limpa os caches de preços, OHLCV e regime de mercado."""
        self._cache.clear()
        self._ohlcv_cache.clear()
        with self._legacy_lock:
            self._regime_cache = None
        logger.debug("Caches de preços e OHLCV limpos")

    def _update_legacy_cache(self, ticker: str, price: float) -> None:
        """Atualiza cache legado de preços simples (thread-safe e normalizado)."""
        base = normalize_b3_ticker(ticker)
        if not base or price <= 0.0 or math.isnan(price):
            return
        with self._legacy_lock:
            if base in self._price_cache_legacy:
                self._previous_prices[base] = self._price_cache_legacy[base]
            self._price_cache_legacy[base] = float(price)

    def set_price(self, ticker: str, price: float) -> None:
        """Define preço manualmente (útil para simulação/testes)."""
        base = normalize_b3_ticker(ticker) or ticker.strip().upper()
        ts = datetime.now(tz=BRT).isoformat()
        self._record_quote_metadata(base, float(price), ts, is_stale=False)
        self._cache.set(base, {
            "bid": float(price),
            "ask": float(price),
            "last": float(price),
            "volume": 0,
            "timestamp": ts,
            "source": "manual",
            "ticker": base,
            "is_stale": False,
        })

    def get_variation(self, ticker: str) -> Optional[float]:
        """Calcula a variação percentual desde a última atualização normalizando sufixo 'F'."""
        base = normalize_b3_ticker(ticker)
        if not base:
            return None
        with self._legacy_lock:
            current = self._price_cache_legacy.get(base)
            previous = self._previous_prices.get(base)
        if current is None or previous is None or previous == 0.0:
            return None
        return ((current - previous) / previous) * 100.0

    def get_prices(self, tickers: list[str]) -> dict[str, Optional[float]]:
        """Obtém preços atuais para múltiplos ativos em paralelo com cache thread-safe."""
        batch_data = self.get_prices_batch(tickers)
        prices: dict[str, Optional[float]] = {}
        for ticker in tickers:
            clean = normalize_b3_ticker(ticker)
            data = batch_data.get(clean)
            if data is not None and "last" in data and float(data.get("last", 0.0) or 0.0) > 0.0:
                prices[ticker] = float(data["last"])
            else:
                prices[ticker] = self.get_cached_price(clean)
        return prices

    def update_prices(self, tickers: list[str]) -> dict[str, Optional[float]]:
        """Atualiza preços de todos os ativos monitorados (interface legado)."""
        logger.debug("Atualizando preços de %d ativos", len(tickers))
        return self.get_prices(tickers)

    def get_market_regime(self, benchmark: str = "BOVA11") -> dict[str, Any]:
        """
        Avalia o regime intradiário do mercado brasileiro usando `BOVA11` como proxy do Ibovespa.
        Retorna `is_bearish_crash=True` quando a queda intradiária atinge ou supera
        `settings.MARKET_REGIME_MAX_DROP_PCT` (padrão `-1.5%`).
        """
        now_mono = time.monotonic()
        with self._legacy_lock:
            cached_regime = self._regime_cache
        if cached_regime is not None:
            cached_data, cached_ts = cached_regime
            if (now_mono - cached_ts) < 60.0 and cached_data.get("benchmark") == benchmark:
                return dict(cached_data)

        max_drop = float(getattr(settings, "MARKET_REGIME_MAX_DROP_PCT", -1.5) or -1.5)
        default_res = {
            "benchmark": benchmark,
            "change_pct": 0.0,
            "is_bearish_crash": False,
        }
        try:
            df = self.get_ohlcv(benchmark, period="5d", interval="1d")
            if df is None or df.empty or len(df) < 2:
                return default_res

            today_brt = datetime.now(tz=BRT).date()
            last_bar_date = _index_last_date_brt(df.index)
            if last_bar_date == today_brt and len(df) >= 2:
                prev_close = float(df["Close"].iloc[-2])
            else:
                prev_close = float(df["Close"].iloc[-1])

            if prev_close <= 0:
                return default_res

            px_info = self.get_current_price(benchmark)
            curr_px = float(px_info.get("last", 0.0) or 0.0)
            if curr_px <= 0:
                curr_px = float(df["Close"].iloc[-1])

            change_pct = ((curr_px - prev_close) / prev_close) * 100.0
            res = {
                "benchmark": benchmark,
                "change_pct": round(change_pct, 2),
                "is_bearish_crash": bool(change_pct <= max_drop),
            }
            with self._legacy_lock:
                self._regime_cache = (res, now_mono)
            return dict(res)
        except Exception as exc:
            logger.debug("Regime de mercado indisponível para %s: %s", benchmark, exc)
            return default_res

    def __repr__(self) -> str:
        return (
            f"MarketData(yfinance={self._yf_available}, "
            f"mt5={self._mt5_available})"
        )
