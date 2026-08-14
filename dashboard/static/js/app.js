let allocationChart = null;

function formatBRL(value) {
    return new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' }).format(value);
}

function formatDate(dateString) {
    const date = new Date(dateString);
    return date.toLocaleString('pt-BR');
}

async function fetchStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        
        // Atualizar métricas
        document.getElementById('val-equity').innerText = formatBRL(data.equity);
        document.getElementById('val-balance').innerText = formatBRL(data.balance);
        
        // Fetch production balance
        fetch('/api/production_balance')
            .then(res => res.json())
            .then(prodData => {
                const prodEl = document.getElementById('prod-balance');
                if (prodData.status === 'ok' && prodEl) {
                    prodEl.innerText = formatBRL(prodData.balance);
                } else if (prodEl) {
                    prodEl.innerText = 'R$ --,-- (Offline)';
                }
            })
            .catch(() => {});
        
        
        // Atualizar tabela de posições
        const tbody = document.querySelector('#positions-table tbody');
        tbody.innerHTML = '';
        
        if (data.positions && data.positions.length > 0) {
            data.positions.forEach(pos => {
                const currentPrice = pos.current_price || pos.entry_price;
                const pnl = (currentPrice - pos.entry_price) * pos.quantity;
                const pnlClass = pnl >= 0 ? 'positive' : 'negative';
                
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td><strong>${pos.ticker}</strong></td>
                    <td>${pos.quantity}</td>
                    <td>${formatBRL(pos.entry_price)}</td>
                    <td>${formatBRL(currentPrice)}</td>
                    <td>${formatBRL(pos.stop_loss)}</td>
                    <td class="${pnlClass}">${formatBRL(pnl)}</td>
                `;
                tbody.appendChild(tr);
            });
        } else {
            tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; color: var(--text-secondary);">Nenhuma posição aberta</td></tr>';
        }
        
        // Atualizar timeline de decisões
        const timeline = document.getElementById('decisions-timeline');
        timeline.innerHTML = '';
        
        if (data.recent_decisions && data.recent_decisions.length > 0) {
            data.recent_decisions.forEach(dec => {
                const div = document.createElement('div');
                div.className = `decision-card ${dec.action}`;
                div.innerHTML = `
                    <div class="decision-header">
                        <span class="decision-ticker">${dec.ticker}</span>
                        <span class="decision-action action-${dec.action}">${dec.action}</span>
                    </div>
                    <div class="decision-reasoning">${dec.reasoning}</div>
                    <div style="font-size: 11px; color: var(--text-secondary); margin-top: 5px; text-align: right;">${formatDate(dec.timestamp)}</div>
                `;
                timeline.appendChild(div);
            });
        } else {
            timeline.innerHTML = '<div style="text-align: center; color: var(--text-secondary);">Aguardando decisões...</div>';
        }
        
        updateChart(data);
        
    } catch (error) {
        console.error('Erro ao buscar status:', error);
    }
}

function updateChart(data) {
    const ctx = document.getElementById('allocationChart');
    if (!ctx) return;

    const labels = ['Caixa Livre'];
    const values = [data.balance];
    const colors = ['#2d3748'];

    if (data.positions) {
        data.positions.forEach((pos, index) => {
            labels.push(pos.ticker);
            values.push((pos.current_price || pos.entry_price) * pos.quantity);
            // Generate some colors
            const hue = (index * 137.508) % 360; 
            colors.push(`hsl(${hue}, 70%, 50%)`);
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
                    borderWidth: 0,
                    hoverOffset: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            color: '#a0aec0',
                            font: {
                                family: 'Inter',
                                size: 12
                            }
                        }
                    }
                }
            }
        });
    }
}

// Configurar WebSocket para logs
function setupWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/logs`);
    const terminal = document.getElementById('log-terminal');
    
    ws.onmessage = function(event) {
        const line = event.data;
        const div = document.createElement('div');
        
        // Sanitize: escape HTML entities from external data
        function escapeHtml(str) {
            var p = document.createElement('p');
            p.appendChild(document.createTextNode(str));
            return p.innerHTML;
        }
        
        let safeLine = escapeHtml(line);
        
        // Simple highlighting based on log level (applied to sanitized text)
        if (safeLine.includes('[INFO]')) {
            safeLine = safeLine.replace('[INFO]', '<span class="info">[INFO]</span>');
        } else if (safeLine.includes('[WARNING]')) {
            safeLine = safeLine.replace('[WARNING]', '<span class="warning">[WARNING]</span>');
        } else if (safeLine.includes('[ERROR]')) {
            safeLine = safeLine.replace('[ERROR]', '<span class="error">[ERROR]</span>');
        }
        
        // Highlight timestamp
        safeLine = safeLine.replace(/\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]/, '<span class="time">[$1]</span>');
        
        div.innerHTML = safeLine;
        terminal.appendChild(div);
        
        // Auto-scroll to bottom
        if (terminal.children.length > 500) {
            terminal.removeChild(terminal.firstChild);
        }
        terminal.scrollTop = terminal.scrollHeight;
    };
    
    ws.onclose = function() {
        console.log('WebSocket desconectado. Tentando reconectar em 5s...');
        setTimeout(setupWebSocket, 5000);
    };
}

// Iniciar
setInterval(fetchStatus, 3000); // Atualizar status a cada 3s
fetchStatus();
setupWebSocket();
