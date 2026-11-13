(function () {
  "use strict";

  const App = window.CortexApp;

  function modeBadgeHtml(modeStr) {
    const m = String(modeStr || "").toUpperCase();
    if (m === "ACTIVE") {
      return '<span class="badge badge-active">ATIVA</span>';
    }
    if (m === "PANIC") {
      return '<span class="badge badge-panic">PÂNICO</span>';
    }
    if (m === "OBSERVE_ONLY") {
      return '<span class="badge badge-observe">APENAS MONITORAR</span>';
    }
    return '<span class="badge badge-maintenance">' + App.esc(m || "PAUSADA") + "</span>";
  }

  function renderWalletSelector(wallets, activeWalletId) {
    const sel = document.getElementById("wallet-select");
    if (!sel) {
      return;
    }
    if (!wallets || wallets.length === 0) {
      sel.innerHTML = '<option value="">Nenhuma carteira cadastrada</option>';
      return;
    }
    sel.innerHTML = wallets
      .map(function (w) {
        const wid = Number(w.id);
        const selected = activeWalletId && Number(activeWalletId) === wid ? " selected" : "";
        return (
          '<option value="' + wid + '"' + selected + ">" +
          App.esc(w.name) + " • " + App.esc(String(w.connector_type || "").toUpperCase()) +
          "</option>"
        );
      })
      .join("");
  }

  function renderWalletsCards(wallets, activeWalletId) {
    const grid = document.getElementById("wallets-cards-grid");
    if (!grid) {
      return;
    }
    if (!wallets || wallets.length === 0) {
      grid.innerHTML =
        '<div class="empty-state">Você ainda não possui carteiras vinculadas. Clique em "+ Adicionar Carteira".</div>';
      return;
    }
    grid.innerHTML = wallets
      .map(function (w) {
        const wid = Number(w.id);
        const isSelected = activeWalletId && Number(activeWalletId) === wid;
        const opMode = String(w.operational_mode || "OBSERVE_ONLY").toUpperCase();
        const autoOn = Boolean(w.automation_enabled);
        const isReady = Boolean(w.connector_ready);
        const nextMode = opMode === "ACTIVE" ? "OBSERVE_ONLY" : "ACTIVE";
        const modeBtnLabel =
          opMode === "ACTIVE" ? "Mudar p/ Apenas Monitorar" : "Ativar Modo Operacional";

        return (
          '<div class="connector-card">' +
          "<div>" +
          '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px">' +
          "<strong>" + App.esc(w.name) + "</strong>" +
          modeBadgeHtml(opMode) +
          "</div>" +
          '<div class="panel-subtitle">Conector: <strong class="text-mono">' +
          App.esc(w.connector_type) +
          "</strong>" +
          (isSelected ? ' • <span class="text-pos">Selecionada</span>' : "") +
          "</div>" +
          '<div style="margin-top:10px;font-size:12px;color:var(--text-secondary)">' +
          "Caixa Livre: <strong class='text-mono'>" + App.formatBRL(w.free_cash) + "</strong><br>" +
          "Teto IA: <strong class='text-mono'>" + App.formatBRL(w.ai_allocated_capital) + "</strong> • " +
          "Cofre: <strong class='text-mono'>" + App.formatBRL(w.vault_reserve_cash) + "</strong><br>" +
          "Automação: <strong>" + (autoOn ? "Ligada" : "Desligada") + "</strong>" +
          "</div>" +
          "</div>" +
          '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
          (isSelected
            ? ""
            : '<button type="button" class="btn btn-sm btn-primary" data-wallet-select="' + wid + '">Selecionar</button>') +
          (isReady
            ? '<button type="button" class="btn btn-sm" data-wallet-mode="' + wid + '" data-target-mode="' + nextMode + '">' + modeBtnLabel + "</button>" +
              '<button type="button" class="btn btn-sm ' + (autoOn ? "btn-amber" : "btn-emerald") + '" data-wallet-auto="' + wid + '" data-next-auto="' + (autoOn ? "0" : "1") + '">' +
              (autoOn ? "Pausar Robô" : "Ligar Robô") + "</button>"
            : '<span class="badge badge-neutral">WIP — Somente Leitura</span>') +
          '<button type="button" class="btn btn-sm" data-wallet-creds="' + wid + '" data-wallet-ctype="' + App.esc(w.connector_type) + '" data-wallet-wname="' + App.esc(w.name) + '">🔑 Credenciais</button>' +
          "</div>" +
          "</div>"
        );
      })
      .join("");
  }

  function renderCatalogCards(catalog) {
    const grid = document.getElementById("connectors-catalog-grid");
    if (!grid) {
      return;
    }
    if (!catalog || catalog.length === 0) {
      grid.innerHTML = '<div class="empty-state">Catálogo indisponível.</div>';
      return;
    }
    grid.innerHTML = catalog
      .map(function (c) {
        const ready = String(c.status || "").toLowerCase() === "ready";
        const badgeCls = ready ? "badge-active" : "badge-observe";
        const badgeTxt = c.badge || (ready ? "Pronta" : "Em Breve (WIP)");
        return (
          '<div class="connector-card">' +
          "<div>" +
          '<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px">' +
          "<strong>" + App.esc(c.display_name) + "</strong>" +
          '<span class="badge ' + badgeCls + '">' + App.esc(badgeTxt) + "</span>" +
          "</div>" +
          '<p class="panel-subtitle">' + App.esc(c.description || "") + "</p>" +
          "</div>" +
          "<div>" +
          '<button type="button" class="btn btn-sm btn-primary" data-catalog-create="' + App.esc(c.connector_type) + '">' +
          (ready ? "+ Conectar Conta Real" : "+ Criar Carteira Monitorada") +
          "</button>" +
          "</div>" +
          "</div>"
        );
      })
      .join("");
  }

  function renderConnectorFields(connectorType, isCredentialsUpdate) {
    const dynWrap = document.getElementById("wallet-dynamic-fields");
    const noteBox = document.getElementById("wallet-connector-status-note");
    if (!dynWrap || !noteBox) {
      return;
    }
    const catalog = App.state.catalog || [];
    const found = catalog.find(function (c) {
      return String(c.connector_type) === String(connectorType);
    });

    if (!found) {
      noteBox.textContent = "Selecione um conector do catálogo.";
      dynWrap.innerHTML = "";
      return;
    }

    const ready = String(found.status || "").toLowerCase() === "ready";
    noteBox.textContent = ready
      ? "Conector homologado para ordens reais na B3. Suas chaves são cifradas localmente com AES-256-GCM + HKDF."
      : "Conector em desenvolvimento (WIP): sua carteira será criada em Modo Apenas Monitorar (OBSERVE_ONLY) sem envio de ordens.";

    const fields = Array.isArray(found.fields) ? found.fields : [];
    dynWrap.innerHTML = fields
      .map(function (f) {
        const inputType = f.type === "password" ? "password" : f.type === "number" ? "number" : "text";
        const reqAttr = f.required && !isCredentialsUpdate && ready ? " required" : "";
        const hint = isCredentialsUpdate && f.type === "password"
          ? "Deixe em branco para manter o segredo atual"
          : f.placeholder || "";
        return (
          '<div class="field-group">' +
          '<label class="field-label">' + App.esc(f.label || f.name) + "</label>" +
          '<input type="' + inputType + '" class="field-input" data-conn-field="' + App.esc(f.name) + '" placeholder="' + App.esc(hint) + '"' + reqAttr + ">" +
          "</div>"
        );
      })
      .join("");
  }

  function openCreateWalletModal(preselectType) {
    const modeEl = document.getElementById("wallet-modal-mode");
    const idEl = document.getElementById("wallet-modal-id");
    const titleEl = document.getElementById("modal-wallet-title");
    const createWrap = document.getElementById("wallet-create-only-fields");
    const connSelect = document.getElementById("wallet-connector-select");
    const submitBtn = document.getElementById("btn-submit-wallet-modal");
    const nameInput = document.getElementById("wallet-name-input");

    if (!modeEl || !connSelect) {
      return;
    }
    modeEl.value = "create";
    if (idEl) idEl.value = "";
    if (titleEl) titleEl.textContent = "Nova Carteira Isolada";
    if (createWrap) createWrap.hidden = false;
    if (submitBtn) submitBtn.textContent = "Criar Carteira";
    if (nameInput) nameInput.value = "";

    const catalog = App.state.catalog || [];
    connSelect.innerHTML = catalog
      .map(function (c) {
        return '<option value="' + App.esc(c.connector_type) + '">' + App.esc(c.display_name) + "</option>";
      })
      .join("");

    if (preselectType) {
      connSelect.value = preselectType;
    }
    renderConnectorFields(connSelect.value, false);
    App.openModal("modal-wallet");
  }

  function openCredentialsModal(walletId, connectorType, walletName) {
    const modeEl = document.getElementById("wallet-modal-mode");
    const idEl = document.getElementById("wallet-modal-id");
    const titleEl = document.getElementById("modal-wallet-title");
    const createWrap = document.getElementById("wallet-create-only-fields");
    const submitBtn = document.getElementById("btn-submit-wallet-modal");

    if (modeEl) modeEl.value = "credentials";
    if (idEl) idEl.value = String(walletId);
    if (titleEl) titleEl.textContent = "🔑 Credenciais — " + walletName;
    if (createWrap) createWrap.hidden = true;
    if (submitBtn) submitBtn.textContent = "Atualizar Credenciais (Step-Up)";

    renderConnectorFields(connectorType, true);
    App.openModal("modal-wallet");
  }

  function collectDynamicConnectorConfig() {
    const cfg = {};
    const inputs = document.querySelectorAll("#wallet-dynamic-fields [data-conn-field]");
    inputs.forEach(function (inp) {
      const key = inp.getAttribute("data-conn-field");
      const raw = String(inp.value || "").trim();
      if (!key) return;
      if (inp.type === "number" && raw !== "") {
        cfg[key] = Number(raw);
      } else if (raw !== "") {
        cfg[key] = raw;
      }
    });
    return cfg;
  }

  function bindWalletsEvents() {
    const sel = document.getElementById("wallet-select");
    if (sel) {
      sel.addEventListener("change", async function () {
        const wid = Number(sel.value);
        if (!wid) return;
        try {
          await App.apiFetch("/api/wallets/" + wid + "/select", {
            method: "POST",
            body: {},
          });
          App.toast("Carteira ativa alternada.", "success");
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const btnTopNew = document.getElementById("btn-new-wallet");
    if (btnTopNew) {
      btnTopNew.addEventListener("click", function () {
        openCreateWalletModal();
      });
    }

    const btnRiskNew = document.getElementById("btn-risk-new-wallet");
    if (btnRiskNew) {
      btnRiskNew.addEventListener("click", function () {
        openCreateWalletModal();
      });
    }

    const connSelect = document.getElementById("wallet-connector-select");
    if (connSelect) {
      connSelect.addEventListener("change", function () {
        renderConnectorFields(connSelect.value, false);
      });
    }

    const catalogGrid = document.getElementById("connectors-catalog-grid");
    if (catalogGrid) {
      catalogGrid.addEventListener("click", function (ev) {
        const btn = ev.target.closest("[data-catalog-create]");
        if (!btn) return;
        openCreateWalletModal(btn.getAttribute("data-catalog-create"));
      });
    }

    const walletsGrid = document.getElementById("wallets-cards-grid");
    if (walletsGrid) {
      walletsGrid.addEventListener("click", async function (ev) {
        const selBtn = ev.target.closest("[data-wallet-select]");
        if (selBtn) {
          const wid = Number(selBtn.getAttribute("data-wallet-select"));
          try {
            await App.apiFetch("/api/wallets/" + wid + "/select", { method: "POST", body: {} });
            App.toast("Carteira selecionada.", "success");
            if (typeof App.refreshAll === "function") await App.refreshAll();
          } catch (err) {
            if (!err.cancelled) App.toast(err.message, "error");
          }
          return;
        }

        const modeBtn = ev.target.closest("[data-wallet-mode]");
        if (modeBtn) {
          const wid = Number(modeBtn.getAttribute("data-wallet-mode"));
          const targetMode = modeBtn.getAttribute("data-target-mode");
          try {
            await App.apiFetch("/api/wallets/" + wid + "/mode", {
              method: "POST",
              body: { operational_mode: targetMode },
            });
            App.toast("Modo operacional da carteira atualizado.", "success");
            if (typeof App.refreshAll === "function") await App.refreshAll();
          } catch (err) {
            if (!err.cancelled) App.toast(err.message, "error");
          }
          return;
        }

        const autoBtn = ev.target.closest("[data-wallet-auto]");
        if (autoBtn) {
          const wid = Number(autoBtn.getAttribute("data-wallet-auto"));
          const nextAuto = autoBtn.getAttribute("data-next-auto") === "1";
          try {
            await App.apiFetch("/api/wallets/" + wid + "/mode", {
              method: "POST",
              body: { automation_enabled: nextAuto },
            });
            App.toast(
              nextAuto ? "Automação ativada nesta carteira." : "Automação pausada nesta carteira.",
              "success"
            );
            if (typeof App.refreshAll === "function") await App.refreshAll();
          } catch (err) {
            if (!err.cancelled) App.toast(err.message, "error");
          }
          return;
        }

        const credsBtn = ev.target.closest("[data-wallet-creds]");
        if (credsBtn) {
          openCredentialsModal(
            Number(credsBtn.getAttribute("data-wallet-creds")),
            credsBtn.getAttribute("data-wallet-ctype"),
            credsBtn.getAttribute("data-wallet-wname")
          );
        }
      });
    }

    const formModal = document.getElementById("form-wallet-modal");
    if (formModal) {
      formModal.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const mode = document.getElementById("wallet-modal-mode").value;
        const cfg = collectDynamicConnectorConfig();

        try {
          if (mode === "credentials") {
            const wid = Number(document.getElementById("wallet-modal-id").value);
            await App.apiFetch("/api/wallets/" + wid + "/credentials", {
              method: "POST",
              requireStepUp: true,
              body: { connector_config: cfg },
            });
            App.toast("Credenciais cifradas atualizadas com sucesso.", "success");
          } else {
            const wName = document.getElementById("wallet-name-input").value.trim();
            const cType = document.getElementById("wallet-connector-select").value;
            const initCap = Number(document.getElementById("wallet-initial-capital").value || 0);
            const makeDef = document.getElementById("wallet-make-default").checked;
            if (!wName || wName.length < 2) {
              App.toast("Informe um nome válido para a carteira.", "warn");
              return;
            }
            await App.apiFetch("/api/wallets", {
              method: "POST",
              body: {
                name: wName,
                connector_type: cType,
                connector_config: cfg,
                initial_capital: initCap,
                make_default: makeDef,
              },
            });
            App.toast("Nova carteira criada com sucesso.", "success");
          }
          App.closeModal("modal-wallet");
          if (typeof App.refreshAll === "function") await App.refreshAll();
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }
  }

  window.CortexWalletsUI = {
    init: bindWalletsEvents,
    renderWalletSelector: renderWalletSelector,
    renderWalletsCards: renderWalletsCards,
    renderCatalogCards: renderCatalogCards,
  };
})();
