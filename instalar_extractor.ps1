# Instala y ejecuta el extractor de ASINs (Best Sellers Electronica amazon.es).
# Uso, desde PowerShell en esta carpeta:
#   powershell -ExecutionPolicy Bypass -File .\instalar_extractor.ps1
# Opcionales: -Categoria telefonia_movil   -Reset (empieza de cero)
param([string]$Categoria = "electronica_hogar", [switch]$Reset)
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# 1. Python
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "No se encuentra Python. Instalando Python 3.12 con winget..." -ForegroundColor Yellow
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    Write-Host "Cierra y vuelve a abrir PowerShell, y ejecuta este script otra vez." -ForegroundColor Yellow
    exit 1
}

# 2. Entorno virtual aislado (.venv) para no tocar tu Python global
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creando entorno virtual .venv..." -ForegroundColor Cyan
    python -m venv .venv
}
$venvPy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

# 3. Dependencias + navegador Chromium de Playwright (solo descarga la primera vez)
Write-Host "Instalando dependencias..." -ForegroundColor Cyan
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -r requirements-extractor.txt
& $venvPy -m playwright install chromium

# 4. Ejecutar
Write-Host "`nExtrayendo ASINs (Ctrl+C para parar; se guarda lo ya obtenido)..." -ForegroundColor Green
$extra = @()
if ($Reset) { $extra += "--reset" }
& $venvPy extractor_amazon.py $Categoria @extra
Write-Host "`nResultado: $(Join-Path $PSScriptRoot "amazon_top1000_$Categoria.csv")" -ForegroundColor Green
