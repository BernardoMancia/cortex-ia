#!/usr/bin/env bash
# ==============================================================================
#  Córtex IA — Script de Verificação de Status Rápido
# ==============================================================================
set -uo pipefail

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -z "${DASHBOARD_PORT:-}" ] && [ -f "${PROJECT_DIR}/.env" ]; then
    ENV_PORT=$(grep -E '^[[:space:]]*DASHBOARD_PORT=' "${PROJECT_DIR}/.env" | tail -n1 | cut -d'=' -f2 | tr -d '[:space:]"'"'" || true)
    DASHBOARD_PORT="${ENV_PORT:-8003}"
fi
DASHBOARD_PORT="${DASHBOARD_PORT:-8003}"

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}   📊 CÓRTEX IA — STATUS OPERACIONAL DO SISTEMA          ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

STATUS=$(systemctl is-active cortex-ia.service 2>/dev/null || true)
STATUS="${STATUS:-inactive}"

case "$STATUS" in
    active)
        echo -e "Estado do Serviço: ${GREEN}🟢 ATIVO (Em Operação)${NC}"
        ;;
    activating|reloading)
        echo -e "Estado do Serviço: ${YELLOW}🟡 TRANSITÓRIO (${STATUS})${NC}"
        ;;
    deactivating)
        echo -e "Estado do Serviço: ${YELLOW}🟠 DESLIGANDO (${STATUS})${NC}"
        ;;
    failed)
        echo -e "Estado do Serviço: ${RED}❌ FALHA CRÍTICA (failed)${NC}"
        ;;
    *)
        echo -e "Estado do Serviço: ${YELLOW}⚪ INATIVO (${STATUS})${NC}"
        ;;
esac

echo -e "\n--- Status da Porta ${DASHBOARD_PORT} ---"
if ss -tln | grep -qE ":${DASHBOARD_PORT}\b"; then
    echo -e "Porta ${DASHBOARD_PORT}: ${GREEN}🟢 LISTEN (Acessível)${NC}"
else
    echo -e "Porta ${DASHBOARD_PORT}: ${RED}🔴 FECHADA${NC}"
fi

echo -e "\n--- Uso de Recursos ---"
systemctl status cortex-ia.service --no-pager 2>/dev/null | grep -iE 'memory|cpu|tasks' || echo "Sem métricas ativas."

echo -e "\n--- Últimos 5 Logs do Motor ---"
sudo journalctl -u cortex-ia.service -n 5 --no-pager || true

echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

if [ "$STATUS" = "failed" ]; then
    exit 1
fi
