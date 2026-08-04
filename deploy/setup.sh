#!/bin/bash
# ============================================
# Projeto Córtex — Script de Setup no VPS
# Configura ambiente Python, dependências e serviços
# ============================================

set -euo pipefail

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_info() { echo -e "${CYAN}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/venv"
SERVICE_NAME="cortex-ia"
DASHBOARD_SERVICE_NAME="cortex-dashboard"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     PROJETO CÓRTEX — SETUP VPS           ║${NC}"
echo -e "${CYAN}║     Inteligência de Mercado & Trading     ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ============================================
# 1. Verificar e instalar dependências do sistema
# ============================================
log_info "Verificando dependências do sistema..."

if ! command -v python3 &> /dev/null; then
    log_warn "Python3 não encontrado. Instalando..."
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv
fi

PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
log_success "Python ${PYTHON_VERSION} encontrado"

# Pacotes adicionais necessários
sudo apt-get install -y --no-install-recommends \
    python3-venv \
    python3-dev \
    build-essential \
    curl \
    git \
    2>/dev/null || log_warn "Alguns pacotes podem não ter sido instalados"

# ============================================
# 2. Criar ambiente virtual
# ============================================
log_info "Configurando ambiente virtual Python..."

if [ -d "$VENV_DIR" ]; then
    log_warn "Ambiente virtual já existe. Recriando..."
    rm -rf "$VENV_DIR"
fi

python3 -m venv "$VENV_DIR"
source "${VENV_DIR}/bin/activate"
pip install --upgrade pip setuptools wheel

log_success "Ambiente virtual criado em ${VENV_DIR}"

# ============================================
# 3. Instalar dependências Python
# ============================================
log_info "Instalando dependências Python..."

pip install -r "${PROJECT_DIR}/requirements.txt"

log_success "Dependências instaladas com sucesso"

# ============================================
# 4. Criar arquivo .env se não existir
# ============================================
if [ ! -f "${PROJECT_DIR}/.env" ]; then
    log_info "Criando arquivo .env a partir do template..."
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    log_warn "IMPORTANTE: Edite ${PROJECT_DIR}/.env com suas credenciais!"
else
    log_success "Arquivo .env já existe"
fi

# ============================================
# 5. Criar diretórios necessários
# ============================================
log_info "Criando diretórios de dados..."

mkdir -p "${PROJECT_DIR}/logs"
mkdir -p "${PROJECT_DIR}/data"

log_success "Diretórios criados"

# ============================================
# 6. Configurar serviço systemd para o bot
# ============================================
log_info "Configurando serviço systemd para o Córtex..."

CURRENT_USER=$(whoami)

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=Projeto Córtex - Trading Algorítmico B3
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
Group=${CURRENT_USER}
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=${VENV_DIR}/bin/python main.py --simulation
Restart=always
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=3

# Logging
StandardOutput=append:${PROJECT_DIR}/logs/cortex-stdout.log
StandardError=append:${PROJECT_DIR}/logs/cortex-stderr.log

# Segurança
ProtectSystem=strict
ReadWritePaths=${PROJECT_DIR}
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

# ============================================
# 7. Configurar serviço systemd para o dashboard
# ============================================
log_info "Configurando serviço systemd para o Dashboard..."

sudo tee /etc/systemd/system/${DASHBOARD_SERVICE_NAME}.service > /dev/null << EOF
[Unit]
Description=Córtex Dashboard - Visualização Web
After=network.target ${SERVICE_NAME}.service
Wants=${SERVICE_NAME}.service

[Service]
Type=simple
User=${CURRENT_USER}
Group=${CURRENT_USER}
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/local/bin:/usr/bin:/bin"
ExecStart=${VENV_DIR}/bin/python -m uvicorn dashboard.app:app --host 0.0.0.0 --port 8003
Restart=always
RestartSec=10

StandardOutput=append:${PROJECT_DIR}/logs/dashboard-stdout.log
StandardError=append:${PROJECT_DIR}/logs/dashboard-stderr.log

ProtectSystem=strict
ReadWritePaths=${PROJECT_DIR}
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

# ============================================
# 8. Ativar e iniciar serviços
# ============================================
log_info "Ativando serviços..."

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl enable ${DASHBOARD_SERVICE_NAME}

# Não iniciar automaticamente — aguardar configuração do .env
log_warn "Serviços configurados mas NÃO iniciados automaticamente."
log_warn "Edite o .env primeiro, depois execute:"
echo ""
echo -e "  ${GREEN}sudo systemctl start ${SERVICE_NAME}${NC}"
echo -e "  ${GREEN}sudo systemctl start ${DASHBOARD_SERVICE_NAME}${NC}"
echo ""

# ============================================
# 9. Configurar logrotate
# ============================================
log_info "Configurando rotação de logs..."

sudo tee /etc/logrotate.d/${SERVICE_NAME} > /dev/null << EOF
${PROJECT_DIR}/logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 ${CURRENT_USER} ${CURRENT_USER}
}
EOF

log_success "Logrotate configurado"

# ============================================
# 10. Verificação final
# ============================================
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       SETUP CONCLUÍDO COM SUCESSO!       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Resumo:${NC}"
echo -e "  📂 Projeto: ${PROJECT_DIR}"
echo -e "  🐍 Python: ${PYTHON_VERSION}"
echo -e "  📦 Venv: ${VENV_DIR}"
echo -e "  🤖 Serviço bot: ${SERVICE_NAME}"
echo -e "  🌐 Dashboard: http://$(hostname -I | awk '{print $1}'):8003"
echo ""
echo -e "${YELLOW}Próximos passos:${NC}"
echo -e "  1. Edite o .env: ${CYAN}nano ${PROJECT_DIR}/.env${NC}"
echo -e "  2. Inicie o bot: ${CYAN}sudo systemctl start ${SERVICE_NAME}${NC}"
echo -e "  3. Inicie o dashboard: ${CYAN}sudo systemctl start ${DASHBOARD_SERVICE_NAME}${NC}"
echo -e "  4. Verifique o status: ${CYAN}sudo systemctl status ${SERVICE_NAME}${NC}"
echo -e "  5. Veja os logs: ${CYAN}tail -f ${PROJECT_DIR}/logs/cortex-stdout.log${NC}"
echo ""
