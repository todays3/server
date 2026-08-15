$ErrorActionPreference = "Stop"
# Loads server/.env.production (not .env)
$env:APP_ENV = "production"
Set-Location $PSScriptRoot
& .\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
