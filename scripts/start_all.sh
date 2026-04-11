#!/bin/bash
# ============================================================
#  Job Hunter PA – Full Startup Script
#  Starts: Puter Bridge → FastAPI Backend → Telegram Bot
#  Usage: bash scripts/start_all.sh
# ============================================================
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Colour helpers ───────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'  # No Color

info()  { echo -e "${YELLOW}▶ $1${NC}"; }
ok()    { echo -e "${GREEN}✅ $1${NC}"; }
error() { echo -e "${RED}❌ $1${NC}"; }
step()  { echo -e "\n${BLUE}━━━ $1 ━━━${NC}\n"; }

# ── Cleanup trap ─────────────────────────────────────────────
cleanup() {
  echo ""
  info "Shutting down..."
  
  # Kill bridge if running
  if [ -n "$BRIDGE_PID" ] && kill -0 "$BRIDGE_PID" 2>/dev/null; then
    kill "$BRIDGE_PID" 2>/dev/null && ok "Puter Bridge stopped" || true
  fi
  
  # Kill backend if running
  if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null && ok "Backend stopped" || true
  fi
  
  exit 0
}
trap cleanup SIGINT SIGTERM

# ── Header ───────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════"
echo "  🤖 Job Hunter PA – Starting Up"
echo "═══════════════════════════════════════════"
echo ""

# ── Prerequisites ────────────────────────────────────────────
step "Checking prerequisites"

if [ ! -f .env ]; then
  error ".env file not found"
  echo "  → Run: cp .env.example .env  then fill in your values"
  exit 1
fi

if ! command -v uvicorn &>/dev/null; then
  error "uvicorn not found"
  echo "  → Run: pip install -r requirements.txt"
  exit 1
fi

if ! command -v node &>/dev/null; then
  error "Node.js not found (required for Puter Bridge)"
  echo "  → Install: brew install node  (macOS) or visit nodejs.org"
  exit 1
fi

# Load environment variables for shell commands
set -a && source .env && set +a

# ── Step 1: Start Puter Bridge ───────────────────────────────
step "Starting Puter Bridge (free Claude access)"

# Check if bridge is already running
if curl -sf http://localhost:3456/health > /dev/null 2>&1; then
  ok "Puter Bridge already running on port 3456"
else
  info "Launching Puter Bridge..."
  
  # Start bridge in background
  node puter_bridge/server.js &
  BRIDGE_PID=$!
  echo "  Bridge PID: $BRIDGE_PID"
  
  # Wait for bridge to be ready (max 15s)
  info "Waiting for bridge to initialize..."
  BRIDGE_READY=false
  for i in $(seq 1 15); do
    if curl -sf http://localhost:3456/health | grep -q '"token_set":true'; then
      BRIDGE_READY=true
      break
    fi
    sleep 1
    echo -n "."
  done
  echo ""
  
  if [ "$BRIDGE_READY" = false ]; then
    error "Puter Bridge failed to start within 15 seconds"
    echo "  → Check: node puter_bridge/server.js (run manually for errors)"
    echo "  → Verify: PUTER_AUTH_TOKEN is set and valid in .env"
    # Continue anyway — llm_client.py will fallback to Anthropic/Ollama
  else
    ok "Puter Bridge ready at http://localhost:3456"
    
    # Show token status (sanitized)
    TOKEN_LEN=${#PUTER_AUTH_TOKEN}
    if [ "$TOKEN_LEN" -gt 40 ]; then
      echo "  🔑 Token: ✅ Loaded (${TOKEN_LEN} chars)"
    fi
  fi
fi

# ── Step 2: Start FastAPI Backend ────────────────────────────
step "Starting FastAPI backend"

info "Launching uvicorn on port 8000..."
uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --reload \
  --reload-exclude '.venv' \
  --reload-exclude '__pycache__' \
  --reload-exclude 'data' \
  --reload-exclude '*.db' \
  --reload-exclude '*.xlsx' \
  --reload-exclude 'puter_bridge' &

BACKEND_PID=$!
echo "  Backend PID: $BACKEND_PID"

# Wait for backend health check
info "Waiting for backend to respond..."
BACKEND_READY=false
for i in $(seq 1 20); do
  if curl -sf http://localhost:8000/health > /dev/null 2>&1; then
    BACKEND_READY=true
    break
  fi
  sleep 1
  echo -n "."
done
echo ""

if [ "$BACKEND_READY" = false ]; then
  error "Backend failed to start within 20 seconds"
  cleanup
  exit 1
fi

ok "Backend ready at http://localhost:8000"
echo "  📚 API docs: http://localhost:8000/docs"
echo "  🔍 Health:   http://localhost:8000/health"

# ── Step 3: Start Telegram Bot ───────────────────────────────
step "Starting Telegram bot"

echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  🚀 All systems go! Bot is listening...${NC}"
echo -e "${GREEN}  Press Ctrl+C to stop everything${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Run bot in foreground so Ctrl+C triggers cleanup trap
python -m bot.telegram_bot

# ── Cleanup (only reached if bot exits normally) ─────────────
cleanup