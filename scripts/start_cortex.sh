#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -x ".venv/bin/python" ]; then
    echo "[ERRO] Ambiente virtual .venv não encontrado em ${PROJECT_DIR}/.venv." >&2
    echo "Execute primeiro: bash scripts/setup.sh" >&2
    exit 1
fi

echo "Iniciando Córtex IA (Motor + Dashboard Integrado)..."
exec .venv/bin/python main.py "$@"
