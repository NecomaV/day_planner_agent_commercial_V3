import datetime as dt
import logging
import os
from pathlib import Path
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from app.settings import PROJECT_ROOT, settings

logger = logging.getLogger("app.db")

def _ensure_sqlite_dir(url: str) -> None:
    if not url.startswith("sqlite:///"):
        return
    path = url.replace("sqlite:///", "", 1)
    dir_path = os.path.dirname(path) if os.path.dirname(path) else "."
    if dir_path and not os.path.exists(dir_path):
        os.makedirs(dir_path, exist_ok=True)

def _resolve_sqlite_url(url: str) -> str:
    if not url.startswith("sqlite:///"):
        return url
    raw_path = url.replace("sqlite:///", "", 1)
    path = Path(raw_path)
    if path.is_absolute():
        abs_path = path
    else:
        base = Path(settings.APP_DATA_DIR) if settings.APP_DATA_DIR else PROJECT_ROOT
        abs_path = (base / path).resolve()
    return f"sqlite:///{abs_path.as_posix()}"


def resolve_database_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        return _resolve_sqlite_url(url)
    return url


RESOLVED_DATABASE_URL = resolve_database_url(settings.DATABASE_URL)

_ensure_sqlite_dir(RESOLVED_DATABASE_URL)

engine = create_engine(
    RESOLVED_DATABASE_URL,
    connect_args={"check_same_thread": False} if RESOLVED_DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)

if engine.dialect.name == "sqlite":
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def _sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    raw_path = url.replace("sqlite:///", "", 1)
    return Path(raw_path)


def _describe_sqlite_path(path: Path | None) -> dict:
    if not path:
        return {"sqlite_path": None, "exists": False, "size_bytes": 0, "mtime": None}
    abs_path = path if path.is_absolute() else (Path.cwd() / path).resolve()
    try:
        stat = abs_path.stat()
        mtime = dt.datetime.fromtimestamp(stat.st_mtime).isoformat()
        return {
            "sqlite_path": str(abs_path),
            "exists": True,
            "size_bytes": int(stat.st_size),
            "mtime": mtime,
        }
    except FileNotFoundError:
        return {"sqlite_path": str(abs_path), "exists": False, "size_bytes": 0, "mtime": None}


def describe_db() -> dict:
    dialect = engine.dialect.name
    info = {"dialect": dialect, "cwd": str(Path.cwd())}
    if dialect == "sqlite":
        info.update(_describe_sqlite_path(_sqlite_path_from_url(RESOLVED_DATABASE_URL)))
    return info


def log_db_startup() -> None:
    info = describe_db()
    logger.info(
        "db_startup dialect=%s cwd=%s sqlite_path=%s exists=%s size_bytes=%s mtime=%s",
        info.get("dialect"),
        info.get("cwd"),
        info.get("sqlite_path"),
        info.get("exists"),
        info.get("size_bytes"),
        info.get("mtime"),
    )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


log_db_startup()
