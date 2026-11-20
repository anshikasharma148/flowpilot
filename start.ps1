# Starts backend (http://localhost:8000) and frontend (http://localhost:5173) in two windows.
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path "$root\backend\.venv")) {
  python -m venv "$root\backend\.venv"
  & "$root\backend\.venv\Scripts\pip" install -r "$root\backend\requirements.txt"
}
if (-not (Test-Path "$root\frontend\node_modules")) { Push-Location "$root\frontend"; npm install; Pop-Location }
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\backend'; .\.venv\Scripts\python -m uvicorn app.main:app --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\frontend'; npm run dev"
Write-Host "Open http://localhost:5173"
