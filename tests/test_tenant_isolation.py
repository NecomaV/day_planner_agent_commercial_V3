from fastapi.testclient import TestClient

from app import crud
from app.settings import settings


def test_token_cannot_access_other_user(test_app):
    app, SessionLocal = test_app
    settings.API_KEY_SECRET = "test-secret"
    settings.API_KEY = None

    with SessionLocal() as db:
        user1 = crud.get_or_create_user_by_chat_id(db, chat_id="user-1")
        user2 = crud.get_or_create_user_by_chat_id(db, chat_id="user-2")
        token1 = crud.rotate_user_api_key(db, user1.id)
        token2 = crud.rotate_user_api_key(db, user2.id)

    client = TestClient(app)
    headers1 = {"Authorization": f"Bearer {token1}"}
    headers2 = {"Authorization": f"Bearer {token2}"}

    resp = client.post("/tasks", json={"title": "Secret task"}, headers=headers1)
    assert resp.status_code == 200
    task_id = resp.json()["id"]

    resp = client.delete(f"/tasks/{task_id}", headers=headers2)
    assert resp.status_code == 404
