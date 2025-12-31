import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import crud
from app.db import get_db
from app.main import create_app
from app.models.base import Base
from app.settings import settings


@pytest.fixture()
def test_app():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(engine)

    app = create_app()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return app, TestingSessionLocal


@pytest.fixture()
def client(test_app):
    app, _ = test_app
    return TestClient(app)


@pytest.fixture()
def auth_headers(test_app):
    app, TestingSessionLocal = test_app
    settings.API_KEY_SECRET = "test-secret"
    settings.API_KEY = None
    with TestingSessionLocal() as db:
        user = crud.get_or_create_user_by_chat_id(db, chat_id="test")
        token = crud.rotate_user_api_key(db, user.id)
    return {"Authorization": f"Bearer {token}"}
