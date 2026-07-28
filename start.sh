#!/bin/bash
# Start AI-Trader backend (port 8000) and Arena frontend (port 3100)
# Usage: ./start.sh [--backend-only | --frontend-only]

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"
BACKEND_DIR="$ROOT/service/server"
FRONTEND_DIR="$ROOT/service/arena"

start_backend() {
  echo "Starting backend on :8000..."
  source "$VENV/bin/activate"
  cd "$BACKEND_DIR"
  python main.py &
  BACKEND_PID=$!
  echo "Backend PID: $BACKEND_PID"
}

start_frontend() {
  echo "Starting Arena frontend on :3100..."
  cd "$FRONTEND_DIR"
  npm run dev &
  FRONTEND_PID=$!
  echo "Frontend PID: $FRONTEND_PID"
}

case "${1:-all}" in
  --backend-only) start_backend ;;
  --frontend-only) start_frontend ;;
  all) start_backend; sleep 2; start_frontend ;;
  *) echo "Usage: ./start.sh [--backend-only | --frontend-only]"; exit 1 ;;
esac

echo ""
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:3100"
echo ""
echo "Press Ctrl+C to stop both."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
wait
