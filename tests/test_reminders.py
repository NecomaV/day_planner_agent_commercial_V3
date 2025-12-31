import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import crud
from app.models.base import Base


def test_list_due_reminders():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    now = dt.datetime.utcnow().replace(microsecond=0)
    with SessionLocal() as db:
        user1 = crud.get_or_create_user_by_chat_id(db, chat_id="tg-1")
        user2 = crud.get_or_create_user_by_chat_id(db, chat_id="tg-2")

        due1 = crud.create_reminder(
            db,
            user1.id,
            due_at=now - dt.timedelta(minutes=1),
            channel="telegram",
            payload_json='{"chat_id": "tg-1", "text": "hello"}',
        )
        crud.create_reminder(
            db,
            user1.id,
            due_at=now + dt.timedelta(minutes=10),
            channel="telegram",
            payload_json='{"chat_id": "tg-1", "text": "later"}',
        )
        due2 = crud.create_reminder(
            db,
            user2.id,
            due_at=now - dt.timedelta(minutes=2),
            channel="telegram",
            payload_json='{"chat_id": "tg-2", "text": "hey"}',
        )
        sent = crud.create_reminder(
            db,
            user2.id,
            due_at=now - dt.timedelta(minutes=3),
            channel="telegram",
            payload_json='{"chat_id": "tg-2", "text": "sent"}',
        )
        sent.sent_at = now
        db.add(sent)
        db.commit()

        due = crud.list_due_reminders(db, now)

    ids = {reminder.id for reminder in due}
    assert ids == {due1.id, due2.id}
