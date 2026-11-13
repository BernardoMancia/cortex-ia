(function () {
  "use strict";

  const state = {
    me: null,
    csrfToken: "",
    stepUpToken: "",
    stepUpExpiresAtMs: 0,
    status: null,
    trades: [],
    equityCurve: [],
    capital: null,
    wallets: [],
    catalog: [],
    risk: null,
    guide: null,
    activeTab: "tab-overview",
  };

  function esc(val) {
    return String(val === null || val === undefined ? "" : val)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  const brlFormatter = new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  function formatBRL(value) {
    const num = Number(value);
    return brlFormatter.format(Number.isFinite(num) ? num : 0);
  }

  function formatPct(value, digits, forceSign) {
    const d = typeof digits === "number" ? digits : 2;
    const num = Number(value);
    const safe = Number.isFinite(num) ? num : 0;
    const prefix = forceSign && safe > 0 ? "+" : "";
    return prefix + safe.toFixed(d).replace(".", ",") + "%";
  }

  function formatDateTime(isoStr) {
    if (!isoStr) {
      return "—";
    }
    const raw = String(isoStr);
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) {
      return raw.slice(0, 19).replace("T", " ");
    }
    return parsed.toLocaleString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function toast(message, level) {
    const stack = document.getElementById("toast-stack");
    if (!stack) {
      return;
    }
    const kind = level || "success";
    const item = document.createElement("div");
    item.className = "toast-item toast-" + kind;
    item.textContent = String(message || "");
    stack.appendChild(item);
    window.setTimeout(function () {
      if (item.parentNode) {
        item.parentNode.removeChild(item);
      }
    }, 4500);
  }

  function openModal(modalId) {
    const el = document.getElementById(modalId);
    if (el) {
      el.hidden = false;
    }
  }

  function closeModal(modalId) {
    const el = document.getElementById(modalId);
    if (el) {
      el.hidden = true;
    }
  }

  function getCachedStepUpToken() {
    if (!state.stepUpToken) {
      return "";
    }
    if (Date.now() >= state.stepUpExpiresAtMs - 3000) {
      state.stepUpToken = "";
      state.stepUpExpiresAtMs = 0;
      return "";
    }
    return state.stepUpToken;
  }

  function setCachedStepUpToken(token, expiresInSeconds) {
    const ttl = Math.max(10, Number(expiresInSeconds) || 180);
    state.stepUpToken = String(token || "");
    state.stepUpExpiresAtMs = Date.now() + ttl * 1000;
  }

  function clearStepUpToken() {
    state.stepUpToken = "";
    state.stepUpExpiresAtMs = 0;
  }

  function extractErrorMessage(data, status) {
    if (!data) {
      return "Falha HTTP " + status;
    }
    const detail = data.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string") {
        return detail.message;
      }
      if (Array.isArray(detail) && detail.length > 0) {
        return detail
          .map(function (entry) {
            return entry && entry.msg ? entry.msg : JSON.stringify(entry);
          })
          .join("; ");
      }
    }
    if (typeof data.message === "string") {
      return data.message;
    }
    return "Erro inesperado (" + status + ")";
  }

  async function apiFetch(path, options) {
    const opts = Object.assign({}, options || {});
    const method = String(opts.method || "GET").toUpperCase();
    const headers = Object.assign({ Accept: "application/json" }, opts.headers || {});

    if (opts.body !== undefined && opts.body !== null && !headers["Content-Type"]) {
      headers["Content-Type"] = "application/json";
    }

    if (method === "POST" || method === "PUT" || method === "PATCH" || method === "DELETE") {
      if (state.csrfToken) {
        headers["X-CSRF-Token"] = state.csrfToken;
      }
    }

    if (opts.requireStepUp && !getCachedStepUpToken() && typeof window.CortexApp.promptStepUp === "function") {
      const freshToken = await window.CortexApp.promptStepUp();
      if (!freshToken) {
        const cancelErr = new Error("Confirmação Step-Up cancelada.");
        cancelErr.cancelled = true;
        throw cancelErr;
      }
    }

    const activeStepUp = getCachedStepUpToken();
    if (activeStepUp && !headers["X-Step-Up-Token"]) {
      headers["X-Step-Up-Token"] = activeStepUp;
    }

    const fetchConfig = {
      method: method,
      headers: headers,
      credentials: "same-origin",
    };

    if (opts.body !== undefined && opts.body !== null) {
      fetchConfig.body = typeof opts.body === "string" ? opts.body : JSON.stringify(opts.body);
    }

    const resp = await window.fetch(path, fetchConfig);
    let data = null;
    try {
      data = await resp.json();
    } catch (_err) {
      data = null;
    }

    if (data && typeof data.csrf_token === "string" && data.csrf_token) {
      state.csrfToken = data.csrf_token;
    }

    if (resp.status === 401) {
      const keepOnPage =
        path.indexOf("/api/security/step-up") === 0 ||
        path.indexOf("/api/security/password/change") === 0;
      if (!keepOnPage) {
        window.location.href = "/login";
      }
      const err401 = new Error(extractErrorMessage(data, 401));
      err401.status = 401;
      err401.payload = data;
      throw err401;
    }

    const detailObj = data && data.detail && typeof data.detail === "object" ? data.detail : null;
    if (resp.status === 403 && detailObj && detailObj.code === "MUST_CHANGE_PASSWORD") {
      window.location.href = "/login";
      const errMustChange = new Error(extractErrorMessage(data, 403));
      errMustChange.status = 403;
      throw errMustChange;
    }

    if (
      resp.status === 403 &&
      detailObj &&
      detailObj.code === "STEP_UP_REQUIRED" &&
      !opts._retriedStepUp &&
      typeof window.CortexApp.promptStepUp === "function"
    ) {
      clearStepUpToken();
      const grantedToken = await window.CortexApp.promptStepUp();
      if (!grantedToken) {
        const abortErr = new Error("Confirmação Step-Up cancelada.");
        abortErr.cancelled = true;
        throw abortErr;
      }
      return apiFetch(path, Object.assign({}, opts, { _retriedStepUp: true }));
    }

    if (!resp.ok) {
      const httpErr = new Error(extractErrorMessage(data, resp.status));
      httpErr.status = resp.status;
      httpErr.payload = data;
      throw httpErr;
    }

    return data;
  }

  document.addEventListener("click", function (ev) {
    const closeBtn = ev.target.closest("[data-close-modal]");
    if (closeBtn) {
      const targetId = closeBtn.getAttribute("data-close-modal");
      if (targetId) {
        closeModal(targetId);
      }
    }
  });

  window.CortexApp = {
    state: state,
    esc: esc,
    formatBRL: formatBRL,
    formatPct: formatPct,
    formatDateTime: formatDateTime,
    toast: toast,
    openModal: openModal,
    closeModal: closeModal,
    getCachedStepUpToken: getCachedStepUpToken,
    setCachedStepUpToken: setCachedStepUpToken,
    clearStepUpToken: clearStepUpToken,
    apiFetch: apiFetch,
    promptStepUp: null,
    refreshAll: null,
    switchTab: null,
  };
})();
