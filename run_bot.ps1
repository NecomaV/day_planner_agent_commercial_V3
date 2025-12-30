$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$venvPath = Join-Path $ProjectRoot ".venv"
$pythonExe = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Host "Missing .venv. Run setup first:"
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\\Scripts\\activate"
    Write-Host "  pip install -r requirements.txt"
    Write-Host "  copy .env.example .env"
    Write-Host "  python -m scripts.init_db"
    exit 1
}

if (-not (Test-Path ".env")) {
    Write-Host "Missing .env. Copy .env.example to .env and set TELEGRAM_BOT_TOKEN."
    exit 1
}

Write-Host "Starting Telegram bot..."
& $pythonExe run_telegram_bot.py
