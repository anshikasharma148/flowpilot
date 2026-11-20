#!/usr/bin/env bash
# Starts backend (http://localhost:8000) and frontend (http://localhost:5173).
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"
if [ ! -d .venv ]; then python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; fi
PY=.venv/bin/python; [ -f .venv/Scripts/python.exe ] && PY=.venv/Scripts/python
$PY -m uvicorn app.main:app --port 8000 &
BACK=$!
cd "$ROOT/frontend"
[ -d node_modules ] || npm install
trap "kill $BACK" EXIT
npm run dev
