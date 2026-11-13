/**
 * Córtex IA — Autenticação & Primeiro Acesso (CSP-strict external script)
 */

const urlParams = new URLSearchParams(window.location.search);
const reason = urlParams.get('reason');
const alertBox = document.getElementById('alert');
const modalAlertBox = document.getElementById('modal-alert');
const loginForm = document.getElementById('login-form');
const firstLoginForm = document.getElementById('first-login-form');
const modalFirst = document.getElementById('modal-first-login');
const newPassInput = document.getElementById('new-password');
const strengthFill = document.getElementById('strength-fill');

let currentAuthUsername = '';
let currentAuthPassword = '';

// Purga tokens legados em localStorage (autenticação via cookie HttpOnly SameSite=Strict)
try { localStorage.removeItem('cortex_token'); } catch (e) {}

function showAlert(msg, type = 'alert-error') {
    if (!alertBox) return;
    alertBox.className = `alert-box ${type}`;
    alertBox.textContent = msg;
    alertBox.style.display = 'block';
}

function showModalAlert(msg, type = 'alert-error') {
    if (!modalAlertBox) return;
    modalAlertBox.className = `alert-box ${type}`;
    modalAlertBox.textContent = msg;
    modalAlertBox.style.display = 'block';
}

if (reason === 'idle') {
    showAlert('Sua sessão foi encerrada automaticamente por inatividade.', 'alert-info');
} else if (reason === 'expired') {
    showAlert('Sua sessão expirou. Por favor faça login novamente.', 'alert-info');
}

// Medidor de Força da Senha
if (newPassInput && strengthFill) {
    newPassInput.addEventListener('input', (e) => {
        const val = e.target.value;
        let score = 0;
        if (val.length >= 8) score += 25;
        if (val.length >= 12) score += 25;
        if (/[A-Z]/.test(val) && /[a-z]/.test(val)) score += 25;
        if (/[0-9]/.test(val) || /[^A-Za-z0-9]/.test(val)) score += 25;

        strengthFill.style.width = score + '%';
        if (score <= 25) strengthFill.style.backgroundColor = '#ef4444';
        else if (score <= 50) strengthFill.style.backgroundColor = '#f59e0b';
        else if (score <= 75) strengthFill.style.backgroundColor = '#3b82f6';
        else strengthFill.style.backgroundColor = '#10b981';
    });
}

// Submit do Login
if (loginForm) {
    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('btn-login');
        const username = document.getElementById('username').value.trim();
        const password = document.getElementById('password').value;

        btn.disabled = true;
        btn.innerHTML = '<span>Verificando...</span>';
        alertBox.style.display = 'none';

        try {
            const res = await fetch('/api/auth/login', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ username, password })
            });

            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.detail || 'Erro ao realizar login.');
            }

            if (data.must_change_password) {
                currentAuthUsername = username;
                currentAuthPassword = password;
                modalFirst.style.display = 'flex';
                document.getElementById('new-username').value = '';
                document.getElementById('new-username').focus();
            } else {
                showAlert('Login realizado com sucesso! Redirecionando...', 'alert-success');
                setTimeout(() => {
                    window.location.href = '/';
                }, 400);
            }
        } catch (err) {
            showAlert(err.message, 'alert-error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '<span>Entrar no Sistema</span>';
        }
    });
}

// Submit do Primeiro Acesso
if (firstLoginForm) {
    firstLoginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('btn-save-credentials');
        const newUsername = document.getElementById('new-username').value.trim();
        const newPassword = document.getElementById('new-password').value;
        const confirmPassword = document.getElementById('confirm-password').value;

        if (newUsername.length < 3) {
            showModalAlert('O nome de usuário deve ter pelo menos 3 caracteres.', 'alert-error');
            return;
        }
        if (newPassword.length < 8) {
            showModalAlert('A nova senha deve ter pelo menos 8 caracteres.', 'alert-error');
            return;
        }
        if (newUsername.toLowerCase() === 'admin' || newPassword.trim().toLowerCase() === 'admin') {
            showModalAlert('Você não pode utilizar o usuário ou a senha padrão de fábrica.', 'alert-error');
            return;
        }
        if (newPassword !== confirmPassword) {
            showModalAlert('As senhas digitadas não coincidem.', 'alert-error');
            return;
        }

        btn.disabled = true;
        btn.innerHTML = '<span>Atualizando credenciais...</span>';
        modalAlertBox.style.display = 'none';

        try {
            const res = await fetch('/api/auth/first-login', {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    current_username: currentAuthUsername,
                    current_password: currentAuthPassword,
                    new_username: newUsername,
                    new_password: newPassword
                })
            });

            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.detail || 'Erro ao atualizar credenciais.');
            }

            showModalAlert('Credenciais salvas com sucesso! Entrando...', 'alert-success');
            setTimeout(() => {
                window.location.href = '/';
            }, 800);
        } catch (err) {
            showModalAlert(err.message, 'alert-error');
            btn.disabled = false;
            btn.innerHTML = '<span>Salvar Credenciais e Acessar</span>';
        }
    });
}
