#!/bin/bash
# ============================================================
#  Job Hunter PA – Start Everything (Backend + Bot together)
#  Usage: bash scripts/start_all.sh
# ============================================================
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Colour helpers ───────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${YELLOW}▶ $1${NC}"; }
ok()    { echo -e "${GREEN}✅ $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; }

echo ""
echo "═══════════════════════════════════════════"
echo "  Job Hunter PA – Starting up"
echo "═══════════════════════════════════════════"
echo ""

# ── Checks ───────────────────────────────────────────────────
if [ ! -f .env ]; then
  error ".env file not found"
  echo "  Run: cp .env.example .env  then fill in your values"
  exit 1
fi

if ! command -v uvicorn &>/dev/null; then
  error "uvicorn not found — run: pip install -r requirements.txt"
  exit 1
fi

# ── Start backend ─────────────────────────────────────────────
info "Starting FastAPI backend on port 8000..."

# --reload-exclude stops APScheduler watchfiles warning
uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload \
  --reload-exclude '.venv' \
  --reload-exclude '__pycache__' \
  --reload-exclude 'data' \
  --reload-exclude '*.db' \
  --reload-exclude '*.xlsx' &

BACKEND_PID=$!
echo "  Backend PID: $BACKEND_PID"

# ── Wait for backend to be ready ──────────────────────────────
info "Waiting for backend to start..."
READY=false
for i in $(seq 1 20); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 1
  echo -n "."
done
echo ""

if [ "$READY" = false ]; then
  error "Backend did not start within 20 seconds"
  kill $BACKEND_PID 2>/dev/null
  exit 1
fi

ok "Backend is ready at http://localhost:8000"
echo "  📖 API docs: http://localhost:8000/docs"
echo ""

# ── Start bot ─────────────────────────────────────────────────
info "Starting Telegram bot..."
echo "  (Press Ctrl+C to stop both processes)"
echo ""

# Run bot in foreground so Ctrl+C stops everything
python -m bot.telegram_bot

# ── Cleanup ───────────────────────────────────────────────────
echo ""
info "Shutting down backend..."
kill $BACKEND_PID 2>/dev/null && ok "Backend stopped" || true
