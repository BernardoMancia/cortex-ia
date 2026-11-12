#!/usr/bin/env bash
# ==============================================================================
#  Córtex IA — Script de Desligamento Seguro e Gracioso (Stop)
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
echo -e "${BLUE}   🛑 CÓRTEX IA — DESLIGAMENTO GRACIOSO DO MOTOR B3     ${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

echo -e "\n${YELLOW}[1/3] Enviando sinal de parada graciosa para o motor...${NC}"
echo -e "ℹ️  O sistema finalizará o ciclo em andamento e salvará a carteira com segurança..."

sudo systemctl stop cortex-ia.service || true

echo -e "\n${YELLOW}[2/3] Confirmando finalização limpa do serviço (até 30s)...${NC}"
STATUS="unknown"
for _ in $(seq 1 30); do
    STATUS=$(systemctl is-active cortex-ia.service 2>/dev/null || true)
    STATUS="${STATUS:-inactive}"
    if [ "$STATUS" = "inactive" ]; then
        break
    fi
    if [ "$STATUS" = "failed" ]; then
        break
    fi
    sleep 1
done

if [ "$STATUS" = "failed" ] || [ "$STATUS" = "active" ] || [ "$STATUS" != "inactive" ]; then
    echo -e "${RED}✖ Erro ao encerrar cortex-ia.service (Status atual: ${STATUS}). Últimos logs:${NC}" >&2
    sudo journalctl -u cortex-ia.service -n 25 --no-pager >&2 || true
    exit 1
fi

echo -e "${GREEN}✔ Serviço cortex-ia encerrado de forma limpa (inactive).${NC}"

echo -e "\n${YELLOW}[3/3] Verificando liberação da porta ${DASHBOARD_PORT}...${NC}"
for _ in $(seq 1 5); do
    if ! ss -tln | grep -qE ":${DASHBOARD_PORT}\b"; then
        break
    fi
    sleep 1
done

if ss -tln | grep -qE ":${DASHBOARD_PORT}\b"; then
    echo -e "${RED}✖ A porta ${DASHBOARD_PORT} continua ocupada após parada do serviço!${NC}" >&2
    exit 1
fi
echo -e "${GREEN}✔ Porta ${DASHBOARD_PORT} liberada com sucesso.${NC}"

echo -e "\n${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}🔒 CÓRTEX IA DESLIGADO COM SEGURANÇA E PATRIMÔNIO PROTEGIDO.${NC}"
echo -e "💡 Para ligar novamente quando desejar: ${YELLOW}./iniciar_ia.sh${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
