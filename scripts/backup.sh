#!/bin/bash
# Script de Backup Automático - Projeto Córtex
# Faz backup do banco de dados e do estado do simulador para proteção do patrimônio.

# Configurações
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
BACKUP_DIR="${PROJECT_DIR}/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RETENTION_DAYS=7

# Criar pasta de backup se não existir
mkdir -p "$BACKUP_DIR"

echo "[$(date)] Iniciando backup do Córtex..."

# Backup do banco de dados (usando sqlite3 backup se possível para evitar locks)
if [ -f "$PROJECT_DIR/cortex.db" ]; then
    sqlite3 "$PROJECT_DIR/cortex.db" ".backup '$BACKUP_DIR/cortex_${TIMESTAMP}.db'"
    echo "[$(date)] Backup do cortex.db concluído."
fi

# Backup do estado do simulador
if [ -f "$PROJECT_DIR/simulator_state.json" ]; then
    cp "$PROJECT_DIR/simulator_state.json" "$BACKUP_DIR/simulator_state_${TIMESTAMP}.json"
    echo "[$(date)] Backup do simulator_state.json concluído."
fi

# Rotatividade de backups: manter apenas os últimos RETENTION_DAYS
find "$BACKUP_DIR" -type f -name "cortex_*.db" -mtime +$RETENTION_DAYS -delete
find "$BACKUP_DIR" -type f -name "simulator_state_*.json" -mtime +$RETENTION_DAYS -delete

echo "[$(date)] Limpeza de backups antigos concluída."
echo "[$(date)] Backup finalizado com sucesso."
