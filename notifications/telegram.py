"""
Notificador Telegram do Projeto Córtex.

Envia alertas formatados para o chat do Telegram configurado:
abertura/fechamento de mercado, operações, stop-loss, volatilidade,
saúde do sistema e relatórios diários.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any ,Optional

import requests

from models .data_models import Action ,BRT ,Decision ,PortfolioSummary

logger =logging .getLogger ('cortex.notifications.telegram')

class TelegramNotifier :
    """Notificador de alertas via Telegram Bot API."""

    API_BASE ='https://api.telegram.org/bot{token}/sendMessage'

    def __init__ (
    self ,
    token :str ,
    chat_id :str ,
    db :Optional [Any ]=None ,
    )->None :
        """
        Inicializa o notificador Telegram.

        Args:
            token: Token do bot Telegram.
            chat_id: ID do chat para envio de mensagens.
            db: Instância do DatabaseManager para logging (opcional).
        """
        self .token =token
        self .chat_id =chat_id
        self .db =db
        self ._enabled =bool (token and chat_id )

        if self ._enabled :
            logger .info ('TelegramNotifier configurado — chat_id: %s',chat_id )
        else :
            logger .warning (
            'TelegramNotifier DESABILITADO — token ou chat_id não configurado'
            )

    @staticmethod
    def _escape_markdown (text :str )->str :
        """Escapa caracteres especiais do Markdown para Telegram."""
        for char in ('_','*','`','[',']','(',')'):
            text =text .replace (char ,f'\\{char }')
        return text

    def _send (self ,text :str ,parse_mode :str ='Markdown')->bool :
        """
        Envia mensagem via Telegram Bot API.

        Args:
            text: Texto da mensagem (suporta Markdown).
            parse_mode: Modo de parsing ('Markdown' ou 'HTML').

        Returns:
            True se enviado com sucesso, False caso contrário.
        """
        if not self ._enabled :
            logger .debug ('Telegram desabilitado — mensagem não enviada')
            return False

        url =self .API_BASE .format (token =self .token )
        payload ={
        'chat_id':self .chat_id ,
        'text':text ,
        'parse_mode':parse_mode ,
        'disable_web_page_preview':True ,
        }

        try :
            response =requests .post (url ,json =payload ,timeout =10 )
            success =response .status_code ==200

            if success :
                logger .debug ('Mensagem Telegram enviada com sucesso')
            else :
                logger .warning (
                'Falha ao enviar Telegram: HTTP %d — %s',
                response .status_code ,response .text [:200 ],
                )

            if self .db is not None :
                try :
                    self .db .insert_telegram_log (
                    message_type ='send',
                    content =text [:500 ],
                    success =success ,
                    )
                except Exception as db_err :
                    logger .error ('Erro ao registrar log Telegram no DB: %s',db_err )

            return success

        except requests .exceptions .Timeout :
            logger .warning ('Timeout ao enviar mensagem Telegram')
            self ._log_failure ('timeout',text )
            return False
        except requests .exceptions .ConnectionError :
            logger .warning ('Erro de conexão com Telegram API')
            self ._log_failure ('connection_error',text )
            return False
        except Exception as e :
            logger .error ('Erro inesperado ao enviar Telegram: %s',e )
            self ._log_failure ('error',text )
            return False

    def _log_failure (self ,error_type :str ,text :str )->None :
        """Registra falha de envio no banco de dados."""
        if self .db is not None :
            try :
                self .db .insert_telegram_log (
                message_type =f'send_failed_{error_type }',
                content =text [:500 ],
                success =False ,
                )
            except Exception :
                pass

    def send_alert (self ,text :str )->bool :
        """
        Envia alerta genérico.

        Args:
            text: Texto do alerta.

        Returns:
            True se enviado com sucesso.
        """
        return self ._send (text )

    def send_market_open_alert (
    self ,
    portfolio_summary :PortfolioSummary ,
    simulation :bool =True ,
    )->bool :
        """
        Envia alerta de abertura do mercado.

        Args:
            portfolio_summary: Resumo atual do portfólio.
            simulation: Se está em modo simulação.

        Returns:
            True se enviado com sucesso.
        """
        now =datetime .now (BRT )
        mode ='SIMULAÇÃO'if simulation else 'LIVE'

        positions_summary ='Nenhum'
        if portfolio_summary .positions :
            parts :list [str ]=[]
            for p in portfolio_summary .positions :
                parts .append (f'{p .ticker } ({p .quantity })')
            positions_summary =', '.join (parts )

        text =(
        f'🟢 *CÓRTEX — MERCADO ABERTO*\n'
        f'━━━━━━━━━━━━━━━━━━━━\n'
        f'📅 {now .strftime ("%d/%m/%Y")} | {now .strftime ("%H:%M")} BRT\n'
        f'💰 Saldo disponível: R$ {portfolio_summary .free_cash :.2f}\n'
        f'📊 Ativos em carteira: {positions_summary }\n'
        f'⚡ Status: OPERACIONAL\n'
        f'🔧 Modo: {mode }'
        )

        logger .info ('Enviando alerta de abertura do mercado')
        return self ._send (text )

    def send_volatility_alert (
    self ,
    ticker :str ,
    old_price :float ,
    new_price :float ,
    variation_pct :float ,
    thinking :str ,
    )->bool :
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
        now =datetime .now (BRT )
        direction ='+'if variation_pct >0 else ''
        emoji ='📈'if variation_pct >0 else '📉'

        text =(
        f'{emoji } *ALERTA DE VOLATILIDADE — {ticker }*\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'⏰ {now .strftime ("%d/%m/%Y %H:%M")} BRT\n'
        f'📊 Variação: {direction }{variation_pct :.2f}% '
        f'(R$ {old_price :.2f} → R$ {new_price :.2f})\n'
        f'\n'
        f'🧠 *PENSAMENTO DO CÓRTEX:*\n'
        f'"{self ._escape_markdown (thinking )}"'
        )

        logger .info (
        'Enviando alerta de volatilidade: %s %s%.2f%%',
        ticker ,direction ,variation_pct ,
        )
        return self ._send (text )

    def send_trade_alert (
    self ,
    decision :Decision ,
    simulation :bool =True ,
    )->bool :
        """
        Envia alerta de operação executada.

        Args:
            decision: Decisão que gerou a operação.
            simulation: Se está em modo simulação.

        Returns:
            True se enviado com sucesso.
        """
        mode ='SIMULAÇÃO'if simulation else 'LIVE'
        action_str ='COMPRA'if decision .action ==Action .BUY else 'VENDA'
        emoji ='🟢'if decision .action ==Action .BUY else '🔴'

        text =(
        f'🔔 *OPERAÇÃO EXECUTADA — {decision .ticker }*\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'📌 Ação: {emoji } {action_str }\n'
        f'📊 Quantidade: {decision .quantity } ações\n'
        f'💵 Preço: R$ {decision .price :.2f}\n'
        f'🛡️ Stop-Loss: R$ {decision .stop_loss :.2f}\n'
        f'🔧 Modo: {mode }\n'
        f'\n'
        f'🧠 *PENSAMENTO:*\n'
        f'"{self ._escape_markdown (decision .reasoning )}"'
        )

        logger .info (
        'Enviando alerta de operação: %s %s %d ações @ R$ %.2f',
        action_str ,decision .ticker ,decision .quantity ,decision .price ,
        )
        return self ._send (text )

    def send_closing_report (
    self ,
    report :dict [str ,Any ],
    simulation :bool =True ,
    )->bool :
        """
        Envia relatório de fechamento do pregão.

        Args:
            report: Dicionário com dados do relatório diário.
            simulation: Se está em modo simulação.

        Returns:
            True se enviado com sucesso.
        """
        now =datetime .now (BRT )
        mode ='SIMULAÇÃO'if simulation else 'LIVE'

        total_value =report .get ('total_value',0.0 )
        pnl_pct =report .get ('pnl_percent',0.0 )
        pnl_sign ='+'if pnl_pct >=0 else ''

        text =(
        f'📋 *RELATÓRIO DE FECHAMENTO — CÓRTEX*\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'📅 {now .strftime ("%d/%m/%Y")} | Pregão Encerrado\n'
        f'\n'
        f'📈 Compras realizadas: {report .get ("total_buys",0 )} ações\n'
        f'📉 Vendas realizadas: {report .get ("total_sells",0 )} ações\n'
        f'💵 Caixa livre: R$ {report .get ("free_cash",0.0 ):.2f}\n'
        f'🔒 Capital alocado: R$ {report .get ("allocated_capital",0.0 ):.2f}\n'
        f'💰 Aporte base: R$ {report .get ("initial_capital",0.0 ):,.2f}\n'
        f'🏦 Patrimônio total: R$ {total_value :.2f} ({pnl_sign }{pnl_pct :.2f}%)\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'✅ Sistema operando normalmente.\n'
        f'🔧 Modo: {mode }'
        )

        logger .info ('Enviando relatório de fechamento')
        return self ._send (text )

    def send_emergency_alert (self ,ticker :str ,reason :str )->bool :
        """
        Envia alerta de stop-loss / venda emergencial.

        Args:
            ticker: Código do ativo.
            reason: Motivo da venda emergencial.

        Returns:
            True se enviado com sucesso.
        """
        now =datetime .now (BRT )

        text =(
        f'🚨 *STOP-LOSS ATIVADO — {ticker }*\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'⚠️ Venda compulsória executada!\n'
        f'📊 {reason }\n'
        f'⏰ {now .strftime ("%d/%m/%Y %H:%M")} BRT'
        )

        logger .warning ('Enviando alerta de emergência: %s — %s',ticker ,reason )
        return self ._send (text )

    def send_health_alert (
    self ,
    cpu :float ,
    ram :float ,
    disk :float ,
    )->bool :
        """
        Envia alerta crítico de infraestrutura.

        Args:
            cpu: Uso de CPU em percentual.
            ram: Uso de RAM em percentual.
            disk: Uso de disco em percentual.

        Returns:
            True se enviado com sucesso.
        """
        now =datetime .now (BRT )

        text =(
        f'🚨 *ALERTA CRÍTICO DE INFRAESTRUTURA*\n'
        f'━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n'
        f'🖥️ CPU: {cpu :.1f}%\n'
        f'💾 RAM: {ram :.1f}%\n'
        f'💿 Disco: {disk :.1f}%\n'
        f'⏰ {now .strftime ("%d/%m/%Y %H:%M")} BRT'
        )

        logger .warning (
        'Enviando alerta de saúde: CPU=%.1f%% RAM=%.1f%% Disco=%.1f%%',
        cpu ,ram ,disk ,
        )
        return self ._send (text )
