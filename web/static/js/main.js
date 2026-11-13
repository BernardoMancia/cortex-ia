(function () {
  "use strict";

  const App = window.CortexApp;

  function enforceLukeAdminVisibility(isLukeAdmin) {
    const topAdminBtn = document.getElementById("btn-go-admin");
    const tabAdminBtn = document.getElementById("tab-btn-admin");
    const tabAdminPanel = document.getElementById("tab-admin");

    if (isLukeAdmin === true) {
      if (topAdminBtn) {
        topAdminBtn.hidden = false;
        topAdminBtn.style.display = "inline-flex";
      }
      if (tabAdminBtn) {
        tabAdminBtn.hidden = false;
        tabAdminBtn.style.display = "inline-block";
      }
      if (tabAdminPanel) {
        tabAdminPanel.hidden = false;
        tabAdminPanel.style.display = "";
      }
    } else {
      if (topAdminBtn) {
        topAdminBtn.hidden = true;
        topAdminBtn.style.display = "none";
      }
      if (tabAdminBtn) {
        tabAdminBtn.hidden = true;
        tabAdminBtn.style.display = "none";
      }
      if (tabAdminPanel && tabAdminPanel.parentNode) {
        tabAdminPanel.parentNode.removeChild(tabAdminPanel);
      }
    }
  }

  function switchTab(targetTabId) {
    const isLuke = Boolean(
      App.state.me && App.state.me.user && App.state.me.user.is_owner_admin === true
    );
    if (targetTabId === "tab-admin" && !isLuke) {
      return;
    }

    App.state.activeTab = targetTabId;
    document.querySelectorAll(".tab-btn").forEach(function (btn) {
      const matches = btn.getAttribute("data-tab") === targetTabId;
      btn.classList.toggle("active", matches);
    });

    document.querySelectorAll(".tab-panel").forEach(function (panel) {
      const matches = panel.id === targetTabId;
      panel.classList.toggle("active", matches);
    });

    if (targetTabId === "tab-security" && window.CortexSecurityUI) {
      window.CortexSecurityUI.loadActiveSessions();
    } else if (targetTabId === "tab-bi" && window.CortexBiUI) {
      window.CortexBiUI.onBiTabOpened();
    } else if (targetTabId === "tab-admin" && isLuke && window.CortexLukeAdminUI) {
      window.CortexLukeAdminUI.onAdminTabOpened();
    }
  }

  async function refreshAll() {
    const [statusRes, tradesRes, curveRes, walletsRes] = await Promise.all([
      App.apiFetch("/api/status"),
      App.apiFetch("/api/trades?limit=40"),
      App.apiFetch("/api/equity_curve?limit=180"),
      App.apiFetch("/api/wallets"),
    ]);

    App.state.status = statusRes;
    App.state.trades = tradesRes.trades || [];
    App.state.equityCurve = curveRes.equity_curve || [];
    App.state.wallets = walletsRes.wallets || [];

    if (!App.state.catalog || App.state.catalog.length === 0) {
      const catRes = await App.apiFetch("/api/wallets/catalog");
      App.state.catalog = catRes.connectors || [];
    }

    if (!App.state.guide) {
      const guideRes = await App.apiFetch("/api/guide");
      App.state.guide = guideRes;
      window.CortexCockpitUI.renderGuide(guideRes);
    }

    window.CortexCockpitUI.renderTopBarBadges(statusRes);
    window.CortexCockpitUI.renderOverview(statusRes, App.state.trades);
    window.CortexCockpitUI.renderEquityChart(App.state.equityCurve, statusRes);
    window.CortexCockpitUI.renderMarketTab(statusRes);

    window.CortexWalletsUI.renderWalletSelector(
      App.state.wallets,
      walletsRes.active_wallet_id
    );
    window.CortexWalletsUI.renderWalletsCards(
      App.state.wallets,
      walletsRes.active_wallet_id
    );
    window.CortexWalletsUI.renderCatalogCards(App.state.catalog);

    if (window.CortexBiUI) {
      window.CortexBiUI.syncWalletOptions(App.state.wallets);
      if (App.state.activeTab === "tab-bi") {
        window.CortexBiUI.loadBiKpis().catch(function () {});
      }
    }

    window.CortexPanicMaintenanceUI.syncBanners(statusRes);

    if (walletsRes.active_wallet_id) {
      const [capRes, riskRes] = await Promise.all([
        App.apiFetch("/api/capital/summary"),
        App.apiFetch("/api/risk/settings"),
      ]);
      App.state.capital = capRes.capital;
      App.state.risk = riskRes;
      window.CortexCockpitUI.renderCapitalTab(capRes);
      window.CortexCockpitUI.renderRiskSettings(riskRes);
    }
  }

  async function bootstrap() {
    App.switchTab = switchTab;
    App.refreshAll = refreshAll;

    window.CortexCockpitUI.init();
    window.CortexWalletsUI.init();
    window.CortexPanicMaintenanceUI.init();
    window.CortexSecurityUI.init();
    if (window.CortexBiUI) {
      window.CortexBiUI.init();
    }

    document.querySelectorAll(".tab-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const target = btn.getAttribute("data-tab");
        if (target) {
          switchTab(target);
        }
      });
    });

    const btnSec = document.getElementById("btn-go-security");
    if (btnSec) {
      btnSec.addEventListener("click", function () {
        switchTab("tab-security");
      });
    }

    const btnAdm = document.getElementById("btn-go-admin");
    if (btnAdm) {
      btnAdm.addEventListener("click", function () {
        switchTab("tab-admin");
      });
    }

    const btnLogout = document.getElementById("btn-logout");
    if (btnLogout) {
      btnLogout.addEventListener("click", async function () {
        try {
          await App.apiFetch("/api/auth/logout", { method: "POST", body: {} });
        } catch (_err) {
          App.clearStepUpToken();
        }
        window.location.href = "/login";
      });
    }

    try {
      const me = await App.apiFetch("/api/auth/me");
      if (me.must_change_password) {
        window.location.href = "/login";
        return;
      }

      App.state.me = me;
      App.state.csrfToken = me.csrf_token || "";

      const userEl = document.getElementById("current-username");
      if (userEl) {
        userEl.textContent = "👤 " + String(me.username || "");
      }

      const isLuke = Boolean(me.user && me.user.is_owner_admin === true);
      enforceLukeAdminVisibility(isLuke);
      if (window.CortexBiUI) {
        window.CortexBiUI.syncLukeAdminOption();
      }
      if (isLuke) {
        window.CortexLukeAdminUI.initForLuke();
      }

      window.CortexSecurityUI.syncSecurityState();
      await refreshAll();

      window.setInterval(function () {
        refreshAll().catch(function () {});
      }, 12000);

      window.setInterval(function () {
        App.apiFetch("/api/auth/heartbeat", { method: "POST", body: {} }).catch(function () {});
      }, 60000);
    } catch (_bootErr) {
      window.location.href = "/login";
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bootstrap);
  } else {
    bootstrap();
  }
})();
