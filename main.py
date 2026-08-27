"""
Ponto de entrada principal do Projeto Córtex.

Sistema de trading autônomo para o mercado fracionário da B3.
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

logger = logging.getLogger('cortex.main')

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

    if args.dashboard_only:
        try:
            from dashboard.app import DashboardState, DashboardServer
            import time as _time
            from config.settings import settings
            state = DashboardState()
            server = DashboardServer(state, host='0.0.0.0', port=settings.DASHBOARD_PORT)
            print(f'🖥️  Dashboard Córtex iniciando em http://0.0.0.0:{settings.DASHBOARD_PORT}')
            server.start()
            while True:
                _time.sleep(1)
        except ImportError:
            print('❌ Módulo dashboard não encontrado. Instale as dependências.')
            sys.exit(1)
        except KeyboardInterrupt:
            print('\n🔴 Dashboard encerrado.')
        except Exception as e:
            print(f'❌ Erro ao iniciar dashboard: {e}')
            sys.exit(1)
        return

    from core.engine import CortexEngine

    engine = CortexEngine(
        force_simulation=args.simulation,
        verbose=args.verbose,
        single_cycle=args.once,
    )

    def shutdown_handler(sig: int, frame: object) -> None:
        """Handler para shutdown gracioso via SIGINT/SIGTERM."""
        signame = signal.Signals(sig).name
        logger.info('Sinal %s recebido — encerrando...', signame)
        engine.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown_handler)
    try:
        signal.signal(signal.SIGTERM, shutdown_handler)
    except (OSError, AttributeError):
        pass

    try:
        engine.start()
        engine.run()
    except Exception as e:
        logger.critical('Erro fatal: %s', e, exc_info=True)
        engine.stop()
        sys.exit(1)

if __name__ == '__main__':
    main()
