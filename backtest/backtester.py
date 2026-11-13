"""
Motor de Backtesting Histórico do Projeto Córtex (B3).

Simula a execução diária da estratégia híbrida (Análise Técnica + Sentimento +
Gestão de Risco) sem viés de antecipação (look-ahead bias):
- Sinais gerados no fechamento da barra `t` são executados na abertura da barra `t+1`
  com derrapagem (`slippage_bps`) e taxas da B3 (`fee_rate`).
- Stop-loss intradiário verificado em `Low <= pos.stop_loss` com preenchimento
  conservador em `min(Open, stop_loss)` ajustado por custos.
- Trailing stop atualizado com base em `High`/`Close`, saídas parciais de take-profit
  sincronizadas entre `Portfolio` e `SimulatorBroker`, validação via `RiskManager`
  e circuit breaker diário.
"""

from __future__ import annotations

import inspect
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    plt = None  # type: ignore[assignment]
import numpy as np
import pandas as pd
import yfinance as yf

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analysis.decision import DecisionEngine
from analysis.sentiment import SentimentAnalyzer
from analysis.technical import TechnicalAnalyzer
from broker.base import OrderStatus
from broker.simulator import SimulatorBroker
from config.settings import settings
from core.risk_manager import RiskManager
from models.data_models import Action, BRT, Position
from portfolio import Portfolio
from utils.helpers import format_brl, normalize_b3_ticker, to_yfinance_ticker
from utils.logger import get_logger

logger = get_logger("backtest.backtester")


class MockMarketData:
    """
    Provedor de dados históricos fatiados até `current_date` (sem look-ahead bias),
    compatível com o protocolo completo esperado por `DecisionEngine`.
    """

    def __init__(self, historical_data: dict[str, pd.DataFrame]) -> None:
        self.historical_data: dict[str, pd.DataFrame] = {
            (normalize_b3_ticker(k) or k.strip().upper()): v
            for k, v in historical_data.items()
        }
        self.current_date: pd.Timestamp | datetime | None = None

    def _resolve_df(self, ticker: str) -> Optional[pd.DataFrame]:
        base = normalize_b3_ticker(ticker) or ticker.strip().upper()
        df = self.historical_data.get(base)
        if df is None:
            df = self.historical_data.get(ticker)
        if df is None or df.empty:
            return None
        if self.current_date is None:
            return df
        cutoff = pd.Timestamp(self.current_date)
        if getattr(cutoff, "tzinfo", None) is not None and getattr(df.index, "tz", None) is None:
            cutoff = cutoff.tz_localize(None)
        return df.loc[df.index <= cutoff]

    def get_ohlcv(
        self, ticker: str, period: str = "1y", interval: str = "1d"
    ) -> Optional[pd.DataFrame]:
        """Retorna cópia do histórico OHLCV estritamente até `self.current_date`."""
        norm_interval = (interval or "1d").strip().lower()
        if norm_interval not in ("1d", "1wk", "1mo"):
            return None
        sliced = self._resolve_df(ticker)
        if sliced is None or sliced.empty:
            return None
        return sliced.copy()

    def get_current_price(self, ticker: str) -> dict[str, Any]:
        """Retorna metadados da cotação no fechamento da barra atual."""
        base = normalize_b3_ticker(ticker) or ticker.strip().upper()
        sliced = self._resolve_df(base)
        if sliced is None or sliced.empty:
            return {}

        last_row = sliced.iloc[-1]
        close_px = float(last_row["Close"])
        open_px = float(last_row["Open"]) if "Open" in sliced.columns else close_px
        high_px = float(last_row["High"]) if "High" in sliced.columns else close_px
        low_px = float(last_row["Low"]) if "Low" in sliced.columns else close_px
        vol = int(last_row["Volume"]) if "Volume" in sliced.columns and pd.notna(last_row["Volume"]) else 0

        return {
            "ticker": base,
            "last": close_px,
            "bid": close_px,
            "ask": close_px,
            "open": open_px,
            "high": high_px,
            "low": low_px,
            "volume": vol,
            "is_stale": False,
        }

    def is_quote_stale(self, ticker: str) -> bool:
        """Cotações históricas sincronizadas no backtest nunca são marcadas como stale."""
        return False

    def get_quote_info(self, ticker: str) -> Optional[dict[str, Any]]:
        """Retorna metadados resumidos da cotação atual."""
        info = self.get_current_price(ticker)
        if not info or "last" not in info:
            return None
        return {
            "price": float(info["last"]),
            "timestamp": str(self.current_date or ""),
            "is_stale": False,
        }

    def get_cached_price(self, ticker: str) -> Optional[float]:
        """Retorna o último preço disponível até `current_date`."""
        info = self.get_current_price(ticker)
        last = info.get("last")
        return float(last) if last is not None else None


class MockDB:
    """Persistência nula isolada para execução de backtests sem tocar SQLite real."""

    def insert_news(self, *args: Any, **kwargs: Any) -> None:
        return None

    def get_recent_news(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []

    def insert_decision(self, *args: Any, **kwargs: Any) -> None:
        return None

    def insert_trade(self, *args: Any, **kwargs: Any) -> None:
        return None


def _normalize_ohlcv_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Normaliza colunas `MultiIndex`, datas tz-naive diárias e remove linhas OHLC inválidas."""
    clean = df.copy()
    if isinstance(clean.columns, pd.MultiIndex):
        clean.columns = clean.columns.get_level_values(0)

    required_cols = [c for c in ("Open", "High", "Low", "Close") if c in clean.columns]
    if len(required_cols) < 4:
        return pd.DataFrame()

    clean = clean.dropna(subset=required_cols)
    if clean.empty:
        return clean

    idx = pd.to_datetime(clean.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_convert(BRT).tz_localize(None)
    clean.index = idx.normalize()
    clean = clean[~clean.index.duplicated(keep="last")].sort_index()
    return clean


def _create_isolated_broker(initial_capital: float, state_path: Path) -> SimulatorBroker:
    """Instancia `SimulatorBroker` em arquivo temporário isolado sem poluir estado real."""
    sig = inspect.signature(SimulatorBroker.__init__)
    params = sig.parameters
    kwargs: dict[str, Any] = {}

    if "initial_balance" in params:
        kwargs["initial_balance"] = initial_capital
    elif "initial_capital" in params:
        kwargs["initial_capital"] = initial_capital

    if "state_path" in params:
        kwargs["state_path"] = state_path
    elif "persist_file" in params:
        kwargs["persist_file"] = state_path

    if "fee_rate" in params:
        kwargs["fee_rate"] = 0.0

    return SimulatorBroker(**kwargs)


def _create_risk_manager() -> RiskManager:
    """Instancia `RiskManager` respeitando os parâmetros configurados em `settings`."""
    params = inspect.signature(RiskManager.__init__).parameters
    kwargs: dict[str, Any] = {}
    sl_cfg = getattr(settings, "STOP_LOSS_PERCENT", None)
    if sl_cfg is not None:
        if "stop_loss_percent" in params:
            kwargs["stop_loss_percent"] = sl_cfg
        elif "stop_loss_pct" in params:
            kwargs["stop_loss_pct"] = sl_cfg
    return RiskManager(**kwargs)


def _sync_broker_prices(broker: SimulatorBroker, prices: dict[str, float]) -> None:
    """Sincroniza preços de mercado nas posições internas do `SimulatorBroker`."""
    if not prices:
        return
    update_many = getattr(broker, "update_prices", None)
    if callable(update_many):
        update_many(prices)
        return
    update_one = getattr(broker, "update_price", None)
    if callable(update_one):
        for ticker, price in prices.items():
            update_one(ticker, price)


def _get_broker_cash(broker: SimulatorBroker, fallback: float) -> float:
    """Obtém saldo livre atual do `SimulatorBroker` de forma segura."""
    get_bal = getattr(broker, "get_balance", None)
    if callable(get_bal):
        val = get_bal()
        if isinstance(val, (int, float)):
            return float(val)
    bal_attr = getattr(broker, "balance", None)
    if isinstance(bal_attr, (int, float)):
        return float(bal_attr)
    return fallback


def _broker_buy(
    broker: SimulatorBroker,
    ticker: str,
    quantity: int,
    price: float,
    stop_loss: float,
) -> bool:
    """Executa ordem de compra no `SimulatorBroker` propagando `stop_loss`."""
    params = inspect.signature(broker.buy).parameters
    accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    kwargs: dict[str, Any] = {}
    if "stop_loss" in params or accepts_varkw:
        kwargs["stop_loss"] = stop_loss
    res = broker.buy(ticker, quantity=quantity, price=price, **kwargs)
    if isinstance(res, bool):
        return res
    if hasattr(res, "status"):
        return res.status == OrderStatus.FILLED
    return bool(getattr(res, "success", True))


def _broker_sell(
    broker: SimulatorBroker,
    ticker: str,
    quantity: int,
    price: float,
    reason: str = "",
) -> bool:
    """Executa ordem de venda no `SimulatorBroker` respeitando sua assinatura."""
    params = inspect.signature(broker.sell).parameters
    accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    kwargs: dict[str, Any] = {}
    if ("comment" in params or accepts_varkw) and reason:
        kwargs["comment"] = reason
    elif "reason" in params and reason:
        kwargs["reason"] = reason

    if "quantity" in params and "price" in params:
        res = broker.sell(ticker, quantity=quantity, price=price, **kwargs)
    else:
        res = broker.sell(ticker, quantity, price, **kwargs)

    if isinstance(res, bool):
        return res
    if hasattr(res, "status"):
        return res.status == OrderStatus.FILLED
    return bool(getattr(res, "success", True))


def _portfolio_remove_position(
    portfolio: Portfolio, ticker: str, sell_price: float
) -> None:
    """Remove posição integral do `Portfolio` creditando `sell_price`."""
    params = inspect.signature(portfolio.remove_position).parameters
    accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if "sell_price" in params or accepts_varkw:
        portfolio.remove_position(ticker, sell_price=sell_price)
    else:
        portfolio.remove_position(ticker)


def _portfolio_reduce_position(
    portfolio: Portfolio, ticker: str, quantity: int, sell_price: float
) -> None:
    """Reduz parcialmente posição do `Portfolio` creditando `sell_price`."""
    params = inspect.signature(portfolio.reduce_position).parameters
    accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    if "quantity_to_sell" in params or accepts_varkw:
        portfolio.reduce_position(ticker, sell_price=sell_price, quantity_to_sell=quantity)
    elif "sell_price" in params:
        portfolio.reduce_position(ticker, quantity, sell_price=sell_price)
    else:
        portfolio.reduce_position(ticker, quantity)


def _validate_buy_with_risk_manager(
    risk_manager: RiskManager,
    ticker: str,
    quantity: int,
    price: float,
    available_capital: float,
    total_portfolio_value: float,
    positions: list[Position],
) -> tuple[bool, str]:
    """Valida ordem de compra junto ao `RiskManager.validate_order`."""
    validate_fn = getattr(risk_manager, "validate_order", None)
    if not callable(validate_fn):
        return (quantity > 0 and (quantity * price) <= available_capital, "")

    params = inspect.signature(validate_fn).parameters
    accepts_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    kwargs: dict[str, Any] = {}

    candidate_args = {
        "ticker": ticker,
        "quantity": quantity,
        "price": price,
        "available_capital": available_capital,
        "total_portfolio_value": total_portfolio_value,
        "positions": positions,
    }
    for key, val in candidate_args.items():
        if key in params or accepts_varkw:
            kwargs[key] = val

    result = validate_fn(**kwargs)
    if isinstance(result, tuple) and len(result) >= 2:
        return bool(result[0]), str(result[1])
    return bool(result), ""


def _ratchet_trailing_stop(
    risk_manager: RiskManager,
    pos: Position,
    bar_high: float,
    bar_close: float,
    broker: SimulatorBroker,
) -> None:
    """Atualiza o trailing stop da posição usando a excursão favorável (`High`/`Close`)."""
    peak_price = max(bar_high, bar_close)
    if peak_price <= 0 or pos.entry_price <= 0:
        return

    updated_stop = float(pos.stop_loss or 0.0)
    rm_trailing = getattr(risk_manager, "update_trailing_stop", None)
    if callable(rm_trailing):
        try:
            prev_px = pos.current_price
            pos.current_price = peak_price
            maybe_stop = rm_trailing(pos)
            pos.current_price = prev_px
            if isinstance(maybe_stop, (int, float)) and float(maybe_stop) > updated_stop:
                updated_stop = float(maybe_stop)
            elif pos.stop_loss > updated_stop:
                updated_stop = float(pos.stop_loss)
        except Exception as exc:
            logger.debug("Fallback de trailing stop para %s: %s", pos.ticker, exc)

    activation = float(
        getattr(
            risk_manager,
            "trailing_stop_activation",
            getattr(settings, "TRAILING_STOP_ACTIVATION", 0.04),
        )
    )
    distance = float(
        getattr(
            risk_manager,
            "trailing_stop_distance",
            getattr(settings, "TRAILING_STOP_DISTANCE", 0.02),
        )
    )
    if activation > 1.0:
        activation /= 100.0
    if distance > 1.0:
        distance /= 100.0

    gain_pct = (peak_price - pos.entry_price) / pos.entry_price
    if gain_pct >= activation and distance > 0:
        candidate_stop = round(peak_price * (1.0 - distance), 2)
        if candidate_stop > updated_stop:
            updated_stop = candidate_stop

    if updated_stop > pos.stop_loss:
        pos.stop_loss = round(updated_stop, 2)
        broker_positions = getattr(broker, "positions", None)
        if isinstance(broker_positions, dict) and pos.ticker in broker_positions:
            try:
                broker_positions[pos.ticker].stop_loss = pos.stop_loss
            except Exception as exc:
                logger.debug("Não foi possível sincronizar SL no broker para %s: %s", pos.ticker, exc)


def _compute_max_drawdown_pct(equity_series: list[float]) -> float:
    """Calcula o drawdown máximo pico-ao-vale em percentual."""
    if not equity_series:
        return 0.0
    peak = equity_series[0]
    max_dd = 0.0
    for val in equity_series:
        if val > peak:
            peak = val
        if peak > 0:
            dd = ((peak - val) / peak) * 100.0
            if dd > max_dd:
                max_dd = dd
    return round(max_dd, 2)


def _compute_sharpe_ratio(equity_series: list[float]) -> float:
    """Calcula o índice de Sharpe anualizado (252 pregões) dos retornos diários."""
    if len(equity_series) < 3:
        return 0.0
    arr = np.asarray(equity_series, dtype=float)
    prev = arr[:-1]
    valid = prev > 0
    if not np.any(valid):
        return 0.0
    rets = (arr[1:][valid] - prev[valid]) / prev[valid]
    if rets.size < 2:
        return 0.0
    std = float(np.std(rets, ddof=1))
    if std <= 1e-12:
        return 0.0
    return round(float((np.mean(rets) / std) * np.sqrt(252.0)), 2)


def run_backtest(
    start_date: str,
    end_date: str,
    initial_capital: float = 10000.0,
    slippage_bps: float = 5.0,
    fee_rate: float | None = None,
    watchlist: Optional[list[str]] = None,
    historical_data: Optional[dict[str, pd.DataFrame]] = None,
    plot: bool = True,
) -> Optional[dict[str, Any]]:
    """
    Executa o backtest histórico sem look-ahead bias no intervalo `[start_date, end_date]`.

    Args:
        start_date: Data inicial (`YYYY-MM-DD`).
        end_date: Data final (`YYYY-MM-DD`).
        initial_capital: Capital inicial em reais.
        slippage_bps: Derrapagem adversa por execução em basis points (default: 5.0 bps).
        fee_rate: Emolumentos + liquidação B3 por operação (default: `settings.B3_FEE_RATE` = 0,03%).
        watchlist: Lista opcional de tickers B3 (default: `settings.WATCHLIST`).
        historical_data: Dicionário opcional de DataFrames OHLCV pré-carregados.
        plot: Se `True`, salva gráfico de curva de patrimônio em `backtest/equity_curve.png`.

    Returns:
        Dicionário com métricas consolidadas, curva de capital e histórico de trades,
        ou `None` caso não haja dados históricos suficientes.
    """
    if fee_rate is None:
        fee_rate = float(getattr(settings, "B3_FEE_RATE", 0.0003) or 0.0003)
    logger.info("Iniciando Backtest de %s a %s...", start_date, end_date)
    logger.info(
        "Capital Inicial: %s | Slippage: %.1f bps | Taxa B3: %.4f%%",
        format_brl(initial_capital),
        slippage_bps,
        fee_rate * 100.0,
    )

    raw_watchlist = watchlist if watchlist is not None else list(getattr(settings, "WATCHLIST", []))
    active_watchlist: list[str] = []
    seen_tickers: set[str] = set()
    for t in raw_watchlist:
        norm = normalize_b3_ticker(t)
        if norm and norm not in seen_tickers:
            seen_tickers.add(norm)
            active_watchlist.append(norm)

    cleaned_data: dict[str, pd.DataFrame] = {}
    all_dates: set[pd.Timestamp] = set()

    if historical_data is not None:
        for raw_ticker, raw_df in historical_data.items():
            norm_ticker = normalize_b3_ticker(raw_ticker) or raw_ticker.strip().upper()
            norm_df = _normalize_ohlcv_dataframe(raw_df)
            if not norm_df.empty:
                cleaned_data[norm_ticker] = norm_df
                all_dates.update(norm_df.index.tolist())
                if norm_ticker not in seen_tickers:
                    seen_tickers.add(norm_ticker)
                    active_watchlist.append(norm_ticker)
    else:
        logger.info("Baixando dados históricos do Yahoo Finance para %d ativos...", len(active_watchlist))
        suffix_map: dict[str, str] = getattr(settings, "YFINANCE_SUFFIX_MAP", {}) or {}
        for ticker in active_watchlist:
            yf_ticker = suffix_map.get(ticker) or to_yfinance_ticker(ticker)
            try:
                df = yf.download(
                    yf_ticker,
                    start=start_date,
                    end=end_date,
                    progress=False,
                    threads=False,
                )
                if df is None or df.empty:
                    logger.warning("Sem dados históricos para %s (%s)", ticker, yf_ticker)
                    continue

                norm_df = _normalize_ohlcv_dataframe(df)
                if norm_df.empty:
                    logger.warning("Dados OHLC inválidos/vazios após limpeza para %s", ticker)
                    continue

                cleaned_data[ticker] = norm_df
                all_dates.update(norm_df.index.tolist())
            except Exception as exc:
                logger.error("Erro baixando dados históricos de %s (%s): %s", ticker, yf_ticker, exc)

    if not cleaned_data:
        logger.error("Nenhum dado histórico válido disponível. Cancelando backtest.")
        return None

    trading_days = sorted(all_dates)
    min_days_required = 55
    if len(trading_days) <= min_days_required:
        logger.error(
            "Poucos pregões (%d) para aquecimento dos indicadores (mínimo: %d).",
            len(trading_days),
            min_days_required + 1,
        )
        return None

    sim_days = trading_days[min_days_required:]
    slip_frac = max(0.0, float(slippage_bps)) / 10_000.0
    fee_frac = max(0.0, float(fee_rate))
    buy_cost_mult = 1.0 + slip_frac + fee_frac
    sell_cost_mult = max(0.0, 1.0 - slip_frac - fee_frac)

    max_daily_loss_cfg = float(getattr(settings, "MAX_DAILY_LOSS_PERCENT", 0.03) or 0.03)
    if max_daily_loss_cfg > 1.0:
        max_daily_loss_cfg /= 100.0

    equity_curve: list[float] = []
    dates_curve: list[pd.Timestamp] = []
    trade_log: list[dict[str, Any]] = []
    pending_orders: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="cortex_backtest_") as tmp_dir:
        temp_state_path = Path(tmp_dir) / "sim_state.json"

        mock_market = MockMarketData(cleaned_data)
        portfolio = Portfolio(initial_capital=initial_capital)
        broker = _create_isolated_broker(initial_capital=initial_capital, state_path=temp_state_path)
        risk_manager = _create_risk_manager()

        engine = DecisionEngine(
            technical=TechnicalAnalyzer(),
            sentiment=SentimentAnalyzer(mode="lightweight"),
            risk_manager=risk_manager,
            market_data=mock_market,
            portfolio=portfolio,
            db=MockDB(),
        )

        logger.info("Iniciando simulação diária sobre %d pregões...", len(sim_days))

        for current_day in sim_days:
            day_start_equity = float(portfolio.get_summary().total_value or initial_capital)
            circuit_breaker_tripped = False
            day_dt = pd.Timestamp(current_day).to_pydatetime().replace(tzinfo=BRT)

            # =================================================================
            # ETAPA 1: Execução na ABERTURA (Open) das ordens geradas em t-1
            # =================================================================
            if pending_orders:
                queued_sells = [
                    o for o in pending_orders if o["action"] in (Action.SELL, Action.EMERGENCY_SELL)
                ]
                queued_buys = [o for o in pending_orders if o["action"] == Action.BUY]
                deferred_sells: list[dict[str, Any]] = []

                for order in queued_sells:
                    ticker = order["ticker"]
                    pos = portfolio.get_position(ticker)
                    if pos is None:
                        continue
                    df_t = cleaned_data.get(ticker)
                    if df_t is None or current_day not in df_t.index:
                        deferred_sells.append(order)
                        continue

                    bar_open = float(df_t.loc[current_day, "Open"])
                    if bar_open <= 0:
                        deferred_sells.append(order)
                        continue

                    exec_price = round(max(0.01, bar_open * sell_cost_mult), 4)
                    qty_to_sell = pos.quantity
                    entry_px = pos.entry_price

                    if _broker_sell(broker, ticker, qty_to_sell, exec_price, reason=order["reason"]):
                        _portfolio_remove_position(portfolio, ticker, sell_price=exec_price)
                        realized_pnl = round((exec_price - entry_px) * qty_to_sell, 2)
                        trade_log.append(
                            {
                                "date": current_day,
                                "ticker": ticker,
                                "action": order["action"].value,
                                "quantity": qty_to_sell,
                                "raw_price": round(bar_open, 4),
                                "price": exec_price,
                                "pnl": realized_pnl,
                                "reason": order["reason"],
                            }
                        )

                # Marca posições remanescentes na abertura para checar circuit breaker e limites
                open_prices_map: dict[str, float] = {}
                for pos in portfolio.get_all_positions():
                    df_t = cleaned_data.get(pos.ticker)
                    if df_t is not None and current_day in df_t.index:
                        open_px = float(df_t.loc[current_day, "Open"])
                        if open_px > 0:
                            open_prices_map[pos.ticker] = open_px

                if open_prices_map:
                    portfolio.update_prices(open_prices_map)
                    _sync_broker_prices(broker, open_prices_map)

                open_equity = float(portfolio.get_summary().total_value or day_start_equity)
                if day_start_equity > 0 and ((day_start_equity - open_equity) / day_start_equity) >= max_daily_loss_cfg:
                    circuit_breaker_tripped = True
                    logger.warning(
                        "Circuit breaker acionado na abertura de %s (queda diária %.2f%%).",
                        current_day,
                        ((day_start_equity - open_equity) / day_start_equity) * 100.0,
                    )

                if not circuit_breaker_tripped:
                    for order in queued_buys:
                        ticker = order["ticker"]
                        if portfolio.get_position(ticker) is not None:
                            continue
                        df_t = cleaned_data.get(ticker)
                        if df_t is None or current_day not in df_t.index:
                            continue

                        bar_open = float(df_t.loc[current_day, "Open"])
                        if bar_open <= 0:
                            continue

                        exec_price = round(bar_open * buy_cost_mult, 4)
                        bp = getattr(portfolio, "buying_power", None)
                        port_cash = float(bp) if isinstance(bp, (int, float)) else float(portfolio.free_cash)
                        avail_cash = min(port_cash, _get_broker_cash(broker, port_cash))
                        if avail_cash < exec_price:
                            continue

                        total_val = float(portfolio.get_summary().total_value or avail_cash)
                        qty = min(int(order["quantity"]), int(avail_cash // exec_price))
                        if qty <= 0:
                            continue

                        is_valid, reject_reason = _validate_buy_with_risk_manager(
                            risk_manager=risk_manager,
                            ticker=ticker,
                            quantity=qty,
                            price=exec_price,
                            available_capital=avail_cash,
                            total_portfolio_value=total_val,
                            positions=portfolio.get_all_positions(),
                        )
                        if not is_valid:
                            resized = int(
                                risk_manager.get_max_shares(
                                    exec_price,
                                    avail_cash,
                                    total_portfolio_value=total_val,
                                    positions=portfolio.get_all_positions(),
                                    ticker=ticker,
                                )
                                or 0
                            )
                            qty = min(qty, resized, int(avail_cash // exec_price))
                            if qty <= 0:
                                logger.debug(
                                    "Ordem de compra em %s descartada na abertura (%s).",
                                    ticker,
                                    reject_reason or "limite de risco",
                                )
                                continue
                            is_valid, reject_reason = _validate_buy_with_risk_manager(
                                risk_manager=risk_manager,
                                ticker=ticker,
                                quantity=qty,
                                price=exec_price,
                                available_capital=avail_cash,
                                total_portfolio_value=total_val,
                                positions=portfolio.get_all_positions(),
                            )
                            if not is_valid:
                                logger.debug(
                                    "Ordem redimensionada de %s recusada (%s).",
                                    ticker,
                                    reject_reason,
                                )
                                continue

                        ref_px = float(order.get("ref_price") or 0.0)
                        queued_sl = float(order.get("stop_loss") or 0.0)
                        if ref_px > 0 and 0 < queued_sl < ref_px:
                            sl_ratio = queued_sl / ref_px
                            fill_sl = round(exec_price * sl_ratio, 2)
                        else:
                            fill_sl = round(
                                float(risk_manager.calculate_stop_loss(exec_price) or 0.0), 2
                            )

                        if _broker_buy(broker, ticker, qty, exec_price, stop_loss=fill_sl):
                            portfolio.add_position(
                                Position(
                                    ticker=ticker,
                                    quantity=qty,
                                    entry_price=exec_price,
                                    current_price=exec_price,
                                    stop_loss=fill_sl,
                                    entry_time=day_dt,
                                )
                            )
                            trade_log.append(
                                {
                                    "date": current_day,
                                    "ticker": ticker,
                                    "action": Action.BUY.value,
                                    "quantity": qty,
                                    "raw_price": round(bar_open, 4),
                                    "price": exec_price,
                                    "pnl": 0.0,
                                    "reason": order["reason"],
                                }
                            )

                pending_orders = deferred_sells

            # =================================================================
            # ETAPA 2: Gestão Intradiária (Stop-Loss em Low, Trailing em High,
            #          Marcação a Mercado em Close e Take-Profit Parcial)
            # =================================================================
            close_prices_today: dict[str, float] = {}
            for ticker, df_t in cleaned_data.items():
                if current_day in df_t.index:
                    close_prices_today[ticker] = float(df_t.loc[current_day, "Close"])

            for pos in list(portfolio.get_all_positions()):
                df_t = cleaned_data.get(pos.ticker)
                if df_t is None or current_day not in df_t.index:
                    continue

                row = df_t.loc[current_day]
                bar_open = float(row["Open"])
                bar_high = float(row["High"])
                bar_low = float(row["Low"])
                bar_close = float(row["Close"])

                # Verifica violação do stop-loss intradiário ANTES de elevar trailing stop
                if pos.stop_loss > 0.0 and bar_low <= pos.stop_loss:
                    raw_stop_fill = min(bar_open, pos.stop_loss)
                    exec_price = round(max(0.01, raw_stop_fill * sell_cost_mult), 4)
                    qty_to_close = pos.quantity
                    entry_px = pos.entry_price

                    if _broker_sell(broker, pos.ticker, qty_to_close, exec_price, reason="STOP_LOSS"):
                        _portfolio_remove_position(portfolio, pos.ticker, sell_price=exec_price)
                        realized_pnl = round((exec_price - entry_px) * qty_to_close, 2)
                        trade_log.append(
                            {
                                "date": current_day,
                                "ticker": pos.ticker,
                                "action": Action.EMERGENCY_SELL.value,
                                "quantity": qty_to_close,
                                "raw_price": round(raw_stop_fill, 4),
                                "price": exec_price,
                                "pnl": realized_pnl,
                                "reason": "INTRADAY_STOP_LOSS",
                            }
                        )
                    continue

                # Posição sobreviveu ao pregão: atualiza trailing stop em High/Close
                _ratchet_trailing_stop(
                    risk_manager=risk_manager,
                    pos=pos,
                    bar_high=bar_high,
                    bar_close=bar_close,
                    broker=broker,
                )

            if close_prices_today:
                portfolio.update_prices(close_prices_today)
                _sync_broker_prices(broker, close_prices_today)

            # Verifica gatilhos de realização parcial de lucro (Take-Profit)
            tp_fn = getattr(risk_manager, "check_take_profit_triggers", None)
            if callable(tp_fn):
                try:
                    tp_positions = tp_fn(portfolio.get_all_positions())
                except Exception as exc:
                    logger.warning("Erro ao verificar take-profit em %s: %s", current_day, exc)
                    tp_positions = []

                for pos in tp_positions:
                    live_pos = portfolio.get_position(pos.ticker)
                    if live_pos is None or live_pos.partial_exit_done:
                        continue
                    if live_pos.quantity < 2:
                        live_pos.partial_exit_done = True
                        live_pos.stop_loss = max(live_pos.stop_loss, live_pos.entry_price)
                        continue

                    qty_to_sell = max(1, live_pos.quantity // 2)
                    raw_tp_px = close_prices_today.get(live_pos.ticker, live_pos.current_price)
                    exec_price = round(max(0.01, raw_tp_px * sell_cost_mult), 4)
                    entry_px = live_pos.entry_price

                    if _broker_sell(
                        broker, live_pos.ticker, qty_to_sell, exec_price, reason="TAKE_PROFIT_PARCIAL"
                    ):
                        _portfolio_reduce_position(
                            portfolio, live_pos.ticker, qty_to_sell, sell_price=exec_price
                        )
                        live_pos.partial_exit_done = True
                        live_pos.stop_loss = max(live_pos.stop_loss, live_pos.entry_price)
                        realized_pnl = round((exec_price - entry_px) * qty_to_sell, 2)
                        trade_log.append(
                            {
                                "date": current_day,
                                "ticker": live_pos.ticker,
                                "action": Action.SELL.value,
                                "quantity": qty_to_sell,
                                "raw_price": round(raw_tp_px, 4),
                                "price": exec_price,
                                "pnl": realized_pnl,
                                "reason": "TAKE_PROFIT_PARCIAL",
                            }
                        )

            # =================================================================
            # ETAPA 3: Fechamento da Barra `t` — Circuit Breaker e Geração de
            #          Sinais para Execução na Abertura de `t+1`
            # =================================================================
            eod_summary = portfolio.get_summary()
            eod_equity = float(eod_summary.total_value)
            if day_start_equity > 0 and ((day_start_equity - eod_equity) / day_start_equity) >= max_daily_loss_cfg:
                if not circuit_breaker_tripped:
                    logger.warning(
                        "Circuit breaker diário atingido no fechamento de %s (queda %.2f%%).",
                        current_day,
                        ((day_start_equity - eod_equity) / day_start_equity) * 100.0,
                    )
                circuit_breaker_tripped = True

            mock_market.current_date = current_day
            bar_dt_15h_brt = day_dt.replace(hour=15, minute=0, second=0, microsecond=0)
            already_queued_tickers = {o["ticker"] for o in pending_orders}

            held_set = {p.ticker for p in portfolio.get_all_positions()}
            ordered_tickers = [t for t in active_watchlist if t in held_set] + [
                t for t in active_watchlist if t not in held_set
            ]

            for ticker in ordered_tickers:
                if ticker not in close_prices_today or ticker in already_queued_tickers:
                    continue
                try:
                    decision = engine.evaluate(
                        ticker, news_items=[], reference_time=bar_dt_15h_brt
                    )
                except Exception as exc:
                    logger.error("Erro avaliando %s em %s: %s", ticker, current_day, exc)
                    continue

                if decision.action in (Action.SELL, Action.EMERGENCY_SELL) and decision.quantity > 0:
                    pending_orders.append(
                        {
                            "ticker": ticker,
                            "action": decision.action,
                            "quantity": decision.quantity,
                            "ref_price": decision.price,
                            "stop_loss": decision.stop_loss,
                            "reason": decision.action.name,
                        }
                    )
                    already_queued_tickers.add(ticker)
                elif (
                    decision.action == Action.BUY
                    and decision.quantity > 0
                    and not circuit_breaker_tripped
                ):
                    pending_orders.append(
                        {
                            "ticker": ticker,
                            "action": Action.BUY,
                            "quantity": decision.quantity,
                            "ref_price": decision.price,
                            "stop_loss": decision.stop_loss,
                            "reason": "BUY_SIGNAL",
                        }
                    )
                    already_queued_tickers.add(ticker)

            equity_curve.append(round(eod_equity, 2))
            dates_curve.append(current_day)

    final_value = equity_curve[-1] if equity_curve else initial_capital
    pnl = final_value - initial_capital
    pnl_pct = (pnl / initial_capital) * 100.0 if initial_capital > 0 else 0.0
    max_dd_pct = _compute_max_drawdown_pct([initial_capital] + equity_curve)
    sharpe = _compute_sharpe_ratio([initial_capital] + equity_curve)

    closed_trades = [t for t in trade_log if t["action"] != Action.BUY.value]
    winning_trades = [t for t in closed_trades if float(t.get("pnl", 0.0)) > 0.0]
    win_rate = (len(winning_trades) / len(closed_trades) * 100.0) if closed_trades else 0.0

    logger.info("=== RESULTADO DO BACKTEST ===")
    logger.info("Capital Inicial: %s", format_brl(initial_capital))
    logger.info("Capital Final:   %s", format_brl(final_value))
    logger.info("Lucro Líquido:   %s (%.2f%%)", format_brl(pnl), pnl_pct)
    logger.info(
        "Max Drawdown:    %.2f%% | Sharpe: %.2f | Trades fechados: %d (Win Rate: %.1f%%)",
        max_dd_pct,
        sharpe,
        len(closed_trades),
        win_rate,
    )

    ibov_ret: Optional[float] = None
    alpha_pct: Optional[float] = None
    if historical_data is None:
        try:
            ibov = yf.download("^BVSP", start=start_date, end=end_date, progress=False, threads=False)
            if ibov is not None and not ibov.empty:
                if isinstance(ibov.columns, pd.MultiIndex):
                    ibov.columns = ibov.columns.get_level_values(0)
                if "Close" in ibov.columns:
                    ibov_close = ibov["Close"].dropna().copy()
                    ibov_idx = pd.to_datetime(ibov_close.index)
                    if getattr(ibov_idx, "tz", None) is not None:
                        ibov_idx = ibov_idx.tz_convert(BRT).tz_localize(None)
                    ibov_close.index = ibov_idx.normalize()
                    ibov_close = ibov_close[~ibov_close.index.duplicated(keep="last")].sort_index()

                    target_idx = pd.to_datetime(pd.Index(dates_curve)).normalize()
                    aligned_ibov = ibov_close.reindex(target_idx).ffill().bfill().dropna()
                    if len(aligned_ibov) >= 2 and float(aligned_ibov.iloc[0]) > 0:
                        ibov_start = float(aligned_ibov.iloc[0])
                        ibov_end = float(aligned_ibov.iloc[-1])
                        ibov_ret = ((ibov_end - ibov_start) / ibov_start) * 100.0
                        alpha_pct = pnl_pct - ibov_ret
                        logger.info("Retorno IBOVESPA alinhado no período: %.2f%%", ibov_ret)
                        logger.info("Alpha do Córtex sobre IBOVESPA: %.2f%%", alpha_pct)
        except Exception as exc:
            logger.warning("Não foi possível calcular benchmark IBOVESPA: %s", exc)

    if plot and plt is not None and dates_curve and equity_curve:
        fig = None
        try:
            fig, ax = plt.subplots(figsize=(10, 5))
            ax.plot(dates_curve, equity_curve, label="Córtex IA", color="#00f2fe", linewidth=1.8)
            ax.axhline(initial_capital, color="#888888", linestyle="--", linewidth=0.9, label="Capital Inicial")
            ax.set_title("Backtest Equity Curve (Sem Look-Ahead Bias)")
            ax.set_xlabel("Data")
            ax.set_ylabel("Patrimônio Total (R$)")
            ax.grid(True, alpha=0.3)
            ax.legend()
            fig.tight_layout()
            out_file = Path(settings.PROJECT_ROOT) / "backtest" / "equity_curve.png"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(out_file)
            logger.info("Gráfico salvo em: %s", out_file)
        except Exception as exc:
            logger.warning("Não foi possível plotar o gráfico de backtest: %s", exc)
        finally:
            if fig is not None:
                plt.close(fig)
    elif plot and plt is None:
        logger.warning("matplotlib não instalado — geração de gráfico ignorada.")

    return {
        "initial_capital": round(initial_capital, 2),
        "final_value": round(final_value, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "max_drawdown_pct": max_dd_pct,
        "sharpe_ratio": sharpe,
        "ibov_return_pct": round(ibov_ret, 2) if ibov_ret is not None else None,
        "alpha_pct": round(alpha_pct, 2) if alpha_pct is not None else None,
        "total_trades": len(trade_log),
        "closed_trades": len(closed_trades),
        "win_rate": round(win_rate, 2),
        "equity_curve": equity_curve,
        "dates": dates_curve,
        "trades": trade_log,
    }


if __name__ == "__main__":
    end_dt = datetime.now(tz=BRT)
    start_dt = end_dt - timedelta(days=730)
    run_backtest(start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d"))
