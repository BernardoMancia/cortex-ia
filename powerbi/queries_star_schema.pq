section CortexIAStarSchema;

shared ServerBaseUrl = "http://localhost:8003" meta [
    IsParameterQuery = true,
    Type = "Text",
    IsParameterQueryRequired = true
];

shared BiApiKey = "COLE_SEU_TOKEN_CTX_BI_AQUI" meta [
    IsParameterQuery = true,
    Type = "Text",
    IsParameterQueryRequired = true
];

shared WalletIdFilter = "" meta [
    IsParameterQuery = true,
    Type = "Text",
    IsParameterQueryRequired = false
];

shared CortexFetch = (relPath as text, optional queryParams as nullable record) as list =>
    let
        BaseParams = if queryParams = null then [flat = "true"] else Record.Combine({[flat = "true"], queryParams}),
        CleanWalletFilter = Text.Trim(Text.From(WalletIdFilter)),
        EffectiveQuery = if CleanWalletFilter <> "" and not Record.HasFields(BaseParams, "wallet_id")
            then Record.AddField(BaseParams, "wallet_id", CleanWalletFilter)
            else BaseParams,
        RawBytes = Web.Contents(
            ServerBaseUrl,
            [
                RelativePath = relPath,
                Query = EffectiveQuery,
                Headers = [
                    Authorization = "Bearer " & Text.Trim(BiApiKey),
                    Accept = "application/json"
                ]
            ]
        ),
        Parsed = Json.Document(RawBytes),
        RowsList = if Value.Is(Parsed, type list)
            then Parsed
            else if Value.Is(Parsed, type record) and Record.HasFields(Parsed, "rows")
            then Parsed[rows]
            else {}
    in
        RowsList;

shared CortexRecordsToTable = (records as list, schemaColumns as list, columnTypes as list) as table =>
    let
        EmptyTyped = Table.TransformColumnTypes(Table.FromRows({}, schemaColumns), columnTypes),
        Populated = if List.IsEmpty(records)
            then EmptyTyped
            else Table.TransformColumnTypes(
                Table.SelectColumns(
                    Table.FromRecords(records, schemaColumns, MissingField.UseNull),
                    schemaColumns
                ),
                columnTypes,
                "en-US"
            )
    in
        Populated;

shared dim_wallets =
    let
        Rows = CortexFetch("api/bi/v1/dim_wallets", null),
        Cols = {
            "wallet_id", "user_id", "name", "connector_type", "is_default",
            "automation_enabled", "operational_mode", "initial_capital",
            "free_cash", "ai_allocated_capital", "vault_reserve_cash",
            "stop_loss_pct", "trailing_trigger_pct", "trailing_distance_pct",
            "protect_20k_exemption", "avoid_day_trade_tax", "created_at",
            "updated_at", "environment", "is_simulation"
        },
        Types = {
            {"wallet_id", Int64.Type},
            {"user_id", Int64.Type},
            {"name", type text},
            {"connector_type", type text},
            {"is_default", type logical},
            {"automation_enabled", type logical},
            {"operational_mode", type text},
            {"initial_capital", type number},
            {"free_cash", type number},
            {"ai_allocated_capital", type number},
            {"vault_reserve_cash", type number},
            {"stop_loss_pct", type number},
            {"trailing_trigger_pct", type number},
            {"trailing_distance_pct", type number},
            {"protect_20k_exemption", type logical},
            {"avoid_day_trade_tax", type logical},
            {"created_at", type text},
            {"updated_at", type text},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared dim_assets_b3 =
    let
        Rows = CortexFetch("api/bi/v1/dim_assets_b3", null),
        Cols = {
            "ticker", "company_name", "sector", "primary_index",
            "asset_class", "standard_lot_size", "swing_20k_exempt_eligible",
            "environment", "is_simulation"
        },
        Types = {
            {"ticker", type text},
            {"company_name", type text},
            {"sector", type text},
            {"primary_index", type text},
            {"asset_class", type text},
            {"standard_lot_size", Int64.Type},
            {"swing_20k_exempt_eligible", type logical},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared dim_calendar_b3 =
    let
        Rows = CortexFetch("api/bi/v1/dim_calendar_b3", [days = "730"]),
        Cols = {
            "date", "year", "quarter", "month", "year_month",
            "month_name_pt", "day_of_week_num", "day_of_week_pt",
            "is_b3_business_day", "is_ash_wednesday", "d2_settlement_date",
            "environment", "is_simulation"
        },
        Types = {
            {"date", type date},
            {"year", Int64.Type},
            {"quarter", Int64.Type},
            {"month", Int64.Type},
            {"year_month", type text},
            {"month_name_pt", type text},
            {"day_of_week_num", Int64.Type},
            {"day_of_week_pt", type text},
            {"is_b3_business_day", type logical},
            {"is_ash_wednesday", type logical},
            {"d2_settlement_date", type date},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared fact_equity =
    let
        Rows = CortexFetch("api/bi/v1/fact_equity", [days = "730"]),
        Cols = {
            "id", "wallet_id", "date", "snapshot_date", "timestamp",
            "initial_capital", "total_equity", "operational_equity",
            "free_cash", "allocated_capital", "vault_reserve_cash",
            "pnl_percent", "daily_pnl_percent", "high_water_mark",
            "drawdown_pct", "buys_count", "sells_count",
            "environment", "is_simulation"
        },
        Types = {
            {"id", Int64.Type},
            {"wallet_id", Int64.Type},
            {"date", type date},
            {"snapshot_date", type date},
            {"timestamp", type text},
            {"initial_capital", type number},
            {"total_equity", type number},
            {"operational_equity", type number},
            {"free_cash", type number},
            {"allocated_capital", type number},
            {"vault_reserve_cash", type number},
            {"pnl_percent", type number},
            {"daily_pnl_percent", type number},
            {"high_water_mark", type number},
            {"drawdown_pct", type number},
            {"buys_count", Int64.Type},
            {"sells_count", Int64.Type},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared fact_positions =
    let
        Rows = CortexFetch("api/bi/v1/fact_positions", null),
        Cols = {
            "wallet_id", "ticker", "sector", "quantity", "entry_price",
            "fiscal_avg_price", "current_price", "stop_loss", "highest_price",
            "market_value", "cost_basis", "weight_pct", "unrealized_pnl",
            "pnl_percent", "stop_distance_pct", "do_not_touch",
            "partial_tp_taken", "holding_days", "opened_at",
            "environment", "is_simulation"
        },
        Types = {
            {"wallet_id", Int64.Type},
            {"ticker", type text},
            {"sector", type text},
            {"quantity", Int64.Type},
            {"entry_price", type number},
            {"fiscal_avg_price", type number},
            {"current_price", type number},
            {"stop_loss", type number},
            {"highest_price", type number},
            {"market_value", type number},
            {"cost_basis", type number},
            {"weight_pct", type number},
            {"unrealized_pnl", type number},
            {"pnl_percent", type number},
            {"stop_distance_pct", type number},
            {"do_not_touch", type logical},
            {"partial_tp_taken", type logical},
            {"holding_days", Int64.Type},
            {"opened_at", type text},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared fact_trades =
    let
        Rows = CortexFetch("api/bi/v1/fact_trades", [days = "730"]),
        Cols = {
            "id", "wallet_id", "date", "timestamp", "ticker", "sector",
            "action", "quantity", "price", "total_value", "fee",
            "net_value", "realized_pnl", "stop_loss", "is_day_trade",
            "client_order_id", "reasoning", "environment", "is_simulation"
        },
        Types = {
            {"id", Int64.Type},
            {"wallet_id", Int64.Type},
            {"date", type date},
            {"timestamp", type text},
            {"ticker", type text},
            {"sector", type text},
            {"action", type text},
            {"quantity", Int64.Type},
            {"price", type number},
            {"total_value", type number},
            {"fee", type number},
            {"net_value", type number},
            {"realized_pnl", type number},
            {"stop_loss", type number},
            {"is_day_trade", type logical},
            {"client_order_id", type text},
            {"reasoning", type text},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared fact_decisions =
    let
        Rows = CortexFetch("api/bi/v1/fact_decisions", [days = "180"]),
        Cols = {
            "id", "wallet_id", "date", "timestamp", "ticker", "sector",
            "action", "confidence", "confidence_pct", "trend_signal",
            "sentiment_score", "reasoning", "environment", "is_simulation"
        },
        Types = {
            {"id", Int64.Type},
            {"wallet_id", Int64.Type},
            {"date", type date},
            {"timestamp", type text},
            {"ticker", type text},
            {"sector", type text},
            {"action", type text},
            {"confidence", type number},
            {"confidence_pct", type number},
            {"trend_signal", type text},
            {"sentiment_score", type number},
            {"reasoning", type text},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared fact_cashflow_d2 =
    let
        Rows = CortexFetch("api/bi/v1/fact_cashflow_d2", [days = "365"]),
        Cols = {
            "wallet_id", "date", "settlement_date", "entry_kind",
            "subtype", "amount", "settled", "details", "timestamp",
            "environment", "is_simulation"
        },
        Types = {
            {"wallet_id", Int64.Type},
            {"date", type date},
            {"settlement_date", type date},
            {"entry_kind", type text},
            {"subtype", type text},
            {"amount", type number},
            {"settled", type logical},
            {"details", type text},
            {"timestamp", type text},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared fact_market_radar =
    let
        Rows = CortexFetch("api/bi/v1/fact_market_radar", null),
        Cols = {
            "ticker", "sector", "price", "bid", "ask", "spread_pct",
            "volume", "change_pct", "rsi", "macd_hist", "atr", "adx",
            "trend_signal", "mtf_bullish", "sentiment_score", "is_stale",
            "updated_at", "environment", "is_simulation"
        },
        Types = {
            {"ticker", type text},
            {"sector", type text},
            {"price", type number},
            {"bid", type number},
            {"ask", type number},
            {"spread_pct", type number},
            {"volume", type number},
            {"change_pct", type number},
            {"rsi", type number},
            {"macd_hist", type number},
            {"atr", type number},
            {"adx", type number},
            {"trend_signal", type text},
            {"mtf_bullish", type logical},
            {"sentiment_score", type number},
            {"is_stale", type logical},
            {"updated_at", type text},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);

shared kpi_summary =
    let
        Rows = CortexFetch("api/bi/v1/kpi_summary", [days = "365"]),
        Cols = {
            "wallet_id", "initial_capital", "total_equity", "operational_equity",
            "free_cash", "allocated_capital", "vault_reserve_cash", "buying_power",
            "unrealized_pnl", "realized_pnl_total", "total_fees",
            "period_return_pct", "cumulative_return_pct", "daily_return_pct",
            "current_drawdown_pct", "max_drawdown_pct", "high_water_mark",
            "volatility_annualized_pct", "sharpe_annualized", "sortino_annualized",
            "calmar_ratio", "total_trades", "buy_trades", "sell_trades",
            "winning_sells", "losing_sells", "win_rate_pct", "avg_win_brl",
            "avg_loss_brl", "payoff_ratio", "profit_factor", "expectancy_brl",
            "positive_days_pct", "bova11_change_pct", "tax_sold_month_brl",
            "tax_limit_brl", "tax_guard_buffer_brl", "tax_remaining_exempt_brl",
            "tax_utilization_pct", "settled_cash_brl", "pending_d2_net_brl",
            "environment", "is_simulation"
        },
        Types = {
            {"wallet_id", Int64.Type},
            {"initial_capital", type number},
            {"total_equity", type number},
            {"operational_equity", type number},
            {"free_cash", type number},
            {"allocated_capital", type number},
            {"vault_reserve_cash", type number},
            {"buying_power", type number},
            {"unrealized_pnl", type number},
            {"realized_pnl_total", type number},
            {"total_fees", type number},
            {"period_return_pct", type number},
            {"cumulative_return_pct", type number},
            {"daily_return_pct", type number},
            {"current_drawdown_pct", type number},
            {"max_drawdown_pct", type number},
            {"high_water_mark", type number},
            {"volatility_annualized_pct", type number},
            {"sharpe_annualized", type number},
            {"sortino_annualized", type number},
            {"calmar_ratio", type number},
            {"total_trades", Int64.Type},
            {"buy_trades", Int64.Type},
            {"sell_trades", Int64.Type},
            {"winning_sells", Int64.Type},
            {"losing_sells", Int64.Type},
            {"win_rate_pct", type number},
            {"avg_win_brl", type number},
            {"avg_loss_brl", type number},
            {"payoff_ratio", type number},
            {"profit_factor", type number},
            {"expectancy_brl", type number},
            {"positive_days_pct", type number},
            {"bova11_change_pct", type number},
            {"tax_sold_month_brl", type number},
            {"tax_limit_brl", type number},
            {"tax_guard_buffer_brl", type number},
            {"tax_remaining_exempt_brl", type number},
            {"tax_utilization_pct", type number},
            {"settled_cash_brl", type number},
            {"pending_d2_net_brl", type number},
            {"environment", type text},
            {"is_simulation", type logical}
        }
    in
        CortexRecordsToTable(Rows, Cols, Types);
