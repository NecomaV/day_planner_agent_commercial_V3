from __future__ import annotations

import datetime as dt
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.api.deps import get_user_id
from app.services.autoplan import autoplan_days

router = APIRouter(prefix="/autoplan", tags=["autoplan"])


@router.post("", response_model=list[dict])
def run_autoplan(
    days: int = Query(1, ge=1, le=14),
    start_date: dt.date | None = Query(None, description="YYYY-MM-DD; default=today"),
    db: Session = Depends(get_db),
    user_id: int = Depends(get_user_id),
):
    start = start_date or dt.date.today()
    return autoplan_days(db, user_id, start, days=days)
