from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import crud
from app.models.user import User
from app.db import get_db
from app.settings import settings


def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    if not value.lower().startswith("bearer "):
        return None
    token = value.split(" ", 1)[1].strip()
    return token or None


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_key: str | None = Header(default=None, alias="X-User-Key"),
) -> User:
    token = _extract_bearer_token(authorization) or (x_user_key.strip() if x_user_key else None)
    if not token:
        raise HTTPException(status_code=401, detail="Missing user API key")
    try:
        user = crud.get_user_by_api_key(db, token)
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Server auth is not configured")
    if not user:
        raise HTTPException(status_code=401, detail="Invalid user API key")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")
    return user
