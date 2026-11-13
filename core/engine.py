"""
Motor principal do Projeto Córtex.

Orquestra todos os componentes do sistema de trading autônomo:
dados de mercado, broker, análise técnica/sentimento, gerenciamento
de risco, notificações, controle de concorrência e monitoramento de saúde.
"""

from __future__ import annotations

import html
import inspect
import json
import logging
import platform
import signal
import threading
import time as time_module
from dataclasses import replace as dc_replace
from datetime import date, datetime, time
from typing import Any, Callable, Optional

from analysis.decision import DecisionEngine
from analysis.sentiment import SentimentAnalyzer
from analysis.technical import TechnicalAnalyzer
from broker.base import BaseBroker, OrderStatus
from broker.simulator import SimulatorBroker
from config.settings import Settings
from core.risk_manager import RiskManager
from core.scheduler import MarketScheduler
from dashboard.app import DashboardServer, DashboardState as WebDashboardState
from data.database import DatabaseManager
from data.market_data import MarketData
from data.news_scraper import NewsScraper
from models.data_models import Action, Decision, Position
from monitoring.health import HealthMonitor
from notifications.telegram import TelegramNotifier
from portfolio import Portfolio
from utils.helpers import BRT, normalize_b3_ticker
from utils.logger import flush_log_handlers, get_logger, setup_logger

logger = get_logger('cortex.engine')


def _call_with_supported_kwargs(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Invoca `func` filtrando kwargs opcionais caso a assinatura não aceite `**kwargs`."""
    try:
        sig = inspect.signature(func)
        has_var_kw = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
        if has_var_kw:
            return func(*args, **kwargs)
        accepted = {
            k: v
            for k, v in kwargs.items()
            if k in sig.parameters
        }
        return func(*args, **accepted)
    except (ValueError, TypeError):
        return func(*args, **kwargs)


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
        self._engine_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._stop_requested: bool = False
        self._stop_signal_num: Optional[int] = None
        self._stopped: bool = False

        self.settings = Settings(
            simulation_mode=force_simulation if force_simulation else None,
            verbose=verbose,
        )

        setup_logger('cortex', verbose=verbose)
        setup_logger('cortex.engine', verbose=verbose)

        self.db = DatabaseManager(self.settings.db_path)
        self.settings.reload_dynamic_config(db=self.db, reload_environment=False)

        logger.info('═══════════════════════════════════════════════════')
        logger.info('   PROJETO CÓRTEX — Inicialização')
        logger.info('═══════════════════════════════════════════════════')

        self.market_data = MarketData()

        shutdown_status = self.db.get_config("shutdown_status")
        last_env = self.db.get_config("last_environment")
        self._recovered_from_crash: bool = False
        self._prod_broker_unavailable_on_boot: bool = False

        if force_simulation:
            effective_env = "homologation"
        elif shutdown_status in ("running", "crashed"):
            effective_env = "homologation"
            self._recovered_from_crash = True
            logger.warning(
                "⚠️ ALERTA: Reinicialização após desligamento inesperado (%s). Forçando HOMOLOGAÇÃO.",
                shutdown_status,
            )
        elif shutdown_status == "graceful" and last_env:
            cand = last_env.strip().lower()
            effective_env = cand if cand in ("homologation", "production") else "homologation"
            logger.info(
                "Último desligamento foi correto (graceful). Restaurando ambiente: %s",
                effective_env.upper(),
            )
        elif shutdown_status is None:
            effective_env = "homologation"
            if last_env and last_env.strip().lower() == "production":
                self._recovered_from_crash = True
                logger.warning(
                    "⚠️ ALERTA: Estado de shutdown ausente com último ambiente PRODUÇÃO. Forçando HOMOLOGAÇÃO."
                )
        else:
            effective_env = "homologation" if self.settings.simulation_mode else "production"

        sim_cap = float(
            self.db.get_config("sim_initial_capital", str(self.settings.CAPITAL_INICIAL))
        )
        self.settings.CAPITAL_INICIAL = sim_cap
        self.sim_portfolio = Portfolio(initial_capital=sim_cap, is_simulated=True)
        self.sim_broker = SimulatorBroker(initial_balance=sim_cap)

        prod_ai_cap = float(self.db.get_config("prod_ai_allocated_capital", "0.0"))
        prod_vault = float(self.db.get_config("prod_vault_reserve_cash", "0.0"))
        prod_bal_cfg = self.db.get_config("prod_broker_account_balance")
        prod_free_init = float(prod_bal_cfg) if prod_bal_cfg is not None else (prod_ai_cap + prod_vault)
        self.prod_portfolio = Portfolio(initial_capital=prod_ai_cap, is_simulated=False)
        self.prod_portfolio.set_free_cash(max(0.0, prod_free_init))
        self.prod_portfolio.ai_allocated_capital = max(0.0, prod_ai_cap)
        self.prod_portfolio.vault_reserve_cash = max(0.0, prod_vault)

        self.prod_broker: Optional[BaseBroker] = self._create_production_broker()

        if effective_env == "production" and self.prod_broker is None:
            logger.critical(
                "🚨 FAIL-SAFE: Ambiente PRODUÇÃO solicitado, mas MetaTrader 5 / Bridge "
                "está indisponível neste host. Revertendo imediatamente para HOMOLOGAÇÃO."
            )
            effective_env = "homologation"
            self._prod_broker_unavailable_on_boot = True

        is_sim = (effective_env == "homologation")
        self.settings.SIMULATION_MODE = is_sim
        self.settings.BROKER_MODE = "simulator" if is_sim else "mt5"
        self.db.set_config("environment", effective_env)
        self.db.set_config("shutdown_status", "running")

        if is_sim:
            self.broker: BaseBroker = self.sim_broker
            self.portfolio: Portfolio = self.sim_portfolio
            logger.info('Broker Ativo: SimulatorBroker (Homologação)')
        else:
            assert self.prod_broker is not None
            self.broker = self.prod_broker
            self.portfolio = self.prod_portfolio
            logger.info('Broker Ativo: Produção (MT5 / Bridge Real)')

        logger.info('Modo: %s', 'HOMOLOGAÇÃO' if self.settings.simulation_mode else 'PRODUÇÃO')
        logger.info('Capital inicial (Homologação): R$ %.2f', self.settings.CAPITAL_INICIAL)
        logger.info('Stop-loss: %.1f%%', self.settings.STOP_LOSS_PERCENT * 100)

        self.technical = TechnicalAnalyzer()
        self.sentiment = SentimentAnalyzer(mode=self.settings.SENTIMENT_MODE)

        self.risk_manager = RiskManager(
            stop_loss_percent=self.settings.STOP_LOSS_PERCENT,
        )
        self.risk_manager.update_parameters(
            stop_loss_percent=self.settings.STOP_LOSS_PERCENT,
            trailing_trigger_percent=self.settings.TRAILING_STOP_TRIGGER_PERCENT,
            trailing_distance_percent=self.settings.TRAILING_STOP_DISTANCE_PERCENT,
        )

        self.decision_engine = DecisionEngine(
            technical=self.technical,
            sentiment=self.sentiment,
            risk_manager=self.risk_manager,
            market_data=self.market_data,
            portfolio=self.portfolio,
            db=self.db,
        )

        self.scheduler = MarketScheduler()
        self.news_scraper = NewsScraper()

        self.telegram = TelegramNotifier(
            token=self.settings.TELEGRAM_TOKEN,
            chat_id=self.settings.TELEGRAM_CHAT_ID,
            db=self.db,
            channel_id=self.settings.TELEGRAM_CHANNEL_ID,
        )

        self.health_monitor = HealthMonitor(
            telegram=self.telegram,
            db=self.db,
            check_interval=self.settings.health_check_interval,
            alert_cooldown=self.settings.alert_cooldown,
        )

        self.dashboard_state = WebDashboardState()
        self.dashboard_state._engine = self
        WebDashboardState._engine = self

        self.dashboard_server = DashboardServer(
            self.dashboard_state,
            host=getattr(self.settings, 'DASHBOARD_HOST', getattr(self.settings, 'dashboard_host', '0.0.0.0')),
            port=getattr(self.settings, 'DASHBOARD_PORT', getattr(self.settings, 'dashboard_port', 8003)),
        )

        self.running: bool = False
        self.single_cycle: bool = single_cycle
        self.last_cycle_time: Optional[datetime] = None
        self._start_time: Optional[datetime] = None
        self._last_error_alert: dict[str, float] = {}
        self._last_volatility_alert: dict[str, float] = {}
        self._opening_alert_sent_today: bool = False
        self._closing_report_sent_today: bool = False
        self._was_market_open: bool = False
        self._last_alert_date: Optional[date] = None
        self._recent_decisions: list[dict[str, Any]] = []
        self._completed_maintenance_windows: set[tuple[date, time]] = set()

        self._start_of_day_equity: dict[str, Optional[float]] = {
            "homologation": None,
            "production": None,
        }
        self._circuit_breaker_alerted: dict[str, bool] = {
            "homologation": False,
            "production": False,
        }
        self._env_circuit_breaker_active: dict[str, bool] = {
            "homologation": False,
            "production": False,
        }
        self._env_daily_pnl: dict[str, float] = {
            "homologation": 0.0,
            "production": 0.0,
        }
        self._sold_today_by_env: dict[str, set[str]] = {
            "homologation": set(),
            "production": set(),
        }
        self._next_prod_broker_probe_ts: float = 0.0

        logger.info('Todos os componentes inicializados')

    def _create_production_broker(self) -> Optional[BaseBroker]:
        """
        Instancia exclusivamente um broker real (RestMT5Broker ou MT5Broker nativo).
        Jamais substitui por SimulatorBroker em Produção.
        """
        bridge_url = str(getattr(self.settings, "MT5_BRIDGE_URL", "") or "").strip()
        if bridge_url:
            try:
                from broker.rest_mt5_broker import RestMT5Broker
                logger.info("RestMT5Broker configurado com URL da Bridge: %s", bridge_url)
                return RestMT5Broker(base_url=bridge_url)
            except Exception as exc:
                logger.error("Falha ao inicializar RestMT5Broker (%s): %s", bridge_url, exc)
                return None

        if platform.system() == "Windows":
            try:
                import MetaTrader5  # noqa: F401
                from broker.mt5_broker import MT5Broker
                logger.info("MT5Broker nativo configurado (Windows)")
                return MT5Broker()
            except Exception as exc:
                logger.warning("MetaTrader5 nativo indisponível neste ambiente Windows: %s", exc)
                return None

        logger.info(
            "Host %s sem MT5_BRIDGE_URL configurada — broker real de produção indisponível.",
            platform.system(),
        )
        return None

    def request_stop(self, sig: Optional[int] = None) -> None:
        """
        Sinaliza parada do loop principal de forma segura para signal handlers,
        sem adquirir locks pesados, tocar atributos privados de `Event` ou abortar ordens.
        """
        if sig is not None:
            self._stop_signal_num = sig
        self._stop_requested = True
        self.running = False

    def _is_stop_requested(self) -> bool:
        """Verifica solicitação de parada e sincroniza `_stop_event` fora do signal frame."""
        if self._stop_requested or self._stop_event.is_set():
            if self._stop_requested and not self._stop_event.is_set():
                if self._stop_signal_num is not None:
                    sig = self._stop_signal_num
                    self._stop_signal_num = None
                    try:
                        signame = signal.Signals(sig).name
                    except ValueError:
                        signame = str(sig)
                    logger.info('Sinal %s recebido — parada graciosa solicitada', signame)
                self._stop_event.set()
            return True
        return False

    def _wait_interruptible(self, timeout_sec: float) -> bool:
        """
        Aguarda até `timeout_sec` em fatias curtas verificando `_stop_requested` e `_stop_event`.
        Retorna True caso a parada tenha sido solicitada.
        """
        deadline = time_module.monotonic() + max(0.0, float(timeout_sec))
        while True:
            if self._is_stop_requested() or not self.running:
                return True
            remaining = deadline - time_module.monotonic()
            if remaining <= 0:
                return False
            self._stop_event.wait(timeout=min(1.0, remaining))

    def start(self) -> None:
        """
        Inicia o sistema Córtex:
        1. Conecta o broker ativo (com fail-safe estrito para Homologação se Produção falhar).
        2. Sincroniza carteira com broker.
        3. Carrega modelo NLP (se modo full).
        4. Inicia HealthMonitor, Dashboard e Telegram Bot.
        5. Registra handlers de sinal não-bloqueantes.
        """
        logger.info('Córtex iniciando...')
        if self._stopped:
            self._stopped = False
            self._stop_requested = False
            self._stop_signal_num = None
        self._stop_event.clear()

        if self._is_stop_requested():
            logger.info('Parada solicitada antes da inicialização — abortando start().')
            self.stop(graceful=True)
            return

        if not self.settings.simulation_mode:
            connected = False
            if self.prod_broker is not None:
                try:
                    connected = bool(self.prod_broker.connect())
                except Exception as exc:
                    logger.error("Falha na conexão do broker de produção: %s", exc)
                    connected = False

            if not connected:
                logger.critical(
                    "🚨 FAIL-SAFE DE PRODUÇÃO: Não foi possível conectar ao MetaTrader 5 / Bridge. "
                    "Revertendo imediatamente para HOMOLOGAÇÃO."
                )
                self._prod_broker_unavailable_on_boot = True
                self.settings.SIMULATION_MODE = True
                self.settings.BROKER_MODE = "simulator"
                self.broker = self.sim_broker
                self.portfolio = self.sim_portfolio
                self.decision_engine.portfolio = self.sim_portfolio
                self.db.set_config("environment", "homologation")
                self.sim_broker.connect()
        else:
            self.sim_broker.connect()

        logger.info('Broker conectado (%s)', self.broker.__class__.__name__)

        if self._is_stop_requested():
            logger.info('Parada solicitada durante conexão do broker — abortando start().')
            self.stop(graceful=True)
            return

        self._sync_portfolio_with_broker()
        logger.info('Banco de dados e carteira sincronizados')

        if self.sentiment.mode == 'full':
            logger.info('Carregando modelo NLP (FinBERT)...')
            try:
                self.sentiment._load_finbert()
                logger.info('Modelo NLP carregado')
            except ImportError:
                logger.warning('Modelo NLP não disponível — usando análise por keywords')

        if self._is_stop_requested():
            logger.info('Parada solicitada durante inicialização — abortando start().')
            self.stop(graceful=True)
            return

        self.running = True
        self._start_time = datetime.now(BRT)

        self.health_monitor.start_daemon()

        try:
            self.dashboard_server.start()
            logger.info(
                'Dashboard web iniciado em http://%s:%d',
                self.dashboard_server.host,
                self.dashboard_server.port,
            )
        except Exception as e:
            logger.warning('Não foi possível iniciar dashboard server: %s', e)

        logger.info('Daemon de monitoramento iniciado')

        self._register_telegram_commands()
        self.telegram.start_polling()

        def _initial_news_fetch() -> None:
            try:
                items = self.news_scraper.fetch_all_news()
                to_persist = (
                    self.news_scraper.get_unpersisted_news()
                    if isinstance(self.news_scraper, NewsScraper)
                    else items
                )
                for item in to_persist:
                    for ticker in (item.tickers_mentioned or [None]):
                        self.db.insert_news(
                            title=item.title,
                            source=item.source,
                            url=item.url,
                            ticker=ticker,
                            published_at=item.published_at.isoformat() if item.published_at else None,
                        )
                logger.info("Radar de Notícias: %d notícias financeiras carregadas no banco", len(to_persist))
            except Exception as e:
                logger.debug("Falha na coleta inicial de notícias: %s", e)

        threading.Thread(target=_initial_news_fetch, daemon=True, name="NewsFetchInit").start()

        self._register_signal_handlers()

        logger.info('═══════════════════════════════════════════════════')
        logger.info('   CÓRTEX OPERACIONAL')
        logger.info('═══════════════════════════════════════════════════')

        mode_str = "Simulação (Homologação)" if self.settings.simulation_mode else "PRODUÇÃO"
        self.telegram.send_to_channel(f"🚀 *Córtex Iniciado!*\nModo: {mode_str}\nStatus: Operacional")

        if self._recovered_from_crash:
            try:
                self.telegram.send_alert(
                    "⚠️ *ALERTA DE SEGURANÇA — CÓRTEX IA*\n\n"
                    "O servidor foi reiniciado após um *desligamento inesperado, crash ou queda de energia*.\n\n"
                    "🛡️ Por segurança patrimonial, o sistema foi inicializado no modo *HOMOLOGAÇÃO*.\n"
                    "Acesse o Dashboard caso deseje reativar a Produção."
                )
            except Exception:
                pass

        if self._prod_broker_unavailable_on_boot:
            try:
                self.telegram.send_alert(
                    "🚨 *FAIL-SAFE CRÍTICO — BROKER DE PRODUÇÃO INDISPONÍVEL*\n\n"
                    "O Córtex tentou iniciar no ambiente de *PRODUÇÃO*, porém o terminal "
                    "*MetaTrader 5 / Bridge* não está acessível ou falhou ao autenticar.\n\n"
                    "🛡️ O ambiente foi revertido automaticamente para *HOMOLOGAÇÃO* "
                    "(nenhuma custódia falsa foi instanciada)."
                )
            except Exception:
                pass

    def run(self) -> None:
        """
        Loop principal do sistema com espera responsiva (`_wait_interruptible`)
        e encerramento gracioso garantido ao sair do loop.
        """
        logger.info('Entrando no loop principal')

        while self.running and not self._is_stop_requested():
            try:
                self.health_monitor.check()
                self._reset_daily_flags()

                if self.scheduler.is_market_open():
                    self._was_market_open = True

                    if not self._opening_alert_sent_today:
                        self._send_opening_alert()

                    self._run_trading_cycle()

                    if self.single_cycle:
                        logger.info('Modo single-cycle — encerrando')
                        break

                    self._wait_interruptible(float(self.settings.trading_cycle_interval))

                else:
                    if (
                        self._was_market_open
                        and not self._closing_report_sent_today
                    ):
                        self._send_closing_report()
                        self._was_market_open = False

                    if self.scheduler.should_run_maintenance():
                        win_getter = getattr(self.scheduler, 'get_active_maintenance_window', None)
                        active_win = win_getter() if callable(win_getter) else None
                        if not isinstance(active_win, tuple) or active_win not in self._completed_maintenance_windows:
                            self._run_maintenance()
                            if isinstance(active_win, tuple):
                                today_d = active_win[0]
                                self._completed_maintenance_windows = {
                                    w for w in self._completed_maintenance_windows if w[0] == today_d
                                }
                                self._completed_maintenance_windows.add(active_win)
                            mark_fn = getattr(self.scheduler, 'mark_maintenance_done', None)
                            if callable(mark_fn):
                                mark_fn()

                    if self.single_cycle:
                        logger.info('Modo single-cycle (mercado fechado) — encerrando')
                        break

                    self._wait_interruptible(float(self.settings.closed_check_interval))

            except KeyboardInterrupt:
                logger.info('Interrupção do teclado recebida no loop principal')
                break

            except Exception as e:
                logger.error('Erro no loop principal: %s', e, exc_info=True)
                error_key = str(e)[:80]
                now = time_module.time()
                last_sent = self._last_error_alert.get(error_key, 0.0)
                if now - last_sent > 900:
                    try:
                        self.telegram.send_alert(f'🚨 ERRO NO LOOP PRINCIPAL: {e}')
                    except Exception:
                        pass
                    self._last_error_alert[error_key] = now
                self._wait_interruptible(float(self.settings.trading_cycle_interval))

        self.stop(graceful=True)

    def _insert_trade_safe(
        self,
        *,
        ticker: str,
        action: str,
        quantity: int,
        price: float,
        total_value: float,
        stop_loss: Optional[float] = None,
        reasoning: str = "",
        is_simulated: Optional[bool] = None,
    ) -> None:
        """Registra trade no banco com compatibilidade de assinatura e contexto completo."""
        sim_flag = self.settings.simulation_mode if is_simulated is None else bool(is_simulated)
        env_tag = "homologation" if sim_flag else "production"
        norm_action = "BUY" if str(action).upper() in ("BUY", "COMPRA") else "SELL"
        _call_with_supported_kwargs(
            self.db.insert_trade,
            ticker=ticker,
            action=norm_action,
            quantity=quantity,
            price=price,
            total_value=total_value,
            stop_loss=stop_loss,
            reasoning=reasoning,
            is_simulated=sim_flag,
            environment=env_tag,
        )

    @staticmethod
    def _is_same_day_position(pos: Position, now_brt: datetime) -> bool:
        """Retorna True se a posição foi aberta na data de pregão atual (BRT)."""
        entry_dt = getattr(pos, "entry_time", None) or getattr(pos, "timestamp", None)
        if not isinstance(entry_dt, datetime):
            return False
        pos_date = entry_dt.astimezone(BRT).date() if entry_dt.tzinfo else entry_dt.date()
        ref_date = now_brt.astimezone(BRT).date() if now_brt.tzinfo else now_brt.date()
        return pos_date == ref_date

    def _mark_sold_today(self, ticker: str, is_simulated: Optional[bool] = None) -> None:
        """Registra em memória que `ticker` teve venda executada hoje no ambiente correspondente."""
        norm = normalize_b3_ticker(ticker)
        if not norm:
            return
        sim_flag = self.settings.simulation_mode if is_simulated is None else bool(is_simulated)
        env_key = "homologation" if sim_flag else "production"
        self._sold_today_by_env.setdefault(env_key, set()).add(norm)

    def _load_prod_partial_exit_map(self) -> dict[str, bool]:
        """Carrega mapa JSON `prod_partial_exit_done` persistido em `system_config`."""
        try:
            raw = self.db.get_config("prod_partial_exit_done")
            if not raw:
                return {}
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {
                    normalize_b3_ticker(str(k)): True
                    for k, v in parsed.items()
                    if v and normalize_b3_ticker(str(k))
                }
        except Exception:
            pass
        return {}

    def _save_prod_partial_exit_map(self, mapping: dict[str, bool]) -> None:
        """Persiste mapa JSON `prod_partial_exit_done` em `system_config`."""
        try:
            clean = {
                normalize_b3_ticker(k): True
                for k, v in sorted(mapping.items())
                if v and normalize_b3_ticker(k)
            }
            self.db.set_config("prod_partial_exit_done", json.dumps(clean))
        except Exception as exc:
            logger.debug("Aviso ao persistir prod_partial_exit_done: %s", exc)

    def _set_prod_partial_exit_flag(self, ticker: str, done: bool) -> None:
        """Atualiza flag persistida de realização parcial de `ticker` em Produção."""
        if self.settings.simulation_mode:
            return
        norm = normalize_b3_ticker(ticker)
        if not norm:
            return
        current = self._load_prod_partial_exit_map()
        if done:
            if not current.get(norm):
                current[norm] = True
                self._save_prod_partial_exit_map(current)
        elif norm in current:
            current.pop(norm, None)
            self._save_prod_partial_exit_map(current)

    def _set_start_of_day_baseline(
        self,
        env_key: str,
        portfolio: Portfolio,
        equity_val: Optional[float],
    ) -> None:
        """Define o baseline diário de patrimônio do ambiente sincronizando o acumulador manual."""
        self._start_of_day_equity[env_key] = (
            float(equity_val) if (equity_val is not None and equity_val > 0) else None
        )
        portfolio._cb_synced_manual_delta = float(  # type: ignore[attr-defined]
            getattr(portfolio, "_manual_capital_delta_accum", 0.0) or 0.0
        )

    def _sync_manual_capital_delta_to_sod(self, env_key: str, portfolio: Portfolio) -> None:
        """
        Ajusta `_start_of_day_equity[env_key]` por movimentações manuais entre Cofre e IA
        para que aportes/saques manuais jamais disparem falso Circuit Breaker (`[ENG-12]`).
        """
        curr_accum = float(getattr(portfolio, "_manual_capital_delta_accum", 0.0) or 0.0)
        synced_accum = float(getattr(portfolio, "_cb_synced_manual_delta", curr_accum) or 0.0)
        delta = curr_accum - synced_accum
        portfolio._cb_synced_manual_delta = curr_accum  # type: ignore[attr-defined]
        sod = self._start_of_day_equity.get(env_key)
        if sod is not None and sod > 0 and abs(delta) > 1e-9:
            new_sod = sod + delta
            self._start_of_day_equity[env_key] = new_sod if new_sod > 0 else None

    def _record_production_realized_pnl(
        self,
        ticker: str,
        pnl: float,
        reason: str,
        entry_price: float = 0.0,
        exit_price: float = 0.0,
        quantity: int = 0,
        force_production: bool = False,
    ) -> float:
        """
        Persiste auto-compounding líquido de taxas B3 da Produção no banco e registra
        em `capital_transactions`.
        """
        fee_rate = max(0.0, float(getattr(self.settings, "B3_FEE_RATE", 0.0003) or 0.0))
        b3_fee = (
            round((max(0.0, float(entry_price)) + max(0.0, float(exit_price))) * int(quantity) * fee_rate, 2)
            if (fee_rate > 0 and int(quantity) > 0 and (entry_price > 0 or exit_price > 0))
            else 0.0
        )
        net_pnl = round(float(pnl) - b3_fee, 2)

        if self.settings.simulation_mode and not force_production:
            return net_pnl

        with self.prod_portfolio._lock:
            if b3_fee > 0:
                self.prod_portfolio.set_free_cash(max(0.0, self.prod_portfolio.free_cash - b3_fee))
                self.prod_portfolio.cumulative_realized_pnl -= b3_fee
                self.prod_portfolio.ai_allocated_capital = max(
                    0.0, self.prod_portfolio.ai_allocated_capital - b3_fee
                )
            cost_basis_eq = self.prod_portfolio.free_cash + self.prod_portfolio.allocated_capital
            self.prod_portfolio.vault_reserve_cash = min(
                self.prod_portfolio.free_cash,
                max(0.0, cost_basis_eq - self.prod_portfolio.ai_allocated_capital),
            )
            ai_cap = self.prod_portfolio.ai_allocated_capital
            vault_cash = self.prod_portfolio.vault_reserve_cash

        try:
            self.db.set_config("prod_ai_allocated_capital", f"{ai_cap:.2f}")
            self.db.set_config("prod_vault_reserve_cash", f"{vault_cash:.2f}")
            self.db.set_config("prod_broker_account_balance", f"{self.prod_portfolio.free_cash:.2f}")
            if abs(net_pnl) > 1e-6 or b3_fee > 0:
                fee_note = f" | Emolumentos B3: R$ {b3_fee:.2f}" if b3_fee > 0 else ""
                self.db.record_capital_transaction(
                    tx_type="REINVESTIMENTO_LUCRO",
                    amount=net_pnl,
                    resulting_ai_capital=round(ai_cap, 2),
                    resulting_vault=round(vault_cash, 2),
                    details=f"{reason} em {ticker} (P&L líq: R$ {net_pnl:+.2f}{fee_note})",
                    environment="production",
                    is_simulated=False,
                )
        except Exception as exc:
            logger.error("Erro ao persistir auto-compounding de produção (%s): %s", ticker, exc)
        return net_pnl

    def _evaluate_circuit_breaker(self) -> None:
        """
        Avalia P&L diário isolado por ambiente e envia alerta de Circuit Breaker
        apenas uma vez por transição de ativação no dia.
        Em Produção, usa exclusivamente `operational_value` (jamais `total_value`).
        """
        is_sim = self.settings.simulation_mode
        env_key = "homologation" if is_sim else "production"
        self._sync_manual_capital_delta_to_sod(env_key, self.portfolio)

        if is_sim:
            current_eq = self.portfolio.total_value
        else:
            current_eq = self.portfolio.operational_value
            if current_eq <= 0:
                return

        start_eq = self._start_of_day_equity.get(env_key)
        if start_eq is None or start_eq <= 0:
            if current_eq > 0:
                self._set_start_of_day_baseline(env_key, self.portfolio, current_eq)
            daily_pnl = 0.0
        else:
            daily_pnl = (current_eq - start_eq) / start_eq

        self.risk_manager.update_daily_pnl(daily_pnl)
        cb_active = self.risk_manager.is_circuit_breaker_active
        self._env_daily_pnl[env_key] = daily_pnl
        self._env_circuit_breaker_active[env_key] = cb_active

        if cb_active and not self._circuit_breaker_alerted.get(env_key, False):
            self._circuit_breaker_alerted[env_key] = True
            env_label = "HOMOLOGAÇÃO" if is_sim else "PRODUÇÃO"
            try:
                self.telegram.send_alert(
                    f"🛑 *CIRCUIT BREAKER ATIVADO ({env_label})*\n"
                    f"Perda diária de `{daily_pnl * 100:.2f}%` excedeu o limite de segurança.\n"
                    "Novas compras estão suspensas neste ambiente até o próximo pregão."
                )
            except Exception:
                pass

    def _run_trading_cycle(self) -> None:
        """
        Executa um ciclo completo de trading:
        1. Sincroniza carteira e atualiza cotações.
        2. Verifica STOP-LOSS e sincroniza Trailing Stops no portfólio e no broker.
        3. Verifica TAKE-PROFIT parcial (com refresh das posições sobreviventes).
        4. Avalia Circuit Breaker ANTES de novas compras.
        5. Verifica metas de saque (exclusivamente em Produção).
        6. Coleta notícias e dados OHLCV em lote.
        7. Avalia posições em carteira PRIMEIRO (liberando caixa em vendas) e depois o restante da watchlist.
        8. Reavalia Circuit Breaker, volatilidade (com cooldown) e Dashboard.
        """
        cycle_start = datetime.now(BRT)
        logger.info('─── Início do ciclo de trading ───')

        with self._engine_lock:
            self._sync_portfolio_with_broker()

        held_tickers_initial = [normalize_b3_ticker(p.ticker) for p in self.portfolio.positions]
        tickers_for_prices = list(dict.fromkeys(list(self.settings.watchlist) + held_tickers_initial))
        prices = self.market_data.update_prices(tickers_for_prices)

        with self._engine_lock:
            self.portfolio.update_prices(prices)
            logger.debug('Preços atualizados para %d ativos', len(prices))

            env_key = "homologation" if self.settings.simulation_mode else "production"
            if (self._start_of_day_equity.get(env_key) or 0.0) <= 0:
                init_eq = (
                    self.portfolio.total_value
                    if self.settings.simulation_mode
                    else self.portfolio.operational_value
                )
                if init_eq > 0:
                    self._set_start_of_day_baseline(env_key, self.portfolio, init_eq)

            partial_tp_tickers_this_cycle: set[str] = set()
            positions = self.portfolio.positions
            if positions:
                prev_stops = {
                    normalize_b3_ticker(p.ticker): p.stop_loss for p in positions
                }
                triggered = self.risk_manager.check_stop_loss_triggers(
                    positions, self.market_data
                )
                triggered_norms = {normalize_b3_ticker(p.ticker) for p in triggered}

                self.portfolio.update_prices({
                    p.ticker: p.current_price
                    for p in positions
                    if p.current_price and p.current_price > 0
                })

                for pos in positions:
                    norm_t = normalize_b3_ticker(pos.ticker)
                    old_sl = prev_stops.get(norm_t, 0.0)
                    if pos.stop_loss > old_sl:
                        self.portfolio.update_position_stop(pos.ticker, pos.stop_loss)
                        if norm_t not in triggered_norms:
                            try:
                                self.broker.modify_stop_loss(pos.ticker, pos.stop_loss)
                            except Exception as mod_err:
                                logger.warning(
                                    'Falha ao atualizar SL no broker para %s: %s',
                                    pos.ticker, mod_err,
                                )

                for position in triggered:
                    logger.warning(
                        '🚨 Executando venda emergencial: %s', position.ticker
                    )
                    entry_before = position.entry_price
                    trade = self.broker.emergency_sell(position.ticker)
                    if trade is not None and trade.status == OrderStatus.FILLED:
                        fill_price = (
                            float(trade.price)
                            if (trade.price is not None and trade.price > 0)
                            else float(position.current_price or position.entry_price)
                        )
                        live_pos = self.portfolio.get_position(position.ticker)
                        live_qty = live_pos.quantity if live_pos else position.quantity
                        sold_qty = (
                            int(trade.quantity)
                            if (trade.quantity and trade.quantity > 0)
                            else live_qty
                        )

                        if sold_qty < live_qty:
                            self.portfolio.reduce_position(position.ticker, fill_price, sold_qty)
                        else:
                            self.portfolio.remove_position(position.ticker, fill_price)
                            self._set_prod_partial_exit_flag(position.ticker, False)

                        self._mark_sold_today(position.ticker, is_simulated=self.settings.simulation_mode)
                        realized_pnl = (fill_price - entry_before) * sold_qty
                        self._record_production_realized_pnl(
                            ticker=position.ticker,
                            pnl=realized_pnl,
                            reason="Stop-Loss Emergencial",
                            entry_price=entry_before,
                            exit_price=fill_price,
                            quantity=sold_qty,
                        )

                        reason = (
                            f'Preço R$ {position.current_price:.2f} <= '
                            f'Stop-loss R$ {position.stop_loss:.2f} '
                            f'(entrada R$ {entry_before:.2f})'
                        )
                        self._insert_trade_safe(
                            ticker=trade.ticker,
                            action=trade.order_type.value,
                            quantity=sold_qty,
                            price=fill_price,
                            total_value=round(fill_price * sold_qty, 2),
                            stop_loss=position.stop_loss,
                            reasoning=reason,
                            is_simulated=self.settings.simulation_mode,
                        )
                        self.telegram.send_emergency_alert(position.ticker, reason)

                positions = self.portfolio.positions
                tp_triggered = self.risk_manager.check_take_profit_triggers(positions)
                fee_rate = max(0.0, float(getattr(self.settings, "B3_FEE_RATE", 0.0003) or 0.0))
                for position in tp_triggered:
                    if position.quantity < 2 or getattr(position, 'partial_exit_done', False):
                        continue

                    be_sl = (
                        round(position.entry_price * (1.0 + 2.0 * fee_rate + 0.001), 2)
                        if fee_rate > 0
                        else position.entry_price
                    )
                    protected_sl = round(max(position.stop_loss, be_sl), 2)

                    if (
                        bool(getattr(self.settings, "AVOID_DAY_TRADE_TAX", False))
                        and self._is_same_day_position(position, cycle_start)
                        and position.current_price > position.entry_price
                    ):
                        if protected_sl > position.stop_loss:
                            self.portfolio.update_position_stop(position.ticker, protected_sl)
                            try:
                                self.broker.modify_stop_loss(position.ticker, protected_sl)
                            except Exception as mod_err:
                                logger.debug(
                                    'Aviso ao elevar SL breakeven em %s: %s',
                                    position.ticker, mod_err,
                                )
                        logger.info(
                            '🛡️ Take-Profit parcial de %s retido até D+1 (Escudo Tributário PF Anti-Day-Trade). '
                            'Stop-loss blindado no breakeven líquido (R$ %.2f).',
                            position.ticker,
                            protected_sl,
                        )
                        continue

                    qty_to_sell = self.risk_manager.calculate_partial_tp_quantity(
                        position,
                        simulation_mode=self.settings.simulation_mode,
                    )
                    norm_tp = normalize_b3_ticker(position.ticker)
                    if qty_to_sell <= 0:
                        self.portfolio.update_position_stop(
                            position.ticker,
                            protected_sl,
                            partial_exit_done=True,
                        )
                        self._set_prod_partial_exit_flag(position.ticker, True)
                        if hasattr(self.broker, "set_partial_exit_done"):
                            try:
                                self.broker.set_partial_exit_done(position.ticker, True)
                            except Exception as p_err:
                                logger.debug(
                                    'Falha ao persistir partial_exit_done no broker para %s: %s',
                                    position.ticker, p_err,
                                )
                        try:
                            self.broker.modify_stop_loss(position.ticker, protected_sl)
                        except Exception as mod_err:
                            logger.warning(
                                'Falha ao elevar SL breakeven para lote padrão indivisível de %s: %s',
                                position.ticker, mod_err,
                            )
                        if norm_tp:
                            partial_tp_tickers_this_cycle.add(norm_tp)
                        logger.info(
                            '🎯 Take-Profit (+15%%) em lote padrão indivisível (%dx %s): '
                            'venda fracionária omitida na B3; Stop-Loss blindado em R$ %.2f.',
                            position.quantity, position.ticker, protected_sl,
                        )
                        continue

                    logger.info('🎯 Executando realização parcial: %s', position.ticker)
                    entry_before = position.entry_price
                    trade = self.broker.sell(position.ticker, qty_to_sell, position.current_price)
                    if trade is not None and trade.status == OrderStatus.FILLED:
                        fill_price = (
                            float(trade.price)
                            if (trade.price is not None and trade.price > 0)
                            else float(position.current_price)
                        )
                        sold_qty = (
                            int(trade.quantity)
                            if (trade.quantity and trade.quantity > 0)
                            else qty_to_sell
                        )
                        self.portfolio.reduce_position(position.ticker, fill_price, sold_qty)
                        new_sl = protected_sl
                        self.portfolio.update_position_stop(
                            position.ticker,
                            new_sl,
                            partial_exit_done=True,
                        )
                        self._set_prod_partial_exit_flag(position.ticker, True)
                        self._mark_sold_today(position.ticker, is_simulated=self.settings.simulation_mode)
                        if norm_tp:
                            partial_tp_tickers_this_cycle.add(norm_tp)
                        if hasattr(self.broker, "set_partial_exit_done"):
                            try:
                                self.broker.set_partial_exit_done(position.ticker, True)
                            except Exception as p_err:
                                logger.debug(
                                    'Falha ao persistir partial_exit_done no broker para %s: %s',
                                    position.ticker, p_err,
                                )
                        try:
                            self.broker.modify_stop_loss(position.ticker, new_sl)
                        except Exception as mod_err:
                            logger.warning(
                                'Falha ao ajustar SL pós-TP no broker para %s: %s',
                                position.ticker, mod_err,
                            )

                        realized_pnl = (fill_price - entry_before) * sold_qty
                        self._record_production_realized_pnl(
                            ticker=position.ticker,
                            pnl=realized_pnl,
                            reason="Take-Profit Parcial (+15%)",
                            entry_price=entry_before,
                            exit_price=fill_price,
                            quantity=sold_qty,
                        )

                        tp_reason = (
                            f'Take-Profit parcial (+15%): venda de {sold_qty}x {position.ticker} '
                            f'@ R$ {fill_price:.2f}. Stop-loss protegido em R$ {new_sl:.2f}.'
                        )
                        self._insert_trade_safe(
                            ticker=trade.ticker,
                            action=trade.order_type.value,
                            quantity=sold_qty,
                            price=fill_price,
                            total_value=round(fill_price * sold_qty, 2),
                            stop_loss=new_sl,
                            reasoning=tp_reason,
                            is_simulated=self.settings.simulation_mode,
                        )
                        self.telegram.send_alert(
                            f'🎯 TAKE-PROFIT PARCIAL: Venda de {sold_qty} ações de {trade.ticker} '
                            f'a R$ {fill_price:.2f}. Stop-loss restante protegido em R$ {new_sl:.2f}.'
                        )

            self._evaluate_circuit_breaker()

            if not self.settings.simulation_mode:
                active_goal = self.db.get_active_withdrawal_goal()
                if active_goal and active_goal.get("mode") == "on_profit":
                    current_equity = self.portfolio.operational_value
                    target_equity = float(active_goal.get("target_equity", 0.0) or 0.0)
                    if current_equity >= target_equity > 0:
                        logger.info(
                            "🎯 META DE SAQUE ATINGIDA (PRODUÇÃO): Equity Operacional R$ %.2f >= Alvo R$ %.2f",
                            current_equity, target_equity,
                        )
                        self.execute_smart_withdrawal(
                            amount=float(active_goal["amount"]),
                            mode="on_profit",
                            goal_id=active_goal["id"],
                        )

        if self._is_stop_requested():
            return

        news = self.news_scraper.fetch_all_news()
        logger.debug('Notícias coletadas: %d itens', len(news))

        news_to_persist = (
            self.news_scraper.get_unpersisted_news()
            if isinstance(self.news_scraper, NewsScraper)
            else news
        )
        for item in news_to_persist:
            for ticker in (item.tickers_mentioned or [None]):
                self.db.insert_news(
                    title=item.title,
                    source=item.source,
                    url=item.url,
                    ticker=ticker,
                    published_at=item.published_at.isoformat() if item.published_at else None,
                )

        held_positions_now = self.portfolio.positions
        held_norms = {normalize_b3_ticker(p.ticker) for p in held_positions_now}
        held_first: list[str] = []
        non_held: list[str] = []
        seen_norms: set[str] = set()

        for p in held_positions_now:
            nt = normalize_b3_ticker(p.ticker)
            if nt and nt not in seen_norms:
                held_first.append(nt)
                seen_norms.add(nt)

        for t in self.settings.watchlist:
            nt = normalize_b3_ticker(t)
            if not nt or nt in seen_norms:
                continue
            seen_norms.add(nt)
            if nt in held_norms:
                held_first.append(t)
            else:
                non_held.append(t)

        ordered_tickers = held_first + non_held

        ohlcv_batch: dict[str, Any] = {}
        market_regime: Optional[dict[str, Any]] = None
        try:
            self.market_data.get_prices_batch(ordered_tickers)
            batch_res = self.market_data.get_ohlcv_batch(ordered_tickers, period="3mo", interval="1d")
            if isinstance(batch_res, dict):
                ohlcv_batch = batch_res
            regime_fn = getattr(self.market_data, "get_market_regime", None)
            if callable(regime_fn):
                cand_reg = regime_fn("BOVA11")
                if isinstance(cand_reg, dict):
                    market_regime = cand_reg
        except Exception as exc:
            logger.debug('Falha no pré-carregamento em lote: %s', exc)

        cycle_sim_mode = self.settings.simulation_mode
        env_tag_cycle = "homologation" if cycle_sim_mode else "production"
        sold_today_norms: set[str] = set()
        if bool(getattr(self.settings, "AVOID_DAY_TRADE_TAX", True)):
            sold_today_norms.update(self._sold_today_by_env.get(env_tag_cycle, set()))
            try:
                raw_today_trades = _call_with_supported_kwargs(
                    self.db.get_trades_today,
                    is_simulated=cycle_sim_mode,
                    environment=env_tag_cycle,
                )
                for tr in self._filter_trades_by_env(raw_today_trades or [], cycle_sim_mode):
                    act_up = str(tr.get("action", "")).upper()
                    if act_up in ("SELL", "VENDA", "EMERGENCY_SELL", "VENDA_EMERGENCIAL"):
                        nt_sold = normalize_b3_ticker(str(tr.get("ticker", "")))
                        if nt_sold:
                            sold_today_norms.add(nt_sold)
            except Exception as st_err:
                logger.debug("Aviso ao consultar vendas do dia p/ escudo Anti-Day-Trade: %s", st_err)

        for ticker in ordered_tickers:
            if self._is_stop_requested():
                break
            if self.settings.simulation_mode != cycle_sim_mode:
                logger.info('Ambiente alterado durante o ciclo — interrompendo lote atual de avaliações.')
                break

            try:
                norm_t = normalize_b3_ticker(ticker)
                preloaded_df = ohlcv_batch.get(norm_t)
                if preloaded_df is None:
                    preloaded_df = ohlcv_batch.get(ticker)
                decision = _call_with_supported_kwargs(
                    self.decision_engine.evaluate,
                    ticker=ticker,
                    news_items=news,
                    ohlcv_df=preloaded_df,
                    scheduler=self.scheduler,
                    market_regime=market_regime,
                )

                env_tag = 'homologation' if self.settings.simulation_mode else 'production'
                self._recent_decisions.append({
                    'ticker': decision.ticker,
                    'action': decision.action.value,
                    'confidence': decision.confidence,
                    'reasoning': (decision.reasoning[:200] if decision.reasoning else ''),
                    'timestamp': decision.timestamp.isoformat(),
                    'environment': env_tag,
                    'is_simulated': self.settings.simulation_mode,
                })
                self._recent_decisions = self._recent_decisions[-50:]

                if decision.action == Action.BUY:
                    if (
                        bool(getattr(self.settings, "AVOID_DAY_TRADE_TAX", True))
                        and norm_t in sold_today_norms
                    ):
                        logger.info(
                            '🛡️ Compra de %s bloqueada — ativo já vendido hoje '
                            '(Escudo Tributário PF Anti-Day-Trade).',
                            decision.ticker,
                        )
                        continue

                    in_opening = (
                        getattr(self.scheduler, 'is_opening_stabilization', lambda: False)() is True
                    )
                    in_closing = (
                        getattr(self.scheduler, 'is_in_closing_call', lambda: False)() is True
                    )
                    if in_opening or in_closing:
                        window_desc = (
                            'estabilização de abertura' if in_opening else 'call de fechamento'
                        )
                        logger.info(
                            '⏸️ Compra de %s ignorada durante %s da B3',
                            decision.ticker, window_desc,
                        )
                        continue

                    with self._engine_lock:
                        if self.settings.simulation_mode == cycle_sim_mode:
                            self._execute_buy(decision)

                elif decision.action in (Action.SELL, Action.EMERGENCY_SELL):
                    if (
                        decision.action == Action.SELL
                        and norm_t in partial_tp_tickers_this_cycle
                    ):
                        logger.info(
                            '⏸️ Venda discricionária de %s ignorada — Take-Profit parcial já executado neste ciclo.',
                            decision.ticker,
                        )
                        continue

                    with self._engine_lock:
                        if self.settings.simulation_mode == cycle_sim_mode:
                            self._execute_sell(decision)
                            if (
                                bool(getattr(self.settings, "AVOID_DAY_TRADE_TAX", True))
                                and norm_t in self._sold_today_by_env.get(env_tag_cycle, set())
                            ):
                                sold_today_norms.add(norm_t)

            except Exception as e:
                logger.error('Erro ao avaliar %s: %s', ticker, e, exc_info=True)

        with self._engine_lock:
            self._evaluate_circuit_breaker()
            self._check_volatility(self.portfolio.positions)
            self._update_dashboard_state()

        self.last_cycle_time = datetime.now(BRT)
        elapsed = (self.last_cycle_time - cycle_start).total_seconds()
        logger.info('─── Ciclo concluído em %.1fs ───', elapsed)

    def _execute_buy(self, decision: Decision) -> None:
        """Executa ordem de compra baseada na decisão sob proteção de risco e lock."""
        with self._engine_lock:
            if self.risk_manager.is_circuit_breaker_active:
                logger.warning('Compra de %s bloqueada — circuit breaker ativo', decision.ticker)
                return

            summary = self.portfolio.get_summary()
            op_portfolio_value = DecisionEngine._resolve_operational_portfolio_value(
                self.portfolio,
                summary,
                self.portfolio.buying_power,
            )
            is_valid, reason = self.risk_manager.validate_order(
                ticker=decision.ticker,
                quantity=decision.quantity,
                price=decision.price,
                available_capital=self.portfolio.buying_power,
                total_portfolio_value=op_portfolio_value,
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
                fill_price = (
                    float(trade.price)
                    if (trade.price is not None and trade.price > 0)
                    else float(decision.price)
                )
                filled_qty = int(trade.quantity) if (trade.quantity and trade.quantity > 0) else decision.quantity
                sl_val = (
                    float(trade.stop_loss)
                    if (trade.stop_loss is not None and trade.stop_loss > 0)
                    else float(decision.stop_loss or 0.0)
                )
                position = Position(
                    ticker=normalize_b3_ticker(decision.ticker),
                    quantity=filled_qty,
                    entry_price=fill_price,
                    stop_loss=sl_val,
                    current_price=fill_price,
                    ticket=trade.ticket,
                )
                self.portfolio.add_position(position)
                if not self.settings.simulation_mode:
                    self.db.set_config("prod_broker_account_balance", f"{self.portfolio.free_cash:.2f}")

                self._insert_trade_safe(
                    ticker=trade.ticker,
                    action=trade.order_type.value,
                    quantity=filled_qty,
                    price=fill_price,
                    total_value=round(fill_price * filled_qty, 2),
                    stop_loss=sl_val,
                    reasoning=decision.reasoning or "Sinal convergente de compra",
                    is_simulated=self.settings.simulation_mode,
                )
                executed_decision = dc_replace(
                    decision,
                    quantity=filled_qty,
                    price=fill_price,
                    stop_loss=sl_val,
                )
                self.telegram.send_trade_alert(
                    executed_decision, simulation=self.settings.simulation_mode
                )
                logger.info(
                    '✅ COMPRA executada: %s — %d ações @ R$ %.2f',
                    decision.ticker, filled_qty, fill_price,
                )
            else:
                rej_reason = getattr(trade, 'comment', 'Desconhecido') if trade else 'Broker retornou None'
                logger.warning('Ordem rejeitada para %s: %s', decision.ticker, rej_reason)

    def _execute_sell(self, decision: Decision) -> None:
        """Executa ordem de venda baseada na decisão."""
        with self._engine_lock:
            pos = self.portfolio.get_position(decision.ticker)
            entry_before = pos.entry_price if pos else decision.price
            pos_quantity = pos.quantity if pos else decision.quantity

            trade = self.broker.sell(
                ticker=decision.ticker,
                quantity=decision.quantity,
                price=decision.price,
            )

            if trade is not None and trade.status == OrderStatus.FILLED:
                fill_price = (
                    float(trade.price)
                    if (trade.price is not None and trade.price > 0)
                    else float(decision.price)
                )
                sold_qty = int(trade.quantity) if (trade.quantity and trade.quantity > 0) else decision.quantity

                if sold_qty < pos_quantity:
                    self.portfolio.reduce_position(decision.ticker, fill_price, sold_qty)
                else:
                    self.portfolio.remove_position(decision.ticker, fill_price)
                    self._set_prod_partial_exit_flag(decision.ticker, False)

                self._mark_sold_today(decision.ticker, is_simulated=self.settings.simulation_mode)
                realized_pnl = (fill_price - entry_before) * sold_qty
                self._record_production_realized_pnl(
                    ticker=decision.ticker,
                    pnl=realized_pnl,
                    reason="Venda por sinal da IA",
                    entry_price=entry_before,
                    exit_price=fill_price,
                    quantity=sold_qty,
                )

                self._insert_trade_safe(
                    ticker=trade.ticker,
                    action=trade.order_type.value,
                    quantity=sold_qty,
                    price=fill_price,
                    total_value=round(fill_price * sold_qty, 2),
                    stop_loss=pos.stop_loss if pos else None,
                    reasoning=decision.reasoning or "Sinal técnico/sentimento de venda",
                    is_simulated=self.settings.simulation_mode,
                )
                executed_decision = dc_replace(
                    decision,
                    quantity=sold_qty,
                    price=fill_price,
                    stop_loss=pos.stop_loss if pos else decision.stop_loss,
                )
                self.telegram.send_trade_alert(
                    executed_decision, simulation=self.settings.simulation_mode
                )
                logger.info(
                    '✅ VENDA executada: %s — %d ações @ R$ %.2f',
                    decision.ticker, sold_qty, fill_price,
                )
            else:
                rej_reason = getattr(trade, 'comment', 'Desconhecido') if trade else 'Broker retornou None'
                logger.warning('Ordem rejeitada para %s: %s', decision.ticker, rej_reason)

    def _run_maintenance(self) -> None:
        """
        Executa ciclo de manutenção fora do pregão: expurga decisões transitórias antigas
        (`AGUARDAR`) e atualiza séries históricas OHLCV.
        """
        logger.info('─── Início do ciclo de manutenção ───')

        try:
            retention_days = int(getattr(self.settings, "DECISION_RETENTION_DAYS", 14) or 14)
            prune_fn = getattr(self.db, "prune_old_operational_logs", None)
            if callable(prune_fn):
                prune_fn(keep_hold_days=retention_days)
        except Exception as prune_err:
            logger.warning("Aviso durante limpeza automática do SQLite: %s", prune_err)

        for ticker in self.settings.watchlist:
            if self._is_stop_requested():
                break
            try:
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

    def _filter_trades_by_env(self, trades: list[dict[str, Any]], is_sim: bool) -> list[dict[str, Any]]:
        """Garante isolamento por ambiente mesmo se o método de banco retornar ambos."""
        filtered: list[dict[str, Any]] = []
        for t in trades:
            if "is_simulated" in t and t["is_simulated"] is not None:
                if bool(int(t["is_simulated"])) != bool(is_sim):
                    continue
            filtered.append(t)
        return filtered

    def _send_closing_report(self) -> None:
        """Calcula estatísticas diárias isoladas por ambiente e envia relatório de fechamento."""
        is_sim = self.settings.simulation_mode
        env_tag = "homologation" if is_sim else "production"
        raw_trades = _call_with_supported_kwargs(
            self.db.get_trades_today,
            is_simulated=is_sim,
            environment=env_tag,
        )
        trades_today = self._filter_trades_by_env(raw_trades or [], is_sim)
        summary = self.portfolio.get_summary()
        baseline_cap = self.portfolio.initial_capital

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
            'initial_capital': baseline_cap,
            'environment': env_tag,
            'is_simulated': is_sim,
        }

        self.telegram.send_closing_report(
            report=report,
            simulation=is_sim,
        )
        self._closing_report_sent_today = True
        try:
            _call_with_supported_kwargs(
                self.db.insert_daily_report,
                report_date=report['date'],
                buys_count=buys,
                sells_count=sells,
                free_cash=summary.free_cash,
                allocated_capital=summary.allocated_capital,
                initial_capital=baseline_cap,
                total_equity=summary.total_value,
                pnl_percent=summary.total_pnl_percent,
                environment=env_tag,
                is_simulated=is_sim,
            )
        except Exception as e:
            logger.error('Erro ao salvar relatório diário no DB: %s', e)
        logger.info('Relatório de fechamento enviado')

    def _check_volatility(self, positions: list[Position]) -> None:
        """
        Verifica variações significativas nas posições mantidas respeitando cooldown por ativo
        e calculando `old_price` fiel à variação percentual observada.
        """
        now_ts = time_module.time()
        cooldown = float(getattr(self.settings, 'alert_cooldown', 1800) or 1800)

        for position in positions:
            variation = self.market_data.get_variation(position.ticker)
            if variation is None or abs(variation) < self.settings.volatility_alert_threshold:
                continue

            norm_t = normalize_b3_ticker(position.ticker)
            last_alert = self._last_volatility_alert.get(norm_t, 0.0)
            if (now_ts - last_alert) < cooldown:
                continue

            new_price = float(position.current_price or 0.0)
            if new_price <= 0:
                continue

            denom = 1.0 + (float(variation) / 100.0)
            old_price = round(new_price / denom, 2) if denom > 0 else round(position.entry_price, 2)

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
            self._last_volatility_alert[norm_t] = now_ts
            logger.info(
                'Alerta de volatilidade enviado: %s %+.2f%% (R$ %.2f -> R$ %.2f)',
                position.ticker, variation, old_price, new_price,
            )

    def _reset_daily_flags(self) -> None:
        """Reseta flags diárias e baselines de Circuit Breaker por ambiente ao mudar de dia."""
        today = datetime.now(BRT).date()
        if self._last_alert_date != today:
            self._opening_alert_sent_today = False
            self._closing_report_sent_today = False
            self._last_alert_date = today
            self._completed_maintenance_windows.clear()
            self._sold_today_by_env = {
                "homologation": set(),
                "production": set(),
            }

            sim_eq = self.sim_portfolio.total_value
            prod_eq = self.prod_portfolio.operational_value
            self._set_start_of_day_baseline(
                "homologation",
                self.sim_portfolio,
                sim_eq if sim_eq > 0 else None,
            )
            self._set_start_of_day_baseline(
                "production",
                self.prod_portfolio,
                prod_eq if prod_eq > 0 else None,
            )
            self._circuit_breaker_alerted = {
                "homologation": False,
                "production": False,
            }
            self._env_circuit_breaker_active = {
                "homologation": False,
                "production": False,
            }
            self._env_daily_pnl = {
                "homologation": 0.0,
                "production": 0.0,
            }
            self.risk_manager.reset_daily_limits()
            logger.debug('Flags diárias resetadas para %s', today)

    def _sync_portfolio_with_broker(self) -> None:
        """
        Sincroniza o portfólio ativo com o saldo real e posições do broker conectado,
        sem jamais sobrescrever o saldo do broker ou apagar o auto-compounding em memória.
        """
        with self._engine_lock:
            try:
                broker_cash: Optional[float] = None
                try:
                    raw_bal = self.broker.get_balance()
                    if raw_bal is not None:
                        broker_cash = max(0.0, float(raw_bal))
                except Exception as bal_err:
                    logger.warning('Erro ao consultar saldo do broker: %s', bal_err)

                try:
                    broker_positions = self.broker.get_positions()
                    if broker_positions is None:
                        raise ValueError("Broker retornou None para posições")
                except Exception as pos_err:
                    logger.warning('Erro ao obter posições do broker, mantendo locais: %s', pos_err)
                    broker_positions = None

                if not self.settings.simulation_mode and (
                    broker_cash is None or broker_positions is None
                ):
                    logger.warning(
                        'Sincronização de Produção abortada (saldo=%s, posições=%s) — '
                        'preservando custódia local e teto da IA contra falha transitória.',
                        'OK' if broker_cash is not None else 'indisponível',
                        len(broker_positions) if broker_positions is not None else 'indisponíveis',
                    )
                    return

                persisted_partial_map = (
                    self._load_prod_partial_exit_map()
                    if not self.settings.simulation_mode
                    else {}
                )
                stops_to_resync: list[tuple[str, float]] = []
                updated_partial_map: Optional[dict[str, bool]] = None

                with self.portfolio._lock:
                    prev_op_eq = self.portfolio.operational_value
                    prev_initial = self.portfolio.initial_capital

                    if broker_cash is not None:
                        self.portfolio.set_free_cash(broker_cash)

                    if broker_positions is not None:
                        local_map = {
                            normalize_b3_ticker(p.ticker): p for p in self.portfolio._positions
                        }

                        grouped_legs: dict[str, list[Any]] = {}
                        for bp in broker_positions:
                            if getattr(bp, 'quantity', 0) is None or int(bp.quantity) <= 0:
                                continue
                            norm = normalize_b3_ticker(bp.ticker)
                            if not norm:
                                continue
                            grouped_legs.setdefault(norm, []).append(bp)

                        reconciled: list[Position] = []

                        for norm, legs in grouped_legs.items():
                            total_qty = sum(int(leg.quantity) for leg in legs)
                            if total_qty <= 0:
                                continue

                            if len(legs) == 1:
                                leg0 = legs[0]
                                cons_entry = float(leg0.entry_price or 0.0)
                                cons_curr = float(
                                    getattr(leg0, 'current_price', 0.0) or leg0.entry_price or 0.0
                                )
                            else:
                                entry_num = sum(
                                    float(leg.entry_price) * int(leg.quantity)
                                    for leg in legs
                                    if getattr(leg, 'entry_price', 0.0) and float(leg.entry_price) > 0
                                )
                                entry_den = sum(
                                    int(leg.quantity)
                                    for leg in legs
                                    if getattr(leg, 'entry_price', 0.0) and float(leg.entry_price) > 0
                                )
                                cons_entry = (
                                    round(entry_num / entry_den, 4) if entry_den > 0 else 0.0
                                )

                                curr_num = 0.0
                                curr_den = 0
                                for leg in legs:
                                    lc = float(
                                        getattr(leg, 'current_price', 0.0) or leg.entry_price or 0.0
                                    )
                                    if lc > 0:
                                        curr_num += lc * int(leg.quantity)
                                        curr_den += int(leg.quantity)
                                cons_curr = (
                                    round(curr_num / curr_den, 4) if curr_den > 0 else cons_entry
                                )

                            leg_dts: list[datetime] = []
                            for leg in legs:
                                raw_dt = getattr(leg, 'entry_time', None) or getattr(leg, 'timestamp', None)
                                if isinstance(raw_dt, datetime):
                                    norm_dt = (
                                        raw_dt.astimezone(BRT)
                                        if raw_dt.tzinfo is not None
                                        else raw_dt.replace(tzinfo=BRT)
                                    )
                                    leg_dts.append(norm_dt)
                            cons_entry_dt: Optional[datetime] = min(leg_dts) if leg_dts else None

                            positive_stops = [
                                float(getattr(leg, 'stop_loss', 0.0) or 0.0)
                                for leg in legs
                                if float(getattr(leg, 'stop_loss', 0.0) or 0.0) > 0
                            ]
                            valid_below_curr = (
                                [s for s in positive_stops if s < cons_curr]
                                if cons_curr > 0
                                else positive_stops
                            )
                            cons_bp_sl = (
                                max(valid_below_curr)
                                if valid_below_curr
                                else (min(positive_stops) if positive_stops else 0.0)
                            )

                            cons_partial_done = (
                                any(bool(getattr(leg, 'partial_exit_done', False)) for leg in legs)
                                or bool(persisted_partial_map.get(norm, False))
                            )

                            primary_ticket = None
                            for leg in legs:
                                tk = getattr(leg, 'ticket', None)
                                raw_sym = str(getattr(leg, 'ticker', '')).strip().upper()
                                if tk is not None and (raw_sym == norm or int(leg.quantity) >= 100):
                                    primary_ticket = tk
                                    break
                            if primary_ticket is None:
                                for leg in legs:
                                    tk = getattr(leg, 'ticket', None)
                                    if tk is not None:
                                        primary_ticket = tk
                                        break

                            if norm in local_map:
                                local_pos = local_map[norm]
                                local_pos.quantity = total_qty
                                if cons_entry > 0:
                                    local_pos.entry_price = cons_entry
                                if cons_curr > 0:
                                    local_pos.current_price = cons_curr
                                if cons_entry_dt is not None:
                                    local_pos.entry_time = cons_entry_dt
                                if primary_ticket is not None:
                                    local_pos.ticket = primary_ticket
                                local_pos.partial_exit_done = bool(
                                    local_pos.partial_exit_done or cons_partial_done
                                )
                                effective_sl = max(local_pos.stop_loss, cons_bp_sl)
                                if effective_sl <= 0 and local_pos.entry_price > 0:
                                    effective_sl = self.risk_manager.calculate_stop_loss(
                                        local_pos.entry_price
                                    )
                                local_pos.stop_loss = effective_sl
                                if local_pos.stop_loss > cons_bp_sl + 1e-4:
                                    stops_to_resync.append((norm, local_pos.stop_loss))
                                reconciled.append(local_pos)
                            else:
                                effective_sl = cons_bp_sl
                                if effective_sl <= 0 and cons_entry > 0:
                                    effective_sl = self.risk_manager.calculate_stop_loss(cons_entry)
                                pos_kwargs: dict[str, Any] = {
                                    "ticker": norm,
                                    "quantity": total_qty,
                                    "entry_price": cons_entry,
                                    "stop_loss": effective_sl,
                                    "current_price": cons_curr if cons_curr > 0 else cons_entry,
                                    "ticket": primary_ticket,
                                    "partial_exit_done": cons_partial_done,
                                }
                                if cons_entry_dt is not None:
                                    pos_kwargs["entry_time"] = cons_entry_dt
                                if (
                                    not self.settings.simulation_mode
                                    and effective_sl > cons_bp_sl + 1e-4
                                ):
                                    stops_to_resync.append((norm, effective_sl))
                                reconciled.append(Position(**pos_kwargs))

                        self.portfolio._positions.clear()
                        self.portfolio._positions.extend(reconciled)

                        if not self.settings.simulation_mode:
                            cleaned_partial = {
                                normalize_b3_ticker(p.ticker): True
                                for p in reconciled
                                if getattr(p, 'partial_exit_done', False)
                            }
                            if cleaned_partial != persisted_partial_map:
                                updated_partial_map = cleaned_partial

                    if not self.settings.simulation_mode:
                        cost_basis_eq = self.portfolio.free_cash + self.portfolio.allocated_capital
                        max_cap_limit = max(self.portfolio.total_value, cost_basis_eq)
                        self.portfolio.ai_allocated_capital = max(
                            0.0,
                            min(self.portfolio.ai_allocated_capital, max_cap_limit),
                        )
                        self.portfolio.vault_reserve_cash = min(
                            self.portfolio.free_cash,
                            max(0.0, cost_basis_eq - self.portfolio.ai_allocated_capital),
                        )
                        self.portfolio._rebalance_baseline_after_capital_change(
                            prev_op_eq,
                            prev_initial,
                            track_manual_delta=False,
                        )
                        try:
                            self.db.set_config(
                                "prod_broker_account_balance",
                                f"{self.portfolio.free_cash:.2f}",
                            )
                        except Exception:
                            pass

                if updated_partial_map is not None:
                    self._save_prod_partial_exit_map(updated_partial_map)

                for norm_t, target_sl in stops_to_resync:
                    try:
                        self.broker.modify_stop_loss(norm_t, target_sl)
                    except Exception as mod_err:
                        logger.warning(
                            'Falha ao ressincronizar Stop-Loss no broker para %s (R$ %.2f): %s',
                            norm_t, target_sl, mod_err,
                        )

                logger.info(
                    'Portfólio sincronizado com broker — Saldo Livre: R$ %.2f, Posições: %d',
                    self.portfolio.free_cash, len(self.portfolio.positions),
                )
                self._update_dashboard_state()
            except Exception as exc:
                logger.warning('Erro ao sincronizar portfólio com broker: %s', exc)

    def _get_cached_watchlist_price(self, ticker: str) -> float:
        """Extrai cotação em cache do MarketData de forma compatível com todas as camadas de cache."""
        norm = normalize_b3_ticker(ticker)
        getter = getattr(self.market_data, 'get_cached_price', None)
        if callable(getter):
            val = getter(ticker)
            if isinstance(val, dict):
                return float(val.get('last', 0.0) or 0.0)
            if isinstance(val, (int, float)) and val > 0:
                return float(val)

        cache_obj = getattr(self.market_data, '_cache', None)
        if cache_obj is not None and hasattr(cache_obj, 'get'):
            cached = cache_obj.get(norm) or cache_obj.get(ticker)
            if isinstance(cached, dict):
                return float(cached.get('last', 0.0) or 0.0)
            if isinstance(cached, (int, float)):
                return float(cached)

        price_cache = getattr(self.market_data, '_price_cache', None)
        if isinstance(price_cache, dict):
            pdata = price_cache.get(ticker) or price_cache.get(norm)
            if isinstance(pdata, dict):
                return float(pdata.get('last', 0.0) or 0.0)
            if isinstance(pdata, (int, float)):
                return float(pdata)

        legacy = getattr(self.market_data, '_price_cache_legacy', None)
        if isinstance(legacy, dict):
            val = legacy.get(norm) or legacy.get(ticker)
            if isinstance(val, (int, float)):
                return float(val)

        return 0.0

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
                'stop_loss': p.stop_loss,
                'partial_exit_done': p.partial_exit_done,
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
            'operational_equity': self.portfolio.operational_value,
            'ai_allocated_capital': self.portfolio.ai_allocated_capital,
            'vault_reserve_cash': self.portfolio.vault_reserve_cash,
            'buying_power': self.portfolio.buying_power,
            'initial_capital': self.portfolio.initial_capital,
            'pnl': summary.total_pnl,
            'pnl_percent': summary.total_pnl_percent,
            'positions': positions_data,
            'simulation_mode': self.settings.simulation_mode,
        })

        watchlist_prices = {
            ticker: self._get_cached_watchlist_price(ticker)
            for ticker in self.settings.watchlist
        }

        self.dashboard_state.update('watchlist', [
            {'ticker': t, 'price': p} for t, p in watchlist_prices.items()
        ])

        self.dashboard_state.update('recent_decisions', self._recent_decisions[-20:])
        self.dashboard_state.update('health', health)

        if self._start_time is not None:
            uptime = (datetime.now(BRT) - self._start_time).total_seconds()
            self.dashboard_state.update('uptime_seconds', uptime)

    def stop(self, graceful: bool = True) -> None:
        """
        Para o sistema registrando `shutdown_status` ('graceful' ou 'crashed') com
        proteção contra reentrância.
        """
        status_label = "graceful" if graceful else "crashed"
        current_env = "homologation" if self.settings.simulation_mode else "production"

        if self._stopped:
            if not graceful:
                try:
                    self.db.set_config("shutdown_status", "crashed")
                    self.db.set_config("last_environment", current_env)
                except Exception:
                    pass
            return

        self._stopped = True
        self._stop_requested = True
        self.running = False
        self._stop_event.set()

        logger.info('Iniciando shutdown (%s)...', status_label)
        try:
            self.db.set_config("shutdown_status", status_label)
            self.db.set_config("last_environment", current_env)
        except Exception as e:
            logger.error("Erro ao registrar estado de shutdown no banco: %s", e)

        try:
            self.health_monitor.stop_daemon()
        except Exception as e:
            logger.debug("Aviso ao parar health monitor: %s", e)

        try:
            self.telegram.stop_polling()
        except Exception as e:
            logger.debug("Aviso ao parar polling Telegram: %s", e)

        try:
            self.dashboard_server.stop()
            logger.info('Dashboard web encerrado')
        except Exception as e:
            logger.warning('Erro ao parar dashboard server: %s', e)

        try:
            self.broker.disconnect()
            logger.info('Broker desconectado')
        except Exception as e:
            logger.error('Erro ao desconectar broker: %s', e)

        try:
            if hasattr(self.news_scraper, "close"):
                self.news_scraper.close()
        except Exception as e:
            logger.debug("Aviso ao fechar news_scraper: %s", e)

        try:
            if hasattr(self.market_data, "close"):
                self.market_data.close()
        except Exception as e:
            logger.debug("Aviso ao fechar market_data: %s", e)

        if getattr(WebDashboardState, '_engine', None) is self:
            WebDashboardState._engine = None
        if getattr(self.dashboard_state, '_engine', None) is self:
            self.dashboard_state._engine = None

        try:
            if graceful:
                self.telegram.send_alert('🔴 Córtex desligando...')
            else:
                self.telegram.send_alert(
                    '🚨 Córtex encerrado por erro crítico — Fail-safe p/ Homologação ativado no próximo boot.'
                )
        except Exception:
            pass

        logger.info('═══════════════════════════════════════════════════')
        logger.info('   CÓRTEX DESLIGADO (%s)', status_label.upper())
        logger.info('═══════════════════════════════════════════════════')
        flush_log_handlers()

    def get_state(self) -> dict[str, Any]:
        """Retorna estado completo do sistema para o dashboard WebSocket."""
        self._update_dashboard_state()
        return self.dashboard_state.get_state()

    def _register_signal_handlers(self) -> None:
        """
        Registra handlers não-bloqueantes para SIGINT e SIGTERM (apenas sinaliza `_stop_event`).
        """
        def _handler(sig: int, frame: Any) -> None:
            self.request_stop(sig)

        try:
            signal.signal(signal.SIGINT, _handler)
            try:
                signal.signal(signal.SIGTERM, _handler)
            except (OSError, AttributeError, ValueError):
                pass
            logger.debug('Signal handlers registrados (SIGINT, SIGTERM)')
        except (OSError, ValueError) as e:
            logger.warning('Não foi possível registrar signal handlers: %s', e)

    def _register_telegram_commands(self) -> None:
        """Registra todos os 11 comandos interativos no Telegram."""
        def cmd_status() -> str:
            is_sim = self.settings.simulation_mode
            mode_str = "🎮 HOMOLOGAÇÃO (SIMULADOR)" if is_sim else "🟢 PRODUÇÃO (B3 / CLEAR)"
            summary = self.portfolio.get_summary()

            if not is_sim:
                summ_cap = self.get_production_summary()
                broker_bal = summ_cap.get("broker_balance", 0.0)
                ai_cap = summ_cap.get("ai_allocated_capital", 0.0)
                vault_cash = summ_cap.get("vault_reserve_cash", 0.0)
                cap_extra = (
                    f"🏦 *Caixa Livre na Clear:* R$ {broker_bal:,.2f}\n"
                    f"🤖 *Teto Liberado p/ IA:* R$ {ai_cap:,.2f}\n"
                    f"🔒 *Cofre Seguro:* R$ {vault_cash:,.2f}\n"
                )
            else:
                cap_extra = (
                    f"💵 *Caixa Disponível:* R$ {summary.free_cash:,.2f}\n"
                    f"⚡ *Poder de Compra:* R$ {self.portfolio.buying_power:,.2f}\n"
                )

            msg = (
                f"📊 *STATUS OPERACIONAL — CÓRTEX IA*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 *Patrimônio Total:* R$ {summary.total_value:,.2f}\n"
                f"{cap_extra}"
                f"📦 *Posições em Custódia:* {len(summary.positions)} ativos\n"
            )
            if summary.positions:
                for pos in summary.positions:
                    msg += f"  • `{pos.ticker}`: {pos.quantity}x @ R$ {pos.entry_price:.2f}\n"

            msg += (
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🛡️ *Ambiente Ativo:* {mode_str}"
            )
            return msg

        def cmd_saldo() -> str:
            is_sim = self.settings.simulation_mode
            if is_sim:
                cap = self.get_simulation_summary()
                return (
                    "💵 *SALDO & ALOCAÇÃO — HOMOLOGAÇÃO*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 *Patrimônio Total:* `R$ {cap['total_equity']:,.2f}`\n"
                    f"💵 *Caixa Livre:* `R$ {cap['free_cash']:,.2f}`\n"
                    f"📊 *Capital em Posições:* `R$ {cap['allocated_capital']:,.2f}`\n"
                    f"🤖 *Teto Operacional IA:* `R$ {cap['ai_allocated_capital']:,.2f}`\n"
                    f"⚡ *Poder de Compra:* `R$ {cap['buying_power']:,.2f}`\n"
                    f"📈 *Lucro Realizado Acum.:* `R$ {cap['cumulative_realized_pnl']:+,.2f}`"
                )
            cap = self.get_production_summary()
            conn_str = "🟢 Conectado" if cap.get("broker_connected") else "🔴 Desconectado"
            return (
                "🏦 *SALDO & COFRE — PRODUÇÃO (CLEAR/MT5)*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔌 *Status MT5:* {conn_str}\n"
                f"💰 *Patrimônio Total:* `R$ {cap['total_equity']:,.2f}`\n"
                f"💵 *Caixa Livre (Corretora):* `R$ {cap['broker_balance']:,.2f}`\n"
                f"📊 *Valor em Posições:* `R$ {cap['allocated_capital']:,.2f}`\n"
                f"🤖 *Teto Liberado p/ IA:* `R$ {cap['ai_allocated_capital']:,.2f}`\n"
                f"🔒 *Cofre Reserva Blindado:* `R$ {cap['vault_reserve_cash']:,.2f}`\n"
                f"⚡ *Poder de Compra Efetivo:* `R$ {cap['buying_power']:,.2f}`\n"
                f"📈 *Lucro Realizado Acum.:* `R$ {cap['cumulative_realized_pnl']:+,.2f}`"
            )

        def cmd_pause() -> str:
            if not self.settings.simulation_mode:
                self.switch_environment("homologation", user_name="Telegram Bot")
                return (
                    "⏸️ *CÓRTEX PAUSADO*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Ambiente alterado para *HOMOLOGAÇÃO* (Simulador). Nenhuma ordem real será enviada."
                )
            return "ℹ️ O Córtex já está operando em modo *HOMOLOGAÇÃO*."

        def cmd_resume() -> str:
            if self.settings.simulation_mode:
                ok = self.switch_environment("production", user_name="Telegram Bot")
                if not ok:
                    return (
                        "❌ *FALHA AO RETOMAR PRODUÇÃO*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "MetaTrader 5 / Bridge indisponível ou desconectado.\n"
                        "🛡️ O sistema permanece em *HOMOLOGAÇÃO* por segurança."
                    )
                return (
                    "▶️ *CÓRTEX EM PRODUÇÃO*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Ambiente ativado para *PRODUÇÃO B3*. Ordens envolverão recursos reais da corretora."
                )
            return "ℹ️ O Córtex já está operando em modo *PRODUÇÃO*."

        def cmd_pensamentos() -> str:
            try:
                is_sim = self.settings.simulation_mode
                env_str = "homologation" if is_sim else "production"
                decisions = self.db.get_recent_decisions(limit=5, environment=env_str)
                if not decisions:
                    return (
                        "🧠 <b>PENSAMENTOS DO CÓRTEX IA</b>\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Ainda não há deliberações registradas para o ambiente atual "
                        f"(<b>{html.escape(env_str.upper())}</b>)."
                    )

                msg = (
                    f"🧠 <b>ÚLTIMAS DELIBERAÇÕES — {html.escape(env_str.upper())}</b>\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                )
                for dec in decisions:
                    emoji = (
                        "🟢 🛒" if dec['action'] in ("BUY", "COMPRA")
                        else "🔴 💸" if dec['action'] in ("SELL", "VENDA")
                        else "⚪ ⏸️"
                    )
                    conf_pct = float(dec.get('confidence', 0.0) or 0.0) * 100
                    dt = html.escape(str(dec.get('timestamp', ''))[:16].replace('T', ' '))
                    ticker_safe = html.escape(str(dec.get('ticker', '')))
                    action_safe = html.escape(str(dec.get('action', '')))
                    msg += f"{emoji} <b>{ticker_safe}</b> — <code>{action_safe}</code> ({conf_pct:.0f}%)\n"
                    msg += f"🕒 {dt}\n"
                    if dec.get('reasoning'):
                        reasoning_safe = html.escape(str(dec['reasoning'])[:220])
                        msg += f"💡 <i>{reasoning_safe}</i>\n\n"

                return msg
            except Exception as e:
                logger.error("Erro em cmd_pensamentos: %s", e)
                return "❌ Erro ao consultar deliberações do cérebro IA."

        cmd_pensamentos.parse_mode = 'HTML'  # type: ignore[attr-defined]

        def cmd_carteira() -> str:
            summary = self.portfolio.get_summary()
            if not summary.positions:
                return (
                    "💼 *CUSTÓDIA VAZIA*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Nenhuma posição aberta sob gestão no momento."
                )

            msg = "💼 *CUSTÓDIA DE ATIVOS B3*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            total_invested = 0.0
            total_current = 0.0

            for pos in summary.positions:
                price_data = self.market_data.get_current_price(pos.ticker)
                raw_last = (
                    price_data.get('last')
                    if isinstance(price_data, dict)
                    else price_data
                )
                curr_price = (
                    float(raw_last)
                    if isinstance(raw_last, (int, float)) and raw_last > 0
                    else float(pos.current_price or pos.entry_price or 0.0)
                )
                cost = pos.quantity * pos.entry_price
                val = pos.quantity * curr_price
                total_invested += cost
                total_current += val

                profit_loss_val = val - cost
                profit_loss_pct = (profit_loss_val / cost * 100) if cost > 0 else 0.0
                emoji = "🟢" if profit_loss_val >= 0 else "🔴"
                is_trailing = pos.stop_loss > (pos.entry_price * (1.0 - self.risk_manager.stop_loss_percent + 0.005))
                prot_label = "🎯 Trailing Stop" if is_trailing else "🛡️ Stop Loss"

                msg += f"{emoji} *{pos.ticker}*\n"
                msg += f"  • Quantidade: `{pos.quantity} ações`\n"
                msg += f"  • Preço Médio: `R$ {pos.entry_price:.2f}`\n"
                msg += f"  • Cotação Atual: `R$ {curr_price:.2f}`\n"
                msg += f"  • Stop-Loss: `R$ {pos.stop_loss:.2f}` ({prot_label})\n"
                msg += f"  • P&L: `{profit_loss_pct:+.2f}%` (R$ {profit_loss_val:+.2f})\n\n"

            tot_pnl = total_current - total_invested
            tot_pct = (tot_pnl / total_invested * 100) if total_invested > 0 else 0.0
            msg += (
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 *Total em Ações:* R$ {total_current:,.2f}\n"
                f"📈 *P&L Consolidado:* {tot_pct:+.2f}% (R$ {tot_pnl:+.2f})"
            )
            return msg

        def cmd_trades() -> str:
            try:
                is_sim = self.settings.simulation_mode
                env_tag = "homologation" if is_sim else "production"
                raw_trades = _call_with_supported_kwargs(
                    self.db.get_trade_history,
                    days=7,
                    is_simulated=is_sim,
                    environment=env_tag,
                )
                trades = self._filter_trades_by_env(raw_trades or [], is_sim)
                if not trades:
                    return (
                        "🧾 *HISTÓRICO DE OPERAÇÕES*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "Nenhuma operação realizada nos últimos 7 dias neste ambiente."
                    )

                msg = f"🧾 *ÚLTIMOS TRADES — {'SIMULADOR' if is_sim else 'PRODUÇÃO'}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                for t in trades[:5]:
                    is_buy = t['action'] in ('BUY', 'COMPRA')
                    emoji = "🟢 🛒" if is_buy else "🔴 💸"
                    dt_str = str(t['timestamp'])[:16].replace('T', ' ')
                    msg += f"{emoji} *{t['action']} {t['ticker']}*\n"
                    msg += f"  • Qtd: `{t['quantity']} ações` @ `R$ {t['price']:.2f}`\n"
                    msg += f"  • Volume Total: `R$ {t['total_value']:.2f}`\n"
                    msg += f"  • Data/Hora: `{dt_str}`\n\n"
                return msg
            except Exception as e:
                logger.error("Erro em cmd_trades: %s", e)
                return "❌ Erro ao consultar histórico de trades."

        def cmd_mercado() -> str:
            is_open = self.scheduler.is_market_open()
            now = datetime.now(BRT)
            msg = "🏛️ *STATUS DO MERCADO (B3)*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            if is_open:
                msg += (
                    f"🟢 *PREGÃO ABERTO*\n🕒 {now.strftime('%H:%M:%S')} BRT\n"
                    "O Córtex IA está analisando a watchlist e monitorando proteções."
                )
            else:
                msg += (
                    f"🛑 *PREGÃO FECHADO*\n🕒 {now.strftime('%H:%M:%S')} BRT\n"
                    "O mercado está fechado ou em leilão. O Córtex permanece em prontidão."
                )
            return msg

        def cmd_desempenho() -> str:
            try:
                is_sim = self.settings.simulation_mode
                env_tag = "homologation" if is_sim else "production"
                metrics = _call_with_supported_kwargs(
                    self.db.get_performance_metrics,
                    is_simulated=is_sim,
                    environment=env_tag,
                )
                return (
                    "📈 *MÉTRICAS DE PERFORMANCE — CÓRTEX IA*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 *Taxa de Acerto (Win Rate):* `{metrics.get('win_rate', 0.0):.1f}%`\n"
                    f"📊 *Total de Operações:* `{metrics.get('total_trades', 0)}` "
                    f"(Compras: {metrics.get('total_buys', 0)} | Vendas: {metrics.get('total_sells', 0)})\n"
                    f"📅 *Dias Monitorados:* `{metrics.get('days_tracked', 0)}`\n"
                    f"🟢 *Dias no Lucro:* `{metrics.get('positive_days', 0)}` | "
                    f"🔴 *Dias no Prejuízo:* `{metrics.get('negative_days', 0)}`\n"
                    f"📉 *Drawdown Máximo:* `{metrics.get('max_drawdown', 0.0):.2f}%`\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "🛡️ Monitoramento de Risco Ativo"
                )
            except Exception as e:
                logger.error("Erro em cmd_desempenho: %s", e)
                return "❌ Erro ao calcular métricas de desempenho."

        def cmd_fechartudo() -> str:
            with self._engine_lock:
                try:
                    positions = self.portfolio.positions
                    if not positions:
                        return "💼 Nenhuma posição em aberto para liquidar."
                    closed: list[str] = []
                    failed: list[str] = []
                    for p in list(positions):
                        entry_before = p.entry_price
                        t = self.broker.emergency_sell(p.ticker)
                        if t and t.status == OrderStatus.FILLED:
                            fill_price = (
                                float(t.price)
                                if (t.price is not None and t.price > 0)
                                else float(p.current_price or p.entry_price)
                            )
                            sold_qty = int(t.quantity) if (t.quantity and t.quantity > 0) else p.quantity
                            if sold_qty < p.quantity:
                                self.portfolio.reduce_position(p.ticker, fill_price, sold_qty)
                            else:
                                self.portfolio.remove_position(p.ticker, fill_price)
                                self._set_prod_partial_exit_flag(p.ticker, False)

                            self._mark_sold_today(p.ticker, is_simulated=self.settings.simulation_mode)

                            realized_pnl = (fill_price - entry_before) * sold_qty
                            self._record_production_realized_pnl(
                                ticker=p.ticker,
                                pnl=realized_pnl,
                                reason="Liquidação manual /fechartudo",
                                entry_price=entry_before,
                                exit_price=fill_price,
                                quantity=sold_qty,
                            )
                            self._insert_trade_safe(
                                ticker=t.ticker,
                                action=t.order_type.value,
                                quantity=sold_qty,
                                price=fill_price,
                                total_value=round(fill_price * sold_qty, 2),
                                stop_loss=p.stop_loss,
                                reasoning="Liquidação compulsória via comando /fechartudo no Telegram",
                                is_simulated=self.settings.simulation_mode,
                            )
                            closed.append(f"`{p.ticker}`: {sold_qty}x @ R$ {fill_price:.2f}")
                        else:
                            failed.append(p.ticker)

                    self._update_dashboard_state()
                    if not closed:
                        return f"❌ Não foi possível liquidar as posições: {', '.join(failed)}"

                    extra_warn = (
                        f"\n⚠️ Pendentes/Falha: {', '.join(failed)}" if failed else ""
                    )
                    return (
                        "🚨 *LIQUIDAÇÃO EMERGENCIAL CONCLUÍDA*\n"
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        + "\n".join(f"• {c}" for c in closed)
                        + extra_warn
                        + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        "🔒 Posições convertidas em caixa."
                    )
                except Exception as e:
                    logger.error("Erro em cmd_fechartudo: %s", e)
                    return f"❌ Erro ao encerrar posições: {e}"

        def cmd_help() -> str:
            return (
                "🤖 *MENU DE COMANDOS — CÓRTEX IA*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🔹 `/status` — Patrimônio, caixa, teto da IA e posições\n"
                "🔹 `/carteira` — Ações em custódia, preço médio e P&L\n"
                "🔹 `/saldo` — Detalhamento de caixa, teto da IA e Cofre\n"
                "🔹 `/trades` — Histórico das últimas 5 operações executadas\n"
                "🔹 `/pensamentos` — Raciocínio quantitativo das últimas análises\n"
                "🔹 `/desempenho` — Estatísticas de assertividade (Win Rate)\n"
                "🔹 `/mercado` — Horário e status do pregão da B3\n"
                "🔹 `/pause` — Alterna para modo Homologação (Simulação)\n"
                "🔹 `/resume` — Alterna para modo Produção (B3 Real)\n"
                "🔹 `/fechartudo` — Liquidação compulsória de todas as posições\n"
                "🔹 `/help` — Exibe este menu de ajuda\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "⚡ *Córtex IA — Sistema Autônomo B3*"
            )

        self.telegram.register_command('/status', cmd_status)
        self.telegram.register_command('/saldo', cmd_saldo)
        self.telegram.register_command('/pause', cmd_pause)
        self.telegram.register_command('/resume', cmd_resume)
        _call_with_supported_kwargs(
            self.telegram.register_command,
            '/pensamentos',
            cmd_pensamentos,
            parse_mode='HTML',
        )
        self.telegram.register_command('/carteira', cmd_carteira)
        self.telegram.register_command('/trades', cmd_trades)
        self.telegram.register_command('/desempenho', cmd_desempenho)
        self.telegram.register_command('/fechartudo', cmd_fechartudo)
        self.telegram.register_command('/mercado', cmd_mercado)
        self.telegram.register_command('/help', cmd_help)

    def get_simulation_summary(self) -> dict[str, Any]:
        """Retorna resumo somente-leitura do capital de Homologação."""
        if hasattr(self, "sim_portfolio") and self.sim_portfolio:
            return self.sim_portfolio.get_capital_summary()
        cap = float(self.settings.CAPITAL_INICIAL)
        return {
            "total_equity": round(cap, 2),
            "operational_equity": round(cap, 2),
            "ai_allocated_capital": round(cap, 2),
            "vault_reserve_cash": 0.0,
            "free_cash": round(cap, 2),
            "allocated_capital": 0.0,
            "buying_power": round(cap, 2),
            "cumulative_realized_pnl": 0.0,
            "positions_count": 0,
        }

    def get_production_summary(self) -> dict[str, Any]:
        """
        Retorna resumo estritamente somente-leitura (zero mutações) do capital de Produção,
        distinguindo `broker_balance` (caixa livre), `allocated_capital` (valor das posições),
        `total_equity` (`free_cash + positions_value`), `ai_allocated_capital`,
        `vault_reserve_cash`, `buying_power` e `broker_connected`.
        """
        broker_connected = bool(
            self.prod_broker is not None
            and getattr(self.prod_broker, "is_connected", False)
        )
        if (
            not broker_connected
            and self.prod_broker is not None
            and str(getattr(self.settings, "MT5_BRIDGE_URL", "") or "").strip()
            and time_module.monotonic() >= getattr(self, "_next_prod_broker_probe_ts", 0.0)
        ):
            self._next_prod_broker_probe_ts = time_module.monotonic() + 30.0
            try:
                broker_connected = bool(self.prod_broker.connect())
            except Exception:
                broker_connected = False

        broker_cash: Optional[float] = None
        if broker_connected and self.prod_broker is not None:
            try:
                raw_bal = self.prod_broker.get_balance()
                if raw_bal is not None:
                    broker_cash = max(0.0, float(raw_bal))
            except Exception:
                broker_cash = None

        if broker_cash is None:
            cfg_bal = self.db.get_config("prod_broker_account_balance")
            if cfg_bal is not None:
                try:
                    broker_cash = max(0.0, float(cfg_bal))
                except (ValueError, TypeError):
                    broker_cash = 0.0
            elif hasattr(self, "prod_portfolio") and self.prod_portfolio is not None:
                broker_cash = max(0.0, float(self.prod_portfolio.free_cash))
            else:
                broker_cash = 0.0

        positions_value = 0.0
        positions_cost = 0.0
        positions_count = 0
        cum_pnl = 0.0
        mem_ai_cap = 0.0

        if hasattr(self, "prod_portfolio") and self.prod_portfolio is not None:
            with self.prod_portfolio._lock:
                positions_value = float(self.prod_portfolio.positions_market_value)
                positions_cost = float(self.prod_portfolio.allocated_capital)
                positions_count = len(self.prod_portfolio._positions)
                cum_pnl = float(self.prod_portfolio.cumulative_realized_pnl)
                mem_ai_cap = float(self.prod_portfolio.ai_allocated_capital)

        total_equity = broker_cash + positions_value
        cost_basis_equity = broker_cash + positions_cost

        if broker_connected or positions_count > 0 or abs(cum_pnl) > 1e-9:
            raw_ai_cap = mem_ai_cap
        else:
            cfg_ai = self.db.get_config("prod_ai_allocated_capital")
            if cfg_ai is not None:
                try:
                    raw_ai_cap = float(cfg_ai)
                except (ValueError, TypeError):
                    raw_ai_cap = mem_ai_cap
            else:
                raw_ai_cap = mem_ai_cap

        max_equity_ref = max(total_equity, cost_basis_equity)
        ai_cap = max(0.0, min(raw_ai_cap, max_equity_ref))
        vault_cash = min(broker_cash, max(0.0, cost_basis_equity - ai_cap))

        if ai_cap <= 0:
            buying_power = 0.0
        else:
            usable_cash = max(0.0, broker_cash - vault_cash)
            remaining_ceiling = max(0.0, ai_cap - positions_cost)
            buying_power = min(usable_cash, remaining_ceiling)

        d2_unsettled = 0.0
        next_d2_date: Optional[str] = None
        try:
            d2_fn = getattr(self.db, "get_d2_settlement_summary", None)
            if callable(d2_fn):
                d2_info = d2_fn(scheduler=getattr(self, "scheduler", None))
                if isinstance(d2_info, dict):
                    d2_unsettled = float(d2_info.get("unsettled_d2_cash", 0.0) or 0.0)
                    next_d2_date = d2_info.get("next_settlement_date")
        except Exception:
            d2_unsettled = 0.0

        settled_account_cash = max(0.0, broker_cash - d2_unsettled)
        settled_withdrawable = round(min(vault_cash, settled_account_cash), 2)

        return {
            "broker_balance": round(broker_cash, 2),
            "free_cash": round(broker_cash, 2),
            "allocated_capital": round(positions_value, 2),
            "allocated_cost": round(positions_cost, 2),
            "total_equity": round(total_equity, 2),
            "operational_equity": round(max(0.0, total_equity - vault_cash), 2),
            "ai_allocated_capital": round(ai_cap, 2),
            "vault_reserve_cash": round(vault_cash, 2),
            "unsettled_d2_cash": round(d2_unsettled, 2),
            "settled_withdrawable_cash": settled_withdrawable,
            "next_d2_settlement_date": next_d2_date,
            "buying_power": round(buying_power, 2),
            "cumulative_realized_pnl": round(cum_pnl, 2),
            "positions_count": positions_count,
            "broker_connected": broker_connected,
        }

    def get_capital_management_summary(self) -> dict[str, Any]:
        """Retorna resumo consolidado de gestão de capital (Homologação e Produção)."""
        return {
            "simulation": self.get_simulation_summary(),
            "production": self.get_production_summary(),
        }

    def adjust_production_ceiling(
        self,
        new_cap: float,
        username: str = "operador",
    ) -> dict[str, Any]:
        """
        Ajusta o teto operacional da IA na Produção validando contra `total_equity`,
        liquidando posições excedentes se necessário, atualizando `prod_portfolio`
        e persistindo em `system_config` e `capital_transactions`.
        """
        with self._engine_lock:
            new_cap = float(new_cap)
            if new_cap < 0:
                return {
                    "success": False,
                    "message": "O capital alocado para a IA não pode ser negativo.",
                }

            broker_connected = bool(
                self.prod_broker is not None
                and getattr(self.prod_broker, "is_connected", False)
            )
            if broker_connected and self.prod_broker is not None:
                try:
                    live_cash = max(0.0, float(self.prod_broker.get_balance()))
                    self.prod_portfolio.set_free_cash(live_cash)
                    self.db.set_config("prod_broker_account_balance", f"{live_cash:.2f}")
                except Exception as exc:
                    logger.warning("Falha ao consultar saldo do broker em adjust_production_ceiling: %s", exc)
            else:
                cfg_bal = self.db.get_config("prod_broker_account_balance")
                if cfg_bal is not None:
                    try:
                        self.prod_portfolio.set_free_cash(max(0.0, float(cfg_bal)))
                    except (ValueError, TypeError):
                        pass

            current_summary = self.get_production_summary()
            total_equity = float(current_summary["total_equity"])

            if new_cap > total_equity + 1e-6:
                return {
                    "success": False,
                    "message": (
                        f"O capital da IA (R$ {new_cap:.2f}) não pode ser maior que o "
                        f"patrimônio total na corretora (R$ {total_equity:.2f})."
                    ),
                }

            liquidated_items: list[str] = []
            current_pos_exposure = max(
                self.prod_portfolio.allocated_capital,
                self.prod_portfolio.positions_market_value,
            )

            if current_pos_exposure > new_cap + 1e-6 and len(self.prod_portfolio.positions) > 0:
                excess = current_pos_exposure - new_cap
                if self.prod_broker is None or (
                    not getattr(self.prod_broker, "is_connected", False)
                    and not self.prod_broker.connect()
                ):
                    return {
                        "success": False,
                        "message": (
                            "Broker de produção desconectado — não é possível liquidar "
                            "posições abertas excedentes para reduzir o teto operacional."
                        ),
                    }

                plan = self.risk_manager.select_positions_for_liquidation(
                    self.prod_portfolio.positions,
                    target_amount=excess,
                    market_data=self.market_data,
                    simulation_mode=False,
                )
                for item in plan:
                    ticker = item["ticker"]
                    qty = int(item["quantity"])
                    price = float(item["price"])
                    existing = self.prod_portfolio.get_position(ticker)
                    entry_before = existing.entry_price if existing else price
                    trade = self.prod_broker.sell(ticker, qty, price)
                    if trade is not None and trade.status == OrderStatus.FILLED:
                        fill_price = (
                            float(trade.price)
                            if (trade.price is not None and trade.price > 0)
                            else price
                        )
                        sold_qty = int(trade.quantity) if (trade.quantity and trade.quantity > 0) else qty
                        if existing and sold_qty < existing.quantity:
                            self.prod_portfolio.reduce_position(ticker, fill_price, sold_qty)
                        else:
                            self.prod_portfolio.remove_position(ticker, fill_price)
                            if not self.settings.simulation_mode:
                                self._set_prod_partial_exit_flag(ticker, False)
                            else:
                                cur_map = self._load_prod_partial_exit_map()
                                nt = normalize_b3_ticker(ticker)
                                if nt in cur_map:
                                    cur_map.pop(nt, None)
                                    self._save_prod_partial_exit_map(cur_map)

                        self._mark_sold_today(ticker, is_simulated=False)
                        realized_pnl = (fill_price - entry_before) * sold_qty
                        self._record_production_realized_pnl(
                            ticker=ticker,
                            pnl=realized_pnl,
                            reason=f"Liquidação p/ redução de teto IA ({username})",
                            entry_price=entry_before,
                            exit_price=fill_price,
                            quantity=sold_qty,
                            force_production=True,
                        )

                        self._insert_trade_safe(
                            ticker=trade.ticker,
                            action=trade.order_type.value,
                            quantity=sold_qty,
                            price=fill_price,
                            total_value=round(fill_price * sold_qty, 2),
                            stop_loss=existing.stop_loss if existing else None,
                            reasoning=f"Liquidação automática p/ redução de teto IA por {username}",
                            is_simulated=False,
                        )
                        liquidated_items.append(f"{sold_qty}x {trade.ticker} @ R$ {fill_price:.2f}")

                try:
                    refreshed_cash = max(0.0, float(self.prod_broker.get_balance()))
                    self.prod_portfolio.set_free_cash(refreshed_cash)
                    self.db.set_config("prod_broker_account_balance", f"{refreshed_cash:.2f}")
                except Exception:
                    pass

            self.prod_portfolio.adjust_ai_capital(new_cap)
            self._sync_manual_capital_delta_to_sod("production", self.prod_portfolio)

            ai_cap_saved = round(self.prod_portfolio.ai_allocated_capital, 2)
            vault_saved = round(self.prod_portfolio.vault_reserve_cash, 2)
            self.db.set_config("prod_ai_allocated_capital", f"{ai_cap_saved:.2f}")
            self.db.set_config("prod_vault_reserve_cash", f"{vault_saved:.2f}")

            details = f"Teto operacional ajustado para R$ {ai_cap_saved:.2f} por {username}"
            if liquidated_items:
                details += f" | Posições liquidadas: {', '.join(liquidated_items)}"

            self.db.record_capital_transaction(
                tx_type="AJUSTE_MANUAL",
                amount=ai_cap_saved,
                resulting_ai_capital=ai_cap_saved,
                resulting_vault=vault_saved,
                details=details,
                environment="production",
                is_simulated=False,
            )

            self._update_dashboard_state()
            updated = self.get_production_summary()
            return {
                "success": True,
                "message": "Teto operacional da IA atualizado com sucesso.",
                "liquidated_positions": liquidated_items,
                "production": updated,
                **updated,
            }

    def reset_homologation(
        self,
        new_capital: float | None = None,
        reset_positions: bool = True,
    ) -> float:
        """
        Redefine de forma thread-safe o capital e (opcionalmente) as posições de Homologação.
        """
        with self._engine_lock:
            if new_capital is not None and float(new_capital) > 0:
                target_cap = float(new_capital)
            else:
                cfg_cap = self.db.get_config("sim_initial_capital")
                target_cap = (
                    float(cfg_cap)
                    if cfg_cap is not None
                    else float(self.settings.CAPITAL_INICIAL)
                )

            self.settings.CAPITAL_INICIAL = target_cap
            self.db.set_config("sim_initial_capital", f"{target_cap:.2f}")

            if hasattr(self.sim_broker, "set_initial_capital"):
                self.sim_broker.set_initial_capital(target_cap, reset_positions=reset_positions)
            elif reset_positions and hasattr(self.sim_broker, "reset"):
                self.sim_broker.reset()

            with self.sim_portfolio._lock:
                if reset_positions:
                    self.sim_portfolio._positions.clear()
                    self.sim_portfolio.initial_capital = target_cap
                    self.sim_portfolio.set_free_cash(target_cap)
                    self.sim_portfolio.ai_allocated_capital = target_cap
                    self.sim_portfolio.vault_reserve_cash = 0.0
                    self.sim_portfolio.cumulative_realized_pnl = 0.0
                else:
                    allocated = self.sim_portfolio.allocated_capital
                    self.sim_portfolio.initial_capital = target_cap
                    self.sim_portfolio.set_free_cash(max(0.0, target_cap - allocated))
                    self.sim_portfolio.ai_allocated_capital = target_cap
                    self.sim_portfolio.vault_reserve_cash = 0.0
                    self.sim_portfolio.cumulative_realized_pnl = 0.0
                self.sim_portfolio._manual_capital_delta_accum = 0.0

            self._set_start_of_day_baseline(
                "homologation",
                self.sim_portfolio,
                self.sim_portfolio.total_value,
            )
            self._circuit_breaker_alerted["homologation"] = False
            self._env_circuit_breaker_active["homologation"] = False
            self._env_daily_pnl["homologation"] = 0.0
            if reset_positions:
                self._sold_today_by_env["homologation"] = set()

            if self.settings.simulation_mode:
                self.portfolio = self.sim_portfolio
                self.decision_engine.portfolio = self.sim_portfolio
                self.risk_manager.reset_daily_limits()

            try:
                self.db.record_capital_transaction(
                    tx_type="RESET_SIMULACAO",
                    amount=round(target_cap, 2),
                    resulting_ai_capital=round(target_cap, 2),
                    resulting_vault=0.0,
                    details=f"Reset de Homologação (reset_positions={reset_positions})",
                    environment="homologation",
                    is_simulated=True,
                )
            except Exception as exc:
                logger.debug("Aviso ao registrar transação de reset: %s", exc)

            self._update_dashboard_state()
            return target_cap

    def execute_smart_withdrawal(
        self,
        amount: float,
        mode: str = "instant",
        goal_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """
        Executa transferência inteligente de capital para o Cofre Reserva exclusivamente
        em Produção, concluindo a meta apenas se ao menos 99% do valor for efetivado.
        """
        with self._engine_lock:
            if self.settings.simulation_mode:
                return {
                    "success": False,
                    "message": "Saques inteligentes são exclusivos do ambiente de PRODUÇÃO.",
                }

            amount = max(0.0, float(amount))
            if amount <= 0:
                return {"success": False, "message": "Valor de saque inválido"}

            logger.info("⚡ Executando Saque Inteligente — Valor: R$ %.2f | Modo: %s", amount, mode)

            free_unlocked = max(0.0, self.portfolio.free_cash - self.portfolio.vault_reserve_cash)
            needed_from_sales = max(0.0, amount - free_unlocked)
            sold_positions_summary: list[str] = []
            total_sold_val: float = 0.0

            if needed_from_sales > 0:
                plan = self.risk_manager.select_positions_for_liquidation(
                    self.portfolio.positions,
                    target_amount=needed_from_sales,
                    market_data=self.market_data,
                    exclude_same_day_profit=(
                        mode == "on_profit"
                        and bool(getattr(self.settings, "AVOID_DAY_TRADE_TAX", True))
                    ),
                    simulation_mode=False,
                )
                for item in plan:
                    ticker = item["ticker"]
                    qty = int(item["quantity"])
                    price = float(item["price"])
                    existing = self.portfolio.get_position(ticker)
                    entry_before = existing.entry_price if existing else price
                    trade = self.broker.sell(ticker, qty, price)
                    if trade is not None and trade.status == OrderStatus.FILLED:
                        fill_price = (
                            float(trade.price)
                            if (trade.price is not None and trade.price > 0)
                            else price
                        )
                        sold_qty = int(trade.quantity) if (trade.quantity and trade.quantity > 0) else qty
                        if existing and sold_qty < existing.quantity:
                            self.portfolio.reduce_position(ticker, fill_price, sold_qty)
                        else:
                            self.portfolio.remove_position(ticker, fill_price)
                            self._set_prod_partial_exit_flag(ticker, False)

                        self._mark_sold_today(ticker, is_simulated=False)
                        realized_pnl = (fill_price - entry_before) * sold_qty
                        self._record_production_realized_pnl(
                            ticker=ticker,
                            pnl=realized_pnl,
                            reason=f"Liquidação inteligente p/ saque ({mode})",
                            entry_price=entry_before,
                            exit_price=fill_price,
                            quantity=sold_qty,
                            force_production=True,
                        )

                        sale_gross = round(fill_price * sold_qty, 2)
                        total_sold_val += sale_gross
                        self._insert_trade_safe(
                            ticker=trade.ticker,
                            action=trade.order_type.value,
                            quantity=sold_qty,
                            price=fill_price,
                            total_value=sale_gross,
                            stop_loss=existing.stop_loss if existing else None,
                            reasoning=f"Liquidação inteligente para saque ({mode})",
                            is_simulated=False,
                        )
                        sold_positions_summary.append(
                            f"{sold_qty}x {trade.ticker} @ R$ {fill_price:.2f}"
                        )

            transferred = self.portfolio.deposit_to_vault(amount)
            self._sync_manual_capital_delta_to_sod("production", self.prod_portfolio)

            prod_summary_now = self.get_production_summary()
            unsettled_d2_cash = float(prod_summary_now.get("unsettled_d2_cash", 0.0) or 0.0)
            settled_withdrawable_cash = float(
                prod_summary_now.get("settled_withdrawable_cash", 0.0) or 0.0
            )
            next_d2_date = prod_summary_now.get("next_d2_settlement_date")

            if transferred <= 0:
                return {
                    "success": False,
                    "transferred_to_vault": 0.0,
                    "total_vault": self.portfolio.vault_reserve_cash,
                    "ai_capital": self.portfolio.ai_allocated_capital,
                    "sold_positions": sold_positions_summary,
                    "unsettled_d2_cash": round(unsettled_d2_cash, 2),
                    "settled_withdrawable_cash": round(settled_withdrawable_cash, 2),
                    "next_d2_settlement_date": next_d2_date,
                    "message": "Saldo livre insuficiente para transferir ao Cofre Reserva.",
                }

            tx_type = "SAQUE_META_LUCRO" if mode == "on_profit" else "RESGATE_IMEDIATO"
            details = (
                f"Saque ({mode}). Vendas: "
                f"{', '.join(sold_positions_summary) if sold_positions_summary else 'Nenhuma (usou caixa livre)'}"
            )

            if goal_id is not None and transferred >= (amount * 0.99):
                self.db.complete_withdrawal_goal(goal_id, notes=details)

            self.db.set_config(
                "prod_ai_allocated_capital",
                f"{self.prod_portfolio.ai_allocated_capital:.2f}",
            )
            self.db.set_config(
                "prod_vault_reserve_cash",
                f"{self.prod_portfolio.vault_reserve_cash:.2f}",
            )
            self.db.set_config(
                "prod_broker_account_balance",
                f"{self.prod_portfolio.free_cash:.2f}",
            )
            self.db.record_capital_transaction(
                tx_type=tx_type,
                amount=round(transferred, 2),
                resulting_ai_capital=round(self.portfolio.ai_allocated_capital, 2),
                resulting_vault=round(self.portfolio.vault_reserve_cash, 2),
                details=details,
                environment="production",
                is_simulated=False,
            )

            prod_summary_after = self.get_production_summary()
            unsettled_d2_cash = float(
                prod_summary_after.get("unsettled_d2_cash", unsettled_d2_cash) or 0.0
            )
            settled_withdrawable_cash = float(
                prod_summary_after.get("settled_withdrawable_cash", settled_withdrawable_cash) or 0.0
            )
            next_d2_date = prod_summary_after.get("next_d2_settlement_date", next_d2_date)

            header_title = (
                "META DE SAQUE CONCLUÍDA!" if mode == "on_profit" else "SAQUE EXECUTADO COM SUCESSO!"
            )
            alert_msg = (
                f"🎉 {header_title}\n"
                f"💰 Valor protegido no Cofre: R$ {transferred:.2f}\n"
                f"🔒 Saldo Total no Cofre: R$ {self.portfolio.vault_reserve_cash:.2f}\n"
                f"🤖 Novo Capital da IA: R$ {self.portfolio.ai_allocated_capital:.2f}\n"
                f"ℹ️ {details}"
            )
            if total_sold_val > 0:
                d2_label = next_d2_date or "D+2 dias úteis"
                alert_msg += (
                    f"\n⏳ Liquidação B3 (D+2): R$ {unsettled_d2_cash:.2f} em liquidação "
                    f"(previsão: {d2_label}) | Disponível p/ resgate imediato: "
                    f"R$ {settled_withdrawable_cash:.2f}"
                )
            try:
                self.telegram.send_alert(alert_msg)
            except Exception:
                pass

            self._update_dashboard_state()
            return {
                "success": True,
                "transferred_to_vault": round(transferred, 2),
                "total_vault": round(self.portfolio.vault_reserve_cash, 2),
                "ai_capital": round(self.portfolio.ai_allocated_capital, 2),
                "sold_positions": sold_positions_summary,
                "unsettled_d2_cash": round(unsettled_d2_cash, 2),
                "settled_withdrawable_cash": round(settled_withdrawable_cash, 2),
                "next_d2_settlement_date": next_d2_date,
                "message": "Saque executado com sucesso e transferido para o cofre seguro",
            }

    def switch_environment(self, new_mode: str, user_name: str = "Usuário") -> bool:
        """
        Alterna entre Homologação e Produção sob lock.
        Recusa ativar Produção caso `prod_broker` real (MT5 / Bridge) não esteja conectado.
        """
        with self._engine_lock:
            new_mode = new_mode.strip().lower()
            if new_mode not in ("homologation", "production"):
                return False

            is_sim = (new_mode == "homologation")
            if is_sim == self.settings.simulation_mode:
                return True

            prev_env = "homologation" if self.settings.simulation_mode else "production"
            self._env_circuit_breaker_active[prev_env] = self.risk_manager.is_circuit_breaker_active
            self._env_daily_pnl[prev_env] = getattr(self.risk_manager, "_daily_pnl", 0.0)

            if not is_sim:
                if self.prod_broker is None:
                    self.prod_broker = self._create_production_broker()

                prod_connected = False
                if self.prod_broker is not None:
                    try:
                        prod_connected = bool(
                            getattr(self.prod_broker, "is_connected", False)
                            or self.prod_broker.connect()
                        )
                    except Exception as exc:
                        logger.error("Erro ao conectar broker de produção: %s", exc)
                        prod_connected = False

                if not prod_connected or self.prod_broker is None:
                    logger.error(
                        "Transição para PRODUÇÃO recusada (solicitada por %s): MT5 / Bridge indisponível.",
                        user_name,
                    )
                    self.settings.SIMULATION_MODE = True
                    self.settings.BROKER_MODE = "simulator"
                    self.broker = self.sim_broker
                    self.portfolio = self.sim_portfolio
                    self.decision_engine.portfolio = self.sim_portfolio
                    self.db.set_config("environment", "homologation")
                    try:
                        self.telegram.send_alert(
                            "⚠️ *TRANSIÇÃO PARA PRODUÇÃO RECUSADA*\n\n"
                            f"👤 *Solicitado por:* {user_name}\n"
                            "❌ *Motivo:* Terminal MetaTrader 5 / Bridge desconectado ou indisponível.\n"
                            "🛡️ O Córtex permanece operando em *HOMOLOGAÇÃO*."
                        )
                    except Exception:
                        pass
                    return False

            prev_mode_str = "PRODUÇÃO" if not self.settings.simulation_mode else "HOMOLOGAÇÃO"
            new_mode_str = "HOMOLOGAÇÃO" if is_sim else "PRODUÇÃO"
            logger.info("🔄 Alternando ambiente: %s → %s por %s", prev_mode_str, new_mode_str, user_name)

            self.settings.SIMULATION_MODE = is_sim
            self.settings.BROKER_MODE = "simulator" if is_sim else "mt5"
            self.db.set_config("environment", new_mode)
            self.db.set_config("last_environment", new_mode)

            if is_sim:
                self.broker = self.sim_broker
                self.portfolio = self.sim_portfolio
                self.decision_engine.portfolio = self.sim_portfolio
                self.broker.connect()
            else:
                assert self.prod_broker is not None
                self.broker = self.prod_broker
                self.portfolio = self.prod_portfolio
                self.decision_engine.portfolio = self.prod_portfolio

            self.risk_manager.restore_daily_state(
                daily_pnl=self._env_daily_pnl.get(new_mode, 0.0),
                circuit_breaker_active=self._env_circuit_breaker_active.get(new_mode, False),
            )

            self._sync_portfolio_with_broker()

            if (self._start_of_day_equity.get(new_mode) or 0.0) <= 0:
                init_eq = (
                    self.portfolio.total_value
                    if is_sim
                    else self.portfolio.operational_value
                )
                if init_eq > 0:
                    self._set_start_of_day_baseline(new_mode, self.portfolio, init_eq)

            try:
                dt_str = datetime.now(BRT).strftime('%d/%m/%Y %H:%M:%S')
                if is_sim:
                    alert_msg = (
                        "🔄 *ALTERAÇÃO DE AMBIENTE: PRODUÇÃO ➔ HOMOLOGAÇÃO*\n\n"
                        f"👤 *Alterado por:* {user_name}\n"
                        f"🕒 *Data/Hora:* {dt_str}\n"
                        "🛡️ *Status:* O Córtex IA agora opera em modo de *SIMULAÇÃO* (Paper Trading).\n"
                        "Nenhuma ordem com dinheiro real será enviada."
                    )
                else:
                    alert_msg = (
                        "⚠️ *ATENÇÃO: ALTERAÇÃO PARA PRODUÇÃO*\n\n"
                        f"👤 *Alterado por:* {user_name}\n"
                        f"🕒 *Data/Hora:* {dt_str}\n"
                        "💰 *Status:* O Córtex IA agora opera em modo de *PRODUÇÃO B3*.\n"
                        "Ordens executadas a partir de agora envolverão *dinheiro real* na corretora."
                    )
                self.telegram.send_alert(alert_msg)
            except Exception:
                pass

            return True
