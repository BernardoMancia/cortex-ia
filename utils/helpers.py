"""
Funções utilitárias do Projeto Córtex.

Fornece helpers para formatação monetária, manipulação de datas,
cálculos financeiros, normalização e roteamento de lotes da B3.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

BRT: Final[ZoneInfo] = ZoneInfo("America/Sao_Paulo")

# ETFs e FIIs negociados em lote unitário (1 cota) na B3 sem sufixo 'F'.
# Units de ações (ex.: BPAC11, TAEE11, SANB11, KLBN11, IGTI11, SAPR11, ALUP11)
# NÃO entram nesta lista pois operam em lote padrão de 100 e usam 'F' para 1..99.
B3_ETF_FII_TICKERS: Final[frozenset[str]] = frozenset({
    "IVVB11", "NASD11", "BOVA11", "BOVV11", "SMAL11", "SMALL11",
    "GOLD11", "HASH11", "XFIX11", "DIVO11", "SPXI11", "BRAX11",
    "ECOO11", "FIND11", "GOVE11", "ISUS11", "MATB11", "PIBB11",
    "QBTC11", "QETH11", "TECK11", "TRIG11", "UTLL11", "WRLD11",
    "IMAB11", "B5P211", "LFTS11", "KDIF11", "HGLG11", "KNRI11",
    "MXRF11", "XPML11", "VISC11", "BTLG11", "KNCR11", "KNIP11",
})

B3_EQUITY_UNITS: Final[frozenset[str]] = frozenset({
    "BPAC11", "SANB11", "TAEE11", "KLBN11", "IGTI11", "SAPR11",
    "ALUP11", "ENGI11", "BIOM11", "MODL11", "RNEW11", " DASA11".strip(),
})


def normalize_b3_ticker(ticker: str) -> str:
    """
    Normaliza qualquer código B3/Yahoo para o ticker-base da B3 (sem '.SA' e sem 'F' fracionário).

    Exemplos::

        >>> normalize_b3_ticker('PETR4F')
        'PETR4'
        >>> normalize_b3_ticker('PETR4.SA')
        'PETR4'
        >>> normalize_b3_ticker('BEEF3F')
        'BEEF3'
        >>> normalize_b3_ticker('TAEE11F')
        'TAEE11'
        >>> normalize_b3_ticker('BOVA11')
        'BOVA11'
    """
    if not ticker:
        return ""
    clean = ticker.strip().upper()
    if clean.endswith(".SA"):
        clean = clean[:-3]
    if len(clean) >= 5 and clean.endswith("F") and clean[-2].isdigit():
        clean = clean[:-1]
    return clean


def is_b3_unit_lot_asset(ticker: str) -> bool:
    """
    Retorna True se o ativo já opera em lote padrão unitário (1 unidade) na B3
    e portanto NÃO utiliza sufixo 'F' (ETFs, FIIs e BDRs terminados em 31..35/39).
    """
    base = normalize_b3_ticker(ticker)
    if not base:
        return False
    if base in B3_EQUITY_UNITS:
        return False
    if base in B3_ETF_FII_TICKERS:
        return True
    try:
        from config.settings import settings
        if base in settings.B3_PORTFOLIOS.get("ETFS", []):
            return True
        if base in settings.B3_PORTFOLIOS.get("BDRS", []):
            return True
    except Exception:
        pass
    if any(base.endswith(suf) for suf in ("31", "32", "33", "34", "35", "39")):
        return True
    # Para outros códigos '11' desconhecidos que não estão listados como Equity Unit,
    # na B3 a esmagadora maioria são ETFs/FIIs (lote 1).
    if base.endswith("11") and base not in B3_EQUITY_UNITS:
        return True
    return False


def format_b3_ticker_for_quantity(ticker: str, quantity: int = 1) -> str:
    """
    Formata o símbolo B3 adequado para uma quantidade específica:
    - ETFs/FIIs/BDRs: sempre ticker-base sem 'F' (ex: BOVA11, AAPL34).
    - Ações ON/PN e Units (ex: PETR4, VALE3, TAEE11, BPAC11):
      - 1..99 ações -> sufixo 'F' (ex: PETR4F, TAEE11F)
      - >= 100 ações -> ticker-base sem 'F' (ex: PETR4, TAEE11)
    """
    base = normalize_b3_ticker(ticker)
    if not base:
        return ""
    if is_b3_unit_lot_asset(base):
        return base
    if 0 < quantity < 100:
        return f"{base}F"
    return base


def split_b3_order_legs(ticker: str, quantity: int) -> list[tuple[str, int]]:
    """
    Divide uma ordem B3 em pernas válidas conforme regras de lote da B3:
    - Ativos de lote unitário (ETFs/BDRs): [(base, quantity)]
    - Ações e Units (lote padrão 100 + fracionário 1..99):
      - Ex.: ('PETR4', 255) -> [('PETR4', 200), ('PETR4F', 55)]
      - Ex.: ('PETR4', 55)  -> [('PETR4F', 55)]
      - Ex.: ('PETR4', 200) -> [('PETR4', 200)]
    """
    base = normalize_b3_ticker(ticker)
    if not base or quantity <= 0:
        return []
    if is_b3_unit_lot_asset(base):
        return [(base, int(quantity))]
    std_qty = (int(quantity) // 100) * 100
    frac_qty = int(quantity) % 100
    legs: list[tuple[str, int]] = []
    if std_qty > 0:
        legs.append((base, std_qty))
    if frac_qty > 0:
        legs.append((f"{base}F", frac_qty))
    return legs


def format_brl(value: float) -> str:
    """
    Formata um valor numérico para o padrão monetário brasileiro.

    Args:
        value: Valor em reais.

    Returns:
        String formatada no padrão 'R$ 1.234,56'.
    """
    try:
        val = float(value)
    except (TypeError, ValueError):
        val = 0.0
    if val != val:  # NaN guard
        val = 0.0
    formatted = f"{abs(val):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "-" if val < 0 else ""
    return f"{sign}R$ {formatted}"


def get_brt_now() -> datetime:
    """
    Retorna o datetime atual com timezone America/Sao_Paulo (BRT).
    """
    return datetime.now(tz=BRT)


def percentage_change(old: float, new: float) -> float:
    """
    Calcula a variação percentual entre dois valores.
    """
    if old == 0.0:
        return 0.0
    return ((new - old) / old) * 100.0


def safe_division(a: float, b: float, default: float = 0.0) -> float:
    """
    Divisão segura que retorna um valor padrão ao dividir por zero.
    """
    if b == 0.0:
        return default
    return a / b


def ensure_fractional_ticker(ticker: str, quantity: int = 1) -> str:
    """
    Retorna o símbolo adequado da B3 respeitando ETFs/BDRs, Units e lotes padrão/fracionário.
    """
    return format_b3_ticker_for_quantity(ticker, quantity=quantity)


def to_yfinance_ticker(ticker: str) -> str:
    """
    Converte ticker da B3 para o formato do Yahoo Finance ('.SA').
    """
    base = normalize_b3_ticker(ticker)
    if not base:
        return ""
    return f"{base}.SA"


def format_timestamp(dt: datetime) -> str:
    """
    Formata datetime para string legível no padrão brasileiro.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone(BRT)
    return dt.strftime("%d/%m/%Y %H:%M:%S") + " BRT"


def truncate_text(text: str, max_len: int = 200) -> str:
    """
    Trunca texto para o comprimento máximo especificado.
    """
    if not text:
        return ""
    text = text.strip()
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def format_percent(value: float, decimals: int = 2) -> str:
    """
    Formata valor como percentual no padrão brasileiro (0.10 -> '10,00%').
    """
    pct = value * 100
    formatted = f"{pct:.{decimals}f}".replace(".", ",")
    return f"{formatted}%"


def format_number(value: float, decimals: int = 2) -> str:
    """
    Formata número decimal no padrão brasileiro.
    """
    formatted = f"{value:,.{decimals}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return formatted


def clamp(value: float, min_val: float, max_val: float) -> float:
    """
    Limita valor entre mínimo e máximo.
    """
    return max(min_val, min(max_val, value))
