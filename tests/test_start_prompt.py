import datetime as dt

from app import crud


def test_start_prompt_flow(test_app):
    _, TestingSessionLocal = test_app
    now = dt.datetime(2026, 1, 1, 9, 0)
    with TestingSessionLocal() as db:
        user = crud.get_or_create_user_by_chat_id(db, chat_id="start-prompt")
        task = crud.create_task_fields(
            db,
            user.id,
            title="Prompt me",
            planned_start=now - dt.timedelta(minutes=1),
            planned_end=now + dt.timedelta(minutes=29),
            estimate_minutes=30,
            priority=2,
        )
        tasks = crud.list_tasks_for_start_prompt(db, user.id, now, window_minutes=10)
        assert any(t.id == task.id for t in tasks)
        crud.mark_start_prompt_sent(db, user.id, task.id, now)
        db.commit()
        pending = crud.list_pending_start_prompts(db, user.id)
        assert any(t.id == task.id for t in pending)
        crud.mark_task_started(db, user.id, task.id, now)
        db.commit()
        pending = crud.list_pending_start_prompts(db, user.id)
        assert all(t.id != task.id for t in pending)
