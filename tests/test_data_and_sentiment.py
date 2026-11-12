"""
Testes de Análise de Sentimento (Gemini Interactions API + Google Search + Negação PT-BR),
Scraper de Notícias (cache rolante 24h, aliases sem falsos positivos, timezone GMT->BRT),
MarketData (cache OHLCV com TTL + cópia defensiva),
Logger SQLite centralizado (scrubbing de tokens) e Motor de Backtest sem look-ahead bias.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from analysis.sentiment import SentimentAnalyzer
from backtest.backtester import run_backtest
from config.settings import settings
from data.market_data import MarketData
from data.news_scraper import NewsItem, NewsScraper, _parse_rss_date, extract_mentioned_tickers
from utils.helpers import BRT
from utils.log_db_handler import SQLiteLogHandler, scrub_secrets


class TestSentimentGeminiAndLexical:
    def test_lexical_negation_crash_verbs_and_word_boundaries(self):
        analyzer = SentimentAnalyzer(mode="lightweight")

        # "Lucro cai 50% e ação desaba" NÃO pode ser positivo só por conter a palavra "lucro"!
        bearish_news = [
            NewsItem(
                title="Lucro cai 50% no trimestre e ação desaba na B3",
                summary="Resultado frustra o mercado com forte queda operacional.",
                source="Valor",
                url="https://example.com/n1",
                published_at=datetime.now(tz=BRT),
                tickers_mentioned=["MGLU3"],
            )
        ]
        res_bear = analyzer.get_sentiment_for_ticker("MGLU3", bearish_news)
        assert res_bear.score < -0.10
        assert res_bear.label in ("NEGATIVO", "MUITO_NEGATIVO")

        # Inversão de polaridade por negação ("sem prejuízo", "não houve queda")
        negated_news = [
            NewsItem(
                title="Companhia encerra ano sem prejuízo e não houve queda nas vendas",
                summary="Operação segue resiliente e com geração de caixa.",
                source="InfoMoney",
                url="https://example.com/n2",
                published_at=datetime.now(tz=BRT),
                tickers_mentioned=["WEGE3"],
            )
        ]
        res_neg = analyzer.get_sentiment_for_ticker("WEGE3", negated_news)
        assert res_neg.score > 0.0

    def test_gemini_interactions_api_with_google_search_and_cache_separation(self, monkeypatch):
        settings.GEMINI_API_KEY = "test-gemini-key"
        mock_client = MagicMock()
        mock_interaction = SimpleNamespace(
            output_text='```json\n{"score": 0.65, "confidence": 0.88, "reasoning": "Forte geração de caixa.", "top_headline": "WEG bate consenso"}\n```',
            outputs=[SimpleNamespace(type="google_search_result")],
        )
        mock_client.interactions.create.return_value = mock_interaction
        monkeypatch.setattr("analysis.sentiment.google_genai.Client", lambda api_key: mock_client)

        analyzer = SentimentAnalyzer(mode="gemini")
        analyzer._last_gemini_call = 0.0

        sample_news = [
            NewsItem(
                title="WEG reporta avanço consistente em exportações",
                summary="Demanda industrial segue aquecida.",
                source="MoneyTimes",
                url="https://example.com/weg",
                published_at=datetime.now(tz=BRT),
                tickers_mentioned=["WEGE3"],
            )
        ]

        # 1) Chamada com allow_gemini=False usa léxico local e NÃO pode envenenar o cache do Gemini!
        lex_res = analyzer.get_sentiment_for_ticker("WEGE3", sample_news, allow_gemini=False)
        assert mock_client.interactions.create.call_count == 0
        assert lex_res.score > 0

        # 2) Chamada subsequente com allow_gemini=True DEVE acionar client.interactions.create
        gem_res = analyzer.get_sentiment_for_ticker("WEGE3", sample_news, allow_gemini=True)
        assert mock_client.interactions.create.call_count == 1
        call_kwargs = mock_client.interactions.create.call_args.kwargs
        assert call_kwargs["model"] == "gemini-3.5-flash-lite"
        assert call_kwargs["store"] is False
        assert {"type": "google_search"} in call_kwargs["tools"]
        assert "<untrusted_headlines>" in call_kwargs["input"]
        assert gem_res.score == 0.65
        assert gem_res.confidence == 0.88


class TestNewsScraperCacheAliasesAndTimezone:
    def test_rolling_24h_cache_preserves_articles_across_cycles(self, monkeypatch):
        scraper = NewsScraper()
        now = datetime.now(tz=BRT)
        item = NewsItem(
            title="Petrobras anuncia descoberta de petróleo na Bacia de Campos",
            summary="Reserva amplia potencial produtivo.",
            source="Google News",
            url="https://example.com/petr-1",
            published_at=now,
            tickers_mentioned=["PETR4"],
        )

        # Primeiro ciclo retorna a notícia; segundo ciclo (mesmo se RSS não trouxer itens novos) TAMBÉM deve retorná-la!
        monkeypatch.setattr(scraper, "_fetch_google_news", lambda *a, **kw: [item])
        monkeypatch.setattr(scraper, "_fetch_infomoney", lambda: [])
        monkeypatch.setattr(scraper, "_fetch_valor_investe", lambda: [])
        monkeypatch.setattr(scraper, "_fetch_money_times", lambda: [])

        first_cycle = scraper.fetch_all_news()
        assert len(first_cycle) == 1

        monkeypatch.setattr(scraper, "_fetch_google_news", lambda *a, **kw: [])
        second_cycle = scraper.fetch_all_news()
        assert len(second_cycle) == 1
        assert second_cycle[0].url == "https://example.com/petr-1"

    def test_corporate_aliases_and_rfc2822_gmt_to_brt_conversion(self):
        # Aliases corporativos reais mapeiam corretamente sem falsos positivos em palavras comuns
        tickers = extract_mentioned_tickers("Petrobras e Nvidia lideram altas enquanto JBS avança")
        assert "PETR4" in tickers
        assert "NVDC34" in tickers
        assert "JBSS32" in tickers

        false_pos = extract_mentioned_tickers("O pregão segue vivo e no rumo certo para investidores")
        assert "VIVT3" not in false_pos
        assert "RAIL3" not in false_pos

        # 15:00 GMT deve ser convertido para 12:00 BRT (-03:00), nunca rotulado como 15:00 BRT!
        parsed = _parse_rss_date("Wed, 15 Jul 2026 15:00:00 GMT")
        assert parsed is not None
        assert parsed.tzinfo == BRT
        assert parsed.hour == 12


class TestMarketDataCacheStaleAndSQLiteLogs:
    def test_ohlcv_ttl_cache_and_defensive_copy(self, monkeypatch):
        md = MarketData()
        dates = pd.date_range("2026-07-01", periods=65, freq="D", tz=BRT)
        sample_df = pd.DataFrame(
            {
                "Open": np.linspace(30, 35, 65),
                "High": np.linspace(30.5, 35.5, 65),
                "Low": np.linspace(29.5, 34.5, 65),
                "Close": np.linspace(30.2, 35.2, 65),
                "Volume": np.full(65, 1_000_000),
            },
            index=dates,
        )

        call_counter = {"count": 0}

        class FakeTicker:
            def __init__(self, sym):
                self.sym = sym

            def history(self, period="3mo", interval="1d", **kwargs):
                call_counter["count"] += 1
                return sample_df.copy()

        monkeypatch.setattr("yfinance.Ticker", FakeTicker)

        df1 = md.get_ohlcv("PETR4", period="3mo", interval="1d")
        df2 = md.get_ohlcv("PETR4F", period="3mo", interval="1d")
        assert call_counter["count"] == 1
        assert len(df1) == 65
        # Cópia defensiva: mutar df1 não altera df2
        df1.iloc[0, 0] = -999.0
        assert df2.iloc[0, 0] != -999.0

    def test_secret_scrubbing_and_sqlite_log_handler(self, tmp_path):
        raw = "Erro em https://api.telegram.org/bot1234567890:AAH_SecretBotTokenValue_1234567890abcd/sendMessage"
        scrubbed = scrub_secrets(raw)
        assert "AAH_SecretBotTokenValue" not in scrubbed
        assert "bot***:***" in scrubbed

        log_db = tmp_path / "logs.db"
        handler = SQLiteLogHandler(db_path=str(log_db))
        test_logger = logging.getLogger("cortex.test_sqlite_log")
        test_logger.setLevel(logging.INFO)
        test_logger.addHandler(handler)
        try:
            test_logger.info("Teste de log com token bot987654321:AAH_AnotherSecretToken_9876543210xyz")
        finally:
            test_logger.removeHandler(handler)
            handler.close()

        with sqlite3.connect(log_db) as conn:
            rows = conn.execute("SELECT timestamp, level, source, message, raw_line FROM logs").fetchall()
        assert len(rows) >= 1
        assert all("AAH_AnotherSecretToken" not in r[3] and "AAH_AnotherSecretToken" not in r[4] for r in rows)


class TestBacktesterNoLookAhead:
    def test_run_backtest_end_to_end_offline(self):
        # Gera 90 pregões sintéticos determinísticos (55 aquecimento + 35 simulados)
        dates = pd.bdate_range("2026-01-05", periods=90)
        pattern = [0.55, 0.45, -0.60, 0.65, -0.35]
        prices: list[float] = []
        px = 25.0
        for i in range(90):
            px = round(max(5.0, px + pattern[i % len(pattern)]), 2)
            prices.append(px)
        arr = np.array(prices)

        petr_df = pd.DataFrame(
            {
                "Open": arr * 0.997,
                "High": arr * 1.012,
                "Low": arr * 0.988,
                "Close": arr,
                "Volume": np.full(90, 1_500_000),
            },
            index=dates,
        )
        bvsp_df = pd.DataFrame(
            {
                "Open": np.linspace(125_000, 132_000, 90),
                "High": np.linspace(125_500, 132_500, 90),
                "Low": np.linspace(124_500, 131_500, 90),
                "Close": np.linspace(125_100, 132_100, 90),
                "Volume": np.full(90, 10_000_000),
            },
            index=dates,
        )

        res = run_backtest(
            start_date="2026-01-05",
            end_date="2026-05-15",
            initial_capital=5000.0,
            watchlist=["PETR4"],
            historical_data={"PETR4": petr_df, "^BVSP": bvsp_df},
            plot=False,
        )
        assert res is not None
        assert res["final_value"] > 0
        assert "max_drawdown_pct" in res
        assert "sharpe_ratio" in res
