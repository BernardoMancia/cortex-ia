from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
import re
from typing import Final
from zoneinfo import ZoneInfo

from config.b3_universe import B3_EQUITY_UNITS, B3_ETF_FII_TICKERS, B3_PORTFOLIOS

BRT: Final[ZoneInfo] = ZoneInfo("America/Sao_Paulo")
_SQLITE_UTC_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$"
)

B3_FIXED_HOLIDAYS: Final[tuple[tuple[int, int], ...]] = (
    (1, 1),
    (21, 4),
    (1, 5),
    (7, 9),
    (12, 10),
    (2, 11),
    (15, 11),
    (20, 11),
    (24, 12),
    (25, 12),
    (31, 12),
)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def get_brt_now() -> datetime:
    return datetime.now(tz=BRT)


def easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def get_b3_movable_holidays(year: int) -> set[date]:
    easter = easter_date(year)
    return {
        easter - timedelta(days=48),
        easter - timedelta(days=47),
        easter - timedelta(days=2),
        easter + timedelta(days=60),
    }


def get_ash_wednesday(year: int) -> date:
    return easter_date(year) - timedelta(days=46)


def is_b3_business_day(check_date: date | None = None) -> bool:
    target = check_date or get_brt_now().date()
    if target.weekday() >= 5:
        return False
    if (target.day, target.month) in B3_FIXED_HOLIDAYS:
        return False
    if target.month == 12:
        dec_31 = date(target.year, 12, 31)
        if dec_31.weekday() == 5 and target.day == 30:
            return False
        if dec_31.weekday() == 6 and target.day == 29:
            return False
    if target in get_b3_movable_holidays(target.year):
        return False
    return True


def add_b3_business_days(start_date: date, business_days: int = 2) -> date:
    curr = start_date
    remaining = max(0, int(business_days))
    while remaining > 0:
        curr = curr + timedelta(days=1)
        if is_b3_business_day(curr):
            remaining -= 1
    return curr


def next_b3_session_open(from_dt: datetime | None = None) -> datetime:
    now = from_dt.astimezone(BRT) if from_dt and from_dt.tzinfo else (from_dt.replace(tzinfo=BRT) if from_dt else get_brt_now())
    candidate_date = now.date()
    open_t = time(13, 0) if candidate_date == get_ash_wednesday(candidate_date.year) else time(10, 0)
    if is_b3_business_day(candidate_date) and now.time() < open_t:
        return datetime.combine(candidate_date, open_t, tzinfo=BRT)
    nxt = candidate_date + timedelta(days=1)
    while not is_b3_business_day(nxt):
        nxt = nxt + timedelta(days=1)
    nxt_open = time(13, 0) if nxt == get_ash_wednesday(nxt.year) else time(10, 0)
    return datetime.combine(nxt, nxt_open, tzinfo=BRT)


def normalize_b3_ticker(ticker: str) -> str:
    if not ticker:
        return ""
    clean = ticker.strip().upper()
    if clean.endswith(".SA"):
        clean = clean[:-3]
    if len(clean) >= 5 and clean.endswith("F") and clean[-2].isdigit():
        clean = clean[:-1]
    return clean


def is_b3_unit_lot_asset(ticker: str) -> bool:
    base = normalize_b3_ticker(ticker)
    if not base:
        return False
    if base in B3_EQUITY_UNITS:
        return False
    if base in B3_ETF_FII_TICKERS:
        return True
    if base in B3_PORTFOLIOS.get("ETFS", []) or base in B3_PORTFOLIOS.get("BDRS", []):
        return True
    if any(base.endswith(suf) for suf in ("31", "32", "33", "34", "35", "39")):
        return True
    if base.endswith("11") and base not in B3_EQUITY_UNITS:
        return True
    return False


def is_b3_pf_20k_exempt_eligible_stock(ticker: str) -> bool:
    base = normalize_b3_ticker(ticker)
    if not base:
        return False
    if base in B3_EQUITY_UNITS:
        return True
    if is_b3_unit_lot_asset(base):
        return False
    return bool(re.match(r"^[A-Z][A-Z0-9]{3}(?:3|4|5|6|7|8)$", base))


def format_b3_ticker_for_quantity(ticker: str, quantity: int = 1) -> str:
    base = normalize_b3_ticker(ticker)
    if not base:
        return ""
    if is_b3_unit_lot_asset(base):
        return base
    if 0 < quantity < 100:
        return f"{base}F"
    return base


def split_b3_order_legs(ticker: str, quantity: int) -> list[tuple[str, int]]:
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


def to_yfinance_ticker(ticker: str) -> str:
    base = normalize_b3_ticker(ticker)
    return f"{base}.SA" if base else ""


def format_brl(value: float | None) -> str:
    try:
        val = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        val = 0.0
    if not math.isfinite(val):
        val = 0.0
    formatted = f"{abs(val):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "-" if val < 0 else ""
    return f"{sign}R$ {formatted}"


def format_percent(value: float | None, decimals: int = 2) -> str:
    try:
        val = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        val = 0.0
    if not math.isfinite(val):
        val = 0.0
    return f"{val * 100.0:.{decimals}f}%".replace(".", ",")


def format_timestamp(dt: datetime | str) -> str:
    if isinstance(dt, str):
        raw = dt.strip()
        if not raw:
            return ""
        try:
            if _SQLITE_UTC_RE.match(raw):
                parsed = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc).astimezone(BRT)
            elif raw.endswith("Z"):
                parsed = datetime.fromisoformat(raw[:-1] + "+00:00").astimezone(BRT)
            else:
                parsed = datetime.fromisoformat(raw)
                parsed = parsed.replace(tzinfo=BRT) if parsed.tzinfo is None else parsed.astimezone(BRT)
            return parsed.strftime("%d/%m/%Y %H:%M:%S") + " BRT"
        except ValueError:
            return raw
    target = dt.replace(tzinfo=BRT) if dt.tzinfo is None else dt.astimezone(BRT)
    return target.strftime("%d/%m/%Y %H:%M:%S") + " BRT"
