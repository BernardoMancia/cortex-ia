//+------------------------------------------------------------------+
//|                                              CortexBridgeEA.mq5  |
//|                                   Projeto Cortex IA v2.9.0 (B3)  |
//|  High-frequency local RPC bridge via MQL5 FILE_COMMON sandbox    |
//|  Elimina dependencia de Named Pipes Win32 sob Wine/Linux 24/7.   |
//+------------------------------------------------------------------+
#property copyright "Projeto Cortex IA"
#property version   "2.90"

string g_rpc_folder = "cortex_rpc";
ulong  g_last_hb_ms = 0;

//+------------------------------------------------------------------+
//| Escape seguro para strings JSON UTF-8                            |
//+------------------------------------------------------------------+
string JsonEscape(const string raw)
{
   string out = "";
   int len = StringLen(raw);
   for(int i = 0; i < len; i++)
   {
      ushort ch = StringGetCharacter(raw, i);
      if(ch == '"')       out += "\\\"";
      else if(ch == '\\') out += "\\\\";
      else if(ch == '\n') out += "\\n";
      else if(ch == '\r') out += "\\r";
      else if(ch == '\t') out += "\\t";
      else if(ch >= 32)   out += ShortToString(ch);
   }
   return out;
}

//+------------------------------------------------------------------+
//| Formata double finito para JSON                                  |
//+------------------------------------------------------------------+
string JsonDouble(const double val, const int digits = 8)
{
   if(!MathIsValidNumber(val))
      return "0.0";
   return DoubleToString(val, digits);
}

//+------------------------------------------------------------------+
//| Escrita atomica UTF-8 (.tmp -> FileMove FILE_REWRITE)            |
//+------------------------------------------------------------------+
bool WriteAtomicJson(const string final_rel_path, const string tmp_rel_path, const string payload)
{
   int h = FileOpen(tmp_rel_path, FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON, '\n', CP_UTF8);
   if(h == INVALID_HANDLE)
      return false;
   FileWriteString(h, payload);
   FileFlush(h);
   FileClose(h);
   return FileMove(tmp_rel_path, FILE_COMMON, final_rel_path, FILE_COMMON | FILE_REWRITE);
}

//+------------------------------------------------------------------+
//| Atualiza heartbeat.json com estado vivo da conta e permissoes    |
//+------------------------------------------------------------------+
string BuildAccountStatusJson()
{
   bool term_connected = (TerminalInfoInteger(TERMINAL_CONNECTED) != 0);
   long login          = AccountInfoInteger(ACCOUNT_LOGIN);
   bool connected      = (term_connected && login > 0);
   bool term_trade     = (TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) != 0);
   bool mql_trade      = (MQLInfoInteger(MQL_TRADE_ALLOWED) != 0);
   bool acc_trade      = (AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) != 0);
   bool acc_expert     = (AccountInfoInteger(ACCOUNT_TRADE_EXPERT) != 0);
   bool trade_allowed  = (term_trade && mql_trade && acc_trade && acc_expert);

   string server   = AccountInfoString(ACCOUNT_SERVER);
   string currency = AccountInfoString(ACCOUNT_CURRENCY);
   double balance  = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity   = AccountInfoDouble(ACCOUNT_EQUITY);
   double margin   = AccountInfoDouble(ACCOUNT_MARGIN);
   double free_m   = AccountInfoDouble(ACCOUNT_MARGIN_FREE);

   string j = "{";
   j += "\"ok\":true,";
   j += "\"connected\":" + (connected ? "true" : "false") + ",";
   j += "\"terminal_connected\":" + (term_connected ? "true" : "false") + ",";
   j += "\"trade_allowed\":" + (trade_allowed ? "true" : "false") + ",";
   j += "\"terminal_trade_allowed\":" + (term_trade ? "true" : "false") + ",";
   j += "\"expert_trade_allowed\":" + (mql_trade ? "true" : "false") + ",";
   j += "\"account_trade_allowed\":" + (acc_trade ? "true" : "false") + ",";
   j += "\"account_expert_allowed\":" + (acc_expert ? "true" : "false") + ",";
   j += "\"login\":" + IntegerToString(login) + ",";
   j += "\"server\":\"" + JsonEscape(server) + "\",";
   j += "\"currency\":\"" + JsonEscape(currency) + "\",";
   j += "\"balance\":" + JsonDouble(balance, 2) + ",";
   j += "\"equity\":" + JsonDouble(equity, 2) + ",";
   j += "\"margin\":" + JsonDouble(margin, 2) + ",";
   j += "\"margin_free\":" + JsonDouble(free_m, 2) + ",";
   j += "\"server_time\":" + IntegerToString((long)TimeCurrent());
   j += "}";
   return j;
}

void WriteHeartbeat()
{
   string payload = BuildAccountStatusJson();
   WriteAtomicJson(g_rpc_folder + "\\heartbeat.json", g_rpc_folder + "\\heartbeat.tmp", payload);
   g_last_hb_ms = GetTickCount64();
}

//+------------------------------------------------------------------+
//| Busca valor em pares chave=valor                                 |
//+------------------------------------------------------------------+
string GetParam(const string &keys[], const string &vals[], const int count, const string key, const string def_val = "")
{
   for(int i = 0; i < count; i++)
   {
      if(keys[i] == key)
         return vals[i];
   }
   return def_val;
}

//+------------------------------------------------------------------+
//| Converte timeframe string ou constante MT5 para ENUM_TIMEFRAMES  |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES ResolveTimeframe(string tf)
{
   StringTrimLeft(tf);
   StringTrimRight(tf);
   StringToUpper(tf);
   if(tf == "1"     || tf == "M1"  || tf == "1M")  return PERIOD_M1;
   if(tf == "5"     || tf == "M5"  || tf == "5M")  return PERIOD_M5;
   if(tf == "15"    || tf == "M15" || tf == "15M") return PERIOD_M15;
   if(tf == "30"    || tf == "M30" || tf == "30M") return PERIOD_M30;
   if(tf == "16385" || tf == "H1"  || tf == "1H" || tf == "60M") return PERIOD_H1;
   if(tf == "16388" || tf == "H4"  || tf == "4H")  return PERIOD_H4;
   if(tf == "16408" || tf == "D1"  || tf == "1D")  return PERIOD_D1;
   if(tf == "32769" || tf == "W1"  || tf == "1W")  return PERIOD_W1;
   if(tf == "49153" || tf == "MN1" || tf == "MN")  return PERIOD_MN1;
   return PERIOD_D1;
}

//+------------------------------------------------------------------+
//| Garante selecao do simbolo no MarketWatch                        |
//+------------------------------------------------------------------+
bool EnsureSymbolSelected(const string sym)
{
   if(StringLen(sym) == 0)
      return false;
   if(SymbolSelect(sym, true))
      return true;
   Sleep(50);
   return SymbolSelect(sym, true);
}

//+------------------------------------------------------------------+
//| Comandos RPC                                                     |
//+------------------------------------------------------------------+
string HandlePositions(const long filter_ticket, const string filter_symbol)
{
   int total = PositionsTotal();
   string j = "{\"ok\":true,\"positions\":[";
   bool first = true;
   for(int i = 0; i < total; i++)
   {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0)
         continue;
      if(filter_ticket > 0 && (long)ticket != filter_ticket)
         continue;
      string sym = PositionGetString(POSITION_SYMBOL);
      if(StringLen(filter_symbol) > 0 && sym != filter_symbol)
         continue;

      long   ptype   = PositionGetInteger(POSITION_TYPE);
      long   magic   = PositionGetInteger(POSITION_MAGIC);
      long   ptime   = PositionGetInteger(POSITION_TIME);
      double vol     = PositionGetDouble(POSITION_VOLUME);
      double popen   = PositionGetDouble(POSITION_PRICE_OPEN);
      double pcurr   = PositionGetDouble(POSITION_PRICE_CURRENT);
      double sl      = PositionGetDouble(POSITION_SL);
      double tp      = PositionGetDouble(POSITION_TP);
      double profit  = PositionGetDouble(POSITION_PROFIT);
      string comment = PositionGetString(POSITION_COMMENT);

      if(!first) j += ",";
      first = false;
      j += "{";
      j += "\"ticket\":" + IntegerToString((long)ticket) + ",";
      j += "\"symbol\":\"" + JsonEscape(sym) + "\",";
      j += "\"type\":" + IntegerToString(ptype) + ",";
      j += "\"magic\":" + IntegerToString(magic) + ",";
      j += "\"time\":" + IntegerToString(ptime) + ",";
      j += "\"volume\":" + JsonDouble(vol, 4) + ",";
      j += "\"price_open\":" + JsonDouble(popen, 4) + ",";
      j += "\"price_current\":" + JsonDouble(pcurr, 4) + ",";
      j += "\"sl\":" + JsonDouble(sl, 4) + ",";
      j += "\"tp\":" + JsonDouble(tp, 4) + ",";
      j += "\"profit\":" + JsonDouble(profit, 2) + ",";
      j += "\"comment\":\"" + JsonEscape(comment) + "\"";
      j += "}";
   }
   j += "]}";
   return j;
}

string HandleSymbolInfo(const string sym)
{
   if(!EnsureSymbolSelected(sym))
      return "{\"ok\":false,\"error_code\":4301,\"error\":\"Symbol not found\"}";

   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   if(point <= 0.0)
   {
      for(int k = 0; k < 10 && point <= 0.0; k++)
      {
         Sleep(50);
         digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
         point  = SymbolInfoDouble(sym, SYMBOL_POINT);
      }
   }
   if(point <= 0.0)
      return "{\"ok\":false,\"error_code\":4301,\"error\":\"Symbol metadata unavailable\"}";

   double bid      = SymbolInfoDouble(sym, SYMBOL_BID);
   double ask      = SymbolInfoDouble(sym, SYMBOL_ASK);
   double last_px  = SymbolInfoDouble(sym, SYMBOL_LAST);
   double vmin     = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax     = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   double vstep    = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   long   fmode    = SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
   long   tmode    = SymbolInfoInteger(sym, SYMBOL_TRADE_MODE);
   string desc     = SymbolInfoString(sym, SYMBOL_DESCRIPTION);
   string curr_p   = SymbolInfoString(sym, SYMBOL_CURRENCY_PROFIT);

   int out_digits = (digits > 0 ? digits : 2);
   string j = "{\"ok\":true,\"data\":{";
   j += "\"name\":\"" + JsonEscape(sym) + "\",";
   j += "\"description\":\"" + JsonEscape(desc) + "\",";
   j += "\"currency_profit\":\"" + JsonEscape(curr_p) + "\",";
   j += "\"digits\":" + IntegerToString(digits) + ",";
   j += "\"point\":" + JsonDouble(point, 8) + ",";
   j += "\"bid\":" + JsonDouble(bid, out_digits) + ",";
   j += "\"ask\":" + JsonDouble(ask, out_digits) + ",";
   j += "\"last\":" + JsonDouble(last_px, out_digits) + ",";
   j += "\"volume_min\":" + JsonDouble(vmin, 4) + ",";
   j += "\"volume_max\":" + JsonDouble(vmax, 4) + ",";
   j += "\"volume_step\":" + JsonDouble(vstep, 4) + ",";
   j += "\"filling_mode\":" + IntegerToString(fmode) + ",";
   j += "\"trade_mode\":" + IntegerToString(tmode);
   j += "}}";
   return j;
}

string HandleTick(const string sym)
{
   if(!EnsureSymbolSelected(sym))
      return "{\"ok\":false,\"error_code\":4301,\"error\":\"Symbol not found\"}";

   MqlTick tick;
   ZeroMemory(tick);
   bool got = SymbolInfoTick(sym, tick);
   for(int k = 0; k < 15 && (!got || (tick.bid <= 0.0 && tick.ask <= 0.0 && tick.last <= 0.0)); k++)
   {
      Sleep(50);
      got = SymbolInfoTick(sym, tick);
   }
   if(!got || (tick.bid <= 0.0 && tick.ask <= 0.0 && tick.last <= 0.0 && tick.time <= 0))
      return "{\"ok\":false,\"error_code\":4303,\"error\":\"Tick not found\"}";

   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   int out_digits = (digits > 0 ? digits : 2);

   string j = "{\"ok\":true,\"data\":{";
   j += "\"time\":" + IntegerToString((long)tick.time) + ",";
   j += "\"bid\":" + JsonDouble(tick.bid, out_digits) + ",";
   j += "\"ask\":" + JsonDouble(tick.ask, out_digits) + ",";
   j += "\"last\":" + JsonDouble(tick.last, out_digits) + ",";
   j += "\"volume\":" + IntegerToString((long)tick.volume) + ",";
   j += "\"time_msc\":" + IntegerToString((long)tick.time_msc) + ",";
   j += "\"flags\":" + IntegerToString((long)tick.flags) + ",";
   j += "\"volume_real\":" + JsonDouble(tick.volume_real, 4);
   j += "}}";
   return j;
}

string HandleRates(const string sym, const string tf_str, int count)
{
   if(!EnsureSymbolSelected(sym))
      return "{\"ok\":false,\"error_code\":4301,\"error\":\"Symbol not found\"}";

   if(count < 1) count = 1;
   if(count > 1000) count = 1000;
   ENUM_TIMEFRAMES tf = ResolveTimeframe(tf_str);

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   int copied = CopyRates(sym, tf, 0, count, rates);
   for(int k = 0; k < 20 && copied <= 0; k++)
   {
      Sleep(100);
      copied = CopyRates(sym, tf, 0, count, rates);
   }
   if(copied <= 0)
      return "{\"ok\":false,\"error_code\":4401,\"error\":\"Rates not found\"}";

   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   int out_digits = (digits > 0 ? digits : 2);

   string j = "{\"ok\":true,\"rates\":[";
   for(int i = 0; i < copied; i++)
   {
      if(i > 0) j += ",";
      j += "{";
      j += "\"time\":" + IntegerToString((long)rates[i].time) + ",";
      j += "\"open\":" + JsonDouble(rates[i].open, out_digits) + ",";
      j += "\"high\":" + JsonDouble(rates[i].high, out_digits) + ",";
      j += "\"low\":" + JsonDouble(rates[i].low, out_digits) + ",";
      j += "\"close\":" + JsonDouble(rates[i].close, out_digits) + ",";
      j += "\"tick_volume\":" + IntegerToString((long)rates[i].tick_volume) + ",";
      j += "\"spread\":" + IntegerToString((long)rates[i].spread) + ",";
      j += "\"real_volume\":" + IntegerToString((long)rates[i].real_volume);
      j += "}";
   }
   j += "]}";
   return j;
}

void PopulateTradeRequest(MqlTradeRequest &req, const string &keys[], const string &vals[], const int n)
{
   ZeroMemory(req);
   string sym = GetParam(keys, vals, n, "symbol", "");
   EnsureSymbolSelected(sym);

   req.action       = (ENUM_TRADE_REQUEST_ACTIONS)StringToInteger(GetParam(keys, vals, n, "action", "1"));
   req.magic        = (ulong)StringToInteger(GetParam(keys, vals, n, "magic", "234000"));
   req.order        = (ulong)StringToInteger(GetParam(keys, vals, n, "order", "0"));
   req.symbol       = sym;
   req.volume       = StringToDouble(GetParam(keys, vals, n, "volume", "0"));
   req.price        = StringToDouble(GetParam(keys, vals, n, "price", "0"));
   req.stoplimit    = StringToDouble(GetParam(keys, vals, n, "stoplimit", "0"));
   req.sl           = StringToDouble(GetParam(keys, vals, n, "sl", "0"));
   req.tp           = StringToDouble(GetParam(keys, vals, n, "tp", "0"));
   req.deviation    = (ulong)StringToInteger(GetParam(keys, vals, n, "deviation", "20"));
   req.type         = (ENUM_ORDER_TYPE)StringToInteger(GetParam(keys, vals, n, "type", "0"));
   req.type_filling = (ENUM_ORDER_TYPE_FILLING)StringToInteger(GetParam(keys, vals, n, "type_filling", "1"));
   req.type_time    = (ENUM_ORDER_TYPE_TIME)StringToInteger(GetParam(keys, vals, n, "type_time", "0"));
   req.expiration   = (datetime)StringToInteger(GetParam(keys, vals, n, "expiration", "0"));
   req.comment      = GetParam(keys, vals, n, "comment", "CortexIA");
   req.position     = (ulong)StringToInteger(GetParam(keys, vals, n, "position", "0"));
   req.position_by  = (ulong)StringToInteger(GetParam(keys, vals, n, "position_by", "0"));
}

string HandleOrderCheck(const string &keys[], const string &vals[], const int n)
{
   MqlTradeRequest req;
   MqlTradeCheckResult res;
   ZeroMemory(res);
   PopulateTradeRequest(req, keys, vals, n);

   ResetLastError();
   bool ok = OrderCheck(req, res);
   int err = GetLastError();
   if(!ok && res.retcode == 0)
      return "{\"ok\":false,\"error_code\":" + IntegerToString(err) + ",\"error\":\"OrderCheck failed\"}";

   string j = "{\"ok\":true,\"data\":{";
   j += "\"retcode\":" + IntegerToString((long)res.retcode) + ",";
   j += "\"balance\":" + JsonDouble(res.balance, 2) + ",";
   j += "\"equity\":" + JsonDouble(res.equity, 2) + ",";
   j += "\"profit\":" + JsonDouble(res.profit, 2) + ",";
   j += "\"margin\":" + JsonDouble(res.margin, 2) + ",";
   j += "\"margin_free\":" + JsonDouble(res.margin_free, 2) + ",";
   j += "\"margin_level\":" + JsonDouble(res.margin_level, 2) + ",";
   j += "\"comment\":\"" + JsonEscape(res.comment) + "\"";
   j += "}}";
   return j;
}

string HandleOrderSend(const string &keys[], const string &vals[], const int n)
{
   MqlTradeRequest req;
   MqlTradeResult res;
   ZeroMemory(res);
   PopulateTradeRequest(req, keys, vals, n);

   ResetLastError();
   bool ok = OrderSend(req, res);
   int err = GetLastError();
   if(!ok && res.retcode == 0)
      return "{\"ok\":false,\"error_code\":" + IntegerToString(err) + ",\"error\":\"OrderSend failed\"}";

   int digits = (int)SymbolInfoInteger(req.symbol, SYMBOL_DIGITS);
   int out_digits = (digits > 0 ? digits : 2);

   string j = "{\"ok\":true,\"data\":{";
   j += "\"retcode\":" + IntegerToString((long)res.retcode) + ",";
   j += "\"deal\":" + IntegerToString((long)res.deal) + ",";
   j += "\"order\":" + IntegerToString((long)res.order) + ",";
   j += "\"volume\":" + JsonDouble(res.volume, 4) + ",";
   j += "\"price\":" + JsonDouble(res.price, out_digits) + ",";
   j += "\"bid\":" + JsonDouble(res.bid, out_digits) + ",";
   j += "\"ask\":" + JsonDouble(res.ask, out_digits) + ",";
   j += "\"comment\":\"" + JsonEscape(res.comment) + "\",";
   j += "\"request_id\":" + IntegerToString((long)res.request_id) + ",";
   j += "\"retcode_external\":" + IntegerToString((long)res.retcode_external);
   j += "}}";
   return j;
}

//+------------------------------------------------------------------+
//| Processa um arquivo req_<id>.cmd e gera resp_<id>.json           |
//+------------------------------------------------------------------+
void ProcessRequestFile(const string base_name)
{
   string req_rel = g_rpc_folder + "\\" + base_name;
   int h = FileOpen(req_rel, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE, '\n', CP_UTF8);
   if(h == INVALID_HANDLE)
      return;

   string keys[];
   string vals[];
   int n = 0;

   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      StringTrimLeft(line);
      StringTrimRight(line);
      if(StringLen(line) == 0)
         continue;
      int eq = StringFind(line, "=");
      if(eq <= 0)
         continue;
      string k = StringSubstr(line, 0, eq);
      string v = StringSubstr(line, eq + 1);
      StringTrimLeft(k);
      StringTrimRight(k);
      StringTrimLeft(v);
      StringTrimRight(v);

      ArrayResize(keys, n + 1);
      ArrayResize(vals, n + 1);
      keys[n] = k;
      vals[n] = v;
      n++;
   }
   FileClose(h);
   FileDelete(req_rel, FILE_COMMON);

   // Extrai <id> de req_<id>.cmd
   string req_id = base_name;
   if(StringFind(req_id, "req_") == 0)
      req_id = StringSubstr(req_id, 4);
   int dot = StringFind(req_id, ".cmd");
   if(dot > 0)
      req_id = StringSubstr(req_id, 0, dot);

   string cmd = GetParam(keys, vals, n, "cmd", "");
   string sym = GetParam(keys, vals, n, "symbol", "");
   string resp = "";

   if(cmd == "heartbeat" || cmd == "account")
      resp = BuildAccountStatusJson();
   else if(cmd == "symbol_select")
   {
      bool sel = EnsureSymbolSelected(sym);
      resp = sel ? "{\"ok\":true}" : "{\"ok\":false,\"error_code\":4301,\"error\":\"Could not select symbol\"}";
   }
   else if(cmd == "symbol_info")
      resp = HandleSymbolInfo(sym);
   else if(cmd == "tick")
      resp = HandleTick(sym);
   else if(cmd == "rates")
   {
      string tf = GetParam(keys, vals, n, "timeframe", "D1");
      int cnt   = (int)StringToInteger(GetParam(keys, vals, n, "count", "120"));
      resp = HandleRates(sym, tf, cnt);
   }
   else if(cmd == "positions")
   {
      long flt_ticket = StringToInteger(GetParam(keys, vals, n, "ticket", "0"));
      resp = HandlePositions(flt_ticket, sym);
   }
   else if(cmd == "order_check")
      resp = HandleOrderCheck(keys, vals, n);
   else if(cmd == "order_send")
      resp = HandleOrderSend(keys, vals, n);
   else
      resp = "{\"ok\":false,\"error_code\":4000,\"error\":\"Unknown command\"}";

   string tmp_rel   = g_rpc_folder + "\\resp_" + req_id + ".tmp";
   string final_rel = g_rpc_folder + "\\resp_" + req_id + ".json";
   WriteAtomicJson(final_rel, tmp_rel, resp);
}

//+------------------------------------------------------------------+
//| Varre fila RPC a cada 25ms                                       |
//+------------------------------------------------------------------+
void PollRpcQueue()
{
   string found = "";
   string queue[];
   int qcount = 0;

   long sh = FileFindFirst(g_rpc_folder + "\\req_*.cmd", found, FILE_COMMON);
   if(sh != INVALID_HANDLE)
   {
      do
      {
         string bname = found;
         int sl = StringFind(bname, "\\");
         while(sl >= 0)
         {
            bname = StringSubstr(bname, sl + 1);
            sl = StringFind(bname, "\\");
         }
         int fsl = StringFind(bname, "/");
         while(fsl >= 0)
         {
            bname = StringSubstr(bname, fsl + 1);
            fsl = StringFind(bname, "/");
         }
         if(StringLen(bname) > 0 && StringFind(bname, "req_") == 0)
         {
            ArrayResize(queue, qcount + 1);
            queue[qcount] = bname;
            qcount++;
         }
      }
      while(FileFindNext(sh, found) && qcount < 64);
      FileFindClose(sh);
   }

   for(int i = 0; i < qcount; i++)
      ProcessRequestFile(queue[i]);
}

//+------------------------------------------------------------------+
//| Eventos principais do EA                                         |
//+------------------------------------------------------------------+
int OnInit()
{
   FolderCreate(g_rpc_folder, FILE_COMMON);
   EventSetMillisecondTimer(25);
   WriteHeartbeat();
   Print("[CortexBridgeEA] Iniciado em ", Symbol(),
         " | Connected=", TerminalInfoInteger(TERMINAL_CONNECTED),
         " | TerminalTradeAllowed=", TerminalInfoInteger(TERMINAL_TRADE_ALLOWED),
         " | ExpertTradeAllowed=", MQLInfoInteger(MQL_TRADE_ALLOWED));
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   PollRpcQueue();
   ulong now_ms = GetTickCount64();
   if(now_ms - g_last_hb_ms >= 1000)
      WriteHeartbeat();
}

void OnTick()
{
   PollRpcQueue();
}
//+------------------------------------------------------------------+
