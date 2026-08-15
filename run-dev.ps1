$ErrorActionPreference = "Stop"
# Loads server/.env.development (not .env)
$env:APP_ENV = "development"
Set-Location $PSScriptRoot
& .\.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
