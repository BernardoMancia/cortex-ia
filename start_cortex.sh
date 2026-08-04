#!/bin/bash
# Inicia o Córtex IA no Linux/VPS

echo "Iniciando o Dashboard Web (Uvicorn)..."
source .venv/bin/activate
uvicorn dashboard.app:app --host 0.0.0.0 --port 8003 &
DASHBOARD_PID=$!

echo "Iniciando o Córtex Engine em modo Simulação..."
python main.py --sim

echo "Encerrando..."
kill $DASHBOARD_PID
