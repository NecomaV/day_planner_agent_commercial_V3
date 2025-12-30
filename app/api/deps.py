from fastapi import Header, HTTPException

from app.settings import settings


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def get_user_id(x_user_id: int | None = Header(default=None, alias="X-User-Id")) -> int:
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header is required")
    try:
        user_id = int(x_user_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="X-User-Id must be an integer")
    if user_id <= 0:
        raise HTTPException(status_code=400, detail="X-User-Id must be positive")
    return user_id
