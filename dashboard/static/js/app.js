

let idleTimer = null;
let warningCountdownInterval = null;
let idleSecondsLeft = 60;
const MAX_IDLE_SECONDS = 900;
const WARNING_SECONDS = 60;
let lastActivityTime = Date.now();

async function checkAuthAndSetupUser() {
    try {
        const res = await fetch('/api/auth/me');
        if (!res.ok) {
            window.location.href = '/login';
            return;
        }
        const user = await res.json();
        const nameEl = document.getElementById('user-display-name');
        if (nameEl) nameEl.textContent = user.username;
    } catch (e) {
        window.location.href = '/login';
    }
}

async function performLogout(reason = '') {
    try {
        await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) {}
    window.location.href = reason ? `/login?reason=${reason}` : '/login';
}

function setupIdleTimeout() {
    const modal = document.getElementById('modal-idle-warning');
    const countdownEl = document.getElementById('idle-countdown');
    const btnStay = document.getElementById('btn-stay-logged');
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

let state = {
    equity: 0,
    balance: 0,
    positions: [],
    recentDecisions: [],
    news: [],
    marketStatus: 'DESCONHECIDO',
    watchlistCount: 101,
    activeTab: 'tab-overview',
    activeFilter: 'ALL',
    searchTerm: '',
    posViewMode: 'table',
    isPausedLogs: false,
};

let allocationChart = null;
let logWs = null;

function formatBRL(value) {
    if (value === undefined || value === null || isNaN(value)) return 'R$ 0,00';
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
}

function formatPercent(value) {
    if (value === undefined || value === null || isNaN(value)) return '0,00%';
    const sign = value > 0 ? '+' : '';
    return `${sign}${value.toFixed(2).replace('.', ',')}%`;
}

function formatDate(dateString) {
    if (!dateString) return '';
    try {
        const date = new Date(dateString);
        return date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit', second: '2-digit' }) +
               ' (' + date.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' }) + ')';
    } catch (e) {
        return dateString;
    }
}

function setupTabs() {
    const tabs = document.querySelectorAll('.nav-tab');
    const contents = document.querySelectorAll('.tab-content');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.tab;
            state.activeTab = target;

            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            if (window.innerWidth < 1024) {
                contents.forEach(content => {
                    if (content.id === target) {
                        content.classList.add('active');
                    } else {
                        content.classList.remove('active');
                    }
                });
            }
        });
    });

    window.addEventListener('resize', () => {
        if (window.innerWidth >= 1024) {
            contents.forEach(c => c.classList.add('active'));
        } else {
            contents.forEach(c => {
                if (c.id === state.activeTab) c.classList.add('active');
                else c.classList.remove('active');
            });
        }
        if (allocationChart) {
            allocationChart.resize();
        }
    });
}

function setupViewToggle() {
    const btnTable = document.getElementById('btnTableView');
    const btnCards = document.getElementById('btnCardsView');
    const tableWrap = document.getElementById('positionsTableWrap');
    const cardsWrap = document.getElementById('positionsCardsWrap');

    function setView(mode) {
        state.posViewMode = mode;
        if (mode === 'table') {
            btnTable.classList.add('active');
            btnCards.classList.remove('active');
            tableWrap.classList.remove('hidden-view');
            cardsWrap.classList.remove('active-view');
        } else {
            btnCards.classList.add('active');
            btnTable.classList.remove('active');
            tableWrap.classList.add('hidden-view');
            cardsWrap.classList.add('active-view');
        }
    }

    if (btnTable && btnCards) {
        btnTable.addEventListener('click', () => setView('table'));
        btnCards.addEventListener('click', () => setView('cards'));
    }

    if (window.innerWidth < 640) {
        setView('cards');
    }
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

function setupDecisionFilters() {
    const pills = document.querySelectorAll('.filter-pill');
    pills.forEach(pill => {
        pill.addEventListener('click', () => {
            pills.forEach(p => p.classList.remove('active'));
            pill.classList.add('active');
            state.activeFilter = pill.dataset.filter;
            renderDecisions();
        });
    });
}

async function fetchStatus() {
    try {
        const response = await fetch('/api/status');
        if (!response.ok) return;
        const data = await response.json();

        state.equity = data.equity || 0;
        state.balance = data.balance || 0;
        state.positions = data.positions || [];
        state.recentDecisions = data.recent_decisions || [];
        state.marketStatus = data.market_status || 'DESCONHECIDO';

        renderKPIs(data);

        renderMarketStatus(data.market_status);

        renderPositions();

        renderChart(data);

        renderDecisions();

        const now = new Date();
        document.getElementById('val-last-update').innerText = `Última atualização: ${now.toLocaleTimeString('pt-BR')}`;

    } catch (err) {
        console.warn('Erro ao atualizar status do Córtex:', err);
    }
}

function fetchProductionBalance() {
    fetch('/api/production_balance')
        .then(res => res.json())
        .then(prodData => {
            const prodEl = document.getElementById('prod-balance');
            if (prodData.status === 'ok' && prodEl) {
                prodEl.innerText = formatBRL(prodData.balance);
                prodEl.style.color = 'var(--emerald-green)';
            } else if (prodEl) {
                prodEl.innerText = 'R$ 0,00 (Off)';
                prodEl.style.color = 'var(--text-muted)';
            }
        })
        .catch(() => {});
}

async function fetchNews() {
    const feed = document.getElementById('newsFeedList');
    try {
        const res = await fetch('/api/news?limit=15');
        if (!res.ok) return;
        const data = await res.json();
        state.news = data.news || [];
        renderNews();
    } catch (err) {
        if (feed) feed.innerHTML = '<div class="loading-state">Nenhuma notícia recente disponível no momento.</div>';
    }
}

function renderKPIs(data) {
    const elEquity = document.getElementById('val-equity');
    const elBalance = document.getElementById('val-balance');
    const elPnlTotal = document.getElementById('val-pnl-total');
    const elAllocPct = document.getElementById('val-alloc-pct');
    const elPosCount = document.getElementById('val-positions-count');
    const tabPosCount = document.getElementById('tab-pos-count');
    const badgePosTotal = document.getElementById('badge-positions-total');

    if (elEquity) elEquity.innerText = formatBRL(state.equity);
    if (elBalance) elBalance.innerText = formatBRL(state.balance);

    let totalPnl = 0;
    let totalCost = 0;

    state.positions.forEach(p => {
        const curr = p.current_price || p.entry_price || 0;
        const entry = p.entry_price || 0;
        const qty = p.quantity || 0;
        totalPnl += (curr - entry) * qty;
        totalCost += entry * qty;
    });

    const pnlPct = totalCost > 0 ? (totalPnl / 100000.0) * 100 : 0;
    if (elPnlTotal) {
        const sign = totalPnl >= 0 ? '+' : '';
        elPnlTotal.innerText = `${sign}${formatBRL(totalPnl)} (${sign}${pnlPct.toFixed(2)}%)`;
        elPnlTotal.className = totalPnl >= 0 ? 'positive' : 'negative';
    }

    const allocPct = state.equity > 0 ? ((state.equity - state.balance) / state.equity) * 100 : 0;
    if (elAllocPct) {
        elAllocPct.innerText = `${allocPct.toFixed(1)}% em Ações`;
    }

    const posLen = state.positions.length;
    if (elPosCount) elPosCount.innerText = `${posLen} ativos`;
    if (tabPosCount) tabPosCount.innerText = posLen;
    if (badgePosTotal) badgePosTotal.innerText = posLen;
}

function renderMarketStatus(status) {
    const badge = document.getElementById('badge-market');
    const text = document.getElementById('market-status-text');
    const isOpen = status === 'ABERTO';

    if (badge && text) {
        text.innerText = isOpen ? 'B3: MERCADO ABERTO' : 'B3: MERCADO FECHADO';
        if (isOpen) {
            badge.className = 'status-pill market-pill';
        } else {
            badge.className = 'status-pill market-pill closed';
        }
    }
}

function renderPositions() {
    const tbody = document.getElementById('positions-tbody');
    const cardsWrap = document.getElementById('positionsCardsWrap');
    if (!tbody || !cardsWrap) return;

    tbody.innerHTML = '';
    cardsWrap.innerHTML = '';

    const filtered = state.positions.filter(p => {
        if (!state.searchTerm) return true;
        return p.ticker.toUpperCase().includes(state.searchTerm);
    });

    if (filtered.length === 0) {
        const emptyMsg = state.searchTerm ?
            '<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 30px;">Nenhum ativo encontrado para o filtro.</td></tr>' :
            '<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 30px;">Nenhuma posição aberta no momento.</td></tr>';
        tbody.innerHTML = emptyMsg;
        cardsWrap.innerHTML = `<div style="text-align: center; color: var(--text-muted); padding: 20px;">${state.searchTerm ? 'Nenhum ativo encontrado.' : 'Nenhuma posição aberta.'}</div>`;
        return;
    }

    filtered.forEach(pos => {
        const curr = pos.current_price || pos.entry_price || 0;
        const entry = pos.entry_price || 0;
        const qty = pos.quantity || 0;
        const sl = pos.stop_loss || 0;
        const pnl = (curr - entry) * qty;
        const pnlPct = entry > 0 ? ((curr - entry) / entry) * 100 : 0;
        const pnlClass = pnl >= 0 ? 'positive' : 'negative';

        const isTrailing = sl > (entry * 0.92);
        const statusBadge = isTrailing ?
            '<span class="status-badge-inline trailing">🎯 TRAILING STOP</span>' :
            '<span class="status-badge-inline monitoring">🛡️ PROTEGIDO</span>';

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>
                <div class="ticker-badge">
                    <span>${pos.ticker}</span>
                </div>
            </td>
            <td>${qty}</td>
            <td>${formatBRL(entry)}</td>
            <td><strong>${formatBRL(curr)}</strong></td>
            <td>${formatBRL(sl)}</td>
            <td class="${pnlClass}">${formatBRL(pnl)}</td>
            <td class="${pnlClass}">${formatPercent(pnlPct)}</td>
            <td>${statusBadge}</td>
        `;
        tbody.appendChild(tr);

        const card = document.createElement('div');
        card.className = 'pos-card';
        card.innerHTML = `
            <div class="pos-card-header">
                <span class="pos-card-ticker">${pos.ticker}</span>
                ${statusBadge}
            </div>
            <div class="pos-card-grid">
                <div class="pos-card-row">
                    <span class="lbl">Qtd</span>
                    <span class="val">${qty} ações</span>
                </div>
                <div class="pos-card-row">
                    <span class="lbl">Preço Entrada</span>
                    <span class="val">${formatBRL(entry)}</span>
                </div>
                <div class="pos-card-row">
                    <span class="lbl">Cotação Atual</span>
                    <span class="val" style="color: var(--cyan-accent);">${formatBRL(curr)}</span>
                </div>
                <div class="pos-card-row">
                    <span class="lbl">Stop-Loss</span>
                    <span class="val">${formatBRL(sl)}</span>
                </div>
                <div class="pos-card-row">
                    <span class="lbl">Lucro/Prejuízo</span>
                    <span class="val ${pnlClass}">${formatBRL(pnl)}</span>
                </div>
                <div class="pos-card-row">
                    <span class="lbl">Retorno</span>
                    <span class="val ${pnlClass}">${formatPercent(pnlPct)}</span>
                </div>
            </div>
        `;
        cardsWrap.appendChild(card);
    });
}

function renderChart(data) {
    const ctx = document.getElementById('allocationChart');
    const legendContainer = document.getElementById('chartLegendCustom');
    if (!ctx) return;

    const labels = ['Caixa Livre'];
    const values = [data.balance || 0];
    const colors = ['#223049'];

    if (data.positions && data.positions.length > 0) {
        data.positions.forEach((pos, idx) => {
            const val = (pos.current_price || pos.entry_price || 0) * (pos.quantity || 0);
            labels.push(pos.ticker);
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
                    borderWidth: 2,
                    borderColor: '#0b0f19',
                    hoverOffset: 6,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '72%',
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: 'rgba(11, 15, 25, 0.95)',
                        titleFont: { family: 'Plus Jakarta Sans', size: 13, weight: 'bold' },
                        bodyFont: { family: 'JetBrains Mono', size: 12 },
                        padding: 10,
                        borderColor: 'rgba(99, 102, 241, 0.3)',
                        borderWidth: 1,
                        callbacks: {
                            label: function(context) {
                                const val = context.parsed || 0;
                                const total = context.dataset.data.reduce((a, b) => a + b, 0);
                                const pct = total > 0 ? ((val / total) * 100).toFixed(1) : 0;
                                return ` ${formatBRL(val)} (${pct}%)`;
                            }
                        }
                    }
                }
            }
        });
    }

    if (legendContainer) {
        legendContainer.innerHTML = '';
        const total = values.reduce((a, b) => a + b, 0);
        labels.forEach((label, i) => {
            const val = values[i];
            const pct = total > 0 ? ((val / total) * 100).toFixed(1) : 0;
            const chip = document.createElement('div');
            chip.className = 'legend-chip';
            chip.innerHTML = `
                <span class="legend-dot" style="background-color: ${colors[i]};"></span>
                <span>${label}: <strong>${pct}%</strong></span>
            `;
            legendContainer.appendChild(chip);
        });
    }
}

function renderDecisions() {
    const container = document.getElementById('decisions-timeline');
    if (!container) return;
    container.innerHTML = '';

    const filter = state.activeFilter;
    const list = state.recentDecisions.filter(d => {
        if (filter === 'ALL') return true;
        if (filter === 'BUY') return d.action === 'COMPRA' || d.action === 'BUY';
        if (filter === 'HOLD') return d.action === 'AGUARDAR' || d.action === 'HOLD';
        if (filter === 'SELL') return d.action === 'VENDA' || d.action === 'SELL' || d.action === 'EMERGENCY_SELL';
        return true;
    });

    if (list.length === 0) {
        container.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 30px;">Aguardando novas deliberações do Cérebro...</div>';
        return;
    }

    list.forEach(dec => {
        const actionNorm = (dec.action === 'COMPRA' || dec.action === 'BUY') ? 'BUY' :
                           (dec.action === 'AGUARDAR' || dec.action === 'HOLD') ? 'HOLD' : 'SELL';

        const actionText = (dec.action === 'COMPRA' || dec.action === 'BUY') ? 'COMPRA' :
                           (dec.action === 'AGUARDAR' || dec.action === 'HOLD') ? 'AGUARDAR / MANTER' : 'VENDA / STOP';

        const card = document.createElement('div');
        card.className = `decision-card ${actionNorm}`;
        card.innerHTML = `
            <div class="decision-header">
                <span class="decision-ticker">${dec.ticker}</span>
                <span class="action-badge action-${actionNorm}">${actionText}</span>
            </div>
            <div class="decision-reasoning">${dec.reasoning || 'Avaliação técnica e de sentimento em andamento.'}</div>
            <div class="decision-time">${formatDate(dec.timestamp)}</div>
        `;
        container.appendChild(card);
    });
}

function renderNews() {
    const feed = document.getElementById('newsFeedList');
    if (!feed) return;
    feed.innerHTML = '';

    if (!state.news || state.news.length === 0) {
        feed.innerHTML = '<div class="loading-state">Nenhuma notícia recente disponível no momento.</div>';
        return;
    }

    state.news.forEach(item => {
        const a = document.createElement('a');
        a.className = 'news-item';
        a.href = item.url || '#';
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.innerHTML = `
            <div class="news-title">${item.title}</div>
            <div class="news-meta">
                <span class="news-source">${item.source || 'B3'}</span>
                <span>${formatDate(item.published_at || item.scraped_at)}</span>
            </div>
        `;
        feed.appendChild(a);
    });
}

function setupWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/logs`;
    const terminal = document.getElementById('log-terminal');
    const wsDot = document.getElementById('wsStatusDot');
    const btnPause = document.getElementById('btnPauseLogs');
    const btnClear = document.getElementById('btnClearLogs');
    const termSearch = document.getElementById('terminalFilter');

    if (btnPause) {
        btnPause.addEventListener('click', () => {
            state.isPausedLogs = !state.isPausedLogs;
            btnPause.innerText = state.isPausedLogs ? '▶️ Retomar' : '⏸️ Pausar';
            btnPause.style.background = state.isPausedLogs ? 'rgba(245, 158, 11, 0.2)' : '';
        });
    }

    if (btnClear) {
        btnClear.addEventListener('click', () => {
            if (terminal) terminal.innerHTML = '';
        });
    }

    function connect() {
        try {
            logWs = new WebSocket(wsUrl);

            logWs.onopen = () => {
                if (wsDot) wsDot.className = 'status-dot-pulse';
            };

            logWs.onmessage = (event) => {
                if (state.isPausedLogs || !terminal) return;
                const raw = event.data;

                const filterVal = termSearch ? termSearch.value.trim().toUpperCase() : '';
                if (filterVal && !raw.toUpperCase().includes(filterVal)) return;

                function escapeHtml(str) {
                    const p = document.createElement('p');
                    p.appendChild(document.createTextNode(str));
                    return p.innerHTML;
                }

                let safeLine = escapeHtml(raw);

                safeLine = safeLine.replace(/\[(INFO)\]/g, '<span class="term-info">[$1]</span>');
                safeLine = safeLine.replace(/\[(WARNING)\]/g, '<span class="term-warning">[$1]</span>');
                safeLine = safeLine.replace(/\[(ERROR|CRITICAL)\]/g, '<span class="term-error">[$1]</span>');
                safeLine = safeLine.replace(/\[(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\]/g, '<span class="term-time">[$1]</span>');

                const div = document.createElement('div');
                div.className = 'term-line';
                div.innerHTML = safeLine;
                terminal.appendChild(div);

                if (terminal.children.length > 400) {
                    terminal.removeChild(terminal.firstChild);
                }

                terminal.scrollTop = terminal.scrollHeight;
            };

            logWs.onclose = () => {
                if (wsDot) wsDot.className = 'status-dot-pulse offline';
                setTimeout(connect, 4000);
            };

            logWs.onerror = () => {
                if (wsDot) wsDot.className = 'status-dot-pulse offline';
            };
        } catch (e) {
            setTimeout(connect, 5000);
        }
    }

    connect();
}

document.addEventListener('DOMContentLoaded', () => {
    checkAuthAndSetupUser();
    setupIdleTimeout();
    setupTabs();
    setupViewToggle();
    setupSearch();
    setupDecisionFilters();
    setupWebSocket();

    const btnRefreshNews = document.getElementById('btnRefreshNews');
    if (btnRefreshNews) {
        btnRefreshNews.addEventListener('click', fetchNews);
    }

    fetchStatus();
    fetchProductionBalance();
    fetchNews();

    setInterval(fetchStatus, 1500);
    setInterval(fetchProductionBalance, 10000);
    setInterval(fetchNews, 60000);
});

