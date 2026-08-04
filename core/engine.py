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
from datetime import datetime ,date
from typing import Any ,Optional

from analysis .decision import DecisionEngine
from analysis .sentiment import SentimentAnalyzer
from analysis .technical import TechnicalAnalyzer
from broker .base import BaseBroker ,OrderStatus
from broker .simulator import SimulatorBroker
from config .settings import Settings
from utils .logger import setup_logger
from core .risk_manager import RiskManager
from core .scheduler import MarketScheduler
from data .database import DatabaseManager
from data .market_data import MarketData
from models .data_models import Action ,BRT ,Decision ,Position
from dashboard .app import DashboardState as WebDashboardState ,DashboardServer
from monitoring .health import HealthMonitor
from notifications .telegram import TelegramNotifier
from portfolio import Portfolio
from data .news_scraper import NewsScraper

logger =logging .getLogger ('cortex.engine')

class CortexEngine :
    """
    Motor principal do Projeto Córtex — orquestrador central.

    Coordena todos os subsistemas: dados de mercado, broker,
    análise, risco, notificações e monitoramento.
    """

    def __init__ (
    self ,
    force_simulation :bool =False ,
    verbose :bool =False ,
    single_cycle :bool =False ,
    )->None :
        """
        Inicializa todos os componentes do sistema.

        Args:
            force_simulation: Se True, força modo simulação independente da config.
            verbose: Se True, ativa logging em nível DEBUG.
            single_cycle: Se True, executa apenas um ciclo e para.
        """

        self .settings =Settings (
        simulation_mode =force_simulation if force_simulation else None ,
        verbose =verbose ,
        )

        setup_logger ('cortex',verbose =verbose )
        logger .info ('═══════════════════════════════════════════════════')
        logger .info ('   PROJETO CÓRTEX — Inicialização')
        logger .info ('═══════════════════════════════════════════════════')
        logger .info ('Modo: %s','SIMULAÇÃO'if self .settings .simulation_mode else 'LIVE')
        logger .info ('Capital inicial: R$ %.2f',self .settings .capital_inicial )
        logger .info ('Stop-loss: %.1f%%',self .settings .stop_loss_percent *100 )

        self .db =DatabaseManager (self .settings .db_path )

        self .market_data =MarketData ()

        if self .settings .simulation_mode or force_simulation :
            self .broker :BaseBroker =SimulatorBroker (
            initial_balance =self .settings .capital_inicial
            )
            logger .info ('Broker: SimulatorBroker')
        else :
            try :
                from broker .mt5_broker import MT5Broker
                self .broker =MT5Broker ()
                logger .info ('Broker: MT5Broker')
            except ImportError :
                logger .warning ('MT5Broker indisponível — usando SimulatorBroker')
                self .broker =SimulatorBroker (
                initial_balance =self .settings .capital_inicial
                )

        self .portfolio =Portfolio (initial_capital =self .settings .capital_inicial )

        self .technical =TechnicalAnalyzer ()
        self .sentiment =SentimentAnalyzer ()

        self .risk_manager =RiskManager (
        stop_loss_percent =self .settings .stop_loss_percent
        )

        self .decision_engine =DecisionEngine (
        technical =self .technical ,
        sentiment =self .sentiment ,
        risk_manager =self .risk_manager ,
        market_data =self .market_data ,
        portfolio =self .portfolio ,
        db =self .db ,
        )

        self .scheduler =MarketScheduler ()

        self .news_scraper =NewsScraper ()

        self .telegram =TelegramNotifier (
        token =self .settings .telegram_token ,
        chat_id =self .settings .telegram_chat_id ,
        db =self .db ,
        )

        self .health_monitor =HealthMonitor (
        telegram =self .telegram ,
        db =self .db ,
        check_interval =self .settings .health_check_interval ,
        alert_cooldown =self .settings .alert_cooldown ,
        )

        self .dashboard_state =WebDashboardState ()

        self .dashboard_server =DashboardServer (
        self .dashboard_state ,
        host =getattr (self .settings ,'dashboard_host','0.0.0.0'),
        port =getattr (self .settings ,'dashboard_port',8080 ),
        )

        self .running :bool =False
        self .single_cycle :bool =single_cycle
        self .last_cycle_time :Optional [datetime ]=None
        self ._start_time :Optional [datetime ]=None
        self ._last_error_alert :dict [str ,float ]={}
        self ._opening_alert_sent_today :bool =False
        self ._closing_report_sent_today :bool =False
        self ._was_market_open :bool =False
        self ._last_alert_date :Optional [date ]=None
        self ._recent_decisions :list [dict [str ,Any ]]=[]

        logger .info ('Todos os componentes inicializados')

    def start (self )->None :
        """
        Inicia o sistema Córtex.

        1. Conecta broker.
        2. Inicializa banco de dados.
        3. Carrega modelo NLP (se modo completo).
        4. Inicia daemon de monitoramento.
        5. Registra handlers de sinal para shutdown gracioso.
        """
        logger .info ('Córtex iniciando...')

        if not self .broker .connect ():
            logger .error ('Falha ao conectar broker — abortando')
            raise RuntimeError ('Falha ao conectar broker')
        logger .info ('Broker conectado')

        logger .info ('Banco de dados pronto')

        if not self .settings .simulation_mode :
            logger .info ('Carregando modelo NLP...')
            if self .sentiment .load_nlp_model ():
                logger .info ('Modelo NLP carregado')
            else :
                logger .warning ('Modelo NLP não disponível — usando análise por keywords')

        self .running =True
        self ._start_time =datetime .now (BRT )

        self .health_monitor .start_daemon ()

        market_open =self .scheduler .is_market_open ()
        self .dashboard_state .update ('market_status','ABERTO'if market_open else 'FECHADO')

        logger .info ('Dashboard standalone em http://0.0.0.0:8003')
        logger .info ('Daemon de monitoramento iniciado')

        self ._register_signal_handlers ()

        logger .info ('═══════════════════════════════════════════════════')
        logger .info ('   CÓRTEX OPERACIONAL')
        logger .info ('═══════════════════════════════════════════════════')

    def run (self )->None :
        """
        Loop principal do sistema.

        Executa continuamente:
        - Verificação de saúde.
        - Ciclos de trading durante horário de mercado.
        - Manutenção durante horário fechado.
        - Alertas de abertura/fechamento.
        """
        logger .info ('Entrando no loop principal')

        while self .running :
            try :

                self .health_monitor .check ()

                self ._reset_daily_flags ()

                if self .scheduler .is_market_open ():
                    self ._was_market_open =True

                    if not self ._opening_alert_sent_today :
                        self ._send_opening_alert ()

                    self ._run_trading_cycle ()

                    if self .single_cycle :
                        logger .info ('Modo single-cycle — encerrando')
                        self .stop ()
                        return

                    time_module .sleep (self .settings .trading_cycle_interval )

                else :

                    if (
                    self ._was_market_open
                    and not self ._closing_report_sent_today
                    ):
                        self ._send_closing_report ()
                        self ._was_market_open =False

                    if self .scheduler .should_run_maintenance ():
                        self ._run_maintenance ()

                    if self .single_cycle :
                        logger .info ('Modo single-cycle (mercado fechado) — encerrando')
                        self .stop ()
                        return

                    time_module .sleep (self .settings .closed_check_interval )

            except KeyboardInterrupt :
                logger .info ('Interrupção do teclado recebida')
                self .stop ()
                return

            except Exception as e :
                logger .error ('Erro no loop principal: %s',e ,exc_info =True )

                error_key =str (e )[:80 ]
                now =time_module .time ()
                last_sent =self ._last_error_alert .get (error_key ,0 )
                if now -last_sent >900 :
                    self .telegram .send_alert (f'🚨 ERRO: {e }')
                    self ._last_error_alert [error_key ]=now
                time_module .sleep (self .settings .trading_cycle_interval )

    def _run_trading_cycle (self )->None :
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
        cycle_start =datetime .now (BRT )
        logger .info ('─── Início do ciclo de trading ───')

        prices =self .market_data .update_prices (self .settings .watchlist )
        self .portfolio .update_prices (prices )
        logger .debug ('Preços atualizados para %d ativos',len (prices ))

        positions =self .portfolio .positions
        if positions :
            triggered =self .risk_manager .check_stop_loss_triggers (
            positions ,self .market_data
            )
            for position in triggered :
                logger .warning (
                '🚨 Executando venda emergencial: %s',position .ticker
                )
                trade =self .broker .emergency_sell (position .ticker )
                if trade is not None and trade .status ==OrderStatus .FILLED :
                    self .portfolio .remove_position (
                    position .ticker ,position .current_price
                    )
                    self .db .insert_trade (
                    ticker =trade .ticker ,
                    action =trade .order_type .value ,
                    quantity =trade .quantity ,
                    price =trade .price ,
                    total_value =trade .total_value ,
                    stop_loss =trade .stop_loss ,
                    is_simulated =self .settings .simulation_mode ,
                    )
                    reason =(
                    f'Preço R$ {position .current_price :.2f} <= '
                    f'Stop-loss R$ {position .stop_loss :.2f} '
                    f'(entrada R$ {position .entry_price :.2f})'
                    )
                    self .telegram .send_emergency_alert (position .ticker ,reason )

        news =self .news_scraper .fetch_all_news ()
        logger .debug ('Notícias coletadas: %d itens',len (news ))

        for ticker in self .settings .watchlist :
            try :
                decision =self .decision_engine .evaluate (
                ticker =ticker ,
                news_items =news ,
                )

                self .db .insert_decision (
                ticker =decision .ticker ,
                action =decision .action .value ,
                confidence =decision .confidence ,
                sentiment_score =decision .sentiment_score ,
                reasoning =decision .reasoning [:500 ]if decision .reasoning else None ,
                )

                self ._recent_decisions .append ({
                'ticker':decision .ticker ,
                'action':decision .action .value ,
                'confidence':decision .confidence ,
                'reasoning':(decision .reasoning [:200 ]if decision .reasoning else ''),
                'timestamp':decision .timestamp .isoformat (),
                })

                self ._recent_decisions =self ._recent_decisions [-50 :]

                if decision .action ==Action .BUY :
                    self ._execute_buy (decision )
                elif decision .action in (Action .SELL ,Action .EMERGENCY_SELL ):
                    self ._execute_sell (decision )

            except Exception as e :
                logger .error ('Erro ao avaliar %s: %s',ticker ,e ,exc_info =True )

        summary =self .portfolio .get_summary ()
        self .risk_manager .update_daily_pnl (summary .total_pnl_percent /100.0 )
        if self .risk_manager .is_circuit_breaker_active :
            self .telegram .send_alert (
            '🛑 CIRCUIT BREAKER: Perda diária excede limite. '
            'Operações suspensas até amanhã.'
            )

        self ._check_volatility (self .portfolio .positions )

        self ._update_dashboard_state ()

        self .last_cycle_time =datetime .now (BRT )
        elapsed =(self .last_cycle_time -cycle_start ).total_seconds ()
        logger .info ('─── Ciclo concluído em %.1fs ───',elapsed )

    def _execute_buy (self ,decision :Decision )->None :
        """Executa ordem de compra baseada na decisão."""

        if self .risk_manager .is_circuit_breaker_active :
            logger .warning ('Compra de %s bloqueada — circuit breaker ativo',decision .ticker )
            return

        summary =self .portfolio .get_summary ()
        is_valid ,reason =self .risk_manager .validate_order (
        ticker =decision .ticker ,
        quantity =decision .quantity ,
        price =decision .price ,
        available_capital =self .portfolio .free_cash ,
        total_portfolio_value =summary .total_value ,
        )

        if not is_valid :
            logger .warning ('Compra rejeitada para %s: %s',decision .ticker ,reason )
            return

        trade =self .broker .buy (
        ticker =decision .ticker ,
        quantity =decision .quantity ,
        price =decision .price ,
        stop_loss =decision .stop_loss ,
        )

        if trade is not None and trade .status ==OrderStatus .FILLED :

            position =Position (
            ticker =decision .ticker ,
            quantity =decision .quantity ,
            entry_price =decision .price ,
            stop_loss =decision .stop_loss ,
            current_price =decision .price ,
            )
            self .portfolio .add_position (position )
            self .db .insert_trade (
            ticker =trade .ticker ,
            action =trade .order_type .value ,
            quantity =trade .quantity ,
            price =trade .price ,
            total_value =trade .total_value ,
            stop_loss =trade .stop_loss ,
            is_simulated =self .settings .simulation_mode ,
            )
            self .telegram .send_trade_alert (
            decision ,simulation =self .settings .simulation_mode
            )
            logger .info (
            '✅ COMPRA executada: %s — %d ações @ R$ %.2f',
            decision .ticker ,decision .quantity ,decision .price ,
            )

    def _execute_sell (self ,decision :Decision )->None :
        """Executa ordem de venda baseada na decisão."""
        trade =self .broker .sell (
        ticker =decision .ticker ,
        quantity =decision .quantity ,
        price =decision .price ,
        )

        if trade is not None and trade .status ==OrderStatus .FILLED :
            self .portfolio .remove_position (decision .ticker ,decision .price )
            self .db .insert_trade (
            ticker =trade .ticker ,
            action =trade .order_type .value ,
            quantity =trade .quantity ,
            price =trade .price ,
            total_value =trade .total_value ,
            is_simulated =self .settings .simulation_mode ,
            )
            self .telegram .send_trade_alert (
            decision ,simulation =self .settings .simulation_mode
            )
            logger .info (
            '✅ VENDA executada: %s — %d ações @ R$ %.2f',
            decision .ticker ,decision .quantity ,decision .price ,
            )

    def _run_maintenance (self )->None :
        """
        Executa ciclo de manutenção.

        1. Atualiza dados históricos OHLCV.
        2. Recalibra indicadores técnicos.
        3. Salva snapshots de mercado.
        4. Registra log de manutenção.
        """
        logger .info ('─── Início do ciclo de manutenção ───')

        for ticker in self .settings .watchlist :
            try :

                df =self .market_data .get_ohlcv (ticker ,period ='3mo',interval ='1d')
                if df is not None and not df .empty :
                    last_close =df ['Close'].iloc [-1 ]
                    logger .debug (
                    'Manutenção %s: %d candles carregados, último fechamento: R$%.2f',
                    ticker ,len (df ),last_close ,
                    )
            except Exception as e :
                logger .error ('Erro na manutenção de %s: %s',ticker ,e )

        logger .info ('─── Manutenção concluída ───')

    def _send_opening_alert (self )->None :
        """Envia alerta de abertura do mercado via Telegram."""
        summary =self .portfolio .get_summary ()
        self .telegram .send_market_open_alert (
        portfolio_summary =summary ,
        simulation =self .settings .simulation_mode ,
        )
        self ._opening_alert_sent_today =True
        logger .info ('Alerta de abertura enviado')

    def _send_closing_report (self )->None :
        """Calcula estatísticas diárias e envia relatório de fechamento."""
        trades_today =self .db .get_trades_today ()
        summary =self .portfolio .get_summary ()

        buys =sum (1 for t in trades_today if t .get ('action')=='COMPRA')
        sells =sum (1 for t in trades_today if t .get ('action')=='VENDA')

        report ={
        'date':datetime .now (BRT ).strftime ('%Y-%m-%d'),
        'total_buys':buys ,
        'total_sells':sells ,
        'free_cash':summary .free_cash ,
        'allocated_capital':summary .allocated_capital ,
        'total_value':summary .total_value ,
        'pnl_percent':summary .total_pnl_percent ,
        'initial_capital':self .settings .capital_inicial ,
        }

        self .telegram .send_closing_report (
        report =report ,
        simulation =self .settings .simulation_mode ,
        )
        self ._closing_report_sent_today =True
        try :
            self .db .insert_daily_report (
            report_date =report ['date'],
            buys_count =buys ,
            sells_count =sells ,
            free_cash =summary .free_cash ,
            allocated_capital =summary .allocated_capital ,
            initial_capital =self .settings .capital_inicial ,
            total_equity =summary .total_value ,
            pnl_percent =summary .total_pnl_percent ,
            )
        except Exception as e :
            logger .error ('Erro ao salvar relatório diário no DB: %s',e )
        logger .info ('Relatório de fechamento enviado')

    def _check_volatility (self ,positions :list [Position ])->None :
        """
        Verifica variações de preço significativas nas posições mantidas.

        Envia alerta se variação > VOLATILITY_ALERT_THRESHOLD%.

        Args:
            positions: Lista de posições abertas.
        """
        for position in positions :
            variation =self .market_data .get_variation (position .ticker )
            if variation is not None and abs (variation )>=self .settings .volatility_alert_threshold :
                old_price =position .entry_price
                new_price =position .current_price

                thinking =(
                f'Variação de {variation :+.2f}% detectada em {position .ticker }. '
                f'{"Movimento a favor da posição."if variation >0 else "Risco elevado — monitorando stop-loss."}'
                )

                self .telegram .send_volatility_alert (
                ticker =position .ticker ,
                old_price =old_price ,
                new_price =new_price ,
                variation_pct =variation ,
                thinking =thinking ,
                )
                logger .info (
                'Alerta de volatilidade enviado: %s %+.2f%%',
                position .ticker ,variation ,
                )

    def _reset_daily_flags (self )->None :
        """Reseta flags diárias ao mudar de dia."""
        today =datetime .now (BRT ).date ()
        if self ._last_alert_date !=today :
            self ._opening_alert_sent_today =False
            self ._closing_report_sent_today =False
            self ._last_alert_date =today

            self .risk_manager .reset_daily_limits ()
            logger .debug ('Flags diárias resetadas para %s',today )

    def _update_dashboard_state (self )->None :
        """Atualiza o estado compartilhado para o dashboard WebSocket."""
        summary =self .portfolio .get_summary ()
        health =self .health_monitor .get_status ()

        self .dashboard_state .update (
        'market_status',
        'ABERTO'if self .scheduler .is_market_open ()else 'FECHADO',
        )

        positions_data =[
        {
        'ticker':p .ticker ,
        'quantity':p .quantity ,
        'entry_price':p .entry_price ,
        'current_price':p .current_price ,
        'pnl':p .pnl ,
        'pnl_percent':p .pnl_percent ,
        }
        for p in summary .positions
        ]

        self .dashboard_state .update ('portfolio',{
        'total_value':summary .total_value ,
        'free_cash':summary .free_cash ,
        'allocated_capital':summary .allocated_capital ,
        'total_equity':summary .total_value ,
        'initial_capital':self .settings .capital_inicial ,
        'pnl':summary .total_pnl ,
        'pnl_percent':summary .total_pnl_percent ,
        'positions':positions_data ,
        })

        watchlist_prices ={}
        for ticker in self .settings .watchlist :
            try :
                price_data =self .market_data .get_current_price (ticker )
                current_price =price_data .get ('last',0.0 )if isinstance (price_data ,dict )else (price_data or 0.0 )
            except Exception :
                current_price =self .market_data ._price_cache_legacy .get (ticker ,0.0 )
                logger .debug ('Preço indisponível para %s, usando cache: %.2f',ticker ,current_price )
            watchlist_prices [ticker ]=current_price
        self .dashboard_state .update ('watchlist',[
        {'ticker':t ,'price':p }for t ,p in watchlist_prices .items ()
        ])

        self .dashboard_state .update ('recent_decisions',self ._recent_decisions [-20 :])
        self .dashboard_state .update ('health',health )

        if self ._start_time is not None :
            uptime =(datetime .now (BRT )-self ._start_time ).total_seconds ()
            self .dashboard_state .update ('uptime_seconds',uptime )

    def stop (self )->None :
        """
        Para o sistema de forma graciosa.

        1. Define flag running = False.
        2. Para daemon de monitoramento.
        3. Desconecta broker.
        4. Envia notificação de desligamento.
        """
        logger .info ('Córtex desligando...')
        self .running =False

        self .health_monitor .stop_daemon ()

        try :
            self .broker .disconnect ()
            logger .info ('Broker desconectado')
        except Exception as e :
            logger .error ('Erro ao desconectar broker: %s',e )

        self .telegram .send_alert ('🔴 Córtex desligando...')

        logger .info ('═══════════════════════════════════════════════════')
        logger .info ('   CÓRTEX DESLIGADO')
        logger .info ('═══════════════════════════════════════════════════')

    def get_state (self )->dict [str ,Any ]:
        """
        Retorna estado completo do sistema para o dashboard WebSocket.

        Returns:
            Dicionário com market_status, portfolio, watchlist,
            recent_decisions, health e uptime.
        """
        self ._update_dashboard_state ()
        return self .dashboard_state .get_state ()

    def _register_signal_handlers (self )->None :
        """Registra handlers para SIGINT e SIGTERM para shutdown gracioso."""
        def _handler (sig :int ,frame :Any )->None :
            signame =signal .Signals (sig ).name
            logger .info ('Sinal %s recebido — iniciando shutdown',signame )
            self .stop ()
            sys .exit (0 )

        try :
            signal .signal (signal .SIGINT ,_handler )
            signal .signal (signal .SIGTERM ,_handler )
            logger .debug ('Signal handlers registrados (SIGINT, SIGTERM)')
        except (OSError ,ValueError )as e :

            logger .warning ('Não foi possível registrar signal handlers: %s',e )
