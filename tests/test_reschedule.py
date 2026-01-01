import datetime as dt

from app import crud


def test_reschedule_task_updates_times(test_app):
    _, TestingSessionLocal = test_app
    with TestingSessionLocal() as db:
        user = crud.get_or_create_user_by_chat_id(db, chat_id="resched")
        start = dt.datetime(2026, 1, 1, 9, 0)
        end = dt.datetime(2026, 1, 1, 9, 30)
        task = crud.create_task_fields(
            db,
            user.id,
            title="Test",
            planned_start=start,
            planned_end=end,
            estimate_minutes=30,
            priority=2,
        )
        target_date = dt.date(2026, 1, 2)
        target_time = dt.time(14, 0)
        updated = crud.reschedule_task(
            db,
            user.id,
            task_id=task.id,
            target_date=target_date,
            target_time=target_time,
            duration_min=None,
        )
        assert updated is not None
        assert updated.planned_start == dt.datetime(2026, 1, 2, 14, 0)
        assert updated.planned_end == dt.datetime(2026, 1, 2, 14, 30)
