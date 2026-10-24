# Instala o actualiza Amazon Sniper (AmazonSniper.exe portable, no necesita Python).
# Uso (PowerShell):  irm https://raw.githubusercontent.com/hilvai-web/amazon-sniper/main/actualizar.ps1 | iex
# Carpeta: C:\Users\<tu usuario>\AmazonSniper  (otra: antes ejecuta  $env:SNIPER_DIR = "D:\MiCarpeta")
# Tu configuracion (config.json), historial (alertas.db) y listas no se tocan.
& {
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"   # acelera Invoke-WebRequest en PowerShell 5
    $url = "https://github.com/hilvai-web/amazon-sniper/releases/latest/download/AmazonSniper.exe"
    $dir = if ($env:SNIPER_DIR) { $env:SNIPER_DIR } else { Join-Path $HOME "AmazonSniper" }
    $exe = Join-Path $dir "AmazonSniper.exe"
    [System.IO.Directory]::CreateDirectory($dir) | Out-Null

    $running = Get-Process -Name "AmazonSniper" -ErrorAction SilentlyContinue
    if ($running) {
        Write-Host "Cerrando Amazon Sniper para actualizarlo..." -ForegroundColor Yellow
        $running | Stop-Process -Force
        Start-Sleep -Seconds 2
    }

    Write-Host "Descargando la ultima version de AmazonSniper.exe..." -ForegroundColor Cyan
    $tmp = "$exe.download"
    Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
    Move-Item -Path $tmp -Destination $exe -Force
    Unblock-File -Path $exe -ErrorAction SilentlyContinue
    Write-Host "Instalado en $exe" -ForegroundColor Green

    # Accesos directos: menu Inicio (siempre) y Escritorio (si Windows lo permite)
    function New-Shortcut($lnk) {
        $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        $sc.TargetPath = $exe
        $sc.WorkingDirectory = $dir
        $sc.Description = "Amazon Sniper - detector de errores de precio"
        $sc.Save()
    }
    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Amazon Sniper.lnk"
    New-Shortcut $startMenu
    Write-Host "Acceso directo en el menu Inicio (busca 'Amazon Sniper')." -ForegroundColor Green
    $desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "Amazon Sniper.lnk"
    try {
        New-Shortcut $desktop
        if (-not (Test-Path $desktop)) { throw "bloqueado" }
        Write-Host "Acceso directo en el Escritorio." -ForegroundColor Green
    } catch {
        Write-Host "Windows no deja crear el acceso en el Escritorio: arrastralo desde la carpeta que se abre." -ForegroundColor Yellow
        Start-Process explorer.exe -ArgumentList "/select,`"$startMenu`""
    }

    # La version anterior (con Python) ya no hace falta
    $oldVenv = Join-Path $dir ".venv"
    if (Test-Path $oldVenv) {
        Remove-Item $oldVenv -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $dir "sniper"), (Join-Path $dir "main.py"), (Join-Path $dir "requirements.txt") -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "Eliminada la instalacion antigua con Python (tus datos se conservan)." -ForegroundColor Green
    }

    Write-Host "`nListo. Abriendo Amazon Sniper..." -ForegroundColor Green
    Start-Process -FilePath $exe -WorkingDirectory $dir
    Write-Host "Desde ahora la propia app te avisara cuando haya una version nueva." -ForegroundColor Green
}
