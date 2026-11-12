#!/usr/bin/env bash
# ==============================================================================
#  Córtex IA — Script de Inicialização Segura (Start)
# ==============================================================================
set -euo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if [ -z "${DASHBOARD_PORT:-}" ] && [ -f "${PROJECT_DIR}/.env" ]; then
    ENV_PORT=$(grep -E '^[[:space:]]*DASHBOARD_PORT=' "${PROJECT_DIR}/.env" | tail -n1 | cut -d'=' -f2 | tr -d '[:space:]"'"'" || true)
    DASHBOARD_PORT="${ENV_PORT:-8003}"
fi
DASHBOARD_PORT="${DASHBOARD_PORT:-8003}"

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}   🧠 CÓRTEX IA — INICIALIZANDO SISTEMA QUANTITATIVO B3  ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo -e "\n${YELLOW}[1/4] Verificando integridade do diretório do projeto (${PROJECT_DIR})...${NC}"
if [ ! -d "${PROJECT_DIR}/.venv" ]; then
    echo -e "${RED}[ERRO] Ambiente virtual .venv não encontrado em ${PROJECT_DIR}/.venv!${NC}" >&2
    exit 1
fi

echo -e "${YELLOW}[2/4] Iniciando serviço via Systemd (cortex-ia.service)...${NC}"
sudo systemctl start cortex-ia.service

echo -e "${YELLOW}[3/4] Aguardando estabilização do motor e resposta HTTP na porta ${DASHBOARD_PORT} (até 15s)...${NC}"
HTTP_READY=0
for _ in $(seq 1 15); do
    STATUS=$(systemctl is-active cortex-ia.service 2>/dev/null || echo "inactive")
    if [ "$STATUS" != "active" ] && [ "$STATUS" != "activating" ]; then
        echo -e "${RED}✖ Serviço cortex-ia.service encerrou inesperadamente (Status: ${STATUS}). Logs recentes:${NC}" >&2
        sudo journalctl -u cortex-ia.service -n 25 --no-pager >&2 || true
        exit 1
    fi

    if command -v curl &>/dev/null; then
        if curl -fsS -o /dev/null --max-time 2 "http://127.0.0.1:${DASHBOARD_PORT}/login" 2>/dev/null; then
            HTTP_READY=1
            break
        fi
    elif ss -tln | grep -qE ":${DASHBOARD_PORT}\b"; then
        HTTP_READY=1
        break
    fi

    sleep 1
done

STATUS=$(systemctl is-active cortex-ia.service 2>/dev/null || echo "inactive")
if [ "$STATUS" != "active" ]; then
    echo -e "${RED}✖ Falha ao iniciar cortex-ia.service (Status: ${STATUS}). Verifique os logs:${NC}" >&2
    sudo journalctl -u cortex-ia.service -n 25 --no-pager >&2 || true
    exit 1
fi

echo -e "${GREEN}✔ Serviço cortex-ia está ATIVO!${NC}"

echo -e "\n${YELLOW}[4/4] Validando disponibilidade do Dashboard em http://127.0.0.1:${DASHBOARD_PORT}/login...${NC}"
if [ "$HTTP_READY" -ne 1 ]; then
    echo -e "${RED}✖ Porta ${DASHBOARD_PORT} não respondeu dentro de 15s! Verifique possíveis conflitos ou erros:${NC}" >&2
    sudo journalctl -u cortex-ia.service -n 25 --no-pager >&2 || true
    exit 1
fi
echo -e "${GREEN}✔ Porta ${DASHBOARD_PORT} ativa e endpoint /login respondendo com sucesso!${NC}"

HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
HOST_IP="${HOST_IP:-127.0.0.1}"

echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✨ CÓRTEX IA ESTÁ EM OPERAÇÃO!${NC}"
echo -e "🌐 Dashboard Web: ${GREEN}http://${HOST_IP}:${DASHBOARD_PORT}/${NC}"
echo -e "📜 Para acompanhar os logs em tempo real: ${YELLOW}journalctl -u cortex-ia.service -f${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
