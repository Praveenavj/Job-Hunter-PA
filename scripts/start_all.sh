#!/bin/bash
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -f .env ]; then
  echo "ERROR: .env file not found. Copy .env.example and fill in your values."
  exit 1
fi

echo "Starting Job Hunter PA..."
echo ""

# Start the FastAPI backend in the background
echo "[1/2] Starting backend on http://localhost:8000"
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
BACKEND_PID=$!

# Give the backend 2 seconds to start before the bot connects to it
sleep 2

# Start the Telegram bot in the foreground
echo "[2/2] Starting Telegram bot"
python -m bot.telegram_bot

# If the bot exits, also kill the backend
kill $BACKEND_PID 2>/dev/null