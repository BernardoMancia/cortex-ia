#!/usr/bin/env bash

set -euo pipefail

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
VENV_DIR="${PROJECT_DIR}/.venv"
SERVICE_NAME="cortex-ia"
CENTRAL_LOG_DIR="/LOGS-PROJETOS/cortex-ia"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║     PROJETO CÓRTEX — SETUP VPS           ║${NC}"
echo -e "${CYAN}║     Inteligência de Mercado & Trading    ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

log_info "Verificando dependências do sistema..."

if ! command -v python3 &> /dev/null; then
    log_warn "Python3 não encontrado. Instalando..."
    sudo apt-get update
    sudo apt-get install -y python3 python3-pip python3-venv
fi

PYTHON_VERSION=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -n1)
log_success "Python ${PYTHON_VERSION} encontrado"

sudo apt-get install -y --no-install-recommends \
    python3-venv \
    python3-dev \
    build-essential \
    sqlite3 \
    curl \
    git \
    2>/dev/null || log_warn "Alguns pacotes de sistema podem não ter sido instalados"

log_info "Configurando ambiente virtual Python (.venv)..."

if [ ! -d "$VENV_DIR" ] || [ ! -x "${VENV_DIR}/bin/python" ]; then
    python3 -m venv "$VENV_DIR"
    log_success "Ambiente virtual criado em ${VENV_DIR}"
else
    log_info "Ambiente virtual já existente em ${VENV_DIR}; atualizando pacotes..."
fi

"${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel

log_info "Instalando dependências Python..."
grep -viE '^\s*metatrader5' "${PROJECT_DIR}/requirements.txt" | "${VENV_DIR}/bin/python" -m pip install -r /dev/stdin
log_success "Dependências instaladas com sucesso"

if [ ! -f "${PROJECT_DIR}/.env" ]; then
    log_info "Criando arquivo .env a partir do template..."
    cp "${PROJECT_DIR}/.env.example" "${PROJECT_DIR}/.env"
    chmod 600 "${PROJECT_DIR}/.env"
    log_warn "IMPORTANTE: Edite ${PROJECT_DIR}/.env com suas credenciais!"
else
    log_success "Arquivo .env já existe"
fi

log_info "Criando diretórios de dados, backups e logs centralizados..."

CURRENT_USER=$(whoami)
mkdir -p "${PROJECT_DIR}/logs" "${PROJECT_DIR}/data" "${PROJECT_DIR}/backups"
sudo mkdir -p "${CENTRAL_LOG_DIR}"
sudo chown -R "${CURRENT_USER}:${CURRENT_USER}" "${CENTRAL_LOG_DIR}"

log_success "Diretórios verificados"

# Remove unidade legada conflitante cortex-dashboard.service (CortexEngine já hospeda o Dashboard na porta 8003)
if [ -f "/etc/systemd/system/cortex-dashboard.service" ]; then
    log_warn "Removendo serviço legado duplicado cortex-dashboard.service..."
    sudo systemctl disable --now cortex-dashboard.service 2>/dev/null || true
    sudo rm -f /etc/systemd/system/cortex-dashboard.service
fi

log_info "Configurando serviço systemd unificado (${SERVICE_NAME}.service)..."

sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null << EOF
[Unit]
Description=Córtex IA - Trading Quantitativo & Dashboard Seguro (B3)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
Group=${CURRENT_USER}
WorkingDirectory=${PROJECT_DIR}
Environment="PATH=${VENV_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStartPre=/bin/mkdir -p ${CENTRAL_LOG_DIR}
ExecStart=${VENV_DIR}/bin/python main.py
Restart=always
RestartSec=10
TimeoutStopSec=90
StartLimitIntervalSec=60
StartLimitBurst=3

StandardOutput=append:${CENTRAL_LOG_DIR}/systemd-out.log
StandardError=append:${CENTRAL_LOG_DIR}/systemd-error.log

ProtectSystem=strict
ReadWritePaths=${PROJECT_DIR} ${CENTRAL_LOG_DIR}
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF

log_info "Habilitando serviço no systemd..."
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}.service"

log_warn "Serviço configurado mas NÃO iniciado automaticamente."
log_warn "Edite o .env primeiro e depois execute:"
echo ""
echo -e "  ${GREEN}sudo systemctl start ${SERVICE_NAME}${NC}"
echo ""

log_info "Configurando rotação de logs (logrotate)..."

sudo tee "/etc/logrotate.d/${SERVICE_NAME}" > /dev/null << EOF
${PROJECT_DIR}/logs/*.log ${CENTRAL_LOG_DIR}/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    create 0640 ${CURRENT_USER} ${CURRENT_USER}
}
EOF

log_success "Logrotate configurado"

HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
HOST_IP="${HOST_IP:-ip_servidor}"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       SETUP CONCLUÍDO COM SUCESSO!       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "${CYAN}Resumo:${NC}"
echo -e "  📂 Projeto: ${PROJECT_DIR}"
echo -e "  🐍 Python: ${PYTHON_VERSION}"
echo -e "  📦 Venv: ${VENV_DIR}"
echo -e "  🤖 Serviço unificado (Motor + Dashboard): ${SERVICE_NAME}.service"
echo -e "  🌐 Dashboard: http://${HOST_IP}:8003"
echo ""
echo -e "${YELLOW}Próximos passos:${NC}"
echo -e "  1. Edite o .env: ${CYAN}nano ${PROJECT_DIR}/.env${NC}"
echo -e "  2. Inicie o serviço: ${CYAN}./iniciar_ia.sh${NC} (ou ${CYAN}sudo systemctl start ${SERVICE_NAME}${NC})"
echo -e "  3. Verifique o status: ${CYAN}./status_ia.sh${NC}"
echo -e "  4. Acompanhe os logs: ${CYAN}tail -f ${CENTRAL_LOG_DIR}/systemd-out.log${NC}"
echo ""
