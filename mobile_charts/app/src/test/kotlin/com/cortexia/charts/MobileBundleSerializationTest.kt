package com.cortexia.charts

import com.cortexia.charts.data.ChartsJson
import com.cortexia.charts.data.LoginResponse
import com.cortexia.charts.data.MobileBundle
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MobileBundleSerializationTest {

    @Test
    fun deserializesFullV020MobileBundleWithUnknownKeysAndNullCoercion() {
        val rawJson = """
        {
          "success": true,
          "environment": "production",
          "environment_label": "PRODUÇÃO REAL",
          "is_production": true,
          "is_simulation": false,
          "generated_at": "2026-11-13T15:00:00Z",
          "market_open": true,
          "market_status": "ABERTO",
          "bova11_change_pct": 1.42,
          "regime_bearish_veto": false,
          "future_server_field": "ignored_safely",
          "active_wallet": {
            "id": 2,
            "name": "Carteira Swing B3",
            "connector_type": "mt5_btg",
            "operational_mode": "AUTONOMOUS",
            "automation_enabled": true,
            "is_selected": true
          },
          "wallets": [
            {
              "id": 2,
              "name": "Carteira Swing B3",
              "connector_type": "mt5_btg",
              "operational_mode": "AUTONOMOUS",
              "automation_enabled": true,
              "is_selected": true
            },
            {
              "id": 5,
              "name": "Carteira Dividendos",
              "connector_type": "simulation",
              "operational_mode": "ASSISTED",
              "automation_enabled": false,
              "is_selected": false
            }
          ],
          "kpi_summary": {
            "wallet_id": 2,
            "initial_capital": 50000.0,
            "total_equity": 58420.50,
            "operational_equity": 55420.50,
            "free_cash": 15420.50,
            "allocated_capital": 40000.0,
            "vault_reserve_cash": 3000.0,
            "buying_power": 15420.50,
            "unrealized_pnl": 2150.0,
            "realized_pnl_total": 6270.50,
            "total_fees": 84.20,
            "period_return_pct": 16.84,
            "cumulative_return_pct": 16.84,
            "daily_return_pct": 0.92,
            "current_drawdown_pct": -0.85,
            "max_drawdown_pct": -3.40,
            "high_water_mark": 58921.0,
            "volatility_annualized_pct": 14.6,
            "sharpe_annualized": 2.18,
            "sortino_annualized": 3.05,
            "calmar_ratio": 4.95,
            "total_trades": 38,
            "buy_trades": 20,
            "sell_trades": 18,
            "winning_sells": 13,
            "losing_sells": 5,
            "win_rate_pct": 72.22,
            "avg_win_brl": 640.0,
            "avg_loss_brl": -410.0,
            "payoff_ratio": 1.56,
            "profit_factor": 4.06,
            "expectancy_brl": 348.36,
            "positive_days_pct": 64.0,
            "bova11_change_pct": 1.42,
            "tax_sold_month_brl": 14200.0,
            "tax_limit_brl": 20000.0,
            "tax_guard_buffer_brl": 19500.0,
            "tax_remaining_exempt_brl": 5800.0,
            "tax_utilization_pct": 71.0,
            "settled_cash_brl": 12220.50,
            "pending_d2_net_brl": 3200.0
          },
          "equity_series": [
            {
              "date": "2026-11-12",
              "timestamp": "2026-11-12T21:00:00Z",
              "total_equity": 57888.0,
              "operational_equity": 54888.0,
              "initial_capital": 50000.0,
              "free_cash": 14888.0,
              "allocated_capital": 40000.0,
              "vault_reserve_cash": 3000.0,
              "pnl_percent": 15.77,
              "daily_pnl_percent": 0.45,
              "high_water_mark": 58921.0,
              "drawdown_pct": -1.75,
              "buys_count": 1,
              "sells_count": 0
            },
            {
              "date": "2026-11-13",
              "timestamp": "2026-11-13T15:00:00Z",
              "total_equity": 58420.50,
              "operational_equity": 55420.50,
              "initial_capital": 50000.0,
              "free_cash": 15420.50,
              "allocated_capital": 40000.0,
              "vault_reserve_cash": 3000.0,
              "pnl_percent": 16.84,
              "daily_pnl_percent": 0.92,
              "high_water_mark": 58921.0,
              "drawdown_pct": -0.85,
              "buys_count": 0,
              "sells_count": 1
            }
          ],
          "monthly_returns": [
            {"year": 2026, "month": 10, "year_month": "2026-10", "return_pct": 8.2, "pnl_brl": 4100.0, "trades_count": 19},
            {"year": 2026, "month": 11, "year_month": "2026-11", "return_pct": 7.98, "pnl_brl": 4320.5, "trades_count": 19}
          ],
          "positions": [
            {
              "ticker": "WEGE3",
              "sector": "Industrial",
              "quantity": 500,
              "entry_price": 50.0,
              "current_price": 53.5,
              "stop_loss": 49.2,
              "highest_price": 54.0,
              "market_value": 26750.0,
              "weight_pct": 45.78,
              "unrealized_pnl": 1750.0,
              "pnl_percent": 7.0,
              "stop_distance_pct": 8.03,
              "do_not_touch": false,
              "partial_tp_taken": true,
              "holding_days": 9
            }
          ],
          "sector_exposure": [
            {"sector": "Industrial", "market_value": 26750.0, "weight_pct": 45.78, "unrealized_pnl": 1750.0, "positions_count": 1}
          ],
          "pnl_by_ticker": [
            {"ticker": "WEGE3", "sector": "Industrial", "realized_pnl": 1200.0, "unrealized_pnl": 1750.0, "total_pnl": 2950.0, "trades_count": 4, "win_rate_pct": 75.0}
          ],
          "pnl_by_sector": [
            {"sector": "Industrial", "realized_pnl": 1200.0, "unrealized_pnl": 1750.0, "total_pnl": 2950.0, "trades_count": 4}
          ],
          "cashflow_d2": [
            {"trade_date": "2026-11-12", "settlement_date": "2026-11-16", "net_sell_amount": 3200.0, "settled": false}
          ],
          "decisions_summary": {
            "total": 25,
            "buy_count": 9,
            "sell_count": 4,
            "hold_count": 12,
            "avg_confidence_pct": 74.5,
            "confidence_buckets": [
              {"label": "0-20%", "count": 0},
              {"label": "20-40%", "count": 2},
              {"label": "40-60%", "count": 5},
              {"label": "60-80%", "count": 11},
              {"label": "80-100%", "count": 7}
            ]
          },
          "market_radar": [
            {
              "ticker": "WEGE3",
              "sector": "Industrial",
              "price": 53.5,
              "change_pct": 2.15,
              "volume": 195000000.0,
              "rsi": 61.4,
              "macd_hist": 0.34,
              "adx": 28.5,
              "sentiment_score": 0.68,
              "trend_signal": "ALTA_FORTE",
              "is_stale": false
            }
          ]
        }
        """.trimIndent()

        val parsed = ChartsJson.decodeFromString(MobileBundle.serializer(), rawJson)
        assertTrue(parsed.is_production)
        assertFalse(parsed.is_simulation)
        assertEquals("production", parsed.environment)
        assertEquals(2, parsed.wallets.size)
        assertEquals("Carteira Swing B3", parsed.active_wallet?.name)
        assertEquals(58420.50, parsed.kpi_summary.total_equity, 0.01)
        assertEquals(2.18, parsed.kpi_summary.sharpe_annualized, 0.01)
        assertEquals(71.0, parsed.kpi_summary.tax_utilization_pct, 0.01)
        assertEquals(2, parsed.equity_series.size)
        assertEquals(2, parsed.monthly_returns.size)
        assertEquals("WEGE3", parsed.positions.first().ticker)
        assertTrue(parsed.positions.first().partial_tp_taken)
        assertEquals(5, parsed.decisions_summary.confidence_buckets.size)
        assertEquals(61.4, parsed.market_radar.first().rsi ?: 0.0, 0.01)
    }

    @Test
    fun deserializesTotpChallengeAndSuccessLoginPayloads() {
        val challengeJson = """{"success": false, "totp_required": true, "must_change_password": false, "message": "Digite o token 2FA"}"""
        val challenge = ChartsJson.decodeFromString(LoginResponse.serializer(), challengeJson)
        assertFalse(challenge.success)
        assertTrue(challenge.totp_required)
        assertEquals("Digite o token 2FA", challenge.message)

        val authJson = """
        {
          "success": true,
          "token": "raw_tok_123",
          "csrf_token": "csrf_456",
          "must_change_password": false,
          "totp_required": false,
          "user": {"id": 1, "username": "Luke", "is_owner_admin": true, "totp_enabled": true}
        }
        """.trimIndent()
        val auth = ChartsJson.decodeFromString(LoginResponse.serializer(), authJson)
        assertTrue(auth.success)
        assertEquals("raw_tok_123", auth.token)
        assertEquals("Luke", auth.user?.username)
        assertTrue(auth.user?.totp_enabled == true)
    }
}
