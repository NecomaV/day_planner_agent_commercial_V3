from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # DB
    DATABASE_URL: str = "sqlite:///./data/planner.db"

    # App
    TZ: str = "Asia/Almaty"

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None


settings = Settings()
