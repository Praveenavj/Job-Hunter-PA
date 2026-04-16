# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Expose port for backend
EXPOSE 8000

# Run backend + bot (bot runs in background)
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port $PORT & sleep 5 && python -m bot.telegram_bot"]
