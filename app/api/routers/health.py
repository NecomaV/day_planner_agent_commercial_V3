from __future__ import annotations

import datetime as dt
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require_api_key
from app.db import get_db
from app.schemas.health import CheckinIn, CheckinOut
from app import crud

router = APIRouter(prefix="/health", tags=["health"], dependencies=[Depends(require_api_key)])


@router.post("/checkin", response_model=CheckinOut)
def upsert_checkin(payload: CheckinIn, db: Session = Depends(get_db), user=Depends(get_current_user)):
    day = payload.day or dt.date.today()
    checkin = crud.upsert_daily_checkin(
        db,
        user.id,
        day,
        sleep_hours=payload.sleep_hours,
        energy_level=payload.energy_level,
        water_ml=payload.water_ml,
        notes=payload.notes,
    )
    return checkin


@router.get("/today", response_model=CheckinOut | None)
def get_today(db: Session = Depends(get_db), user=Depends(get_current_user)):
    day = dt.date.today()
    return crud.get_daily_checkin(db, user.id, day)
