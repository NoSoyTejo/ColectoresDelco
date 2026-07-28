# Genera ejecutable Windows en dist/ProyectoColectores/
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $Root "venv\Scripts\python.exe"
$VenvPip = Join-Path $Root "venv\Scripts\pip.exe"

if (-not (Test-Path $VenvPy)) {
    Write-Host "Cree el entorno: py -3.14 -m venv venv" -ForegroundColor Red
    exit 1
}

& $VenvPip install -r (Join-Path $Root "requirements-dev.txt")

& $VenvPy -m PyInstaller `
    --noconfirm `
    --windowed `
    --name ProyectoColectores `
    --paths (Join-Path $Root "src") `
    --collect-all customtkinter `
    --hidden-import serial.tools.list_ports `
    --hidden-import tcp_client `
    --hidden-import polling_schedule `
    --hidden-import command_queue `
    (Join-Path $Root "src\main.py")

Write-Host ""
Write-Host "Listo: $Root\dist\ProyectoColectores\ProyectoColectores.exe" -ForegroundColor Green
Write-Host "Copie la carpeta dist\ProyectoColectores a los PCs de campo."
