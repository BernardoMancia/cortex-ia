(function () {
  "use strict";

  const App = window.CortexApp;
  let equityChartInstance = null;

  function setSignedClass(el, num) {
    if (!el) {
      return;
    }
    el.classList.remove("text-pos", "text-neg");
    if (num > 0.0001) {
      el.classList.add("text-pos");
    } else if (num < -0.0001) {
      el.classList.add("text-neg");
    }
  }

  function renderTopBarBadges(statusData) {
    if (!statusData) {
      return;
    }
    const brandVer = document.getElementById("brand-version");
    if (brandVer && statusData.version) {
      brandVer.textContent = "v" + statusData.version + " • B3";
    }

    const envBadge = document.getElementById("env-badge");
    if (envBadge) {
      const isSim = Boolean(statusData.is_simulation);
      envBadge.className = "badge " + (isSim ? "badge-sim" : "badge-prod");
      envBadge.textContent = isSim ? "HOMOLOGAÇÃO SIMULADA B3" : "PRODUÇÃO REAL B3";
    }

    const mktBadge = document.getElementById("market-badge");
    if (mktBadge) {
      const open = Boolean(statusData.market_open);
      mktBadge.className = "badge " + (open ? "badge-active" : "badge-neutral");
      mktBadge.textContent = open ? "PREGÃO B3 ABERTO" : "PREGÃO B3 FECHADO";
    }

    const modeBadge = document.getElementById("mode-badge");
    if (modeBadge) {
      const globalMode =
        statusData.global_state && statusData.global_state.global_mode
          ? String(statusData.global_state.global_mode).toUpperCase()
          : "ACTIVE";
      const walletEff =
        statusData.wallet_gate && statusData.wallet_gate.effective_mode
          ? String(statusData.wallet_gate.effective_mode).toUpperCase()
          : globalMode;

      if (globalMode === "PANIC" || walletEff === "PANIC") {
        modeBadge.className = "badge badge-panic";
        modeBadge.textContent = "MODO PÂNICO";
      } else if (globalMode === "MAINTENANCE") {
        modeBadge.className = "badge badge-maintenance";
        modeBadge.textContent = "MANUTENÇÃO PÓS-BOOT";
      } else if (walletEff.indexOf("OBSERVE") >= 0 || walletEff.indexOf("PAUSED") >= 0) {
        modeBadge.className = "badge badge-observe";
        modeBadge.textContent = "OBSERVAÇÃO";
      } else {
        modeBadge.className = "badge badge-active";
        modeBadge.textContent = "ATIVO";
      }
    }

    const simCard = document.getElementById("sim-capital-card");
    if (simCard) {
      if (statusData.is_simulation === true) {
        simCard.hidden = false;
        simCard.style.display = "block";
      } else {
        simCard.hidden = true;
        simCard.style.display = "none";
      }
    }
  }

  function renderOverview(statusData, tradesList) {
    if (!statusData) {
      return;
    }
    const port = statusData.portfolio || {};
    const stats = statusData.trade_stats || {};
    const tax = statusData.tax_summary || {};

    const opEqEl = document.getElementById("kpi-operational-equity");
    if (opEqEl) {
      opEqEl.textContent = App.formatBRL(statusData.operational_equity);
    }
    const totEqSub = document.getElementById("kpi-total-equity-sub");
    if (totEqSub) {
      totEqSub.textContent = "Patrimônio Total: " + App.formatBRL(statusData.total_equity);
    }

    const freeCashEl = document.getElementById("kpi-free-cash");
    if (freeCashEl) {
      freeCashEl.textContent = App.formatBRL(statusData.free_cash);
    }
    const bpSub = document.getElementById("kpi-buying-power-sub");
    if (bpSub) {
      bpSub.textContent = "Disponível p/ IA: " + App.formatBRL(statusData.buying_power);
    }

    const vaultEl = document.getElementById("kpi-vault-cash");
    if (vaultEl) {
      vaultEl.textContent = App.formatBRL(statusData.vault_reserve_cash);
    }

    const unrealEl = document.getElementById("kpi-unrealized-pnl");
    const unrealVal = Number(statusData.unrealized_pnl || 0);
    if (unrealEl) {
      unrealEl.textContent =
        App.formatBRL(unrealVal) + " (" + App.formatPct(port.pnl_percent || 0, 2, true) + ")";
      setSignedClass(unrealEl, unrealVal);
    }
    const realSub = document.getElementById("kpi-realized-pnl-sub");
    if (realSub) {
      realSub.textContent = "Realizado acumulado: " + App.formatBRL(stats.realized_pnl_total || 0);
    }

    const wrEl = document.getElementById("kpi-win-rate");
    if (wrEl) {
      wrEl.textContent = App.formatPct(stats.win_rate || 0, 1, false);
    }
    const trSub = document.getElementById("kpi-total-trades-sub");
    if (trSub) {
      trSub.textContent = String(stats.total_trades || 0) + " operações registradas";
    }

    const soldMonth = Number(tax.gross_swing_sales_brl || tax.month_gross_sales || 0);
    const limitBrl = Number(tax.exemption_limit_brl || 20000);
    const remExempt = Number(
      tax.remaining_exemption_brl !== undefined
        ? tax.remaining_exemption_brl
        : Math.max(0, limitBrl - soldMonth)
    );
    const pctUsed = limitBrl > 0 ? Math.min(100, Math.max(0, (soldMonth / limitBrl) * 100)) : 0;

    const taxSoldEl = document.getElementById("kpi-tax-sold");
    if (taxSoldEl) {
      taxSoldEl.textContent = App.formatBRL(soldMonth);
    }
    const taxBar = document.getElementById("tax-progress-fill");
    if (taxBar) {
      taxBar.style.width = pctUsed.toFixed(1) + "%";
      taxBar.classList.toggle("danger", pctUsed >= 90);
    }
    const taxRemSub = document.getElementById("kpi-tax-remaining-sub");
    if (taxRemSub) {
      taxRemSub.textContent = "Margem isenta restante: " + App.formatBRL(remExempt);
    }

    const activePill = document.getElementById("active-wallet-pill");
    if (activePill) {
      if (statusData.active_wallet) {
        activePill.textContent =
          statusData.active_wallet.name + " (" + statusData.active_wallet.connector_type + ")";
      } else {
        activePill.textContent = "Nenhuma carteira ativa";
      }
    }

    renderPositionsTable(statusData.positions || []);
    renderDecisionsTable(statusData.recent_decisions || []);
    renderTradesTable(tradesList || []);
  }

  function renderPositionsTable(positions) {
    const tbody = document.getElementById("positions-tbody");
    if (!tbody) {
      return;
    }
    if (!positions || positions.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="8" class="empty-state">Nenhuma posição aberta nesta carteira.</td></tr>';
      return;
    }
    tbody.innerHTML = positions
      .map(function (pos) {
        const pnl = Number(pos.pnl || 0);
        const pnlCls = pnl > 0 ? "text-pos" : pnl < 0 ? "text-neg" : "";
        const isProtected = Boolean(pos.do_not_touch);
        const btnLabel = isProtected ? "🔒 Protegido (Liberar)" : "🔓 Proteger Ativo";
        const btnCls = isProtected ? "btn btn-sm btn-amber" : "btn btn-sm";
        return (
          "<tr>" +
          "<td><strong>" + App.esc(pos.ticker) + "</strong></td>" +
          '<td class="text-mono">' + App.esc(pos.quantity) + "</td>" +
          '<td class="text-mono">' + App.formatBRL(pos.entry_price) + "</td>" +
          '<td class="text-mono">' + App.formatBRL(pos.current_price) + "</td>" +
          '<td class="text-mono">' + App.formatBRL(pos.stop_loss) + "</td>" +
          '<td class="text-mono">' + App.formatBRL(pos.market_value) + "</td>" +
          '<td class="text-mono ' + pnlCls + '">' +
          App.formatBRL(pnl) + " (" + App.formatPct(pos.pnl_percent || 0, 2, true) + ")" +
          "</td>" +
          "<td>" +
          '<button type="button" class="' + btnCls + '" data-protect-ticker="' +
          App.esc(pos.ticker) + '" data-protect-next="' + (isProtected ? "0" : "1") + '">' +
          btnLabel +
          "</button>" +
          "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderDecisionsTable(decisions) {
    const tbody = document.getElementById("overview-decisions-tbody");
    if (!tbody) {
      return;
    }
    if (!decisions || decisions.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="5" class="empty-state">Sem decisões recentes registradas.</td></tr>';
      return;
    }
    tbody.innerHTML = decisions
      .slice(0, 12)
      .map(function (dec) {
        const act = String(dec.action || "HOLD").toUpperCase();
        let badgeCls = "badge-neutral";
        if (act === "BUY") {
          badgeCls = "badge-active";
        } else if (act === "SELL") {
          badgeCls = "badge-prod";
        }
        const confPct = Number(dec.confidence || 0) <= 1
          ? Number(dec.confidence || 0) * 100
          : Number(dec.confidence || 0);
        return (
          "<tr>" +
          '<td class="text-mono">' + App.esc(App.formatDateTime(dec.timestamp)) + "</td>" +
          "<td><strong>" + App.esc(dec.ticker) + "</strong></td>" +
          '<td><span class="badge ' + badgeCls + '">' + App.esc(act) + "</span></td>" +
          '<td class="text-mono">' + App.formatPct(confPct, 0, false) + "</td>" +
          "<td>" + App.esc(dec.reasoning || "—") + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderTradesTable(trades) {
    const tbody = document.getElementById("overview-trades-tbody");
    if (!tbody) {
      return;
    }
    if (!trades || trades.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="6" class="empty-state">Nenhuma ordem executada até o momento.</td></tr>';
      return;
    }
    tbody.innerHTML = trades
      .slice(0, 12)
      .map(function (tr) {
        const side = String(tr.action || tr.side || "").toUpperCase();
        const sideCls = side === "BUY" ? "badge-active" : "badge-prod";
        const pnl = tr.pnl !== null && tr.pnl !== undefined ? Number(tr.pnl) : null;
        const pnlHtml =
          side === "SELL" && pnl !== null && Number.isFinite(pnl)
            ? '<span class="' + (pnl >= 0 ? "text-pos" : "text-neg") + '">' + App.formatBRL(pnl) + "</span>"
            : "—";
        return (
          "<tr>" +
          '<td class="text-mono">' + App.esc(App.formatDateTime(tr.timestamp)) + "</td>" +
          "<td><strong>" + App.esc(tr.ticker) + "</strong></td>" +
          '<td><span class="badge ' + sideCls + '">' + App.esc(side) + "</span></td>" +
          '<td class="text-mono">' + App.esc(tr.quantity) + "</td>" +
          '<td class="text-mono">' + App.formatBRL(tr.price) + "</td>" +
          '<td class="text-mono">' + pnlHtml + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderEquityChart(curvePoints, statusData) {
    const canvas = document.getElementById("equity-chart");
    if (!canvas || typeof window.Chart === "undefined") {
      return;
    }
    const pts = Array.isArray(curvePoints) ? curvePoints.slice() : [];
    const labels = [];
    const values = [];

    if (pts.length === 0 && statusData) {
      labels.push("Atual");
      values.push(Number(statusData.total_equity || 0));
    } else {
      pts.forEach(function (pt) {
        labels.push(String(pt.date || pt.snapshot_date || ""));
        values.push(Number(pt.equity !== undefined ? pt.equity : pt.total_equity || 0));
      });
    }

    if (equityChartInstance) {
      equityChartInstance.data.labels = labels;
      equityChartInstance.data.datasets[0].data = values;
      equityChartInstance.update("none");
      return;
    }

    const ctx = canvas.getContext("2d");
    equityChartInstance = new window.Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Patrimônio Total (R$)",
            data: values,
            borderColor: "#38bdf8",
            backgroundColor: "rgba(56, 189, 248, 0.14)",
            borderWidth: 2,
            pointRadius: 3,
            tension: 0.25,
            fill: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            labels: { color: "#cbd5e1" },
          },
        },
        scales: {
          x: {
            ticks: { color: "#94a3b8" },
            grid: { color: "rgba(148, 163, 184, 0.08)" },
          },
          y: {
            ticks: {
              color: "#94a3b8",
              callback: function (v) {
                return App.formatBRL(v);
              },
            },
            grid: { color: "rgba(148, 163, 184, 0.08)" },
          },
        },
      },
    });
  }

  function renderMarketTab(statusData) {
    if (!statusData) {
      return;
    }
    const sessEl = document.getElementById("mkt-session-state");
    if (sessEl) {
      sessEl.textContent = statusData.market_open ? "ABERTO (10:00–17:55 BRT)" : "FECHADO";
    }
    const coolSub = document.getElementById("mkt-cooldown-sub");
    if (coolSub) {
      coolSub.textContent = statusData.opening_cooldown_active
        ? "Cooldown inicial ativo (aguardando estabilização de leilão)"
        : "Janela operacional liberada";
    }

    const bovaEl = document.getElementById("mkt-bova11-pct");
    const bovaPct = Number(statusData.bova11_change_pct || 0);
    if (bovaEl) {
      bovaEl.textContent = App.formatPct(bovaPct, 2, true);
      setSignedClass(bovaEl, bovaPct);
    }
    const regSub = document.getElementById("mkt-regime-sub");
    if (regSub) {
      regSub.textContent = statusData.regime_bearish_veto
        ? "VETO BEARISH ATIVO: novas compras bloqueadas"
        : "Regime normal: compras permitidas pelos filtros";
    }

    const health = statusData.health || {};
    const healthEl = document.getElementById("mkt-health-cpu");
    if (healthEl) {
      healthEl.textContent =
        "CPU " + Number(health.cpu_percent || 0).toFixed(0) + "% • RAM " +
        Number(health.ram_percent || 0).toFixed(0) + "%";
    }
    const cycSub = document.getElementById("mkt-cycle-sub");
    if (cycSub) {
      const em = statusData.engine_metrics;
      cycSub.textContent = em && em.cycles_completed !== undefined
        ? "Ciclos concluídos: " + em.cycles_completed + " • Último: " + (em.last_cycle_duration_ms || 0) + " ms"
        : "Market Intelligence Hub pronto";
    }

    filterAndRenderRadar();
    renderHeadlines(statusData.headlines || []);
  }

  function filterAndRenderRadar() {
    const tbody = document.getElementById("market-radar-tbody");
    if (!tbody) {
      return;
    }
    const statusData = App.state.status;
    const rows = statusData && Array.isArray(statusData.market_radar) ? statusData.market_radar : [];
    const searchInput = document.getElementById("market-search-input");
    const q = searchInput ? String(searchInput.value || "").trim().toUpperCase() : "";

    const sentMap = {};
    if (statusData && Array.isArray(statusData.sentiments)) {
      statusData.sentiments.forEach(function (s) {
        if (s && s.ticker) {
          sentMap[String(s.ticker).toUpperCase()] = s;
        }
      });
    }

    const filtered = rows.filter(function (r) {
      if (!q) {
        return true;
      }
      return String(r.ticker || "").toUpperCase().indexOf(q) >= 0;
    });

    if (filtered.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="9" class="empty-state">Nenhum ativo correspondente encontrado no radar.</td></tr>';
      return;
    }

    tbody.innerHTML = filtered
      .map(function (r) {
        const tk = String(r.ticker || "");
        const chg = Number(r.change_pct || 0);
        const chgCls = chg > 0 ? "text-pos" : chg < 0 ? "text-neg" : "";
        const sig = String(r.trend_signal || r.signal || "NEUTRAL");
        let sigBadge = "badge-neutral";
        if (sig.indexOf("BUY") >= 0) {
          sigBadge = "badge-active";
        } else if (sig.indexOf("SELL") >= 0) {
          sigBadge = "badge-prod";
        }
        const sent = sentMap[tk.toUpperCase()];
        const sentLabel = sent
          ? sent.label + " (" + Number(sent.score || 0).toFixed(2) + ")"
          : "NEUTRO";
        return (
          "<tr>" +
          "<td><strong>" + App.esc(tk) + "</strong></td>" +
          '<td class="text-mono">' + App.formatBRL(r.price) + "</td>" +
          '<td class="text-mono ' + chgCls + '">' + App.formatPct(chg, 2, true) + "</td>" +
          '<td class="text-mono">' + Number(r.rsi || 50).toFixed(1) + "</td>" +
          '<td class="text-mono">' + Number(r.macd_hist || 0).toFixed(3) + "</td>" +
          '<td class="text-mono">' + Number(r.adx || 0).toFixed(1) + "</td>" +
          '<td><span class="badge ' + sigBadge + '">' + App.esc(sig) + "</span></td>" +
          "<td>" + App.esc(sentLabel) + "</td>" +
          '<td class="text-mono">' + App.esc(App.formatDateTime(r.updated_at || r.timestamp)) + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderHeadlines(headlines) {
    const tbody = document.getElementById("market-headlines-tbody");
    if (!tbody) {
      return;
    }
    if (!headlines || headlines.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="4" class="empty-state">Nenhuma manchete coletada recentemente.</td></tr>';
      return;
    }
    tbody.innerHTML = headlines
      .slice(0, 20)
      .map(function (item) {
        const score = Number(item.sentiment_score || item.score || 0);
        const cls = score > 0.1 ? "text-pos" : score < -0.1 ? "text-neg" : "";
        return (
          "<tr>" +
          '<td class="text-mono">' + App.esc(App.formatDateTime(item.published_at || item.fetched_at)) + "</td>" +
          "<td><strong>" + App.esc(item.ticker || item.source || "MERCADO") + "</strong></td>" +
          "<td>" + App.esc(item.title || item.headline || "") + "</td>" +
          '<td class="text-mono ' + cls + '">' + score.toFixed(2) + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }

  function renderCapitalTab(capResp) {
    const cap = capResp && capResp.capital ? capResp.capital : capResp;
    if (!cap) {
      return;
    }
    const freeEl = document.getElementById("cap-free-cash");
    if (freeEl) {
      freeEl.textContent = App.formatBRL(cap.free_cash);
    }
    const connSub = document.getElementById("cap-broker-conn-sub");
    if (connSub) {
      const connLabel =
        cap.broker_connected === true
          ? "Online e conciliada"
          : cap.broker_connected === false
          ? "Desconectada"
          : App.esc(cap.connector_type || "Sob demanda");
      connSub.textContent = "Corretora: " + connLabel;
    }

    const ceilEl = document.getElementById("cap-ai-ceiling");
    if (ceilEl) {
      ceilEl.textContent = App.formatBRL(cap.ai_allocated_capital);
    }
    const availSub = document.getElementById("cap-available-ai-sub");
    if (availSub) {
      availSub.textContent = "Poder de compra IA: " + App.formatBRL(cap.buying_power);
    }

    const vaultEl = document.getElementById("cap-vault-cash");
    if (vaultEl) {
      vaultEl.textContent = App.formatBRL(cap.vault_reserve_cash);
    }

    const settledEl = document.getElementById("cap-settled-cash");
    if (settledEl) {
      settledEl.textContent = App.formatBRL(cap.settled_withdrawable_cash);
    }
    const d2Sub = document.getElementById("cap-d2-pending-sub");
    if (d2Sub) {
      const nextStr = cap.next_d2_settlement_date ? " (próx: " + cap.next_d2_settlement_date + ")" : "";
      d2Sub.textContent = "Pendente D+2: " + App.formatBRL(cap.unsettled_d2_cash) + nextStr;
    }

    const ceilInput = document.getElementById("input-ai-ceiling");
    if (ceilInput && document.activeElement !== ceilInput) {
      ceilInput.value = Number(cap.ai_allocated_capital || 0).toFixed(2);
    }

    const goalBox = document.getElementById("active-withdraw-goal-box");
    const goalTxt = document.getElementById("active-withdraw-goal-text");
    if (goalBox && goalTxt) {
      if (cap.active_goal) {
        goalBox.hidden = false;
        goalTxt.textContent =
          "Meta agendada no lucro: resgatar " +
          App.formatBRL(cap.active_goal.amount) +
          " ao atingir patrimônio de " +
          App.formatBRL(cap.active_goal.target_equity) + ".";
      } else {
        goalBox.hidden = true;
      }
    }

    const d2Body = document.getElementById("d2-schedule-tbody");
    if (d2Body) {
      const sched = Array.isArray(cap.d2_schedule) ? cap.d2_schedule : [];
      if (sched.length === 0) {
        d2Body.innerHTML =
          '<tr><td colspan="3" class="empty-state">Sem liquidações D+2 pendentes.</td></tr>';
      } else {
        d2Body.innerHTML = sched
          .map(function (row) {
            return (
              "<tr>" +
              '<td class="text-mono">' + App.esc(row.settlement_date) + "</td>" +
              '<td class="text-mono">' + App.formatBRL(row.net_sell_amount) + "</td>" +
              '<td><span class="badge badge-maintenance">' + App.esc(row.status || "PENDENTE D+2") + "</span></td>" +
              "</tr>"
            );
          })
          .join("");
      }
    }

    const txBody = document.getElementById("capital-tx-tbody");
    if (txBody) {
      const txs = Array.isArray(cap.transactions) ? cap.transactions : [];
      if (txs.length === 0) {
        txBody.innerHTML =
          '<tr><td colspan="4" class="empty-state">Sem movimentações registradas.</td></tr>';
      } else {
        txBody.innerHTML = txs
          .slice(0, 20)
          .map(function (tx) {
            return (
              "<tr>" +
              '<td class="text-mono">' + App.esc(App.formatDateTime(tx.created_at || tx.timestamp)) + "</td>" +
              '<td><span class="badge badge-neutral">' + App.esc(tx.tx_type) + "</span></td>" +
              '<td class="text-mono">' + App.formatBRL(tx.amount) + "</td>" +
              "<td>" + App.esc(tx.details || "") + "</td>" +
              "</tr>"
            );
          })
          .join("");
      }
    }
  }

  function renderRiskSettings(riskResp) {
    const cfg = riskResp && riskResp.settings ? riskResp.settings : riskResp;
    if (!cfg) {
      return;
    }
    const sl = document.getElementById("risk-stop-loss");
    const tt = document.getElementById("risk-trailing-trigger");
    const td = document.getElementById("risk-trailing-distance");
    const tg = document.getElementById("risk-telegram-chat");
    const p20 = document.getElementById("risk-protect-20k");
    const adt = document.getElementById("risk-avoid-daytrade");

    if (sl) sl.value = (Number(cfg.stop_loss_pct || 0.1) * 100).toFixed(1);
    if (tt) tt.value = (Number(cfg.trailing_trigger_pct || 0.02) * 100).toFixed(1);
    if (td) td.value = (Number(cfg.trailing_distance_pct || 0.015) * 100).toFixed(1);
    if (tg) tg.value = String(cfg.telegram_chat_id || "");
    if (p20) p20.checked = Boolean(cfg.protect_20k_exemption);
    if (adt) adt.checked = Boolean(cfg.avoid_day_trade_tax);

    const fwConc = document.getElementById("fw-max-conc");
    const fwSec = document.getElementById("fw-max-sector");
    const fwDaily = document.getElementById("fw-max-daily");
    const fwTrade = document.getElementById("fw-max-trade-risk");
    const fwEx = document.getElementById("fw-exemption");

    if (fwConc && cfg.max_concentration !== undefined) {
      fwConc.textContent = App.formatPct(Number(cfg.max_concentration) * 100, 0, false);
    }
    if (fwSec && cfg.max_sector_exposure !== undefined) {
      fwSec.textContent = App.formatPct(Number(cfg.max_sector_exposure) * 100, 0, false);
    }
    if (fwDaily && cfg.max_daily_loss_percent !== undefined) {
      fwDaily.textContent = App.formatPct(Number(cfg.max_daily_loss_percent) * 100, 1, false);
    }
    if (fwTrade && cfg.max_risk_per_trade_percent !== undefined) {
      fwTrade.textContent = App.formatPct(Number(cfg.max_risk_per_trade_percent) * 100, 1, false);
    }
    if (fwEx && cfg.b3_swing_exemption_limit !== undefined) {
      fwEx.textContent = App.formatBRL(cfg.b3_swing_exemption_limit);
    }
  }

  function renderGuide(guidePayload) {
    if (!guidePayload) {
      return;
    }
    const titleEl = document.getElementById("guide-main-title");
    const subEl = document.getElementById("guide-main-subtitle");
    if (titleEl && guidePayload.title) {
      titleEl.textContent = guidePayload.title;
    }
    if (subEl && guidePayload.subtitle) {
      subEl.textContent = guidePayload.subtitle;
    }
    filterGuideItems();
  }

  function filterGuideItems() {
    const guide = App.state.guide;
    if (!guide) {
      return;
    }
    const qInput = document.getElementById("guide-search-input");
    const q = qInput ? String(qInput.value || "").trim().toLowerCase() : "";

    const secWrap = document.getElementById("guide-sections-list");
    if (secWrap) {
      const sections = Array.isArray(guide.sections) ? guide.sections : [];
      const matchingSecs = sections.filter(function (s) {
        if (!q) return true;
        const blob = (
          String(s.title || "") + " " +
          String(s.summary || "") + " " +
          String(s.content || "") + " " +
          (Array.isArray(s.bullets) ? s.bullets.join(" ") : "")
        ).toLowerCase();
        return blob.indexOf(q) >= 0;
      });

      secWrap.innerHTML = matchingSecs
        .map(function (sec) {
          const bulletsHtml = Array.isArray(sec.bullets) && sec.bullets.length > 0
            ? "<ul>" + sec.bullets.map(function (b) { return "<li>" + App.esc(b) + "</li>"; }).join("") + "</ul>"
            : "";
          return (
            '<article class="connector-card">' +
            "<div>" +
            '<span class="badge badge-neutral" style="margin-bottom:8px">' + App.esc(sec.category || "B3") + "</span>" +
            '<h3 class="panel-title">' + App.esc(sec.title) + "</h3>" +
            '<p class="panel-subtitle" style="margin:6px 0">' + App.esc(sec.summary || "") + "</p>" +
            '<p style="font-size:13px;margin:8px 0">' + App.esc(sec.content || "") + "</p>" +
            bulletsHtml +
            "</div>" +
            "</article>"
          );
        })
        .join("");
    }

    const glossWrap = document.getElementById("guide-glossary-list");
    if (glossWrap) {
      const terms = Array.isArray(guide.glossary) ? guide.glossary : [];
      const matchingTerms = terms.filter(function (t) {
        if (!q) return true;
        return (
          String(t.term || "").toLowerCase().indexOf(q) >= 0 ||
          String(t.definition || "").toLowerCase().indexOf(q) >= 0
        );
      });
      glossWrap.innerHTML = matchingTerms
        .map(function (g) {
          return (
            '<div class="connector-card">' +
            "<div>" +
            "<strong>" + App.esc(g.term) + "</strong>" +
            '<p class="panel-subtitle" style="margin-top:6px">' + App.esc(g.definition) + "</p>" +
            "</div>" +
            "</div>"
          );
        })
        .join("");
    }
  }

  function bindCockpitEvents() {
    const posBody = document.getElementById("positions-tbody");
    if (posBody) {
      posBody.addEventListener("click", async function (ev) {
        const btn = ev.target.closest("[data-protect-ticker]");
        if (!btn) return;
        const ticker = btn.getAttribute("data-protect-ticker");
        const desired = btn.getAttribute("data-protect-next") === "1";
        btn.disabled = true;
        try {
          await App.apiFetch("/api/positions/" + encodeURIComponent(ticker) + "/protect", {
            method: "POST",
            body: { do_not_touch: desired },
          });
          App.toast(
            desired
              ? ticker + " protegido contra vendas automáticas."
              : ticker + " liberado para gestão da IA.",
            "success"
          );
          if (typeof App.refreshAll === "function") {
            await App.refreshAll();
          }
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        } finally {
          btn.disabled = false;
        }
      });
    }

    const btnRefresh = document.getElementById("btn-refresh-overview");
    if (btnRefresh) {
      btnRefresh.addEventListener("click", async function () {
        if (typeof App.refreshAll === "function") {
          await App.refreshAll();
          App.toast("Cockpit atualizado.", "success");
        }
      });
    }

    const mktSearch = document.getElementById("market-search-input");
    if (mktSearch) {
      mktSearch.addEventListener("input", filterAndRenderRadar);
    }

    const guideSearch = document.getElementById("guide-search-input");
    if (guideSearch) {
      guideSearch.addEventListener("input", filterGuideItems);
    }

    const btnSync = document.getElementById("btn-sync-broker");
    if (btnSync) {
      btnSync.addEventListener("click", async function () {
        btnSync.disabled = true;
        try {
          const res = await App.apiFetch("/api/capital/sync-broker", {
            method: "POST",
            body: {},
          });
          App.state.capital = res.capital;
          renderCapitalTab(res);
          if (res.sync && res.sync.connected) {
            App.toast("Saldo e custódia sincronizados com a corretora.", "success");
          } else {
            App.toast(
              (res.sync && res.sync.message) || "Corretora indisponível para sincronização no momento.",
              "warn"
            );
          }
          if (typeof App.refreshAll === "function") {
            await App.refreshAll();
          }
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        } finally {
          btnSync.disabled = false;
        }
      });
    }

    const formCeiling = document.getElementById("form-ai-ceiling");
    if (formCeiling) {
      formCeiling.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const val = Number(document.getElementById("input-ai-ceiling").value);
        try {
          const res = await App.apiFetch("/api/capital/ai-ceiling", {
            method: "POST",
            requireStepUp: true,
            body: { ceiling_brl: val },
          });
          App.state.capital = res.capital;
          renderCapitalTab(res);
          App.toast("Teto operacional da IA atualizado com sucesso.", "success");
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const formVaultDep = document.getElementById("form-vault-deposit");
    if (formVaultDep) {
      formVaultDep.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const inp = document.getElementById("input-vault-deposit");
        const val = Number(inp.value);
        try {
          const res = await App.apiFetch("/api/capital/vault/deposit", {
            method: "POST",
            body: { amount: val },
          });
          inp.value = "";
          App.state.capital = res.capital;
          renderCapitalTab(res);
          App.toast("Valor blindado no Cofre de Reserva.", "success");
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const formVaultRel = document.getElementById("form-vault-release");
    if (formVaultRel) {
      formVaultRel.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const inp = document.getElementById("input-vault-release");
        const val = Number(inp.value);
        try {
          const res = await App.apiFetch("/api/capital/vault/release", {
            method: "POST",
            requireStepUp: true,
            body: { amount: val },
          });
          inp.value = "";
          App.state.capital = res.capital;
          renderCapitalTab(res);
          App.toast("Valor liberado do Cofre para Caixa Livre.", "success");
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const formWithdraw = document.getElementById("form-withdraw");
    if (formWithdraw) {
      formWithdraw.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const inp = document.getElementById("input-withdraw-amount");
        const mode = document.getElementById("select-withdraw-mode").value;
        const val = Number(inp.value);
        try {
          const res = await App.apiFetch("/api/capital/withdraw", {
            method: "POST",
            requireStepUp: true,
            body: { amount: val, mode: mode },
          });
          inp.value = "";
          App.state.capital = res.capital;
          renderCapitalTab(res);
          App.toast(
            mode === "on_profit"
              ? "Meta de resgate agendada no lucro."
              : "Liquidação de resgate concluída e isolada no Cofre.",
            "success"
          );
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const btnCancelGoal = document.getElementById("btn-cancel-withdraw-goal");
    if (btnCancelGoal) {
      btnCancelGoal.addEventListener("click", async function () {
        try {
          const res = await App.apiFetch("/api/capital/withdraw/cancel", {
            method: "POST",
            body: {},
          });
          App.state.capital = res.capital;
          renderCapitalTab(res);
          App.toast("Meta de resgate cancelada.", "success");
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const formRisk = document.getElementById("form-risk-settings");
    if (formRisk) {
      formRisk.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const sl = Number(document.getElementById("risk-stop-loss").value) / 100;
        const tt = Number(document.getElementById("risk-trailing-trigger").value) / 100;
        const td = Number(document.getElementById("risk-trailing-distance").value) / 100;
        const tg = document.getElementById("risk-telegram-chat").value.trim();
        const p20 = document.getElementById("risk-protect-20k").checked;
        const adt = document.getElementById("risk-avoid-daytrade").checked;
        try {
          const res = await App.apiFetch("/api/risk/settings", {
            method: "POST",
            body: {
              stop_loss_pct: sl,
              trailing_trigger_pct: tt,
              trailing_distance_pct: td,
              telegram_chat_id: tg,
              protect_20k_exemption: p20,
              avoid_day_trade_tax: adt,
            },
          });
          App.state.risk = res;
          renderRiskSettings(res);
          App.toast("Parâmetros de risco e tributação salvos.", "success");
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }
  }

  window.CortexCockpitUI = {
    init: bindCockpitEvents,
    renderTopBarBadges: renderTopBarBadges,
    renderOverview: renderOverview,
    renderEquityChart: renderEquityChart,
    renderMarketTab: renderMarketTab,
    renderCapitalTab: renderCapitalTab,
    renderRiskSettings: renderRiskSettings,
    renderGuide: renderGuide,
  };
})();
