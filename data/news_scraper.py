"""
Scraper de notícias financeiras do Projeto Córtex.

Coleta notícias de múltiplas fontes brasileiras (Google News RSS,
InfoMoney RSS, Investing.com BR) com detecção de menções a tickers
B3, deduplicação por URL, rate limiting e rotação de User-Agent.
"""

from __future__ import annotations

import logging
import random
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
from xml.etree import ElementTree

import requests
from zoneinfo import ZoneInfo

from config.settings import settings

logger = logging.getLogger("cortex.data.news_scraper")

BRT: ZoneInfo = ZoneInfo("America/Sao_Paulo")


# ═══════════════════════════════════════════════════════════════════
#  Constantes
# ═══════════════════════════════════════════════════════════════════

# Tickers B3 para detecção de menções (incluindo variações comuns)
_B3_TICKERS: set[str] = set(settings.watchlist)

# Regex para encontrar menções a tickers B3 em texto
# Padrão: 4 letras maiúsculas + 1-2 dígitos (opcionalmente + F)
_TICKER_PATTERN: re.Pattern[str] = re.compile(
    r"\b([A-Z]{4}\d{1,2}F?)\b"
)

# User-Agents para rotação (evitar bloqueio)
_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

# Tamanho máximo do set de URLs vistas (para deduplicação)
_MAX_SEEN_URLS: int = 1000


# ═══════════════════════════════════════════════════════════════════
#  Modelo de dados
# ═══════════════════════════════════════════════════════════════════

@dataclass
class NewsItem:
    """
    Representa uma notícia financeira.

    Attributes:
        title: Título da notícia.
        summary: Resumo ou descrição da notícia.
        source: Fonte da notícia (ex.: 'Google News', 'InfoMoney').
        url: URL completa da notícia.
        published_at: Data/hora de publicação (timezone-aware).
        tickers_mentioned: Tickers B3 mencionados no título/resumo.
    """

    title: str
    summary: str
    source: str
    url: str
    published_at: datetime
    tickers_mentioned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serializa a notícia para dicionário."""
        return {
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "url": self.url,
            "published_at": self.published_at.isoformat(),
            "tickers_mentioned": self.tickers_mentioned,
        }


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════

def _extract_tickers(text: str) -> list[str]:
    """
    Extrai tickers B3 mencionados em um texto.

    Args:
        text: Texto a analisar (título, resumo, etc.).

    Returns:
        Lista de tickers encontrados (sem duplicatas, ordenados).
    """
    if not text:
        return []
    matches = _TICKER_PATTERN.findall(text.upper())
    # Filtrar apenas tickers conhecidos na watchlist
    # mas também aceitar qualquer padrão válido de ticker B3
    found: set[str] = set()
    for match in matches:
        base = match.rstrip("Ff")
        if base in _B3_TICKERS or match in _B3_TICKERS:
            found.add(base)
    return sorted(found)


def _random_user_agent() -> str:
    """Retorna um User-Agent aleatório para rotação."""
    return random.choice(_USER_AGENTS)


def _parse_rss_date(date_str: str) -> datetime:
    """
    Faz parse de data RSS (vários formatos comuns).

    Args:
        date_str: String de data do feed RSS.

    Returns:
        Datetime timezone-aware (BRT).
    """
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRT)
            return dt
        except ValueError:
            continue

    # Fallback: retorna agora
    logger.debug("Formato de data não reconhecido: '%s'", date_str)
    return datetime.now(tz=BRT)


def _clean_html(text: str) -> str:
    """Remove tags HTML de um texto."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


# ═══════════════════════════════════════════════════════════════════
#  LRU Set para deduplicação de URLs
# ═══════════════════════════════════════════════════════════════════

class _LRUSet:
    """Set com tamanho máximo que descarta entradas mais antigas (LRU)."""

    def __init__(self, max_size: int = _MAX_SEEN_URLS) -> None:
        """
        Inicializa o LRU set.

        Args:
            max_size: Número máximo de entradas.
        """
        self._data: OrderedDict[str, None] = OrderedDict()
        self._max_size: int = max_size
        self._lock: threading.Lock = threading.Lock()

    def add(self, item: str) -> bool:
        """
        Adiciona item ao set.

        Args:
            item: Item a adicionar.

        Returns:
            True se o item é novo, False se já existia.
        """
        with self._lock:
            if item in self._data:
                self._data.move_to_end(item)
                return False
            self._data[item] = None
            if len(self._data) > self._max_size:
                self._data.popitem(last=False)
            return True

    def __contains__(self, item: str) -> bool:
        """Verifica se o item está no set."""
        with self._lock:
            return item in self._data

    def __len__(self) -> int:
        """Retorna o número de itens no set."""
        with self._lock:
            return len(self._data)


# ═══════════════════════════════════════════════════════════════════
#  Classe principal
# ═══════════════════════════════════════════════════════════════════

class NewsScraper:
    """
    Scraper de notícias financeiras para ativos B3.

    Coleta de múltiplas fontes com deduplicação, rate limiting,
    rotação de User-Agent e detecção de menções a tickers.

    Fontes suportadas:
    1. Google News RSS (busca em português, Brasil)
    2. InfoMoney RSS (feed principal)
    3. Investing.com BR (scraping HTML)
    """

    def __init__(self, timeout: int = settings.news_request_timeout) -> None:
        """
        Inicializa o scraper.

        Args:
            timeout: Timeout em segundos para cada requisição HTTP.
        """
        self._timeout: int = timeout
        self._seen_urls: _LRUSet = _LRUSet()
        self._last_request_time: dict[str, float] = {}
        self._rate_limit_lock: threading.Lock = threading.Lock()
        self._session: requests.Session = requests.Session()
        logger.info("NewsScraper inicializado — timeout: %ds", timeout)

    # ══════════════════════════════════════════════════════════════
    #  Rate limiting
    # ══════════════════════════════════════════════════════════════

    def _wait_rate_limit(self, source: str) -> None:
        """
        Aguarda para respeitar rate limit (1 req/s por fonte).

        Args:
            source: Identificador da fonte.
        """
        wait = 0.0
        with self._rate_limit_lock:
            now = time.monotonic()
            last = self._last_request_time.get(source, 0.0)
            elapsed = now - last
            if elapsed < 1.0:
                wait = 1.0 - elapsed
            self._last_request_time[source] = now + wait
        if wait > 0.0:
            logger.debug("Rate limit %s — aguardando %.2fs", source, wait)
            time.sleep(wait)

    # ══════════════════════════════════════════════════════════════
    #  Requisição HTTP
    # ══════════════════════════════════════════════════════════════

    def _fetch(self, url: str, source: str) -> Optional[str]:
        """
        Faz requisição HTTP com rate limiting e User-Agent rotativo.

        Args:
            url: URL a acessar.
            source: Identificador da fonte (para rate limiting).

        Returns:
            Conteúdo da resposta ou None em caso de erro.
        """
        self._wait_rate_limit(source)
        headers = {"User-Agent": _random_user_agent()}

        try:
            response = self._session.get(
                url,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response.text
        except requests.Timeout:
            logger.warning("Timeout ao acessar %s (%s)", source, url)
        except requests.ConnectionError:
            logger.warning("Erro de conexão ao acessar %s (%s)", source, url)
        except requests.HTTPError as exc:
            logger.warning("HTTP %d ao acessar %s: %s", exc.response.status_code, source, url)
        except requests.RequestException as exc:
            logger.warning("Erro de requisição para %s: %s", source, exc)

        return None

    # ══════════════════════════════════════════════════════════════
    #  Google News RSS
    # ══════════════════════════════════════════════════════════════

    def _fetch_google_news(self, query: str = "bolsa B3 ações") -> list[NewsItem]:
        """
        Coleta notícias do Google News RSS.

        Args:
            query: Termo de busca.

        Returns:
            Lista de NewsItem do Google News.
        """
        source = "google_news"
        encoded_query = requests.utils.quote(query)
        url = (
            f"https://news.google.com/rss/search?"
            f"q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        )

        content = self._fetch(url, source)
        if not content:
            return []

        items: list[NewsItem] = []
        try:
            root = ElementTree.fromstring(content)
            channel = root.find("channel")
            if channel is None:
                return []

            for item_elem in channel.findall("item"):
                title_elem = item_elem.find("title")
                link_elem = item_elem.find("link")
                desc_elem = item_elem.find("description")
                pubdate_elem = item_elem.find("pubDate")

                title = title_elem.text if title_elem is not None and title_elem.text else ""
                link = link_elem.text if link_elem is not None and link_elem.text else ""
                description = _clean_html(
                    desc_elem.text if desc_elem is not None and desc_elem.text else ""
                )
                pub_date = (
                    _parse_rss_date(pubdate_elem.text)
                    if pubdate_elem is not None and pubdate_elem.text
                    else datetime.now(tz=BRT)
                )

                if not link or not self._seen_urls.add(link):
                    continue

                tickers = _extract_tickers(f"{title} {description}")

                items.append(
                    NewsItem(
                        title=title.strip(),
                        summary=description[:500],
                        source="Google News",
                        url=link,
                        published_at=pub_date,
                        tickers_mentioned=tickers,
                    )
                )

            logger.info("Google News: %d notícias coletadas", len(items))
        except ElementTree.ParseError as exc:
            logger.warning("Erro ao parsear RSS do Google News: %s", exc)
        except Exception as exc:
            logger.warning("Erro inesperado no Google News: %s", exc)

        return items

    # ══════════════════════════════════════════════════════════════
    #  InfoMoney RSS
    # ══════════════════════════════════════════════════════════════

    def _fetch_infomoney(self) -> list[NewsItem]:
        """
        Coleta notícias do feed RSS da InfoMoney.

        Returns:
            Lista de NewsItem do InfoMoney.
        """
        source = "infomoney"
        url = "https://www.infomoney.com.br/feed/"

        content = self._fetch(url, source)
        if not content:
            return []

        items: list[NewsItem] = []
        try:
            root = ElementTree.fromstring(content)
            channel = root.find("channel")
            if channel is None:
                return []

            for item_elem in channel.findall("item"):
                title_elem = item_elem.find("title")
                link_elem = item_elem.find("link")
                desc_elem = item_elem.find("description")
                pubdate_elem = item_elem.find("pubDate")

                title = title_elem.text if title_elem is not None and title_elem.text else ""
                link = link_elem.text if link_elem is not None and link_elem.text else ""
                description = _clean_html(
                    desc_elem.text if desc_elem is not None and desc_elem.text else ""
                )
                pub_date = (
                    _parse_rss_date(pubdate_elem.text)
                    if pubdate_elem is not None and pubdate_elem.text
                    else datetime.now(tz=BRT)
                )

                if not link or not self._seen_urls.add(link):
                    continue

                tickers = _extract_tickers(f"{title} {description}")

                items.append(
                    NewsItem(
                        title=title.strip(),
                        summary=description[:500],
                        source="InfoMoney",
                        url=link,
                        published_at=pub_date,
                        tickers_mentioned=tickers,
                    )
                )

            logger.info("InfoMoney: %d notícias coletadas", len(items))
        except ElementTree.ParseError as exc:
            logger.warning("Erro ao parsear RSS da InfoMoney: %s", exc)
        except Exception as exc:
            logger.warning("Erro inesperado no InfoMoney: %s", exc)

        return items

    # ══════════════════════════════════════════════════════════════
    #  Investing.com BR (scraping)
    # ══════════════════════════════════════════════════════════════

    def _fetch_valor_investe(self) -> list[NewsItem]:
        """
        Coleta notícias do feed RSS do Valor Investe (Globo).

        Returns:
            Lista de NewsItem do Valor Investe.
        """
        source = "valor_investe"
        url = "https://pox.globo.com/rss/valorinveste/"

        content = self._fetch(url, source)
        if not content:
            return []

        items: list[NewsItem] = []
        try:
            root = ElementTree.fromstring(content)
            channel = root.find("channel")
            if channel is None:
                return []

            for item_elem in channel.findall("item"):
                title_elem = item_elem.find("title")
                link_elem = item_elem.find("link")
                desc_elem = item_elem.find("description")
                pubdate_elem = item_elem.find("pubDate")

                title = title_elem.text if title_elem is not None and title_elem.text else ""
                link = link_elem.text if link_elem is not None and link_elem.text else ""
                description = _clean_html(
                    desc_elem.text if desc_elem is not None and desc_elem.text else ""
                )
                pub_date = (
                    _parse_rss_date(pubdate_elem.text)
                    if pubdate_elem is not None and pubdate_elem.text
                    else datetime.now(tz=BRT)
                )

                if not link or not self._seen_urls.add(link):
                    continue

                tickers = _extract_tickers(f"{title} {description}")

                items.append(
                    NewsItem(
                        title=title.strip(),
                        summary=description[:500],
                        source="Valor Investe",
                        url=link,
                        published_at=pub_date,
                        tickers_mentioned=tickers,
                    )
                )

            logger.info("Valor Investe: %d notícias coletadas", len(items))
        except ElementTree.ParseError as exc:
            logger.warning("Erro ao parsear RSS do Valor Investe: %s", exc)
        except Exception as exc:
            logger.warning("Erro inesperado no Valor Investe: %s", exc)

        return items

    def _fetch_money_times(self) -> list[NewsItem]:
        """
        Coleta notícias do feed RSS do Money Times.

        Returns:
            Lista de NewsItem do Money Times.
        """
        source = "money_times"
        url = "https://www.moneytimes.com.br/feed/"

        content = self._fetch(url, source)
        if not content:
            return []

        items: list[NewsItem] = []
        try:
            root = ElementTree.fromstring(content)
            channel = root.find("channel")
            if channel is None:
                return []

            for item_elem in channel.findall("item"):
                title_elem = item_elem.find("title")
                link_elem = item_elem.find("link")
                desc_elem = item_elem.find("description")
                pubdate_elem = item_elem.find("pubDate")

                title = title_elem.text if title_elem is not None and title_elem.text else ""
                link = link_elem.text if link_elem is not None and link_elem.text else ""
                description = _clean_html(
                    desc_elem.text if desc_elem is not None and desc_elem.text else ""
                )
                pub_date = (
                    _parse_rss_date(pubdate_elem.text)
                    if pubdate_elem is not None and pubdate_elem.text
                    else datetime.now(tz=BRT)
                )

                if not link or not self._seen_urls.add(link):
                    continue

                tickers = _extract_tickers(f"{title} {description}")

                items.append(
                    NewsItem(
                        title=title.strip(),
                        summary=description[:500],
                        source="Money Times",
                        url=link,
                        published_at=pub_date,
                        tickers_mentioned=tickers,
                    )
                )

            logger.info("Money Times: %d notícias coletadas", len(items))
        except ElementTree.ParseError as exc:
            logger.warning("Erro ao parsear RSS do Money Times: %s", exc)
        except Exception as exc:
            logger.warning("Erro inesperado no Money Times: %s", exc)

        return items

    # ══════════════════════════════════════════════════════════════
    #  Interface pública
    # ══════════════════════════════════════════════════════════════

    def fetch_all_news(self) -> list[NewsItem]:
        """
        Coleta notícias de todas as fontes disponíveis.

        Agrega resultados de Google News, InfoMoney, Valor Investe
        e Money Times. Se uma fonte falhar, as demais continuam.
        Resultados deduplicados por URL.

        Returns:
            Lista consolidada de NewsItem, ordenada por data de publicação
            (mais recente primeiro).
        """
        all_news: list[NewsItem] = []

        # ── Google News ──────────────────────────────────────────
        try:
            google_items = self._fetch_google_news()
            all_news.extend(google_items)
        except Exception as exc:
            logger.warning("Falha ao coletar Google News: %s", exc)

        # ── InfoMoney ────────────────────────────────────────────
        try:
            infomoney_items = self._fetch_infomoney()
            all_news.extend(infomoney_items)
        except Exception as exc:
            logger.warning("Falha ao coletar InfoMoney: %s", exc)

        # ── Valor Investe ────────────────────────────────────────
        try:
            valor_items = self._fetch_valor_investe()
            all_news.extend(valor_items)
        except Exception as exc:
            logger.warning("Falha ao coletar Valor Investe: %s", exc)

        # ── Money Times ──────────────────────────────────────────
        try:
            money_items = self._fetch_money_times()
            all_news.extend(money_items)
        except Exception as exc:
            logger.warning("Falha ao coletar Money Times: %s", exc)

        # Ordenar por data (mais recente primeiro)
        all_news.sort(key=lambda n: n.published_at, reverse=True)

        logger.info(
            "Total de notícias coletadas: %d (Google: %d, InfoMoney: %d, Valor: %d, Money Times: %d)",
            len(all_news),
            sum(1 for n in all_news if n.source == "Google News"),
            sum(1 for n in all_news if n.source == "InfoMoney"),
            sum(1 for n in all_news if n.source == "Valor Investe"),
            sum(1 for n in all_news if n.source == "Money Times"),
        )
        return all_news

    def fetch_ticker_news(self, ticker: str) -> list[NewsItem]:
        """
        Coleta notícias relacionadas a um ticker específico.

        Faz busca direcionada no Google News e filtra resultados
        de todas as fontes por menção ao ticker.

        Args:
            ticker: Código B3 do ativo (ex.: 'PETR4').

        Returns:
            Lista de NewsItem que mencionam o ticker.
        """
        ticker = ticker.upper().strip().rstrip("Ff")

        # Busca direcionada no Google News
        news: list[NewsItem] = []
        try:
            google_items = self._fetch_google_news(query=f"{ticker} ações B3")
            news.extend(google_items)
        except Exception as exc:
            logger.warning("Falha ao buscar notícias de %s no Google News: %s", ticker, exc)

        # Buscar em fontes gerais e filtrar
        try:
            infomoney_items = self._fetch_infomoney()
            for item in infomoney_items:
                if ticker in item.tickers_mentioned:
                    news.append(item)
        except Exception as exc:
            logger.warning("Falha ao filtrar notícias de %s no InfoMoney: %s", ticker, exc)

        # Filtrar apenas notícias que mencionam o ticker
        filtered: list[NewsItem] = []
        for item in news:
            if ticker in item.tickers_mentioned:
                filtered.append(item)
            elif ticker.lower() in item.title.lower() or ticker.lower() in item.summary.lower():
                # Menção direta no texto sem estar na watchlist
                if ticker not in item.tickers_mentioned:
                    item.tickers_mentioned.append(ticker)
                filtered.append(item)

        filtered.sort(key=lambda n: n.published_at, reverse=True)
        logger.info("Notícias para %s: %d encontradas", ticker, len(filtered))
        return filtered

    def close(self) -> None:
        """Fecha a sessão HTTP."""
        try:
            self._session.close()
            logger.debug("Sessão HTTP do NewsScraper fechada")
        except Exception as exc:
            logger.warning("Erro ao fechar sessão: %s", exc)

    def __repr__(self) -> str:
        """Representação textual do scraper."""
        return (
            f"NewsScraper(timeout={self._timeout}s, "
            f"urls_vistas={len(self._seen_urls)})"
        )

    def __del__(self) -> None:
        """Cleanup na destruição do objeto."""
        try:
            self._session.close()
        except Exception:
            pass
