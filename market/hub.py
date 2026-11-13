from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time as dt_time
import threading
import time
from typing import Any
from uuid import uuid4

from config.settings import settings
from domain.b3_rules import (
    BRT,
    get_ash_wednesday,
    get_brt_now,
    is_b3_business_day,
    normalize_b3_ticker,
)
from domain.models import (
    MarketCycleSnapshot,
    MarketQuote,
    SentimentVector,
    TechnicalVector,
)
from market.news.rss_scraper import B3NewsScraper
from market.providers.yfinance_prov import YFinanceMarketProvider
from observability.logger import get_logger
from quant.sentiment import SentimentAnalyzer
from quant.technical import TechnicalAnalyzer
from storage.connection import DatabaseConnection
from storage.market_hub_repo import MarketHubRepository

logger = get_logger("market.hub")


class MarketIntelligenceHub:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()
        self.repo = MarketHubRepository(self.db)
        self.yf_provider = YFinanceMarketProvider()
        self.mt5_provider: Any = None
        if settings.IS_PRODUCTION:
            try:
                from market.providers.mt5_prov import MT5BridgeMarketProvider

                self.mt5_provider = MT5BridgeMarketProvider()
            except Exception as exc:
                logger.warning("Provedor MT5 indisponível na inicialização do Hub: %s", exc)
                self.mt5_provider = None

        self.news_scraper = B3NewsScraper()
        self.technical_analyzer = TechnicalAnalyzer()
        self.sentiment_analyzer = SentimentAnalyzer(market_repo=self.repo)
        self._state_lock = threading.RLock()
        self._cycle_lock = threading.Lock()
        self._latest_snapshot: MarketCycleSnapshot | None = None
        self._last_refresh_mono: float = 0.0

    def is_b3_session_open(self, now: datetime | None = None) -> tuple[bool, bool]:
        if now is None:
            curr = get_brt_now()
        elif now.tzinfo is None:
            curr = now.replace(tzinfo=BRT)
        else:
            curr = now.astimezone(BRT)

        session_date = curr.date()
        if not is_b3_business_day(session_date):
            return (False, False)

        open_t = (
            dt_time(13, 0)
            if session_date == get_ash_wednesday(session_date.year)
            else dt_time(10, 0)
        )
        close_t = dt_time(17, 55)
        curr_t = curr.time()

        if not (open_t <= curr_t <= close_t):
            return (False, False)

        open_dt = datetime.combine(session_date, open_t, tzinfo=BRT)
        elapsed_seconds = (curr - open_dt).total_seconds()
        cooldown_window = max(0, int(settings.OPENING_COOLDOWN_MINUTES)) * 60.0
        opening_cooldown_active = 0.0 <= elapsed_seconds < cooldown_window
        return (True, opening_cooldown_active)

    def _build_universe(self, extra_tickers: set[str] | None = None) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        raw_items: list[str] = ["BOVA11", *settings.WATCHLIST]
        if extra_tickers:
            raw_items.extend(sorted(extra_tickers))
        for raw_tk in raw_items:
            clean_tk = normalize_b3_ticker(raw_tk)
            if clean_tk and clean_tk not in seen:
                seen.add(clean_tk)
                ordered.append(clean_tk)
        return ordered

    def get_latest_snapshot(self) -> MarketCycleSnapshot | None:
        with self._state_lock:
            return self._latest_snapshot

    def refresh_cycle(
        self,
        extra_tickers: set[str] | None = None,
        force: bool = False,
    ) -> MarketCycleSnapshot:
        universe = self._build_universe(extra_tickers)
        min_reuse_window = min(12.0, float(settings.PRICE_CACHE_TTL) * 0.5)

        if not force:
            with self._state_lock:
                if (
                    self._latest_snapshot is not None
                    and (time.monotonic() - self._last_refresh_mono) <= min_reuse_window
                    and all(tk in self._latest_snapshot.quotes for tk in universe)
                ):
                    return self._latest_snapshot

        with self._cycle_lock:
            if not force:
                with self._state_lock:
                    if (
                        self._latest_snapshot is not None
                        and (time.monotonic() - self._last_refresh_mono) <= min_reuse_window
                        and all(tk in self._latest_snapshot.quotes for tk in universe)
                    ):
                        return self._latest_snapshot

            market_open, opening_cooldown = self.is_b3_session_open()
            mt5_online = bool(
                settings.IS_PRODUCTION
                and self.mt5_provider is not None
                and self.mt5_provider.is_available()
            )

            yf_quotes = self.yf_provider.fetch_quotes(universe, market_open=market_open)
            quotes: dict[str, MarketQuote] = dict(yf_quotes)

            if mt5_online and self.mt5_provider is not None:
                for tk in universe:
                    mt5_quote = self.mt5_provider.fetch_tick(tk)
                    if mt5_quote is None or mt5_quote.price <= 0.0:
                        continue
                    yf_ref = yf_quotes.get(tk)
                    merged_change = (
                        mt5_quote.change_pct
                        if mt5_quote.change_pct != 0.0 or yf_ref is None
                        else yf_ref.change_pct
                    )
                    merged_vol = (
                        mt5_quote.volume
                        if mt5_quote.volume > 0.0 or yf_ref is None
                        else yf_ref.volume
                    )
                    effective_stale = bool(market_open and mt5_quote.is_stale)
                    quotes[tk] = replace(
                        mt5_quote,
                        change_pct=merged_change,
                        volume=merged_vol,
                        is_stale=effective_stale,
                    )

            db_fallback_map: dict[str, dict[str, Any]] | None = None
            for tk in universe:
                if tk in quotes:
                    continue
                if db_fallback_map is None:
                    try:
                        db_fallback_map = self.repo.get_snapshot_map()
                    except Exception:
                        db_fallback_map = {}
                saved = db_fallback_map.get(tk)
                if saved and float(saved.get("price", 0.0) or 0.0) > 0.0:
                    quotes[tk] = MarketQuote(
                        ticker=tk,
                        price=float(saved["price"]),
                        bid=float(saved.get("bid", saved["price"]) or saved["price"]),
                        ask=float(saved.get("ask", saved["price"]) or saved["price"]),
                        spread_pct=float(saved.get("spread_pct", 0.0) or 0.0),
                        volume=float(saved.get("volume", 0.0) or 0.0),
                        change_pct=float(saved.get("change_pct", 0.0) or 0.0),
                        quote_ts=0.0,
                        source="db_cache",
                        is_stale=bool(market_open),
                    )

            headlines = self.news_scraper.fetch_latest_headlines(universe)
            if headlines:
                try:
                    self.repo.save_news_items(headlines)
                except Exception as exc:
                    logger.warning("Falha ao persistir manchetes RSS: %s", exc)
            else:
                try:
                    headlines = self.repo.get_recent_news(limit=60)
                except Exception:
                    headlines = []

            self.yf_provider.prefetch_ohlcv_batch(
                universe,
                include_60m=settings.ENABLE_MTF_CONFIRMATION,
            )

            headlines_by_ticker: dict[str, list[dict[str, Any]]] = {}
            general_headlines: list[dict[str, Any]] = []
            for item in headlines:
                tk_key = normalize_b3_ticker(str(item.get("ticker", "") or ""))
                if tk_key:
                    headlines_by_ticker.setdefault(tk_key, []).append(item)
                else:
                    general_headlines.append(item)

            sentiments: dict[str, SentimentVector] = {}
            market_sent = self.sentiment_analyzer.analyze_headlines("MARKET", headlines[:25])
            sentiments["MARKET"] = market_sent

            technicals: dict[str, TechnicalVector] = {}
            now_iso = get_brt_now().isoformat()
            snapshot_rows: list[dict[str, Any]] = []

            for tk in universe:
                df_d1 = None
                df_60m = None
                if mt5_online and self.mt5_provider is not None:
                    df_d1 = self.mt5_provider.fetch_ohlcv(tk, timeframe="D1", bars=120)
                    if settings.ENABLE_MTF_CONFIRMATION:
                        df_60m = self.mt5_provider.fetch_ohlcv(tk, timeframe="H1", bars=120)

                if df_d1 is None or df_d1.empty or len(df_d1) < 25:
                    df_d1 = self.yf_provider.fetch_ohlcv_daily(tk)
                if settings.ENABLE_MTF_CONFIRMATION and (df_60m is None or df_60m.empty or len(df_60m) < 15):
                    df_60m = self.yf_provider.fetch_ohlcv_60m(tk)

                tech = self.technical_analyzer.analyze_ticker(tk, df_d1, df_60m)
                technicals[tk] = tech

                tk_news = headlines_by_ticker.get(tk, [])
                if not tk_news and general_headlines:
                    tk_news = self.sentiment_analyzer._filter_headlines_for_ticker(tk, general_headlines)
                sent = self.sentiment_analyzer.analyze_headlines(tk, tk_news)
                sentiments[tk] = sent

                if tk not in quotes and not df_d1.empty:
                    last_close = round(float(df_d1["Close"].iloc[-1]), 2)
                    prev_close = (
                        float(df_d1["Close"].iloc[-2])
                        if len(df_d1) >= 2
                        else float(df_d1["Open"].iloc[-1])
                    )
                    chg = (
                        round(((last_close - prev_close) / prev_close) * 100.0, 4)
                        if prev_close > 0.0
                        else 0.0
                    )
                    if last_close > 0.0:
                        quotes[tk] = MarketQuote(
                            ticker=tk,
                            price=last_close,
                            bid=last_close,
                            ask=last_close,
                            spread_pct=0.0,
                            volume=round(float(df_d1["Volume"].iloc[-1]), 2),
                            change_pct=chg,
                            quote_ts=0.0 if market_open else time.time(),
                            source="ohlcv_fallback",
                            is_stale=bool(market_open),
                        )

                q_obj = quotes.get(tk)
                if q_obj is not None and q_obj.price > 0.0:
                    snapshot_rows.append(
                        {
                            "ticker": tk,
                            "price": q_obj.price,
                            "bid": q_obj.bid,
                            "ask": q_obj.ask,
                            "spread_pct": q_obj.spread_pct,
                            "volume": q_obj.volume,
                            "change_pct": q_obj.change_pct,
                            "rsi": tech.rsi,
                            "macd_hist": tech.macd_hist,
                            "atr": tech.atr,
                            "adx": tech.adx,
                            "trend_signal": tech.signal.value,
                            "mtf_bullish": tech.mtf_bullish,
                            "sentiment_score": sent.score,
                            "is_stale": q_obj.is_stale,
                            "updated_at": now_iso,
                        }
                    )

            if snapshot_rows:
                try:
                    self.repo.upsert_market_snapshots(snapshot_rows)
                except Exception as exc:
                    logger.error("Falha ao gravar snapshots de mercado no SQLite: %s", exc)

            bova_quote = quotes.get("BOVA11")
            bova11_change_pct = round(float(bova_quote.change_pct), 4) if bova_quote is not None else 0.0
            regime_bearish_veto = bool(bova11_change_pct <= settings.MARKET_REGIME_MAX_DROP_PCT)

            cycle_snapshot = MarketCycleSnapshot(
                cycle_id=uuid4().hex[:12],
                generated_at=get_brt_now(),
                market_open=market_open,
                opening_cooldown_active=opening_cooldown,
                bova11_change_pct=bova11_change_pct,
                regime_bearish_veto=regime_bearish_veto,
                quotes=quotes,
                technicals=technicals,
                sentiments=sentiments,
                headlines=headlines,
            )

            with self._state_lock:
                self._latest_snapshot = cycle_snapshot
                self._last_refresh_mono = time.monotonic()

            logger.info(
                "Ciclo MarketHub %s concluído: %d cotações, BOVA11=%+.2f%%, veto_regime=%s, pregão=%s",
                cycle_snapshot.cycle_id,
                len(quotes),
                bova11_change_pct,
                regime_bearish_veto,
                market_open,
            )
            return cycle_snapshot
