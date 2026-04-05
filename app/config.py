from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8000

    openclaw_api_url: str = "http://localhost:11434/v1/chat/completions"
    openclaw_api_key: str = ""
    openclaw_model: str = "openclaw"

    notion_api_key: str = ""
    notion_database_id: str = ""

    telegram_bot_token: str = ""
    backend_base_url: str = "http://localhost:8000"

    gmail_client_id: str = ""
    gmail_client_secret: str = ""
    oauth_redirect_url: str = "http://localhost:8000/oauth/gmail/callback"
    app_secret_key: str = "change-me-in-production"
    sqlite_db_path: str = "./data/job_hunter.db"


settings = Settings()
