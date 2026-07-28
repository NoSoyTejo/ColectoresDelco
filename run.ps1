# Arranque rápido en desarrollo
$Root = $PSScriptRoot
$Py = Join-Path $Root "venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "Ejecute primero: py -3.14 -m venv venv && pip install -r requirements.txt"
    exit 1
}
& $Py (Join-Path $Root "src\main.py")
