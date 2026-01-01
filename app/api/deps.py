from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.abuse import get_auth_failure_tracker
from app import crud
from app.models.user import User
from app.db import get_db
from app.rate_limit import get_rate_limiter
from app.settings import settings


def _client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    if not settings.API_KEY:
        return
    if x_api_key != settings.API_KEY:
        limiter = get_rate_limiter()
        ip = _client_ip(request)
        result = limiter.allow(f"auth:{ip}", settings.API_RATE_LIMIT_AUTH_PER_MIN_IP, settings.API_RATE_WINDOW_SEC)
        if not result.allowed:
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(result.retry_after)},
            )
        raise HTTPException(status_code=401, detail="Invalid API key")


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    value = authorization.strip()
    if not value.lower().startswith("bearer "):
        return None
    token = value.split(" ", 1)[1].strip()
    return token or None


def _authenticate_user(
    request: Request,
    db: Session,
    authorization: str | None,
    x_user_key: str | None,
) -> User:
    ip = _client_ip(request)
    tracker = get_auth_failure_tracker()
    ip_block = tracker.is_blocked(f"ip:{ip}")
    if ip_block.blocked:
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(ip_block.retry_after)},
        )

    token = _extract_bearer_token(authorization) or (x_user_key.strip() if x_user_key else None)
    if not token:
        tracker.record_failure(f"ip:{ip}")
        raise HTTPException(status_code=401, detail="Missing user API key")

    prefix = token[:8]
    prefix_block = tracker.is_blocked(f"prefix:{prefix}")
    if prefix_block.blocked:
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(prefix_block.retry_after)},
        )
    try:
        user = crud.get_user_by_api_key(db, token)
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Server auth is not configured")
    if not user:
        tracker.record_failure(f"ip:{ip}")
        tracker.record_failure(f"prefix:{prefix}")
        raise HTTPException(status_code=401, detail="Invalid user API key")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="User is inactive")

    request.state.user_id = user.id
    return user


def _apply_rate_limit(user: User, key_suffix: str, limit: int) -> None:
    limiter = get_rate_limiter()
    key = f"token:{user.api_key_prefix or user.id}{key_suffix}"
    result = limiter.allow(key, limit, settings.API_RATE_WINDOW_SEC)
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(result.retry_after)},
        )


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_key: str | None = Header(default=None, alias="X-User-Key"),
) -> User:
    user = _authenticate_user(request, db, authorization, x_user_key)
    _apply_rate_limit(user, "", settings.API_RATE_LIMIT_PER_MIN)
    crud.touch_user_api_key(db, user.id)
    return user


def get_current_user_read(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_key: str | None = Header(default=None, alias="X-User-Key"),
) -> User:
    user = _authenticate_user(request, db, authorization, x_user_key)
    _apply_rate_limit(user, ":read", settings.API_RATE_LIMIT_READ_PER_MIN)
    crud.touch_user_api_key(db, user.id)
    return user


def get_current_user_ai(
    request: Request,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_user_key: str | None = Header(default=None, alias="X-User-Key"),
) -> User:
    user = get_current_user(request, db, authorization, x_user_key)
    _apply_rate_limit(user, ":ai", settings.API_RATE_LIMIT_AI_PER_MIN)
    return user
