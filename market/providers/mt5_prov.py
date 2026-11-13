from __future__ import annotations

from datetime import datetime, timezone
import math
import threading
import time
from typing import Any
import pandas as pd
import requests

from config.settings import settings
from domain.b3_rules import BRT, format_b3_ticker_for_quantity, normalize_b3_ticker
from domain.models import MarketQuote
from observability.logger import get_logger

logger = get_logger("market.mt5_bridge")

OHLCV_COLUMNS: list[str] = ["Open", "High", "Low", "Close", "Volume"]
HEALTH_TTL_SECONDS: float = 15.0


def _safe_float(val: object, default: float = 0.0) -> float:
    try:
        num = float(val)
        return num if math.isfinite(num) else default
    except (TypeError, ValueError):
        return default


def _parse_epoch_seconds(raw_ts: object) -> float:
    if raw_ts is None:
        return time.time()
    if isinstance(raw_ts, (int, float)):
        val = float(raw_ts)
        if not math.isfinite(val) or val <= 0.0:
            return time.time()
        if val > 1e11:
            return val / 1000.0
        return val
    text = str(raw_ts).strip()
    if not text:
        return time.time()
    try:
        num = float(text)
        if math.isfinite(num) and num > 0.0:
            return num / 1000.0 if num > 1e11 else num
    except ValueError:
        pass
    try:
        iso_clean = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(iso_clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BRT)
        return dt.timestamp()
    except ValueError:
        return time.time()


def _parse_bar_timestamp(raw_t: object, is_daily: bool) -> pd.Timestamp | None:
    if raw_t is None:
        return None
    if is_daily:
        if isinstance(raw_t, (int, float)):
            val = float(raw_t)
            if not math.isfinite(val) or val <= 0.0:
                return None
            sec = val / 1000.0 if val > 1e11 else val
            civil_date = datetime.fromtimestamp(sec, tz=timezone.utc).date()
            return pd.Timestamp(civil_date)
        raw_str = str(raw_t).strip()
        if len(raw_str) >= 10 and raw_str[4:5] == "-" and raw_str[7:8] == "-":
            try:
                return pd.Timestamp(raw_str[:10])
            except ValueError:
                return None
        try:
            num = float(raw_str)
            sec = num / 1000.0 if num > 1e11 else num
            civil_date = datetime.fromtimestamp(sec, tz=timezone.utc).date()
            return pd.Timestamp(civil_date)
        except ValueError:
            return None

    if isinstance(raw_t, (int, float)):
        val = float(raw_t)
        if not math.isfinite(val) or val <= 0.0:
            return None
        sec = val / 1000.0 if val > 1e11 else val
        return pd.Timestamp(datetime.fromtimestamp(sec, tz=BRT))
    raw_str = str(raw_t).strip()
    if not raw_str:
        return None
    try:
        iso_clean = raw_str[:-1] + "+00:00" if raw_str.endswith("Z") else raw_str
        dt = datetime.fromisoformat(iso_clean)
        dt = dt.replace(tzinfo=BRT) if dt.tzinfo is None else dt.astimezone(BRT)
        return pd.Timestamp(dt)
    except ValueError:
        return None


class MT5BridgeMarketProvider:
    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url: str = (base_url or settings.MT5_BRIDGE_URL).strip().rstrip("/")
        self.api_key: str = api_key if api_key is not None else settings.BRIDGE_API_KEY
        self._lock = threading.RLock()
        self._session = requests.Session()
        self._session.headers.update(
            {
                "X-Bridge-Key": self.api_key,
                "Accept": "application/json",
            }
        )
        self._health_cached_at: float = 0.0
        self._health_state: bool = False
        self._ohlcv_cache: dict[tuple[str, str, int], tuple[float, pd.DataFrame]] = {}

    def is_available(self) -> bool:
        mono_now = time.monotonic()
        with self._lock:
            if self._health_cached_at > 0.0 and (mono_now - self._health_cached_at) <= HEALTH_TTL_SECONDS:
                return self._health_state

        ok = False
        try:
            with self._lock:
                resp = self._session.get(f"{self.base_url}/health", timeout=3.5)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, dict) and bool(payload.get("connected")):
                    ok = True
        except Exception as exc:
            logger.debug("Bridge MT5 indisponível em /health: %s", exc)
            ok = False

        with self._lock:
            self._health_cached_at = time.monotonic()
            self._health_state = ok
        return ok

    def fetch_tick(self, ticker: str) -> MarketQuote | None:
        base_tk = normalize_b3_ticker(ticker)
        symbol = format_b3_ticker_for_quantity(base_tk, 100)
        if not base_tk or not symbol:
            return None

        try:
            with self._lock:
                resp = self._session.get(f"{self.base_url}/tick/{symbol}", timeout=3.5)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not isinstance(data, dict):
                return None

            bid = _safe_float(data.get("bid"), 0.0)
            ask = _safe_float(data.get("ask"), 0.0)
            last = _safe_float(data.get("last") or data.get("price"), 0.0)
            if last > 0.0:
                price = round(last, 2)
            elif bid > 0.0 and ask > 0.0:
                price = round((bid + ask) / 2.0, 2)
            else:
                price = round(max(bid, ask), 2)

            if price <= 0.0:
                return None

            eff_bid = round(bid if bid > 0.0 else price, 2)
            eff_ask = round(ask if ask > 0.0 else max(eff_bid, price), 2)
            spread_pct = round(max(0.0, eff_ask - eff_bid) / price, 6)
            volume = round(
                _safe_float(
                    data.get("volume_real")
                    or data.get("real_volume")
                    or data.get("volume"),
                    0.0,
                ),
                2,
            )
            change_pct = round(
                _safe_float(data.get("change_pct") or data.get("session_change_pct"), 0.0),
                4,
            )
            raw_ts = (
                data.get("quote_ts")
                or data.get("time_msc")
                or data.get("time")
                or data.get("timestamp")
            )
            quote_ts = _parse_epoch_seconds(raw_ts)
            is_stale = (time.time() - quote_ts) > settings.STALE_QUOTE_MAX_SECONDS

            return MarketQuote(
                ticker=base_tk,
                price=price,
                bid=eff_bid,
                ask=eff_ask,
                spread_pct=spread_pct,
                volume=volume,
                change_pct=change_pct,
                quote_ts=quote_ts,
                source="mt5_bridge",
                is_stale=is_stale,
            )
        except Exception as exc:
            logger.debug("Falha ao buscar tick MT5 para %s: %s", symbol, exc)
            return None

    def fetch_ohlcv(self, ticker: str, timeframe: str = "D1", bars: int = 120) -> pd.DataFrame:
        base_tk = normalize_b3_ticker(ticker)
        symbol = format_b3_ticker_for_quantity(base_tk, 100)
        tf_clean = (timeframe or "D1").strip().upper()
        capped_bars = max(10, int(bars))
        if not base_tk or not symbol:
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        cache_key = (base_tk, tf_clean, capped_bars)
        mono_now = time.monotonic()
        with self._lock:
            cached = self._ohlcv_cache.get(cache_key)
            if cached is not None and (mono_now - cached[0]) <= settings.OHLCV_CACHE_TTL:
                return cached[1].copy()

        try:
            with self._lock:
                resp = self._session.get(
                    f"{self.base_url}/ohlcv/{symbol}",
                    params={"timeframe": tf_clean, "bars": capped_bars},
                    timeout=6.0,
                )
            if resp.status_code != 200:
                return pd.DataFrame(columns=OHLCV_COLUMNS)
            payload = resp.json()
            rows_raw: list[dict[str, Any]]
            if isinstance(payload, list):
                rows_raw = [r for r in payload if isinstance(r, dict)]
            elif isinstance(payload, dict):
                candidate = payload.get("bars") or payload.get("rates") or payload.get("data") or []
                rows_raw = [r for r in candidate if isinstance(r, dict)]
            else:
                return pd.DataFrame(columns=OHLCV_COLUMNS)

            if not rows_raw:
                return pd.DataFrame(columns=OHLCV_COLUMNS)

            is_daily = tf_clean in ("D1", "1D", "DAILY")
            records: list[dict[str, Any]] = []
            indices: list[pd.Timestamp] = []

            for item in rows_raw:
                lower_map = {str(k).lower(): v for k, v in item.items()}
                ts = _parse_bar_timestamp(
                    lower_map.get("time") or lower_map.get("date") or lower_map.get("timestamp"),
                    is_daily=is_daily,
                )
                o_val = _safe_float(lower_map.get("open"), 0.0)
                h_val = _safe_float(lower_map.get("high"), 0.0)
                l_val = _safe_float(lower_map.get("low"), 0.0)
                c_val = _safe_float(lower_map.get("close"), 0.0)
                real_vol = _safe_float(lower_map.get("real_volume"), 0.0)
                vol = real_vol if real_vol > 0.0 else _safe_float(
                    lower_map.get("volume") or lower_map.get("tick_volume"),
                    0.0,
                )
                if ts is None or c_val <= 0.0 or o_val <= 0.0 or h_val <= 0.0 or l_val <= 0.0:
                    continue
                indices.append(ts)
                records.append(
                    {
                        "Open": o_val,
                        "High": h_val,
                        "Low": l_val,
                        "Close": c_val,
                        "Volume": max(0.0, vol),
                    }
                )

            if not records:
                return pd.DataFrame(columns=OHLCV_COLUMNS)

            df = pd.DataFrame(records, index=pd.DatetimeIndex(indices), columns=OHLCV_COLUMNS)
            df = df[~df.index.duplicated(keep="last")].sort_index()
            with self._lock:
                self._ohlcv_cache[cache_key] = (time.monotonic(), df)
            return df.copy()
        except Exception as exc:
            logger.debug("Falha ao buscar OHLCV MT5 (%s, %s): %s", symbol, tf_clean, exc)
            return pd.DataFrame(columns=OHLCV_COLUMNS)
