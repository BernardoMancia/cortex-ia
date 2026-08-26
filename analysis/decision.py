"""
Motor de decisão autônomo do Projeto Córtex.

Combina análise técnica e sentimento para gerar decisões de trading
com raciocínio explicativo ('Pensamento do Córtex') em português.
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
import pandas as pd
from models.data_models import Action, Decision
from typing import Any, Optional, Protocol, runtime_checkable

from analysis.technical import TechnicalAnalyzer, TechnicalResult, TrendSignal
from analysis.sentiment import SentimentAnalyzer, SentimentResult, NewsItem
from config.settings import settings
from utils.logger import get_logger
from utils.helpers import format_brl, format_percent

logger = get_logger('analysis.decision')

# Timezone BRT (UTC-3)
BRT = timezone(timedelta(hours=-3))


# ─── Protocolos para dependências injetadas ─────────────────────────────────


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
    """Protocolo para o gerenciador de portfólio."""

    @property
    def free_cash(self) -> float: ...

    def get_position(self, ticker: str) -> Optional[Any]: ...

    def get_all_positions(self) -> list[Any]: ...


@runtime_checkable
class DatabaseProtocol(Protocol):
    """Protocolo para a camada de persistência."""

    def insert_decision(self, **kwargs) -> None: ...


# ─── Motor de Decisão Autônomo ───────────────────────────────────────────────


class DecisionEngine:
    """
    Motor de decisão autônomo do Córtex.

    Combina análise técnica (EMAs, RSI, S/R) e sentimento de
    notícias para gerar decisões de compra, venda, manutenção
    ou venda emergencial (stop-loss).

    Pipeline de avaliação:
        1. Obter preço atual e histórico OHLCV
        2. Executar análise técnica
        3. Executar análise de sentimento
        4. Verificar posição existente
        5. Se posicionado: verificar stop-loss → EMERGENCY_SELL
        6. Se não posicionado: avaliar convergência para BUY
        7. Gerar 'Pensamento do Córtex'
        8. Retornar Decision
    """

    # Configurações padrão de mercado
    DEFAULT_OHLCV_PERIOD: str = '3mo'
    DEFAULT_OHLCV_INTERVAL: str = '1d'

    def __init__(
        self,
        technical: TechnicalAnalyzer,
        sentiment: SentimentAnalyzer,
        risk_manager: RiskManagerProtocol,
        market_data: MarketDataProtocol,
        portfolio: PortfolioProtocol,
        db: DatabaseProtocol,
    ) -> None:
        """
        Inicializa o motor de decisão.

        Args:
            technical: Instância do analisador técnico.
            sentiment: Instância do analisador de sentimento.
            risk_manager: Gerenciador de risco para cálculo de
                          stop-loss e quantidade de ações.
            market_data: Provedor de dados de mercado (preços, OHLCV).
            portfolio: Gerenciador de portfólio (posições, capital).
            db: Camada de persistência para salvar decisões.
        """
        self.technical = technical
        self.sentiment = sentiment
        self.risk_manager = risk_manager
        self.market_data = market_data
        self.portfolio = portfolio
        self.db = db

        logger.info('DecisionEngine inicializado com todos os componentes.')

    def evaluate(self, ticker: str, news_items: list[NewsItem]) -> Decision:
        """
        Avalia um ativo e gera decisão de trading.

        Executa o pipeline completo: dados de mercado → análise técnica →
        análise de sentimento → verificação de posição → decisão final.

        Args:
            ticker: Código do ativo (ex: 'PETR4').
            news_items: Lista de notícias relevantes ao ativo.

        Returns:
            Decision com ação, confiança, raciocínio e parâmetros.
        """
        logger.debug('Avaliando %s com %d notícias', ticker, len(news_items))
        now = datetime.now(BRT)

        # ── 1. Obter preço atual com fallback resiliente ──────────────────
        current_price = None
        price_data = None
        try:
            price_data = self.market_data.get_current_price(ticker)
            current_price = price_data.get('last') if price_data else None
        except Exception as exc:
            logger.debug('Preço direto indisponível para %s: %s', ticker, exc)

        # ── 2. Obter histórico OHLCV ────────────────────────────────────
        df = self.market_data.get_ohlcv(
            ticker=ticker, period=self.DEFAULT_OHLCV_PERIOD, interval=self.DEFAULT_OHLCV_INTERVAL
        )

        has_data = False
        if isinstance(df, pd.DataFrame):
            has_data = not df.empty
            if (current_price is None or current_price <= 0) and has_data:
                try:
                    current_price = float(df.iloc[-1]['Close'])
                    price_data = {'last': current_price, 'bid': current_price, 'ask': current_price, 'ticker': ticker}
                except Exception:
                    pass
        elif isinstance(df, (list, tuple)):
            has_data = len(df) > 0
            if (current_price is None or current_price <= 0) and has_data:
                try:
                    last_item = df[-1]
                    current_price = float(getattr(last_item, 'close', getattr(last_item, 'Close', 0.0)))
                    price_data = {'last': current_price, 'bid': current_price, 'ask': current_price, 'ticker': ticker}
                except Exception:
                    pass

        if current_price is None or current_price <= 0 or not has_data:
            logger.debug('Dados de mercado indisponíveis para %s — mantendo HOLD', ticker)
            return Decision(
                ticker=ticker,
                action=Action.HOLD,
                confidence=0.0,
                reasoning=f"Dados de mercado temporariamente indisponíveis para {ticker}.",
                technical_score=0.0,
                sentiment_score=0.0,
                quantity=0,
                price=0.0,
                stop_loss=0.0,
                timestamp=now,
            )

        # ── 3. Análise técnica ───────────────────────────────────────────
        try:
            tech_result = self.technical.analyze(ticker, df)
        except ValueError as exc:
            logger.warning('Análise técnica falhou para %s: %s', ticker, exc)
            tech_result = TechnicalResult(
                signal=TrendSignal.NEUTRAL,
                ema_9=0.0, ema_21=0.0, ema_50=0.0,
                rsi=50.0,
                support=current_price * 0.95,
                resistance=current_price * 1.05,
                macd_hist=0.0,
                atr=0.0,
                rel_vol=1.0,
                bb_lower=0.0,
                bb_upper=0.0,
                confidence=0.0,
                reasoning=f'Análise técnica indisponível: {exc}',
            )

        # ── 4. Verificar posição existente ───────────────────────────────
        position = self.portfolio.get_position(ticker)
        is_holding = position is not None

        entry_price: float | None = None
        existing_stop: float | None = None
        existing_qty: int = 0

        if is_holding and position is not None:
            entry_price = getattr(position, 'entry_price', None)
            existing_stop = getattr(position, 'stop_loss', None)
            existing_qty = getattr(position, 'quantity', 0)

        # ── 5. Análise de sentimento inteligente (sob demanda por ativo) ─
        # Filtrar notícias exclusivas deste ativo (e seus nomes/aliases corporativos)
        ticker_news = self._filter_news_for_ticker(ticker, news_items or [])

        # Economia de cota da IA: só aciona o Gemini se já temos posição aberta
        # OU se a análise técnica já detectou um sinal de compra/venda (não neutro).
        allow_gemini = is_holding or (tech_result.signal != TrendSignal.NEUTRAL)
        sent_result = self.sentiment.get_sentiment_for_ticker(
            ticker, ticker_news, allow_gemini=allow_gemini
        )

        # ── 6. Se posicionado: verificar stop-loss ───────────────────────
        if is_holding and existing_stop is not None:
            if current_price <= existing_stop:
                reasoning = self._generate_thinking(
                    ticker, tech_result, sent_result, Action.EMERGENCY_SELL, position,
                    current_price=current_price,
                    stop_loss=existing_stop,
                    quantity=existing_qty,
                )
                decision = Decision(
                    ticker=ticker,
                    action=Action.EMERGENCY_SELL,
                    confidence=1.0,
                    reasoning=reasoning,
                    technical_score=tech_result.confidence,
                    sentiment_score=sent_result.score,
                    quantity=existing_qty,
                    price=current_price,
                    stop_loss=existing_stop or 0.0,
                    timestamp=now,
                )
                logger.warning(
                    'STOP-LOSS ATIVADO para %s: preço %s <= SL %s',
                    ticker, format_brl(current_price),
                    format_brl(existing_stop),
                )
                self._persist_decision(decision, trend_signal=tech_result.signal.value)
                return decision

        # ── 7. Calcular confiança (antes do sizing) ──────────────────────
        confidence = self._calculate_confidence(tech_result.signal, sent_result.score)

        # ── 8. Determinar ação ───────────────────────────────────────────
        action: Action
        stop_loss: float | None = None
        target_quantity: int = 0

        if not is_holding:
            # Avaliar convergência e setup para BUY
            # Condição: Sinal técnico altista (STRONG_BUY / BUY) e sentimento corporativo não hostil (>= -0.15)
            is_bullish_tech = tech_result.signal in (TrendSignal.STRONG_BUY, TrendSignal.BUY)
            sentiment_not_hostile = sent_result.score >= -0.15

            if is_bullish_tech and sentiment_not_hostile:
                available = self.portfolio.free_cash
                summary = self.portfolio.get_summary()
                target_quantity = self.risk_manager.get_max_shares(
                    current_price, available,
                    confidence=confidence,
                    total_portfolio_value=summary.total_value or available,
                    positions=self.portfolio.get_all_positions(),
                    ticker=ticker,
                )
                stop_loss = self.risk_manager.calculate_stop_loss(current_price, atr=tech_result.atr)

                if target_quantity > 0:
                    action = Action.BUY
                else:
                    action = Action.HOLD
                    logger.info(
                        '%s: Setup de compra detectado mas capital/concentração insuficiente '
                        '(disponível: %s, preço: %s)',
                        ticker, format_brl(available), format_brl(current_price),
                    )
            elif (
                tech_result.signal in (TrendSignal.STRONG_SELL, TrendSignal.SELL)
                and sent_result.score <= -0.2
            ):
                # Sem posição + sinal de venda → apenas HOLD (não abre short)
                action = Action.HOLD
            else:
                action = Action.HOLD
        else:
            # Avaliação de posição existente (stop não atingido)
            if (
                tech_result.signal in (TrendSignal.STRONG_SELL, TrendSignal.SELL)
                or sent_result.score < -0.3
            ):
                action = Action.SELL
                target_quantity = existing_qty
                stop_loss = existing_stop
            else:
                action = Action.HOLD
                target_quantity = existing_qty
                stop_loss = existing_stop

        # (confiança já calculada acima, passo 7)

        # ── 9. Gerar 'Pensamento do Córtex' ──────────────────────────────
        reasoning = self._generate_thinking(
            ticker, tech_result, sent_result, action, position,
            current_price=current_price,
            stop_loss=stop_loss,
            quantity=target_quantity,
        )

        decision = Decision(
            ticker=ticker,
            action=action,
            confidence=round(confidence, 4),
            reasoning=reasoning,
            technical_score=tech_result.confidence,
            sentiment_score=sent_result.score,
            quantity=target_quantity,
            price=current_price,
            stop_loss=stop_loss or 0.0,
            timestamp=now,
        )

        logger.info(
            'Decisão %s: %s (confiança=%.2f, técnico=%s, sentimento=%.2f)',
            ticker, action.value, confidence,
            tech_result.signal.value, sent_result.score,
        )

        self._persist_decision(decision, trend_signal=tech_result.signal.value)
        return decision

    def evaluate_all(
        self,
        watchlist: list[str],
        news_items: list[NewsItem],
    ) -> list[Decision]:
        """
        Avalia todos os ativos da watchlist.

        Prioriza verificação de stop-loss para posições existentes
        antes de avaliar novas oportunidades.

        Args:
            watchlist: Lista de tickers a avaliar.
            news_items: Lista consolidada de notícias (serão filtradas por ticker).

        Returns:
            Lista de Decision para cada ativo avaliado.
        """
        logger.info('Avaliando watchlist com %d ativos', len(watchlist))
        decisions: list[Decision] = []

        # Separar ativos com posição (prioridade: stop-loss) dos sem posição
        held_tickers: list[str] = []
        free_tickers: list[str] = []

        for ticker in watchlist:
            position = self.portfolio.get_position(ticker)
            if position is not None:
                held_tickers.append(ticker)
            else:
                free_tickers.append(ticker)

        # Avaliar posições existentes primeiro (stop-loss tem prioridade)
        for ticker in held_tickers:
            ticker_news = self._filter_news_for_ticker(ticker, news_items)
            decision = self.evaluate(ticker, ticker_news)
            decisions.append(decision)

        # Avaliar ativos sem posição
        for ticker in free_tickers:
            ticker_news = self._filter_news_for_ticker(ticker, news_items)
            decision = self.evaluate(ticker, ticker_news)
            decisions.append(decision)

        # Resumo
        actionable = [d for d in decisions if d.action != Action.HOLD]
        logger.info(
            'Avaliação concluída: %d decisões totais, %d acionáveis',
            len(decisions), len(actionable),
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
    ) -> str:
        """
        Gera o texto completo do 'Pensamento do Córtex' em português.

        Explica:
            - Setup técnico atual (EMAs, RSI, S/R)
            - Resumo da análise de sentimento
            - Razão da decisão tomada
            - Avaliação de risco

        Args:
            ticker: Código do ativo.
            tech: Resultado da análise técnica.
            sent: Resultado da análise de sentimento.
            action: Ação decidida.
            position: Posição existente (se houver).
            current_price: Preço de mercado atual.
            stop_loss: Stop-loss calculado para a operação.
            quantity: Quantidade de ações alvo.

        Returns:
            Texto rico em português.
        """
        parts: list[str] = []
        fractional_ticker = f'{ticker}F'

        # ── Setup técnico ────────────────────────────────────────────────
        parts.append(tech.reasoning)

        # ── Sentimento Corporativo Individual ────────────────────────────
        score_fmt = f'{sent.score:+.2f}'.replace('.', ',')
        if sent.news_count > 0 and sent.top_headline and sent.top_headline not in ('Sem notícias disponíveis', 'Sem notícias específicas'):
            headline_trunc = (
                sent.top_headline[:75] + '...'
                if len(sent.top_headline) > 75
                else sent.top_headline
            )
            sentiment_desc = f"Sentimento corporativo: {score_fmt} ({sent.label.lower()}) — notícia: '{headline_trunc}'"
        else:
            sentiment_desc = f"Sentimento corporativo: {score_fmt} (neutro - sem notícias específicas recentes)"

        parts.append(f'{sentiment_desc}.')

        # ── Decisão e raciocínio ─────────────────────────────────────────
        if action == Action.EMERGENCY_SELL:
            entry_price = getattr(position, 'entry_price', 0.0) if position else 0.0
            pos_stop = getattr(position, 'stop_loss', 0.0) if position else (stop_loss or 0.0)
            qty = getattr(position, 'quantity', 0) if position else quantity
            parts.append(
                f'⚠️ STOP-LOSS ATIVADO! Preço atual ({format_brl(current_price)}) atingiu ou ultrapassou '
                f'o stop-loss em {format_brl(pos_stop)}. '
                f'Posição de {qty} ações (entrada em {format_brl(entry_price)}) '
                f'liquidada para proteção de capital. '
                f'→ DECISÃO: VENDA EMERGENCIAL de {qty} ações {fractional_ticker}.'
            )

        elif action == Action.BUY:
            if sent.score >= 0.20:
                parts.append('Convergência confirmada: Setup técnico altista com sentimento corporativo favorável.')
            else:
                parts.append('Setup técnico altista confirmado com sentimento corporativo estável (sem notícias desfavoráveis).')

            stop_val = stop_loss if (stop_loss and stop_loss > 0) else self.risk_manager.calculate_stop_loss(current_price, atr=tech.atr)
            if current_price > 0 and stop_val > 0:
                dist_pct = ((current_price - stop_val) / current_price) * 100.0
                parts.append(
                    f'Stop-loss configurado em {format_brl(stop_val)} ({dist_pct:.2f}% abaixo da entrada).'
                )
            if quantity > 0:
                parts.append(f'Ordem dimensionada para {quantity} ações (R$ {quantity * current_price:,.2f}).')
            parts.append(f'→ DECISÃO: COMPRAR ações {fractional_ticker}.')

        elif action == Action.SELL:
            parts.append(
                f'Reversão técnica baixista identificada. '
                f'Indicadores apontam perda de suporte ou médias cruzadas para baixo.'
            )
            qty = getattr(position, 'quantity', 0) if position else quantity
            parts.append(f'→ DECISÃO: VENDER {qty} ações {fractional_ticker}.')

        elif action == Action.HOLD:
            if position is not None:
                parts.append(
                    f'Posição existente mantida. '
                    f'Indicadores em andamento sem gatilho de saída. '
                    f'Monitorando trailing stop.'
                )
                parts.append(f'→ DECISÃO: MANTER posição em {fractional_ticker}.')
            else:
                if tech.signal in (TrendSignal.STRONG_BUY, TrendSignal.BUY):
                    if sent.score < -0.15:
                        parts.append(
                            f'Sinal técnico favorável, porém sentimento corporativo desfavorável '
                            f'(score: {score_fmt} < -0,15). Entrada bloqueada por precaução de risco.'
                        )
                    else:
                        parts.append(
                            f'Setup de compra identificado. Aguardando alocação de capital disponível.'
                        )
                elif tech.signal in (TrendSignal.STRONG_SELL, TrendSignal.SELL):
                    parts.append(
                        f'Sinal técnico desfavorável. Sem posição para vender.'
                    )
                else:
                    parts.append(
                        f'Sinais técnicos inconclusivos. Sem condições para entrada.'
                    )
                parts.append(f'→ DECISÃO: AGUARDAR para {fractional_ticker}.')

        return ' '.join(parts)

    @staticmethod
    def _calculate_confidence(trend_signal: TrendSignal, sentiment_score: float) -> float:
        """
        Calcula confiança na decisão baseada na convergência técnica e sentimento.

        Matriz de confiança:
            - STRONG_BUY + sentimento >= 0.3 → 0.95
            - STRONG_BUY + sentimento >= 0.0 → 0.85
            - STRONG_BUY + sentimento >= -0.15 → 0.75
            - BUY + sentimento >= 0.3 → 0.80
            - BUY + sentimento >= 0.0 → 0.70
            - BUY + sentimento >= -0.15 → 0.60
            - NEUTRAL → 0.30

        Args:
            trend_signal: Sinal da análise técnica.
            sentiment_score: Score de sentimento (-1.0 a 1.0).

        Returns:
            Confiança entre 0.0 e 1.0.
        """
        if trend_signal == TrendSignal.STRONG_BUY:
            if sentiment_score >= 0.3:
                return 0.95
            elif sentiment_score >= 0.0:
                return 0.85
            elif sentiment_score >= -0.15:
                return 0.75
            else:
                return 0.40

        if trend_signal == TrendSignal.BUY:
            if sentiment_score >= 0.3:
                return 0.80
            elif sentiment_score >= 0.0:
                return 0.70
            elif sentiment_score >= -0.15:
                return 0.60
            else:
                return 0.35

        if trend_signal == TrendSignal.STRONG_SELL:
            if sentiment_score <= -0.3:
                return 0.95
            elif sentiment_score <= 0.0:
                return 0.85
            elif sentiment_score <= 0.15:
                return 0.75
            else:
                return 0.40

        if trend_signal == TrendSignal.SELL:
            if sentiment_score <= -0.3:
                return 0.80
            elif sentiment_score <= 0.0:
                return 0.70
            elif sentiment_score <= 0.15:
                return 0.60
            else:
                return 0.35

        if trend_signal == TrendSignal.NEUTRAL:
            return 0.30

        # Sinais conflitantes
        return 0.35

    TICKER_ALIASES: dict[str, list[str]] = {
        "PETR4": ["PETR4", "PETR3", "PETROBRAS", "PETROBRÁS"],
        "VALE3": ["VALE3", "VALE DO RIO DOCE", "VALE"],
        "ITUB4": ["ITUB4", "ITUB3", "ITAÚ UNIBANCO", "ITAU UNIBANCO", "ITAÚ", "ITAU"],
        "BBDC4": ["BBDC4", "BBDC3", "BRADESCO"],
        "BBAS3": ["BBAS3", "BANCO DO BRASIL"],
        "BBSE3": ["BBSE3", "BB SEGURIDADE", "BB SEGUROS"],
        "CXSE3": ["CXSE3", "CAIXA SEGURIDADE", "CAIXA SEGUROS"],
        "SANB11": ["SANB11", "SANTANDER"],
        "BPAC11": ["BPAC11", "BTG PACTUAL", "BTG"],
        "B3SA3": ["B3SA3", "B3 "],
        "ITSA4": ["ITSA4", "ITAÚSA", "ITAUSA"],
        "WEGE3": ["WEGE3", "WEG"],
        "RENT3": ["RENT3", "LOCALIZA"],
        "PRIO3": ["PRIO3", "PRIO", "PETRORIO", "PETRO RIO"],
        "RECV3": ["RECV3", "PETRORECONCAVO", "PETRORECÔNCAVO"],
        "BRAV3": ["BRAV3", "BRAVA ENERGIA", "ENAUTA"],
        "UGPA3": ["UGPA3", "ULTRAPAR", "IPIRANGA"],
        "VBBR3": ["VBBR3", "VIBRA ENERGIA", "VIBRA", "BR DISTRIBUIDORA"],
        "CSAN3": ["CSAN3", "COSAN"],
        "RAIZ4": ["RAIZ4", "RAÍZEN", "RAIZEN"],
        "GGBR4": ["GGBR4", "GERDAU"],
        "GOAU4": ["GOAU4", "METALÚRGICA GERDAU", "METALURGICA GERDAU"],
        "CSNA3": ["CSNA3", "SIDERÚRGICA NACIONAL", "SIDERURGICA NACIONAL", "CSN"],
        "USIM5": ["USIM5", "USIMINAS"],
        "CMIN3": ["CMIN3", "CSN MINERAÇÃO", "CSN MINERACAO"],
        "SUZB3": ["SUZB3", "SUZANO"],
        "KLBN11": ["KLBN11", "KLABIN"],
        "MGLU3": ["MGLU3", "MAGALU", "MAGAZINE LUIZA"],
        "LREN3": ["LREN3", "LOJAS RENNER", "RENNER"],
        "AZZA3": ["AZZA3", "AZZAS", "AREZZO", "GRUPO SOMA"],
        "ABEV3": ["ABEV3", "AMBEV"],
        "ASAI3": ["ASAI3", "ASSAÍ", "ASSAI"],
        "BEEF3": ["BEEF3", "MINERVA FOODS", "MINERVA"],
        "RDOR3": ["RDOR3", "REDE D'OR", "REDE DOR"],
        "HAPV3": ["HAPV3", "HAPVIDA", "NOTRE DAME"],
        "RADL3": ["RADL3", "RAIADROGASIL", "RAIA DROGASIL", "DROGA RAIA"],
        "FLRY3": ["FLRY3", "FLEURY"],
        "HYPE3": ["HYPE3", "HYPERA", "HYPERMARCAS"],
        "BLAU3": ["BLAU3", "BLAU FARMACEUTICA", "BLAU"],
        "TOTS3": ["TOTS3", "TOTVS"],
        "LWSA3": ["LWSA3", "LOCAWEB"],
        "CASH3": ["CASH3", "MÉLIUZ", "MELIUZ"],
        "POSI3": ["POSI3", "POSITIVO TECNOLOGIA"],
        "INTB3": ["INTB3", "INTELBRAS"],
        "BMOB3": ["BMOB3", "BEMOBI"],
        "VIVT3": ["VIVT3", "TELEFÔNICA", "TELEFONICA BRASIL", "VIVO"],
        "TIMS3": ["TIMS3", "TIM BRASIL"],
        "CMIG4": ["CMIG4", "CEMIG"],
        "CPFE3": ["CPFE3", "CPFL ENERGIA", "CPFL"],
        "EGIE3": ["EGIE3", "ENGIE BRASIL", "ENGIE"],
        "EQTL3": ["EQTL3", "EQUATORIAL ENERGIA", "EQUATORIAL"],
        "ENEV3": ["ENEV3", "ENEVA"],
        "TAEE11": ["TAEE11", "TAESA"],
        "ALUP11": ["ALUP11", "ALUPAR"],
        "SBSP3": ["SBSP3", "SABESP"],
        "CSMG3": ["CSMG3", "COPASA"],
        "SAPR11": ["SAPR11", "SANEPAR"],
        "CYRE3": ["CYRE3", "CYRELA"],
        "EZTC3": ["EZTC3", "EZTEC"],
        "MRVE3": ["MRVE3", "MRV ENGENHARIA", "MRV"],
        "DIRR3": ["DIRR3", "DIRECIONAL ENGENHARIA", "DIRECIONAL"],
        "CURY3": ["CURY3", "CURY CONSTRUTORA", "CURY"],
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
        "PSSA3": ["PSSA3", "PORTO SEGURO"],
        "WIZC3": ["WIZC3", "WIZ CO"],
        "POMO4": ["POMO4", "MARCOPOLO"],
        "TUPY3": ["TUPY3", "TUPY"],
        "LEVE3": ["LEVE3", "MAHLE METAL LEVE", "METAL LEVE"],
        "SHUL4": ["SHUL4", "SCHULZ"],
        "ROMI3": ["ROMI3", "INDÚSTRIAS ROMI", "INDUSTRIAS ROMI"],
        "KEPL3": ["KEPL3", "KEPLER WEBER"],
        "UNIP6": ["UNIP6", "UNIPAR CARBOCLORO", "UNIPAR"],
        "FESA4": ["FESA4", "FERBASA"],
        "RANI3": ["RANI3", "IRANI PAPEL", "IRANI"],
        "MYPK3": ["MYPK3", "IOCHPE-MAXION"],
        "TGMA3": ["TGMA3", "TEGMA"],
        "LOGN3": ["LOGN3", "LOG-IN LOGISTICA", "LOG-IN"],
        "RAIL3": ["RAIL3", "RUMO LOGISTICA", "RUMO"],
        "SMTO3": ["SMTO3", "SÃO MARTINHO", "SAO MARTINHO"],
        "SLCE3": ["SLCE3", "SLC AGRICOLA", "SLC AGRÍCOLA"],
        "DXCO3": ["DXCO3", "DEXCO", "DURATEX"],
        "BRKM5": ["BRKM5", "BRASKEM"],
        "YDUQ3": ["YDUQ3", "YDUQS", "ESTÁCIO"],
        "COGN3": ["COGN3", "COGNA", "KROTON"],
        "ANIM3": ["ANIM3", "ÂNIMA EDUCAÇÃO", "ANIMA EDUCACAO"],
        "SEER3": ["SEER3", "SER EDUCACIONAL"],
        "MDIA3": ["MDIA3", "M. DIAS BRANCO", "M DIAS BRANCO"],
        "CAML3": ["CAML3", "CAMIL ALIMENTOS", "CAMIL"],
        "AURA33": ["AURA33", "AURA MINERALS"],
    }

    @classmethod
    def _filter_news_for_ticker(
        cls, ticker: str, news_items: list[NewsItem]
    ) -> list[NewsItem]:
        """
        Filtra notícias relevantes para um ativo específico usando tickers e nomes corporativos.

        Args:
            ticker: Código do ativo (ex: 'PETR4').
            news_items: Lista completa de notícias.

        Returns:
            Subconjunto de notícias relevantes exclusivamente ao ativo.
        """
        if not news_items:
            return []

        base = ticker.upper().rstrip('F')
        aliases = cls.TICKER_ALIASES.get(base, [base, f'{base}F', f'{base}.SA'])
        variants = {a.upper() for a in aliases}
        variants.add(base)
        variants.add(f'{base}F')
        variants.add(f'{base}.SA')

        filtered: list[NewsItem] = []
        for item in news_items:
            text = f'{item.title} {item.summary}'.upper()
            if any(v in text for v in variants):
                filtered.append(item)

        return filtered

    def _persist_decision(self, decision: Decision, trend_signal: Optional[str] = None) -> None:
        """
        Persiste a decisão no banco de dados.

        Args:
            decision: Decisão a ser salva.
            trend_signal: Sinal técnico (ex: 'BUY', 'STRONG_SELL').
        """
        try:
            self.db.insert_decision(
                ticker=decision.ticker,
                action=decision.action.value,
                confidence=decision.confidence,
                trend_signal=trend_signal,
                sentiment_score=decision.sentiment_score,
                reasoning=decision.reasoning,
                timestamp=decision.timestamp.isoformat(),
            )
        except Exception as exc:
            logger.error('Erro ao persistir decisão para %s: %s', decision.ticker, exc)

