from __future__ import annotations

import math

from config.settings import settings
from domain.b3_rules import clamp, normalize_b3_ticker
from domain.models import (
    DecisionCandidate,
    MarketCycleSnapshot,
    MarketQuote,
    SentimentVector,
    TechnicalVector,
    TrendSignal,
)

_SIGNAL_NUMERIC: dict[TrendSignal, float] = {
    TrendSignal.STRONG_BUY: 0.85,
    TrendSignal.BUY: 0.50,
    TrendSignal.NEUTRAL: 0.0,
    TrendSignal.SELL: -0.50,
    TrendSignal.STRONG_SELL: -0.85,
}


class DecisionEngine:
    def _compute_stop_loss(self, price: float, atr: float) -> float:
        if price <= 0.0 or not math.isfinite(price):
            return 0.01
        safe_atr = atr if (math.isfinite(atr) and atr > 0.0) else 0.0
        distance = max(safe_atr * 2.0, price * settings.STOP_LOSS_PERCENT)
        return max(0.01, round(price - distance, 2))

    def evaluate_ticker(
        self,
        quote: MarketQuote,
        tech: TechnicalVector,
        sent: SentimentVector,
        market_sent: SentimentVector | None = None,
        regime_bearish_veto: bool = False,
        opening_cooldown_active: bool = False,
        is_owned: bool = False,
    ) -> DecisionCandidate:
        norm_tk = normalize_b3_ticker(quote.ticker or tech.ticker)
        price = round(float(quote.price), 2) if math.isfinite(quote.price) else 0.0
        safe_atr = round(max(0.0, float(tech.atr)), 4) if math.isfinite(tech.atr) else 0.0
        suggested_stop = self._compute_stop_loss(price, safe_atr)
        trend_str = tech.signal.value if isinstance(tech.signal, TrendSignal) else str(tech.signal)
        sent_score = round(clamp(float(sent.score), -1.0, 1.0), 4)

        if price <= 0.0:
            return DecisionCandidate(
                ticker=norm_tk,
                action="HOLD",
                confidence=0.0,
                trend_signal=trend_str,
                sentiment_score=sent_score,
                price=0.0,
                suggested_stop_loss=0.01,
                atr=safe_atr,
                reasoning="Cotação indisponível ou zerada; operação bloqueada.",
            )

        sig_enum = (
            tech.signal
            if isinstance(tech.signal, TrendSignal)
            else TrendSignal(str(tech.signal))
            if str(tech.signal) in TrendSignal._value2member_map_
            else TrendSignal.NEUTRAL
        )
        tech_base = _SIGNAL_NUMERIC.get(sig_enum, 0.0)
        eff_tech = tech_base * max(0.55, clamp(float(tech.confidence), 0.0, 1.0))
        eff_mkt = clamp(float(market_sent.score), -1.0, 1.0) if market_sent is not None else 0.0
        composite = (eff_tech * 0.72) + (sent_score * 0.20) + (eff_mkt * 0.08)

        if quote.is_stale:
            return DecisionCandidate(
                ticker=norm_tk,
                action="HOLD",
                confidence=round(clamp(0.15 + abs(composite) * 0.20, 0.10, 0.38), 4),
                trend_signal=trend_str,
                sentiment_score=sent_score,
                price=price,
                suggested_stop_loss=suggested_stop,
                atr=safe_atr,
                reasoning=(
                    f"Cotação defasada (stale quote guard ativo para {norm_tk}): "
                    "novas compras bloqueadas por segurança operacional."
                ),
            )

        is_bearish = sig_enum in (TrendSignal.SELL, TrendSignal.STRONG_SELL) and composite <= -0.22
        if is_bearish or (is_owned and sig_enum == TrendSignal.STRONG_SELL):
            if is_owned:
                sell_conf = round(clamp(0.56 + abs(composite) * 0.42, 0.56, 0.97), 4)
                return DecisionCandidate(
                    ticker=norm_tk,
                    action="SELL",
                    confidence=sell_conf,
                    trend_signal=trend_str,
                    sentiment_score=sent_score,
                    price=price,
                    suggested_stop_loss=suggested_stop,
                    atr=safe_atr,
                    reasoning=(
                        f"Sinal vendedor para encerramento de custódia ({trend_str}, "
                        f"sentimento {sent_score:+.2f}): {tech.reasoning}"
                    )[:390],
                )
            bear_conf = round(clamp(0.50 + abs(composite) * 0.35, 0.50, 0.88), 4)
            return DecisionCandidate(
                ticker=norm_tk,
                action="SELL",
                confidence=bear_conf,
                trend_signal=trend_str,
                sentiment_score=sent_score,
                price=price,
                suggested_stop_loss=suggested_stop,
                atr=safe_atr,
                reasoning=(
                    f"Pressão vendedora confirmada ({trend_str}, sentimento {sent_score:+.2f}): "
                    f"{tech.reasoning}"
                )[:390],
            )

        buy_threshold = 0.34 if is_owned else 0.26
        wants_buy = sig_enum in (TrendSignal.STRONG_BUY, TrendSignal.BUY) and composite >= buy_threshold

        if wants_buy:
            if opening_cooldown_active:
                return DecisionCandidate(
                    ticker=norm_tk,
                    action="HOLD",
                    confidence=round(clamp(0.25 + composite * 0.20, 0.20, 0.45), 4),
                    trend_signal=trend_str,
                    sentiment_score=sent_score,
                    price=price,
                    suggested_stop_loss=suggested_stop,
                    atr=safe_atr,
                    reasoning=(
                        f"Compra vetada pelo cooldown de abertura da B3 ({settings.OPENING_COOLDOWN_MINUTES} min)."
                    ),
                )
            if regime_bearish_veto:
                return DecisionCandidate(
                    ticker=norm_tk,
                    action="HOLD",
                    confidence=round(clamp(0.22 + composite * 0.18, 0.18, 0.42), 4),
                    trend_signal=trend_str,
                    sentiment_score=sent_score,
                    price=price,
                    suggested_stop_loss=suggested_stop,
                    atr=safe_atr,
                    reasoning="Compra vetada pelo filtro de regime de mercado (queda acentuada do BOVA11).",
                )
            if settings.ENABLE_MTF_CONFIRMATION and not tech.mtf_bullish:
                return DecisionCandidate(
                    ticker=norm_tk,
                    action="HOLD",
                    confidence=round(clamp(0.24 + composite * 0.18, 0.20, 0.44), 4),
                    trend_signal=trend_str,
                    sentiment_score=sent_score,
                    price=price,
                    suggested_stop_loss=suggested_stop,
                    atr=safe_atr,
                    reasoning="Compra bloqueada: gráfico de 60 minutos (MTF) não confirma tendência altista.",
                )
            if quote.spread_pct > settings.MAX_SPREAD_PERCENT:
                return DecisionCandidate(
                    ticker=norm_tk,
                    action="HOLD",
                    confidence=0.25,
                    trend_signal=trend_str,
                    sentiment_score=sent_score,
                    price=price,
                    suggested_stop_loss=suggested_stop,
                    atr=safe_atr,
                    reasoning=(
                        f"Compra bloqueada: spread atual ({quote.spread_pct * 100:.2f}%) "
                        f"acima do limite ({settings.MAX_SPREAD_PERCENT * 100:.2f}%)."
                    ),
                )
            if sent_score <= -0.55 and sent.confidence >= 0.50:
                return DecisionCandidate(
                    ticker=norm_tk,
                    action="HOLD",
                    confidence=0.30,
                    trend_signal=trend_str,
                    sentiment_score=sent_score,
                    price=price,
                    suggested_stop_loss=suggested_stop,
                    atr=safe_atr,
                    reasoning=f"Compra vetada por sentimento noticioso severamente negativo ({sent_score:+.2f}).",
                )

            buy_conf = round(clamp(0.55 + composite * 0.44, 0.55, 0.98), 4)
            return DecisionCandidate(
                ticker=norm_tk,
                action="BUY",
                confidence=buy_conf,
                trend_signal=trend_str,
                sentiment_score=sent_score,
                price=price,
                suggested_stop_loss=suggested_stop,
                atr=safe_atr,
                reasoning=(
                    f"Confluência compradora ({trend_str}, sent {sent_score:+.2f}, "
                    f"stop sugerido R$ {suggested_stop:.2f}): {tech.reasoning}"
                )[:390],
            )

        hold_conf = round(clamp(0.20 + abs(composite) * 0.35, 0.15, 0.49), 4)
        return DecisionCandidate(
            ticker=norm_tk,
            action="HOLD",
            confidence=hold_conf,
            trend_signal=trend_str,
            sentiment_score=sent_score,
            price=price,
            suggested_stop_loss=suggested_stop,
            atr=safe_atr,
            reasoning=f"Aguardando gatilho direcional claro ({trend_str}): {tech.reasoning}"[:390],
        )

    def evaluate_universe(
        self,
        snapshot: MarketCycleSnapshot,
        owned_tickers: set[str] | None = None,
    ) -> list[DecisionCandidate]:
        owned_norm = {
            normalize_b3_ticker(tk)
            for tk in (owned_tickers or set())
            if normalize_b3_ticker(tk)
        }
        market_sent = snapshot.sentiments.get("MARKET")
        candidates: list[DecisionCandidate] = []

        for raw_tk, quote in snapshot.quotes.items():
            tk = normalize_b3_ticker(raw_tk)
            if not tk or tk == "MARKET":
                continue
            tech = snapshot.technicals.get(tk) or TechnicalVector(
                ticker=tk,
                signal=TrendSignal.NEUTRAL,
            )
            sent = snapshot.sentiments.get(tk) or SentimentVector(ticker=tk)
            cand = self.evaluate_ticker(
                quote=quote,
                tech=tech,
                sent=sent,
                market_sent=market_sent,
                regime_bearish_veto=snapshot.regime_bearish_veto,
                opening_cooldown_active=snapshot.opening_cooldown_active,
                is_owned=(tk in owned_norm),
            )
            candidates.append(cand)

        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return candidates
