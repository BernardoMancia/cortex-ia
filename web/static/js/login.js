(function () {
  "use strict";

  var csrfToken = "";
  var useRecovery = false;

  var loginForm = document.getElementById("login-form");
  var firstLoginForm = document.getElementById("first-login-form");
  var errorBox = document.getElementById("login-error-box");
  var infoBox = document.getElementById("login-info-box");
  var totpBox = document.getElementById("totp-step-box");
  var totpGroup = document.getElementById("totp-digit-group");
  var recGroup = document.getElementById("recovery-code-group");
  var toggleRecBtn = document.getElementById("toggle-recovery-btn");
  var loginBtn = document.getElementById("login-submit-btn");
  var firstBtn = document.getElementById("first-login-submit-btn");

  function showError(msg) {
    if (!msg) {
      errorBox.hidden = true;
      errorBox.textContent = "";
      return;
    }
    errorBox.textContent = msg;
    errorBox.hidden = false;
  }

  function showInfo(msg) {
    if (!msg) {
      infoBox.hidden = true;
      infoBox.textContent = "";
      return;
    }
    infoBox.textContent = msg;
    infoBox.hidden = false;
  }

  function extractDetail(data, fallback) {
    if (!data) return fallback;
    if (typeof data.detail === "string") return data.detail;
    if (data.detail && typeof data.detail.message === "string") return data.detail.message;
    if (typeof data.message === "string") return data.message;
    return fallback;
  }

  function switchToFirstLogin(token) {
    csrfToken = token || csrfToken;
    loginForm.hidden = true;
    firstLoginForm.hidden = false;
    showError("");
    showInfo("");
    var newPwdInput = document.getElementById("first-new-password");
    if (newPwdInput) newPwdInput.focus();
  }

  async function probeExistingSession() {
    try {
      var resp = await fetch("/api/auth/me", { credentials: "same-origin" });
      if (!resp.ok) return;
      var data = await resp.json();
      if (data && data.csrf_token) {
        csrfToken = data.csrf_token;
      }
      if (data && data.must_change_password) {
        switchToFirstLogin(csrfToken);
      } else if (data && data.id) {
        window.location.replace("/");
      }
    } catch (_err) {
    }
  }

  if (toggleRecBtn) {
    toggleRecBtn.addEventListener("click", function () {
      useRecovery = !useRecovery;
      totpGroup.hidden = useRecovery;
      recGroup.hidden = !useRecovery;
      toggleRecBtn.textContent = useRecovery
        ? "Usar código de 6 dígitos do autenticador"
        : "Usar código de recuperação";
    });
  }

  if (loginForm) {
    loginForm.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      showError("");
      showInfo("");
      loginBtn.disabled = true;

      var username = document.getElementById("login-username").value.trim();
      var password = document.getElementById("login-password").value;
      var totpCode = document.getElementById("login-totp").value.trim();
      var recCode = document.getElementById("login-recovery").value.trim();

      var payload = { username: username, password: password };
      if (!totpBox.hidden) {
        if (useRecovery && recCode) {
          payload.recovery_code = recCode;
        } else if (totpCode) {
          payload.totp_code = totpCode;
        }
      }

      try {
        var resp = await fetch("/api/auth/login", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });
        var data = await resp.json().catch(function () { return {}; });
        if (!resp.ok) {
          showError(extractDetail(data, "Falha na autenticação."));
          loginBtn.disabled = false;
          return;
        }
        if (data.totp_required) {
          totpBox.hidden = false;
          showInfo(data.message || "Informe o código 2FA para concluir o login.");
          var totpInput = document.getElementById("login-totp");
          if (totpInput) totpInput.focus();
          loginBtn.disabled = false;
          return;
        }
        csrfToken = data.csrf_token || "";
        if (data.must_change_password) {
          switchToFirstLogin(csrfToken);
          loginBtn.disabled = false;
          return;
        }
        window.location.replace("/");
      } catch (_netErr) {
        showError("Erro de conexão com o servidor Córtex IA.");
        loginBtn.disabled = false;
      }
    });
  }

  if (firstLoginForm) {
    firstLoginForm.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      showError("");
      var newPassword = document.getElementById("first-new-password").value;
      var confirmPassword = document.getElementById("first-confirm-password").value;
      if (newPassword !== confirmPassword) {
        showError("As senhas digitadas não coincidem.");
        return;
      }
      firstBtn.disabled = true;
      try {
        var resp = await fetch("/api/auth/first-login", {
          method: "POST",
          credentials: "same-origin",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": csrfToken
          },
          body: JSON.stringify({
            new_password: newPassword,
            confirm_password: confirmPassword
          })
        });
        var data = await resp.json().catch(function () { return {}; });
        if (!resp.ok) {
          showError(extractDetail(data, "Não foi possível atualizar a senha."));
          firstBtn.disabled = false;
          return;
        }
        window.location.replace("/");
      } catch (_err) {
        showError("Falha de rede ao salvar a nova senha.");
        firstBtn.disabled = false;
      }
    });
  }

  probeExistingSession();
})();
