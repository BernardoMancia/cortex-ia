import os
import sys
import logging
from datetime import datetime, timedelta

import pandas as pd
import numpy as np
import yfinance as yf
import matplotlib.pyplot as plt

# Adiciona o diretório raiz ao PYTHONPATH
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from core.portfolio import Portfolio
from core.risk_manager import RiskManager
from analysis.technical import TechnicalAnalyzer
from analysis.sentiment import SentimentAnalyzer
from analysis.decision import DecisionEngine
from broker.simulator import SimulatorBroker
from models.data_models import Action, BRT

# Configuração de logging simplificada para o backtester
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logger = logging.getLogger('backtester')


class MockMarketData:
    """Provedor de dados mockado para retornar fatias do histórico."""
    def __init__(self, historical_data: dict[str, pd.DataFrame]):
        self.historical_data = historical_data
        self.current_date: datetime | None = None

    def get_ohlcv(self, ticker: str, period: str = '1y', interval: str = '1d') -> pd.DataFrame | None:
        if ticker not in self.historical_data:
            return None
        df = self.historical_data[ticker]
        if self.current_date is None:
            return df
        
        # Filtra dados até a current_date
        # Para backtest, usamos data de fechamento para tomar decisão
        mask = df.index <= self.current_date
        return df.loc[mask].copy()


def run_backtest(start_date: str, end_date: str, initial_capital: float = 10000.0) -> None:
    logger.info("Iniciando Backtest de %s a %s...", start_date, end_date)
    logger.info("Capital Inicial: R$ %.2f", initial_capital)
    
    # 1. Download de dados
    logger.info("Baixando dados históricos do Yahoo Finance...")
    historical_data = {}
    all_dates = set()
    
    for ticker in settings.WATCHLIST:
        yf_ticker = settings.YFINANCE_SUFFIX_MAP.get(ticker, f"{ticker}.SA")
        try:
            df = yf.download(yf_ticker, start=start_date, end=end_date, progress=False)
            if df.empty:
                logger.warning("Sem dados para %s", ticker)
                continue
            
            # Limpa colunas MultiIndex se o yfinance retornar
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
                
            historical_data[ticker] = df
            all_dates.update(df.index.tolist())
        except Exception as e:
            logger.error("Erro baixando %s: %s", ticker, e)

    if not historical_data:
        logger.error("Nenhum dado histórico baixado. Cancelando backtest.")
        return

    # Ordena todas as datas de pregão disponíveis
    trading_days = sorted(list(all_dates))
    
    # Precisamos garantir um "aquecimento" dos indicadores (ex: EMA de 50)
    # Então começamos a operar apenas a partir do 50º dia
    min_days_required = 55
    if len(trading_days) <= min_days_required:
        logger.error("Poucos dados para calcular indicadores (min: %d).", min_days_required)
        return
    
    trading_days = trading_days[min_days_required:]
    
    # 2. Inicializar Motor Mockado
    mock_market = MockMarketData(historical_data)
    # Portfolio e Broker do Simulador funcionam perfeitamente para backtest local
    portfolio = Portfolio(initial_capital)
    broker = SimulatorBroker(portfolio, persist_file=None) # Sem persistência
    risk_manager = RiskManager(stop_loss_percent=settings.STOP_LOSS_PERCENT)
    
    # O DecisionEngine não precisa ser mockado, apenas passamos os componentes
    from analysis.decision import DecisionEngine
    # Mock de DB
    class MockDB:
        def insert_news(self, *args, **kwargs): pass
        def get_recent_news(self, *args, **kwargs): return []
        def insert_decision(self, *args, **kwargs): pass
        
    engine = DecisionEngine(
        market_data=mock_market,
        sentiment=SentimentAnalyzer(), # Retornará neutro pois enviaremos [] news
        technical=TechnicalAnalyzer(),
        portfolio=portfolio,
        risk_manager=risk_manager,
        db=MockDB()
    )
    
    equity_curve = []
    dates_curve = []

    # 3. Loop de Trading no Tempo
    logger.info("Iniciando loop sobre %d dias úteis...", len(trading_days))
    
    for current_day in trading_days:
        mock_market.current_date = current_day
        
        # Obter os preços "atuais" para este dia (Fechamento)
        current_prices = {}
        for ticker, df in historical_data.items():
            if current_day in df.index:
                current_prices[ticker] = float(df.loc[current_day, 'Close'])
        
        # 1. Atualizar preços no broker (para marcação a mercado)
        for t, p in current_prices.items():
            broker.update_price(t, p)
            
        # 2. Avaliar posições abertas primeiro (Stop-loss e Take-profit)
        # Processar Take-Profit parcial
        tp_positions = risk_manager.check_take_profit_triggers(portfolio.get_all_positions())
        for pos in tp_positions:
            qty_to_sell = max(1, pos.quantity // 2)
            broker.sell(pos.ticker, qty_to_sell, current_prices.get(pos.ticker, pos.current_price), "TAKE_PROFIT_PARCIAL")
            portfolio.reduce_position(pos.ticker, qty_to_sell)
            pos.partial_exit_done = True
            pos.stop_loss = pos.entry_price
            
        # 3. Rodar Engine para cada ticker
        for ticker in settings.WATCHLIST:
            if ticker not in current_prices:
                continue
                
            price = current_prices[ticker]
            # O mock_market já filtra para esconder o futuro
            try:
                decision = engine.evaluate(ticker, current_price=price, news_items=[])
                
                if decision.action == Action.BUY:
                    broker.buy(ticker, decision.quantity, price)
                elif decision.action == Action.EMERGENCY_SELL:
                    # SL atingido
                    broker.sell(ticker, decision.quantity, price, "STOP_LOSS")
                elif decision.action == Action.SELL:
                    broker.sell(ticker, decision.quantity, price, "SELL_SIGNAL")
            except Exception as e:
                # Silencia erros por falta de liquidez no histórico inicial
                pass

        # Gravar patrimônio no final do dia
        summary = portfolio.get_summary()
        equity_curve.append(summary.total_value)
        dates_curve.append(current_day)
        
    # 4. Relatório Final
    final_value = equity_curve[-1]
    pnl = final_value - initial_capital
    pnl_pct = (pnl / initial_capital) * 100
    
    logger.info("=== RESULTADO DO BACKTEST ===")
    logger.info("Capital Inicial: R$ %.2f", initial_capital)
    logger.info("Capital Final:   R$ %.2f", final_value)
    logger.info("Lucro Líquido:   R$ %.2f (%.2f%%)", pnl, pnl_pct)
    
    # Calcular Buy and Hold IBOV
    try:
        ibov = yf.download("^BVSP", start=start_date, end=end_date, progress=False)
        if not ibov.empty:
            ibov_start = float(ibov['Close'].iloc[min_days_required])
            ibov_end = float(ibov['Close'].iloc[-1])
            ibov_ret = ((ibov_end - ibov_start) / ibov_start) * 100
            logger.info("Retorno IBOVESPA no período: %.2f%%", ibov_ret)
            logger.info("Alpha do Córtex: %.2f%%", pnl_pct - ibov_ret)
    except:
        pass
        
    # Salvar Gráfico
    try:
        plt.figure(figsize=(10, 5))
        plt.plot(dates_curve, equity_curve, label='Córtex IA', color='#00f2fe')
        plt.title('Backtest Equity Curve')
        plt.xlabel('Data')
        plt.ylabel('Patrimônio Total (R$)')
        plt.grid(True, alpha=0.3)
        plt.legend()
        out_file = os.path.join(settings.PROJECT_ROOT, "backtest", "equity_curve.png")
        plt.savefig(out_file)
        logger.info("Gráfico salvo em: %s", out_file)
    except Exception as e:
        logger.warning("Não foi possível plotar o gráfico: %s", e)

if __name__ == '__main__':
    # Últimos 2 anos
    end = datetime.now()
    start = end - timedelta(days=730)
    run_backtest(start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d'))
