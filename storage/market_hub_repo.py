from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from domain.b3_rules import BRT, get_brt_now, normalize_b3_ticker
from storage.connection import DatabaseConnection


class MarketHubRepository:
    def __init__(self, db: DatabaseConnection | None = None) -> None:
        self.db = db or DatabaseConnection()

    def upsert_market_snapshots(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        now_iso = get_brt_now().isoformat()
        batch: list[tuple[Any, ...]] = []
        for r in rows:
            tk = normalize_b3_ticker(str(r.get("ticker", "")))
            if not tk:
                continue
            batch.append(
                (
                    tk,
                    float(r.get("price", 0.0) or 0.0),
                    float(r.get("bid", 0.0) or 0.0),
                    float(r.get("ask", 0.0) or 0.0),
                    float(r.get("spread_pct", 0.0) or 0.0),
                    float(r.get("volume", 0.0) or 0.0),
                    float(r.get("change_pct", 0.0) or 0.0),
                    float(r.get("rsi", 50.0) or 50.0),
                    float(r.get("macd_hist", 0.0) or 0.0),
                    float(r.get("atr", 0.0) or 0.0),
                    float(r.get("adx", 0.0) or 0.0),
                    str(r.get("trend_signal", "NEUTRAL") or "NEUTRAL"),
                    int(bool(r.get("mtf_bullish", True))),
                    float(r.get("sentiment_score", 0.0) or 0.0),
                    int(bool(r.get("is_stale", False))),
                    str(r.get("updated_at") or now_iso),
                )
            )
        if not batch:
            return
        with self.db.transaction() as conn:
            conn.executemany(
                """
                INSERT INTO market_snapshots (
                    ticker, price, bid, ask, spread_pct, volume, change_pct,
                    rsi, macd_hist, atr, adx, trend_signal, mtf_bullish,
                    sentiment_score, is_stale, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    price = excluded.price,
                    bid = excluded.bid,
                    ask = excluded.ask,
                    spread_pct = excluded.spread_pct,
                    volume = excluded.volume,
                    change_pct = excluded.change_pct,
                    rsi = excluded.rsi,
                    macd_hist = excluded.macd_hist,
                    atr = excluded.atr,
                    adx = excluded.adx,
                    trend_signal = excluded.trend_signal,
                    mtf_bullish = excluded.mtf_bullish,
                    sentiment_score = excluded.sentiment_score,
                    is_stale = excluded.is_stale,
                    updated_at = excluded.updated_at
                """,
                batch,
            )

    def get_market_snapshots(self) -> list[dict[str, Any]]:
        with self.db.readonly() as conn:
            rows = conn.execute(
                "SELECT * FROM market_snapshots ORDER BY ticker ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def get_snapshot_map(self) -> dict[str, dict[str, Any]]:
        items = self.get_market_snapshots()
        return {str(it["ticker"]): it for it in items}

    def save_news_items(self, items: list[dict[str, Any]]) -> int:
        if not items:
            return 0
        now_iso = get_brt_now().isoformat()
        inserted = 0
        with self.db.transaction() as conn:
            for it in items:
                title = str(it.get("title", "") or "").strip()[:300]
                url = str(it.get("url", "") or "").strip()[:500]
                if not title or not url:
                    continue
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO news_items (
                        title, summary, source, url, ticker, sentiment, published_at, scraped_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        str(it.get("summary", "") or "")[:600],
                        str(it.get("source", "B3 RSS") or "B3 RSS")[:80],
                        url,
                        normalize_b3_ticker(str(it.get("ticker", "") or "")),
                        float(it.get("sentiment", 0.0) or 0.0),
                        str(it.get("published_at") or now_iso),
                        now_iso,
                    ),
                )
                inserted += int(cur.rowcount or 0)
        return inserted

    def get_recent_news(self, limit: int = 30, ticker: str | None = None) -> list[dict[str, Any]]:
        capped = max(1, min(int(limit), 200))
        with self.db.readonly() as conn:
            if ticker:
                clean_tk = normalize_b3_ticker(ticker)
                rows = conn.execute(
                    """
                    SELECT * FROM news_items
                    WHERE ticker = ? OR ticker = ''
                    ORDER BY published_at DESC, id DESC
                    LIMIT ?
                    """,
                    (clean_tk, capped),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM news_items ORDER BY published_at DESC, id DESC LIMIT ?",
                    (capped,),
                ).fetchall()
        return [dict(r) for r in rows]

    def upsert_sentiment_score(
        self,
        ticker: str,
        score: float,
        label: str,
        confidence: float,
        news_count: int,
        top_headline: str,
        reasoning: str,
    ) -> None:
        clean_tk = normalize_b3_ticker(ticker) or "MARKET"
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sentiment_scores (
                    ticker, score, label, confidence, news_count, top_headline, reasoning, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    score = excluded.score,
                    label = excluded.label,
                    confidence = excluded.confidence,
                    news_count = excluded.news_count,
                    top_headline = excluded.top_headline,
                    reasoning = excluded.reasoning,
                    updated_at = excluded.updated_at
                """,
                (
                    clean_tk,
                    round(float(score), 4),
                    str(label)[:32],
                    round(float(confidence), 4),
                    int(news_count),
                    str(top_headline)[:280],
                    str(reasoning)[:400],
                    now_iso,
                ),
            )

    def get_cached_sentiment(self, ticker: str, max_age_seconds: int) -> dict[str, Any] | None:
        clean_tk = normalize_b3_ticker(ticker) or "MARKET"
        with self.db.readonly() as conn:
            row = conn.execute(
                "SELECT * FROM sentiment_scores WHERE ticker = ?",
                (clean_tk,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        try:
            dt = datetime.fromisoformat(str(data["updated_at"]))
            dt = dt.replace(tzinfo=BRT) if dt.tzinfo is None else dt.astimezone(BRT)
            if (get_brt_now() - dt).total_seconds() > max_age_seconds:
                return None
        except Exception:
            return None
        return data

    def get_all_sentiment_scores(self) -> dict[str, dict[str, Any]]:
        with self.db.readonly() as conn:
            rows = conn.execute("SELECT * FROM sentiment_scores").fetchall()
        return {str(r["ticker"]): dict(r) for r in rows}

    def record_health(self, cpu_percent: float, ram_percent: float, disk_percent: float) -> None:
        now_iso = get_brt_now().isoformat()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO system_health (cpu_percent, ram_percent, disk_percent, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (float(cpu_percent), float(ram_percent), float(disk_percent), now_iso),
            )

    def get_latest_health(self) -> dict[str, Any] | None:
        with self.db.readonly() as conn:
            row = conn.execute(
                "SELECT * FROM system_health ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row is not None else None

    def prune_old_records(self, retention_days: int = 14) -> None:
        cutoff = (get_brt_now() - timedelta(days=max(3, int(retention_days)))).isoformat()
        health_cutoff = (get_brt_now() - timedelta(days=3)).isoformat()
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM news_items WHERE scraped_at < ?", (cutoff,))
            conn.execute("DELETE FROM ai_decisions WHERE timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM system_health WHERE timestamp < ?", (health_cutoff,))
