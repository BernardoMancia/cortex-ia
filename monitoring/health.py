"""
Monitor de saúde do Projeto Córtex.

Monitora CPU, RAM e disco em background thread, envia alertas
via Telegram quando thresholds são ultrapassados e armazena
métricas no banco de dados.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Optional

import psutil

from models.data_models import BRT, HealthReport

logger = logging.getLogger('cortex.monitoring.health')


class HealthMonitor:
    """Monitor de saúde do sistema com thread daemon."""

    # Thresholds de alerta
    CPU_THRESHOLD: float = 90.0
    RAM_THRESHOLD: float = 90.0
    DISK_THRESHOLD: float = 95.0

    # Intervalo entre verificações (segundos)
    CHECK_INTERVAL: int = 60

    # Cooldown entre alertas (segundos) — evitar spam no Telegram
    ALERT_COOLDOWN: int = 300  # 5 minutos

    def __init__(
        self,
        telegram: Optional[Any] = None,
        db: Optional[Any] = None,
        check_interval: int = 60,
        alert_cooldown: int = 300,
    ) -> None:
        """
        Inicializa o monitor de saúde.

        Args:
            telegram: Instância do TelegramNotifier (opcional).
            db: Instância do DatabaseManager (opcional).
            check_interval: Intervalo entre verificações em segundos.
            alert_cooldown: Cooldown mínimo entre alertas em segundos.
        """
        self.telegram = telegram
        self.db = db
        self.CHECK_INTERVAL = check_interval
        self.ALERT_COOLDOWN = alert_cooldown

        self._daemon_thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._last_alert_time: Optional[datetime] = None
        self._last_report: Optional[HealthReport] = None

        logger.info(
            'HealthMonitor inicializado — intervalo: %ds, cooldown: %ds',
            self.CHECK_INTERVAL, self.ALERT_COOLDOWN,
        )

    def check(self) -> HealthReport:
        """
        Executa verificação de saúde do sistema.

        Coleta métricas de CPU, RAM e disco via psutil.

        Returns:
            HealthReport com métricas atuais.
        """
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            ram = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
        except Exception as e:
            logger.error('Erro ao coletar métricas do sistema: %s', e)
            return HealthReport(
                cpu_percent=0.0,
                ram_percent=0.0,
                disk_percent=0.0,
                is_healthy=False,
                alerts=[f'Erro ao coletar métricas: {e}'],
            )

        report = HealthReport(
            cpu_percent=cpu_percent,
            ram_percent=ram.percent,
            disk_percent=disk.percent,
        )

        with self._lock:
            self._last_report = report

        # Logar métricas
        logger.debug(
            'Saúde: CPU=%.1f%% RAM=%.1f%% Disco=%.1f%% — %s',
            report.cpu_percent, report.ram_percent, report.disk_percent,
            'OK' if report.is_healthy else 'ALERTA',
        )

        # Registrar no banco de dados
        if self.db is not None:
            try:
                self.db.insert_health(
                    report.cpu_percent, report.ram_percent, report.disk_percent
                )
            except Exception as db_err:
                logger.error('Erro ao salvar métricas no DB: %s', db_err)

        # Enviar alerta se necessário
        if not report.is_healthy:
            self._handle_alert(report)

        return report

    def _handle_alert(self, report: HealthReport) -> None:
        """
        Processa alerta de saúde respeitando o cooldown.

        Args:
            report: Relatório com alertas ativos.
        """
        now = datetime.now(BRT)

        with self._lock:
            if self._last_alert_time is not None:
                elapsed = (now - self._last_alert_time).total_seconds()
                if elapsed < self.ALERT_COOLDOWN:
                    logger.debug(
                        'Alerta suprimido — cooldown ativo (%.0fs restantes)',
                        self.ALERT_COOLDOWN - elapsed,
                    )
                    return

            self._last_alert_time = now

        logger.warning(
            '⚠️ Sistema em alerta: %s',
            ', '.join(report.alerts),
        )

        # Enviar via Telegram
        if self.telegram is not None:
            try:
                self.telegram.send_health_alert(
                    cpu=report.cpu_percent,
                    ram=report.ram_percent,
                    disk=report.disk_percent,
                )
            except Exception as tg_err:
                logger.error('Erro ao enviar alerta Telegram: %s', tg_err)

    def start_daemon(self) -> None:
        """
        Inicia thread daemon de monitoramento em background.

        A thread roda com daemon=True para morrer junto com o
        processo principal.
        """
        if self._daemon_thread is not None and self._daemon_thread.is_alive():
            logger.warning('Daemon de monitoramento já está em execução')
            return

        self._running = True
        self._daemon_thread = threading.Thread(
            target=self._daemon_loop,
            name='cortex-health-monitor',
            daemon=True,
        )
        self._daemon_thread.start()
        logger.info('Daemon de monitoramento iniciado')

    def stop_daemon(self) -> None:
        """Para o daemon de monitoramento."""
        self._running = False
        if self._daemon_thread is not None:
            self._daemon_thread.join(timeout=5)
            logger.info('Daemon de monitoramento parado')

    def _daemon_loop(self) -> None:
        """Loop principal do daemon de monitoramento."""
        logger.info('Health monitor daemon loop iniciado')
        while self._running:
            try:
                self.check()
            except Exception as e:
                logger.error('Erro no daemon de monitoramento: %s', e)
            time.sleep(self.CHECK_INTERVAL)
        logger.info('Health monitor daemon loop encerrado')

    @property
    def last_report(self) -> Optional[HealthReport]:
        """Retorna o último relatório de saúde."""
        with self._lock:
            return self._last_report

    def get_status(self) -> dict[str, Any]:
        """
        Retorna status atual do monitoramento como dicionário.

        Returns:
            Dicionário com métricas e status do daemon.
        """
        with self._lock:
            report = self._last_report

        if report is None:
            return {
                'status': 'sem dados',
                'daemon_running': self._running,
            }

        return {
            'cpu_percent': report.cpu_percent,
            'ram_percent': report.ram_percent,
            'disk_percent': report.disk_percent,
            'is_healthy': report.is_healthy,
            'alerts': report.alerts,
            'timestamp': report.timestamp.isoformat(),
            'daemon_running': self._running,
        }
