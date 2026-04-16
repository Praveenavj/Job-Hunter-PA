#!/bin/bash
# ============================================================
#  Job Hunter PA — Start Everything
#  Starts: Puter Bridge → FastAPI Backend → Telegram Bot
#
#  Usage: bash scripts/start_all.sh
# ============================================================
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${YELLOW}▶ $1${NC}"; }
ok()    { echo -e "${GREEN}✅ $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; }
blue()  { echo -e "${BLUE}ℹ  $1${NC}"; }

echo ""
echo "═══════════════════════════════════════════════════"
echo "  Job Hunter PA v4.0 — Starting up"
echo "═══════════════════════════════════════════════════"
echo ""

# ── Checks ────────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  error ".env file not found"
  echo "  Run: cp .env.example .env  then fill in your values"
  exit 1
fi

if ! command -v uvicorn &>/dev/null; then
  error "uvicorn not found — run: source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

# ── Kill any lingering processes from previous runs ───────────────────────────
info "Cleaning up any lingering processes on ports 3456 and 8000..."
lsof -ti:3456 | xargs kill -9 2>/dev/null || true
lsof -ti:8000 | xargs kill -9 2>/dev/null || true
sleep 1

# ── Step 1: Start Puter Bridge (Node.js) ─────────────────────────────────────
PUTER_TOKEN=$(grep -E "^PUTER_AUTH_TOKEN=" .env 2>/dev/null | cut -d= -f2 | tr -d '"' | tr -d "'" || echo "")

if [ -z "$PUTER_TOKEN" ]; then
  echo ""
  echo -e "${YELLOW}⚠️  PUTER_AUTH_TOKEN not set in .env${NC}"
  echo "   The bot will fall back to ANTHROPIC_API_KEY instead."
  echo "   To get FREE Claude access via Puter:"
  echo "   1. Go to https://puter.com → Sign up (free)"
  echo "   2. Open DevTools → Console → run:"
  echo "      puter.auth.getToken().then(t => console.log(t))"
  echo "   3. Copy the token → add to .env: PUTER_AUTH_TOKEN=your_token"
  echo ""
elif command -v node &>/dev/null && [ -f puter_bridge/server.js ]; then
  info "Starting Puter Bridge (free Claude API)..."
  node puter_bridge/server.js &
  BRIDGE_PID=$!
  echo "  Bridge PID: $BRIDGE_PID"

  # Wait up to 5 seconds for bridge to be ready
  for i in $(seq 1 10); do
    if curl -sf http://localhost:3456/health > /dev/null 2>&1; then
      ok "Puter Bridge is ready → http://localhost:3456"
      break
    fi
    sleep 0.5
  done
  if ! curl -sf http://localhost:3456/health > /dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Puter Bridge didn't respond — continuing without it${NC}"
    BRIDGE_PID=""
  fi
else
  if ! command -v node &>/dev/null; then
    echo -e "${YELLOW}⚠️  Node.js not found — skipping Puter Bridge${NC}"
    echo "   Install Node.js: https://nodejs.org"
  fi
  BRIDGE_PID=""
fi

# ── Step 2: Start FastAPI backend ─────────────────────────────────────────────
info "Starting FastAPI backend on port 8000..."

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

# Wait for backend (up to 20s)
info "Waiting for backend..."
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
  kill $BACKEND_PID 2>/dev/null || true
  [ -n "$BRIDGE_PID" ] && kill $BRIDGE_PID 2>/dev/null || true
  exit 1
fi

ok "Backend is ready → http://localhost:8000"
blue "API docs: http://localhost:8000/docs"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "═══════════════════════════════════════════════════"
echo "  Services running:"
[ -n "$BRIDGE_PID" ] && echo -e "  ${GREEN}✅ Puter Bridge${NC}  → http://localhost:3456  (FREE Claude)"
echo -e "  ${GREEN}✅ FastAPI backend${NC} → http://localhost:8000"
echo ""
echo "  Starting Telegram bot now..."
echo "  Press Ctrl+C to stop everything."
echo "═══════════════════════════════════════════════════"
echo ""

# ── Cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
  echo ""
  info "Shutting down..."
  kill $BACKEND_PID 2>/dev/null && ok "Backend stopped" || true
  [ -n "$BRIDGE_PID" ] && (kill $BRIDGE_PID 2>/dev/null && ok "Puter Bridge stopped" || true)
  exit 0
}
trap cleanup INT TERM

# ── Step 3: Start Telegram bot (foreground — Ctrl+C stops everything) ─────────
python -m bot.telegram_bot

cleanup
