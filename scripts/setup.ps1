$ErrorActionPreference = "Stop"
Write-Host "Iniciando instalador do ambiente Córtex IA..." -ForegroundColor Cyan

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python "$PSScriptRoot\setup.py"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 "$PSScriptRoot\setup.py"
} else {
    Write-Host "[ERRO] Python não encontrado no PATH do sistema!" -ForegroundColor Red
    exit 1
}
