"""
Motor principal do Projeto Córtex.

Orquestra todos os componentes do sistema de trading autônomo:
dados de mercado, broker, análise técnica/sentimento, gerenciamento
de risco, notificações e monitoramento de saúde.
"""

from __future__ import annotations

import logging
import signal
import sys
import time as time_module
from datetime import datetime, date
from typing import Any, Optional

from analysis.decision import DecisionEngine
from analysis.sentiment import SentimentAnalyzer
from analysis.technical import TechnicalAnalyzer
from broker.base import BaseBroker, OrderStatus
from broker.simulator import SimulatorBroker
from config.settings import Settings
from utils.logger import setup_logger
from core.risk_manager import RiskManager
from core.scheduler import MarketScheduler
from data.database import DatabaseManager
from data.market_data import MarketData
from models.data_models import Action, BRT, Decision, Position
from dashboard.app import DashboardState as WebDashboardState, DashboardServer
from monitoring.health import HealthMonitor
from notifications.telegram import TelegramNotifier
from portfolio import Portfolio
from data.news_scraper import NewsScraper

logger = logging.getLogger('cortex.engine')


class CortexEngine:
    """
    Motor principal do Projeto Córtex — orquestrador central.

    Coordena todos os subsistemas: dados de mercado, broker,
    análise, risco, notificações e monitoramento.
    """

    def __init__(
        self,
        force_simulation: bool = False,
        verbose: bool = False,
        single_cycle: bool = False,
    ) -> None:
        """
        Inicializa todos os componentes do sistema.

        Args:
            force_simulation: Se True, força modo simulação independente da config.
            verbose: Se True, ativa logging em nível DEBUG.
            single_cycle: Se True, executa apenas um ciclo e para.
        """
        # ── Configuração ─────────────────────────────────────────────────
        self.settings = Settings(
            simulation_mode=force_simulation if force_simulation else None,
            verbose=verbose,
        )

        # ── Logging ──────────────────────────────────────────────────────
        setup_logger('cortex', verbose=verbose)
        logger.info('═══════════════════════════════════════════════════')
        logger.info('   PROJETO CÓRTEX — Inicialização')
        logger.info('═══════════════════════════════════════════════════')
        logger.info('Modo: %s', 'SIMULAÇÃO' if self.settings.simulation_mode else 'LIVE')
        logger.info('Capital inicial: R$ %.2f', self.settings.capital_inicial)
        logger.info('Stop-loss: %.1f%%', self.settings.stop_loss_percent * 100)

        # ── Banco de Dados ───────────────────────────────────────────────
        self.db = DatabaseManager(self.settings.db_path)

        # ── Dados de Mercado ─────────────────────────────────────────────
        self.market_data = MarketData()

        # ── Broker ───────────────────────────────────────────────────────
        if self.settings.simulation_mode or force_simulation:
            self.broker: BaseBroker = SimulatorBroker(
                initial_balance=self.settings.capital_inicial
            )
            logger.info('Broker: SimulatorBroker')
        else:
            try:
                from broker.mt5_broker import MT5Broker
                self.broker = MT5Broker()
                logger.info('Broker: MT5Broker')
            except ImportError:
                logger.warning('MT5Broker indisponível — usando SimulatorBroker')
                self.broker = SimulatorBroker(
                    initial_balance=self.settings.capital_inicial
                )

        # ── Portfólio ────────────────────────────────────────────────────
        self.portfolio = Portfolio(initial_capital=self.settings.capital_inicial)

        # ── Análise ──────────────────────────────────────────────────────
        self.technical = TechnicalAnalyzer()
        self.sentiment = SentimentAnalyzer(mode=self.settings.SENTIMENT_MODE)

        # ── Risco (deve ser criado antes do DecisionEngine) ──────────
        self.risk_manager = RiskManager(
            stop_loss_percent=self.settings.stop_loss_percent
        )

        self.decision_engine = DecisionEngine(
            technical=self.technical,
            sentiment=self.sentiment,
            risk_manager=self.risk_manager,
            market_data=self.market_data,
            portfolio=self.portfolio,
            db=self.db,
        )

        # ── Scheduler ────────────────────────────────────────────────────
        self.scheduler = MarketScheduler()

        # ── Scraping ─────────────────────────────────────────────────────
        self.news_scraper = NewsScraper()

        # ── Notificações ─────────────────────────────────────────────────
        self.telegram = TelegramNotifier(
            token=self.settings.TELEGRAM_TOKEN,
            chat_id=self.settings.TELEGRAM_CHAT_ID,
            db=self.db,
            channel_id=self.settings.TELEGRAM_CHANNEL_ID,
        )

        # ── Monitoramento ────────────────────────────────────────────────
        self.health_monitor = HealthMonitor(
            telegram=self.telegram,
            db=self.db,
            check_interval=self.settings.health_check_interval,
            alert_cooldown=self.settings.alert_cooldown,
        )

        # ── Estado do Dashboard ──────────────────────────────────────────
        self.dashboard_state = WebDashboardState()

        # ── Servidor Dashboard ───────────────────────────────────────────
        self.dashboard_server = DashboardServer(
            self.dashboard_state,
            host=getattr(self.settings, 'dashboard_host', '0.0.0.0'),
            port=getattr(self.settings, 'dashboard_port', 8003),
        )

        # ── Flags de Controle ────────────────────────────────────────────
        self.running: bool = False
        self.single_cycle: bool = single_cycle
        self.last_cycle_time: Optional[datetime] = None
        self._start_time: Optional[datetime] = None
        self._last_error_alert: dict[str, float] = {}  # cooldown por erro
        self._opening_alert_sent_today: bool = False
        self._closing_report_sent_today: bool = False
        self._was_market_open: bool = False
        self._last_alert_date: Optional[date] = None
        self._recent_decisions: list[dict[str, Any]] = []

        logger.info('Todos os componentes inicializados')

    def start(self) -> None:
        """
        Inicia o sistema Córtex.

        1. Conecta broker.
        2. Inicializa banco de dados.
        3. Carrega modelo NLP (se modo completo).
        4. Inicia daemon de monitoramento.
        5. Registra handlers de sinal para shutdown gracioso.
        """
        logger.info('Córtex iniciando...')

        # Conectar broker
        if not self.broker.connect():
            logger.error('Falha ao conectar broker — abortando')
            raise RuntimeError('Falha ao conectar broker')
        logger.info('Broker conectado')

        # Banco de dados já inicializado no __init__
        logger.info('Banco de dados pronto')

        # Carregar modelo NLP se modo 'full' ativo
        if self.sentiment.mode == 'full':
            logger.info('Carregando modelo NLP (FinBERT)...')
            try:
                self.sentiment._load_finbert()
                logger.info('Modelo NLP carregado')
            except ImportError:
                logger.warning('Modelo NLP não disponível — usando análise por keywords')

        # Flags de controle
        self.running = True
        self._start_time = datetime.now(BRT)

        # Iniciar monitoramento de saúde
        self.health_monitor.start_daemon()

        market_open = self.scheduler.is_market_open()
        self.dashboard_state.update('market_status', 'ABERTO' if market_open else 'FECHADO')
        # O dashboard standalone (uvicorn) já roda na porta 8003
        # Não iniciar o dashboard embutido para evitar conflito de porta
        # self.dashboard_server.start()
        logger.info('Dashboard standalone em http://0.0.0.0:8003')
        logger.info('Daemon de monitoramento iniciado')

        # Iniciar polling interativo do Telegram
        self._register_telegram_commands()
        self.telegram.start_polling()

        # Registrar signal handlers
        self._register_signal_handlers()

        logger.info('═══════════════════════════════════════════════════')
        logger.info('   CÓRTEX OPERACIONAL')
        logger.info('═══════════════════════════════════════════════════')

        # Enviar aviso de inicialização
        mode_str = "Simulação" if self.settings.simulation_mode else "PRODUÇÃO"
        self.telegram.send_to_channel(f"🚀 *Córtex Iniciado!*\nModo: {mode_str}\nStatus: Operacional")

    def run(self) -> None:
        """
        Loop principal do sistema.

        Executa continuamente:
        - Verificação de saúde.
        - Ciclos de trading durante horário de mercado.
        - Manutenção durante horário fechado.
        - Alertas de abertura/fechamento.
        """
        logger.info('Entrando no loop principal')

        while self.running:
            try:
                # Verificação de saúde
                self.health_monitor.check()

                # Resetar flags diárias
                self._reset_daily_flags()

                if self.scheduler.is_market_open():
                    self._was_market_open = True

                    # Enviar alerta de abertura uma vez por dia
                    if not self._opening_alert_sent_today:
                        self._send_opening_alert()

                    # Executar ciclo de trading
                    self._run_trading_cycle()

                    # Modo single-cycle: executar uma vez e parar
                    if self.single_cycle:
                        logger.info('Modo single-cycle — encerrando')
                        self.stop()
                        return

                    time_module.sleep(self.settings.trading_cycle_interval)

                else:
                    # Mercado fechado
                    if (
                        self._was_market_open
                        and not self._closing_report_sent_today
                    ):
                        self._send_closing_report()
                        self._was_market_open = False

                    # Manutenção
                    if self.scheduler.should_run_maintenance():
                        self._run_maintenance()

                    # Modo single-cycle fora do mercado
                    if self.single_cycle:
                        logger.info('Modo single-cycle (mercado fechado) — encerrando')
                        self.stop()
                        return

                    time_module.sleep(self.settings.closed_check_interval)

            except KeyboardInterrupt:
                logger.info('Interrupção do teclado recebida')
                self.stop()
                return

            except Exception as e:
                logger.error('Erro no loop principal: %s', e, exc_info=True)
                # Cooldown de 15 min por mensagem de erro para não spammar Telegram
                error_key = str(e)[:80]
                now = time_module.time()
                last_sent = self._last_error_alert.get(error_key, 0)
                if now - last_sent > 900:  # 15 minutos
                    self.telegram.send_alert(f'🚨 ERRO: {e}')
                    self._last_error_alert[error_key] = now
                time_module.sleep(self.settings.trading_cycle_interval)

    def _run_trading_cycle(self) -> None:
        """
        Executa um ciclo completo de trading.

        1. Atualiza preços do watchlist.
        2. VERIFICA STOP-LOSS PRIMEIRO (maior prioridade).
        3. Coleta notícias recentes.
        4. Avalia cada ativo do watchlist via DecisionEngine.
        5. Executa operações se acionáveis.
        6. Verifica alertas de volatilidade.
        7. Atualiza estado do dashboard.
        """
        cycle_start = datetime.now(BRT)
        logger.info('─── Início do ciclo de trading ───')

        # 1. Atualizar preços
        prices = self.market_data.update_prices(self.settings.watchlist)
        self.portfolio.update_prices(prices)
        logger.debug('Preços atualizados para %d ativos', len(prices))

        # 2. STOP-LOSS — MÁXIMA PRIORIDADE
        positions = self.portfolio.positions
        if positions:
            triggered = self.risk_manager.check_stop_loss_triggers(
                positions, self.market_data
            )
            for position in triggered:
                logger.warning(
                    '🚨 Executando venda emergencial: %s', position.ticker
                )
                trade = self.broker.emergency_sell(position.ticker)
                if trade is not None and trade.status == OrderStatus.FILLED:
                    self.portfolio.remove_position(
                        position.ticker, position.current_price
                    )
                    self.db.insert_trade(
                        ticker=trade.ticker,
                        action=trade.order_type.value,
                        quantity=trade.quantity,
                        price=trade.price,
                        total_value=trade.total_value,
                        stop_loss=trade.stop_loss,
                        is_simulated=self.settings.simulation_mode,
                    )
                    reason = (
                        f'Preço R$ {position.current_price:.2f} <= '
                        f'Stop-loss R$ {position.stop_loss:.2f} '
                        f'(entrada R$ {position.entry_price:.2f})'
                    )
                    self.telegram.send_emergency_alert(position.ticker, reason)

            # TAKE-PROFIT PARCIAL
            tp_triggered = self.risk_manager.check_take_profit_triggers(positions)
            for position in tp_triggered:
                logger.info('🎯 Executando realização parcial: %s', position.ticker)
                qty_to_sell = max(1, position.quantity // 2)
                trade = self.broker.sell(position.ticker, qty_to_sell, position.current_price)
                if trade is not None and trade.status == OrderStatus.FILLED:
                    self.portfolio.reduce_position(position.ticker, trade.price, trade.quantity)
                    position.partial_exit_done = True
                    # Subir stop para o breakeven
                    position.stop_loss = position.entry_price
                    self.db.insert_trade(
                        ticker=trade.ticker,
                        action=trade.order_type.value,
                        quantity=trade.quantity,
                        price=trade.price,
                        total_value=trade.total_value,
                        stop_loss=trade.stop_loss,
                        is_simulated=self.settings.simulation_mode,
                    )
                    self.telegram.send_alert(
                        f'🎯 TAKE-PROFIT PARCIAL: Venda de {trade.quantity} ações de {trade.ticker} a R$ {trade.price:.2f}. '
                        f'Stop-loss restante ajustado para breakeven (R$ {position.stop_loss:.2f}).'
                    )

        # 3. Coletar notícias
        news = self.news_scraper.fetch_all_news()
        logger.debug('Notícias coletadas: %d itens', len(news))

        # 3.1 Persistir notícias no banco de dados (deduplicação por URL)
        for item in news:
            for ticker in (item.tickers_mentioned or [None]):
                self.db.insert_news(
                    title=item.title,
                    source=item.source,
                    url=item.url,
                    ticker=ticker,
                    published_at=item.published_at.isoformat() if item.published_at else None,
                )

        # 4. Avaliar cada ativo do watchlist
        for ticker in self.settings.watchlist:
            try:
                decision = self.decision_engine.evaluate(
                    ticker=ticker,
                    news_items=news,
                )

                # Registrar decisão no banco
                self.db.insert_decision(
                    ticker=decision.ticker,
                    action=decision.action.value,
                    confidence=decision.confidence,
                    sentiment_score=decision.sentiment_score,
                    reasoning=decision.reasoning[:500] if decision.reasoning else None,
                )

                # Manter histórico recente para dashboard
                self._recent_decisions.append({
                    'ticker': decision.ticker,
                    'action': decision.action.value,
                    'confidence': decision.confidence,
                    'reasoning': (decision.reasoning[:200] if decision.reasoning else ''),
                    'timestamp': decision.timestamp.isoformat(),
                })
                # Manter apenas as 50 decisões mais recentes
                self._recent_decisions = self._recent_decisions[-50:]

                # Executar operação se acionável
                if decision.action == Action.BUY:
                    self._execute_buy(decision)
                elif decision.action in (Action.SELL, Action.EMERGENCY_SELL):
                    self._execute_sell(decision)

            except Exception as e:
                logger.error('Erro ao avaliar %s: %s', ticker, e, exc_info=True)

        # 5. Atualizar P&L diário para circuit breaker
        summary = self.portfolio.get_summary()
        self.risk_manager.update_daily_pnl(summary.total_pnl_percent / 100.0)
        if self.risk_manager.is_circuit_breaker_active:
            self.telegram.send_alert(
                '🛑 CIRCUIT BREAKER: Perda diária excede limite. '
                'Operações suspensas até amanhã.'
            )

        # 6. Verificar volatilidade em posições mantidas
        self._check_volatility(self.portfolio.positions)

        # 7. Atualizar estado do dashboard
        self._update_dashboard_state()

        self.last_cycle_time = datetime.now(BRT)
        elapsed = (self.last_cycle_time - cycle_start).total_seconds()
        logger.info('─── Ciclo concluído em %.1fs ───', elapsed)

    def _execute_buy(self, decision: Decision) -> None:
        """Executa ordem de compra baseada na decisão."""
        # Verificar circuit breaker antes de operar
        if self.risk_manager.is_circuit_breaker_active:
            logger.warning('Compra de %s bloqueada — circuit breaker ativo', decision.ticker)
            return

        # Validar ordem via RiskManager
        summary = self.portfolio.get_summary()
        is_valid, reason = self.risk_manager.validate_order(
            ticker=decision.ticker,
            quantity=decision.quantity,
            price=decision.price,
            available_capital=self.portfolio.free_cash,
            total_portfolio_value=summary.total_value,
            positions=self.portfolio.get_all_positions(),
        )

        if not is_valid:
            logger.warning('Compra rejeitada para %s: %s', decision.ticker, reason)
            return

        trade = self.broker.buy(
            ticker=decision.ticker,
            quantity=decision.quantity,
            price=decision.price,
            stop_loss=decision.stop_loss,
        )

        if trade is not None and trade.status == OrderStatus.FILLED:
            # Adicionar posição ao portfólio
            position = Position(
                ticker=decision.ticker,
                quantity=decision.quantity,
                entry_price=decision.price,
                stop_loss=decision.stop_loss,
                current_price=decision.price,
            )
            self.portfolio.add_position(position)
            self.db.insert_trade(
                ticker=trade.ticker,
                action=trade.order_type.value,
                quantity=trade.quantity,
                price=trade.price,
                total_value=trade.total_value,
                stop_loss=trade.stop_loss,
                is_simulated=self.settings.simulation_mode,
            )
            self.telegram.send_trade_alert(
                decision, simulation=self.settings.simulation_mode
            )
            logger.info(
                '✅ COMPRA executada: %s — %d ações @ R$ %.2f',
                decision.ticker, decision.quantity, decision.price,
            )

    def _execute_sell(self, decision: Decision) -> None:
        """Executa ordem de venda baseada na decisão."""
        trade = self.broker.sell(
            ticker=decision.ticker,
            quantity=decision.quantity,
            price=decision.price,
        )

        if trade is not None and trade.status == OrderStatus.FILLED:
            self.portfolio.remove_position(decision.ticker, decision.price)
            self.db.insert_trade(
                ticker=trade.ticker,
                action=trade.order_type.value,
                quantity=trade.quantity,
                price=trade.price,
                total_value=trade.total_value,
                is_simulated=self.settings.simulation_mode,
            )
            self.telegram.send_trade_alert(
                decision, simulation=self.settings.simulation_mode
            )
            logger.info(
                '✅ VENDA executada: %s — %d ações @ R$ %.2f',
                decision.ticker, decision.quantity, decision.price,
            )

    def _run_maintenance(self) -> None:
        """
        Executa ciclo de manutenção.

        1. Atualiza dados históricos OHLCV.
        2. Recalibra indicadores técnicos.
        3. Salva snapshots de mercado.
        4. Registra log de manutenção.
        """
        logger.info('─── Início do ciclo de manutenção ───')

        for ticker in self.settings.watchlist:
            try:
                # Pré-carregar dados OHLCV para cache (usado no próximo ciclo de trading)
                df = self.market_data.get_ohlcv(ticker, period='3mo', interval='1d')
                if df is not None and not df.empty:
                    last_close = df['Close'].iloc[-1]
                    logger.debug(
                        'Manutenção %s: %d candles carregados, último fechamento: R$%.2f',
                        ticker, len(df), last_close,
                    )
            except Exception as e:
                logger.error('Erro na manutenção de %s: %s', ticker, e)

        logger.info('─── Manutenção concluída ───')

    def _send_opening_alert(self) -> None:
        """Envia alerta de abertura do mercado via Telegram."""
        summary = self.portfolio.get_summary()
        self.telegram.send_market_open_alert(
            portfolio_summary=summary,
            simulation=self.settings.simulation_mode,
        )
        self._opening_alert_sent_today = True
        logger.info('Alerta de abertura enviado')

    def _send_closing_report(self) -> None:
        """Calcula estatísticas diárias e envia relatório de fechamento."""
        trades_today = self.db.get_trades_today()
        summary = self.portfolio.get_summary()

        buys = sum(1 for t in trades_today if t.get('action') in ('BUY', 'COMPRA'))
        sells = sum(1 for t in trades_today if t.get('action') in ('SELL', 'VENDA'))

        report = {
            'date': datetime.now(BRT).strftime('%Y-%m-%d'),
            'total_buys': buys,
            'total_sells': sells,
            'free_cash': summary.free_cash,
            'allocated_capital': summary.allocated_capital,
            'total_value': summary.total_value,
            'pnl_percent': summary.total_pnl_percent,
            'initial_capital': self.settings.capital_inicial,
        }

        self.telegram.send_closing_report(
            report=report,
            simulation=self.settings.simulation_mode,
        )
        self._closing_report_sent_today = True
        try:
            self.db.insert_daily_report(
                report_date=report['date'],
                buys_count=buys,
                sells_count=sells,
                free_cash=summary.free_cash,
                allocated_capital=summary.allocated_capital,
                initial_capital=self.settings.capital_inicial,
                total_equity=summary.total_value,
                pnl_percent=summary.total_pnl_percent,
            )
        except Exception as e:
            logger.error('Erro ao salvar relatório diário no DB: %s', e)
        logger.info('Relatório de fechamento enviado')

    def _check_volatility(self, positions: list[Position]) -> None:
        """
        Verifica variações de preço significativas nas posições mantidas.

        Envia alerta se variação > VOLATILITY_ALERT_THRESHOLD%.

        Args:
            positions: Lista de posições abertas.
        """
        for position in positions:
            variation = self.market_data.get_variation(position.ticker)
            if variation is not None and abs(variation) >= self.settings.volatility_alert_threshold:
                old_price = position.entry_price
                new_price = position.current_price

                thinking = (
                    f'Variação de {variation:+.2f}% detectada em {position.ticker}. '
                    f'{"Movimento a favor da posição." if variation > 0 else "Risco elevado — monitorando stop-loss."}'
                )

                self.telegram.send_volatility_alert(
                    ticker=position.ticker,
                    old_price=old_price,
                    new_price=new_price,
                    variation_pct=variation,
                    thinking=thinking,
                )
                logger.info(
                    'Alerta de volatilidade enviado: %s %+.2f%%',
                    position.ticker, variation,
                )

    def _reset_daily_flags(self) -> None:
        """Reseta flags diárias ao mudar de dia."""
        today = datetime.now(BRT).date()
        if self._last_alert_date != today:
            self._opening_alert_sent_today = False
            self._closing_report_sent_today = False
            self._last_alert_date = today
            # Resetar limites diários de risco (circuit breaker)
            self.risk_manager.reset_daily_limits()
            logger.debug('Flags diárias resetadas para %s', today)

    def _update_dashboard_state(self) -> None:
        """Atualiza o estado compartilhado para o dashboard WebSocket."""
        summary = self.portfolio.get_summary()
        health = self.health_monitor.get_status()

        self.dashboard_state.update(
            'market_status',
            'ABERTO' if self.scheduler.is_market_open() else 'FECHADO',
        )

        positions_data = [
            {
                'ticker': p.ticker,
                'quantity': p.quantity,
                'entry_price': p.entry_price,
                'current_price': p.current_price,
                'pnl': p.pnl,
                'pnl_percent': p.pnl_percent,
            }
            for p in summary.positions
        ]

        self.dashboard_state.update('portfolio', {
            'total_value': summary.total_value,
            'free_cash': summary.free_cash,
            'allocated_capital': summary.allocated_capital,
            'total_equity': summary.total_value,
            'initial_capital': self.settings.capital_inicial,
            'pnl': summary.total_pnl,
            'pnl_percent': summary.total_pnl_percent,
            'positions': positions_data,
        })

        watchlist_prices = {}
        for ticker in self.settings.watchlist:
            try:
                price_data = self.market_data.get_current_price(ticker)
                current_price = price_data.get('last', 0.0) if isinstance(price_data, dict) else (price_data or 0.0)
            except Exception:
                current_price = self.market_data._price_cache_legacy.get(ticker, 0.0)
                logger.debug('Preço indisponível para %s, usando cache: %.2f', ticker, current_price)
            watchlist_prices[ticker] = current_price
        self.dashboard_state.update('watchlist', [
            {'ticker': t, 'price': p} for t, p in watchlist_prices.items()
        ])

        self.dashboard_state.update('recent_decisions', self._recent_decisions[-20:])
        self.dashboard_state.update('health', health)

        if self._start_time is not None:
            uptime = (datetime.now(BRT) - self._start_time).total_seconds()
            self.dashboard_state.update('uptime_seconds', uptime)

    def stop(self) -> None:
        """
        Para o sistema de forma graciosa.

        1. Define flag running = False.
        2. Para daemon de monitoramento.
        3. Desconecta broker.
        4. Envia notificação de desligamento.
        """
        logger.info('Iniciando shutdown...')
        self.running = False

        self.health_monitor.stop_daemon()
        self.telegram.stop_polling()
        # self.dashboard_server.stop()
        try:
            self.broker.disconnect()
            logger.info('Broker desconectado')
        except Exception as e:
            logger.error('Erro ao desconectar broker: %s', e)

        # Notificar via Telegram
        self.telegram.send_alert('🔴 Córtex desligando...')

        logger.info('═══════════════════════════════════════════════════')
        logger.info('   CÓRTEX DESLIGADO')
        logger.info('═══════════════════════════════════════════════════')

    def get_state(self) -> dict[str, Any]:
        """
        Retorna estado completo do sistema para o dashboard WebSocket.

        Returns:
            Dicionário com market_status, portfolio, watchlist,
            recent_decisions, health e uptime.
        """
        self._update_dashboard_state()
        return self.dashboard_state.get_state()

    def _register_signal_handlers(self) -> None:
        """Registra handlers para SIGINT e SIGTERM para shutdown gracioso."""
        def _handler(sig: int, frame: Any) -> None:
            signame = signal.Signals(sig).name
            logger.info('Sinal %s recebido — iniciando shutdown', signame)
            self.stop()
            sys.exit(0)

        try:
            signal.signal(signal.SIGINT, _handler)
            signal.signal(signal.SIGTERM, _handler)
            logger.debug('Signal handlers registrados (SIGINT, SIGTERM)')
        except (OSError, ValueError) as e:
            # Pode falhar em threads não-principais
            logger.warning('Não foi possível registrar signal handlers: %s', e)
    def _register_telegram_commands(self) -> None:
        """Registra os comandos interativos no Telegram."""
        def cmd_status():
            summary = self.portfolio.get_summary()
            msg = f"📊 *Status do Córtex*\n"
            msg += f"💰 *Patrimônio:* R$ {summary.total_value:,.2f}\n"
            msg += f"💵 *Caixa Livre:* R$ {summary.free_cash:,.2f}\n"
            msg += f"📦 *Posições Abertas:* {len(summary.positions)}\n"
            for pos in summary.positions:
                msg += f"  • {pos.ticker}: {pos.quantity}x @ R$ {pos.entry_price:.2f}\n"
            msg += f"\n🚦 *Modo:* {'Simulação' if self.settings.simulation_mode else 'PRODUÇÃO'}"
            return msg

        def cmd_pause():
            if not self.settings.simulation_mode:
                self.settings.simulation_mode = True
                return "⏸ *Córtex Pausado* (Modo Simulação ativado. Novas compras bloqueadas)."
            return "O Córtex já está em modo simulação."

        def cmd_resume():
            if self.settings.simulation_mode:
                return "⚠️ *Atenção:* O Córtex está travado em modo Simulação por diretriz do usuário. Altere o `.env` ou `settings.py` para produzir."
            return "O Córtex já está rodando em produção."

        def cmd_pensamentos():
            try:
                decisions = self.db.get_decisions_today()
                if not decisions:
                    return "🧠 *Pensamentos do Córtex*\n\nAinda não analisei nenhum ativo hoje."
                
                msg = "🧠 *Últimas Análises do Córtex*\n\n"
                # Pega as últimas 5
                for dec in decisions[:5]:
                    emoji = "🟢" if dec['action'] in ("BUY", "COMPRA") else "🔴" if dec['action'] in ("SELL", "VENDA") else "⚪"
                    msg += f"{emoji} *{dec['ticker']}* ({dec['action']})\n"
                    msg += f"Confiança: {dec['confidence']:.0%}\n"
                    if dec.get('reasoning'):
                        reasoning_safe = str(dec['reasoning']).replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
                        msg += f"💡 {reasoning_safe}\n\n"
                
                return msg
            except Exception as e:
                logger.error(f"Erro em cmd_pensamentos: {e}")
                return "❌ Erro ao buscar os pensamentos no banco de dados."

        def cmd_carteira():
            summary = self.portfolio.get_summary()
            if not summary.positions:
                return "💼 *Carteira Vazia*\n\nNenhuma ação comprada no momento."
            
            msg = f"💼 *Sua Carteira de Ações*\n\n"
            for pos in summary.positions:
                price_data = self.market_data.get_current_price(pos.ticker)
                curr_price = price_data.get('last', pos.current_price) if price_data else pos.current_price
                
                profit_loss = (curr_price - pos.entry_price) / pos.entry_price
                emoji = "🟢" if profit_loss > 0 else "🔴" if profit_loss < 0 else "⚪"
                
                msg += f"{emoji} *{pos.ticker}*\n"
                msg += f"  • Quantidade: {pos.quantity} un\n"
                msg += f"  • Preço de Entrada: R$ {pos.entry_price:.2f}\n"
                msg += f"  • Preço Atual: R$ {curr_price:.2f}\n"
                msg += f"  • Rendimento: {profit_loss:+.2%}\n\n"
            return msg

        def cmd_trades():
            try:
                trades = self.db.get_trade_history(days=7)
                if not trades:
                    return "🧾 *Últimos Trades*\n\nNenhuma operação nos últimos 7 dias."
                
                msg = "🧾 *Últimos 5 Trades Realizados*\n\n"
                for t in trades[:5]:
                    emoji = "🛒" if t['action'] == "BUY" else "💸"
                    dt_str = t['timestamp'][:16].replace('T', ' ')
                    msg += f"{emoji} *{t['action']} {t['ticker']}*\n"
                    msg += f"  • {t['quantity']}x @ R$ {t['price']:.2f}\n"
                    msg += f"  • Total: R$ {t['total_value']:.2f}\n"
                    msg += f"  • Data: {dt_str}\n\n"
                return msg
            except Exception as e:
                logger.error(f"Erro em cmd_trades: {e}")
                return "❌ Erro ao buscar o histórico de trades."

        def cmd_mercado():
            is_open = self.scheduler.is_market_open()
            msg = "🏛 *Status do Mercado (B3)*\n\n"
            if is_open:
                msg += "✅ *ABERTO* - O Córtex está analisando ativos ativamente."
            else:
                msg += "🛑 *FECHADO* - O pregão está encerrado ou em leilão.\n"
                msg += "O Córtex está hibernando até a próxima janela operacional."
            return msg

        def cmd_help():
            return (
                "🤖 *Comandos do Córtex IA*\n\n"
                "🔹 `/status` - Resumo rápido: patrimônio, caixa e posições.\n"
                "🔹 `/carteira` - Detalhamento das ações compradas e rendimento atual.\n"
                "🔹 `/trades` - Exibe o histórico das últimas 5 compras/vendas.\n"
                "🔹 `/pensamentos` - Exibe o raciocínio das últimas 5 análises tomadas pela IA.\n"
                "🔹 `/mercado` - Mostra se a B3 está aberta ou fechada para o bot.\n"
                "🔹 `/pause` - Trava o bot em modo Simulação (não emite ordens reais).\n"
                "🔹 `/resume` - Destrava o bot de volta para Produção.\n"
                "🔹 `/help` - Exibe esta mensagem de ajuda."
            )

        self.telegram.register_command('/status', cmd_status)
        self.telegram.register_command('/pause', cmd_pause)
        self.telegram.register_command('/resume', cmd_resume)
        self.telegram.register_command('/pensamentos', cmd_pensamentos)
        self.telegram.register_command('/carteira', cmd_carteira)
        self.telegram.register_command('/trades', cmd_trades)
        self.telegram.register_command('/mercado', cmd_mercado)
        self.telegram.register_command('/help', cmd_help)
