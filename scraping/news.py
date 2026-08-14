"""
Scraper de notícias do Projeto Córtex.

Coleta notícias financeiras de fontes públicas (RSS feeds)
para alimentar o analisador de sentimento.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from models.data_models import BRT

logger = logging.getLogger('cortex.scraping.news')


# Feeds RSS de notícias financeiras brasileiras
RSS_FEEDS: list[dict[str, str]] = [
    {'name': 'InfoMoney', 'url': 'https://www.infomoney.com.br/feed/'},
    {'name': 'Valor Econômico', 'url': 'https://valor.globo.com/rss/valor'},
    {'name': 'Investing.com BR', 'url': 'https://br.investing.com/rss/news.rss'},
]


class NewsScraper:
    """Scraper de notícias financeiras via RSS feeds."""

    def __init__(self) -> None:
        """Inicializa o scraper de notícias."""
        self._cache: list[dict[str, Any]] = []
        self._last_fetch: datetime | None = None
        self._cache_ttl_seconds: int = 300  # 5 minutos
        logger.info('NewsScraper inicializado — %d feeds configurados', len(RSS_FEEDS))

    def scrape(self, tickers: list[str] | None = None) -> list[dict[str, Any]]:
        """
        Coleta notícias de todas as fontes configuradas.

        Args:
            tickers: Lista de tickers para filtrar relevância (opcional).

        Returns:
            Lista de notícias com chaves 'title', 'summary', 'source', 'published'.
        """
        now = datetime.now(BRT)

        # Usar cache se recente
        if (
            self._last_fetch is not None
            and (now - self._last_fetch).total_seconds() < self._cache_ttl_seconds
            and self._cache
        ):
            logger.debug('Usando cache de notícias (%d itens)', len(self._cache))
            if tickers:
                return self._filter_by_tickers(self._cache, tickers)
            return self._cache

        all_news: list[dict[str, Any]] = []
        for feed_config in RSS_FEEDS:
            try:
                news = self._parse_feed(feed_config['url'], feed_config['name'])
                all_news.extend(news)
            except Exception as e:
                logger.warning('Erro ao processar feed %s: %s', feed_config['name'], e)

        self._cache = all_news
        self._last_fetch = now
        logger.info('Notícias coletadas: %d itens de %d fontes', len(all_news), len(RSS_FEEDS))

        if tickers:
            return self._filter_by_tickers(all_news, tickers)
        return all_news

    def _parse_feed(self, url: str, source_name: str) -> list[dict[str, Any]]:
        """
        Parseia um feed RSS e retorna notícias.

        Args:
            url: URL do feed RSS.
            source_name: Nome da fonte.

        Returns:
            Lista de dicts com dados das notícias.
        """
        try:
            import feedparser
            feed = feedparser.parse(url)
            news: list[dict[str, Any]] = []

            for entry in feed.entries[:20]:  # Limitar a 20 por fonte
                item: dict[str, Any] = {
                    'title': getattr(entry, 'title', ''),
                    'summary': getattr(entry, 'summary', ''),
                    'source': source_name,
                    'link': getattr(entry, 'link', ''),
                    'published': getattr(entry, 'published', ''),
                    'fetched_at': datetime.now(BRT).isoformat(),
                }
                news.append(item)

            return news
        except ImportError:
            logger.warning('feedparser não instalado')
            return []
        except Exception as e:
            logger.error('Erro ao parsear feed %s: %s', url, e)
            return []

    @staticmethod
    def _filter_by_tickers(
        news: list[dict[str, Any]], tickers: list[str]
    ) -> list[dict[str, Any]]:
        """Filtra notícias que mencionam os tickers especificados."""
        # Incluir também o nome das empresas comuns
        ticker_aliases: dict[str, list[str]] = {
            'PETR4': ['petrobras', 'petr4'],
            'VALE3': ['vale', 'vale3'],
            'ITUB4': ['itaú', 'itau', 'itub4'],
            'BBDC4': ['bradesco', 'bbdc4'],
            'BBAS3': ['banco do brasil', 'bb', 'bbas3'],
            'WEGE3': ['weg', 'wege3'],
            'RENT3': ['localiza', 'rent3'],
            'ABEV3': ['ambev', 'abev3'],
            'MGLU3': ['magazine luiza', 'magalu', 'mglu3'],
            'SUZB3': ['suzano', 'suzb3'],
            'ELET3': ['eletrobras', 'elet3'],
            'JBSS3': ['jbs', 'jbss3'],
            'B3SA3': ['b3', 'b3sa3'],
            'RDOR3': ['rede d\'or', 'rdor3'],
            'VIVT3': ['vivo', 'telefônica', 'vivt3'],
            'CSAN3': ['cosan', 'csan3'],
            'GGBR4': ['gerdau', 'ggbr4'],
            'CSNA3': ['csn', 'csna3'],
            'TOTS3': ['totvs', 'tots3'],
            'BPAC11': ['btg', 'btg pactual', 'bpac11'],
        }

        filtered: list[dict[str, Any]] = []
        general_news: list[dict[str, Any]] = []

        for item in news:
            text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
            matched = False

            for ticker in tickers:
                aliases = ticker_aliases.get(ticker, [ticker.lower()])
                for alias in aliases:
                    if alias in text:
                        item_copy = dict(item)
                        item_copy['matched_ticker'] = ticker
                        filtered.append(item_copy)
                        matched = True
                        break
                if matched:
                    break

            if not matched:
                # Notícias gerais de mercado
                market_keywords = ['ibovespa', 'b3', 'bolsa', 'mercado', 'ações']
                if any(kw in text for kw in market_keywords):
                    general_news.append(item)

        # Retornar notícias específicas + gerais
        return filtered + general_news[:5]
