from app import crud
from app.debug_info import build_db_debug


def test_debug_db_endpoint_matches_build(client, auth_headers, test_app):
    _, TestingSessionLocal = test_app
    token = auth_headers["Authorization"].split()[-1]
    with TestingSessionLocal() as db:
        user = crud.get_user_by_api_key(db, token)
        assert user is not None
        crud.create_task_fields(db, user.id, title="Debug task", estimate_minutes=10, priority=2)
        expected = build_db_debug(db, user.id)

    resp = client.get("/debug/db", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["dialect"] == expected["dialect"]
    assert data["tasks_total"] == expected["tasks_total"]
    if data["dialect"] == "sqlite":
        assert data["sqlite_path"] == expected["sqlite_path"]
