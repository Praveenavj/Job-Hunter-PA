#!/bin/bash
# ============================================================
#  Job Hunter PA – Run ONLY the Telegram bot
#  Use this when the backend is already running elsewhere
#  (e.g. on Railway, or in another terminal window)
#
#  Usage: bash scripts/run_bot.sh
# ============================================================
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

if [ ! -f .env ]; then
  echo -e "${RED}❌ .env not found${NC}"
  exit 1
fi

# Read BACKEND_BASE_URL from .env for display
BACKEND_URL=$(grep -E "^BACKEND_BASE_URL=" .env 2>/dev/null | cut -d= -f2 || echo "http://localhost:8000")

echo ""
echo "═══════════════════════════════════════════"
echo "  Job Hunter PA – Bot only"
echo "  Backend: $BACKEND_URL"
echo "═══════════════════════════════════════════"
echo ""

# Quick backend health check
echo -e "${YELLOW}▶ Checking backend...${NC}"
if curl -sf "${BACKEND_URL}/health" > /dev/null 2>&1; then
  echo -e "${GREEN}✅ Backend is reachable${NC}"
else
  echo -e "${RED}❌ Backend not reachable at ${BACKEND_URL}${NC}"
  echo "  Make sure the backend is running first:"
  echo "  uvicorn app.main:app --host 0.0.0.0 --port 8000"
  echo ""
  echo "  Or set BACKEND_BASE_URL in .env to point to your deployed backend."
  echo ""
  read -p "  Start anyway? (y/N): " CONFIRM
  if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    exit 1
  fi
fi

echo ""
echo -e "${YELLOW}▶ Starting Telegram bot...${NC}"
python -m bot.telegram_bot
