(function () {
  "use strict";

  const App = window.CortexApp;
  let logsSocket = null;
  let logLinesBuffer = [];
  let initialized = false;

  function isLukeVerified() {
    return Boolean(
      App.state.me &&
      App.state.me.user &&
      App.state.me.user.is_owner_admin === true
    );
  }

  function showTemporaryPasswordReveal(username, tempPwd, inviteExpiresAt) {
    const box = document.getElementById("admin-temp-password-box");
    const valEl = document.getElementById("admin-temp-password-value");
    if (!box || !valEl) {
      return;
    }
    box.hidden = false;
    valEl.textContent =
      "Usuário: " + username +
      "  |  Senha Temporária: " + tempPwd +
      "  |  Expira em: " + App.formatDateTime(inviteExpiresAt);
  }

  async function loadUsersTable() {
    if (!isLukeVerified()) return;
    const tbody = document.getElementById("admin-users-tbody");
    if (!tbody) return;

    try {
      const res = await App.apiFetch("/api/admin/users");
      const users = Array.isArray(res.users) ? res.users : [];
      if (users.length === 0) {
        tbody.innerHTML =
          '<tr><td colspan="8" class="empty-state">Nenhum usuário encontrado.</td></tr>';
        return;
      }
      tbody.innerHTML = users
        .map(function (u) {
          const uid = Number(u.id);
          const isOwner = Boolean(u.is_owner_admin);
          const active = Boolean(u.is_active);
          const totp = Boolean(u.totp_enabled);
          const mustChange = Boolean(u.must_change_password);
          const roleBadge = isOwner
            ? '<span class="badge badge-observe">OWNER ADMIN</span>'
            : '<span class="badge badge-neutral">OPERADOR</span>';
          const statusBadge = active
            ? '<span class="badge badge-active">ATIVO</span>'
            : '<span class="badge badge-prod">BLOQUEADO</span>';
          const totpBadge = totp
            ? '<span class="badge badge-active">2FA ON</span>'
            : '<span class="badge badge-neutral">2FA OFF</span>';
          const inviteTxt = mustChange
            ? "Pendente (exp: " + App.esc(App.formatDateTime(u.invite_expires_at)) + ")"
            : "Definitiva";

          const actionsHtml = isOwner
            ? '<span class="panel-subtitle">Conta Proprietária Protegida</span>'
            : '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
              '<button type="button" class="btn btn-sm ' + (active ? "btn-amber" : "btn-emerald") + '" data-admin-toggle="' + uid + '">' +
              (active ? "Bloquear" : "Ativar") + "</button>" +
              '<button type="button" class="btn btn-sm" data-admin-reset-pwd="' + uid + '" data-admin-uname="' + App.esc(u.username) + '">Reset Senha 24h</button>' +
              (totp
                ? '<button type="button" class="btn btn-sm" data-admin-reset-2fa="' + uid + '">Reset 2FA</button>'
                : "") +
              "</div>";

          return (
            "<tr>" +
            '<td class="text-mono">#' + uid + "</td>" +
            "<td><strong>" + App.esc(u.username) + "</strong></td>" +
            "<td>" + roleBadge + "</td>" +
            "<td>" + statusBadge + "</td>" +
            "<td>" + totpBadge + "</td>" +
            '<td style="font-size:12px">' + inviteTxt + "</td>" +
            '<td class="text-mono">' + App.esc(App.formatDateTime(u.last_login_at)) + "</td>" +
            "<td>" + actionsHtml + "</td>" +
            "</tr>"
          );
        })
        .join("");
    } catch (err) {
      tbody.innerHTML =
        '<tr><td colspan="8" class="empty-state">' + App.esc(err.message) + "</td></tr>";
    }
  }

  async function verifyAndLoadAudit() {
    if (!isLukeVerified()) return;
    const badge = document.getElementById("audit-integrity-badge");
    const tbody = document.getElementById("admin-audit-tbody");

    try {
      const [verRes, evRes] = await Promise.all([
        App.apiFetch("/api/admin/audit/verify"),
        App.apiFetch("/api/admin/audit/events?limit=100"),
      ]);

      if (badge) {
        const valid = Boolean(verRes.valid);
        badge.className = "badge " + (valid ? "badge-active" : "badge-panic");
        badge.textContent = valid
          ? "ÍNTEGRA (" + Number(verRes.checked_count || verRes.checked_rows || verRes.total_events || 0) + " elos)"
          : "VIOLADA!";
      }

      if (tbody) {
        const events = Array.isArray(evRes.events) ? evRes.events : [];
        if (events.length === 0) {
          tbody.innerHTML =
            '<tr><td colspan="4" class="empty-state">Sem eventos registrados.</td></tr>';
        } else {
          tbody.innerHTML = events
            .map(function (ev) {
              const actorStr =
                (ev.actor_user_id ? "UID#" + ev.actor_user_id + " • " : "") +
                (ev.ip_address || "local");
              return (
                "<tr>" +
                '<td class="text-mono">#' + App.esc(ev.id) + "</td>" +
                '<td class="text-mono">' + App.esc(App.formatDateTime(ev.created_at || ev.timestamp)) + "</td>" +
                '<td><span class="badge badge-neutral">' + App.esc(ev.event_type) + "</span></td>" +
                '<td class="text-mono">' + App.esc(actorStr) + "</td>" +
                "</tr>"
              );
            })
            .join("");
        }
      }
    } catch (err) {
      if (badge) {
        badge.className = "badge badge-prod";
        badge.textContent = "ERRO AUDITORIA";
      }
    }
  }

  function renderLogTerminal() {
    const term = document.getElementById("admin-log-terminal");
    if (!term) return;
    if (logLinesBuffer.length === 0) {
      term.textContent = "Nenhuma linha de log registrada no buffer atual.";
      return;
    }
    term.textContent = logLinesBuffer.join("\n");
    term.scrollTop = term.scrollHeight;
  }

  async function fetchLogsFallback() {
    if (!isLukeVerified()) return;
    try {
      const res = await App.apiFetch("/api/logs?limit=160");
      if (Array.isArray(res.logs)) {
        logLinesBuffer = res.logs.slice(-220);
        renderLogTerminal();
      }
    } catch (_err) {
      return;
    }
  }

  function connectLogsWebSocket() {
    if (!isLukeVerified()) return;
    if (logsSocket && (logsSocket.readyState === WebSocket.OPEN || logsSocket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    const badge = document.getElementById("ws-logs-badge");
    const wsScheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = wsScheme + "//" + window.location.host + "/ws/logs";

    try {
      logsSocket = new window.WebSocket(wsUrl);
    } catch (_err) {
      if (badge) {
        badge.className = "badge badge-maintenance";
        badge.textContent = "HTTP FALLBACK";
      }
      fetchLogsFallback();
      return;
    }

    logsSocket.addEventListener("open", function () {
      if (badge) {
        badge.className = "badge badge-active";
        badge.textContent = "AO VIVO WS";
      }
    });

    logsSocket.addEventListener("message", function (ev) {
      try {
        const msg = JSON.parse(ev.data);
        if (msg.type === "snapshot" && Array.isArray(msg.logs)) {
          logLinesBuffer = msg.logs.slice(-220);
          renderLogTerminal();
        } else if (msg.type === "line" && typeof msg.line === "string") {
          logLinesBuffer.push(msg.line);
          if (logLinesBuffer.length > 240) {
            logLinesBuffer = logLinesBuffer.slice(-240);
          }
          renderLogTerminal();
        }
      } catch (_parseErr) {
        return;
      }
    });

    logsSocket.addEventListener("close", function () {
      if (badge) {
        badge.className = "badge badge-neutral";
        badge.textContent = "DESCONECTADO";
      }
      logsSocket = null;
    });

    logsSocket.addEventListener("error", function () {
      fetchLogsFallback();
    });
  }

  async function onAdminTabOpened() {
    if (!isLukeVerified()) return;
    await Promise.all([loadUsersTable(), verifyAndLoadAudit(), fetchLogsFallback()]);
    connectLogsWebSocket();
  }

  function initForLuke() {
    if (!isLukeVerified() || initialized) {
      return;
    }
    initialized = true;

    const btnOpenCreate = document.getElementById("btn-open-create-user");
    if (btnOpenCreate) {
      btnOpenCreate.addEventListener("click", function () {
        const form = document.getElementById("form-admin-create-user");
        if (form) form.reset();
        App.openModal("modal-admin-user");
      });
    }

    const formCreate = document.getElementById("form-admin-create-user");
    if (formCreate) {
      formCreate.addEventListener("submit", async function (ev) {
        ev.preventDefault();
        const uname = document.getElementById("admin-new-username").value.trim();
        const tmpPwd = document.getElementById("admin-new-temp-pwd").value.trim();
        const hours = Number(document.getElementById("admin-invite-hours").value || 24);
        const mkWallet = document.getElementById("admin-create-initial-wallet").checked;

        try {
          const res = await App.apiFetch("/api/admin/users", {
            method: "POST",
            requireStepUp: true,
            body: {
              username: uname,
              temporary_password: tmpPwd || null,
              invite_hours: hours,
              create_initial_wallet: mkWallet,
            },
          });
          App.closeModal("modal-admin-user");
          showTemporaryPasswordReveal(
            res.user.username,
            res.temporary_password,
            res.invite_expires_at
          );
          App.toast("Operador " + res.user.username + " criado com sucesso!", "success");
          await Promise.all([loadUsersTable(), verifyAndLoadAudit()]);
        } catch (err) {
          if (!err.cancelled) App.toast(err.message, "error");
        }
      });
    }

    const usersTbody = document.getElementById("admin-users-tbody");
    if (usersTbody) {
      usersTbody.addEventListener("click", async function (ev) {
        const toggleBtn = ev.target.closest("[data-admin-toggle]");
        if (toggleBtn) {
          const uid = Number(toggleBtn.getAttribute("data-admin-toggle"));
          try {
            const res = await App.apiFetch("/api/admin/users/" + uid + "/toggle-active", {
              method: "POST",
              requireStepUp: true,
              body: {},
            });
            App.toast(
              res.is_active ? "Conta reativada." : "Conta bloqueada imediatamente.",
              "success"
            );
            await Promise.all([loadUsersTable(), verifyAndLoadAudit()]);
          } catch (err) {
            if (!err.cancelled) App.toast(err.message, "error");
          }
          return;
        }

        const resetPwdBtn = ev.target.closest("[data-admin-reset-pwd]");
        if (resetPwdBtn) {
          const uid = Number(resetPwdBtn.getAttribute("data-admin-reset-pwd"));
          const uname = resetPwdBtn.getAttribute("data-admin-uname") || ("#" + uid);
          try {
            const res = await App.apiFetch("/api/admin/users/" + uid + "/reset-password", {
              method: "POST",
              requireStepUp: true,
              body: { invite_hours: 24 },
            });
            showTemporaryPasswordReveal(uname, res.temporary_password, res.invite_expires_at);
            App.toast("Nova senha temporária de 24h emitida para " + uname + ".", "success");
            await Promise.all([loadUsersTable(), verifyAndLoadAudit()]);
          } catch (err) {
            if (!err.cancelled) App.toast(err.message, "error");
          }
          return;
        }

        const reset2faBtn = ev.target.closest("[data-admin-reset-2fa]");
        if (reset2faBtn) {
          const uid = Number(reset2faBtn.getAttribute("data-admin-reset-2fa"));
          try {
            await App.apiFetch("/api/admin/users/" + uid + "/reset-2fa", {
              method: "POST",
              requireStepUp: true,
              body: {},
            });
            App.toast("2FA redefinido para o operador #" + uid + ".", "warn");
            await Promise.all([loadUsersTable(), verifyAndLoadAudit()]);
          } catch (err) {
            if (!err.cancelled) App.toast(err.message, "error");
          }
        }
      });
    }

    const btnEnterMaint = document.getElementById("btn-admin-enter-maintenance");
    if (btnEnterMaint) {
      btnEnterMaint.addEventListener("click", async function () {
        try {
          await App.apiFetch("/api/system/maintenance/enter", {
            method: "POST",
            body: { reason: "Modo Manutenção ativado manualmente no Painel Luke." },
          });
          App.toast("Servidor colocado em Modo Manutenção global.", "warn");
          if (typeof App.refreshAll === "function") await App.refreshAll();
          await verifyAndLoadAudit();
        } catch (err) {
          App.toast(err.message, "error");
        }
      });
    }

    const btnVerifyAudit = document.getElementById("btn-verify-audit");
    if (btnVerifyAudit) {
      btnVerifyAudit.addEventListener("click", async function () {
        await verifyAndLoadAudit();
        App.toast("Verificação criptográfica HMAC-SHA256 concluída.", "success");
      });
    }

    const btnRefreshLogs = document.getElementById("btn-refresh-logs");
    if (btnRefreshLogs) {
      btnRefreshLogs.addEventListener("click", function () {
        fetchLogsFallback();
        connectLogsWebSocket();
      });
    }

    const btnClearScreen = document.getElementById("btn-clear-logs-screen");
    if (btnClearScreen) {
      btnClearScreen.addEventListener("click", function () {
        logLinesBuffer = [];
        renderLogTerminal();
      });
    }
  }

  window.CortexLukeAdminUI = {
    initForLuke: initForLuke,
    onAdminTabOpened: onAdminTabOpened,
  };
})();
