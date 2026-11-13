"""
Scraper de notícias financeiras do Projeto Córtex.

Coleta notícias de múltiplas fontes brasileiras (Google News RSS,
InfoMoney RSS, Valor Investe, Money Times) com registro unificado de
aliases corporativos (ações, Units, BDRs e ETFs), janela rolante de 24h
em memória, parse correto de fusos GMT/UTC -> BRT, decodificação UTF-8/HTML
e sessões HTTP isoladas por thread.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import html
import logging
import random
import re
import threading
import time
from typing import Any, Optional
from xml.etree import ElementTree

import requests
from requests.adapters import HTTPAdapter

from config.settings import settings
from utils.helpers import BRT, normalize_b3_ticker

try:
    import feedparser  # type: ignore[import-untyped]
    _FEEDPARSER_AVAILABLE = True
except ImportError:
    _FEEDPARSER_AVAILABLE = False

logger = logging.getLogger("cortex.data.news_scraper")

_USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

_MAX_CACHED_ARTICLES: int = 1000
_CACHE_RETENTION_HOURS: float = 24.0

_TICKER_CODE_PATTERN: re.Pattern[str] = re.compile(r"\b([A-Z][A-Z0-9]{3}\d{1,2}F?)\b")

# Registro corporativo compartilhado livre de palavras genéricas ambíguas em PT-BR
TICKER_ALIASES: dict[str, list[str]] = {
    # Petróleo, Gás e Combustíveis
    "PETR4": ["PETR4", "PETR3", "PETROBRAS", "PETROBRÁS"],
    "PETR3": ["PETR3", "PETR4", "PETROBRAS", "PETROBRÁS"],
    "PRIO3": ["PRIO3", "PETRORIO", "PETRO RIO"],
    "RECV3": ["RECV3", "PETRORECONCAVO", "PETRORECÔNCAVO"],
    "BRAV3": ["BRAV3", "BRAVA ENERGIA", "ENAUTA", "3R PETROLEUM"],
    "UGPA3": ["UGPA3", "ULTRAPAR", "GRUPO ULTRA"],
    "VBBR3": ["VBBR3", "VIBRA ENERGIA", "BR DISTRIBUIDORA"],
    "CSAN3": ["CSAN3", "COSAN"],
    "RAIZ4": ["RAIZ4", "RAÍZEN", "RAIZEN"],
    # Mineração, Siderurgia e Química
    "VALE3": ["VALE3", "VALE S.A.", "MINERADORA VALE", "VALE DO RIO DOCE", "VALE MINERAÇÃO", "VALE MINERACAO"],
    "GGBR4": ["GGBR4", "GERDAU"],
    "GOAU4": ["GOAU4", "METALÚRGICA GERDAU", "METALURGICA GERDAU"],
    "CSNA3": ["CSNA3", "SIDERÚRGICA NACIONAL", "SIDERURGICA NACIONAL", "CSN"],
    "USIM5": ["USIM5", "USIMINAS"],
    "CMIN3": ["CMIN3", "CSN MINERAÇÃO", "CSN MINERACAO"],
    "CBAV3": ["CBAV3", "COMPANHIA BRASILEIRA DE ALUMÍNIO", "CBA ALUMÍNIO"],
    "FESA4": ["FESA4", "FERBASA"],
    "AURA33": ["AURA33", "AURA MINERALS"],
    "BRKM5": ["BRKM5", "BRASKEM"],
    "UNIP6": ["UNIP6", "UNIPAR CARBOCLORO", "UNIPAR"],
    # Financeiro e Seguros
    "ITUB4": ["ITUB4", "ITUB3", "ITAÚ UNIBANCO", "ITAU UNIBANCO"],
    "ITSA4": ["ITSA4", "ITAÚSA", "ITAUSA"],
    "BBDC4": ["BBDC4", "BBDC3", "BANCO BRADESCO", "BRADESCO"],
    "BBDC3": ["BBDC3", "BBDC4", "BANCO BRADESCO", "BRADESCO"],
    "BBAS3": ["BBAS3", "BANCO DO BRASIL"],
    "SANB11": ["SANB11", "SANTANDER BRASIL", "BANCO SANTANDER"],
    "BPAC11": ["BPAC11", "BTG PACTUAL"],
    "B3SA3": ["B3SA3", "B3 S.A."],
    "BBSE3": ["BBSE3", "BB SEGURIDADE"],
    "CXSE3": ["CXSE3", "CAIXA SEGURIDADE"],
    "PSSA3": ["PSSA3", "PORTO SEGURO"],
    "WIZC3": ["WIZC3", "WIZ CO"],
    # Bens Industriais
    "WEGE3": ["WEGE3", "WEG S.A.", "WEG"],
    "EMBJ3": ["EMBJ3", "EMBR3", "EMBRAER"],
    "POMO4": ["POMO4", "MARCOPOLO"],
    "TUPY3": ["TUPY3", "TUPY"],
    "LEVE3": ["LEVE3", "MAHLE METAL LEVE", "METAL LEVE"],
    "SHUL4": ["SHUL4", "SCHULZ"],
    "ROMI3": ["ROMI3", "INDÚSTRIAS ROMI", "INDUSTRIAS ROMI"],
    "KEPL3": ["KEPL3", "KEPLER WEBER"],
    "MYPK3": ["MYPK3", "IOCHPE-MAXION", "IOCHPE MAXION"],
    "ARML3": ["ARML3", "ARMAC"],
    # Energia Elétrica e Saneamento
    "AXIA3": ["AXIA3", "ELET3", "ELET6", "AXIA ENERGIA", "ELETROBRAS", "ELETROBRÁS"],
    "CPLE3": ["CPLE3", "CPLE6", "COPEL"],
    "CMIG4": ["CMIG4", "CEMIG"],
    "CPFE3": ["CPFE3", "CPFL ENERGIA", "CPFL"],
    "EGIE3": ["EGIE3", "ENGIE BRASIL"],
    "EQTL3": ["EQTL3", "EQUATORIAL ENERGIA"],
    "ENEV3": ["ENEV3", "ENEVA"],
    "TAEE11": ["TAEE11", "TAESA"],
    "ISAE4": ["ISAE4", "ISA ENERGIA", "ISA CTEEP"],
    "ALUP11": ["ALUP11", "ALUPAR"],
    "SBSP3": ["SBSP3", "SABESP"],
    "CSMG3": ["CSMG3", "COPASA"],
    "SAPR11": ["SAPR11", "SANEPAR"],
    "ORVR3": ["ORVR3", "ORIZON VALORIZAÇÃO", "ORIZON"],
    # Papel, Celulose e Madeira
    "SUZB3": ["SUZB3", "SUZANO"],
    "KLBN11": ["KLBN11", "KLABIN"],
    "RANI3": ["RANI3", "CELULOSE IRANI", "IRANI PAPEL"],
    "DXCO3": ["DXCO3", "DEXCO", "DURATEX"],
    # Varejo, Consumo, Alimentos e Agro
    "MGLU3": ["MGLU3", "MAGALU", "MAGAZINE LUIZA"],
    "LREN3": ["LREN3", "LOJAS RENNER"],
    "AZZA3": ["AZZA3", "AZZAS 2154", "AREZZO", "GRUPO SOMA"],
    "VIVA3": ["VIVA3", "VIVARA"],
    "ALPA4": ["ALPA4", "ALPARGATAS"],
    "ABEV3": ["ABEV3", "AMBEV"],
    "ASAI3": ["ASAI3", "ASSAÍ ATACADISTA", "ASSAÍ", "ASSAI"],
    "MBRF3": ["MBRF3", "BRFS3", "MRFG3", "MBRF", "BRF S.A.", "MARFRIG"],
    "BEEF3": ["BEEF3", "MINERVA FOODS"],
    "SMTO3": ["SMTO3", "SÃO MARTINHO", "SAO MARTINHO"],
    "SLCE3": ["SLCE3", "SLC AGRÍCOLA", "SLC AGRICOLA"],
    "NATU3": ["NATU3", "NATURA &CO", "GRUPO NATURA", "NATURA COSMÉTICOS"],
    "MDIA3": ["MDIA3", "M. DIAS BRANCO", "M DIAS BRANCO"],
    "CAML3": ["CAML3", "CAMIL ALIMENTOS"],
    "SMFT3": ["SMFT3", "SMART FIT", "SMARTFIT"],
    "TFCO4": ["TFCO4", "TRACK&FIELD", "TRACK & FIELD"],
    "RIAA3": ["RIAA3", "GUAR3", "GUARARAPES", "LOJAS RIACHUELO"],
    "SBFG3": ["SBFG3", "GRUPO SBF", "CENTAURO"],
    # Construção e Shoppings
    "CYRE3": ["CYRE3", "CYRELA"],
    "EZTC3": ["EZTC3", "EZTEC"],
    "MRVE3": ["MRVE3", "MRV ENGENHARIA"],
    "DIRR3": ["DIRR3", "DIRECIONAL ENGENHARIA"],
    "CURY3": ["CURY3", "CURY CONSTRUTORA"],
    "PLPL3": ["PLPL3", "PLANO & PLANO", "PLANO E PLANO"],
    "TEND3": ["TEND3", "CONSTRUTORA TENDA"],
    "LAVV3": ["LAVV3", "LAVVI"],
    "TRIS3": ["TRIS3", "TRISUL"],
    "JHSF3": ["JHSF3", "JHSF"],
    "LOGG3": ["LOGG3", "LOG COMMERCIAL PROPERTIES", "LOG CP"],
    "EVEN3": ["EVEN3", "EVEN CONSTRUTORA"],
    "ALOS3": ["ALOS3", "ALLOS", "ALIANSCE SONAE"],
    "MULT3": ["MULT3", "MULTIPLAN"],
    "IGTI11": ["IGTI11", "IGUATEMI"],
    # Saúde e Farmacêutica
    "RDOR3": ["RDOR3", "REDE D'OR", "REDE DOR"],
    "HAPV3": ["HAPV3", "HAPVIDA"],
    "RADL3": ["RADL3", "RAIADROGASIL", "RAIA DROGASIL", "DROGA RAIA"],
    "FLRY3": ["FLRY3", "GRUPO FLEURY", "FLEURY"],
    "HYPE3": ["HYPE3", "HYPERA PHARMA", "HYPERA"],
    "BLAU3": ["BLAU3", "BLAU FARMACÊUTICA", "BLAU FARMACEUTICA"],
    "ODPV3": ["ODPV3", "ODONTOPREV"],
    "MATD3": ["MATD3", "HOSPITAL MATER DEI", "MATER DEI"],
    "PARD3": ["PARD3", "HERMES PARDINI"],
    # Tecnologia e Telecomunicações
    "TOTS3": ["TOTS3", "TOTVS"],
    "LWSA3": ["LWSA3", "LWSA", "LOCAWEB"],
    "CASH3": ["CASH3", "MÉLIUZ", "MELIUZ"],
    "POSI3": ["POSI3", "POSITIVO TECNOLOGIA"],
    "INTB3": ["INTB3", "INTELBRAS"],
    "BMOB3": ["BMOB3", "BEMOBI"],
    "VIVT3": ["VIVT3", "TELEFÔNICA BRASIL", "TELEFONICA BRASIL"],
    "TIMS3": ["TIMS3", "TIM BRASIL", "TIM S.A."],
    # Logística, Transporte, Locação e Concessões
    "RENT3": ["RENT3", "LOCALIZA&CO", "LOCALIZA & CO", "LOCALIZA RENT A CAR"],
    "RAIL3": ["RAIL3", "RUMO LOGÍSTICA", "RUMO LOGISTICA"],
    "MOTV3": ["MOTV3", "MOTIVA INFRAESTRUTURA", "GRUPO CCR", "CCR S.A."],
    "ECOR3": ["ECOR3", "ECORODOVIAS"],
    "STBP3": ["STBP3", "SANTOS BRASIL"],
    "TGMA3": ["TGMA3", "TEGMA"],
    "LOGN3": ["LOGN3", "LOG-IN LOGÍSTICA", "LOG-IN LOGISTICA"],
    "SIMH3": ["SIMH3", "SIMPAR"],
    "MOVI3": ["MOVI3", "MOVIDA RENT A CAR", "MOVIDA LOCAÇÃO", "MOVIDA S.A."],
    "VAMO3": ["VAMO3", "VAMOS LOCAÇÃO", "GRUPO VAMOS"],
    # Educação
    "YDUQ3": ["YDUQ3", "YDUQS", "ESTÁCIO"],
    "COGN3": ["COGN3", "COGNA EDUCAÇÃO", "COGNA", "KROTON"],
    "ANIM3": ["ANIM3", "ÂNIMA EDUCAÇÃO", "ANIMA EDUCACAO"],
    "SEER3": ["SEER3", "SER EDUCACIONAL"],
    # BDRs Internacionais
    "NVDC34": ["NVDC34", "NVIDIA"],
    "AAPL34": ["AAPL34", "APPLE"],
    "MSFT34": ["MSFT34", "MICROSOFT"],
    "AMZO34": ["AMZO34", "AMAZON"],
    "GOGL34": ["GOGL34", "GOOGLE", "ALPHABET"],
    "M1TA34": ["M1TA34", "META34", "META PLATFORMS"],
    "TSLA34": ["TSLA34", "TESLA"],
    "MELI34": ["MELI34", "MERCADO LIVRE", "MERCADOLIBRE"],
    "NFLX34": ["NFLX34", "NETFLIX"],
    "TSMC34": ["TSMC34", "TSMC", "TAIWAN SEMICONDUCTOR"],
    "A1MD34": ["A1MD34", "AMD34", "AMD", "ADVANCED MICRO DEVICES"],
    "AVGO34": ["AVGO34", "BROADCOM"],
    "ASML34": ["ASML34", "ASML"],
    "BABA34": ["BABA34", "ALIBABA"],
    "JBSS32": ["JBSS32", "JBS S.A.", "JBS"],
    "BERK34": ["BERK34", "BERKSHIRE HATHAWAY"],
    "JPMC34": ["JPMC34", "JPMORGAN", "JP MORGAN"],
    "VISA34": ["VISA34", "VISA INC"],
    "DISB34": ["DISB34", "WALT DISNEY", "DISNEY"],
    "COCA34": ["COCA34", "COCA-COLA", "COCA COLA"],
    "MCDC34": ["MCDC34", "MCDONALD'S", "MCDONALDS"],
    # ETFs
    "BOVA11": ["BOVA11"],
    "IVVB11": ["IVVB11"],
    "NASD11": ["NASD11"],
    "SMAL11": ["SMAL11"],
    "GOLD11": ["GOLD11"],
    "HASH11": ["HASH11"],
}

# Frases típicas de relatórios sell-side do BTG Pactual sobre terceiros (evita falsos positivos em BPAC11)
_BTG_SELLSIDE_EXCLUSION_RE: re.Pattern[str] = re.compile(
    r"\b("
    r"BTG\s+PACTUAL\s+(?:RECOMENDA|ELEVA|CORTA|REBAIXA|INICIA|MANT[EÉ]M|V[EÊ]|APONTA|PREFERE|DESTACA| ATUALIZA|REVISA)"
    r"|(?:SEGUNDO|CONFORME|PARA)\s+O\s+BTG\s+PACTUAL"
    r"|(?:ANALISTAS?|RELAT[OÓ]RIO|CARTEIRA)\s+DO\s+BTG\s+PACTUAL"
    r")\b",
    re.IGNORECASE,
)


def _compile_alias_pattern(alias: str) -> re.Pattern[str]:
    """Compila regex com fronteira estrita de palavra/token, inclusive para siglas com pontuação."""
    clean = alias.strip().upper()
    left = r"\b" if (clean and clean[0].isalnum()) else r"(?<![0-9A-ZÀ-Ý_])"
    right = r"\b" if (clean and clean[-1].isalnum()) else r"(?![0-9A-ZÀ-Ý_])"
    return re.compile(rf"{left}{re.escape(clean)}{right}", re.IGNORECASE)


def _build_compiled_aliases() -> dict[str, list[re.Pattern[str]]]:
    compiled: dict[str, list[re.Pattern[str]]] = {}
    for base_ticker, alias_list in TICKER_ALIASES.items():
        variants = list(dict.fromkeys([
            base_ticker,
            f"{base_ticker}F",
            f"{base_ticker}.SA",
            *[a.strip().upper() for a in alias_list if a.strip()],
        ]))
        compiled[base_ticker] = [_compile_alias_pattern(v) for v in variants]
    return compiled


_COMPILED_TICKER_PATTERNS: dict[str, list[re.Pattern[str]]] = _build_compiled_aliases()
_EXPLICIT_BPAC_RE: re.Pattern[str] = re.compile(r"\bBPAC11F?(?:\.SA)?\b", re.IGNORECASE)


def text_mentions_ticker(ticker: str, text: str) -> bool:
    """
    Verifica com fronteira de palavra estrita (`\\b...\\b`) se um texto menciona
    o ticker ou algum de seus aliases corporativos oficiais.
    """
    if not text or not ticker:
        return False
    base = normalize_b3_ticker(ticker)
    if not base:
        return False

    upper_text = text.upper()
    if base == "BPAC11":
        if _EXPLICIT_BPAC_RE.search(upper_text):
            return True
        scrubbed = _BTG_SELLSIDE_EXCLUSION_RE.sub(" ", upper_text)
        patterns = _COMPILED_TICKER_PATTERNS.get(base, [])
        return any(p.search(scrubbed) for p in patterns)

    patterns = _COMPILED_TICKER_PATTERNS.get(base)
    if patterns is None:
        patterns = [
            _compile_alias_pattern(base),
            _compile_alias_pattern(f"{base}F"),
            _compile_alias_pattern(f"{base}.SA"),
        ]
        _COMPILED_TICKER_PATTERNS[base] = patterns

    return any(p.search(upper_text) for p in patterns)


def extract_mentioned_tickers(
    text: str,
    allowed_tickers: Optional[set[str]] = None,
) -> list[str]:
    """
    Extrai todos os tickers B3 mencionados em um texto, combinando códigos diretos
    e o registro corporativo `TICKER_ALIASES` com fronteira estrita de palavras.
    """
    if not text:
        return []

    universe: set[str]
    if allowed_tickers is not None:
        universe = allowed_tickers
    else:
        universe = set(getattr(settings, "WATCHLIST", [])) | set(TICKER_ALIASES.keys())

    upper_text = text.upper()
    found: set[str] = set()

    # 1. Códigos explícitos (ex.: PETR4, VALE3F, NVDC34, BOVA11)
    for raw_match in _TICKER_CODE_PATTERN.findall(upper_text):
        base = normalize_b3_ticker(raw_match)
        if base and base in universe:
            found.add(base)

    # 2. Aliases corporativos (ex.: Petrobras -> PETR4, Nvidia -> NVDC34)
    watchlist_set = set(getattr(settings, "WATCHLIST", []))
    candidates = (watchlist_set & set(TICKER_ALIASES.keys())) or set(TICKER_ALIASES.keys())
    if allowed_tickers is not None:
        candidates = candidates & allowed_tickers

    for candidate in candidates:
        if candidate in found:
            continue
        if text_mentions_ticker(candidate, upper_text):
            found.add(candidate)

    return sorted(found)


@dataclass
class NewsItem:
    """Representa uma notícia financeira normalizada."""

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


def _extract_tickers(text: str) -> list[str]:
    """Extrai tickers B3 mencionados em um texto usando códigos e aliases corporativos."""
    return extract_mentioned_tickers(text)


def _random_user_agent() -> str:
    """Retorna um User-Agent aleatório para rotação."""
    return random.choice(_USER_AGENTS)


def _normalize_dt_brt(dt: datetime) -> datetime:
    """Normaliza qualquer datetime (naive ou aware) para timezone BRT."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BRT)
    return dt.astimezone(BRT)


def _parse_rss_date(date_str: str) -> datetime:
    """
    Faz parse de datas RSS/Atom convertendo corretamente GMT/UTC/Z e offsets
    RFC-2822 / ISO-8601 para `America/Sao_Paulo` (BRT).
    """
    if not date_str or not date_str.strip():
        return datetime.now(tz=BRT)

    raw = date_str.strip()

    # 1. RFC-2822 padrão de feeds RSS (ex.: 'Wed, 11 Nov 2026 15:00:00 GMT' ou '+0000' ou '-0300')
    try:
        dt_rfc = parsedate_to_datetime(raw)
        if dt_rfc is not None:
            if dt_rfc.tzinfo is None:
                dt_rfc = dt_rfc.replace(tzinfo=timezone.utc)
            return dt_rfc.astimezone(BRT)
    except (TypeError, ValueError, IndexError):
        pass

    # 2. ISO-8601 (ex.: '2026-11-11T15:00:00Z' ou '2026-11-11T12:00:00-03:00')
    iso_candidate = raw[:-1] + "+00:00" if raw.upper().endswith("Z") else raw
    try:
        dt_iso = datetime.fromisoformat(iso_candidate)
        if dt_iso.tzinfo is None:
            dt_iso = dt_iso.replace(tzinfo=BRT)
        return dt_iso.astimezone(BRT)
    except ValueError:
        pass

    # 3. Formatos explícitos residuais
    utc_formats = (
        "%a, %d %b %Y %H:%M:%S GMT",
        "%a, %d %b %Y %H:%M:%S UTC",
        "%Y-%m-%dT%H:%M:%SZ",
    )
    for fmt in utc_formats:
        try:
            dt_utc = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt_utc.astimezone(BRT)
        except ValueError:
            continue

    local_or_offset_formats = (
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
    )
    for fmt in local_or_offset_formats:
        try:
            dt_loc = datetime.strptime(raw, fmt)
            return _normalize_dt_brt(dt_loc)
        except ValueError:
            continue

    logger.debug("Formato de data RSS não reconhecido: '%s'", date_str)
    return datetime.now(tz=BRT)


def _clean_html(text: str) -> str:
    """Remove tags HTML e decodifica entidades HTML preservando acentuação PT-BR."""
    if not text:
        return ""
    unescaped = html.unescape(text)
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    clean = html.unescape(without_tags)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _decode_http_bytes(raw_bytes: bytes, declared_encoding: Optional[str] = None) -> str:
    """
    Decodifica bytes HTTP priorizando encoding declarado no XML/HTTP ou UTF-8,
    evitando mojibake causado pelo fallback ISO-8859-1 padrão do requests em `text/xml`.
    """
    if not raw_bytes:
        return ""

    # Inspeciona declaração <?xml version="1.0" encoding="..." ?>
    head_ascii = raw_bytes[:200].decode("ascii", errors="ignore")
    xml_enc_match = re.search(r'encoding=["\']([A-Za-z0-9._-]+)["\']', head_ascii, re.IGNORECASE)
    candidates: list[str] = []
    if xml_enc_match:
        candidates.append(xml_enc_match.group(1))
    if declared_encoding and declared_encoding.lower() not in ("iso-8859-1", "latin-1", "latin1"):
        candidates.append(declared_encoding)
    candidates.extend(["utf-8", "utf-8-sig"])
    if declared_encoding:
        candidates.append(declared_encoding)
    candidates.append("latin-1")

    seen: set[str] = set()
    for enc in candidates:
        enc_norm = enc.strip().lower()
        if not enc_norm or enc_norm in seen:
            continue
        seen.add(enc_norm)
        try:
            return raw_bytes.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue

    return raw_bytes.decode("utf-8", errors="replace")


class NewsScraper:
    """
    Scraper de notícias financeiras para ativos da B3.

    Mantém um store rolante thread-safe de 24h (`_article_cache`, até 1000 itens)
    para que ciclos consecutivos de 60s preservem visibilidade completa do noticiário
    recente sem sofrer do bug de `0 notícias`.
    """

    def __init__(self, timeout: Optional[int] = None) -> None:
        resolved_timeout = (
            int(timeout)
            if timeout is not None
            else int(getattr(settings, "news_request_timeout", 15))
        )
        self._timeout: int = resolved_timeout
        self._article_cache: dict[str, NewsItem] = {}
        self._persisted_urls: set[str] = set()
        self._cache_lock: threading.Lock = threading.Lock()
        self._last_request_time: dict[str, float] = {}
        self._rate_limit_lock: threading.Lock = threading.Lock()
        self._thread_local = threading.local()
        self._sessions_lock: threading.Lock = threading.Lock()
        self._sessions: list[requests.Session] = []
        self._executor_lock: threading.Lock = threading.Lock()
        self._executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="cortex-news",
        )
        logger.info("NewsScraper inicializado — timeout: %ds", self._timeout)

    def _get_executor(self) -> ThreadPoolExecutor:
        """Retorna o ThreadPoolExecutor persistente, recriando-o caso close() tenha sido chamado."""
        with self._executor_lock:
            if self._executor is None or getattr(self._executor, "_shutdown", False):
                self._executor = ThreadPoolExecutor(
                    max_workers=4,
                    thread_name_prefix="cortex-news",
                )
            return self._executor

    def _get_session(self) -> requests.Session:
        """Retorna um `requests.Session` isolado por thread com pool limitado."""
        sess = getattr(self._thread_local, "session", None)
        if sess is None:
            sess = requests.Session()
            adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4)
            sess.mount("http://", adapter)
            sess.mount("https://", adapter)
            self._thread_local.session = sess
            with self._sessions_lock:
                self._sessions.append(sess)
        return sess

    def _wait_rate_limit(self, source: str) -> None:
        """Respeita rate limit de 1 requisição/segundo por fonte."""
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

    def _fetch(self, url: str, source: str) -> Optional[str]:
        """Executa requisição HTTP thread-safe com decodificação UTF-8 fiel."""
        self._wait_rate_limit(source)
        headers = {
            "User-Agent": _random_user_agent(),
            "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            "Connection": "close",
        }
        session = self._get_session()

        try:
            response = session.get(
                url,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "")
            charset_match = re.search(r"charset=([A-Za-z0-9._-]+)", content_type, re.IGNORECASE)
            explicit_charset = charset_match.group(1) if charset_match else None
            return _decode_http_bytes(response.content, declared_encoding=explicit_charset)
        except requests.Timeout:
            logger.warning("Timeout ao acessar %s (%s)", source, url)
        except requests.ConnectionError:
            logger.warning("Erro de conexão ao acessar %s (%s)", source, url)
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else 0
            logger.warning("HTTP %d ao acessar %s: %s", status_code, source, url)
        except requests.RequestException as exc:
            logger.warning("Erro de requisição para %s: %s", source, exc)

        return None

    def _parse_rss_items(self, content: str, source_label: str) -> list[NewsItem]:
        """
        Converte payload XML/RSS em lista de `NewsItem` usando `feedparser` (quando disponível)
        com fallback resiliente para `xml.etree.ElementTree`.
        """
        if not content:
            return []

        items: list[NewsItem] = []
        seen_in_batch: set[str] = set()

        if _FEEDPARSER_AVAILABLE:
            try:
                parsed = feedparser.parse(content)
                entries = getattr(parsed, "entries", None) or []
                if entries:
                    for entry in entries:
                        link = str(entry.get("link") or "").strip()
                        raw_title = str(entry.get("title") or "")
                        raw_desc = str(entry.get("summary") or entry.get("description") or "")
                        raw_date = str(
                            entry.get("published")
                            or entry.get("updated")
                            or entry.get("pubDate")
                            or ""
                        )

                        title = _clean_html(raw_title)
                        description = _clean_html(raw_desc)
                        if not link or not title or link in seen_in_batch:
                            continue
                        seen_in_batch.add(link)

                        pub_date = _parse_rss_date(raw_date) if raw_date else datetime.now(tz=BRT)
                        tickers = _extract_tickers(f"{title} {description}")

                        items.append(
                            NewsItem(
                                title=title,
                                summary=description[:500],
                                source=source_label,
                                url=link,
                                published_at=pub_date,
                                tickers_mentioned=tickers,
                            )
                        )
                    return items
            except Exception as exc:
                logger.debug("Fallback ElementTree ativado para %s (%s)", source_label, exc)

        try:
            root = ElementTree.fromstring(content)
            channel = root.find("channel")
            item_nodes = channel.findall("item") if channel is not None else root.findall(".//item")

            for item_elem in item_nodes:
                title_elem = item_elem.find("title")
                link_elem = item_elem.find("link")
                desc_elem = item_elem.find("description")
                pubdate_elem = item_elem.find("pubDate")

                title = _clean_html(title_elem.text if title_elem is not None and title_elem.text else "")
                link = (link_elem.text or "").strip() if link_elem is not None and link_elem.text else ""
                description = _clean_html(desc_elem.text if desc_elem is not None and desc_elem.text else "")
                pub_date = (
                    _parse_rss_date(pubdate_elem.text)
                    if pubdate_elem is not None and pubdate_elem.text
                    else datetime.now(tz=BRT)
                )

                if not link or not title or link in seen_in_batch:
                    continue
                seen_in_batch.add(link)

                tickers = _extract_tickers(f"{title} {description}")
                items.append(
                    NewsItem(
                        title=title,
                        summary=description[:500],
                        source=source_label,
                        url=link,
                        published_at=pub_date,
                        tickers_mentioned=tickers,
                    )
                )
        except ElementTree.ParseError as exc:
            logger.warning("Erro ao parsear RSS de %s: %s", source_label, exc)
        except Exception as exc:
            logger.warning("Erro inesperado ao processar RSS de %s: %s", source_label, exc)

        return items

    def _merge_and_prune_cache(self, new_items: list[NewsItem]) -> list[NewsItem]:
        """
        Mescla artigos coletados no cache em memória de 24h, remove itens expirados
        (>24h), limita a 1000 artigos e retorna a lista ordenada do mais recente ao mais antigo.
        """
        now_brt = datetime.now(tz=BRT)
        cutoff = now_brt - timedelta(hours=_CACHE_RETENTION_HOURS)

        with self._cache_lock:
            # 1. Remove artigos expirados (> 24h)
            expired_urls = [
                url
                for url, item in self._article_cache.items()
                if _normalize_dt_brt(item.published_at) < cutoff
            ]
            for url in expired_urls:
                self._article_cache.pop(url, None)
                self._persisted_urls.discard(url)

            # 2. Mescla itens recentes (<= 24h)
            for item in new_items:
                item.published_at = _normalize_dt_brt(item.published_at)
                if item.published_at < cutoff:
                    continue
                existing = self._article_cache.get(item.url)
                if existing is not None:
                    merged_tickers = sorted(set(existing.tickers_mentioned) | set(item.tickers_mentioned))
                    item.tickers_mentioned = merged_tickers
                self._article_cache[item.url] = item

            # 3. Ordena mais recentes primeiro e aplica teto de 1000 itens
            ordered = sorted(
                self._article_cache.values(),
                key=lambda n: _normalize_dt_brt(n.published_at),
                reverse=True,
            )
            if len(ordered) > _MAX_CACHED_ARTICLES:
                evicted = ordered[_MAX_CACHED_ARTICLES:]
                ordered = ordered[:_MAX_CACHED_ARTICLES]
                for ev in evicted:
                    self._article_cache.pop(ev.url, None)
                    self._persisted_urls.discard(ev.url)

            return list(ordered)

    def get_unpersisted_news(self) -> list[NewsItem]:
        """
        Retorna artigos presentes no cache de 24h que ainda não foram marcados
        como persistidos nesta sessão e marca-os como persistidos.
        """
        with self._cache_lock:
            fresh: list[NewsItem] = []
            for url, item in self._article_cache.items():
                if url not in self._persisted_urls:
                    self._persisted_urls.add(url)
                    fresh.append(item)
            fresh.sort(key=lambda n: _normalize_dt_brt(n.published_at), reverse=True)
            return fresh

    def _fetch_google_news(self, query: str = "bolsa B3 ações when:1d") -> list[NewsItem]:
        """Coleta notícias recentes do Google News RSS."""
        encoded_query = requests.utils.quote(query)
        url = (
            f"https://news.google.com/rss/search?"
            f"q={encoded_query}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        )
        content = self._fetch(url, "google_news")
        items = self._parse_rss_items(content or "", "Google News")
        if items:
            logger.info("Google News: %d notícias lidas do feed", len(items))
        return items

    def _fetch_infomoney(self) -> list[NewsItem]:
        """Coleta notícias do feed RSS da InfoMoney."""
        content = self._fetch("https://www.infomoney.com.br/feed/", "infomoney")
        items = self._parse_rss_items(content or "", "InfoMoney")
        if items:
            logger.info("InfoMoney: %d notícias lidas do feed", len(items))
        return items

    def _fetch_valor_investe(self) -> list[NewsItem]:
        """Coleta notícias do feed RSS do Valor Investe (Globo)."""
        content = self._fetch("https://pox.globo.com/rss/valorinveste/", "valor_investe")
        items = self._parse_rss_items(content or "", "Valor Investe")
        if items:
            logger.info("Valor Investe: %d notícias lidas do feed", len(items))
        return items

    def _fetch_money_times(self) -> list[NewsItem]:
        """Coleta notícias do feed RSS do Money Times."""
        content = self._fetch("https://www.moneytimes.com.br/feed/", "money_times")
        items = self._parse_rss_items(content or "", "Money Times")
        if items:
            logger.info("Money Times: %d notícias lidas do feed", len(items))
        return items

    def fetch_all_news(self) -> list[NewsItem]:
        """
        Coleta notícias de todas as fontes, atualiza o store rolante de 24h
        e retorna todos os artigos recentes consolidados (mais recentes primeiro).
        """
        fetchers = [
            ("Google News", self._fetch_google_news),
            ("InfoMoney", self._fetch_infomoney),
            ("Valor Investe", self._fetch_valor_investe),
            ("Money Times", self._fetch_money_times),
        ]

        scraped_items: list[NewsItem] = []
        executor = self._get_executor()
        future_to_source = {executor.submit(fn): name for name, fn in fetchers}
        for future in as_completed(future_to_source):
            src_name = future_to_source[future]
            try:
                items = future.result()
                if items:
                    scraped_items.extend(items)
            except Exception as exc:
                logger.warning("Falha ao coletar notícias de %s: %s", src_name, exc)

        cached_recent = self._merge_and_prune_cache(scraped_items)

        logger.info(
            "Notícias ativas (janela 24h): %d (Google: %d, InfoMoney: %d, Valor: %d, Money Times: %d)",
            len(cached_recent),
            sum(1 for n in cached_recent if n.source == "Google News"),
            sum(1 for n in cached_recent if n.source == "InfoMoney"),
            sum(1 for n in cached_recent if n.source == "Valor Investe"),
            sum(1 for n in cached_recent if n.source == "Money Times"),
        )
        return cached_recent

    def fetch_ticker_news(self, ticker: str) -> list[NewsItem]:
        """
        Coleta notícias relacionadas a um ticker específico mesclando busca dedicada
        e o cache rolante de 24h.
        """
        base = normalize_b3_ticker(ticker)
        if not base:
            return []

        scraped: list[NewsItem] = []
        try:
            scraped.extend(self._fetch_google_news(query=f"{base} ações B3 when:1d"))
        except Exception as exc:
            logger.warning("Falha ao buscar notícias de %s no Google News: %s", base, exc)

        try:
            scraped.extend(self._fetch_infomoney())
        except Exception as exc:
            logger.warning("Falha ao buscar notícias de %s no InfoMoney: %s", base, exc)

        all_recent = self._merge_and_prune_cache(scraped)

        filtered: list[NewsItem] = []
        for item in all_recent:
            if base in item.tickers_mentioned or text_mentions_ticker(base, f"{item.title} {item.summary}"):
                if base not in item.tickers_mentioned:
                    item.tickers_mentioned.append(base)
                    item.tickers_mentioned.sort()
                filtered.append(item)

        filtered.sort(key=lambda n: _normalize_dt_brt(n.published_at), reverse=True)
        logger.info("Notícias para %s: %d encontradas", base, len(filtered))
        return filtered

    def close(self) -> None:
        """Encerra o pool persistente e fecha todas as sessões HTTP criadas pelas threads."""
        with self._executor_lock:
            executor = self._executor
            self._executor = None
        if executor is not None:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except Exception as exc:
                logger.debug("Erro ao encerrar executor do NewsScraper: %s", exc)

        with self._sessions_lock:
            sessions = list(self._sessions)
            self._sessions.clear()
        for sess in sessions:
            try:
                sess.close()
            except Exception as exc:
                logger.debug("Erro ao fechar sessão HTTP: %s", exc)
        self._thread_local = threading.local()
        logger.debug("Executor e sessões HTTP do NewsScraper encerrados")

    def __repr__(self) -> str:
        with self._cache_lock:
            count = len(self._article_cache)
        return f"NewsScraper(timeout={self._timeout}s, artigos_24h={count})"

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
