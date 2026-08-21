"""
Dados de mercado do Projeto Córtex.

Fornece cotações em tempo real e dados históricos OHLCV usando
yfinance como fonte primária (cross-platform) com fallback para
MetaTrader 5 quando disponível. Cache thread-safe com TTL para
evitar spam de API.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Optional

import pandas as pd
from zoneinfo import ZoneInfo

from config.settings import settings

logger = logging.getLogger("cortex.data.market_data")

# TODO: When utils.logger is importable without circular deps, switch to:
# from utils.logger import get_logger
# logger = get_logger(__name__)

BRT: ZoneInfo = ZoneInfo("America/Sao_Paulo")

# ═══════════════════════════════════════════════════════════════════
#  Mapeamento de tickers B3 → Yahoo Finance
# ═══════════════════════════════════════════════════════════════════

def _b3_to_yfinance(ticker: str) -> str:
    """
    Converte ticker B3 para formato Yahoo Finance.

    Args:
        ticker: Código B3 (ex.: 'PETR4', 'PETR4F').

    Returns:
        Código Yahoo Finance (ex.: 'PETR4.SA').
    """
    clean = ticker.upper().strip().rstrip("Ff")
    if not clean.endswith(".SA"):
        clean += ".SA"
    return clean


def _yfinance_to_b3(ticker: str) -> str:
    """
    Converte ticker Yahoo Finance para formato B3.

    Args:
        ticker: Código Yahoo Finance (ex.: 'PETR4.SA').

    Returns:
        Código B3 (ex.: 'PETR4').
    """
    return ticker.upper().replace(".SA", "").strip()


# ═══════════════════════════════════════════════════════════════════
#  Cache de preços com TTL
# ═══════════════════════════════════════════════════════════════════

class _PriceCache:
    """Cache thread-safe de preços com TTL configurável."""

    def __init__(self, ttl_seconds: int = settings.price_cache_ttl_seconds) -> None:
        """
        Inicializa o cache.

        Args:
            ttl_seconds: Tempo de vida de cada entrada (em segundos).
        """
        self._cache: dict[str, tuple[dict[str, Any], float]] = {}
        self._lock: threading.Lock = threading.Lock()
        self._ttl: int = ttl_seconds

    def get(self, key: str) -> Optional[dict[str, Any]]:
        """
        Retorna dados do cache se ainda válidos.

        Args:
            key: Chave de busca (ticker).

        Returns:
            Dados em cache ou None se expirado/inexistente.
        """
        with self._lock:
            if key in self._cache:
                data, timestamp = self._cache[key]
                if time.monotonic() - timestamp < self._ttl:
                    return data
                del self._cache[key]
        return None

    def set(self, key: str, data: dict[str, Any]) -> None:
        """
        Armazena dados no cache.

        Args:
            key: Chave de armazenamento (ticker).
            data: Dados de preço.
        """
        with self._lock:
            self._cache[key] = (data, time.monotonic())

    def clear(self) -> None:
        """Limpa todo o cache."""
        with self._lock:
            self._cache.clear()


# ═══════════════════════════════════════════════════════════════════
#  Classe principal
# ═══════════════════════════════════════════════════════════════════

class MarketData:
    """
    Provedor de dados de mercado.

    Usa yfinance como fonte primária (funciona em qualquer SO).
    Fallback para MT5 quando disponível e conectado. Cache com TTL
    para evitar excesso de requisições. Retry com backoff exponencial
    para rate limits.

    Mantém compatibilidade retroativa com a interface anterior
    (get_current_price retorna dict, mas set_price e get_variation
    continuam funcionando para código legado).
    """

    MAX_RETRIES: int = 3
    BASE_BACKOFF_SECONDS: float = 1.0

    def __init__(self, broker: Any = None) -> None:
        """
        Inicializa o provedor de dados de mercado.

        Args:
            broker: Instância de BrokerBase (opcional, usado para fallback MT5).
        """
        self._broker: Any = broker
        self._cache: _PriceCache = _PriceCache()
        self._yf_available: bool = False
        # Caches legado para compatibilidade
        self._price_cache_legacy: dict[str, float] = {}
        self._previous_prices: dict[str, float] = {}
        self._legacy_lock: threading.Lock = threading.Lock()

        # Verificar disponibilidade do yfinance
        try:
            import yfinance  # noqa: F401
            self._yf_available = True
            logger.info("yfinance disponível como fonte primária de dados")
        except ImportError:
            logger.warning(
                "yfinance não instalado — funcionalidade de market data limitada. "
                "Execute: pip install yfinance"
            )

        # Verificar disponibilidade do MT5 (para fallback)
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

    # ══════════════════════════════════════════════════════════════
    #  Preço atual
    # ══════════════════════════════════════════════════════════════

    def get_current_price(self, ticker: str) -> dict[str, Any]:
        """
        Obtém preço atual de um ativo.

        Tenta yfinance primeiro, depois MT5 como fallback.
        Resultados são cacheados por 30 segundos.

        Args:
            ticker: Código B3 do ativo (ex.: 'PETR4').

        Returns:
            Dicionário com chaves: bid, ask, last, volume, timestamp.

        Raises:
            ValueError: Se o ticker for inválido ou nenhuma fonte disponível.
        """
        ticker = ticker.upper().strip().rstrip("Ff")

        # ── Verificar cache ──────────────────────────────────────
        cached = self._cache.get(ticker)
        if cached is not None:
            logger.debug("Cache hit para %s", ticker)
            return cached

        # ── Tentar yfinance ──────────────────────────────────────
        if self._yf_available:
            result = self._get_price_yfinance(ticker)
            if result is not None:
                self._cache.set(ticker, result)
                self._update_legacy_cache(ticker, result.get("last", 0.0))
                return result

        # ── Fallback para MT5 ────────────────────────────────────
        if self._mt5_available:
            result = self._get_price_mt5(ticker)
            if result is not None:
                self._cache.set(ticker, result)
                self._update_legacy_cache(ticker, result.get("last", 0.0))
                return result

        raise ValueError(
            f"Não foi possível obter preço de {ticker} — "
            "nenhuma fonte de dados disponível"
        )

    def get_prices_batch(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        """
        Obtém cotações atuais de uma lista de ativos em paralelo com cache thread-safe.

        Reduz a latência do ciclo de ~25s para ~1.2s executando consultas simultâneas.

        Args:
            tickers: Lista de tickers B3 (ex: ['PETR4', 'VALE3', ...]).

        Returns:
            Dicionário {ticker: price_dict}.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[str, dict[str, Any]] = {}
        missing_tickers: list[str] = []

        # 1. Checar cache
        for ticker in tickers:
            clean = ticker.upper().strip().rstrip("Ff")
            cached = self._cache.get(clean)
            if cached is not None:
                results[clean] = cached
            else:
                missing_tickers.append(clean)

        if not missing_tickers:
            return results

        # 2. Buscar em paralelo com workers
        def _fetch_single(t: str) -> tuple[str, Optional[dict[str, Any]]]:
            try:
                p = self.get_current_price(t)
                return t, p
            except Exception as exc:
                logger.debug("Falha ao obter preço para %s no lote: %s", t, exc)
                return t, None

        max_workers = min(len(missing_tickers), 25)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
        Obtém dados históricos OHLCV para múltiplos ativos em paralelo.

        Args:
            tickers: Lista de tickers B3.
            period: Período histórico (ex: '1y', '6mo').
            interval: Intervalo (ex: '1d', '60m').

        Returns:
            Dicionário {ticker: DataFrame}.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[str, pd.DataFrame] = {}

        def _fetch_df(t: str) -> tuple[str, pd.DataFrame]:
            try:
                df = self.get_ohlcv(t, period=period, interval=interval)
                return t, df
            except Exception as exc:
                logger.warning("Falha ao obter OHLCV para %s no lote: %s", t, exc)
                return t, pd.DataFrame()

        max_workers = min(len(tickers), 25)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ticker = {executor.submit(_fetch_df, t): t for t in tickers}
            for future in as_completed(future_to_ticker):
                t, df = future.result()
                results[t] = df

        return results

    def _get_price_yfinance(self, ticker: str) -> Optional[dict[str, Any]]:
        """
        Obtém preço atual via yfinance com retry e backoff.

        Args:
            ticker: Código B3 do ativo.

        Returns:
            Dicionário de preço ou None em caso de falha.
        """
        import yfinance as yf

        yf_symbol = _b3_to_yfinance(ticker)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                yf_ticker = yf.Ticker(yf_symbol)

                # Tentar fast_info primeiro (mais rápido)
                try:
                    fast = yf_ticker.fast_info
                    last_price = float(
                        fast.get("lastPrice", 0)
                        or fast.get("last_price", 0)
                        or 0
                    )
                    if last_price > 0:
                        result: dict[str, Any] = {
                            "bid": last_price,
                            "ask": last_price,
                            "last": last_price,
                            "volume": int(
                                fast.get("lastVolume", 0)
                                or fast.get("last_volume", 0)
                                or 0
                            ),
                            "timestamp": datetime.now(tz=BRT).isoformat(),
                            "source": "yfinance_fast_info",
                            "ticker": ticker,
                        }
                        logger.debug("Preço %s via fast_info: R$%.2f", ticker, last_price)
                        return result
                except Exception:
                    pass

                # Fallback para history intraday
                hist = yf_ticker.history(period="1d", interval="1m")
                if hist is not None and not hist.empty:
                    last_row = hist.iloc[-1]
                    last_price = float(last_row["Close"])
                    volume = int(last_row.get("Volume", 0))

                    result = {
                        "bid": last_price,
                        "ask": last_price,
                        "last": last_price,
                        "volume": volume,
                        "timestamp": datetime.now(tz=BRT).isoformat(),
                        "source": "yfinance_history",
                        "ticker": ticker,
                    }
                    logger.debug("Preço %s via history: R$%.2f", ticker, last_price)
                    return result

                # Fallback para history diário
                hist_daily = yf_ticker.history(period="5d")
                if hist_daily is not None and not hist_daily.empty:
                    last_row = hist_daily.iloc[-1]
                    last_price = float(last_row["Close"])
                    volume = int(last_row.get("Volume", 0))

                    result = {
                        "bid": last_price,
                        "ask": last_price,
                        "last": last_price,
                        "volume": volume,
                        "timestamp": datetime.now(tz=BRT).isoformat(),
                        "source": "yfinance_daily",
                        "ticker": ticker,
                    }
                    logger.debug("Preço %s via daily history: R$%.2f", ticker, last_price)
                    return result

                logger.debug(
                    "yfinance retornou dados vazios para %s",
                    yf_symbol,
                )
                # Tentar 1mo como última alternativa rápida
                hist_month = yf_ticker.history(period="1mo")
                if hist_month is not None and not hist_month.empty:
                    last_row = hist_month.iloc[-1]
                    last_price = float(last_row["Close"])
                    volume = int(last_row.get("Volume", 0))
                    return {
                        "bid": last_price,
                        "ask": last_price,
                        "last": last_price,
                        "volume": volume,
                        "timestamp": datetime.now(tz=BRT).isoformat(),
                        "source": "yfinance_monthly",
                        "ticker": ticker,
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
            ticker, self.MAX_RETRIES,
        )
        return None

    def _get_price_mt5(self, ticker: str) -> Optional[dict[str, Any]]:
        """
        Obtém preço atual via MetaTrader 5.

        Args:
            ticker: Código B3 do ativo.

        Returns:
            Dicionário de preço ou None em caso de falha.
        """
        try:
            import MetaTrader5 as mt5

            for symbol in [f"{ticker}F", ticker]:
                tick = mt5.symbol_info_tick(symbol)
                if tick is not None:
                    result: dict[str, Any] = {
                        "bid": float(tick.bid),
                        "ask": float(tick.ask),
                        "last": float(tick.last),
                        "volume": int(tick.volume),
                        "timestamp": datetime.now(tz=BRT).isoformat(),
                        "source": "mt5",
                        "ticker": ticker,
                    }
                    logger.debug(
                        "Preço %s via MT5: bid=R$%.2f, ask=R$%.2f, last=R$%.2f",
                        ticker, tick.bid, tick.ask, tick.last,
                    )
                    return result

        except Exception as exc:
            logger.warning("Erro ao obter preço via MT5 para %s: %s", ticker, exc)
        return None

    # ══════════════════════════════════════════════════════════════
    #  Dados históricos OHLCV
    # ══════════════════════════════════════════════════════════════

    def get_ohlcv(
        self,
        ticker: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> pd.DataFrame:
        """
        Obtém dados históricos OHLCV.

        Args:
            ticker: Código B3 do ativo.
            period: Período de dados (ex.: '1y', '6mo', '1mo').
            interval: Intervalo entre candles (ex.: '1d', '1h', '5m').

        Returns:
            DataFrame com colunas: Open, High, Low, Close, Volume.
            DataFrame vazio se não houver dados.
        """
        ticker = ticker.upper().strip().rstrip("Ff")

        if not self._yf_available:
            logger.error("yfinance não disponível para dados OHLCV")
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        yf_symbol = _b3_to_yfinance(ticker)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                import yfinance as yf

                yf_ticker = yf.Ticker(yf_symbol)
                hist = yf_ticker.history(period=period, interval=interval)

                if hist is None or hist.empty:
                    logger.debug(
                        "Dados OHLCV vazios para %s (period=%s, interval=%s)",
                        ticker, period, interval,
                    )
                    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

                columns = ["Open", "High", "Low", "Close", "Volume"]
                available_cols = [c for c in columns if c in hist.columns]
                result = hist[available_cols].copy()

                logger.info(
                    "OHLCV obtido: %s | %d candles | period=%s, interval=%s",
                    ticker, len(result), period, interval,
                )
                return result

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
            ticker, self.MAX_RETRIES,
        )
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    def get_intraday(
        self,
        ticker: str,
        interval: str = "1m",
        period: str = "1d",
    ) -> pd.DataFrame:
        """
        Obtém dados intraday OHLCV.

        Atalho para get_ohlcv com parâmetros intraday padrão.

        Args:
            ticker: Código B3 do ativo.
            interval: Intervalo entre candles (ex.: '1m', '5m', '15m').
            period: Período de dados (ex.: '1d', '5d').

        Returns:
            DataFrame com colunas: Open, High, Low, Close, Volume.
        """
        logger.debug(
            "Obtendo dados intraday: %s | interval=%s, period=%s",
            ticker, interval, period,
        )
        return self.get_ohlcv(ticker, period=period, interval=interval)

    # ══════════════════════════════════════════════════════════════
    #  Utilitários
    # ══════════════════════════════════════════════════════════════

    def get_multiple_prices(self, tickers: list[str]) -> dict[str, dict[str, Any]]:
        """
        Obtém preços de múltiplos ativos.

        Args:
            tickers: Lista de códigos B3.

        Returns:
            Dicionário ticker → dados de preço.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        results: dict[str, dict[str, Any]] = {}

        def _fetch_one(ticker: str) -> tuple[str, dict[str, Any]]:
            try:
                return ticker, self.get_current_price(ticker)
            except Exception as exc:
                logger.warning("Falha ao obter preço de %s: %s", ticker, exc)
                return ticker, {
                    "bid": 0.0,
                    "ask": 0.0,
                    "last": 0.0,
                    "volume": 0,
                    "timestamp": datetime.now(tz=BRT).isoformat(),
                    "source": "error",
                    "ticker": ticker,
                    "error": str(exc),
                }

        # Limitar workers para evitar rate-limiting do yfinance
        max_workers = min(5, len(tickers))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_one, t): t for t in tickers}
            for future in as_completed(futures):
                ticker, data = future.result()
                results[ticker] = data

        return results

    def clear_cache(self) -> None:
        """Limpa o cache de preços."""
        self._cache.clear()
        logger.debug("Cache de preços limpo")

    # ══════════════════════════════════════════════════════════════
    #  Métodos legado (compatibilidade retroativa)
    # ══════════════════════════════════════════════════════════════

    def _update_legacy_cache(self, ticker: str, price: float) -> None:
        """Atualiza cache legado de preços simples (thread-safe)."""
        with self._legacy_lock:
            if ticker in self._price_cache_legacy:
                self._previous_prices[ticker] = self._price_cache_legacy[ticker]
            self._price_cache_legacy[ticker] = price

    def set_price(self, ticker: str, price: float) -> None:
        """
        Define preço manualmente (útil para simulação/testes).

        Args:
            ticker: Código do ativo.
            price: Preço a definir.
        """
        self._update_legacy_cache(ticker, price)
        self._cache.set(ticker, {
            "bid": price,
            "ask": price,
            "last": price,
            "volume": 0,
            "timestamp": datetime.now(tz=BRT).isoformat(),
            "source": "manual",
            "ticker": ticker,
        })

    def get_variation(self, ticker: str) -> Optional[float]:
        """
        Calcula a variação percentual desde a última atualização.

        Args:
            ticker: Código do ativo.

        Returns:
            Variação percentual ou None.
        """
        with self._legacy_lock:
            current = self._price_cache_legacy.get(ticker)
            previous = self._previous_prices.get(ticker)
        if current is None or previous is None or previous == 0:
            return None
        return ((current - previous) / previous) * 100.0

    def get_prices(self, tickers: list[str]) -> dict[str, Optional[float]]:
        """
        Obtém preços atuais para múltiplos ativos em paralelo com cache thread-safe.

        Args:
            tickers: Lista de códigos de ativos.

        Returns:
            Dicionário ticker → preço.
        """
        batch_data = self.get_prices_batch(tickers)
        prices: dict[str, Optional[float]] = {}
        for ticker in tickers:
            clean = ticker.upper().strip().rstrip("Ff")
            data = batch_data.get(clean)
            if data is not None and "last" in data:
                prices[ticker] = data.get("last")
            else:
                prices[ticker] = self._price_cache_legacy.get(clean)
        return prices

    def update_prices(self, tickers: list[str]) -> dict[str, Optional[float]]:
        """
        Atualiza preços de todos os ativos monitorados (interface legado).

        Args:
            tickers: Lista de códigos de ativos.

        Returns:
            Dicionário com preços atualizados.
        """
        logger.debug("Atualizando preços de %d ativos", len(tickers))
        return self.get_prices(tickers)

    def __repr__(self) -> str:
        """Representação textual do provedor de dados."""
        return (
            f"MarketData(yfinance={self._yf_available}, "
            f"mt5={self._mt5_available})"
        )
