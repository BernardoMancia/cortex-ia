(function () {
  "use strict";

  const App = window.CortexApp;
  let selectedPreset = "1h";
  let panicDeadlineMs = null;
  let activePanicScope = "wallet";
  let countdownIntervalId = null;

  function formatCountdown(remainingSeconds) {
    if (remainingSeconds === null || remainingSeconds === undefined) {
      return "(Pausa Indefinida)";
    }
    const sec = Math.max(0, Math.floor(Number(remainingSeconds)));
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const s = sec % 60;
    const pad = function (n) {
      return n < 10 ? "0" + n : String(n);
    };
    return "(" + pad(h) + ":" + pad(m) + ":" + pad(s) + " restantes)";
  }

  function tickPanicCountdown() {
    const el = document.getElementById("panic-countdown-text");
    if (!el) {
      return;
    }
    if (panicDeadlineMs === null) {
      el.textContent = "(Pausa Indefinida)";
      return;
    }
    const remSec = Math.max(0, Math.floor((panicDeadlineMs - Date.now()) / 1000));
    el.textContent = formatCountdown(remSec);
  }

  function syncBanners(statusData) {
    if (!statusData) {
      return;
    }
    const isLuke = Boolean(
      App.state.me && App.state.me.user && App.state.me.user.is_owner_admin === true
    );
    const globalState = statusData.global_state || {};
    const globalMode = String(globalState.global_mode || "ACTIVE").toUpperCase();
    const walletGate = statusData.wallet_gate || null;
    const globalGate = statusData.global_gate || null;

    const maintBanner = document.getElementById("maintenance-banner");
    const btnOpenPreflight = document.getElementById("btn-open-preflight");
    const maintReasonEl = document.getElementById("maintenance-banner-reason");

    if (maintBanner) {
      if (globalMode === "MAINTENANCE") {
        maintBanner.hidden = false;
        if (maintReasonEl && globalState.maintenance_reason) {
          maintReasonEl.textContent = String(globalState.maintenance_reason);
        }
        if (btnOpenPreflight) {
          if (isLuke) {
            btnOpenPreflight.hidden = false;
            btnOpenPreflight.style.display = "inline-flex";
          } else {
            btnOpenPreflight.hidden = true;
            btnOpenPreflight.style.display = "none";
          }
        }
      } else {
        maintBanner.hidden = true;
      }
    }

    const globalOpt = document.getElementById("panic-scope-global-opt");
    if (globalOpt) {
      globalOpt.hidden = !isLuke;
    }

    const panicBanner = document.getElementById("panic-banner");
    const panicTitle = document.getElementById("panic-banner-title");
    const panicReason = document.getElementById("panic-banner-reason");
    const walletPanic =
      walletGate && String(walletGate.effective_mode || "").toUpperCase() === "PANIC";

    if (panicBanner) {
      if (globalMode === "PANIC") {
        activePanicScope = "global";
        panicBanner.hidden = false;
        if (panicTitle) panicTitle.textContent = "🚨 PÂNICO GLOBAL DO SERVIDOR ATIVO:";
        if (panicReason) {
          panicReason.textContent = String(globalState.panic_reason || "Execução global suspensa.");
        }
        const remGlobal = globalGate ? globalGate.remaining_seconds : null;
        panicDeadlineMs =
          remGlobal !== null && remGlobal !== undefined ? Date.now() + Number(remGlobal) * 1000 : null;
        tickPanicCountdown();
      } else if (walletPanic) {
        activePanicScope = "wallet";
        panicBanner.hidden = false;
        if (panicTitle) panicTitle.textContent = "🚨 PÂNICO DA CARTEIRA ATIVO:";
        if (panicReason) {
          panicReason.textContent = String(walletGate.block_reason || "Carteira pausada em modo de emergência.");
        }
        const remWallet = walletGate.remaining_seconds;
        panicDeadlineMs =
          remWallet !== null && remWallet !== undefined ? Date.now() + Number(remWallet) * 1000 : null;
        tickPanicCountdown();
      } else {
        panicBanner.hidden = true;
        panicDeadlineMs = null;
      }
    }
  }

  async function loadPreflightChecklist() {
    const summaryEl = document.getElementById("preflight-summary-callout");
    const listEl = document.getElementById("preflight-checklist");
    const issuesEl = document.getElementById("preflight-issues-box");
    if (!listEl) {
      return;
    }
    if (summaryEl) {
      summaryEl.textContent = "Executando verificação Pre-Flight de produção...";
    }
    listEl.innerHTML = "";
    if (issuesEl) {
      issuesEl.hidden = true;
      issuesEl.textContent = "";
    }

    try {
      const res = await App.apiFetch("/api/system/preflight");
      renderPreflightReport(res.preflight || res);
    } catch (err) {
      if (summaryEl) {
        summaryEl.textContent = "Falha ao executar Pre-Flight: " + err.message;
      }
    }
  }

  function renderPreflightReport(rep) {
    const summaryEl = document.getElementById("preflight-summary-callout");
    const listEl = document.getElementById("preflight-checklist");
    const issuesEl = document.getElementById("preflight-issues-box");
    if (!rep || !listEl) {
      return;
    }

    if (summaryEl) {
      summaryEl.textContent = rep.ready
        ? "Todos os critérios de segurança aprovados! Pronto para desbloquear investimentos reais."
        : "Pendências detectadas no Pre-Flight. Revise os itens abaixo antes de liberar ordens.";
    }

    const flagBadge = function (ok) {
      return ok
        ? '<span class="badge badge-active">OK</span>'
        : '<span class="badge badge-prod">FALHA</span>';
    };

    const items = [
      { label: "Bridge / Conector Online", html: flagBadge(Boolean(rep.bridge_online)) },
      { label: "Autenticação na Corretora (" + App.esc(rep.account_server || "—") + ")", html: flagBadge(Boolean(rep.broker_authenticated)) },
      { label: "Saldo Reconhecido na Corretora", html: '<strong class="text-mono">' + App.formatBRL(rep.broker_balance) + "</strong>" },
      { label: "Custódia Sincronizada (" + Number(rep.positions_found || 0) + " posições)", html: flagBadge(Boolean(rep.custody_synced)) },
      { label: "Cadeia de Auditoria HMAC-SHA256 Íntegra", html: flagBadge(Boolean(rep.audit_chain_intact)) },
      { label: "Relógio BRT Sincronizado", html: flagBadge(Boolean(rep.clock_synced)) },
    ];

    listEl.innerHTML = items
      .map(function (it) {
        return '<li class="preflight-item"><span>' + it.label + "</span>" + it.html + "</li>";
      })
      .join("");

    if (issuesEl) {
      const issues = Array.isArray(rep.issues) ? rep.issues : [];
      if (issues.length > 0) {
        issuesEl.hidden = false;
        issuesEl.innerHTML =
          "<strong>Pendências identificadas:</strong><ul>" +
          issues.map(function (msg) { return "<li>" + App.esc(msg) + "</li>"; }).join("") +
          "</ul>";
      } else {
        issuesEl.hidden = true;
      }
    }
  }

  function bindPanicAndMaintenanceEvents() {
    if (!countdownIntervalId) {
      countdownIntervalId = window.setInterval(tickPanicCountdown, 1000);
    }

    const btnOpenPanic = document.getElementById("btn-open-panic");
    if (btnOpenPanic) {
      btnOpenPanic.addEventListener("click", function () {
        const liqChk = document.getElementById("panic-liquidate-checkbox");
        if (liqChk) liqChk.checked = false;
        App.openModal("modal-panic");
      });
    }

    const presetGrid = document.getElementById("panic-preset-grid");
    if (presetGrid) {
      presetGrid.addEventListener("click", function (ev) {
        const btn = ev.target.closest("[data-preset]");
        if (!btn) return;
        selectedPreset = btn.getAttribute("data-preset") || "1h";
        presetGrid.querySelectorAll(".preset-btn").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        const customGroup = document.getElementById("panic-custom-minutes-group");
        if (customGroup) {
          customGroup.hidden = selectedPreset !== "custom";
        }
      });
    }

    const formPanic = document.getElementById("form-panic-activate");
    if (formPanic) {
      formPanic.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const scope = document.getElementById("panic-scope-select").value || "wallet";
        const reason = document.getElementById("panic-reason-input").value.trim();
        const liquidate = document.getElementById("panic-liquidate-checkbox").checked;
        const customMins = Number(document.getElementById("panic-custom-minutes").value || 30);

        const body = {
          scope: scope,
          duration_preset: selectedPreset,
          reason: reason || "Botão de Pânico acionado manualmente.",
          liquidate_positions: liquidate,
        };
        if (selectedPreset === "custom") {
          body.custom_minutes = customMins;
        }

        try {
          await App.apiFetch("/api/system/panic/activate", {
            method: "POST",
            requireStepUp: liquidate,
            body: body,
          });
          App.closeModal("modal-panic");
          App.toast("Modo Pânico ativado imediatamente!", "warn");
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const btnClearPanic = document.getElementById("btn-clear-panic");
    if (btnClearPanic) {
      btnClearPanic.addEventListener("click", async function () {
        try {
          await App.apiFetch("/api/system/panic/clear", {
            method: "POST",
            body: { scope: activePanicScope, restore_mode: "ACTIVE" },
          });
          App.toast("Operação retomada com segurança.", "success");
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const openPreflightModal = function () {
      App.openModal("modal-preflight");
      loadPreflightChecklist();
    };

    const btnBannerPreflight = document.getElementById("btn-open-preflight");
    if (btnBannerPreflight) {
      btnBannerPreflight.addEventListener("click", openPreflightModal);
    }

    const btnAdminPreflight = document.getElementById("btn-admin-preflight");
    if (btnAdminPreflight) {
      btnAdminPreflight.addEventListener("click", openPreflightModal);
    }

    const btnRerun = document.getElementById("btn-rerun-preflight");
    if (btnRerun) {
      btnRerun.addEventListener("click", loadPreflightChecklist);
    }

    const btnUnlock = document.getElementById("btn-unlock-maintenance");
    if (btnUnlock) {
      btnUnlock.addEventListener("click", async function () {
        const forceChk = document.getElementById("preflight-force-checkbox");
        const force = Boolean(forceChk && forceChk.checked);
        btnUnlock.disabled = true;
        try {
          await App.apiFetch("/api/system/maintenance/unlock", {
            method: "POST",
            requireStepUp: true,
            body: { force: force },
          });
          App.closeModal("modal-preflight");
          App.toast("Modo Manutenção desbloqueado! Motor liberado para operar.", "success");
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (
            err.payload &&
            err.payload.detail &&
            err.payload.detail.preflight
          ) {
            renderPreflightReport(err.payload.detail.preflight);
          }
          if (!err.cancelled) App.toast(err.message, "error");
        } finally {
          btnUnlock.disabled = false;
        }
      });
    }
  }

  window.CortexPanicMaintenanceUI = {
    init: bindPanicAndMaintenanceEvents,
    syncBanners: syncBanners,
  };
})();
