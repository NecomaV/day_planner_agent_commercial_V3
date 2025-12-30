from __future__ import annotations

import datetime as dt
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import crud
from app.db import get_db
from app.api.deps import get_user_id, require_api_key
from app.services.autoplan import autoplan_days

router = APIRouter(prefix="/autoplan", tags=["autoplan"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=list[dict])
def run_autoplan(
    days: int = Query(1, ge=1, le=14),
    start_date: dt.date | None = Query(None, description="YYYY-MM-DD; default=today"),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_user_id),
):
    start = start_date or dt.date.today()
    routine = crud.get_routine(db, user_id)
    return autoplan_days(db, user_id, routine, days=days, start_date=start)
