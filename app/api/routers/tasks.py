from __future__ import annotations

import datetime as dt
from fastapi import APIRouter, Depends, HTTPException, Query

from sqlalchemy.orm import Session

from app.db import get_db
from app.api.deps import get_current_user, require_api_key
from app.schemas.tasks import TaskCreate, TaskOut, TaskUpdate, PlanOut, TaskLocationIn
from app import crud

router = APIRouter(prefix="/tasks", tags=["tasks"], dependencies=[Depends(require_api_key)])


@router.post("", response_model=TaskOut)
def create_task(payload: TaskCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    return crud.create_task(db, user.id, payload)


@router.get("/day", response_model=list[TaskOut])
def list_day(
    date: dt.date = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    return crud.list_scheduled_for_day(db, user.id, date)


@router.get("/backlog", response_model=list[TaskOut])
def list_backlog(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return crud.list_backlog(db, user.id)


@router.get("/plan", response_model=PlanOut)
def get_plan(
    date: dt.date = Query(..., description="YYYY-MM-DD"),
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    scheduled = crud.list_scheduled_for_day(db, user.id, date)
    backlog = crud.list_backlog(db, user.id)
    return PlanOut(date=date.isoformat(), scheduled=scheduled, backlog=backlog)


@router.patch("/{task_id}", response_model=TaskOut)
def patch_task(task_id: int, payload: TaskUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    t = crud.update_task(db, user.id, task_id, payload)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    return t


@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ok = crud.delete_task(db, user.id, task_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"ok": True}


@router.post("/{task_id}/location", response_model=TaskOut)
def set_task_location(
    task_id: int,
    payload: TaskLocationIn,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    task = crud.update_task_location(
        db,
        user.id,
        task_id,
        lat=payload.lat,
        lon=payload.lon,
        radius_m=payload.radius_m,
        label=payload.label,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task
