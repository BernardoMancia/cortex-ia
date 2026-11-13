from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
import html
import re
import threading
import time
from typing import Any, Final
from xml.etree import ElementTree
import requests

from config.b3_universe import TICKER_ALIASES
from config.settings import settings
from domain.b3_rules import BRT, get_brt_now, normalize_b3_ticker
from observability.logger import get_logger
from security.net_guard import (
    fetch_safe_external_bytes,
    parse_xml_safely,
    sanitize_untrusted_text,
)

logger = get_logger("market.rss_scraper")

RSS_FEEDS: Final[tuple[tuple[str, str], ...]] = (
    ("InfoMoney", "https://www.infomoney.com.br/mercados/feed/"),
    ("MoneyTimes", "https://www.moneytimes.com.br/feed/"),
    ("Suno", "https://www.suno.com.br/noticias/feed/"),
    ("Investing BR", "https://br.investing.com/rss/news_25.rss"),
)

_HTML_TAG_RE: Final[re.Pattern[str]] = re.compile(r"<[^>]+>")
_DIRECT_TICKER_RE: Final[re.Pattern[str]] = re.compile(
    r"\b([A-Z]{4}(?:3|4|5|6|11|31|32|33|34|35|39))\b"
)
_REQUEST_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (compatible; CortexIA/1.0)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def _strip_markup(raw_text: str, max_length: int) -> str:
    unescaped = html.unescape(raw_text or "")
    no_tags = _HTML_TAG_RE.sub(" ", unescaped)
    return sanitize_untrusted_text(no_tags, max_length=max_length)


def _parse_pub_date(raw_date: str) -> str:
    text = (raw_date or "").strip()
    if not text:
        return get_brt_now().isoformat()
    try:
        dt = parsedate_to_datetime(text)
        dt = dt.replace(tzinfo=BRT) if dt.tzinfo is None else dt.astimezone(BRT)
        return dt.isoformat()
    except Exception:
        pass
    try:
        iso_clean = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(iso_clean)
        dt = dt.replace(tzinfo=BRT) if dt.tzinfo is None else dt.astimezone(BRT)
        return dt.isoformat()
    except Exception:
        return get_brt_now().isoformat()


def _compile_alias_patterns() -> dict[str, list[re.Pattern[str]]]:
    compiled: dict[str, list[re.Pattern[str]]] = {}
    for tk, aliases in TICKER_ALIASES.items():
        norm_tk = normalize_b3_ticker(tk)
        if not norm_tk:
            continue
        pats: list[re.Pattern[str]] = []
        for alias in aliases:
            clean_alias = alias.strip().upper()
            if len(clean_alias) < 3:
                continue
            pats.append(re.compile(rf"(?<![A-Z0-9À-Ú]){re.escape(clean_alias)}(?![A-Z0-9À-Ú])"))
        if pats:
            compiled[norm_tk] = pats
    return compiled


_ALIAS_PATTERNS: Final[dict[str, list[re.Pattern[str]]]] = _compile_alias_patterns()


class B3NewsScraper:
    def __init__(self, cache_ttl_seconds: int = 55) -> None:
        self._lock = threading.RLock()
        self._cache_ttl = max(15, int(cache_ttl_seconds))
        self._cached_at: float = 0.0
        self._cached_raw_entries: list[dict[str, str]] = []

    def _extract_entries_from_xml(
        self,
        root: ElementTree.Element,
        source_name: str,
    ) -> list[dict[str, str]]:
        extracted: list[dict[str, str]] = []
        for elem in root.iter():
            tag_name = _local_tag(elem.tag)
            if tag_name not in ("item", "entry"):
                continue
            title_raw = ""
            summary_raw = ""
            link_raw = ""
            pub_raw = ""
            for child in elem:
                ctag = _local_tag(child.tag)
                if ctag == "title" and not title_raw:
                    title_raw = "".join(child.itertext())
                elif ctag in ("description", "summary", "content") and not summary_raw:
                    summary_raw = "".join(child.itertext())
                elif ctag == "link" and not link_raw:
                    href = (child.attrib.get("href") or "").strip()
                    link_raw = href if href else "".join(child.itertext()).strip()
                elif ctag in ("pubdate", "published", "updated", "date") and not pub_raw:
                    pub_raw = "".join(child.itertext()).strip()

            title = _strip_markup(title_raw, max_length=260)
            summary = _strip_markup(summary_raw, max_length=520)
            url = sanitize_untrusted_text(link_raw.strip(), max_length=480)
            if not title or not url.lower().startswith("https://"):
                continue
            extracted.append(
                {
                    "title": title,
                    "summary": summary,
                    "source": source_name,
                    "url": url,
                    "published_at": _parse_pub_date(pub_raw),
                }
            )
            if len(extracted) >= 35:
                break
        return extracted

    def _fetch_all_raw_entries(self) -> list[dict[str, str]]:
        mono_now = time.monotonic()
        with self._lock:
            if self._cached_raw_entries and (mono_now - self._cached_at) <= self._cache_ttl:
                return list(self._cached_raw_entries)

        collected: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        with requests.Session() as session:
            for source_name, feed_url in RSS_FEEDS:
                try:
                    xml_bytes = fetch_safe_external_bytes(
                        session=session,
                        url=feed_url,
                        timeout=settings.NEWS_REQUEST_TIMEOUT,
                        headers=_REQUEST_HEADERS,
                    )
                    root = parse_xml_safely(xml_bytes)
                    items = self._extract_entries_from_xml(root, source_name=source_name)
                    for it in items:
                        if it["url"] not in seen_urls:
                            seen_urls.add(it["url"])
                            collected.append(it)
                except Exception as exc:
                    logger.debug("Feed RSS indisponível (%s): %s", source_name, exc)

        if collected:
            collected.sort(key=lambda x: x["published_at"], reverse=True)
            with self._lock:
                self._cached_at = time.monotonic()
                self._cached_raw_entries = list(collected)
            return collected

        with self._lock:
            return list(self._cached_raw_entries)

    def _match_tickers(self, text_upper: str, watchlist_order: list[str], watchlist_set: set[str]) -> list[str]:
        matched: list[str] = []
        for direct in _DIRECT_TICKER_RE.findall(text_upper):
            norm_direct = normalize_b3_ticker(direct)
            if norm_direct in watchlist_set and norm_direct not in matched:
                matched.append(norm_direct)
                if len(matched) >= 3:
                    return matched

        for tk in watchlist_order:
            if tk in matched:
                continue
            patterns = _ALIAS_PATTERNS.get(tk)
            if not patterns:
                continue
            if any(pat.search(text_upper) for pat in patterns):
                matched.append(tk)
                if len(matched) >= 3:
                    break
        return matched

    def fetch_latest_headlines(self, watchlist: list[str]) -> list[dict[str, Any]]:
        watchlist_order: list[str] = []
        watchlist_set: set[str] = set()
        for raw_tk in watchlist:
            norm_tk = normalize_b3_ticker(raw_tk)
            if norm_tk and norm_tk not in watchlist_set:
                watchlist_set.add(norm_tk)
                watchlist_order.append(norm_tk)

        raw_entries = self._fetch_all_raw_entries()
        results: list[dict[str, Any]] = []

        for entry in raw_entries:
            combined_upper = f"{entry['title']} {entry['summary']}".upper()
            matched_tickers = self._match_tickers(combined_upper, watchlist_order, watchlist_set)
            if matched_tickers:
                for tk in matched_tickers:
                    results.append(
                        {
                            "title": entry["title"],
                            "summary": entry["summary"],
                            "source": entry["source"],
                            "url": entry["url"],
                            "ticker": tk,
                            "published_at": entry["published_at"],
                        }
                    )
            else:
                results.append(
                    {
                        "title": entry["title"],
                        "summary": entry["summary"],
                        "source": entry["source"],
                        "url": entry["url"],
                        "ticker": "",
                        "published_at": entry["published_at"],
                    }
                )
        return results
