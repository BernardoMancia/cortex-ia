#!/bin/bash

PROJECT_DIR="/home/servico/cortex-ia"
BACKUP_DIR="/home/servico/cortex-ia/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

echo "[$(date)] Iniciando backup do Córtex..."

if [ -f "$PROJECT_DIR/cortex.db" ]; then
    sqlite3 "$PROJECT_DIR/cortex.db" ".backup '$BACKUP_DIR/cortex_${TIMESTAMP}.db'"
    echo "[$(date)] Backup do cortex.db concluído."
fi

if [ -f "$PROJECT_DIR/simulator_state.json" ]; then
    cp "$PROJECT_DIR/simulator_state.json" "$BACKUP_DIR/simulator_state_${TIMESTAMP}.json"
    echo "[$(date)] Backup do simulator_state.json concluído."
fi

find "$BACKUP_DIR" -type f -name "cortex_*.db" -mtime +$RETENTION_DAYS -delete
find "$BACKUP_DIR" -type f -name "simulator_state_*.json" -mtime +$RETENTION_DAYS -delete

echo "[$(date)] Limpeza de backups antigos concluída."
echo "[$(date)] Backup finalizado com sucesso."
