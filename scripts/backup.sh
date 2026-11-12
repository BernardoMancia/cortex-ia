#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${PROJECT_DIR}/data"
BACKUP_DIR="${PROJECT_DIR}/backups"
TIMESTAMP="$(date +"%Y%m%d_%H%M%S")"
RETENTION_DAYS="${RETENTION_DAYS:-7}"

DB_FILE="${DATA_DIR}/cortex.db"
SIM_STATE_FILE="${DATA_DIR}/simulator_state.json"
PROD_STATE_FILE="${DATA_DIR}/prod_simulator_state.json"

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando backup do Córtex IA em ${PROJECT_DIR}..."

if [ ! -f "$DB_FILE" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [ERRO] Banco de dados não encontrado em: ${DB_FILE}" >&2
    exit 1
fi

BACKUP_DB_TARGET="${BACKUP_DIR}/cortex_${TIMESTAMP}.db"
if command -v sqlite3 &>/dev/null; then
    sqlite3 "$DB_FILE" ".backup '${BACKUP_DB_TARGET}'"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup atômico (sqlite3 .backup) de cortex.db concluído: ${BACKUP_DB_TARGET}"
else
    cp -p "$DB_FILE" "$BACKUP_DB_TARGET"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup por cópia (cp) de cortex.db concluído: ${BACKUP_DB_TARGET}"
fi

if [ -f "$SIM_STATE_FILE" ]; then
    cp -p "$SIM_STATE_FILE" "${BACKUP_DIR}/simulator_state_${TIMESTAMP}.json"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup de simulator_state.json concluído."
fi

if [ -f "$PROD_STATE_FILE" ]; then
    cp -p "$PROD_STATE_FILE" "${BACKUP_DIR}/prod_simulator_state_${TIMESTAMP}.json"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup de prod_simulator_state.json concluído."
fi

find "$BACKUP_DIR" -maxdepth 1 -type f -name "cortex_*.db" -mtime "+${RETENTION_DAYS}" -delete
find "$BACKUP_DIR" -maxdepth 1 -type f -name "simulator_state_*.json" -mtime "+${RETENTION_DAYS}" -delete
find "$BACKUP_DIR" -maxdepth 1 -type f -name "prod_simulator_state_*.json" -mtime "+${RETENTION_DAYS}" -delete

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Limpeza de backups com mais de ${RETENTION_DAYS} dias concluída."
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Backup finalizado com sucesso."
