(function () {
  "use strict";

  const App = window.CortexApp;
  let latestGeneratedToken = "";

  function formatDecimal(val, digits) {
    const d = typeof digits === "number" ? digits : 2;
    const num = Number(val);
    const safe = Number.isFinite(num) ? num : 0;
    return safe.toFixed(d).replace(".", ",");
  }

  function setSignedTone(el, num) {
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

  function syncServerBaseUrl() {
    const baseEl = document.getElementById("bi-server-base-url");
    if (baseEl && window.location && window.location.origin) {
      baseEl.textContent = String(window.location.origin);
    }
  }

  function syncLukeAdminOption() {
    const isLuke = Boolean(
      App.state.me && App.state.me.user && App.state.me.user.is_owner_admin === true
    );
    const adminRow = document.getElementById("bi-key-admin-row");
    const adminCheck = document.getElementById("bi-key-include-admin");
    if (!adminRow) {
      return;
    }
    if (isLuke) {
      adminRow.hidden = false;
      adminRow.style.display = "flex";
    } else {
      adminRow.hidden = true;
      adminRow.style.display = "none";
      if (adminCheck) {
        adminCheck.checked = false;
      }
    }
  }

  function syncWalletOptions(wallets) {
    const sel = document.getElementById("bi-key-wallet-scope");
    if (!sel) {
      return;
    }
    const prevValue = sel.value || "";
    const list = Array.isArray(wallets) ? wallets : [];
    const optionsHtml = ['<option value="">Todas as minhas carteiras</option>'];
    list.forEach(function (w) {
      const wid = String(w.id);
      const label = App.esc(w.name || ("Carteira #" + wid));
      optionsHtml.push('<option value="' + App.esc(wid) + '">' + label + " (#" + App.esc(wid) + ")</option>");
    });
    sel.innerHTML = optionsHtml.join("");
    const stillExists = list.some(function (w) {
      return String(w.id) === prevValue;
    });
    sel.value = stillExists ? prevValue : "";
  }

  function renderBiKpis(kpiData) {
    const kpi = (kpiData && kpiData.kpi_summary) ? kpiData.kpi_summary : (kpiData || {});

    const sharpeEl = document.getElementById("bi-kpi-sharpe");
    const volSub = document.getElementById("bi-kpi-vol-sub");
    const sharpeVal = Number(kpi.sharpe_annualized) || 0;
    if (sharpeEl) {
      sharpeEl.textContent = formatDecimal(sharpeVal, 2);
      setSignedTone(sharpeEl, sharpeVal);
    }
    if (volSub) {
      volSub.textContent = "Volatilidade anual: " + App.formatPct(kpi.volatility_annualized_pct || 0, 2, false);
    }

    const sortinoEl = document.getElementById("bi-kpi-sortino");
    const calmarSub = document.getElementById("bi-kpi-calmar-sub");
    const sortinoVal = Number(kpi.sortino_annualized) || 0;
    if (sortinoEl) {
      sortinoEl.textContent = formatDecimal(sortinoVal, 2);
      setSignedTone(sortinoEl, sortinoVal);
    }
    if (calmarSub) {
      calmarSub.textContent = "Calmar Ratio: " + formatDecimal(kpi.calmar_ratio || 0, 2);
    }

    const maxDdEl = document.getElementById("bi-kpi-max-dd");
    const curDdSub = document.getElementById("bi-kpi-cur-dd-sub");
    const maxDdVal = Number(kpi.max_drawdown_pct) || 0;
    if (maxDdEl) {
      maxDdEl.textContent = App.formatPct(maxDdVal, 2, false);
      setSignedTone(maxDdEl, maxDdVal);
    }
    if (curDdSub) {
      curDdSub.textContent = "Drawdown atual: " + App.formatPct(kpi.current_drawdown_pct || 0, 2, false);
    }

    const pfEl = document.getElementById("bi-kpi-profit-factor");
    const wrSub = document.getElementById("bi-kpi-winrate-sub");
    const pfVal = Number(kpi.profit_factor) || 0;
    if (pfEl) {
      pfEl.textContent = formatDecimal(pfVal, 2);
      setSignedTone(pfEl, pfVal > 0 ? pfVal - 1 : 0);
    }
    if (wrSub) {
      wrSub.textContent =
        "Win Rate: " +
        App.formatPct(kpi.win_rate_pct || 0, 1, false) +
        " (" +
        String(kpi.sell_trades || 0) +
        " vendas)";
    }

    const payoffEl = document.getElementById("bi-kpi-payoff");
    const avgSub = document.getElementById("bi-kpi-avgwinloss-sub");
    const payoffVal = Number(kpi.payoff_ratio) || 0;
    if (payoffEl) {
      payoffEl.textContent = formatDecimal(payoffVal, 2);
      setSignedTone(payoffEl, payoffVal > 0 ? payoffVal - 1 : 0);
    }
    if (avgSub) {
      avgSub.textContent =
        "Ganho " +
        App.formatBRL(kpi.avg_win_brl || 0) +
        " / Perda " +
        App.formatBRL(kpi.avg_loss_brl || 0);
    }

    const expEl = document.getElementById("bi-kpi-expectancy");
    const posDaysSub = document.getElementById("bi-kpi-posdays-sub");
    const expVal = Number(kpi.expectancy_brl) || 0;
    if (expEl) {
      expEl.textContent = App.formatBRL(expVal);
      setSignedTone(expEl, expVal);
    }
    if (posDaysSub) {
      posDaysSub.textContent = "Pregões positivos: " + App.formatPct(kpi.positive_days_pct || 0, 1, false);
    }
  }

  function renderBiKeysTable(keys) {
    const tbody = document.getElementById("bi-keys-tbody");
    if (!tbody) {
      return;
    }
    const list = Array.isArray(keys) ? keys : [];
    if (list.length === 0) {
      tbody.innerHTML =
        '<tr><td colspan="7" class="empty-state">Nenhum token Power BI emitido até o momento.</td></tr>';
      return;
    }

    const rowsHtml = list.map(function (item) {
      const scopeText = item.wallet_id
        ? App.esc((item.wallet_name || "Carteira") + " (#" + String(item.wallet_id) + ")")
        : "Todas as carteiras";
      const adminTag = item.include_admin
        ? ' <span class="badge badge-sim">ADMIN META</span>'
        : "";
      const cidrText = item.allowed_cidr ? App.esc(item.allowed_cidr) : "Qualquer IP";

      let statusBadge = "";
      if (item.revoked_at) {
        statusBadge = '<span class="badge badge-panic">REVOGADO</span>';
      } else if (item.is_expired) {
        statusBadge = '<span class="badge badge-observe">EXPIRADO</span>';
      } else if (item.is_active) {
        statusBadge = '<span class="badge badge-active">ATIVO</span>';
      } else {
        statusBadge = '<span class="badge badge-neutral">INATIVO</span>';
      }

      const expInfo = item.expires_at
        ? "Expira: " + App.esc(App.formatDateTime(item.expires_at))
        : "Sem expiração";

      const lastUsedInfo = item.last_used_at
        ? App.esc(App.formatDateTime(item.last_used_at)) +
          (item.last_used_ip ? ' <span class="text-mono">(' + App.esc(item.last_used_ip) + ")</span>" : "")
        : "Nunca utilizado";

      const actionHtml = item.is_active
        ? '<button type="button" class="btn btn-sm btn-amber" data-revoke-bi-key="' +
          App.esc(String(item.id)) +
          '">Revogar</button>'
        : '<span class="text-muted">—</span>';

      return (
        "<tr>" +
        "<td><strong>" + App.esc(item.label) + "</strong>" + adminTag + "</td>" +
        "<td>" + scopeText + "</td>" +
        '<td class="text-mono">' + App.esc(item.token_prefix) + "...</td>" +
        '<td class="text-mono">' + cidrText + "</td>" +
        "<td>" + statusBadge + '<div class="kpi-sub">' + expInfo + "</div></td>" +
        "<td>" + lastUsedInfo + "</td>" +
        "<td>" + actionHtml + "</td>" +
        "</tr>"
      );
    });

    tbody.innerHTML = rowsHtml.join("");
  }

  async function loadBiKpis() {
    const walletSel = document.getElementById("wallet-select");
    const activeWid = walletSel && walletSel.value ? String(walletSel.value).trim() : "";
    const query = activeWid ? "?wallet_id=" + encodeURIComponent(activeWid) : "";
    const kpiRes = await App.apiFetch("/api/bi/v1/kpi_summary" + query);
    renderBiKpis(kpiRes);
  }

  async function loadBiKeys() {
    const res = await App.apiFetch("/api/bi/keys");
    renderBiKeysTable(res.keys || []);
  }

  async function onBiTabOpened() {
    syncServerBaseUrl();
    syncLukeAdminOption();
    syncWalletOptions(App.state.wallets || []);
    try {
      await Promise.all([loadBiKpis(), loadBiKeys()]);
    } catch (err) {
      App.toast(err.message || "Falha ao carregar dados do Power BI.", "error");
    }
  }

  function triggerPbidsDownload() {
    const anchor = document.createElement("a");
    anchor.href = "/api/bi/download/pbids";
    anchor.setAttribute("download", "");
    anchor.style.display = "none";
    document.body.appendChild(anchor);
    anchor.click();
    window.setTimeout(function () {
      if (anchor.parentNode) {
        anchor.parentNode.removeChild(anchor);
      }
    }, 500);
  }

  async function copyGeneratedToken() {
    if (!latestGeneratedToken) {
      return;
    }
    try {
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        await navigator.clipboard.writeText(latestGeneratedToken);
        App.toast("Token Bearer Power BI copiado para a área de transferência.", "success");
        return;
      }
    } catch (_err) {
      latestGeneratedToken = String(latestGeneratedToken);
    }

    const tempInput = document.createElement("textarea");
    tempInput.value = latestGeneratedToken;
    document.body.appendChild(tempInput);
    tempInput.select();
    document.execCommand("copy");
    document.body.removeChild(tempInput);
    App.toast("Token Bearer Power BI copiado.", "success");
  }

  function init() {
    syncServerBaseUrl();

    const formCreate = document.getElementById("form-create-bi-key");
    if (formCreate) {
      formCreate.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const labelInput = document.getElementById("bi-key-label");
        const scopeSelect = document.getElementById("bi-key-wallet-scope");
        const expirySelect = document.getElementById("bi-key-expiry");
        const cidrInput = document.getElementById("bi-key-cidr");
        const adminCheck = document.getElementById("bi-key-include-admin");

        const label = labelInput ? String(labelInput.value || "").trim() : "";
        const rawWid = scopeSelect ? String(scopeSelect.value || "").trim() : "";
        const expDays = expirySelect ? Number(expirySelect.value || 90) : 90;
        const cidr = cidrInput ? String(cidrInput.value || "").trim() : "";
        const includeAdmin = Boolean(adminCheck && adminCheck.checked);

        const body = {
          label: label,
          wallet_id: rawWid ? Number(rawWid) : null,
          expires_in_days: expDays > 0 ? expDays : 0,
          allowed_cidr: cidr,
          include_admin: includeAdmin,
        };

        try {
          const res = await App.apiFetch("/api/bi/keys", {
            method: "POST",
            requireStepUp: true,
            body: body,
          });
          latestGeneratedToken = String(res.raw_token || "");
          const tokenBox = document.getElementById("bi-generated-token-box");
          const tokenVal = document.getElementById("bi-generated-token-value");
          if (tokenVal) {
            tokenVal.textContent = latestGeneratedToken;
          }
          if (tokenBox) {
            tokenBox.hidden = false;
          }
          if (labelInput) {
            labelInput.value = "";
          }
          App.toast("Token Power BI somente-leitura emitido com sucesso.", "success");
          await loadBiKeys();
        } catch (err) {
          if (!err.cancelled) {
            App.toast(err.message || "Não foi possível emitir o token Power BI.", "error");
          }
        }
      });
    }

    const keysTbody = document.getElementById("bi-keys-tbody");
    if (keysTbody) {
      keysTbody.addEventListener("click", async function (ev) {
        const btn = ev.target.closest("[data-revoke-bi-key]");
        if (!btn) {
          return;
        }
        const keyId = btn.getAttribute("data-revoke-bi-key");
        if (!keyId) {
          return;
        }
        try {
          await App.apiFetch("/api/bi/keys/" + encodeURIComponent(keyId) + "/revoke", {
            method: "POST",
            body: {},
          });
          App.toast("Token Power BI revogado imediatamente.", "success");
          await loadBiKeys();
        } catch (err) {
          App.toast(err.message || "Falha ao revogar token Power BI.", "error");
        }
      });
    }

    const btnDl = document.getElementById("btn-download-pbids");
    if (btnDl) {
      btnDl.addEventListener("click", triggerPbidsDownload);
    }

    const btnDlInline = document.getElementById("btn-download-pbids-inline");
    if (btnDlInline) {
      btnDlInline.addEventListener("click", triggerPbidsDownload);
    }

    const btnCopy = document.getElementById("btn-copy-bi-token");
    if (btnCopy) {
      btnCopy.addEventListener("click", function () {
        copyGeneratedToken();
      });
    }

    const btnRefresh = document.getElementById("btn-refresh-bi");
    if (btnRefresh) {
      btnRefresh.addEventListener("click", function () {
        onBiTabOpened();
      });
    }
  }

  window.CortexBiUI = {
    init: init,
    onBiTabOpened: onBiTabOpened,
    syncWalletOptions: syncWalletOptions,
    syncLukeAdminOption: syncLukeAdminOption,
    loadBiKpis: loadBiKpis,
    loadBiKeys: loadBiKeys,
  };
})();
