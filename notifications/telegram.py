"""
Notificador Telegram do Projeto Córtex.

Envia alertas formatados para o chat do Telegram configurado:
abertura/fechamento de mercado, operações, stop-loss, volatilidade,
saúde do sistema e relatórios diários.
"""

from __future__ import annotations

import logging
import threading
import time
import socket
import urllib3.util.connection as urllib3_cn
from datetime import datetime
from typing import Any, Optional

# Força o urllib3 a usar apenas IPv4 (corrige timeout em VPS com IPv6 quebrado para o Telegram)
def allowed_gai_family():
    return socket.AF_INET
urllib3_cn.allowed_gai_family = allowed_gai_family

import requests

from models.data_models import Action, BRT, Decision, PortfolioSummary

logger = logging.getLogger('cortex.notifications.telegram')


class TelegramNotifier:
    """Notificador de alertas via Telegram Bot API."""

    API_BASE = 'https://api.telegram.org/bot{token}/sendMessage'

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
        self.token = token
        self.chat_id = chat_id
        self.channel_id = channel_id
        self.db = db
        self._enabled = bool(token and chat_id)
        
        self.command_callbacks = {}
        self._polling_thread = None
        self._stop_event = threading.Event()

        if self._enabled:
            logger.info('TelegramNotifier configurado — chat_id: %s', chat_id)
        else:
            logger.warning(
                'TelegramNotifier DESABILITADO — token ou chat_id não configurado'
            )

    @staticmethod
    def _escape_markdown(text: str) -> str:
        """Escapa caracteres especiais do Markdown para Telegram."""
        if not text:
            return ''
        for char in ('_', '*', '`', '[', ']', '(', ')'):
            text = text.replace(char, f'\\{char}')
        return text

    def register_command(self, command: str, callback: Any) -> None:
        """Registra um callback para um comando específico (ex: '/status')."""
        self.command_callbacks[command] = callback
        
    def start_polling(self) -> None:
        """Inicia a thread de polling para receber comandos interativos."""
        if not self._enabled:
            return
        self._stop_event.clear()
        self._polling_thread = threading.Thread(target=self._poll_updates, daemon=True)
        self._polling_thread.start()
        logger.info("Telegram polling iniciado para comandos.")

    def stop_polling(self) -> None:
        """Para a thread de polling."""
        self._stop_event.set()
        if self._polling_thread:
            self._polling_thread.join(timeout=2.0)
            
    def _poll_updates(self) -> None:
        offset = 0
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        while not self._stop_event.is_set():
            try:
                # Usa long polling de 30s
                resp = requests.get(url, params={'offset': offset, 'timeout': 30}, timeout=40)
                if resp.status_code == 200:
                    data = resp.json()
                    for item in data.get('result', []):
                        offset = item['update_id'] + 1
                        msg = item.get('message') or item.get('channel_post', {})
                        text = msg.get('text', '')
                        chat_id = str(msg.get('chat', {}).get('id', ''))
                        
                        logger.info(f"Telegram MSG: chat_id={chat_id}, text={text}")
                        
                        if text.startswith('/'):
                            # Trata @botname se enviado em grupo
                            parts = text.split(' ')
                            cmd = parts[0].split('@')[0]
                            
                            logger.info(f"Comando recebido: {cmd} de chat: {chat_id}")
                            
                            if True: # Always allow commands, or you could restrict to a list of allowed chats
                                if cmd in self.command_callbacks:
                                    try:
                                        logger.info(f"Executando callback para {cmd}")
                                        response = self.command_callbacks[cmd]()
                                        if response:
                                            self._send(response, target_chat_id=chat_id)
                                    except Exception as e:
                                        logger.error(f"Erro no comando {cmd}: {e}")
                                        self._send(f"❌ Erro ao executar comando: {e}", target_chat_id=chat_id)
                                else:
                                    available = ', '.join(self.command_callbacks.keys())
                                    self._send(f"⚠️ Comando desconhecido: {cmd}\nComandos disponíveis: {available}", target_chat_id=chat_id)
            except Exception as e:
                logger.error(f"Erro no polling do Telegram: {e}")
                time.sleep(5)

    def _send(self, text: str, parse_mode: str = 'Markdown', target_chat_id: str = None) -> bool:
        """
        Envia mensagem via Telegram Bot API.

        Args:
            text: Texto da mensagem (suporta Markdown).
            parse_mode: Modo de parsing ('Markdown' ou 'HTML').
            target_chat_id: Opcional. Sobrescreve o chat_id padrão.

        Returns:
            True se enviado com sucesso, False caso contrário.
        """
        if not self._enabled:
            logger.debug('Telegram desabilitado — mensagem não enviada')
            return False

        url = self.API_BASE.format(token=self.token)
        payload = {
            'chat_id': target_chat_id or self.chat_id,
            'text': text,
            'parse_mode': parse_mode,
            'disable_web_page_preview': True,
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            success = response.status_code == 200

            if success:
                logger.debug('Mensagem Telegram enviada com sucesso')
            else:
                logger.warning(
                    'Falha ao enviar Telegram: HTTP %d — %s',
                    response.status_code, response.text[:200],
                )

            # Registrar no banco de dados
            if self.db is not None:
                try:
                    self.db.insert_telegram_log(
                        message_type='send',
                        content=text[:500],
                        success=success,
                    )
                except Exception as db_err:
                    logger.error('Erro ao registrar log Telegram no DB: %s', db_err)

            return success

        except requests.exceptions.Timeout:
            logger.warning('Timeout ao enviar mensagem Telegram')
            self._log_failure('timeout', text)
            return False
        except requests.exceptions.ConnectionError:
            logger.warning('Erro de conexão com Telegram API')
            self._log_failure('connection_error', text)
            return False
        except Exception as e:
            logger.error('Erro inesperado ao enviar Telegram: %s', e)
            self._log_failure('generic_error', text)
            return False

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
                    content=text[:500],
                    success=False,
                )
            except Exception:
                pass

    def send_alert(self, text: str) -> bool:
        """
        Envia alerta genérico.

        Args:
            text: Texto do alerta.

        Returns:
            True se enviado com sucesso.
        """
        return self._send(text)

    def send_market_open_alert(
        self,
        portfolio_summary: PortfolioSummary,
        simulation: bool = True,
    ) -> bool:
        """
        Envia alerta de abertura do mercado.

        Args:
            portfolio_summary: Resumo atual do portfólio.
            simulation: Se está em modo simulação.

        Returns:
            True se enviado com sucesso.
        """
        now = datetime.now(BRT)
        mode = 'SIMULAÇÃO' if simulation else 'LIVE'

        positions_summary = 'Nenhum'
        if portfolio_summary.positions:
            parts: list[str] = []
            for p in portfolio_summary.positions:
                parts.append(f'{p.ticker} ({p.quantity})')
            positions_summary = ', '.join(parts)

        text = (
            f'🟢 *CÓRTEX — MERCADO ABERTO*\n'
            f'━━━━━━━━━━━━━━━━━━━━\n'
            f'📅 {now.strftime("%d/%m/%Y")} | {now.strftime("%H:%M")} BRT\n'
            f'💰 Saldo disponível: R$ {portfolio_summary.free_cash:.2f}\n'
            f'📊 Ativos em carteira: {positions_summary}\n'
            f'⚡ Status: OPERACIONAL\n'
            f'🔧 Modo: {mode}'
        )

        logger.info('Enviando alerta de abertura do mercado')
        return self._send(text)

    def send_volatility_alert(
        self,
        ticker: str,
        old_price: float,
        new_price: float,
        variation_pct: float,
        thinking: str,
    ) -> bool:
        """
        Envia alerta de volatilidade.

        Args:
            ticker: Código do ativo.
            old_price: Preço anterior.
            new_price: Preço atual.
            variation_pct: Variação percentual.
            thinking: Pensamento/análise do Córtex sobre o movimento.

        Returns:
            True se enviado com sucesso.
        """
        now = datetime.now(BRT)
        direction = '+' if variation_pct > 0 else ''
        emoji = '📈' if variation_pct > 0 else '📉'

        text = (
            f'{emoji} *ALERTA DE VOLATILIDADE — {ticker}*\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'⏰ {now.strftime("%d/%m/%Y %H:%M")} BRT\n'
            f'📊 Variação: {direction}{variation_pct:.2f}% '
            f'(R$ {old_price:.2f} → R$ {new_price:.2f})\n'
            f'\n'
            f'🧠 *PENSAMENTO DO CÓRTEX:*\n'
            f'"{self._escape_markdown(thinking)}"'
        )

        logger.info(
            'Enviando alerta de volatilidade: %s %s%.2f%%',
            ticker, direction, variation_pct,
        )
        return self._send(text)

    def send_trade_alert(
        self,
        decision: Decision,
        simulation: bool = True,
    ) -> bool:
        """
        Envia alerta de operação executada.

        Args:
            decision: Decisão que gerou a operação.
            simulation: Se está em modo simulação.

        Returns:
            True se enviado com sucesso.
        """
        mode = 'SIMULAÇÃO' if simulation else 'LIVE'
        action_str = 'COMPRA' if decision.action == Action.BUY else 'VENDA'
        emoji = '🟢' if decision.action == Action.BUY else '🔴'

        text = (
            f'🔔 *OPERAÇÃO EXECUTADA — {decision.ticker}*\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'📌 Ação: {emoji} {action_str}\n'
            f'📊 Quantidade: {decision.quantity} ações\n'
            f'💵 Preço: R$ {decision.price:.2f}\n'
            f'🛡️ Stop-Loss: R$ {decision.stop_loss:.2f}\n'
            f'🔧 Modo: {mode}\n'
            f'\n'
            f'🧠 *PENSAMENTO:*\n'
            f'"{self._escape_markdown(decision.reasoning)}"'
        )

        logger.info(
            'Enviando alerta de operação: %s %s %d ações @ R$ %.2f',
            action_str, decision.ticker, decision.quantity, decision.price,
        )
        return self._send(text)

    def send_closing_report(
        self,
        report: dict[str, Any],
        simulation: bool = True,
    ) -> bool:
        """
        Envia relatório de fechamento do pregão.

        Args:
            report: Dicionário com dados do relatório diário.
            simulation: Se está em modo simulação.

        Returns:
            True se enviado com sucesso.
        """
        now = datetime.now(BRT)
        mode = 'SIMULAÇÃO' if simulation else 'LIVE'

        total_value = report.get('total_value', 0.0)
        pnl_pct = report.get('pnl_percent', 0.0)
        pnl_sign = '+' if pnl_pct >= 0 else ''

        text = (
            f'📋 *RELATÓRIO DE FECHAMENTO — CÓRTEX*\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'📅 {now.strftime("%d/%m/%Y")} | Pregão Encerrado\n'
            f'\n'
            f'📈 Compras realizadas: {report.get("total_buys", 0)} ações\n'
            f'📉 Vendas realizadas: {report.get("total_sells", 0)} ações\n'
            f'💵 Caixa livre: R$ {report.get("free_cash", 0.0):.2f}\n'
            f'🔒 Capital alocado: R$ {report.get("allocated_capital", 0.0):.2f}\n'
            f'💰 Aporte base: R$ {report.get("initial_capital", 0.0):,.2f}\n'
            f'🏦 Patrimônio total: R$ {total_value:.2f} ({pnl_sign}{pnl_pct:.2f}%)\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'✅ Sistema operando normalmente.\n'
            f'🔧 Modo: {mode}'
        )

        logger.info('Enviando relatório de fechamento')
        return self._send(text)

    def send_emergency_alert(self, ticker: str, reason: str) -> bool:
        """
        Envia alerta de stop-loss / venda emergencial.

        Args:
            ticker: Código do ativo.
            reason: Motivo da venda emergencial.

        Returns:
            True se enviado com sucesso.
        """
        now = datetime.now(BRT)

        text = (
            f'🚨 *STOP-LOSS ATIVADO — {ticker}*\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'⚠️ Venda compulsória executada!\n'
            f'📊 {reason}\n'
            f'⏰ {now.strftime("%d/%m/%Y %H:%M")} BRT'
        )

        logger.warning('Enviando alerta de emergência: %s — %s', ticker, reason)
        return self._send(text)

    def send_health_alert(
        self,
        cpu: float,
        ram: float,
        disk: float,
    ) -> bool:
        """
        Envia alerta crítico de infraestrutura.

        Args:
            cpu: Uso de CPU em percentual.
            ram: Uso de RAM em percentual.
            disk: Uso de disco em percentual.

        Returns:
            True se enviado com sucesso.
        """
        now = datetime.now(BRT)

        text = (
            f'🚨 *ALERTA CRÍTICO DE INFRAESTRUTURA*\n'
            f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
            f'🖥️ CPU: {cpu:.1f}%\n'
            f'💾 RAM: {ram:.1f}%\n'
            f'💿 Disco: {disk:.1f}%\n'
            f'⏰ {now.strftime("%d/%m/%Y %H:%M")} BRT'
        )

        logger.warning(
            'Enviando alerta de saúde: CPU=%.1f%% RAM=%.1f%% Disco=%.1f%%',
            cpu, ram, disk,
        )
        return self._send(text)
