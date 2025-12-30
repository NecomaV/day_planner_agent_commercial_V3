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

    # API
    API_KEY: str | None = None

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None


settings = Settings()
