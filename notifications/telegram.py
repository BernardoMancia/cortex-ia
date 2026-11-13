"""
Notificador Telegram do Projeto Córtex.

Envia alertas formatados para o chat do Telegram configurado:
abertura/fechamento de mercado, operações, stop-loss, volatilidade,
saúde do sistema e relatórios diários.
"""

from __future__ import annotations

import html
import logging
import threading
from datetime import datetime
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter

from models.data_models import Action, BRT, Decision, PortfolioSummary

logger = logging.getLogger('cortex.notifications.telegram')

_ORIG_REQUESTS_POST = requests.post
_ORIG_REQUESTS_GET = requests.get


class _IPv4HTTPAdapter(HTTPAdapter):
    """HTTPAdapter isolado para forçar binding IPv4 apenas na sessão do Telegram."""

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **pool_kwargs: Any) -> None:
        pool_kwargs.setdefault('source_address', ('0.0.0.0', 0))
        super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)


class TelegramNotifier:
    """Notificador de alertas via Telegram Bot API."""

    API_BASE = 'https://api.telegram.org/bot{token}/sendMessage'
    SEND_TIMEOUT: tuple[float, float] = (3.05, 5.0)
    POLL_TIMEOUT_SECONDS: int = 10

    def __init__(
        self,
        token: str,
        chat_id: str,
        db: Optional[Any] = None,
        channel_id: str = "",
    ) -> None:
        """
        Inicializa o notificador Telegram.

        Args:
            token: Token do bot Telegram.
            chat_id: ID do chat principal/grupo.
            db: Instância do DatabaseManager para logging (opcional).
            channel_id: ID do canal para avisos gerais (opcional).
        """
        self.token = (token or '').strip()
        self.chat_id = str(chat_id).strip() if chat_id else ''
        self.channel_id = str(channel_id).strip() if channel_id else ''
        self.db = db
        self._enabled = bool(self.token and self.chat_id)

        self.command_callbacks: dict[str, Any] = {}
        self._command_parse_modes: dict[str, Optional[str]] = {}
        self._polling_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._tls = threading.local()
        self._sessions_lock = threading.Lock()
        self._sessions: list[requests.Session] = []
        self._primary_session: Optional[requests.Session] = None
        self._primary_ipv4_session: Optional[requests.Session] = None
        # Inicializa sessões da thread principal
        self._primary_session = self._get_session()
        self._primary_ipv4_session = self._get_ipv4_session()

        if self._enabled:
            logger.info('TelegramNotifier configurado — chat_id: %s', self.chat_id)
        else:
            logger.warning(
                'TelegramNotifier DESABILITADO — token ou chat_id não configurado'
            )

    def _get_session(self) -> requests.Session:
        primary = self._primary_session
        if primary is not None and (
            'post' in getattr(primary, '__dict__', {}) or 'get' in getattr(primary, '__dict__', {})
        ):
            return primary
        sess = getattr(self._tls, 'session', None)
        if sess is None:
            sess = requests.Session()
            adapter = HTTPAdapter(pool_connections=2, pool_maxsize=4)
            sess.mount('https://', adapter)
            sess.mount('http://', adapter)
            self._tls.session = sess
            with self._sessions_lock:
                self._sessions.append(sess)
        return sess

    def _get_ipv4_session(self) -> requests.Session:
        primary = self._primary_ipv4_session
        if primary is not None and (
            'post' in getattr(primary, '__dict__', {}) or 'get' in getattr(primary, '__dict__', {})
        ):
            return primary
        sess = getattr(self._tls, 'ipv4_session', None)
        if sess is None:
            sess = requests.Session()
            ipv4_adapter = _IPv4HTTPAdapter(pool_connections=2, pool_maxsize=4)
            sess.mount('https://', ipv4_adapter)
            sess.mount('http://', ipv4_adapter)
            self._tls.ipv4_session = sess
            with self._sessions_lock:
                self._sessions.append(sess)
        return sess

    @property
    def _session(self) -> requests.Session:
        return self._get_session()

    @_session.setter
    def _session(self, session: requests.Session) -> None:
        self._tls.session = session
        self._primary_session = session
        with self._sessions_lock:
            if session not in self._sessions:
                self._sessions.append(session)

    @property
    def _ipv4_session(self) -> requests.Session:
        return self._get_ipv4_session()

    @_ipv4_session.setter
    def _ipv4_session(self, session: requests.Session) -> None:
        self._tls.ipv4_session = session
        self._primary_ipv4_session = session
        with self._sessions_lock:
            if session not in self._sessions:
                self._sessions.append(session)

    def close(self) -> None:
        """Encerra a thread de polling e fecha todas as sessões HTTP criadas pelas threads."""
        self.stop_polling()
        with self._sessions_lock:
            sessions = list(self._sessions)
            self._sessions.clear()
        self._primary_session = None
        self._primary_ipv4_session = None
        for sess in sessions:
            try:
                sess.close()
            except Exception as exc:
                logger.debug('Erro ao fechar sessão HTTP do Telegram: %s', exc)
        self._tls = threading.local()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _scrub(self, value: Any) -> str:
        """Remove o bot token e outros segredos antes de registrar em logs."""
        raw = str(value or '')
        if self.token:
            raw = raw.replace(self.token, '***')
        try:
            from utils.log_db_handler import scrub_secrets

            raw = scrub_secrets(raw)
        except Exception:
            pass
        return raw

    @staticmethod
    def _escape_markdown(text: str) -> str:
        """
        Escapa caracteres especiais do Markdown legado (v1) do Telegram.
        Parênteses '(' e ')' NÃO são caracteres especiais no Markdown v1 e
        não devem ser escapados para evitar '\\(' e '\\)' literais.
        """
        if not text:
            return ''
        escaped = str(text)
        for char in ('_', '*', '`', '['):
            escaped = escaped.replace(char, f'\\{char}')
        return escaped

    @staticmethod
    def _escape_html(text: Any) -> str:
        """Escapa texto dinâmico com segurança para parse_mode='HTML'."""
        if text is None:
            return ''
        return html.escape(str(text), quote=False)

    def _http_post(self, url: str, json_payload: dict[str, Any], timeout: tuple[float, float] | float) -> requests.Response:
        if requests.post is not _ORIG_REQUESTS_POST:
            return requests.post(url, json=json_payload, timeout=timeout)
        try:
            return self._session.post(url, json=json_payload, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return self._ipv4_session.post(url, json=json_payload, timeout=timeout)

    def _http_get(self, url: str, params: dict[str, Any], timeout: tuple[float, float] | float) -> requests.Response:
        if requests.get is not _ORIG_REQUESTS_GET:
            return requests.get(url, params=params, timeout=timeout)
        try:
            return self._session.get(url, params=params, timeout=timeout)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            return self._ipv4_session.get(url, params=params, timeout=timeout)

    def register_command(
        self,
        command: str,
        callback: Any,
        parse_mode: Optional[str] = 'Markdown',
    ) -> None:
        """Registra um callback para um comando específico (ex: '/status')."""
        self.command_callbacks[command] = callback
        self._command_parse_modes[command] = parse_mode

    def start_polling(self) -> None:
        """Inicia a thread de polling para receber comandos interativos (idempotente)."""
        if not self._enabled:
            return
        if self._polling_thread is not None and self._polling_thread.is_alive():
            logger.debug('Telegram polling já está em execução.')
            return
        self._stop_event.clear()
        self._polling_thread = threading.Thread(
            target=self._poll_updates,
            name='cortex-telegram-poller',
            daemon=True,
        )
        self._polling_thread.start()
        logger.info('Telegram polling iniciado para comandos.')

    def stop_polling(self) -> None:
        """Para a thread de polling prontamente."""
        self._stop_event.set()
        if self._polling_thread is not None:
            self._polling_thread.join(timeout=3.0)

    def _authorized_chats(self) -> set[str]:
        """Autoriza exclusivamente `self.chat_id` para execução de comandos (nunca `channel_id`)."""
        return {str(self.chat_id)} if self.chat_id else set()

    def _process_updates(self, updates: list[dict[str, Any]], offset: int = 0) -> int:
        """Processa lote de updates do Telegram ignorando `channel_post` e validando `chat_id`."""
        authorized = self._authorized_chats()
        for item in updates or []:
            if not isinstance(item, dict):
                continue
            if 'update_id' in item:
                offset = int(item['update_id']) + 1
            if 'channel_post' in item or 'edited_channel_post' in item:
                continue
            msg = item.get('message')
            if not isinstance(msg, dict):
                continue

            chat_id = str(msg.get('chat', {}).get('id', '')).strip()
            text = str(msg.get('text') or '')

            # Valida autorização ANTES de registrar qualquer conteúdo da mensagem
            if not chat_id or chat_id not in authorized:
                if text.startswith('/'):
                    logger.warning(
                        'Tentativa de acesso não autorizada ao bot Telegram por chat_id=%s',
                        chat_id or 'desconhecido',
                    )
                continue

            if text.startswith('/'):
                cmd = text.split(' ', 1)[0].split('@', 1)[0].strip()
                safe_cmd = cmd.replace('\r', '').replace('\n', '')[:64]
                logger.info('Comando Telegram autorizado recebido: %s (chat_id=%s)', safe_cmd, chat_id)

                if cmd in self.command_callbacks:
                    try:
                        response = self.command_callbacks[cmd]()
                        if response:
                            cmd_mode = self._command_parse_modes.get(cmd, 'Markdown')
                            self._send(str(response), parse_mode=cmd_mode, target_chat_id=chat_id)
                    except Exception as cmd_err:
                        clean_err = self._scrub(cmd_err)
                        logger.error('Erro no comando %s: %s', safe_cmd, clean_err)
                        self._send(
                            f'❌ Erro ao executar comando: {self._escape_html(clean_err)}',
                            parse_mode='HTML',
                            target_chat_id=chat_id,
                        )
                else:
                    available = ', '.join(sorted(self.command_callbacks.keys()))
                    self._send(
                        f'⚠️ Comando desconhecido: <code>{self._escape_html(safe_cmd)}</code>\n'
                        f'Comandos disponíveis: {self._escape_html(available)}',
                        parse_mode='HTML',
                        target_chat_id=chat_id,
                    )
        return offset

    def _poll_updates(self) -> None:
        offset = 0
        url = f'https://api.telegram.org/bot{self.token}/getUpdates'
        poll_timeout = self.POLL_TIMEOUT_SECONDS
        http_timeout = (3.05, float(poll_timeout + 5))

        while not self._stop_event.is_set():
            try:
                resp = self._http_get(
                    url,
                    params={'offset': offset, 'timeout': poll_timeout},
                    timeout=http_timeout,
                )
                if self._stop_event.is_set():
                    break

                if resp.status_code == 200:
                    data = resp.json()
                    offset = self._process_updates(data.get('result', []), offset=offset)
                else:
                    logger.warning(
                        'Polling Telegram retornou HTTP %d: %s',
                        resp.status_code,
                        self._scrub(resp.text[:200]),
                    )
                    if self._stop_event.wait(5.0):
                        break
            except Exception as err:
                if self._stop_event.is_set():
                    break
                logger.error('Erro no polling do Telegram: %s', self._scrub(err))
                if self._stop_event.wait(5.0):
                    break

    def _send(
        self,
        text: str,
        parse_mode: Optional[str] = 'Markdown',
        target_chat_id: Optional[str] = None,
    ) -> bool:
        """
        Envia mensagem via Telegram Bot API com timeout curto e sanitização de erros.
        """
        if not self._enabled:
            logger.debug('Telegram desabilitado — mensagem não enviada')
            return False

        url = self.API_BASE.format(token=self.token)
        payload: dict[str, Any] = {
            'chat_id': target_chat_id or self.chat_id,
            'text': text,
            'disable_web_page_preview': True,
        }
        if parse_mode:
            payload['parse_mode'] = parse_mode

        try:
            response = self._http_post(url, json_payload=payload, timeout=self.SEND_TIMEOUT)
            success = response.status_code == 200

            if not success and response.status_code == 400 and parse_mode:
                payload.pop('parse_mode', None)
                response = self._http_post(url, json_payload=payload, timeout=self.SEND_TIMEOUT)
                success = response.status_code == 200

            if success:
                logger.debug('Mensagem Telegram enviada com sucesso')
            else:
                logger.warning(
                    'Falha ao enviar Telegram: HTTP %d — %s',
                    response.status_code,
                    self._scrub(response.text[:200]),
                )

            if self.db is not None:
                try:
                    self.db.insert_telegram_log(
                        message_type='send',
                        content=self._scrub(text)[:500],
                        success=success,
                    )
                except Exception as db_err:
                    logger.error('Erro ao registrar log Telegram no DB: %s', self._scrub(db_err))

            return success

        except requests.exceptions.Timeout:
            logger.warning('Timeout ao enviar mensagem Telegram')
            self._log_failure('timeout', text)
            return False
        except requests.exceptions.ConnectionError as conn_err:
            logger.warning('Erro de conexão com Telegram API: %s', self._scrub(conn_err))
            self._log_failure('connection_error', text)
            return False
        except Exception as err:
            logger.error('Erro inesperado ao enviar Telegram: %s', self._scrub(err))
            self._log_failure('generic_error', text)
            return False

    def dispatch_async(
        self,
        text: str,
        parse_mode: Optional[str] = 'HTML',
        target_chat_id: Optional[str] = None,
    ) -> None:
        """Dispara alerta não-crítico em background sem bloquear a thread chamadora."""
        if not self._enabled:
            return
        threading.Thread(
            target=self._send,
            args=(text, parse_mode, target_chat_id),
            name='cortex-telegram-dispatch',
            daemon=True,
        ).start()

    def send_to_channel(self, text: str) -> bool:
        """Envia uma mensagem para o canal (se configurado), senão para o chat."""
        target = self.channel_id if self.channel_id else self.chat_id
        if not target:
            return False
        return self._send(text, target_chat_id=target)

    def _log_failure(self, error_type: str, text: str) -> None:
        """Registra falha de envio no banco de dados."""
        if self.db is not None:
            try:
                self.db.insert_telegram_log(
                    message_type=f'send_failed_{error_type}',
                    content=self._scrub(text)[:500],
                    success=False,
                )
            except Exception:
                pass

    def send_alert(self, text: str) -> bool:
        """Envia alerta genérico."""
        return self._send(text)

    def send_market_open_alert(
        self,
        portfolio_summary: PortfolioSummary,
        simulation: bool = True,
    ) -> bool:
        """Envia alerta estruturado de abertura do mercado."""
        now = datetime.now(BRT)
        mode = '🎮 HOMOLOGAÇÃO (SIMULADOR)' if simulation else '🟢 PRODUÇÃO (B3 / CLEAR)'

        positions_summary = 'Nenhum ativo sob custódia'
        if portfolio_summary.positions:
            parts: list[str] = []
            for p in portfolio_summary.positions:
                safe_ticker = self._escape_html(p.ticker)
                parts.append(f'<code>{safe_ticker}</code> ({p.quantity}x @ R$ {p.entry_price:.2f})')
            positions_summary = '\n• ' + '\n• '.join(parts)

        text = (
            f'🟢 <b>CÓRTEX IA — PREGÃO B3 ABERTO</b>\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'📅 <b>Data:</b> {now.strftime("%d/%m/%Y")} | 🕒 <b>Hora:</b> {now.strftime("%H:%M:%S")} BRT\n'
            f'💰 <b>Patrimônio Total:</b> R$ {portfolio_summary.total_value:,.2f}\n'
            f'💵 <b>Caixa Disponível:</b> R$ {portfolio_summary.free_cash:,.2f}\n'
            f'📊 <b>Custódia de Ações:</b>\n{positions_summary}\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'⚡ <b>Status:</b> MOTOR OPERACIONAL ATIVO\n'
            f'🛡️ <b>Ambiente:</b> {mode}'
        )

        logger.info('Enviando alerta de abertura do mercado')
        return self._send(text, parse_mode='HTML')

    def send_volatility_alert(
        self,
        ticker: str,
        old_price: float,
        new_price: float,
        variation_pct: float,
        thinking: str,
    ) -> bool:
        """Envia alerta de volatilidade anormal com raciocínio da IA."""
        now = datetime.now(BRT)
        direction = '+' if variation_pct > 0 else ''
        emoji = '📈' if variation_pct > 0 else '📉'
        safe_ticker = self._escape_html(ticker)
        safe_thinking = self._escape_html(thinking)

        text = (
            f'{emoji} <b>ALERTA DE VOLATILIDADE — {safe_ticker}</b>\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'🕒 <b>Horário:</b> {now.strftime("%d/%m/%Y %H:%M:%S")} BRT\n'
            f'📊 <b>Variação:</b> <code>{direction}{variation_pct:.2f}%</code>\n'
            f'💵 <b>Movimento:</b> R$ {old_price:.2f} ➔ R$ {new_price:.2f}\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'🧠 <b>ANÁLISE QUANTITATIVA:</b>\n'
            f'<i>{safe_thinking}</i>'
        )

        logger.info(
            'Enviando alerta de volatilidade: %s %s%.2f%%',
            ticker, direction, variation_pct,
        )
        return self._send(text, parse_mode='HTML')

    def send_trade_alert(
        self,
        decision: Decision,
        simulation: bool = True,
    ) -> bool:
        """Envia alerta estruturado de ordem executada na B3."""
        now = datetime.now(BRT)
        mode = '🎮 HOMOLOGAÇÃO (SIMULADOR)' if simulation else '🟢 PRODUÇÃO (B3 / CLEAR)'
        is_buy = decision.action == Action.BUY
        action_title = 'COMPRA EXECUTADA' if is_buy else 'VENDA EXECUTADA'
        emoji = '🟢 🛒' if is_buy else '🔴 💸'
        total_val = decision.quantity * decision.price

        sl_text = f'R$ {decision.stop_loss:.2f}'
        if decision.price > 0 and decision.stop_loss > 0:
            sl_pct = ((decision.stop_loss - decision.price) / decision.price) * 100
            sl_text += f' ({sl_pct:.1f}%)'

        safe_ticker = self._escape_html(decision.ticker)
        safe_reasoning = self._escape_html(decision.reasoning)

        text = (
            f'{emoji} <b>{action_title} — {safe_ticker}</b>\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'🏷️ <b>Ativo:</b> <code>{safe_ticker}</code>\n'
            f'📦 <b>Quantidade:</b> {decision.quantity} ações\n'
            f'💵 <b>Preço Executado:</b> R$ {decision.price:.2f}\n'
            f'💰 <b>Volume Total:</b> R$ {total_val:,.2f}\n'
            f'🛡️ <b>Stop-Loss:</b> {self._escape_html(sl_text)}\n'
            f'🎯 <b>Confiança da IA:</b> {(decision.confidence * 100):.0f}%\n'
            f'🕒 <b>Horário:</b> {now.strftime("%d/%m/%Y %H:%M:%S")} BRT\n'
            f'🔧 <b>Ambiente:</b> {mode}\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'🧠 <b>RACIOCÍNIO DO CÉREBRO IA:</b>\n'
            f'<i>{safe_reasoning}</i>'
        )

        logger.info(
            'Enviando alerta de trade Telegram: %s %s %d ações @ R$ %.2f',
            action_title, decision.ticker, decision.quantity, decision.price,
        )
        return self._send(text, parse_mode='HTML')

    def send_closing_report(
        self,
        report: dict[str, Any],
        simulation: bool = True,
    ) -> bool:
        """Envia relatório diário de fechamento do pregão."""
        now = datetime.now(BRT)
        mode = '🎮 HOMOLOGAÇÃO (SIMULADOR)' if simulation else '🟢 PRODUÇÃO (B3 / CLEAR)'

        total_value = report.get('total_value', 0.0)
        pnl_pct = report.get('pnl_percent', 0.0)
        pnl_sign = '+' if pnl_pct >= 0 else ''
        pnl_emoji = '🟢' if pnl_pct >= 0 else '🔴'

        text = (
            f'📋 <b>RELATÓRIO DIÁRIO DE FECHAMENTO — CÓRTEX</b>\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'📅 <b>Data:</b> {now.strftime("%d/%m/%Y")} | Pregão B3 Encerrado\n'
            f'\n'
            f'🛒 <b>Compras no Dia:</b> {report.get("total_buys", 0)} operações\n'
            f'💸 <b>Vendas no Dia:</b> {report.get("total_sells", 0)} operações\n'
            f'💵 <b>Caixa Disponível:</b> R$ {report.get("free_cash", 0.0):,.2f}\n'
            f'🔒 <b>Capital Alocado em Ações:</b> R$ {report.get("allocated_capital", 0.0):,.2f}\n'
            f'🏦 <b>Patrimônio Total:</b> R$ {total_value:,.2f} ({pnl_emoji} {pnl_sign}{pnl_pct:.2f}%)\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'✅ <b>Status:</b> PREGÃO FINALIZADO — SISTEMA HIBERNANDO\n'
            f'🛡️ <b>Ambiente:</b> {mode}'
        )

        logger.info('Enviando relatório de fechamento Telegram')
        return self._send(text, parse_mode='HTML')

    def send_emergency_alert(self, ticker: str, reason: str) -> bool:
        """Envia alerta de stop-loss / liquidação emergencial."""
        now = datetime.now(BRT)
        safe_ticker = self._escape_html(ticker)
        safe_reason = self._escape_html(reason)

        text = (
            f'🚨 <b>STOP-LOSS ATIVADO — {safe_ticker}</b>\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'⚠️ <b>Liquidação compulsória de proteção executada!</b>\n'
            f'📊 <b>Motivo:</b> {safe_reason}\n'
            f'🕒 <b>Horário:</b> {now.strftime("%d/%m/%Y %H:%M:%S")} BRT'
        )

        logger.warning(
            'Enviando alerta de emergência Telegram: %s — %s',
            ticker,
            self._scrub(reason),
        )
        return self._send(text, parse_mode='HTML')

    def send_health_alert(
        self,
        cpu: float,
        ram: float,
        disk: float,
    ) -> bool:
        """Envia alerta crítico de infraestrutura da VPS."""
        now = datetime.now(BRT)

        text = (
            f'⚠️ <b>ALERTA DE INFRAESTRUTURA (VPS)</b>\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'🖥️ <b>Uso de CPU:</b> {cpu:.1f}%\n'
            f'💾 <b>Uso de RAM:</b> {ram:.1f}%\n'
            f'💿 <b>Uso de Disco:</b> {disk:.1f}%\n'
            f'🕒 <b>Data/Hora:</b> {now.strftime("%d/%m/%Y %H:%M:%S")} BRT'
        )

        logger.warning(
            'Enviando alerta de saúde Telegram: CPU=%.1f%% RAM=%.1f%% Disco=%.1f%%',
            cpu, ram, disk,
        )
        return self._send(text, parse_mode='HTML')
