(function () {
  "use strict";

  const App = window.CortexApp;
  let pendingStepUpResolver = null;

  function resolveStepUpModal(tokenValue) {
    App.closeModal("modal-step-up");
    const pwdInput = document.getElementById("step-up-password");
    const totpInput = document.getElementById("step-up-totp");
    const errBox = document.getElementById("step-up-error-box");
    if (pwdInput) pwdInput.value = "";
    if (totpInput) totpInput.value = "";
    if (errBox) {
      errBox.hidden = true;
      errBox.textContent = "";
    }
    if (pendingStepUpResolver) {
      const fn = pendingStepUpResolver;
      pendingStepUpResolver = null;
      fn(tokenValue || "");
    }
  }

  function promptStepUp() {
    const cached = App.getCachedStepUpToken();
    if (cached) {
      return Promise.resolve(cached);
    }
    return new Promise(function (resolve) {
      pendingStepUpResolver = resolve;
      const totpGroup = document.getElementById("step-up-totp-group");
      const hasTotp = Boolean(App.state.me && App.state.me.totp_enabled);
      if (totpGroup) {
        totpGroup.hidden = !hasTotp;
      }
      App.openModal("modal-step-up");
      const pwdInput = document.getElementById("step-up-password");
      if (pwdInput) {
        window.setTimeout(function () {
          pwdInput.focus();
        }, 40);
      }
    });
  }

  function svgToDataUri(svgMarkup) {
    const utf8Bytes = encodeURIComponent(String(svgMarkup || "")).replace(
      /%([0-9A-F]{2})/g,
      function (_match, hex) {
        return String.fromCharCode(parseInt(hex, 16));
      }
    );
    return "data:image/svg+xml;base64," + window.btoa(utf8Bytes);
  }

  function syncSecurityState() {
    const hasTotp = Boolean(App.state.me && App.state.me.totp_enabled);
    const badge = document.getElementById("totp-status-badge");
    const btnSetup = document.getElementById("btn-totp-setup");
    const btnDisable = document.getElementById("btn-totp-disable");
    const pwdTotpWrap = document.getElementById("sec-totp-field-wrap");

    if (badge) {
      badge.className = "badge " + (hasTotp ? "badge-active" : "badge-maintenance");
      badge.textContent = hasTotp ? "2FA TOTP ATIVO" : "2FA TOTP INATIVO";
    }
    if (btnSetup) {
      btnSetup.hidden = hasTotp;
    }
    if (btnDisable) {
      btnDisable.hidden = !hasTotp;
    }
    if (pwdTotpWrap) {
      pwdTotpWrap.hidden = !hasTotp;
    }
  }

  async function loadActiveSessions() {
    const tbody = document.getElementById("sessions-tbody");
    if (!tbody) {
      return;
    }
    try {
      const res = await App.apiFetch("/api/security/sessions");
      const list = Array.isArray(res.sessions) ? res.sessions : [];
      if (list.length === 0) {
        tbody.innerHTML =
          '<tr><td colspan="5" class="empty-state">Nenhuma sessão encontrada.</td></tr>';
        return;
      }
      tbody.innerHTML = list
        .map(function (s) {
          const curBadge = s.is_current
            ? '<span class="badge badge-active">SESSÃO ATUAL</span>'
            : '<span class="badge badge-neutral">OUTRO DISPOSITIVO</span>';
          return (
            "<tr>" +
            "<td>" + curBadge + "</td>" +
            '<td class="text-mono">' + App.esc(s.ip_address || "—") + "</td>" +
            '<td class="text-mono">' + App.esc(App.formatDateTime(s.created_at)) + "</td>" +
            '<td class="text-mono">' + App.esc(App.formatDateTime(s.last_seen_at)) + "</td>" +
            '<td class="text-mono">' + App.esc(App.formatDateTime(s.expires_at)) + "</td>" +
            "</tr>"
          );
        })
        .join("");
    } catch (err) {
      tbody.innerHTML =
        '<tr><td colspan="5" class="empty-state">' + App.esc(err.message) + "</td></tr>";
    }
  }

  function bindSecurityEvents() {
    App.promptStepUp = promptStepUp;

    const btnCancelStep = document.getElementById("btn-cancel-step-up");
    const btnAbortStep = document.getElementById("btn-abort-step-up");
    if (btnCancelStep) {
      btnCancelStep.addEventListener("click", function () {
        resolveStepUpModal("");
      });
    }
    if (btnAbortStep) {
      btnAbortStep.addEventListener("click", function () {
        resolveStepUpModal("");
      });
    }

    const formStepUp = document.getElementById("form-step-up");
    if (formStepUp) {
      formStepUp.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const pwd = document.getElementById("step-up-password").value;
        const rawCode = document.getElementById("step-up-totp").value.trim();
        const errBox = document.getElementById("step-up-error-box");

        const body = { password: pwd };
        if (rawCode) {
          if (rawCode.indexOf("-") >= 0 || rawCode.length > 6) {
            body.recovery_code = rawCode;
          } else {
            body.totp_code = rawCode;
          }
        }

        try {
          const res = await App.apiFetch("/api/security/step-up", {
            method: "POST",
            body: body,
          });
          App.setCachedStepUpToken(res.step_up_token, res.expires_in);
          resolveStepUpModal(res.step_up_token);
        } catch (err) {
          if (errBox) {
            errBox.hidden = false;
            errBox.textContent = err.message;
          }
        }
      });
    }

    const btnSetup = document.getElementById("btn-totp-setup");
    if (btnSetup) {
      btnSetup.addEventListener("click", async function () {
        btnSetup.disabled = true;
        try {
          const res = await App.apiFetch("/api/security/totp/setup", {
            method: "POST",
            body: {},
          });
          const setupBox = document.getElementById("totp-setup-box");
          const qrImg = document.getElementById("totp-qr-img");
          const secretEl = document.getElementById("totp-secret-code");
          const recGrid = document.getElementById("totp-recovery-grid");

          if (qrImg && res.qr_svg) {
            qrImg.src = svgToDataUri(res.qr_svg);
          }
          if (secretEl) {
            secretEl.textContent = res.secret || "";
          }
          if (recGrid && Array.isArray(res.recovery_codes)) {
            recGrid.innerHTML = res.recovery_codes
              .map(function (c) {
                return '<div class="recovery-chip">' + App.esc(c) + "</div>";
              })
              .join("");
          }
          if (setupBox) {
            setupBox.hidden = false;
          }
          App.toast("QR Code 2FA gerado localmente. Escaneie e confirme os 6 dígitos.", "success");
        } catch (err) {
          App.toast(err.message, "error");
        } finally {
          btnSetup.disabled = false;
        }
      });
    }

    const formEnable = document.getElementById("form-totp-enable");
    if (formEnable) {
      formEnable.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const codeInput = document.getElementById("input-totp-enable-code");
        const code = codeInput.value.trim();
        try {
          await App.apiFetch("/api/security/totp/enable", {
            method: "POST",
            body: { code: code },
          });
          codeInput.value = "";
          const setupBox = document.getElementById("totp-setup-box");
          if (setupBox) setupBox.hidden = true;
          if (App.state.me) {
            App.state.me.totp_enabled = true;
            if (App.state.me.user) App.state.me.user.totp_enabled = true;
          }
          syncSecurityState();
          App.toast("Autenticação 2FA TOTP ativada com sucesso!", "success");
        } catch (err) {
          App.toast(err.message, "error");
        }
      });
    }

    const btnDisable = document.getElementById("btn-totp-disable");
    if (btnDisable) {
      btnDisable.addEventListener("click", async function () {
        try {
          await App.apiFetch("/api/security/totp/disable", {
            method: "POST",
            requireStepUp: true,
            body: {},
          });
          if (App.state.me) {
            App.state.me.totp_enabled = false;
            if (App.state.me.user) App.state.me.user.totp_enabled = false;
          }
          syncSecurityState();
          App.toast("2FA TOTP desativado nesta conta.", "warn");
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const formPwd = document.getElementById("form-change-password");
    if (formPwd) {
      formPwd.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const cur = document.getElementById("sec-current-password").value;
        const nw = document.getElementById("sec-new-password").value;
        const cf = document.getElementById("sec-confirm-password").value;
        const totp = document.getElementById("sec-totp-code").value.trim();

        try {
          await App.apiFetch("/api/security/password/change", {
            method: "POST",
            body: {
              current_password: cur,
              new_password: nw,
              confirm_password: cf,
              totp_code: totp || null,
            },
          });
          formPwd.reset();
          App.clearStepUpToken();
          App.toast("Senha alterada e outras sessões revogadas!", "success");
          await loadActiveSessions();
        } catch (err) {
          App.toast(err.message, "error");
        }
      });
    }

    const btnRevoke = document.getElementById("btn-revoke-other-sessions");
    if (btnRevoke) {
      btnRevoke.addEventListener("click", async function () {
        try {
          const res = await App.apiFetch("/api/security/sessions/revoke-others", {
            method: "POST",
            body: {},
          });
          App.toast(
            "Outras sessões encerradas (" + Number(res.revoked_count || 0) + ").",
            "success"
          );
          await loadActiveSessions();
        } catch (err) {
          App.toast(err.message, "error");
        }
      });
    }
  }

  window.CortexSecurityUI = {
    init: bindSecurityEvents,
    syncSecurityState: syncSecurityState,
    loadActiveSessions: loadActiveSessions,
  };
})();
