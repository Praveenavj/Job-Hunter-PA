"""
Application settings — loaded from .env file automatically.
All fields have defaults so the app starts even with a minimal .env.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load .env from project root
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path, override=True)
# ───────────────────────────────────

import os

BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ──────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Puter Bridge (FREE Claude — highest priority) ────────────────────────
    # Get token: puter.com → DevTools → puter.auth.getToken().then(t=>console.log(t))
    puter_auth_token: str = ""
    puter_bridge_port: int = 3456
    puter_model: str = "claude-sonnet-4-5"
    puter_bridge_url: str = os.getenv("PUTER_BRIDGE_URL", "http://localhost:3456")
    
    # ── LLM: Anthropic Claude (paid fallback) ────────────────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # ── LLM: Ollama (local last resort) ─────────────────────────────────────
    ollama_api_url: str = "http://localhost:11434/v1/chat/completions"
    ollama_model: str = "mistral"

    # ── Job Search APIs ──────────────────────────────────────────────────────
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    # ── Telegram ─────────────────────────────────────────────────────────────
    telegram_bot_token: str = ""
    backend_base_url: str = "http://localhost:8000"

    # ── Gmail OAuth ──────────────────────────────────────────────────────────
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    oauth_redirect_url: str = "http://localhost:8000/oauth/gmail/callback"

    # ── Database ─────────────────────────────────────────────────────────────
    app_secret_key: str = "change-me-in-production"
    sqlite_db_path: str = "./data/job_hunter.db"

    # ── Scheduler ────────────────────────────────────────────────────────────
    daily_digest_hour: int = 9            # 9 AM Singapore time
    daily_digest_timezone: str = "Asia/Singapore"
    followup_reminder_days: int = 3       # remind N days after applying
    job_search_cache_hours: int = 6

    # ── Cron trigger secret (for external schedulers like cron-job.org) ──────
    # External schedulers POST to /jobs/digest-trigger with this in X-Cron-Secret header
    cron_secret: str = "change-this-secret-in-production"


settings = Settings()