// Purga qualquer token legado em localStorage (autenticação usa exclusivamente cookie HttpOnly + CSRF)
try {
    localStorage.removeItem('cortex_token');
} catch (e) {}

let csrfToken = '';

// Interceptor global para envio transparente de cookies same-origin e cabeçalho X-CSRF-Token
(function() {
    const originalFetch = window.fetch.bind(window);
    const mutatingMethods = new Set(['POST', 'PUT', 'DELETE', 'PATCH']);

    window.fetch = async function(url, options) {
        const opts = Object.assign({}, options || {});
        opts.credentials = opts.credentials || 'same-origin';

        const method = String(opts.method || 'GET').toUpperCase();
        if (csrfToken && mutatingMethods.has(method)) {
            if (opts.headers instanceof Headers) {
                if (!opts.headers.has('X-CSRF-Token')) {
                    opts.headers.set('X-CSRF-Token', csrfToken);
                }
            } else if (Array.isArray(opts.headers)) {
                const hasCsrf = opts.headers.some(([k]) => String(k).toLowerCase() === 'x-csrf-token');
                if (!hasCsrf) {
                    opts.headers = opts.headers.concat([['X-CSRF-Token', csrfToken]]);
                }
            } else {
                opts.headers = Object.assign({}, opts.headers, {
                    'X-CSRF-Token': csrfToken
                });
            }
        }

        const response = await originalFetch(url, opts);
        const urlStr = typeof url === 'string' ? url : (url && url.url ? url.url : '');
        if (
            response.status === 401 &&
            !urlStr.includes('/api/auth/login') &&
            !urlStr.includes('/api/auth/first-login') &&
            !urlStr.includes('/api/auth/me') &&
            !window.location.pathname.startsWith('/login')
        ) {
            window.location.href = '/login?reason=expired';
        }
        return response;
    };
})();

const VALID_TOAST_TYPES = new Set(['info', 'success', 'error', 'warning']);

function showToast(message, type = 'info') {
    let container = document.getElementById('cortex-toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'cortex-toast-container';
        container.className = 'toast-container';
        document.body.appendChild(container);
    }
    const safeType = VALID_TOAST_TYPES.has(type) ? type : 'info';
    const toast = document.createElement('div');
    toast.className = `toast-item toast-${safeType}`;
    const span = document.createElement('span');
    span.textContent = message === null || message === undefined ? '' : String(message);
    toast.appendChild(span);
    container.appendChild(toast);
    setTimeout(() => toast.classList.add('show'), 10);
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 3500);
}

let idleTimer = null;
let warningCountdownInterval = null;
let idleSecondsLeft = 60;
const MAX_IDLE_SECONDS = 900;
const WARNING_SECONDS = 60;
let lastActivityTime = Date.now();

let isDraggingRiskSlider = false;
let riskSliderEditUntil = 0;
let lastServerRiskSignature = '';
let currentTxEnv = 'production';

let state = {
    equity: 0,
    balance: 0,
    positions: [],
    recentDecisions: [],
    news: [],
    marketStatus: 'DESCONHECIDO',
    activeTab: 'tab-overview',
    searchTerm: '',
    isPausedLogs: false,
    environment: 'homologation',
    activeGoal: null,
    capitalSummary: null,
    simulationSummary: null,
    productionSummary: null,
    riskParams: {
        stop_loss_percent: 7.5,
        trailing_trigger_percent: 3.5,
        trailing_distance_percent: 2.0
    }
};

let allocationChart = null;

function formatBRL(value) {
    const num = Number(value);
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(
        Number.isFinite(num) ? num : 0
    );
}

function formatPercent(value) {
    const num = Number(value);
    const v = Number.isFinite(num) ? num : 0;
    const sign = v >= 0 ? '+' : '';
    return `${sign}${v.toFixed(2)}%`;
}

function formatDate(dateStr) {
    if (!dateStr) return '--:--';
    const d = new Date(dateStr);
    if (Number.isNaN(d.getTime())) return '--:--';
    return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function sanitizeHttpUrl(rawUrl) {
    if (!rawUrl || typeof rawUrl !== 'string') return '#';
    try {
        const parsed = new URL(rawUrl.trim(), window.location.origin);
        if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
            return parsed.href;
        }
    } catch (e) {}
    return '#';
}

function dedupeAsync(fn) {
    let activePromise = null;
    return function(...args) {
        if (activePromise) return activePromise;
        activePromise = Promise.resolve()
            .then(() => fn(...args))
            .finally(() => {
                activePromise = null;
            });
        return activePromise;
    };
}

function startPollingLoop(taskFn, intervalMs) {
    let stopped = false;
    let timerId = null;

    async function tick() {
        if (stopped) return;
        try {
            await taskFn();
        } catch (e) {
            // Erros de rede transitórios são tratados silenciosamente no polling
        }
        if (!stopped) {
            timerId = setTimeout(tick, intervalMs);
        }
    }

    timerId = setTimeout(tick, intervalMs);
    return () => {
        stopped = true;
        if (timerId) clearTimeout(timerId);
    };
}

async function checkAuthAndSetupUser() {
    try {
        const res = await fetch('/api/auth/me');
        if (!res.ok) {
            window.location.href = '/login';
            return false;
        }
        const user = await res.json();
        if (user && user.csrf_token) {
            csrfToken = user.csrf_token;
        }
        const nameEl = document.getElementById('user-display-name');
        if (nameEl && user && user.username) {
            nameEl.textContent = user.username;
        }
        return true;
    } catch (e) {
        window.location.href = '/login';
        return false;
    }
}

async function performLogout(reason = '') {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) {}
    csrfToken = '';
    window.location.href = reason ? `/login?reason=${encodeURIComponent(reason)}` : '/login';
}

function setupIdleTimeout() {
    const modal = document.getElementById('modal-idle-warning');
    const countdownEl = document.getElementById('idle-countdown-seconds');
    const btnStay = document.getElementById('btn-extend-session');
    const btnLogout = document.getElementById('btn-logout');

    if (btnLogout) {
        btnLogout.addEventListener('click', () => performLogout());
    }

    if (btnStay) {
        btnStay.addEventListener('click', () => {
            resetIdleTimer();
            if (modal) modal.style.display = 'none';
            fetch('/api/auth/heartbeat', { method: 'POST' }).catch(() => {});
        });
    }

    function showIdleWarning() {
        if (modal) modal.style.display = 'flex';
        idleSecondsLeft = WARNING_SECONDS;
        if (countdownEl) countdownEl.textContent = idleSecondsLeft;

        clearInterval(warningCountdownInterval);
        warningCountdownInterval = setInterval(() => {
            idleSecondsLeft--;
            if (countdownEl) countdownEl.textContent = idleSecondsLeft;
            if (idleSecondsLeft <= 0) {
                clearInterval(warningCountdownInterval);
                performLogout('idle');
            }
        }, 1000);
    }

    function resetIdleTimer() {
        lastActivityTime = Date.now();
        clearInterval(warningCountdownInterval);
        if (modal) modal.style.display = 'none';
        clearTimeout(idleTimer);
        idleTimer = setTimeout(showIdleWarning, (MAX_IDLE_SECONDS - WARNING_SECONDS) * 1000);
    }

    const activityEvents = ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'];
    activityEvents.forEach(evt => {
        window.addEventListener(evt, () => {
            if (!modal || modal.style.display === 'none') {
                const now = Date.now();
                if (now - lastActivityTime > 30000) {
                    fetch('/api/auth/heartbeat', { method: 'POST' }).catch(() => {});
                    lastActivityTime = now;
                }
                resetIdleTimer();
            }
        }, { passive: true });
    });

    resetIdleTimer();
}

function activateTab(targetId) {
    const tabs = document.querySelectorAll('.nav-tab');
    const panes = document.querySelectorAll('.tab-pane');

    tabs.forEach(t => {
        t.classList.toggle('active', t.dataset.tab === targetId);
    });
    panes.forEach(p => {
        p.classList.toggle('active', p.id === targetId);
    });

    state.activeTab = targetId;
    if (targetId === 'tab-overview' && allocationChart) {
        allocationChart.resize();
    }
}

function setupTabs() {
    const tabs = document.querySelectorAll('.nav-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const targetId = tab.dataset.tab;
            if (targetId) activateTab(targetId);
        });
    });
}

function setupClock() {
    const timeEl = document.getElementById('system-time');
    function update() {
        const now = new Date();
        if (timeEl) {
            timeEl.textContent = now.toLocaleTimeString('pt-BR');
        }
    }
    update();
    setInterval(update, 1000);
}

function setupSearch() {
    const searchInput = document.getElementById('posSearch');
    if (searchInput) {
        searchInput.addEventListener('input', (e) => {
            state.searchTerm = e.target.value.trim().toUpperCase();
            renderPositions();
        });
    }
}

const fetchStatus = dedupeAsync(async function _fetchStatus() {
    try {
        const res = await fetch('/api/status');
        if (!res.ok) return;
        const data = await res.json();

        state.equity = data.equity ?? 0;
        state.balance = data.balance ?? 0;
        state.positions = Array.isArray(data.positions) ? data.positions : [];
        state.recentDecisions = Array.isArray(data.recent_decisions) ? data.recent_decisions : [];
        state.marketStatus = data.market_status || 'DESCONHECIDO';
        if (data.environment) {
            state.environment = data.environment;
            updateEnvironmentUI();
        }

        renderKPIs(data);
        renderMarketStatus(data.market_status);
        renderPositions();
        renderChart(data);
        renderDecisions();
    } catch (err) {}
});

function syncRiskSlidersFromServer(riskParams) {
    if (!riskParams) return;
    const sl = Number(riskParams.stop_loss_percent);
    const trig = Number(riskParams.trailing_trigger_percent);
    const dist = Number(riskParams.trailing_distance_percent);
    if (!Number.isFinite(sl) || !Number.isFinite(trig) || !Number.isFinite(dist)) return;

    const sig = `${sl.toFixed(1)}|${trig.toFixed(1)}|${dist.toFixed(1)}`;
    const serverChanged = sig !== lastServerRiskSignature;

    if (isDraggingRiskSlider) return;
    if (!serverChanged && Date.now() < riskSliderEditUntil) return;

    lastServerRiskSignature = sig;

    const rangeSl = document.getElementById('range-stop-loss');
    const rangeTrig = document.getElementById('range-trailing-trigger');
    const rangeDist = document.getElementById('range-trailing-dist');
    const dispSl = document.getElementById('disp-stop-loss');
    const dispTrig = document.getElementById('disp-trailing-trigger');
    const dispDist = document.getElementById('disp-trailing-dist');

    if (rangeSl) rangeSl.value = String(sl);
    if (rangeTrig) rangeTrig.value = String(trig);
    if (rangeDist) rangeDist.value = String(dist);
    if (dispSl) dispSl.textContent = `${sl.toFixed(1)}%`;
    if (dispTrig) dispTrig.textContent = `${trig.toFixed(1)}%`;
    if (dispDist) dispDist.textContent = `${dist.toFixed(1)}%`;
}

const fetchCapitalSummary = dedupeAsync(async function _fetchCapitalSummary() {
    try {
        const res = await fetch('/api/capital/summary');
        if (!res.ok) return;
        const data = await res.json();
        if (!data.success) return;

        state.environment = data.environment || 'homologation';
        state.activeGoal = data.active_goal || null;
        state.capitalSummary = data.active || data.capital || null;
        state.simulationSummary = data.simulation || data.capital || null;
        state.productionSummary = data.production || {
            total_equity: 0,
            ai_allocated_capital: 0,
            vault_reserve_cash: 0,
            broker_balance: 0
        };
        if (data.risk_params) {
            state.riskParams = data.risk_params;
            syncRiskSlidersFromServer(data.risk_params);
        }

        updateEnvironmentUI();
        renderKPIs(state);
        renderCapitalTab();
        renderActiveGoal();
    } catch (err) {}
});

function updateEnvironmentUI() {
    const isSim = (state.environment === 'homologation');
    const envPill = document.getElementById('global-env-pill');
    const envText = document.getElementById('global-env-text');
    const envDisplay = document.getElementById('env-current-display');
    const btnToggle = document.getElementById('btn-toggle-env');

    const tabSim = document.getElementById('nav-tab-sim');
    const tabCap = document.getElementById('nav-tab-cap');
    const vaultCard = document.getElementById('kpi-card-vault');

    if (envPill && envText) {
        if (isSim) {
            envPill.className = 'env-pill env-pill-sim';
            envText.textContent = '🟡 HOMOLOGAÇÃO';
        } else {
            envPill.className = 'env-pill env-pill-prod';
            envText.textContent = '🟢 PRODUÇÃO';
        }
    }

    if (envDisplay) {
        envDisplay.textContent = isSim ? '🟡 HOMOLOGAÇÃO' : '🟢 PRODUÇÃO';
        envDisplay.className = isSim ? 'highlight-sim' : 'highlight-equity';
    }

    if (btnToggle) {
        btnToggle.textContent = isSim ? 'Alternar para PRODUÇÃO' : 'Alternar para HOMOLOGAÇÃO';
        btnToggle.className = isSim ? 'btn-primary btn-full' : 'btn-warning btn-full';
    }

    if (tabSim && tabCap) {
        tabSim.style.display = isSim ? 'flex' : 'none';
        tabCap.style.display = isSim ? 'none' : 'flex';
    }

    if (vaultCard) {
        vaultCard.style.display = isSim ? 'none' : 'flex';
    }

    if ((isSim && state.activeTab === 'tab-capital') || (!isSim && state.activeTab === 'tab-simulation')) {
        activateTab('tab-overview');
    }

    const lbl1 = document.getElementById('kpi-label-1');
    const lbl2 = document.getElementById('kpi-label-2');
    if (lbl1) lbl1.textContent = isSim ? 'Patrimônio Simulado' : 'Patrimônio Total';
    if (lbl2) lbl2.textContent = isSim ? 'Caixa Simulado Livre' : 'Capital IA (Operacional)';
}

function renderKPIs(data) {
    const isSim = (state.environment === 'homologation');
    const elEquity = document.getElementById('val-equity');
    const elBalance = document.getElementById('val-balance');
    const elPnlTotal = document.getElementById('val-pnl-total');
    const elAllocPct = document.getElementById('val-alloc-pct');
    const elPosCount = document.getElementById('val-positions-count');
    const tabPosCount = document.getElementById('tab-pos-count');
    const elBuyingPower = document.getElementById('val-buying-power');
    const elVault = document.getElementById('val-vault-cash');

    const activeSummary = isSim
        ? (state.simulationSummary || state.capitalSummary)
        : (state.productionSummary || state.capitalSummary);

    const brokerBal = (activeSummary && activeSummary.broker_balance !== undefined && activeSummary.broker_balance !== null)
        ? Number(activeSummary.broker_balance)
        : 0.0;
    let displayEquity = (activeSummary && activeSummary.total_equity !== undefined && activeSummary.total_equity !== null)
        ? Number(activeSummary.total_equity)
        : Number((data && data.equity !== undefined) ? data.equity : (state.equity ?? 0));
    let displayBalance = activeSummary
        ? Number(isSim ? (activeSummary.free_cash ?? state.balance ?? 0) : (activeSummary.ai_allocated_capital ?? state.balance ?? 0))
        : Number((data && data.balance !== undefined) ? data.balance : (state.balance ?? 0));
    const displayVault = activeSummary
        ? Number(activeSummary.vault_reserve_cash !== undefined && activeSummary.vault_reserve_cash !== null
            ? activeSummary.vault_reserve_cash
            : Math.max(0, Math.max(displayEquity, brokerBal) - (displayBalance || 0)))
        : 0.0;
    const displayBuyingPower = activeSummary && activeSummary.buying_power !== undefined && activeSummary.buying_power !== null
        ? Number(activeSummary.buying_power)
        : (isSim ? displayBalance : 0.0);

    const subLbl2 = document.getElementById('kpi-sub-label-2');

    if (!isSim) {
        const accountCeiling = Math.max(brokerBal, displayEquity || 0);
        displayEquity = accountCeiling;
        displayBalance = Math.max(0, Math.min(displayBalance || 0, accountCeiling));
    }

    if (elEquity) elEquity.innerText = formatBRL(displayEquity);
    if (elBalance) elBalance.innerText = formatBRL(displayBalance);
    if (elVault) elVault.innerText = formatBRL(displayVault);

    if (elBuyingPower) {
        if (isSim) {
            if (subLbl2) subLbl2.innerText = 'Poder de Compra:';
            elBuyingPower.innerText = formatBRL(displayBuyingPower ?? 0);
        } else {
            if (subLbl2) subLbl2.innerText = 'Saldo na Clear:';
            elBuyingPower.innerText = formatBRL(brokerBal ?? 0);
        }
    }

    let totalPnl = 0;
    let totalCurrentPos = 0;
    state.positions.forEach(p => {
        const curr = Number(p.current_price ?? p.entry_price ?? 0);
        const entry = Number(p.entry_price ?? 0);
        const qty = Number(p.quantity ?? 0);
        totalPnl += (curr - entry) * qty;
        totalCurrentPos += curr * qty;
    });

    const baseCap = isSim
        ? (activeSummary ? Number(activeSummary.total_equity ?? state.equity ?? 0) : Number(state.equity ?? 0))
        : (activeSummary ? Number(activeSummary.ai_allocated_capital ?? state.equity ?? 0) : Number(state.equity ?? 0));
    const pnlPct = (baseCap && baseCap > 0) ? (totalPnl / baseCap) * 100 : 0;
    if (elPnlTotal) {
        const sign = totalPnl >= 0 ? '+' : '';
        elPnlTotal.innerText = `${sign}${formatBRL(totalPnl)} (${sign}${pnlPct.toFixed(2)}%)`;
        elPnlTotal.className = totalPnl >= 0 ? 'positive' : 'negative';
    }

    const allocatedAmount = (activeSummary && activeSummary.allocated_capital !== undefined && activeSummary.allocated_capital !== null)
        ? Number(activeSummary.allocated_capital)
        : totalCurrentPos;
    const allocPct = (displayEquity && displayEquity > 0)
        ? Math.min(100, (Math.max(0, allocatedAmount) / displayEquity) * 100)
        : 0;
    if (elAllocPct) {
        elAllocPct.innerText = `${Math.max(0, allocPct).toFixed(1)}% em Ações`;
    }

    const posLen = state.positions.length;
    if (elPosCount) elPosCount.innerText = `${posLen} ativos`;
    if (tabPosCount) tabPosCount.innerText = String(posLen);
}

function renderMarketStatus(status) {
    const badge = document.getElementById('badge-market');
    const text = document.getElementById('market-status-text');
    const isOpen = (status === 'ABERTO');

    if (badge && text) {
        text.innerText = isOpen ? 'B3: MERCADO ABERTO' : 'B3: MERCADO FECHADO';
        badge.className = isOpen ? 'status-pill market-pill' : 'status-pill market-pill closed';
    }
}

function renderPositions() {
    const tbody = document.getElementById('positionsTableBody');
    const subCount = document.getElementById('sub-pos-count');
    const elCost = document.getElementById('custody-total-cost');
    const elCurr = document.getElementById('custody-total-current');
    const elPnl = document.getElementById('custody-total-pnl');
    const isSim = (state.environment === 'homologation');

    if (!tbody) return;

    tbody.innerHTML = '';
    const filtered = state.positions.filter(p => {
        if (!state.searchTerm) return true;
        return String(p.ticker || '').toUpperCase().includes(state.searchTerm);
    });

    let totalCost = 0;
    let totalCurrent = 0;
    let totalPnl = 0;

    state.positions.forEach(p => {
        const curr = Number(p.current_price ?? p.entry_price ?? 0);
        const entry = Number(p.entry_price ?? 0);
        const qty = Number(p.quantity ?? 0);
        totalCost += (entry * qty);
        totalCurrent += (curr * qty);
        totalPnl += ((curr - entry) * qty);
    });

    const totalPnlPct = totalCost > 0 ? (totalPnl / totalCost) * 100 : 0;

    if (elCost) elCost.textContent = formatBRL(totalCost);
    if (elCurr) elCurr.textContent = formatBRL(totalCurrent);
    if (elPnl) {
        const sign = totalPnl >= 0 ? '+' : '';
        elPnl.textContent = `${sign}${formatBRL(totalPnl)} (${sign}${totalPnlPct.toFixed(2)}%)`;
        elPnl.className = `val ${totalPnl >= 0 ? 'positive' : 'negative'}`;
    }

    if (subCount) {
        const envLabel = isSim ? 'HOMOLOGAÇÃO / SIMULADO' : 'PRODUÇÃO / CLEAR MT5';
        subCount.textContent = `${state.positions.length} ATIVOS EM OPERAÇÃO (${envLabel})`;
    }

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="9" class="text-center text-muted" style="padding: 24px;">${state.searchTerm ? 'Nenhum ativo corresponde à busca.' : 'Nenhuma posição aberta sob custódia no momento.'}</td></tr>`;
        return;
    }

    filtered.forEach(pos => {
        const curr = Number(pos.current_price ?? pos.entry_price ?? 0);
        const entry = Number(pos.entry_price ?? 0);
        const qty = Number(pos.quantity ?? 0);
        const sl = Number(pos.stop_loss ?? 0);
        const pnl = (curr - entry) * qty;
        const pnlPct = entry > 0 ? ((curr - entry) / entry) * 100 : 0;
        const pnlClass = pnl >= 0 ? 'positive' : 'negative';

        const isTrailing = sl > 0 && sl > (entry * 0.95);
        const statusBadge = isTrailing
            ? '<span class="status-badge-inline trailing">🎯 TRAILING STOP</span>'
            : '<span class="status-badge-inline monitoring">🛡️ STOP LOSS</span>';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${escapeHtml(pos.ticker)}</strong></td>
            <td>${escapeHtml(qty)}</td>
            <td>${formatBRL(entry)}</td>
            <td><strong>${formatBRL(curr)}</strong></td>
            <td>${formatBRL(sl)}</td>
            <td class="${pnlClass}">${formatBRL(pnl)}</td>
            <td class="${pnlClass}">${formatPercent(pnlPct)}</td>
            <td>${formatBRL(curr * qty)}</td>
            <td>${statusBadge}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderChart(data) {
    const ctx = document.getElementById('allocationChart');
    const legendContainer = document.getElementById('allocationLegend');
    if (!ctx || typeof Chart === 'undefined') return;

    const labels = ['Caixa'];
    const values = [Math.max(0, Number(data.balance ?? 0))];
    const colors = ['#1e293b'];

    if (data.positions && data.positions.length > 0) {
        data.positions.forEach((pos, idx) => {
            const val = Math.max(0, Number(pos.current_price ?? pos.entry_price ?? 0) * Number(pos.quantity ?? 0));
            labels.push(String(pos.ticker || 'ATIVO'));
            values.push(val);
            const hue = (idx * 137.508) % 360;
            colors.push(`hsl(${hue}, 75%, 55%)`);
        });
    }

    if (allocationChart) {
        allocationChart.data.labels = labels;
        allocationChart.data.datasets[0].data = values;
        allocationChart.data.datasets[0].backgroundColor = colors;
        allocationChart.update();
    } else {
        allocationChart = new Chart(ctx, {
            type: 'doughnut',
            data: {
                labels: labels,
                datasets: [{
                    data: values,
                    backgroundColor: colors,
                    borderWidth: 1,
                    borderColor: 'rgba(255, 255, 255, 0.08)'
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '70%',
                plugins: {
                    legend: { display: false }
                }
            }
        });
    }

    if (legendContainer) {
        legendContainer.innerHTML = '';
        const total = values.reduce((a, b) => a + b, 0);
        labels.forEach((label, i) => {
            const val = values[i];
            const pct = total > 0 ? ((val / total) * 100).toFixed(1) : '0.0';
            const chip = document.createElement('div');
            chip.className = 'legend-chip';
            chip.innerHTML = `
                <span class="legend-dot" style="background-color: ${escapeHtml(colors[i])};"></span>
                <span>${escapeHtml(label)}: <strong>${escapeHtml(pct)}%</strong></span>
            `;
            legendContainer.appendChild(chip);
        });
    }
}

const fetchDecisions = dedupeAsync(async function _fetchDecisions() {
    try {
        const env = state.environment || 'homologation';
        const res = await fetch(`/api/decisions?limit=30&environment=${encodeURIComponent(env)}`);
        if (!res.ok) return;
        const data = await res.json();
        state.recentDecisions = Array.isArray(data.decisions) ? data.decisions : [];
        renderDecisions();
    } catch (err) {}
});

function renderDecisions() {
    const container = document.getElementById('decisionFeed');
    const fullContainer = document.getElementById('brainFeedFull');
    const badgeEnv = document.getElementById('badge-brain-env');
    const isSim = (state.environment === 'homologation');

    if (badgeEnv) {
        badgeEnv.textContent = isSim ? 'MODO ATIVO: HOMOLOGAÇÃO (SIMULADOR)' : 'MODO ATIVO: PRODUÇÃO (B3 / CLEAR)';
    }

    if (!container && !fullContainer) return;
    if (container) container.innerHTML = '';
    if (fullContainer) fullContainer.innerHTML = '';

    if (!state.recentDecisions || state.recentDecisions.length === 0) {
        const empty = `<div class="loading-state">Nenhuma deliberação registrada para o modo <strong>${isSim ? 'HOMOLOGAÇÃO' : 'PRODUÇÃO'}</strong> ainda.</div>`;
        if (container) container.innerHTML = empty;
        if (fullContainer) fullContainer.innerHTML = empty;
        return;
    }

    state.recentDecisions.forEach(dec => {
        const actionNorm = (dec.action === 'COMPRA' || dec.action === 'BUY') ? 'BUY' :
                           (dec.action === 'AGUARDAR' || dec.action === 'HOLD') ? 'HOLD' : 'SELL';
        const actionText = (dec.action === 'COMPRA' || dec.action === 'BUY') ? 'COMPRA' :
                           (dec.action === 'AGUARDAR' || dec.action === 'HOLD') ? 'AGUARDAR / MANTER' : 'VENDA / STOP';
        const decEnv = (dec.environment || (dec.is_simulated ? 'homologation' : 'production')).toUpperCase();
        const confPct = Number.isFinite(Number(dec.confidence)) ? (Number(dec.confidence) * 100).toFixed(0) : '0';

        const card = document.createElement('div');
        card.className = `decision-card ${actionNorm}`;
        card.innerHTML = `
            <div class="decision-header">
                <span class="decision-ticker">${escapeHtml(dec.ticker)} <small style="font-size: 0.68rem; opacity: 0.75; font-weight: normal; margin-left: 4px;">[${escapeHtml(decEnv)}]</small></span>
                <span class="action-badge action-${actionNorm}">${actionText}</span>
            </div>
            <div class="decision-reasoning">${escapeHtml(dec.reasoning || 'Avaliação quantitativa de convergência técnica e sentimento em andamento.')}</div>
            <div class="decision-time">${escapeHtml(formatDate(dec.timestamp))} — Confiança: ${escapeHtml(confPct)}%</div>
        `;

        if (container) container.appendChild(card);
        if (fullContainer) fullContainer.appendChild(card.cloneNode(true));
    });
}

let newsFetchInFlight = false;
async function fetchNews(isManual = false) {
    if (newsFetchInFlight && !isManual) return;
    newsFetchInFlight = true;
    const btnRef = document.getElementById('btn-refresh-news');
    if (btnRef && isManual) btnRef.textContent = '🔄 Atualizando...';
    try {
        const res = await fetch('/api/news?limit=25');
        if (!res.ok) return;
        const data = await res.json();
        state.news = Array.isArray(data) ? data : (data.news || []);
        renderNews();
        if (isManual) showToast('Radar de Notícias atualizado com sucesso!', 'success');
    } catch (err) {
        if (isManual) showToast('Erro ao atualizar notícias', 'error');
    } finally {
        newsFetchInFlight = false;
        if (btnRef && isManual) btnRef.textContent = '🔄 Atualizar Notícias Agora';
    }
}

function renderNews() {
    const feed = document.getElementById('newsList');
    if (!feed) return;
    feed.innerHTML = '';

    if (!state.news || state.news.length === 0) {
        feed.innerHTML = '<div class="loading-state">Nenhuma notícia financeira recente encontrada no momento. Clique em Atualizar para buscar novos dados.</div>';
        return;
    }

    state.news.forEach(item => {
        const a = document.createElement('a');
        a.className = 'news-item';
        const safeHref = sanitizeHttpUrl(item.url);
        a.href = safeHref;
        if (safeHref !== '#') {
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
        } else {
            a.addEventListener('click', (e) => e.preventDefault());
        }

        const sentimentBadge = item.sentiment !== undefined && item.sentiment !== null
            ? (item.sentiment > 0.15
                ? '<span class="status-badge-inline trailing" style="font-size: 0.68rem; margin-left: 6px;">🟢 POSITIVO</span>'
                : item.sentiment < -0.15
                    ? '<span class="status-badge-inline" style="background: rgba(244, 63, 94, 0.2); color: #fb7185; font-size: 0.68rem; margin-left: 6px;">🔴 NEGATIVO</span>'
                    : '<span class="status-badge-inline" style="background: rgba(148, 163, 184, 0.2); color: #94a3b8; font-size: 0.68rem; margin-left: 6px;">⚪ NEUTRO</span>')
            : '';

        a.innerHTML = `
            <div class="news-title">${escapeHtml(item.title)} ${sentimentBadge}</div>
            <div class="news-meta">
                <span class="news-source">${escapeHtml(item.source || 'B3')}</span>
                ${item.ticker ? `<span style="font-family: monospace; font-weight: bold; color: #93c5fd;">$${escapeHtml(item.ticker)}</span>` : ''}
                <span>${escapeHtml(formatDate(item.published_at || item.scraped_at))}</span>
            </div>
        `;
        feed.appendChild(a);
    });
}

function getMaxAllocatableCapital() {
    const prod = state.productionSummary || {};
    const totalEq = Number(prod.total_equity ?? 0);
    const brokerBal = Number(prod.broker_balance ?? 0);
    return Math.max(
        0,
        Number.isFinite(totalEq) ? totalEq : 0,
        Number.isFinite(brokerBal) ? brokerBal : 0
    );
}

function renderCapitalTab() {
    const prod = state.productionSummary || {
        total_equity: 0,
        ai_allocated_capital: 0,
        vault_reserve_cash: 0,
        broker_balance: 0
    };
    const brokerBal = (prod.broker_balance !== undefined && prod.broker_balance !== null)
        ? Number(prod.broker_balance)
        : Number(prod.total_equity ?? 0);
    const maxCap = getMaxAllocatableCapital();
    const aiCap = Math.max(0, Math.min(Number(prod.ai_allocated_capital ?? 0), maxCap));
    const vaultCash = (prod.vault_reserve_cash !== undefined && prod.vault_reserve_cash !== null)
        ? Math.max(0, Number(prod.vault_reserve_cash))
        : Math.max(0, maxCap - aiCap);

    const elBroker = document.getElementById('cap-broker-balance');
    const elTxtBroker = document.getElementById('txt-broker-avail');
    const aiEl = document.getElementById('cap-manage-ai');
    const vaultEl = document.getElementById('cap-manage-vault');
    const badgeAi = document.getElementById('badge-ai-status');
    const capHint = document.getElementById('cap-ai-hint');

    if (elBroker) elBroker.textContent = formatBRL(maxCap > 0 ? maxCap : brokerBal);
    if (elTxtBroker) elTxtBroker.textContent = formatBRL(maxCap);
    if (aiEl) aiEl.textContent = formatBRL(aiCap);
    if (vaultEl) vaultEl.textContent = formatBRL(vaultCash);

    if (badgeAi) {
        if (aiCap <= 0) {
            badgeAi.className = 'status-badge-inline monitoring';
            badgeAi.textContent = '🟡 IA ZERADA (EM ESPERA)';
            if (capHint) capHint.textContent = 'Aguardando definição manual para iniciar compras';
        } else {
            badgeAi.className = 'status-badge-inline trailing';
            badgeAi.textContent = `🟢 IA OPERANDO (${formatBRL(aiCap)})`;
            if (capHint) capHint.textContent = `Limite máximo operacional autorizado: ${formatBRL(aiCap)}`;
        }
    }

    const fmtShort = (v) => 'R$ ' + (Number.isInteger(v) ? v.toFixed(0) : v.toFixed(2).replace('.', ','));
    const pill25 = document.querySelector('[data-set-ai="pct-25"]');
    const pill50 = document.querySelector('[data-set-ai="pct-50"]');
    const pill75 = document.querySelector('[data-set-ai="pct-75"]');
    if (pill25) pill25.textContent = `25% (${fmtShort(maxCap * 0.25)})`;
    if (pill50) pill50.textContent = `50% (${fmtShort(maxCap * 0.50)})`;
    if (pill75) pill75.textContent = `75% (${fmtShort(maxCap * 0.75)})`;

    updateWithdrawalPreview();
}

function updateWithdrawalPreview() {
    const previewText = document.getElementById('goal-preview-text');
    const inputAmt = document.getElementById('input-withdraw-amount');
    const mode = document.querySelector('input[name="withdrawal-mode"]:checked')?.value || 'on_profit';
    const prod = state.productionSummary || {};
    const maxCap = getMaxAllocatableCapital();
    const aiCap = Math.max(0, Math.min(Number(prod.ai_allocated_capital ?? 0), maxCap));
    const amt = parseFloat(inputAmt?.value) || 5.0;

    if (!previewText) return;

    if (mode === 'on_profit') {
        const targetEq = aiCap + amt;
        previewText.innerHTML = `Quando a IA gerar lucro e o patrimônio atingir <strong>${escapeHtml(formatBRL(targetEq))}</strong> (Teto IA ${escapeHtml(formatBRL(aiCap))} + Lucro ${escapeHtml(formatBRL(amt))}), o lucro de <strong>${escapeHtml(formatBRL(amt))}</strong> será automaticamente bloqueado no cofre seguro.`;
    } else {
        previewText.innerHTML = `Transfere <strong>${escapeHtml(formatBRL(amt))}</strong> imediatamente para o cofre seguro (reduzindo o teto da IA se necessário), impedindo novas ordens desse montante.`;
    }
}

function renderActiveGoal() {
    const box = document.getElementById('active-goal-box');
    if (!box) return;

    if (!state.activeGoal) {
        box.style.display = 'none';
        return;
    }

    box.style.display = 'flex';
    const g = state.activeGoal;
    const amtEl = document.getElementById('active-goal-amount');
    const tgtEl = document.getElementById('active-goal-target');
    const bar = document.getElementById('goal-progress-bar');

    if (amtEl) amtEl.textContent = formatBRL(g.amount);
    if (tgtEl) tgtEl.textContent = formatBRL(g.target_equity);

    const curEq = Number(state.equity ?? 0);
    const base = Number(g.base_equity ?? curEq);
    const tgt = Number(g.target_equity ?? (base + Number(g.amount ?? 0)));
    let pct = 0;
    if (tgt > base) {
        pct = Math.max(0, Math.min(100, ((curEq - base) / (tgt - base)) * 100));
    }
    if (bar) bar.style.width = `${pct}%`;
}

function setTxFilterUI(env) {
    currentTxEnv = env;
    const btnTxProd = document.getElementById('btn-filter-tx-prod');
    const btnTxSim = document.getElementById('btn-filter-tx-sim');
    const badgeTxEnv = document.getElementById('badge-tx-env-label');
    if (btnTxProd && btnTxSim) {
        if (env === 'production') {
            btnTxProd.className = 'btn-chip btn-chip-highlight';
            btnTxSim.className = 'btn-chip';
            if (badgeTxEnv) badgeTxEnv.textContent = 'MODO SELECIONADO: PRODUÇÃO (B3)';
        } else {
            btnTxSim.className = 'btn-chip btn-chip-highlight';
            btnTxProd.className = 'btn-chip';
            if (badgeTxEnv) badgeTxEnv.textContent = 'MODO SELECIONADO: HOMOLOGAÇÃO (SIMULADOR)';
        }
    }
}

function refreshCapitalTransactions() {
    return fetchCapitalTransactions(currentTxEnv);
}

function setupCapitalControls() {
    const inputSim = document.getElementById('input-sim-capital');
    const btnSim = document.getElementById('btn-apply-sim-cap');
    const simChips = document.querySelectorAll('[data-set-sim]');
    const btnResetSim = document.getElementById('btn-reset-simulation');
    const modalReset = document.getElementById('modal-reset-sim-confirm');
    const btnCancelReset = document.getElementById('btn-cancel-reset-sim');
    const btnConfirmReset = document.getElementById('btn-confirm-reset-sim');

    simChips.forEach(chip => {
        chip.addEventListener('click', () => {
            if (inputSim) inputSim.value = chip.dataset.setSim;
        });
    });

    if (btnSim && inputSim) {
        btnSim.addEventListener('click', async () => {
            const val = parseFloat(inputSim.value);
            if (!Number.isFinite(val) || val <= 0) {
                showToast('Insira um valor válido para o capital de simulação.', 'error');
                return;
            }
            btnSim.disabled = true;
            try {
                const res = await fetch('/api/config/simulator/capital', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ capital: val, reset_positions: false })
                });
                const d = await res.json();
                if (res.ok && d.success) {
                    showToast(`Capital de simulação atualizado para ${formatBRL(val)}`, 'success');
                    await Promise.all([
                        fetchStatus(),
                        fetchCapitalSummary(),
                        refreshCapitalTransactions()
                    ]);
                } else {
                    showToast(d.detail || d.message || 'Erro ao atualizar capital de simulação.', 'error');
                }
            } catch (e) {
                showToast('Erro ao atualizar capital de simulação.', 'error');
            } finally {
                btnSim.disabled = false;
            }
        });
    }

    if (btnResetSim && modalReset) {
        btnResetSim.addEventListener('click', () => { modalReset.style.display = 'flex'; });
        if (btnCancelReset) btnCancelReset.addEventListener('click', () => { modalReset.style.display = 'none'; });
        if (btnConfirmReset) {
            btnConfirmReset.addEventListener('click', async () => {
                btnConfirmReset.disabled = true;
                try {
                    const res = await fetch('/api/config/simulator/reset', { method: 'POST' });
                    const d = await res.json();
                    if (res.ok && d.success) {
                        modalReset.style.display = 'none';
                        showToast('Simulação resetada do zero!', 'success');
                        await Promise.all([
                            fetchStatus(),
                            fetchCapitalSummary(),
                            fetchDecisions(),
                            refreshCapitalTransactions()
                        ]);
                    } else {
                        showToast(d.detail || d.message || 'Erro ao resetar simulação.', 'error');
                    }
                } catch (e) {
                    showToast('Erro ao resetar simulação.', 'error');
                } finally {
                    btnConfirmReset.disabled = false;
                }
            });
        }
    }

    // Controles de Capital de Produção
    const inputAi = document.getElementById('input-new-ai-cap');
    const btnAi = document.getElementById('btn-apply-ai-cap');
    const btnUseMax = document.getElementById('btn-use-broker-max');
    const hintAiVal = document.getElementById('hint-ai-cap-validation');
    const aiChips = document.querySelectorAll('[data-set-ai]');

    aiChips.forEach(chip => {
        chip.addEventListener('click', () => {
            const valType = chip.dataset.setAi;
            const bBal = getMaxAllocatableCapital();
            if (valType === '0') {
                if (inputAi) inputAi.value = '0.00';
            } else if (valType === 'pct-25') {
                if (inputAi) inputAi.value = (bBal * 0.25).toFixed(2);
            } else if (valType === 'pct-50') {
                if (inputAi) inputAi.value = (bBal * 0.50).toFixed(2);
            } else if (valType === 'pct-75') {
                if (inputAi) inputAi.value = (bBal * 0.75).toFixed(2);
            }
            if (inputAi) inputAi.dispatchEvent(new Event('input'));
        });
    });

    if (btnUseMax && inputAi) {
        btnUseMax.addEventListener('click', () => {
            const bBal = getMaxAllocatableCapital();
            inputAi.value = bBal.toFixed(2);
            inputAi.dispatchEvent(new Event('input'));
        });
    }

    if (inputAi) {
        inputAi.addEventListener('input', () => {
            const bBal = getMaxAllocatableCapital();
            const val = parseFloat(inputAi.value) || 0;
            if (val > bBal) {
                inputAi.style.borderColor = '#f43f5e';
                if (hintAiVal) {
                    hintAiVal.style.display = 'block';
                    hintAiVal.style.color = '#fb7185';
                    hintAiVal.textContent = `⚠️ O teto da IA (${formatBRL(val)}) não pode ser maior que o patrimônio na Clear (${formatBRL(bBal)})!`;
                }
            } else {
                inputAi.style.borderColor = '';
                if (hintAiVal) {
                    hintAiVal.style.display = 'none';
                }
            }
        });
    }

    if (btnAi && inputAi) {
        btnAi.addEventListener('click', async () => {
            const val = parseFloat(inputAi.value);
            const bBal = getMaxAllocatableCapital();
            if (!Number.isFinite(val) || val < 0) {
                showToast('Insira um valor numérico válido.', 'error');
                return;
            }
            if (val > bBal) {
                showToast(`O teto de capital da IA (${formatBRL(val)}) não pode exceder o patrimônio na Clear (${formatBRL(bBal)})!`, 'error');
                return;
            }
            btnAi.disabled = true;
            try {
                const res = await fetch('/api/capital/adjust-allocation', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ai_capital: val })
                });
                const d = await res.json();
                if (res.ok && d.success) {
                    showToast(d.message || `Teto de capital da IA ajustado para ${formatBRL(val)}`, 'success');
                    await Promise.all([
                        fetchStatus(),
                        fetchCapitalSummary(),
                        refreshCapitalTransactions()
                    ]);
                } else {
                    showToast(d.detail || d.message || 'Erro ao ajustar teto de capital.', 'error');
                }
            } catch (e) {
                showToast('Erro ao ajustar teto de capital.', 'error');
            } finally {
                btnAi.disabled = false;
            }
        });
    }

    // Controles de Saque de Produção
    const btnGoal = document.getElementById('btn-create-withdrawal-goal');
    const inputWithdraw = document.getElementById('input-withdraw-amount');
    const btnCancelGoal = document.getElementById('btn-cancel-goal');
    const withdrawChips = document.querySelectorAll('[data-set-withdraw]');
    const modeRadios = document.querySelectorAll('input[name="withdrawal-mode"]');
    const lblWithdraw = document.getElementById('lbl-withdraw-amount');

    withdrawChips.forEach(chip => {
        chip.addEventListener('click', () => {
            if (inputWithdraw) {
                inputWithdraw.value = chip.dataset.setWithdraw;
                updateWithdrawalPreview();
            }
        });
    });

    modeRadios.forEach(radio => {
        radio.addEventListener('change', () => {
            document.querySelectorAll('.radio-label').forEach(l => l.classList.remove('active'));
            radio.closest('.radio-label')?.classList.add('active');
            if (lblWithdraw) {
                lblWithdraw.textContent = (radio.value === 'on_profit')
                    ? 'Valor do Lucro a Guardar no Cofre (R$):'
                    : 'Valor do Saque Imediato para o Cofre (R$):';
            }
            if (btnGoal) {
                btnGoal.textContent = (radio.value === 'on_profit')
                    ? '🛡️ Programar Saque no Lucro'
                    : '⚡ Executar Saque Imediato';
            }
            updateWithdrawalPreview();
        });
    });

    if (inputWithdraw) {
        inputWithdraw.addEventListener('input', updateWithdrawalPreview);
    }

    if (btnGoal && inputWithdraw) {
        btnGoal.addEventListener('click', async () => {
            const val = parseFloat(inputWithdraw.value);
            if (!Number.isFinite(val) || val <= 0) {
                showToast('Insira um valor de saque válido.', 'error');
                return;
            }
            const mode = document.querySelector('input[name="withdrawal-mode"]:checked')?.value || 'on_profit';
            btnGoal.disabled = true;
            try {
                const res = await fetch('/api/capital/withdrawal-goal', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ amount: val, mode })
                });
                const d = await res.json();
                if (res.ok && d.success) {
                    showToast(d.message || 'Operação de saque processada com sucesso!', 'success');
                    inputWithdraw.value = '';
                    await Promise.all([
                        fetchStatus(),
                        fetchCapitalSummary(),
                        refreshCapitalTransactions()
                    ]);
                } else {
                    showToast(d.detail || d.message || 'Erro ao processar saque.', 'error');
                }
            } catch (e) {
                showToast('Erro ao programar saque.', 'error');
            } finally {
                btnGoal.disabled = false;
            }
        });
    }

    if (btnCancelGoal) {
        btnCancelGoal.addEventListener('click', async () => {
            if (!confirm('Deseja cancelar a meta de saque ativa?')) return;
            try {
                const res = await fetch('/api/capital/withdrawal-goal/cancel', { method: 'POST' });
                const d = await res.json();
                if (res.ok && d.success) {
                    showToast(d.message || 'Meta de saque cancelada.', 'info');
                }
                await Promise.all([
                    fetchCapitalSummary(),
                    refreshCapitalTransactions()
                ]);
            } catch (e) {
                showToast('Erro ao cancelar meta de saque.', 'error');
            }
        });
    }

    const rangeSl = document.getElementById('range-stop-loss');
    const rangeTrig = document.getElementById('range-trailing-trigger');
    const rangeDist = document.getElementById('range-trailing-dist');
    const dispSl = document.getElementById('disp-stop-loss');
    const dispTrig = document.getElementById('disp-trailing-trigger');
    const dispDist = document.getElementById('disp-trailing-dist');
    const btnSaveRisk = document.getElementById('btn-save-risk');

    const attachSliderTracking = (slider, dispEl) => {
        if (!slider) return;
        const startDrag = () => {
            isDraggingRiskSlider = true;
            riskSliderEditUntil = Date.now() + 15000;
        };
        const endDrag = () => {
            isDraggingRiskSlider = false;
            riskSliderEditUntil = Date.now() + 15000;
        };
        slider.addEventListener('pointerdown', startDrag);
        slider.addEventListener('mousedown', startDrag);
        slider.addEventListener('touchstart', startDrag, { passive: true });
        slider.addEventListener('pointerup', endDrag);
        slider.addEventListener('mouseup', endDrag);
        slider.addEventListener('touchend', endDrag, { passive: true });
        slider.addEventListener('blur', () => { isDraggingRiskSlider = false; });
        slider.addEventListener('input', () => {
            riskSliderEditUntil = Date.now() + 15000;
            if (dispEl) dispEl.textContent = `${parseFloat(slider.value).toFixed(1)}%`;
        });
    };

    attachSliderTracking(rangeSl, dispSl);
    attachSliderTracking(rangeTrig, dispTrig);
    attachSliderTracking(rangeDist, dispDist);

    if (btnSaveRisk && rangeSl && rangeTrig && rangeDist) {
        btnSaveRisk.addEventListener('click', async () => {
            const slVal = parseFloat(rangeSl.value);
            const trigVal = parseFloat(rangeTrig.value);
            const distVal = parseFloat(rangeDist.value);

            if (distVal >= trigVal) {
                showToast('A distância do Trailing Stop deve ser menor que o gatilho de ativação.', 'error');
                return;
            }

            btnSaveRisk.disabled = true;
            try {
                const res = await fetch('/api/config/risk', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        stop_loss_percent: slVal,
                        trailing_trigger_percent: trigVal,
                        trailing_distance_percent: distVal
                    })
                });
                const d = await res.json();
                if (res.ok && d.success) {
                    isDraggingRiskSlider = false;
                    riskSliderEditUntil = 0;
                    showToast('Parâmetros de risco salvos com sucesso!', 'success');
                    await fetchCapitalSummary();
                } else {
                    showToast(d.detail || d.message || 'Erro ao salvar parâmetros de risco.', 'error');
                }
            } catch (e) {
                showToast('Erro ao salvar parâmetros de risco.', 'error');
            } finally {
                btnSaveRisk.disabled = false;
            }
        });
    }

    const btnToggleEnv = document.getElementById('btn-toggle-env');
    const modalProd = document.getElementById('modal-prod-confirm');
    const btnCancelProd = document.getElementById('btn-cancel-prod-switch');
    const btnConfirmProd = document.getElementById('btn-confirm-prod-switch');

    if (btnToggleEnv && modalProd) {
        btnToggleEnv.addEventListener('click', () => {
            if (state.environment === 'homologation') {
                modalProd.style.display = 'flex';
            } else {
                switchEnv('homologation');
            }
        });

        if (btnCancelProd) btnCancelProd.addEventListener('click', () => { modalProd.style.display = 'none'; });
        if (btnConfirmProd) {
            btnConfirmProd.addEventListener('click', () => {
                modalProd.style.display = 'none';
                switchEnv('production');
            });
        }
    }

    async function switchEnv(target) {
        try {
            const res = await fetch('/api/config/environment', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ environment: target })
            });
            const d = await res.json();
            if (res.ok && d.success) {
                state.environment = target;
                updateEnvironmentUI();
                setTxFilterUI(target);
                showToast(`Ambiente alterado para ${target === 'production' ? 'PRODUÇÃO' : 'HOMOLOGAÇÃO'}`, 'success');
                await Promise.all([
                    fetchCapitalSummary(),
                    fetchStatus(),
                    fetchDecisions(),
                    refreshCapitalTransactions()
                ]);
            } else {
                showToast(d.detail || d.message || 'Erro ao alternar ambiente.', 'error');
            }
        } catch (e) {
            showToast('Erro ao alternar ambiente.', 'error');
        }
    }
}

async function fetchInitialLogs() {
    const terminal = document.getElementById('terminalLogs');
    if (!terminal) return;
    try {
        const res = await fetch('/api/logs?limit=120');
        if (!res.ok) return;
        const data = await res.json();
        if (data.logs && Array.isArray(data.logs)) {
            terminal.innerHTML = '';
            data.logs.forEach(logText => {
                appendLogLine(logText);
            });
        }
    } catch (e) {}
}

const MAX_TERMINAL_LINES = 300;

function appendLogLine(logText) {
    const terminal = document.getElementById('terminalLogs');
    if (!terminal || state.isPausedLogs) return;
    const text = String(logText ?? '');
    const line = document.createElement('div');
    line.className = 'log-line';
    line.textContent = text;
    if (text.includes('[ERROR]') || text.includes('CRITICAL')) line.style.color = '#fb7185';
    else if (text.includes('[WARNING]') || text.includes('WARN')) line.style.color = '#fbbf24';
    else if (text.includes('[INFO]')) line.style.color = '#93c5fd';
    else if (text.includes('[DEBUG]')) line.style.color = '#94a3b8';

    while (terminal.childElementCount >= MAX_TERMINAL_LINES) {
        terminal.removeChild(terminal.firstElementChild);
    }
    terminal.appendChild(line);
    terminal.scrollTop = terminal.scrollHeight;
}

function setupWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/logs`;
    const terminal = document.getElementById('terminalLogs');
    const btnPause = document.getElementById('btn-pause-logs');
    const btnClear = document.getElementById('btn-clear-logs');
    const btnReload = document.getElementById('btn-reload-logs');
    const wsBadge = document.getElementById('badge-ws-status');

    if (btnPause) {
        btnPause.addEventListener('click', () => {
            state.isPausedLogs = !state.isPausedLogs;
            btnPause.innerText = state.isPausedLogs ? '▶️' : '⏸️';
            btnPause.title = state.isPausedLogs ? 'Retomar Logs' : 'Pausar Logs';
            btnPause.style.background = state.isPausedLogs ? 'rgba(245, 158, 11, 0.2)' : '';
        });
    }

    if (btnClear && terminal) {
        btnClear.addEventListener('click', () => { terminal.innerHTML = ''; });
    }

    if (btnReload) {
        btnReload.addEventListener('click', () => {
            fetchInitialLogs();
            showToast('Logs recarregados do buffer com sucesso!', 'info');
        });
    }

    fetchInitialLogs();

    let ws = null;
    let reconnectTimer = null;

    function connectWs() {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;

        if (ws) {
            ws.onopen = null;
            ws.onmessage = null;
            ws.onclose = null;
            ws.onerror = null;
            try { ws.close(); } catch (e) {}
            ws = null;
        }

        try {
            ws = new WebSocket(wsUrl);
            ws.onopen = () => {
                if (wsBadge) {
                    wsBadge.textContent = '🟢 CONECTADO';
                    wsBadge.className = 'status-badge-inline trailing';
                }
            };
            ws.onmessage = (event) => {
                appendLogLine(event.data);
            };
            ws.onclose = (event) => {
                if (event && event.code === 1008) {
                    clearTimeout(reconnectTimer);
                    window.location.href = '/login?reason=expired';
                    return;
                }
                if (wsBadge) {
                    wsBadge.textContent = '🔴 RECONECTANDO...';
                    wsBadge.className = 'status-badge-inline';
                }
                clearTimeout(reconnectTimer);
                reconnectTimer = setTimeout(connectWs, 3000);
            };
            ws.onerror = () => {
                try { ws.close(); } catch (e) {}
            };
        } catch (e) {
            clearTimeout(reconnectTimer);
            reconnectTimer = setTimeout(connectWs, 5000);
        }
    }

    connectWs();
}

let glossaryDataCache = [];

function setupGuideWidget() {
    const btnOpen = document.getElementById('btn-open-guide');
    const widget = document.getElementById('guide-floating-widget');
    const btnClose = document.getElementById('btn-close-guide');
    const btnMinimize = document.getElementById('btn-minimize-guide');

    if (btnOpen && widget) {
        btnOpen.addEventListener('click', () => {
            const isShown = widget.style.display === 'flex';
            widget.style.display = isShown ? 'none' : 'flex';
            if (!isShown) {
                const input = document.getElementById('inputGuideChat');
                if (input) input.focus();
                loadGlossaryOnce();
            }
        });
    }

    if (btnClose && widget) btnClose.addEventListener('click', () => { widget.style.display = 'none'; });
    if (btnMinimize && widget) btnMinimize.addEventListener('click', () => { widget.style.display = 'none'; });

    const guideTabs = document.querySelectorAll('.guide-tab');
    const guidePanes = document.querySelectorAll('.guide-pane');

    guideTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            guideTabs.forEach(t => t.classList.remove('active'));
            guidePanes.forEach(p => p.classList.remove('active'));
            tab.classList.add('active');
            const target = document.getElementById(tab.dataset.guideTab);
            if (target) target.classList.add('active');
            if (tab.dataset.guideTab === 'guide-glossary-pane') loadGlossaryOnce();
        });
    });

    const inputChat = document.getElementById('inputGuideChat');
    const btnSend = document.getElementById('btnSendGuideChat');

    if (btnSend && inputChat) {
        btnSend.addEventListener('click', () => sendGuideMessage());
        inputChat.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                sendGuideMessage();
            }
        });
    }

    document.querySelectorAll('.suggestion-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            if (inputChat) {
                inputChat.value = chip.dataset.question;
                sendGuideMessage();
            }
        });
    });

    const glossSearch = document.getElementById('glossarySearchInput');
    if (glossSearch) {
        glossSearch.addEventListener('input', (e) => {
            filterGlossary(e.target.value);
        });
    }
}

async function sendGuideMessage() {
    const input = document.getElementById('inputGuideChat');
    const stream = document.getElementById('guideChatStream');
    if (!input || !stream) return;

    const text = input.value.trim();
    if (!text) return;

    input.value = '';

    const userDiv = document.createElement('div');
    userDiv.className = 'chat-msg user-msg';
    userDiv.innerHTML = `
        <div class="msg-avatar">👤</div>
        <div class="msg-bubble">${escapeHtml(text)}</div>
    `;
    stream.appendChild(userDiv);
    stream.scrollTop = stream.scrollHeight;

    const botDiv = document.createElement('div');
    botDiv.className = 'chat-msg bot-msg';
    botDiv.innerHTML = `
        <div class="msg-avatar">🤖</div>
        <div class="msg-bubble"><em>Pensando...</em></div>
    `;
    stream.appendChild(botDiv);
    stream.scrollTop = stream.scrollHeight;

    try {
        const res = await fetch('/api/guide/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text })
        });
        const data = await res.json();

        if (res.ok && data.success && data.answer) {
            botDiv.querySelector('.msg-bubble').innerHTML = formatMarkdownText(data.answer);

            if (data.suggestions && data.suggestions.length > 0) {
                const suggWrap = document.getElementById('guideSuggestions');
                if (suggWrap) {
                    suggWrap.innerHTML = '';
                    data.suggestions.forEach(s => {
                        const btn = document.createElement('button');
                        btn.className = 'suggestion-chip';
                        btn.textContent = s;
                        btn.dataset.question = s;
                        btn.addEventListener('click', () => {
                            input.value = s;
                            sendGuideMessage();
                        });
                        suggWrap.appendChild(btn);
                    });
                }
            }
        } else {
            botDiv.querySelector('.msg-bubble').textContent = 'Desculpe, não consegui processar a resposta no momento.';
        }
    } catch (e) {
        botDiv.querySelector('.msg-bubble').textContent = 'Erro ao conectar com o assistente.';
    }
    stream.scrollTop = stream.scrollHeight;
}

async function loadGlossaryOnce() {
    if (glossaryDataCache.length > 0) return;
    const list = document.getElementById('glossaryList');
    try {
        const res = await fetch('/api/guide/glossary');
        const data = await res.json();
        if (res.ok && data.success && Array.isArray(data.glossary)) {
            glossaryDataCache = data.glossary;
            renderGlossary(glossaryDataCache);
        }
    } catch (e) {
        if (list) list.innerHTML = '<div class="loading-state">Erro ao carregar glossário.</div>';
    }
}

function renderGlossary(items) {
    const list = document.getElementById('glossaryList');
    if (!list) return;
    list.innerHTML = '';

    if (!items || items.length === 0) {
        list.innerHTML = '<div class="loading-state">Nenhum termo encontrado.</div>';
        return;
    }

    items.forEach(item => {
        const card = document.createElement('div');
        card.className = 'glossary-item-card';
        card.innerHTML = `
            <div class="glossary-item-header">
                <span class="glossary-item-term">${escapeHtml(item.term)}</span>
                <span class="glossary-item-cat">${escapeHtml(item.category)}</span>
            </div>
            <div class="glossary-item-short">${escapeHtml(item.short_def)}</div>
            <div class="glossary-item-details">${escapeHtml(item.details)}</div>
        `;
        list.appendChild(card);
    });
}

function filterGlossary(query) {
    const q = (query || '').toLowerCase().trim();
    if (!q) {
        renderGlossary(glossaryDataCache);
        return;
    }
    const filtered = glossaryDataCache.filter(item =>
        String(item.term || '').toLowerCase().includes(q) ||
        String(item.category || '').toLowerCase().includes(q) ||
        String(item.short_def || '').toLowerCase().includes(q) ||
        String(item.details || '').toLowerCase().includes(q)
    );
    renderGlossary(filtered);
}

function formatMarkdownText(md) {
    if (!md) return '';
    let html = escapeHtml(md);
    html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.*?)\*/g, '<em>$1</em>');
    html = html.replace(/`(.*?)`/g, '<code style="background: rgba(255,255,255,0.1); padding: 2px 4px; border-radius: 4px; font-family: monospace;">$1</code>');
    html = html.replace(/\n/g, '<br>');
    return html;
}

document.addEventListener('DOMContentLoaded', async () => {
    const authenticated = await checkAuthAndSetupUser();
    if (!authenticated) return;

    const btnSyncBroker = document.getElementById('btn-sync-broker-bal');
    if (btnSyncBroker) {
        btnSyncBroker.addEventListener('click', async () => {
            btnSyncBroker.classList.add('rotating');
            await fetchCapitalSummary();
            await fetchStatus();
            await refreshCapitalTransactions();
            setTimeout(() => btnSyncBroker.classList.remove('rotating'), 600);
            showToast('Saldo da Corretora atualizado com sucesso!', 'success');
        });
    }

    const btnRefNews = document.getElementById('btn-refresh-news');
    if (btnRefNews) {
        btnRefNews.addEventListener('click', () => fetchNews(true));
    }

    const btnRefBrain = document.getElementById('btn-refresh-brain');
    if (btnRefBrain) {
        btnRefBrain.addEventListener('click', async () => {
            btnRefBrain.textContent = '🔄 Atualizando...';
            await fetchDecisions();
            btnRefBrain.textContent = '🔄 Atualizar Deliberações';
            showToast('Deliberações quantitativas atualizadas!', 'success');
        });
    }

    const btnTxProd = document.getElementById('btn-filter-tx-prod');
    const btnTxSim = document.getElementById('btn-filter-tx-sim');

    if (btnTxProd && btnTxSim) {
        btnTxProd.addEventListener('click', () => {
            setTxFilterUI('production');
            fetchCapitalTransactions('production');
        });
        btnTxSim.addEventListener('click', () => {
            setTxFilterUI('homologation');
            fetchCapitalTransactions('homologation');
        });
    }

    setupIdleTimeout();
    setupTabs();
    setupClock();
    setupSearch();
    setupCapitalControls();
    setupWebSocket();
    setupGuideWidget();

    await Promise.all([
        fetchStatus(),
        fetchCapitalSummary(),
        fetchCapitalTransactions(currentTxEnv),
        fetchNews(),
        fetchDecisions()
    ]);

    startPollingLoop(fetchStatus, 2000);
    startPollingLoop(fetchCapitalSummary, 3000);
    startPollingLoop(() => fetchNews(false), 60000);
    startPollingLoop(fetchDecisions, 15000);
});

async function fetchCapitalTransactions(targetEnv) {
    try {
        const env = targetEnv || currentTxEnv || state.environment || 'production';
        const res = await fetch(`/api/capital/transactions?limit=50&environment=${encodeURIComponent(env)}`);
        if (!res.ok) return;
        const data = await res.json();
        const txs = Array.isArray(data) ? data : (data.transactions || []);
        renderCapitalTransactions(txs);
    } catch (err) {}
}

function renderCapitalTransactions(txs) {
    const tbody = document.getElementById('tbody-capital-tx');
    if (!tbody) return;
    if (!txs || txs.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">Nenhuma movimentação de capital registrada para este ambiente até o momento.</td></tr>';
        return;
    }
    tbody.innerHTML = txs.map(t => {
        const dt = escapeHtml(String(t.timestamp || '').replace('T', ' ').slice(0, 16));
        const txTypeStr = String(t.tx_type || '');
        const isOutflow = txTypeStr.includes('SAQUE') || txTypeStr.includes('RESGATE');
        const badgeClass = isOutflow ? 'status-badge-inline' : 'status-badge-inline trailing';
        const badgeStyle = isOutflow ? 'background: rgba(244, 63, 94, 0.2); color: #fb7185;' : '';
        const envName = String(t.environment || (t.is_simulated ? 'homologation' : 'production')).toUpperCase();
        const envBadge = envName === 'PRODUCTION' || envName === 'PRODUÇÃO'
            ? '<span class="status-badge-inline trailing" style="font-size: 0.68rem;">PRODUÇÃO</span>'
            : '<span class="status-badge-inline" style="background: rgba(148, 163, 184, 0.2); color: #94a3b8; font-size: 0.68rem;">HOMOLOGAÇÃO</span>';

        return `
            <tr>
                <td class="text-muted small">${dt}</td>
                <td><span class="${badgeClass}" style="${badgeStyle}">${escapeHtml(txTypeStr)}</span></td>
                <td class="fw-semibold ${isOutflow ? 'negative' : 'positive'}">${isOutflow ? '-' : '+'}${formatBRL(t.amount)}</td>
                <td>${formatBRL(t.resulting_ai_capital)}</td>
                <td>${formatBRL(t.resulting_vault)}</td>
                <td>${envBadge}</td>
                <td class="text-muted small">${escapeHtml(t.details || '')}</td>
            </tr>
        `;
    }).join('');
}

async function updateBrokerBalanceDirect(newBal) {
    try {
        const res = await fetch('/api/config/production/broker_balance', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ balance: parseFloat(newBal) })
        });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(data.message || 'Saldo da corretora atualizado!', 'success');
            await Promise.all([
                fetchCapitalSummary(),
                fetchStatus(),
                refreshCapitalTransactions()
            ]);
        } else {
            showToast(data.detail || data.message || 'Erro ao atualizar saldo da corretora.', 'error');
        }
    } catch (err) {
        showToast('Erro ao atualizar saldo da corretora', 'error');
    }
}
