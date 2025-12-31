import asyncio
import datetime as dt

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import crud
from app.bot.handlers.core import cmd_start
from app.bot.handlers.tasks import cmd_todo
from app.models.base import Base
from app.models.user import User


class DummyMessage:
    def __init__(self, message_id: int = 1):
        self.message_id = message_id
        self.replies = []
        self.text = None

    async def reply_text(self, text: str):
        self.replies.append(text)


class DummyChat:
    def __init__(self, chat_id: str):
        self.id = chat_id


class DummyUpdate:
    def __init__(self, chat_id: str, message_id: int = 1):
        self.effective_chat = DummyChat(chat_id)
        self.message = DummyMessage(message_id)


class DummyContext:
    def __init__(self, args=None):
        self.args = args or []
        self.user_data = {}


def make_session():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def test_start_creates_user(monkeypatch):
    SessionLocal = make_session()
    monkeypatch.setattr("app.bot.context.SessionLocal", SessionLocal)

    update = DummyUpdate("tg-1")
    context = DummyContext()
    asyncio.run(cmd_start(update, context))

    with SessionLocal() as db:
        user = db.execute(select(User).where(User.telegram_chat_id == "tg-1")).scalar_one_or_none()
        assert user is not None


def test_todo_scopes_to_user(monkeypatch):
    SessionLocal = make_session()
    monkeypatch.setattr("app.bot.context.SessionLocal", SessionLocal)

    with SessionLocal() as db:
        user1 = crud.get_or_create_user_by_chat_id(db, chat_id="tg-1")
        user2 = crud.get_or_create_user_by_chat_id(db, chat_id="tg-2")
        user1.onboarded = True
        user2.onboarded = True
        db.add(user1)
        db.add(user2)
        db.commit()

    update1 = DummyUpdate("tg-1", message_id=1)
    update2 = DummyUpdate("tg-2", message_id=2)

    context1 = DummyContext(args=["30", "Task", "one"])
    context2 = DummyContext(args=["45", "Task", "two"])

    asyncio.run(cmd_todo(update1, context1))
    asyncio.run(cmd_todo(update2, context2))

    today = dt.date.today()
    with SessionLocal() as db:
        tasks_user1 = crud.list_tasks_for_day(db, user1.id, today)
        tasks_user2 = crud.list_tasks_for_day(db, user2.id, today)

    assert tasks_user1 and all(t.user_id == user1.id for t in tasks_user1)
    assert tasks_user2 and all(t.user_id == user2.id for t in tasks_user2)
