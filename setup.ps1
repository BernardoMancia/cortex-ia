# ========================================================
#   Córtex IA — Instalador Automático (PowerShell)
# ========================================================
Write-Host "Iniciando instalador do ambiente Córtex IA..." -ForegroundColor Cyan

if (Get-Command python -ErrorAction SilentlyContinue) {
    python bootstrap.py
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    py -3 bootstrap.py
} else {
    Write-Host "[ERRO] Python não encontrado no PATH do sistema!" -ForegroundColor Red
}
