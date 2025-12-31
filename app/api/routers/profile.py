from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_user_id, require_api_key
from app.db import get_db
from app.schemas.profile import ProfileOut, ProfilePatch
from app import crud

router = APIRouter(prefix="/profile", tags=["profile"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    user = crud.get_user(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("", response_model=ProfileOut)
def patch_profile(payload: ProfilePatch, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    data = payload.model_dump(exclude_unset=True)
    user = crud.update_user_fields(db, user_id, **data)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
