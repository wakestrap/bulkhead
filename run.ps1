# Atlantic Pressure — Windows launcher
# Usage: .\run.ps1 [port]

param([int]$Port = 5050)

$env:FLASK_APP = "app.py"
$env:FLASK_DEBUG = "0"

Write-Host "🌊  Atlantic Pressure" -ForegroundColor Cyan
Write-Host "    http://localhost:${Port}"
Write-Host "    Press Ctrl+C to stop"
Write-Host ""

python -m flask run --host=0.0.0.0 --port=$Port
