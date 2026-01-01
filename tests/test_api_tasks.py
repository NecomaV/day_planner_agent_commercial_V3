import datetime as dt

from app import crud


def test_tasks_requires_auth(client):
    resp = client.get("/tasks/plan", params={"date": "2026-01-01"})
    assert resp.status_code == 401


def test_create_and_list_task(client, auth_headers):
    payload = {
        "title": "Call supplier",
        "estimate_minutes": 20,
        "priority": 2,
    }
    create_resp = client.post("/tasks", json=payload, headers=auth_headers)
    assert create_resp.status_code == 200
    created = create_resp.json()
    assert created["title"] == "Call supplier"

    day = dt.date.today().isoformat()
    plan_resp = client.get("/tasks/plan", params={"date": day}, headers=auth_headers)
    assert plan_resp.status_code == 200
    data = plan_resp.json()
    assert data["date"] == day
    assert isinstance(data["scheduled"], list)
    assert isinstance(data["backlog"], list)


def test_week_plan_endpoint(client, auth_headers):
    today = dt.date.today()
    start = today - dt.timedelta(days=today.weekday())
    planned_start = dt.datetime.combine(start, dt.time(9, 0))
    planned_end = planned_start + dt.timedelta(minutes=30)

    payload = {
        "title": "Weekly sync",
        "planned_start": planned_start.isoformat(),
        "planned_end": planned_end.isoformat(),
        "estimate_minutes": 30,
        "priority": 2,
    }
    create_resp = client.post("/tasks", json=payload, headers=auth_headers)
    assert create_resp.status_code == 200
    created = create_resp.json()

    resp = client.get("/tasks/plan/week", params={"start": start.isoformat()}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["start"] == start.isoformat()
    assert start.isoformat() in data["days"]
    assert any(t["id"] == created["id"] for t in data["days"][start.isoformat()])


def test_api_write_visible_in_db(client, auth_headers, test_app):
    _, TestingSessionLocal = test_app
    payload = {
        "title": "API sync check",
        "estimate_minutes": 25,
        "priority": 2,
    }
    resp = client.post("/tasks", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    created = resp.json()
    token = auth_headers["Authorization"].split()[-1]

    with TestingSessionLocal() as db:
        user = crud.get_user_by_api_key(db, token)
        assert user is not None
        task = crud.get_task(db, user.id, created["id"])
        assert task is not None
