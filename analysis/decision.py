"""
Motor de decisão autônomo do Projeto Córtex.

Combina análise técnica quantitativa e análise de sentimento para gerar
decisões de trading com raciocínio explicativo ('Pensamento do Córtex')
em português, respeitando lotes da B3, limites de risco e proteção contra
cotações defasadas de abertura.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Protocol, runtime_checkable

import pandas as pd

from analysis.sentiment import NewsItem, SentimentAnalyzer, SentimentResult
from analysis.technical import TechnicalAnalyzer, TechnicalResult, TrendSignal
from config.settings import settings
from data.news_scraper import TICKER_ALIASES, _compile_alias_pattern, text_mentions_ticker
from models.data_models import Action, Decision, Position
from utils.helpers import (
    BRT,
    clamp,
    format_b3_ticker_for_quantity,
    format_brl,
    format_percent,
    normalize_b3_ticker,
)
from utils.logger import get_logger

logger = get_logger("analysis.decision")


@runtime_checkable
class RiskManagerProtocol(Protocol):
    """Protocolo para o gerenciador de risco."""

    def get_max_shares(self, price: float, available_capital: float, **kwargs: Any) -> int: ...

    def calculate_stop_loss(self, entry_price: float, atr: float | None = None) -> float: ...


@runtime_checkable
class MarketDataProtocol(Protocol):
    """Protocolo para o provedor de dados de mercado."""

    def get_current_price(self, ticker: str) -> dict[str, Any]: ...

    def get_ohlcv(self, ticker: str, period: str, interval: str) -> Any: ...


@runtime_checkable
class PortfolioProtocol(Protocol):
    """Protocolo completo para o gerenciador de portfólio."""

    @property
    def free_cash(self) -> float: ...

    def get_position(self, ticker: str) -> Optional[Position]: ...

    def get_all_positions(self) -> list[Position]: ...

    def get_summary(self) -> Any: ...


@runtime_checkable
class DatabaseProtocol(Protocol):
    """Protocolo para a camada de persistência."""

    def insert_decision(self, **kwargs: Any) -> Any: ...


class DecisionEngine:
    """
    Motor de decisão autônomo do Córtex.

    Combina análise técnica (EMAs, RSI, MACD, BB, ATR, S/R) e sentimento
    corporativo para decidir entre compra, venda, manutenção ou stop-loss
    emergencial.
    """

    DEFAULT_OHLCV_PERIOD: str = "3mo"
    DEFAULT_OHLCV_INTERVAL: str = "1d"
    TICKER_ALIASES: dict[str, list[str]] = TICKER_ALIASES

    def __init__(
        self,
        technical: TechnicalAnalyzer,
        sentiment: SentimentAnalyzer,
        risk_manager: RiskManagerProtocol,
        market_data: MarketDataProtocol,
        portfolio: PortfolioProtocol,
        db: DatabaseProtocol,
    ) -> None:
        self.technical = technical
        self.sentiment = sentiment
        self.risk_manager = risk_manager
        self.market_data = market_data
        self.portfolio = portfolio
        self.db = db

        logger.info("DecisionEngine inicializado com todos os componentes.")

    def _is_market_quote_stale(
        self, ticker: str, price_data: Optional[dict[str, Any]]
    ) -> bool:
        """
        Verifica se a cotação pertence ao pregão anterior usando comparação booleana
        estrita (`is True`) para preservar compatibilidade com mocks de testes.
        """
        if isinstance(price_data, dict) and price_data.get("is_stale") is True:
            return True

        stale_fn = getattr(self.market_data, "is_quote_stale", None)
        if callable(stale_fn):
            try:
                if stale_fn(ticker) is True:
                    return True
            except Exception:
                return False
        return False

    @staticmethod
    def _is_real_number(val: Any) -> bool:
        return isinstance(val, (int, float)) and not isinstance(val, bool)

    @classmethod
    def _resolve_operational_portfolio_value(
        cls,
        portfolio: Any,
        summary: Any,
        available_capital: float,
    ) -> float:
        """
        Determina a base patrimonial estritamente operacional para cálculo dos limites de
        concentração (25% por ativo / 40% por setor), excluindo sempre o Cofre Reserva
        blindado (`vault_reserve_cash`) para nunca superdimensionar ordens em Produção.
        """
        summary_total = getattr(summary, "total_value", None)
        base_total = (
            float(summary_total)
            if cls._is_real_number(summary_total) and float(summary_total) > 0
            else float(available_capital)
        )

        vault_cash = getattr(portfolio, "vault_reserve_cash", None)
        op_val = getattr(portfolio, "operational_value", None)
        ai_cap = getattr(portfolio, "ai_allocated_capital", None)
        pos_mkt_val = getattr(portfolio, "positions_market_value", None)

        if cls._is_real_number(vault_cash) and float(vault_cash) > 0:
            if cls._is_real_number(op_val) and float(op_val) > 0:
                base_total = float(op_val)
            else:
                base_total = max(0.0, base_total - float(vault_cash))

        if cls._is_real_number(ai_cap) and float(ai_cap) > 0:
            pos_val_f = float(pos_mkt_val) if cls._is_real_number(pos_mkt_val) else 0.0
            effective_cap = max(float(ai_cap), pos_val_f)
            if base_total > effective_cap + 1e-6 and (
                (cls._is_real_number(vault_cash) and float(vault_cash) > 0)
                or float(ai_cap) < base_total * 0.99
            ):
                base_total = effective_cap

        return max(0.0, base_total)

    def evaluate(
        self,
        ticker: str,
        news_items: list[NewsItem],
        ohlcv_df: Any = None,
        scheduler: Any = None,
        market_regime: Optional[dict[str, Any]] = None,
    ) -> Decision:
        """
        Avalia um ativo e gera decisão de trading completa.
        """
        base_ticker = normalize_b3_ticker(ticker) or ticker.strip().upper()
        logger.debug("Avaliando %s com %d notícias", base_ticker, len(news_items or []))
        now = datetime.now(BRT)

        current_price: Optional[float] = None
        price_data: Optional[dict[str, Any]] = None
        try:
            raw_price_data = self.market_data.get_current_price(base_ticker)
            if isinstance(raw_price_data, dict):
                price_data = raw_price_data
                raw_last = price_data.get("last")
                current_price = float(raw_last) if raw_last is not None else None
        except Exception as exc:
            logger.debug("Preço direto indisponível para %s: %s", base_ticker, exc)

        if self._is_market_quote_stale(base_ticker, price_data):
            stale_px = float(current_price) if (current_price and current_price > 0) else 0.0
            logger.warning(
                "Cotação defasada do pregão anterior para %s (%s) — mantendo AGUARDAR.",
                base_ticker, format_brl(stale_px),
            )
            return Decision(
                ticker=base_ticker,
                action=Action.HOLD,
                confidence=0.0,
                reasoning=(
                    f"Cotação de {base_ticker} ({format_brl(stale_px)}) ainda reflete o fechamento "
                    f"do pregão anterior sem negócios intraday hoje. Aguardando abertura efetiva."
                ),
                technical_score=0.0,
                sentiment_score=0.0,
                quantity=0,
                price=stale_px,
                stop_loss=0.0,
                timestamp=now,
            )

        df = (
            ohlcv_df
            if ohlcv_df is not None
            else self.market_data.get_ohlcv(
                ticker=base_ticker,
                period=self.DEFAULT_OHLCV_PERIOD,
                interval=self.DEFAULT_OHLCV_INTERVAL,
            )
        )

        has_data = False
        if isinstance(df, pd.DataFrame):
            has_data = not df.empty
            if (current_price is None or current_price <= 0) and has_data:
                try:
                    df_cols = df
                    if isinstance(df_cols.columns, pd.MultiIndex):
                        df_cols = df_cols.copy()
                        df_cols.columns = df_cols.columns.get_level_values(0)
                    close_col = "Close" if "Close" in df_cols.columns else "close"
                    current_price = float(df_cols.iloc[-1][close_col])
                    price_data = {
                        "last": current_price,
                        "bid": current_price,
                        "ask": current_price,
                        "ticker": base_ticker,
                    }
                except Exception:
                    pass
        elif isinstance(df, (list, tuple)):
            has_data = len(df) > 0
            if (current_price is None or current_price <= 0) and has_data:
                try:
                    last_item = df[-1]
                    current_price = float(
                        getattr(last_item, "close", getattr(last_item, "Close", 0.0))
                    )
                    price_data = {
                        "last": current_price,
                        "bid": current_price,
                        "ask": current_price,
                        "ticker": base_ticker,
                    }
                except Exception:
                    pass

        if current_price is None or current_price <= 0 or not has_data:
            logger.debug("Dados de mercado indisponíveis para %s — mantendo HOLD", base_ticker)
            return Decision(
                ticker=base_ticker,
                action=Action.HOLD,
                confidence=0.0,
                reasoning=f"Dados de mercado temporariamente indisponíveis para {base_ticker}.",
                technical_score=0.0,
                sentiment_score=0.0,
                quantity=0,
                price=0.0,
                stop_loss=0.0,
                timestamp=now,
            )

        try:
            tech_result = self.technical.analyze(base_ticker, df)
        except ValueError as exc:
            logger.warning("Análise técnica falhou para %s: %s", base_ticker, exc)
            tech_result = TechnicalResult(
                signal=TrendSignal.NEUTRAL,
                ema_9=0.0,
                ema_21=0.0,
                ema_50=0.0,
                rsi=50.0,
                support=current_price * 0.95,
                resistance=current_price * 1.05,
                macd_hist=0.0,
                atr=0.0,
                rel_vol=1.0,
                bb_lower=0.0,
                bb_upper=0.0,
                confidence=0.0,
                reasoning=f"Análise técnica indisponível: {exc}",
            )

        position = self.portfolio.get_position(base_ticker)
        is_holding = position is not None

        existing_stop: float | None = None
        existing_qty: int = 0

        if is_holding and position is not None:
            raw_stop = getattr(position, "stop_loss", None)
            existing_stop = float(raw_stop) if raw_stop is not None else None
            existing_qty = int(getattr(position, "quantity", 0) or 0)

        ticker_news = self._filter_news_for_ticker(base_ticker, news_items or [])

        allow_gemini = is_holding or (tech_result.signal != TrendSignal.NEUTRAL)
        sent_result = self.sentiment.get_sentiment_for_ticker(
            base_ticker, ticker_news, allow_gemini=allow_gemini
        )

        # Verificação prioritária de Stop-Loss em posição aberta
        if is_holding and existing_stop is not None and existing_stop > 0.0:
            if current_price <= existing_stop:
                reasoning = self._generate_thinking(
                    base_ticker,
                    tech_result,
                    sent_result,
                    Action.EMERGENCY_SELL,
                    position,
                    current_price=current_price,
                    stop_loss=existing_stop,
                    quantity=existing_qty,
                )
                decision = Decision(
                    ticker=base_ticker,
                    action=Action.EMERGENCY_SELL,
                    confidence=1.0,
                    reasoning=reasoning,
                    technical_score=tech_result.confidence,
                    sentiment_score=sent_result.score,
                    quantity=existing_qty,
                    price=current_price,
                    stop_loss=existing_stop,
                    timestamp=now,
                )
                logger.warning(
                    "STOP-LOSS ATIVADO para %s: preço %s <= SL %s",
                    base_ticker,
                    format_brl(current_price),
                    format_brl(existing_stop),
                )
                self._persist_decision(decision, trend_signal=tech_result.signal.value)
                return decision

        confidence = self._blend_confidence(tech_result, sent_result)

        action: Action = Action.HOLD
        stop_loss: float = 0.0
        target_quantity: int = 0
        available_capital: Optional[float] = None
        block_reason_note: str = ""

        if not is_holding:
            is_bullish_tech = tech_result.signal in (TrendSignal.STRONG_BUY, TrendSignal.BUY)
            sentiment_not_hostile = sent_result.score >= -0.15

            if is_bullish_tech and sentiment_not_hostile:
                # 1. Guarda de Volatilidade de Abertura (10:00–10:15 BRT por padrão)
                cooldown_min = int(getattr(settings, "OPENING_COOLDOWN_MINUTES", 0) or 0)
                in_opening_cooldown = False
                if cooldown_min > 0:
                    sched_obj = scheduler
                    if sched_obj is None:
                        from core.scheduler import MarketScheduler
                        sched_obj = MarketScheduler()
                    in_opening_cooldown = bool(
                        sched_obj.is_opening_stabilization(dt=now, minutes=cooldown_min) is True
                    )

                # 2. Filtro de Regime Sistêmico da B3 (queda aguda de BOVA11)
                resolved_regime = market_regime
                if resolved_regime is None:
                    regime_fn = getattr(self.market_data, "get_market_regime", None)
                    if callable(regime_fn):
                        try:
                            cand_reg = regime_fn("BOVA11")
                            if isinstance(cand_reg, dict):
                                resolved_regime = cand_reg
                        except Exception:
                            resolved_regime = None

                is_bearish_regime = bool(
                    isinstance(resolved_regime, dict)
                    and resolved_regime.get("is_bearish_crash") is True
                )

                # 3. Confirmação Multi-Timeframe (Diário 1d + Intraday 60m)
                mtf_blocked = False
                mtf_detail = ""
                if (
                    not in_opening_cooldown
                    and not is_bearish_regime
                    and bool(getattr(settings, "ENABLE_MTF_CONFIRMATION", False))
                ):
                    try:
                        df_60m = self.market_data.get_ohlcv(
                            ticker=base_ticker, period="1mo", interval="60m"
                        )
                        if isinstance(df_60m, pd.DataFrame) and len(df_60m) >= 35:
                            h1_tech = self.technical.analyze(base_ticker, df_60m)
                            if h1_tech.signal in (TrendSignal.SELL, TrendSignal.STRONG_SELL):
                                mtf_blocked = True
                                mtf_detail = (
                                    f"gráfico intradiário de 60m em reversão baixista ({h1_tech.signal.value})"
                                )
                            elif self._is_real_number(h1_tech.rsi) and float(h1_tech.rsi) >= 76.0:
                                mtf_blocked = True
                                mtf_detail = (
                                    f"gráfico intradiário de 60m sobrecomprado (RSI={float(h1_tech.rsi):.1f})"
                                )
                    except Exception as mtf_err:
                        logger.debug("Confirmação MTF 60m indisponível para %s: %s", base_ticker, mtf_err)

                if in_opening_cooldown:
                    action = Action.HOLD
                    block_reason_note = (
                        f"Compra adiada pela Guarda de Abertura da B3 (primeiros {cooldown_min} min de pregão) "
                        f"para evitar spreads e distorções do leilão inicial."
                    )
                elif is_bearish_regime:
                    bova_chg = float((resolved_regime or {}).get("change_pct", 0.0) or 0.0)
                    action = Action.HOLD
                    block_reason_note = (
                        f"Compra bloqueada pelo Filtro de Regime de Mercado (BOVA11 em queda intradiária de "
                        f"{bova_chg:+.2f}%, abaixo do limite de segurança)."
                    )
                elif mtf_blocked:
                    action = Action.HOLD
                    block_reason_note = (
                        f"Compra bloqueada pela Confirmação Multi-Timeframe: tendência diária favorável, "
                        f"porém {mtf_detail}."
                    )
                else:
                    bp = getattr(self.portfolio, "buying_power", None)
                    if self._is_real_number(bp):
                        available_capital = float(bp)
                    else:
                        available_capital = float(getattr(self.portfolio, "free_cash", 0.0) or 0.0)

                    summary = self.portfolio.get_summary()
                    portfolio_total = self._resolve_operational_portfolio_value(
                        self.portfolio, summary, available_capital
                    )

                    target_quantity = int(
                        self.risk_manager.get_max_shares(
                            current_price,
                            available_capital,
                            confidence=confidence,
                            total_portfolio_value=portfolio_total,
                            positions=self.portfolio.get_all_positions(),
                            ticker=base_ticker,
                        )
                        or 0
                    )

                    if target_quantity > 0:
                        action = Action.BUY
                        stop_loss = float(
                            self.risk_manager.calculate_stop_loss(current_price, atr=tech_result.atr)
                            or 0.0
                        )
                    else:
                        action = Action.HOLD
                        target_quantity = 0
                        stop_loss = 0.0
                        logger.info(
                            "%s: Setup de compra detectado mas ordem bloqueada (disponível: %s, base op: %s, preço: %s)",
                            base_ticker,
                            format_brl(available_capital),
                            format_brl(portfolio_total),
                            format_brl(current_price),
                        )
            else:
                action = Action.HOLD
                target_quantity = 0
                stop_loss = 0.0
        else:
            if (
                tech_result.signal in (TrendSignal.STRONG_SELL, TrendSignal.SELL)
                or sent_result.score < -0.3
            ):
                # Escudo Tributário PF Anti-Day-Trade (preserva isenção Swing-Trade de R$ 20 mil/mês)
                same_day_profit_hold = False
                if bool(getattr(settings, "AVOID_DAY_TRADE_TAX", False)) and position is not None:
                    entry_dt = getattr(position, "entry_time", None) or getattr(position, "timestamp", None)
                    entry_px = float(getattr(position, "entry_price", 0.0) or 0.0)
                    if isinstance(entry_dt, datetime) and entry_px > 0 and current_price > entry_px:
                        opened_date = entry_dt.astimezone(BRT).date() if entry_dt.tzinfo else entry_dt.date()
                        if opened_date == now.date():
                            same_day_profit_hold = True

                if same_day_profit_hold:
                    action = Action.HOLD
                    target_quantity = existing_qty
                    stop_loss = float(existing_stop or 0.0)
                    block_reason_note = (
                        "Proteção Tributária PF (Anti-Day-Trade): posição aberta hoje com lucro; "
                        "venda discricionária retida até D+1 para preservar a isenção mensal de "
                        "R$ 20.000,00 em Swing-Trade (Stop-Loss permanece 100% ativo)."
                    )
                    logger.info(
                        "🛡️ Anti-Day-Trade ativo para %s: mantendo lucro aberto hoje até D+1.",
                        base_ticker,
                    )
                else:
                    action = Action.SELL
                    target_quantity = existing_qty
                    stop_loss = float(existing_stop or 0.0)
            else:
                action = Action.HOLD
                target_quantity = existing_qty
                stop_loss = float(existing_stop or 0.0)

        reasoning = self._generate_thinking(
            base_ticker,
            tech_result,
            sent_result,
            action,
            position,
            current_price=current_price,
            stop_loss=stop_loss,
            quantity=target_quantity,
            available_capital=available_capital,
            block_reason_note=block_reason_note,
        )

        decision = Decision(
            ticker=base_ticker,
            action=action,
            confidence=round(confidence, 4),
            reasoning=reasoning,
            technical_score=tech_result.confidence,
            sentiment_score=sent_result.score,
            quantity=target_quantity,
            price=current_price,
            stop_loss=stop_loss if (action != Action.HOLD or is_holding) else 0.0,
            timestamp=now,
        )

        logger.info(
            "Decisão %s: %s (confiança=%.2f, técnico=%s, sentimento=%.2f)",
            base_ticker,
            action.value,
            confidence,
            tech_result.signal.value,
            sent_result.score,
        )

        self._persist_decision(decision, trend_signal=tech_result.signal.value)
        return decision

    def evaluate_all(
        self,
        watchlist: list[str],
        news_items: list[NewsItem],
    ) -> list[Decision]:
        """
        Avalia todos os ativos da watchlist priorizando posições abertas (stop-loss/saídas).
        """
        logger.info("Avaliando watchlist com %d ativos", len(watchlist))
        decisions: list[Decision] = []

        held_tickers: list[str] = []
        free_tickers: list[str] = []

        for ticker in watchlist:
            base = normalize_b3_ticker(ticker) or ticker
            if self.portfolio.get_position(base) is not None:
                held_tickers.append(base)
            else:
                free_tickers.append(base)

        for ticker in held_tickers:
            ticker_news = self._filter_news_for_ticker(ticker, news_items)
            decisions.append(self.evaluate(ticker, ticker_news))

        for ticker in free_tickers:
            ticker_news = self._filter_news_for_ticker(ticker, news_items)
            decisions.append(self.evaluate(ticker, ticker_news))

        actionable = [d for d in decisions if d.action != Action.HOLD]
        logger.info(
            "Avaliação concluída: %d decisões totais, %d acionáveis",
            len(decisions),
            len(actionable),
        )
        return decisions

    def _generate_thinking(
        self,
        ticker: str,
        tech: TechnicalResult,
        sent: SentimentResult,
        action: Action,
        position: Optional[Position] = None,
        current_price: float = 0.0,
        stop_loss: float | None = None,
        quantity: int = 0,
        available_capital: Optional[float] = None,
        block_reason_note: str = "",
    ) -> str:
        """
        Gera o texto explicativo completo do 'Pensamento do Córtex' em português,
        formatando corretamente lotes da B3, moeda BRL e causas reais da decisão.
        """
        parts: list[str] = []
        base_ticker = normalize_b3_ticker(ticker) or ticker.strip().upper()
        order_symbol = (
            format_b3_ticker_for_quantity(base_ticker, quantity)
            if quantity > 0
            else base_ticker
        )

        if tech.reasoning:
            parts.append(tech.reasoning)

        score_fmt = f"{sent.score:+.2f}".replace(".", ",")
        if (
            sent.news_count > 0
            and sent.top_headline
            and sent.top_headline not in ("Sem notícias disponíveis", "Sem notícias específicas", "N/A")
        ):
            headline_trunc = (
                sent.top_headline[:75] + "..."
                if len(sent.top_headline) > 75
                else sent.top_headline
            )
            sentiment_desc = (
                f"Sentimento corporativo: {score_fmt} ({sent.label.lower()}) — "
                f"notícia: '{headline_trunc}'"
            )
        else:
            sentiment_desc = (
                f"Sentimento corporativo: {score_fmt} (neutro - sem notícias específicas recentes)"
            )
        parts.append(f"{sentiment_desc}.")

        if action == Action.EMERGENCY_SELL:
            entry_price = float(getattr(position, "entry_price", 0.0) or 0.0) if position else 0.0
            pos_stop = float(getattr(position, "stop_loss", 0.0) or 0.0) if position else float(stop_loss or 0.0)
            qty = int(getattr(position, "quantity", 0) or 0) if position else quantity
            sym = format_b3_ticker_for_quantity(base_ticker, qty if qty > 0 else 1)
            parts.append(
                f"STOP-LOSS ATIVADO! Preço atual ({format_brl(current_price)}) atingiu ou "
                f"ultrapassou o stop-loss em {format_brl(pos_stop)}. "
                f"Posição de {qty} unidades (entrada em {format_brl(entry_price)}) "
                f"liquidada para proteção de capital. "
                f"→ DECISÃO: VENDA EMERGENCIAL de {qty} {sym}."
            )

        elif action == Action.BUY:
            if sent.score >= 0.20:
                parts.append(
                    "Convergência confirmada: Setup técnico altista com sentimento corporativo favorável."
                )
            else:
                parts.append(
                    "Setup técnico altista confirmado com sentimento corporativo estável (sem notícias adversas)."
                )

            stop_val = (
                float(stop_loss)
                if (stop_loss and stop_loss > 0)
                else float(self.risk_manager.calculate_stop_loss(current_price, atr=tech.atr) or 0.0)
            )
            if current_price > 0 and stop_val > 0:
                dist_ratio = (current_price - stop_val) / current_price
                parts.append(
                    f"Stop-loss configurado em {format_brl(stop_val)} ({format_percent(dist_ratio)} abaixo da entrada)."
                )
            if quantity > 0:
                parts.append(
                    f"Ordem dimensionada para {quantity} unidades ({format_brl(quantity * current_price)})."
                )
            parts.append(f"→ DECISÃO: COMPRAR {quantity} {order_symbol}.")

        elif action == Action.SELL:
            is_bearish_tech = tech.signal in (TrendSignal.STRONG_SELL, TrendSignal.SELL)
            is_hostile_sent = sent.score < -0.3

            if is_bearish_tech and is_hostile_sent:
                parts.append(
                    f"Convergência baixista: reversão técnica ({tech.signal.value}) somada a "
                    f"deterioração severa no sentimento corporativo ({score_fmt})."
                )
            elif is_hostile_sent:
                parts.append(
                    f"Saída defensiva acionada por sentimento corporativo hostil ({score_fmt} < -0,30) "
                    f"diante de noticiário adverso, protegendo o capital antes de deterioração técnica maior."
                )
            else:
                parts.append(
                    "Reversão técnica baixista identificada. Indicadores apontam perda de momentum "
                    "com médias móveis inclinadas para baixo."
                )

            qty = int(getattr(position, "quantity", 0) or 0) if position else quantity
            sym = format_b3_ticker_for_quantity(base_ticker, qty if qty > 0 else 1)
            parts.append(f"→ DECISÃO: VENDER {qty} {sym}.")

        elif action == Action.HOLD:
            if position is not None:
                qty = int(getattr(position, "quantity", 0) or 0)
                entry = float(getattr(position, "entry_price", 0.0) or 0.0)
                pos_stop = float(getattr(position, "stop_loss", 0.0) or 0.0)
                sym = format_b3_ticker_for_quantity(base_ticker, qty if qty > 0 else 1)
                if block_reason_note:
                    parts.append(block_reason_note)
                else:
                    parts.append(
                        f"Posição existente mantida ({qty} unidades com entrada em {format_brl(entry)}). "
                        f"Sem gatilho técnico ou fundamentalista de saída. "
                        f"Monitorando stop-loss em {format_brl(pos_stop)}."
                    )
                parts.append(f"→ DECISÃO: MANTER posição em {sym}.")
            else:
                if block_reason_note:
                    parts.append(block_reason_note)
                elif tech.signal in (TrendSignal.STRONG_BUY, TrendSignal.BUY):
                    if sent.score < -0.15:
                        parts.append(
                            f"Sinal técnico favorável, porém sentimento corporativo desfavorável "
                            f"(score: {score_fmt} < -0,15). Entrada bloqueada por precaução de risco."
                        )
                    else:
                        cash_val = (
                            available_capital
                            if available_capital is not None
                            else float(getattr(self.portfolio, "free_cash", 0.0) or 0.0)
                        )
                        if current_price > 0 and cash_val < current_price:
                            parts.append(
                                f"Capital insuficiente para compra de 1 unidade "
                                f"(disponível: {format_brl(cash_val)}, cotação: {format_brl(current_price)})."
                            )
                        else:
                            parts.append(
                                f"Setup de compra identificado, mas bloqueado pelos limites de "
                                f"risco/concentração da carteira (caixa disponível: {format_brl(cash_val)})."
                            )
                elif tech.signal in (TrendSignal.STRONG_SELL, TrendSignal.SELL):
                    parts.append("Sinal técnico baixista. Sem posição em carteira para vender.")
                else:
                    parts.append("Sinais técnicos neutros/inconclusivos. Sem condições para entrada.")
                parts.append(f"→ DECISÃO: AGUARDAR para {base_ticker}.")

        return " ".join(parts)

    def _blend_confidence(self, tech: TechnicalResult, sent: SentimentResult) -> float:
        """Combina a matriz de convergência com as confianças técnica e de sentimento."""
        matrix_conf = self._calculate_confidence(tech.signal, sent.score)
        if tech.confidence <= 0.0:
            return matrix_conf
        sent_conf = sent.confidence if sent.news_count > 0 else matrix_conf
        blended = (0.65 * matrix_conf) + (0.25 * tech.confidence) + (0.10 * sent_conf)
        return clamp(blended, 0.0, 1.0)

    @staticmethod
    def _calculate_confidence(trend_signal: TrendSignal, sentiment_score: float) -> float:
        """Calcula confiança-base pela matriz de convergência técnica + sentimento."""
        if trend_signal == TrendSignal.STRONG_BUY:
            if sentiment_score >= 0.3:
                return 0.95
            if sentiment_score >= 0.0:
                return 0.85
            if sentiment_score >= -0.15:
                return 0.75
            return 0.40

        if trend_signal == TrendSignal.BUY:
            if sentiment_score >= 0.3:
                return 0.80
            if sentiment_score >= 0.0:
                return 0.70
            if sentiment_score >= -0.15:
                return 0.60
            return 0.35

        if trend_signal == TrendSignal.STRONG_SELL:
            if sentiment_score <= -0.3:
                return 0.95
            if sentiment_score <= 0.0:
                return 0.85
            if sentiment_score <= 0.15:
                return 0.75
            return 0.40

        if trend_signal == TrendSignal.SELL:
            if sentiment_score <= -0.3:
                return 0.80
            if sentiment_score <= 0.0:
                return 0.70
            if sentiment_score <= 0.15:
                return 0.60
            return 0.35

        if trend_signal == TrendSignal.NEUTRAL:
            return 0.30

        return 0.35

    @classmethod
    def _filter_news_for_ticker(
        cls, ticker: str, news_items: list[NewsItem]
    ) -> list[NewsItem]:
        """
        Filtra notícias relevantes para um ativo específico usando fronteira estrita
        de palavras (`\\b...\\b`) e o registro corporativo compartilhado `TICKER_ALIASES`.
        """
        if not news_items:
            return []

        base = normalize_b3_ticker(ticker)
        if not base:
            return []

        custom_aliases = cls.TICKER_ALIASES.get(base)
        extra_patterns = (
            [_compile_alias_pattern(a) for a in custom_aliases if a.strip()]
            if custom_aliases and cls.TICKER_ALIASES is not TICKER_ALIASES
            else []
        )

        filtered: list[NewsItem] = []
        for item in news_items:
            mentioned = getattr(item, "tickers_mentioned", None)
            if isinstance(mentioned, (list, tuple, set)) and base in mentioned:
                filtered.append(item)
                continue

            combined_text = f"{getattr(item, 'title', '')} {getattr(item, 'summary', '')}"
            if text_mentions_ticker(base, combined_text):
                filtered.append(item)
            elif extra_patterns and any(p.search(combined_text.upper()) for p in extra_patterns):
                filtered.append(item)

        return filtered

    def _persist_decision(
        self, decision: Decision, trend_signal: Optional[str] = None
    ) -> None:
        """Persiste a decisão no banco de dados com isolamento estrito de ambiente."""
        try:
            is_sim = bool(getattr(settings, "SIMULATION_MODE", True))
            env = "homologation" if is_sim else "production"
            self.db.insert_decision(
                ticker=decision.ticker,
                action=decision.action.value,
                confidence=decision.confidence,
                trend_signal=trend_signal,
                sentiment_score=decision.sentiment_score,
                reasoning=decision.reasoning,
                timestamp=decision.timestamp.isoformat(),
                environment=env,
                is_simulated=is_sim,
            )
        except Exception as exc:
            logger.error("Erro ao persistir decisão para %s: %s", decision.ticker, exc)
