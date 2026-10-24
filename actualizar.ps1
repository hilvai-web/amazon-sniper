# Instala o actualiza Amazon Sniper y el extractor de ASINs desde GitHub.
# Uso (PowerShell):  irm https://raw.githubusercontent.com/hilvai-web/amazon-sniper/main/actualizar.ps1 | iex
# Tu configuracion (config.json), historial (alertas.db) y CSV no se tocan.
& {
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"   # acelera Invoke-WebRequest en PowerShell 5
    $repoZip = "https://codeload.github.com/hilvai-web/amazon-sniper/zip/refs/heads/main"
    $sniper = Join-Path $HOME "AmazonSniper"
    $extractor = Join-Path $HOME "AmazonExtractor"

    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "No se encuentra Python. Instala Python 3.12 (marca 'Add python.exe to PATH') y repite." -ForegroundColor Yellow
        return
    }

    # 1. Descargar la ultima version
    Write-Host "Descargando la ultima version..." -ForegroundColor Cyan
    $tmp = Join-Path $env:TEMP ("amazon-sniper-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    Invoke-WebRequest -Uri $repoZip -OutFile (Join-Path $tmp "src.zip") -UseBasicParsing
    Expand-Archive -Path (Join-Path $tmp "src.zip") -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Select-Object -First 1 -ExpandProperty FullName

    # 2. Copiar codigo (solo .py y requisitos; nunca config.json, alertas.db ni CSV)
    function Copy-Code($items, $dest) {
        [System.IO.Directory]::CreateDirectory($dest) | Out-Null
        foreach ($item in $items) {
            Copy-Item -Path (Join-Path $src $item) -Destination $dest -Recurse -Force
        }
    }
    Copy-Code @("main.py", "sniper", "requirements.txt") $sniper
    Copy-Code @("extractor_amazon.py", "extractor", "requirements-extractor.txt") $extractor
    Remove-Item (Join-Path $extractor "extraer_asins.py") -ErrorAction SilentlyContinue  # version antigua
    Remove-Item $tmp -Recurse -Force
    Write-Host "Codigo actualizado en $sniper y $extractor" -ForegroundColor Green

    # 3. Entornos virtuales y dependencias
    function Initialize-Venv($dir, [string[]]$packages) {
        $py = Join-Path $dir ".venv\Scripts\python.exe"
        if (-not (Test-Path $py)) { python -m venv (Join-Path $dir ".venv") | Out-Host }
        & $py -m pip install --quiet --upgrade pip | Out-Host
        & $py -m pip install --quiet @packages | Out-Host
        return $py
    }
    Write-Host "Actualizando dependencias de Amazon Sniper..." -ForegroundColor Cyan
    $null = Initialize-Venv $sniper @("customtkinter", "aiohttp", "beautifulsoup4", "lxml", "certifi")
    Write-Host "Actualizando dependencias del extractor..." -ForegroundColor Cyan
    $extPy = Initialize-Venv $extractor @("playwright")
    & $extPy -m playwright install chromium

    # 4. Accesos directos: menu Inicio (siempre) y Escritorio (si Windows lo permite)
    function New-Shortcut($lnk) {
        $wsh = New-Object -ComObject WScript.Shell
        $sc = $wsh.CreateShortcut($lnk)
        $sc.TargetPath = Join-Path $sniper ".venv\Scripts\pythonw.exe"
        $sc.Arguments = "main.py"
        $sc.WorkingDirectory = $sniper
        $sc.Description = "Amazon Sniper - detector de errores de precio"
        $sc.Save()
    }
    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Amazon Sniper.lnk"
    New-Shortcut $startMenu
    Write-Host "Acceso directo creado en el menu Inicio (busca 'Amazon Sniper')." -ForegroundColor Green
    $desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "Amazon Sniper.lnk"
    try {
        New-Shortcut $desktop
        if (-not (Test-Path $desktop)) { throw "bloqueado" }
        Write-Host "Acceso directo creado en el Escritorio." -ForegroundColor Green
    } catch {
        Write-Host "Windows no deja crear el acceso en el Escritorio (proteccion de carpetas)." -ForegroundColor Yellow
        Write-Host "Abriendo la carpeta del menu Inicio: arrastra 'Amazon Sniper' al Escritorio." -ForegroundColor Yellow
        Start-Process explorer.exe -ArgumentList "/select,`"$startMenu`""
    }

    Write-Host "`nListo. Comandos utiles:" -ForegroundColor Green
    Write-Host "  Abrir Amazon Sniper:   cd $sniper; .\.venv\Scripts\pythonw.exe main.py"
    Write-Host "  Ver categorias:        cd $extractor; .\.venv\Scripts\python.exe extractor_amazon.py --listar"
    Write-Host "  Extraer una categoria: cd $extractor; .\.venv\Scripts\python.exe extractor_amazon.py telefonia_movil"
    Write-Host "  Actualizar de nuevo:   irm https://raw.githubusercontent.com/hilvai-web/amazon-sniper/main/actualizar.ps1 | iex"
}
