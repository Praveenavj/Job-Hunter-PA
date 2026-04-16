# Procfile
# Run all services in one container

bridge: node puter_bridge/server.js
backend: uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
bot: python -m bot.telegram_bot