#!/usr/bin/env bash
set -e

if command -v python3 &>/dev/null; then
    PYTHON_BIN="python3"
elif command -v python &>/dev/null; then
    PYTHON_BIN="python"
else
    echo "[ERRO] Python 3 não foi encontrado. Por favor, instale o Python 3.10 ou superior."
    exit 1
fi

"$PYTHON_BIN" bootstrap.py
