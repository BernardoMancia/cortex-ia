import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from analysis.sentiment import SentimentAnalyzer
from analysis.technical import TechnicalAnalyzer
from data.news_scraper import NewsItem

BRT = ZoneInfo("America/Sao_Paulo")


def create_sample_df(trend: str = "up", periods: int = 70) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = [datetime(2026, 8, 1, 14, 0, tzinfo=BRT) + timedelta(days=i) for i in range(periods)]
    prices_list: list[float] = []
    if trend == "up":
        pattern = [0.45, 0.40, -0.55, 0.50, -0.30]
        px = 30.0
        for i in range(periods):
            delta = pattern[i % len(pattern)] if i < periods - 7 else (-0.25 if i < periods - 4 else 0.45)
            px = round(px + delta, 2)
            prices_list.append(px)
        prices = np.array(prices_list)
    elif trend == "down":
        pattern = [-0.45, -0.40, 0.55, -0.50, 0.30]
        px = 40.0
        for i in range(periods):
            delta = pattern[i % len(pattern)] if i < periods - 7 else (0.25 if i < periods - 4 else -0.45)
            px = round(px + delta, 2)
            prices_list.append(px)
        prices = np.array(prices_list)
    else:
        prices = np.full(periods, 35.0) + rng.normal(0, 0.2, periods)

    volumes = np.where(np.arange(periods) >= periods - 5, 45_000, 30_000)
    df = pd.DataFrame(
        {
            "open": prices * 0.996,
            "high": prices * 1.01,
            "low": prices * 0.99,
            "close": prices,
            "volume": volumes,
        },
        index=dates,
    )
    return df


def test_technical_analyzer_uptrend_signals():
    analyzer = TechnicalAnalyzer()
    df_up = create_sample_df("up", 70)
    sig = analyzer.analyze("PETR4", df_up)
    assert sig is not None
    assert sig.signal.value in ("BUY", "STRONG_BUY")
    assert sig.ema_9 > sig.ema_50


def test_technical_analyzer_downtrend_signals():
    analyzer = TechnicalAnalyzer()
    df_down = create_sample_df("down", 70)
    sig = analyzer.analyze("MGLU3", df_down)
    assert sig is not None
    assert sig.signal.value in ("SELL", "STRONG_SELL")
    assert sig.ema_9 < sig.ema_50


def test_sentiment_analyzer_lightweight():
    analyzer = SentimentAnalyzer(mode="lightweight")
    news = [
        NewsItem(
            title="Petrobras registra lucro recorde e anuncia dividendos bilionários",
            summary="A estatal superou as expectativas do mercado financeiro com alta nas margens.",
            source="InfoMoney",
            url="http://example.com/1",
            published_at=datetime.now(tz=BRT),
            tickers_mentioned=["PETR4"],
        ),
        NewsItem(
            title="Produção de petróleo atinge máxima histórica no pré-sal",
            summary="Crescimento sólido e expansão de capacidade.",
            source="Valor",
            url="http://example.com/2",
            published_at=datetime.now(tz=BRT),
            tickers_mentioned=["PETR4"],
        ),
    ]
    res = analyzer.get_sentiment_for_ticker("PETR4", news)
    assert res.score > 0.10
    assert res.label in ("POSITIVO", "MUITO_POSITIVO")
