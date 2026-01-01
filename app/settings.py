from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILES = [PROJECT_ROOT / ".env", ".env"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILES, extra="ignore")

    # DB
    DATABASE_URL: str = "sqlite:///./data/planner.db"
    APP_DATA_DIR: str | None = None

    # App
    TZ: str = "Asia/Almaty"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000

    # API
    API_KEY: str | None = None
    API_KEY_SECRET: str | None = None
    REDIS_URL: str | None = None
    API_RATE_LIMIT_PER_MIN: int = 60
    API_RATE_LIMIT_READ_PER_MIN: int = 300
    API_RATE_LIMIT_AI_PER_MIN: int = 10
    API_RATE_LIMIT_AUTH_PER_MIN_IP: int = 10
    API_RATE_WINDOW_SEC: int = 60
    AUTH_FAIL_MAX: int = 10
    AUTH_FAIL_WINDOW_SEC: int = 600
    AUTH_BLOCK_SEC: int = 600

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
    AI_MAX_TEXT_CHARS: int = 2000
    AI_MAX_AUDIO_SECONDS: int = 120
    AI_MAX_AUDIO_BYTES: int = 6_000_000
    AI_REQUESTS_PER_DAY: int = 50
    AI_TRANSCRIBE_MINUTES_PER_DAY: int = 30
    AI_ERROR_WINDOW_SEC: int = 300
    AI_ERROR_THRESHOLD: int = 5
    AI_COOLDOWN_SEC: int = 300
    AI_TIMEOUT_SEC: int = 20
    AI_RETRY_MAX: int = 1
    AI_RETRY_BACKOFF_SEC: float = 0.5

    # Telegram
    TELEGRAM_BOT_TOKEN: str | None = None
    BOT_COOLDOWN_SEC: float = 1.5
    BOT_BURST_MAX: int = 3
    BOT_BURST_WINDOW_SEC: int = 6
    BOT_HEAVY_COOLDOWN_SEC: float = 8.0
    BOT_DEDUPE_WINDOW_SEC: float = 8.0


settings = Settings()
