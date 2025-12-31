from __future__ import annotations

import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_user_id, require_api_key
from app.db import get_db
from app.schemas.health import HabitOut, HabitCreate, HabitLogIn
from app import crud

router = APIRouter(prefix="/habits", tags=["habits"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[HabitOut])
def list_habits(db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    return crud.list_habits(db, user_id, active_only=False)


@router.post("", response_model=HabitOut)
def create_habit(payload: HabitCreate, db: Session = Depends(get_db), user_id: int = Depends(get_user_id)):
    return crud.upsert_habit(
        db,
        user_id,
        name=payload.name,
        target_per_day=payload.target_per_day,
        unit=payload.unit,
    )


@router.post("/{habit_id}/log")
def log_habit(
    habit_id: int,
    payload: HabitLogIn,
    db: Session = Depends(get_db),
    user_id: int = Depends(get_user_id),
):
    day = payload.day or dt.date.today()
    habit = crud.get_habit(db, user_id, habit_id)
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    crud.log_habit(db, user_id, habit_id, day, value=payload.value)
    return {"ok": True}
