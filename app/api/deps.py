from fastapi import Header, HTTPException


def get_user_id(x_user_id: int | None = Header(default=None, alias="X-User-Id")) -> int:
    if x_user_id is None:
        raise HTTPException(status_code=401, detail="X-User-Id header is required")
    return int(x_user_id)
