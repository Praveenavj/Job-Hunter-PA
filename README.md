# Job Hunter Personal Assistant (Telegram Bot + Backend)

This project provides a Telegram-based personal assistant for job hunting with these services:

1. See jobs available
2. Revise resume
3. Draft email
4. Track job on Notion
5. Prepare for interviews

It uses:
- **FastAPI** backend
- **aiogram** Telegram bot
- **OpenClaw-compatible Chat Completions API** for AI responses
- **Notion API** for job application tracking
- **Gmail API** for optional outreach email sending

## Project Structure

- `app/main.py` — FastAPI endpoints
- `app/services/openclaw_client.py` — OpenClaw API integration
- `app/services/job_service.py` — job search integration (Remotive API)
- `app/services/notion_service.py` — Notion tracking
- `bot/telegram_bot.py` — Telegram bot commands and messaging

## 1) Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
```

Update `.env` with your values:

- `TELEGRAM_BOT_TOKEN`
- `BACKEND_BASE_URL` (default: `http://localhost:8000`)
- `OPENCLAW_API_URL` (OpenClaw endpoint)
- `OPENCLAW_API_KEY` (if needed)
- `OPENCLAW_MODEL`
- `NOTION_API_KEY` and `NOTION_DATABASE_ID` (for `/track`)
- `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET` (for Gmail OAuth)
- `OAUTH_REDIRECT_URL` (must match Google Cloud OAuth redirect URI)
- `APP_SECRET_KEY` (used to sign OAuth state and encrypt refresh tokens)
- `SQLITE_DB_PATH` (default `./data/job_hunter.db`)

## 2) Run backend

```bash
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 3) Run Telegram bot

Open a second terminal:

```bash
python3 -m bot.telegram_bot
```

## Telegram Commands

- `/jobs <role> | <location> | <limit>`
- `/resume <target_role> || <resume_text> || <skill1,skill2>`
- `/email <purpose> || <recipient_name> || <context> || <tone>`
- `/outreach <to_email> || <recipient_name> || <role> || <company> || <resume_text> || <tone> || <send_now:true|false>`
- `/gmail_connect`
- `/gmail_status`
- `/gmail_disconnect`
- `/track <company> || <role> || <status> || <link> || <notes>`
- `/interview <role> || <company> || <focus1,focus2>`
- `/help`

## Bot UI

The bot now shows a menu-style keyboard:

1. See jobs available
2. Revise resume
3. Draft email
4. Track job on Notion
5. Prepare for interviews

Option 3 uses an LLM to generate outreach emails and can optionally send them via Gmail API.

## Multi-user Gmail OAuth flow (recommended)

1. User runs `/gmail_connect` in Telegram.
2. Bot sends an OAuth link from backend endpoint `/gmail/connect-link`.
3. User authorizes Google access in browser.
4. Google redirects to `/oauth/gmail/callback`.
5. Backend stores user refresh token encrypted in SQLite.
6. User runs `/outreach ... || true` to send from their own Gmail.

This avoids collecting user credentials in Telegram messages.

## Notes

- OpenClaw integration expects OpenAI-style `chat/completions` response shape.
- If your OpenClaw deployment uses a different schema, adjust `app/services/openclaw_client.py` accordingly.
- Notion database should contain properties with names:
  - `Company` (title)
  - `Role` (rich_text)
  - `Status` (select)
  - `Link` (url, optional)
  - `Notes` (rich_text, optional)
