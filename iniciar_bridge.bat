@echo off
setlocal
title CORTEX IA - MT5 BRIDGE (CLEAR CORRETORA)
color 0A

echo ==============================================================================
echo    CORTEX IA - BRIDGE DE CONEXAO METATRADER 5 (WINDOWS)
echo ==============================================================================
echo.
echo [1/2] Verificando MetaTrader 5 no Windows...
echo Certifique-se de que o aplicativo MetaTrader 5 da Clear esta aberto na sua tela.
echo.
echo [2/2] Iniciando servidor da Bridge na porta 5000...
echo.

cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" bridge\app.py
) else (
    python bridge\app.py
)

pause
