"""
Ponto de entrada principal do Projeto Córtex.

Sistema de trading autônomo para o mercado da B3.
Uso:
    python main.py                  # Inicia com configuração padrão
    python main.py --simulation     # Força modo simulação
    python main.py --verbose        # Log detalhado (DEBUG)
    python main.py --once           # Executa um ciclo e para
    python main.py --dashboard-only # Inicia apenas o dashboard
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from typing import Any

from utils.logger import flush_log_handlers, get_logger, setup_logger

logger = get_logger('cortex.main')


def main() -> None:
    """Função principal — ponto de entrada do Projeto Córtex."""
    parser = argparse.ArgumentParser(
        description='Projeto Córtex — Sistema de Trading Autônomo para B3',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            'Exemplos:\n'
            '  python main.py --simulation         Modo simulação\n'
            '  python main.py --simulation -v       Simulação com debug\n'
            '  python main.py --once                Um ciclo e encerra\n'
            '  python main.py --dashboard-only      Apenas o dashboard web\n'
        ),
    )
    parser.add_argument(
        '--simulation',
        action='store_true',
        help='Força modo simulação (independente da variável de ambiente)',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Log detalhado em nível DEBUG',
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Executa um único ciclo de trading e encerra',
    )
    parser.add_argument(
        '--dashboard-only',
        action='store_true',
        help='Inicia apenas o dashboard web (sem trading)',
    )

    args = parser.parse_args()

    if args.verbose:
        setup_logger('cortex.main', verbose=True)
        logger.setLevel(logging.DEBUG)

    if args.dashboard_only:
        stop_event = threading.Event()
        dashboard_stop_sig: list[int] = []
        server = None
        try:
            from config.settings import settings
            from dashboard.app import DashboardServer, DashboardState

            def _dashboard_signal_handler(sig: int, frame: Any) -> None:
                dashboard_stop_sig.append(sig)

            signal.signal(signal.SIGINT, _dashboard_signal_handler)
            try:
                signal.signal(signal.SIGTERM, _dashboard_signal_handler)
            except (OSError, AttributeError, ValueError):
                pass

            state = DashboardState()
            host = getattr(settings, 'DASHBOARD_HOST', '0.0.0.0')
            port = int(getattr(settings, 'DASHBOARD_PORT', 8003))
            server = DashboardServer(state, host=host, port=port)
            logger.info('🖥️  Dashboard Córtex iniciando em http://%s:%d', host, port)
            server.start()
            while not dashboard_stop_sig and not stop_event.is_set():
                stop_event.wait(timeout=0.5)
            if dashboard_stop_sig:
                sig = dashboard_stop_sig[-1]
                try:
                    signame = signal.Signals(sig).name
                except ValueError:
                    signame = str(sig)
                logger.info('Sinal %s recebido — encerrando dashboard...', signame)
                stop_event.set()
        except ImportError:
            logger.critical('❌ Módulo dashboard não encontrado. Instale as dependências.')
            sys.exit(1)
        except KeyboardInterrupt:
            logger.info('Interrupção do teclado — encerrando dashboard.')
        except Exception as e:
            logger.critical('❌ Erro ao iniciar dashboard: %s', e, exc_info=True)
            sys.exit(1)
        finally:
            if server is not None:
                try:
                    server.stop()
                except Exception as stop_err:
                    logger.warning('Aviso ao encerrar servidor do dashboard: %s', stop_err)
            logger.info('🔴 Dashboard encerrado.')
            flush_log_handlers()
        return

    from core.engine import CortexEngine

    engine = CortexEngine(
        force_simulation=args.simulation,
        verbose=args.verbose,
        single_cycle=args.once,
    )

    def shutdown_handler(sig: int, frame: Any) -> None:
        """Sinaliza parada graciosa sem bloquear locks ou abortar ordens no meio do frame."""
        engine.request_stop(sig)

    signal.signal(signal.SIGINT, shutdown_handler)
    try:
        signal.signal(signal.SIGTERM, shutdown_handler)
    except (OSError, AttributeError, ValueError):
        pass

    try:
        engine.start()
        if not getattr(engine, '_stop_requested', False) and not engine._stopped:
            engine.run()
        engine.stop(graceful=True)
    except KeyboardInterrupt:
        logger.info('Interrupção do teclado recebida — encerrando graciosamente...')
        engine.stop(graceful=True)
    except Exception as e:
        logger.critical('Erro fatal não tratado: %s', e, exc_info=True)
        engine.stop(graceful=False)
        flush_log_handlers()
        sys.exit(1)
    finally:
        flush_log_handlers()


if __name__ == '__main__':
    main()
