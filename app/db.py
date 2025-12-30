import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from app.settings import settings


def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite:///"):
        return
    path = url.replace("sqlite:///", "", 1)
    dir_path = os.path.dirname(path) if os.path.dirname(path) else "."
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)


_ensure_sqlite_dir(settings.DATABASE_URL)

engine = create_engine(
    settings.DATABASE_URL,
    connect_args={"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {},
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
