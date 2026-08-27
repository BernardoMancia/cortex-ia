@echo off
title Cortex IA - Instalador Universal
echo ========================================================
echo   Iniciando Instalador do Ambiente Cortex IA (Windows)
echo ========================================================
python bootstrap.py
if errorlevel 1 (
    py -3 bootstrap.py
)
pause
