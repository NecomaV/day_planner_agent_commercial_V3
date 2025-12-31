from __future__ import annotations

import datetime as dt
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_api_key
from app.db import get_db
from app.schemas.health import HabitOut, HabitCreate, HabitLogIn
from app import crud

router = APIRouter(prefix="/habits", tags=["habits"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=list[HabitOut])
def list_habits(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return crud.list_habits(db, user.id, active_only=False)


@router.post("", response_model=HabitOut)
def create_habit(payload: HabitCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return crud.upsert_habit(
        db,
        user.id,
        name=payload.name,
        target_per_day=payload.target_per_day,
        unit=payload.unit,
    )


@router.post("/{habit_id}/log")
def log_habit(
    habit_id: int,
    payload: HabitLogIn,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    day = payload.day or dt.date.today()
    habit = crud.get_habit(db, user.id, habit_id)
    if not habit:
        raise HTTPException(status_code=404, detail="Habit not found")
    crud.log_habit(db, user.id, habit_id, day, value=payload.value)
    return {"ok": True}
