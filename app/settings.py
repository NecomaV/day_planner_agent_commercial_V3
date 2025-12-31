from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILES = [PROJECT_ROOT / ".env", ".env"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    # DB
    DATABASE_URL: str = "sqlite:///./data/planner.db"

    # App
    TZ: str = "Asia/Almaty"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000

    # API
    API_KEY: str | None = None

    # Reminders
    REMINDER_LEAD_MIN: int = 10
    CALL_FOLLOWUP_DAYS: int = 1
    DELAY_GRACE_MIN: int = 10
    LOCATION_STALE_MIN: int = 60

    # AI (optional)
    OPENAI_API_KEY: str | None = None
    OPENAI_TRANSCRIBE_MODEL: str = "gpt-4o-mini-transcribe"
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    OPENAI_TRANSCRIBE_LANGUAGE: str = "ru"

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None


settings = Settings()
