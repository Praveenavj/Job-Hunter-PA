# Job Hunter Personal Assistant (Telegram Bot + FastAPI)

AI-powered Telegram assistant for job search, resume tailoring, outreach emails, interview prep, application tracking, daily digests, and reminders.

## Current architecture (v3)

- **Backend:** FastAPI (`app/main.py`)
- **Bot:** aiogram 3 (`bot/telegram_bot.py`)
- **Database:** SQLite (`data/job_hunter.db`)
- **LLM cascade (in order):**
  1. **Puter Bridge** (free Claude via `puter_bridge/server.js`)
  2. **Anthropic API** (paid)
  3. **Ollama** (local fallback)

---

## Quick start (latest recommended way)

### 1) Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

If you plan to use **Puter Bridge**, also install Node deps once:

```bash
cd puter_bridge
npm install
cd ..
```

### 2) Create/update `.env`

Create `.env` in project root (same level as `README.md`).

Minimum required:

```env
TELEGRAM_BOT_TOKEN=...
BACKEND_BASE_URL=http://localhost:8000
```

LLM options (use at least one):

```env
# Option A (recommended free)
PUTER_AUTH_TOKEN=...
PUTER_BRIDGE_URL=http://localhost:3456
PUTER_MODEL=claude-sonnet-4-5

# Option B (paid Claude API)
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-sonnet-4-5

# Option C (local)
OLLAMA_API_URL=http://localhost:11434/v1/chat/completions
OLLAMA_MODEL=mistral
```

Optional integrations:

```env
# Job sources
ADZUNA_APP_ID=...
ADZUNA_APP_KEY=...

# Gmail OAuth (for /outreach send-now)
GMAIL_CLIENT_ID=...
GMAIL_CLIENT_SECRET=...
OAUTH_REDIRECT_URL=http://localhost:8000/oauth/gmail/callback

# App security + storage
APP_SECRET_KEY=change-me
SQLITE_DB_PATH=./data/job_hunter.db

# Scheduler trigger protection
CRON_SECRET=change-this-secret
```

### 3) Start backend + bot together

```bash
bash scripts/start_all.sh
```

This script:

- checks `.env`
- starts FastAPI on port `8000`
- waits for `/health`
- starts Telegram bot in foreground

---

## Optional: start Puter Bridge (for free Claude)

Open another terminal:

```bash
node puter_bridge/server.js
```

Health check:

- `http://localhost:3456/health`

---

## Other run modes

### Bot only (when backend is already running remotely)

```bash
bash scripts/run_bot.sh
```

### Manual mode (2 terminals)

Terminal 1:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Terminal 2:

```bash
python -m bot.telegram_bot
```

---

## Verify services

- Backend health: `http://localhost:8000/health`
- API docs: `http://localhost:8000/docs`
- Bridge health (if used): `http://localhost:3456/health`

---

## Main bot commands

- `/jobs` — search jobs (multi-source)
- `/digest` — save daily search (9 AM digest)
- `/resume` — upload/revise resume
- `/tailor` — tailor to specific job description
- `/email` — draft email
- `/outreach` — outreach draft + optional Gmail send
- `/gmail_connect`, `/gmail_status`, `/gmail_disconnect`
- `/track`, `/myapps`, `/update`, `/export`
- `/interview`, `/practice`
- `/addstar`, `/mystars`
- `/remindme`, `/myreminders`, `/testalert`
- `/status`, `/stop`, `/help`

---

## Test suite

```bash
bash scripts/test_all.sh
```

---

## Deployment notes

- `Dockerfile.combined` builds Python + Node runtime.
- `railway.json` is configured to deploy from that Dockerfile.
- `Procfile` includes process definitions for bridge/backend/bot.

For Railway single-service backend deployment, ensure your environment variables are set in Railway and `/health` is reachable.
