from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ──────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── LLM (Anthropic Claude – primary) ────────────────────
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # ── Fallback: local Ollama ───────────────────────────────
    ollama_api_url: str = "http://localhost:11434/v1/chat/completions"
    ollama_model: str = "mistral"

    # ── Job Search APIs ──────────────────────────────────────
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""

    # ── Telegram ─────────────────────────────────────────────
    telegram_bot_token: str = ""
    backend_base_url: str = "http://localhost:8000"

    # ── Gmail OAuth ──────────────────────────────────────────
    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    oauth_redirect_url: str = "http://localhost:8000/oauth/gmail/callback"

    # ── Database ─────────────────────────────────────────────
    app_secret_key: str = "change-me-in-production"
    sqlite_db_path: str = "./data/job_hunter.db"

    # ── Scheduler ────────────────────────────────────────────
    daily_digest_hour: int = 9           # 9 AM Singapore time
    daily_digest_timezone: str = "Asia/Singapore"
    followup_reminder_days: int = 3      # remind N days after applying
    job_search_cache_hours: int = 6


settings = Settings()
