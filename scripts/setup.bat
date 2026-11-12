@echo off
setlocal
title Cortex IA - Instalador Universal
echo ========================================================
echo   Iniciando Instalador do Ambiente Cortex IA (Windows)
echo ========================================================
where python >nul 2>nul
if %errorlevel% equ 0 (
    python "%~dp0setup.py"
) else (
    py -3 "%~dp0setup.py"
)
pause
