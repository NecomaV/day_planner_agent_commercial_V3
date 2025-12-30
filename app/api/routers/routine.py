from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.deps import get_user_id
from app.schemas.routine import RoutineOut, RoutinePatch
from app import crud

router = APIRouter(prefix="/routine", tags=["routine"])


@router.get("", response_model=RoutineOut)
def get_routine(db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    return crud.get_routine(db, user_id)


@router.patch("", response_model=RoutineOut)
def patch_routine(payload: RoutinePatch, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    return crud.patch_routine(db, user_id, payload)
